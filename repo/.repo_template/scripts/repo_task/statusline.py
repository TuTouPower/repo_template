"""状态行 worktree 绑定核心实现（只读账本 + 显式会话绑定）。

绑定由核心命令在拿到 identity 后显式建立：`run-begin` 写入，`run-close` 解除。
本脚本不解析任何终端命令文本，也不依赖宿主私有字段；会话标识由调用方提供
（`--session`，通常即 `owner_id`）。

用法：
  python3 .repo_template/scripts/track_worktree.py bind --session <owner> --tid t113
  python3 .repo_template/scripts/track_worktree.py resolve --session <owner>

`resolve` 只在该会话绑定的 attempt 仍处于打开状态时输出 worktree 绝对路径，
否则输出空字符串；读取或账本异常时输出空并向 stderr 给出可定位诊断，
不伪装成功，也不影响业务事务。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

STATUSLINE_FILE = os.path.join(".scratch", "statusline_workdir.jsonl")
# 打开窗口：start / attempt_reserved / execution_claimed 等非终态事件；
# 以下事件关闭窗口。
TERMINAL_EVENTS = {"report", "integrated", "attempt_terminal"}


def _iter_lines_reverse(path: str, block_size: int = 8192):
    """从文件末尾向前逐行产出非空行。"""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            remainder = b""
            while position > 0:
                read_size = min(block_size, position)
                position -= read_size
                handle.seek(position)
                chunk = handle.read(read_size) + remainder
                lines = chunk.split(b"\n")
                remainder = lines[0]
                for line in reversed(lines[1:]):
                    cleaned = line.strip()
                    if cleaned:
                        yield cleaned.decode("utf-8", errors="replace")
            if remainder.strip():
                yield remainder.strip().decode("utf-8", errors="replace")
    except OSError as error:
        print(f"track_worktree: 无法读取 {path}：{error}", file=sys.stderr)
        return


def resolve_worktree(project: str, tid: str) -> str:
    """该 tid 当前打开的 worktree 绝对路径；已终结或不可判定时返回空。"""
    ledger = os.path.join(project, "docs", "runtime", "dispatch_ledger.jsonl")
    if not os.path.isfile(ledger):
        return ""
    worktree = ""
    open_window = False
    for line in _iter_lines_reverse(ledger):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("tid") != tid:
            continue
        event = record.get("event")
        if event in TERMINAL_EVENTS:
            open_window = False
            break
        if event == "start":
            worktree = str(record.get("worktree") or "")
            open_window = True
            break
        open_window = True
    if not open_window or not worktree:
        return ""
    return os.path.abspath(os.path.join(project, worktree))


def _read_last_line(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = [line for line in handle.read().splitlines() if line.strip()]
    except OSError:
        return ""
    return lines[-1] if lines else ""


def append_record(path: str, record: dict) -> None:
    """幂等追加一条绑定记录；失败只报诊断，不影响业务事务。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError as error:
        print(f"track_worktree: 无法创建 {os.path.dirname(path)}：{error}", file=sys.stderr)
        return
    line = json.dumps(record, ensure_ascii=False)
    if _read_last_line(path).strip() == line:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as error:
        print(f"track_worktree: 无法写入 {path}：{error}", file=sys.stderr)


def _statusline_path(project: str) -> str:
    return os.path.join(project, STATUSLINE_FILE)


def bind(project: str, session: str, tid: str, attempt: int | None = None,
         execution_id: str = "") -> bool:
    """显式建立 session → worktree 绑定；无打开窗口时返回 False。"""
    if not session or not tid:
        return False
    worktree = resolve_worktree(project, tid)
    if not worktree:
        return False
    append_record(_statusline_path(project), {
        "session": session,
        "tid": tid,
        "attempt": attempt,
        "execution_id": execution_id,
        "worktree": worktree,
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    return True


def unbind(project: str, session: str, tid: str) -> None:
    if not session or not tid:
        return
    append_record(_statusline_path(project), {
        "session": session,
        "tid": tid,
        "attempt": None,
        "execution_id": "",
        "worktree": "",
        "ts": datetime.now(timezone.utc).isoformat(),
    })


def current_worktree(project: str, session: str) -> str:
    """会话当前 worktree；绑定已解除或 attempt 已终结时返回空。"""
    if not session:
        return ""
    path = _statusline_path(project)
    if not os.path.isfile(path):
        return ""
    latest = None
    for line in _iter_lines_reverse(path):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("session") == session:
            latest = record
            break
    if not latest or not latest.get("worktree"):
        return ""
    tid = str(latest.get("tid") or "")
    reopened = resolve_worktree(project, tid)
    return reopened if reopened else ""


def main() -> int:
    parser = argparse.ArgumentParser(description="状态行 worktree 绑定（显式会话身份）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    bind_parser = sub.add_parser("bind", help="会话绑定到 task 当前 worktree")
    bind_parser.add_argument("--session", required=True)
    bind_parser.add_argument("--tid", required=True)
    bind_parser.add_argument("--project", default=os.getcwd())
    resolve_parser = sub.add_parser("resolve", help="输出会话当前 worktree（无则空）")
    resolve_parser.add_argument("--session", required=True)
    resolve_parser.add_argument("--project", default=os.getcwd())
    args = parser.parse_args()

    if args.cmd == "bind":
        return 0 if bind(args.project, args.session, args.tid) else 1
    print(current_worktree(args.project, args.session))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
