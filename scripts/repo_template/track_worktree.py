#!/usr/bin/env python3
"""Worktree tracker for the status line.

Two roles:

1. Agent-triggered marker (no --write) — run when a task worktree is created:
       python3 scripts/repo_template/track_worktree.py --tid t113
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


def resolve_worktree(project, tid):
    """Worktree path (absolute) for a tid from the dispatch ledger, or ''.

    A `start` event opens the window; `attempt_reserved` with state=running
    keeps it open; any terminal event (report/integrated/attempt_terminal) closes it,
    so a merged or finished task no longer resolves to its (removed) worktree.
    """
    ledger = os.path.join(project, "docs", "runtime", "dispatch_ledger.jsonl")
    if not os.path.isfile(ledger):
        return ""
    worktree = ""
    active = False
    try:
        with open(ledger, encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("tid") != tid:
                    continue
                event = rec.get("event")
                if event == "start":
                    worktree = str(rec.get("worktree") or "")
                    active = True
                elif event == "attempt_reserved" and rec.get("state") == "running":
                    active = True
                elif event in TERMINAL_EVENTS:
                    active = False
    except OSError:
        return ""
    if not active or not worktree:
        return ""
    return os.path.normpath(os.path.join(project, worktree))


def append_record(path, record):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError:
        return
    line = json.dumps(record, ensure_ascii=False)
    # De-dup: skip when the last record is identical.
    try:
        with open(path, encoding="utf-8") as handle:
            last_line = ""
            for existing in handle:
                last_line = existing
        if last_line.strip() == line:
            return
    except OSError:
        pass
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
