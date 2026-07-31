#!/usr/bin/env python3
"""PreToolUse hook: 所有 merge 操作必须显式授权（token 机制）。

拦截范围：Bash 命令里的 `git merge` 与 `gh pr merge`。

流程：
1. 首次执行（命令无 `# merge-token=XXX`）：deny 并签发一次性 token，
   写入状态文件，把 token 与「需用户明确授权」提示注入 agent 上下文。
2. agent 拿到用户授权后，重跑命令并在末尾追加 `# merge-token=XXX`。
3. hook 校验 token：存在、未过期、未用过、绑定同一目标；通过则标记已用并 allow，
   否则 deny。

token 单命令一次性，10 分钟过期，绑定到 merge 目标（branch 名 / PR 标识），
防串用。
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any

TOKEN_TTL_SECONDS = 600  # 10 分钟
STATE_PATH = Path(__file__).resolve().parent.parent / "state" / "merge_tokens.json"
TOKEN_COMMENT_RE = re.compile(r"#\s*merge-token\s*=\s*([0-9a-fA-F]+)\b", re.IGNORECASE)
GIT_MERGE_TARGET_RE = re.compile(
    r"(?:^|\s)git\s+merge(?:\s+(?P<target>[A-Za-z0-9_./@:-]+))?\s*$",
    re.IGNORECASE,
)
GH_PR_MERGE_RE = re.compile(
    r"(?:^|\s)gh\s+(?:pr\s+)?merge\b",
    re.IGNORECASE,
)


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


def detect_merge(command: str) -> tuple[str, str] | None:
    """识别 merge 操作并返回 (kind, target_key)。

    target_key 用于 token 绑定：git merge 取目标 branch 名；gh pr merge 取整条
    命令的归一化文本（PR 号/URL 可能出现在多种位置，整条命令作键更稳）。
    非 merge 返回 None。
    """
    if GIT_MERGE_TARGET_RE.search(command):
        m = GIT_MERGE_TARGET_RE.search(command)
        target = (m.group("target") if m else "").strip().strip('"\'')
        target = target or "unspecified"
        return ("git-merge", f"git-merge:{target}")
    if GH_PR_MERGE_RE.search(command):
        return ("gh-pr-merge", f"gh-pr-merge:{compact(command, 120)}")
    return None


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


def issue_token(target_key: str, command_text: str) -> str:
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


def verify_token(token: str, target_key: str, command_text: str) -> tuple[bool, str]:
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

    if token_match:
        ok, reason = verify_token(token_match.group(1), target_key, command_body)
        if ok:
            allow()
        emit("deny", f"[Merge Guard] {reason}")
        return

    token = issue_token(target_key, command_body)
    message = (
        "[Merge Guard] merge 操作需用户明确授权。\n"
        f"  目标：{target_key}\n"
        f"  token：{token}\n"
        "  流程：向用户说明此次 merge 的目标与影响，得到用户明确同意后，"
        "在原命令末尾追加注释重跑：\n"
        f"    {compact(command_body)} # merge-token={token}\n"
        "  token 单次有效、10 分钟过期、绑定目标与命令；未授权直接重跑会被再次拦截。"
    )
    emit("deny", message)


if __name__ == "__main__":
    main()
