"""Canonical control implementation for the task toolchain."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import repo_task.context as ctx

from .documents import parse_tid_list, tid_sort_key
from .git_ops import require_primary_worktree
from .ledger import (
    current_attempt,
    dispatch_for_attempt,
    ledger_append,
    ledger_next_attempt,
    ledger_read,
    latest_worker_terminal,
)
from .monitoring import compute_ps_rows, compute_reconcile_plan, observe_attempt
from .scheduling import compute_schedule
from .store import discover_effective_tasks, scan_tasks

def cmd_view(args):
    try:
        schedule = compute_schedule()
        tasks = schedule["tasks"]
        conflicts = schedule["conflicts"]
        main_done_set = schedule["main_done_set"]
        unmerged_done = schedule["unmerged_done"]
        dropped_set = schedule["dropped_set"]
        active_list = schedule["active_list"]
        active_set = schedule["active_set"]
        backlog_tasks = schedule["backlog_tasks"]
        selected = schedule["selected"]
        waiting_deps = schedule["waiting_deps"]
        blocked_conflicts = schedule["blocked_conflicts"]
        pending_clarify = schedule["pending_clarify"]
        unscheduled = schedule["unscheduled"]

        # 输出全景
        lines: list[str] = ["== task 全景 =="]

        lines.append("")
        lines.append(f"[运行中] active {len(active_list)}")
        if active_list:
            for tid in active_list:
                task = tasks[tid]
                peer_conflicts = sorted(conflicts[tid] & active_set, key=tid_sort_key)
                peer_conflicts += sorted(
                    (c for c in conflicts[tid] if c not in active_set
                     and tasks[c]["status"] == "backlog"),
                    key=tid_sort_key,
                )
                tag = f"  conflicts: {', '.join(peer_conflicts)}" if peer_conflicts else ""
                lines.append(f"  {tid}  {task['title']}{tag}")
        else:
            lines.append("  -")

        lines.append("")
        lines.append(f"[待运行] backlog {len(backlog_tasks)}")
        if selected:
            lines.append("")
            lines.append("  ▸ 下一批可跑")
            for tid in selected:
                lines.append(f"    {tid}  {tasks[tid]['title']}")
        if waiting_deps:
            lines.append("")
            lines.append("  ▸ 被依赖阻塞")
            for dep, tid in waiting_deps:
                lines.append(f"    {dep} → {tid}")
        if blocked_conflicts:
            lines.append("")
            lines.append("  ▸ 被冲突阻塞")
            for tid, peer in blocked_conflicts:
                lines.append(
                    f"    {tid} ↔ {peer}  — {tid}: {tasks[tid]['title']}"
                )
        if pending_clarify:
            lines.append("")
            lines.append("  ▸ 调度未就绪")
            for tid in pending_clarify:
                lines.append(f"    {tid}  schedule_status=pending_clarification")
        if unscheduled:
            lines.append("")
            lines.append("  ▸ 未排程")
            for tid in unscheduled:
                lines.append(f"    {tid}  {tasks[tid]['title']}")

        lines.append("")
        lines.append(
            f"[已结束] done={len(main_done_set)}  dropped={len(dropped_set)}"
        )
        if unmerged_done:
            lines.append(
                f"  （{len(unmerged_done)} 个 done 在未合并分支，未入 main："
                + " ".join(unmerged_done)
                + "）"
            )

        print("\n".join(lines))
    except ctx.TaskDataError as error:
        message = str(error)
        if not message.startswith(("invalid_graph:", "invalid_done:")):
            message = f"invalid_graph: {message}"
        sys.exit(f"view=FAIL：{message}")

def cmd_ledger_record(args):
    event = {"event": args.event}
    if args.event == "observation":
        sys.exit("observation 只能由 task.py observe 写入")
    attempt_events = (
        "dispatch", "report", "failed", "escalated", "worker_terminal", "silent_alerted"
    )
    if args.event in attempt_events:
        if not getattr(args, "tid", None):
            sys.exit(f"ledger record --event {args.event} 必须给 --tid")
        event["tid"] = args.tid
    elif getattr(args, "tid", None):
        event["tid"] = args.tid
    events = ledger_read()
    if args.event == "dispatch" and args.attempt is None:
        args.attempt = ledger_next_attempt(args.tid, events)
    elif args.event in (
        "report", "failed", "escalated", "worker_terminal", "silent_alerted"
    ) and args.attempt is None:
        sys.exit(f"ledger record --event {args.event} 必须显式给 --attempt")
    if args.event == "dispatch":
        previous_attempt = current_attempt(args.tid, events)
        if previous_attempt is not None:
            previous_dispatch = dispatch_for_attempt(args.tid, previous_attempt, events)
            if previous_dispatch and previous_dispatch.get("worker_id"):
                if latest_worker_terminal(args.tid, previous_attempt, events) is None:
                    sys.exit(
                        f"{args.tid} attempt={previous_attempt} 尚无 worker_terminal；"
                        "禁止派发新的并行 attempt"
                    )
    if args.event == "report":
        if not args.status:
            sys.exit("ledger record --event report 必须给 --status")
        if args.status not in ctx.LEDGER_REPORT_STATUSES:
            sys.exit(
                "ledger record --event report --status 必须是 "
                + "/".join(ctx.LEDGER_REPORT_STATUSES)
            )
    if args.event == "escalated":
        current = current_attempt(args.tid, events)
        dispatch = dispatch_for_attempt(args.tid, args.attempt, events)
        if current == args.attempt and dispatch and dispatch.get("worker_id"):
            if latest_worker_terminal(args.tid, args.attempt, events) is None:
                sys.exit(
                    f"{args.tid} attempt={args.attempt} 尚无 worker_terminal；"
                    "禁止结束仍运行的并行 worker"
                )
    if args.event == "worker_terminal":
        if not getattr(args, "worker_id", None):
            sys.exit("ledger record --event worker_terminal 必须给 --worker-id")
        if not args.status:
            sys.exit("ledger record --event worker_terminal 必须给 --status")
        if args.status not in ctx.LEDGER_TERMINAL_STATUSES:
            sys.exit(
                "ledger record --event worker_terminal --status 必须是 "
                + "/".join(ctx.LEDGER_TERMINAL_STATUSES)
            )
        dispatch = next(
            (
                item for item in reversed(events)
                if item.get("event") == "dispatch"
                and item.get("tid") == args.tid
                and item.get("attempt") == args.attempt
            ),
            None,
        )
        if dispatch is None:
            sys.exit(
                f"worker_terminal {args.tid} attempt={args.attempt} 无匹配 dispatch；"
                "必须显式绑定已派发 attempt"
            )
        if not dispatch.get("worker_id"):
            sys.exit(
                f"worker_terminal {args.tid} attempt={args.attempt} 的 dispatch 缺 worker_id"
            )
        if dispatch.get("worker_id") != args.worker_id:
            sys.exit(
                f"worker_terminal {args.tid} attempt={args.attempt} worker_id={args.worker_id!r}"
                f" 与 dispatch worker_id={dispatch.get('worker_id')!r} 不匹配"
            )
    if args.event == "failed" and not args.fail_class:
        sys.exit("ledger record --event failed 必须给 --class")
    if args.event == "breaker" and not args.model:
        sys.exit("ledger record --event breaker 必须给 --model")
    if args.event == "silent_alerted" and not getattr(args, "fingerprint", None):
        sys.exit("ledger record --event silent_alerted 必须给 --fingerprint")
    if args.event == "breaker" and args.state is None:
        args.state = "open"
    for key, value in (
        ("attempt", args.attempt),
        ("model", args.model),
        ("worker_id", getattr(args, "worker_id", None)),
        ("status", args.status),
        ("sha", args.sha),
        ("class", args.fail_class),
        ("state", args.state),
        ("fingerprint", getattr(args, "fingerprint", None)),
    ):
        if value is not None:
            event[key] = value
    if args.reason is not None:
        event["text" if args.event == "note" else "reason"] = args.reason
    final = ledger_append(event)
    parts = [f"recorded: {final['event']}"]
    if final.get("tid"):
        label = final["tid"]
        if final.get("attempt") is not None:
            label += f"#{final['attempt']}"
        parts.append(label)
    for key in ("model", "worker_id", "status", "class", "state", "fingerprint"):
        if final.get(key):
            parts.append(f"{key}={final[key]}")
    print(" ".join(parts))

def cmd_ledger_tail(args):
    events = ledger_read()
    if args.tid:
        events = [e for e in events if e.get("tid") == args.tid]
    if not events:
        print("（账本无匹配记录）")
        return
    for e in events[-args.n:][::-1]:
        parts = [e.get("ts", "-"), e.get("event", "?")]
        if e.get("tid"):
            label = e["tid"]
            if e.get("attempt") is not None:
                label += f"#{e['attempt']}"
            parts.append(label)
        for key in (
            "model", "worker_id", "status", "class", "state", "reason", "text",
            "merge_sha", "fingerprint", "head", "worktree", "dirty",
        ):
            if e.get(key):
                parts.append(f"{key}={e[key]}")
        print(" ".join(parts))


def cmd_observe(args):
    require_primary_worktree()
    result = observe_attempt(args.tid, args.attempt, ledger_read())
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return
    changed = "changed" if result["changed"] else "unchanged"
    worker = result["worker_id"] or "-"
    print(
        f"OBSERVE {result['tid']} attempt={result['attempt']} {changed} "
        f"fingerprint={result['fingerprint']} last_change={result['last_change']} "
        f"silent={result['silent_minutes']}m worker_id={worker} head={result['head']} "
        f"worktree={result['worktree']} dirty={result['dirty']}"
    )

def cmd_ps(args):
    require_primary_worktree()
    events = ledger_read()
    if not events:
        print("调度账本为空：尚无记录（docs/runtime/dispatch_ledger.jsonl）")
        return
    effective = discover_effective_tasks()
    main_statuses = {task["tid"]: task["status"] for task in scan_tasks()}
    rows = compute_ps_rows(
        events,
        effective,
        main_statuses,
        silent_minutes=args.silent_minutes,
        now=datetime.now().astimezone(),
    )
    if not args.all:
        rows = [row for row in rows if row["state"] not in ctx.ARCHIVED_STATUSES]
    if not rows:
        print("（无在飞 task；--all 显示已结束）")
        return
    cells = [
        [
            row["tid"],
            str(row["attempt"]) if row["attempt"] is not None else "-",
            row["model"] or "-",
            row["worker_id"] or "-",
            row["state"],
            row["last_activity"],
            row["note"],
        ]
        for row in rows
    ]
    headers = ["tid", "attempt", "model", "worker_id", "state", "last_activity", "note"]
    widths = [
        max(len(headers[i]), *(len(cell[i]) for cell in cells))
        for i in range(len(headers))
    ]
    print("  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))))
    for cell in cells:
        print("  ".join(cell[i].ljust(widths[i]) for i in range(len(headers))))


def cmd_reconcile(args):
    require_primary_worktree()
    events = ledger_read()
    schedule = compute_schedule()
    scope = set(parse_tid_list(args.tids, field="--tids")) if args.tids else None
    ladder = (
        [model.strip() for model in args.model_ladder.split(">") if model.strip()]
        if args.model_ladder else []
    ) or None
    plan = compute_reconcile_plan(
        events,
        schedule,
        limit=args.limit,
        scope=scope,
        ladder=ladder,
        silent_minutes=args.silent_minutes,
        max_auto_retries=args.max_auto_retries,
        now=datetime.now().astimezone(),
    )
    if args.json:
        print(json.dumps(plan, ensure_ascii=False))
        return
    for action in plan["actions"]:
        parts = [action["action"].upper(), action["tid"]]
        if action.get("attempt") is not None:
            parts.append(f"attempt={action['attempt']}")
        if action.get("model"):
            parts.append(f"model={action['model']}")
        if action.get("worker_id"):
            parts.append(f"worker_id={action['worker_id']}")
        if action.get("mode"):
            parts.append(f"mode={action['mode']}")
        print(" ".join(parts) + f" — {action['reason']}")
    if not plan["actions"]:
        if plan.get("silent_hold"):
            print("plan 为空：静默告警保持暂停，不补 dispatch")
        else:
            print("plan 为空：无待执行动作（可安全空闲）")
    occupancy = plan["occupancy"]
    print(f"占槽 {occupancy['used']}/{occupancy['limit']}")
