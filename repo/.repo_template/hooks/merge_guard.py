#!/usr/bin/env python3
"""PreToolUse hook: 所有 merge 操作必须显式授权（token 机制）。

拦截范围：Bash 命令里的 `git merge` 与 `gh pr merge`。

流程：
1. 首次执行（命令无 `# merge-token=XXX`）：deny 并签发一次性 token，
   写入状态文件，把 token 与「需用户明确授权」提示注入 agent 上下文。
2. agent 拿到用户授权后，重跑命令并在末尾追加 `# merge-token=XXX`。
3. hook 校验 token：存在、未过期、未用过、绑定同一目标与命令；通过则标记已用并 allow，
   否则回退到步骤 1 重新签发（失效 token 不卡死，agent 拿新 token 重新走授权流程）。

token 单命令一次性，10 分钟过期，绑定到 merge 目标（branch 名 / PR 标识），
防串用。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any

TOKEN_TTL_SECONDS = 600  # 10 分钟
STATE_PATH = Path(__file__).resolve().parent.parent / "state" / "merge_tokens.json"
TOKEN_COMMENT_RE = re.compile(r"#\s*merge-token\s*=\s*([0-9a-fA-F]+)\b", re.IGNORECASE)
# token 级识别 git/gh 及其 wrapper 前缀，避免引号内文本误判、避免 echo/cat
# 等非命令文本误判；git merge-* 子命令（merge-base/merge-tree 等）经子命令名排除。
GIT_WRAPPERS = {"command", "env", "sudo", "nohup", "time", "nice", "xargs"}
ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# wrapper option 吃参数（如 sudo -u root / nice -n 5）；未列出的 option 视为无参
WRAPPER_OPT_ARGS = {
    "sudo": {"-u", "-g", "-p", "-r", "-t", "-C", "--user", "--group", "--prompt", "--role", "--type", "--chdir"},
    "nice": {"-n", "--adjustment"},
    "env": {"-u", "--unset", "-C", "--chdir", "-S"},
    "xargs": {"-I", "-P", "-n", "-d"},
}
GH_GLOBAL_OPTS = {"-R", "--repo", "--host", "-t", "--config", "-c", "--cli-config"}


def _shell_tokens(command: str) -> list[tuple[str, bool]]:
    """手写 shell tokenizer：保留分隔符（; && || | & 换行）为独立 token，引号内不切。"""
    tokens: list[tuple[str, bool]] = []
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if c in " \t":
            i += 1
            continue
        if command.startswith("&&", i):
            tokens.append(("&&", True)); i += 2; continue
        if command.startswith("||", i):
            tokens.append(("||", True)); i += 2; continue
        if c in ";&|\n":
            tokens.append((c, True)); i += 1; continue
        buf: list[str] = []
        while i < n:
            c = command[i]
            if c == "'":
                j = command.find("'", i + 1)
                if j == -1:
                    j = n
                buf.append(command[i + 1:j])
                i = j + 1
                continue
            if c == '"':
                j = i + 1
                while j < n and command[j] != '"':
                    if command[j] == "\\" and j + 1 < n:
                        j += 2
                    else:
                        j += 1
                buf.append(command[i + 1:j])
                i = j + 1 if j < n else j
                continue
            if c == "\\":
                if i + 1 < n:
                    buf.append(command[i + 1])
                    i += 2
                    continue
                i += 1
                continue
            if c in " \t;&|\n" or command.startswith("&&", i) or command.startswith("||", i):
                break
            buf.append(c)
            i += 1
        tokens.append(("".join(buf), False))
    return tokens


def _command_segments(command: str) -> list[list[str]]:
    """按 shell 分隔符切段；引号内分隔符不切。"""
    segments, current = [], []
    for token, is_sep in _shell_tokens(command):
        if is_sep:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _find_command(segment: list[str], names: set[str]) -> int | None:
    """段内命令位置（git/gh 起始 index）。

    识别环境变量赋值（FOO=bar）、绝对路径 wrapper（/usr/bin/env）、
    wrapper 的选项及其参数（sudo -u root）。
    """
    index = 0
    while index < len(segment):
        token = segment[index]
        base = os.path.basename(token)
        if ENV_ASSIGN_RE.match(token):
            index += 1
            continue
        if base.lower() in names:
            return index
        if base in GIT_WRAPPERS:
            index += 1
            opts = WRAPPER_OPT_ARGS.get(base, set())
            while index < len(segment):
                nxt = segment[index]
                if os.path.basename(nxt).lower() in names:
                    return index
                if ENV_ASSIGN_RE.match(nxt):
                    index += 1
                    continue
                if nxt in GIT_WRAPPERS:
                    break
                if nxt.startswith("-"):
                    index += 1
                    if nxt in opts:
                        index += 1
                    continue
                # 裸 token：wrapper 的普通参数，跳过后继续找命令
                index += 1
                continue
            continue
        return None
    return None


def _git_subcommand(segment: list[str], git_index: int) -> str | None:
    """git 后第一个非 option 子命令；git 顶层选项可带参数（-C <dir> 等）。"""
    index = git_index + 1
    while index < len(segment):
        token = segment[index]
        if token.startswith("-"):
            if token in (
                "-C", "--git-dir", "--work-tree", "--exec-path",
                "--namespace", "--super-prefix", "-c", "--config-env",
            ):
                index += 2
            else:
                index += 1
            continue
        return token
    return None


def _git_merge_target(segment: list[str], git_index: int) -> str:
    """merge 子命令后第一个非选项、非注释参数作为 target key。"""
    index = git_index + 1
    while index < len(segment) and segment[index] != "merge":
        index += 1
    for token in segment[index + 1:]:
        if token.startswith("-") or token.startswith("#"):
            continue
        return token.strip("\"'")
    return "unspecified"


def detect_merge(command: str) -> tuple[str, str] | None:
    """识别 merge 操作并返回 (kind, target_key)。

    target_key 用于 token 绑定：git merge 取目标 branch 名；gh pr merge 取整条
    命令的归一化文本。非 merge 返回 None。
    """
    for segment in _command_segments(command):
        git_index = _find_command(segment, {"git", "git.exe"})
        if git_index is not None:
            if _git_subcommand(segment, git_index) == "merge":
                return (
                    "git-merge",
                    f"git-merge:{_git_merge_target(segment, git_index)}",
                )
        gh_index = _find_command(segment, {"gh", "gh.exe"})
        if gh_index is not None:
            rest = segment[gh_index + 1:]
            # 跳过 gh 全局参数（-R/--repo 及值 等）
            while rest and rest[0].startswith("-"):
                opt = rest[0]
                rest = rest[1:]
                if opt in GH_GLOBAL_OPTS and rest:
                    rest = rest[1:]
            if rest and rest[0] == "pr":
                rest = rest[1:]
            if rest and rest[0] == "merge":
                return (
                    "gh-pr-merge",
                    f"gh-pr-merge:{compact(' '.join(segment), 120)}",
                )
    return None


def allow() -> None:
    raise SystemExit(0)


def load_event() -> dict[str, Any]:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        allow()
    return event if isinstance(event, dict) else {}


def emit(decision: str, message: str) -> None:
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": message,
                    "additionalContext": message,
                }
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0)


def compact(command: str, limit: int = 200) -> str:
    text = " ".join(command.split())
    return text if len(text) <= limit else f"{text[:limit]}..."


def load_state() -> list[dict[str, Any]]:
    if not STATE_PATH.is_file():
        return []
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def save_state(records: list[dict[str, Any]]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def prune_expired(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = time.time()
    return [r for r in records if now - r.get("issued_at", 0) < TOKEN_TTL_SECONDS * 2]


def _state_lock(fh, exclusive: bool) -> None:
    fh.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(
            fh.fileno(), msvcrt.LK_LOCK if exclusive else msvcrt.LK_UNLCK, 1
        )
    else:
        import fcntl
        fcntl.flock(fh, fcntl.LOCK_EX if exclusive else fcntl.LOCK_UN)


def _with_state_lock(callback):
    """token 状态读改写全程独占锁，防并发会话同时校验通过并复用同一 token。"""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_path = STATE_PATH.with_suffix(".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_fh:
        if lock_fh.tell() == 0:
            lock_fh.write("\0")
            lock_fh.flush()
        _state_lock(lock_fh, True)
        try:
            return callback()
        finally:
            _state_lock(lock_fh, False)


def issue_token(target_key: str, command_text: str) -> str:
    def build() -> str:
        token = secrets.token_hex(8)
        records = prune_expired(load_state())
        cmd_hash = hashlib.sha1(command_text.encode("utf-8")).hexdigest()[:16]
        records.append(
            {
                "token": token,
                "target": target_key,
                "cmd_hash": cmd_hash,
                "issued_at": time.time(),
                "used": False,
            }
        )
        save_state(records)
        return token

    return _with_state_lock(build)


def verify_token(token: str, target_key: str, command_text: str) -> tuple[bool, str]:
    def check() -> tuple[bool, str]:
        records = prune_expired(load_state())
        now = time.time()
        cmd_hash = hashlib.sha1(command_text.encode("utf-8")).hexdigest()[:16]
        for r in records:
            if r.get("token") != token:
                continue
            if r.get("used"):
                return False, "token 已使用过（一次性）；请重新申请授权。"
            if now - r.get("issued_at", 0) > TOKEN_TTL_SECONDS:
                return False, "token 已过期；请重新申请授权。"
            if r.get("target") != target_key:
                return (
                    False,
                    f"token 绑定的目标与当前命令不符（期望 {r.get('target')}，实际 {target_key}）；拒签。",
                )
            if r.get("cmd_hash") != cmd_hash:
                return False, "token 绑定的命令与当前命令不符；拒签。"
            r["used"] = True
            save_state(records)
            return True, "授权通过"
        return False, "token 无效；请重新申请授权。"

    return _with_state_lock(check)


def main() -> None:
    event = load_event()
    if str(event.get("tool_name") or "") != "Bash":
        allow()
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        allow()
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        allow()

    # 先剥 token 注释，拿命令本体做 merge 识别与 target 提取
    token_match = TOKEN_COMMENT_RE.search(command)
    command_body = TOKEN_COMMENT_RE.sub("", command).strip()

    detected = detect_merge(command_body)
    if detected is None:
        # 非 merge 命令一律放行，不论注释是否含 merge-token
        allow()
    kind, target_key = detected

    stale_reason = ""
    if token_match:
        ok, reason = verify_token(token_match.group(1), target_key, command_body)
        if ok:
            allow()
        # token 失效（过期/已用/不匹配）：记录原因，重新签发并提示。
        stale_reason = f"原 token 失效（{reason}）已废弃；"

    token = issue_token(target_key, command_body)
    message = (
        "[Merge Guard] merge 操作需用户明确授权。\n"
        f"  {stale_reason}新 token：{token}\n"
        f"  目标：{target_key}\n"
        "  流程：向用户说明此次 merge 的目标与影响，得到用户明确同意后，"
        "在原命令末尾追加注释重跑：\n"
        f"    {compact(command_body)} # merge-token={token}\n"
        "  token 单次有效、10 分钟过期、绑定目标与命令；未授权直接重跑会被再次拦截。"
    )
    emit("deny", message)


if __name__ == "__main__":
    main()
