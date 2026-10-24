#!/usr/bin/env python3
"""Worktree tracker for the status line.

Two roles:

1. Agent-triggered marker (no --write) — run when a task worktree is created:
       python3 .repo_template/scripts/track_worktree.py --tid t113
   Validates the tid and prints a confirmation. The PostToolUse hook
   (matcher=Bash, command contains this script) performs the actual write.

2. Hook writer (--write --agent <name>) — run by the PostToolUse hook. Reads
   the hook payload from stdin (session_id, cwd, tool_input.command), extracts
   --tid from the command text, resolves the worktree from
   docs/runtime/dispatch_ledger.jsonl, and appends one JSON line to
   <project>/.scratch/statusline_workdir.jsonl:
       {"agent": ..., "session_id": ..., "tid": ..., "worktree": ..., "ts": ...}
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone


def read_payload():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


# Events that close a task's active window: the worktree is no longer valid.
TERMINAL_EVENTS = {"report", "integrated", "attempt_terminal"}


def _iter_lines_reverse(path, block_size=8192):
    """从文件末尾向前逐行产出非空行。"""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            position = f.tell()
            remainder = b""
            while position > 0:
                read_size = min(block_size, position)
                position -= read_size
                f.seek(position)
                chunk = f.read(read_size) + remainder
                lines = chunk.split(b"\n")
                remainder = lines[0]
                for line in reversed(lines[1:]):
                    cleaned = line.strip()
                    if cleaned:
                        yield cleaned.decode("utf-8", errors="replace")
            if remainder.strip():
                yield remainder.strip().decode("utf-8", errors="replace")
    except OSError:
        return


def resolve_worktree(project, tid):
    """Worktree path (absolute) for a tid from the attempt ledger, or ''.

    逆序扫描账本（从后向前），匹配到该 tid 的最新状态即快速终止（Issue #61）。
    A `start` event opens the window; `attempt_reserved` with state=running
    keeps it open; any terminal event (report/integrated/attempt_terminal) closes it.
    """
    ledger = os.path.join(project, "docs", "runtime", "dispatch_ledger.jsonl")
    if not os.path.isfile(ledger):
        return ""
    worktree = ""
    active = False
    saw_latest = False
    for line in _iter_lines_reverse(ledger):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("tid") != tid:
            continue
        event = rec.get("event")
        if not saw_latest:
            saw_latest = True
            if event in TERMINAL_EVENTS:
                return ""
            if event == "start":
                worktree = str(rec.get("worktree") or "")
                active = True
                break
            elif event == "attempt_reserved" and rec.get("state") == "running":
                active = True
                # attempt_reserved 无 worktree 字段，继续向前找到对应 start 事件
            else:
                return ""
        else:
            if event == "start":
                worktree = str(rec.get("worktree") or "")
                break
    if not active or not worktree:
        return ""
    return os.path.normpath(os.path.join(project, worktree))


def _read_last_line(path):
    """O(1) 读取文件最后一行用于去重（Issue #61）。"""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size == 0:
                return ""
            buffer_size = min(size, 4096)
            f.seek(size - buffer_size)
            lines = f.read().splitlines()
            return lines[-1].decode("utf-8", errors="replace") if lines else ""
    except OSError:
        return ""


def append_record(path, record):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError:
        return
    line = json.dumps(record, ensure_ascii=False)
    # De-dup: skip when the last record is identical (O(1) tail check).
    if _read_last_line(path).strip() == line:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def extract_tid(command, fallback_tid):
    """Extract tid from the triggering command, or fall back.

    Accepts `--tid tNNN`, `--tid=tNNN`, and the positional form used by
    task.py (`task.py start tNNN`).
    """
    match = re.search(r"--tid[= ](\S+)", command)
    if match:
        return match.group(1)
    match = re.search(r"\bstart\s+(t\d+)", command)
    if match:
        return match.group(1)
    return fallback_tid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tid", default="")
    parser.add_argument("--write", action="store_true", help="hook writer mode")
    parser.add_argument("--agent", default="", help="agent identity in hook mode")
    args = parser.parse_args()

    if args.write:
        payload = read_payload()
        session_id = str(payload.get("session_id") or "")
        if not session_id:
            return 0
        project = str(payload.get("cwd") or "")
        if not project:
            return 0
        tool_input = payload.get("tool_input") or {}
        command = str(tool_input.get("command") or "")
        # 快速短路：常规开发命令（不含 task/start/tid/track 调度关键词）立即零副作用退出（Issue #53）
        if "track_worktree" not in command and "start" not in command and "--tid" not in command:
            return 0
        ledger_path = os.path.join(project, "docs", "runtime", "dispatch_ledger.jsonl")
        if not os.path.isfile(ledger_path):
            return 0
        tid = extract_tid(command, args.tid)
        if not tid:
            return 0
        worktree = resolve_worktree(project, tid)
        if not worktree:
            return 0
        record = {
            "agent": args.agent,
            "session_id": session_id,
            "tid": tid,
            "worktree": worktree,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        append_record(os.path.join(project, ".scratch", "statusline_workdir.jsonl"), record)
        return 0

    # Agent-triggered marker mode.
    if not args.tid:
        print("--tid is required in marker mode", file=sys.stderr)
        return 2
    worktree = resolve_worktree(os.getcwd(), args.tid)
    if worktree:
        print(f"worktree tracked: {os.path.basename(worktree)}")
    else:
        print(f"no worktree found for tid {args.tid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
