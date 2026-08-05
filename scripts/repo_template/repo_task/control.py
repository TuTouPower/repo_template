"""CLI control plane for attempts, ledger inspection, monitoring, and scheduling."""

import json
import sys
from datetime import datetime

import repo_task.context as ctx

from .attempts import (
    bind_attempt,
    escalate_attempt,
    report_attempt,
    reserve_attempt,
    silent_alert_attempt,
    terminal_attempt,
)
from .documents import parse_tid_list, tid_sort_key
from .git_ops import require_primary_worktree
from .ledger import ledger_append, ledger_read
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

        lines: list[str] = ["== task 全景 ==", "", f"[运行中] active {len(active_list)}"]
        if active_list:
            for tid in active_list:
                task = tasks[tid]
                peers = sorted(conflicts[tid] & active_set, key=tid_sort_key)
                peers += sorted(
                    (peer for peer in conflicts[tid] if peer not in active_set
                     and tasks[peer]["status"] == "backlog"),
                    key=tid_sort_key,
                )
                tag = f"  conflicts: {', '.join(peers)}" if peers else ""
                lines.append(f"  {tid}  {task['title']}{tag}")
        else:
            lines.append("  -")
        lines.extend(["", f"[待运行] backlog {len(backlog_tasks)}"])
        groups = (
            ("▸ 下一批可跑", [(tid, tasks[tid]["title"]) for tid in selected]),
            ("▸ 被依赖阻塞", waiting_deps),
            ("▸ 被冲突阻塞", blocked_conflicts),
            ("▸ 调度未就绪", [(tid, "schedule_status=pending_clarification") for tid in pending_clarify]),
            ("▸ 未排程", [(tid, tasks[tid]["title"]) for tid in unscheduled]),
        )
        for heading, rows in groups:
            if not rows:
                continue
            lines.extend(["", f"  {heading}"])
            for left, right in rows:
                if heading == "▸ 被依赖阻塞":
                    lines.append(f"    {left} → {right}")
                elif heading == "▸ 被冲突阻塞":
                    lines.append(f"    {left} ↔ {right}  — {left}: {tasks[left]['title']}")
                else:
                    lines.append(f"    {left}  {right}")
        lines.extend(["", f"[已结束] done={len(main_done_set)}  dropped={len(dropped_set)}"])
        if unmerged_done:
            lines.append(
                f"  （{len(unmerged_done)} 个 done 在未合并分支，未入 main："
                + " ".join(unmerged_done) + "）"
            )
        print("\n".join(lines))
    except ctx.TaskDataError as error:
        message = str(error)
        if not message.startswith(("invalid_graph:", "invalid_done:")):
            message = f"invalid_graph: {message}"
        sys.exit(f"view=FAIL：{message}")


def _print_json(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False))


def cmd_attempt_reserve(args):
    require_primary_worktree()
    from .store import scan_tasks
    known_tids = {task["tid"] for task in scan_tasks()}
    if args.tid not in known_tids:
        raise ctx.TaskDataError(f"{args.tid} 不存在于 task 目录；拒绝 reserve 孤立 attempt")
    _print_json(reserve_attempt(args.tid, args.executor, args.model))


def cmd_attempt_bind(args):
    require_primary_worktree()
    _print_json(bind_attempt(
        args.tid, args.attempt, args.execution_id, args.host_worker_id
    ))


def cmd_attempt_terminal(args):
    require_primary_worktree()
    _print_json(terminal_attempt(
        args.tid, args.attempt, args.execution_id, args.status
    ))


def cmd_attempt_report(args):
    require_primary_worktree()
    _print_json(report_attempt(
        args.tid,
        args.attempt,
        args.execution_id,
        args.status,
        sha=args.sha,
        fail_class=args.fail_class,
        reason=args.reason,
    ))


def cmd_attempt_escalate(args):
    require_primary_worktree()
    _print_json(escalate_attempt(
        args.tid, args.attempt, args.execution_id, args.reason
    ))


def cmd_attempt_silent_alert(args):
    require_primary_worktree()
    _print_json(silent_alert_attempt(
        args.tid, args.attempt, args.execution_id, args.fingerprint
    ))


def cmd_ledger_record(args):
    if args.event not in ctx.LEDGER_RECORDABLE_EVENTS:
        sys.exit(
            f"ledger record 不允许生命周期事件 {args.event!r}；请使用 task.py attempt 子命令"
        )
    event = {"event": args.event}
    if args.tid:
        event["tid"] = args.tid
    if args.event == "note":
        if args.reason is not None:
            event["text"] = args.reason
    else:
        if not args.model:
            sys.exit("ledger record --event breaker 必须给 --model")
        event["model"] = args.model
        event["state"] = args.state or "open"
        if args.reason is not None:
            event["reason"] = args.reason
    final = ledger_append(event)
    parts = [f"recorded: {final['event']}"]
    for key in ("tid", "model", "state"):
        if final.get(key):
            parts.append(f"{key}={final[key]}")
    print(" ".join(parts))


def cmd_ledger_tail(args):
    events = ledger_read()
    if args.tid:
        events = [event for event in events if event.get("tid") == args.tid]
    if not events:
        print("（账本无匹配记录）")
        return
    for event in events[-args.n:][::-1]:
        parts = [event.get("ts", "-"), event.get("event", "?")]
        if event.get("tid"):
            label = event["tid"]
            if event.get("attempt") is not None:
                label += f"#{event['attempt']}"
            parts.append(label)
        for key in (
            "execution_id", "executor", "model", "host_worker_id", "status",
            "class", "state", "reason", "text", "merge_sha", "fingerprint",
            "head", "worktree", "dirty",
        ):
            if event.get(key):
                parts.append(f"{key}={event[key]}")
        print(" ".join(parts))


def cmd_observe(args):
    require_primary_worktree()
    result = observe_attempt(
        args.tid, args.attempt, args.execution_id, ledger_read()
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return
    changed = "changed" if result["changed"] else "unchanged"
    host = result["host_worker_id"] or "-"
    print(
        f"OBSERVE {result['tid']} attempt={result['attempt']} "
        f"execution_id={result['execution_id']} {changed} "
        f"fingerprint={result['fingerprint']} last_change={result['last_change']} "
        f"silent={result['silent_minutes']}m host_worker_id={host} "
        f"head={result['head']} worktree={result['worktree']} dirty={result['dirty']}"
    )


def cmd_ps(args):
    require_primary_worktree()
    events = ledger_read()
    effective = discover_effective_tasks()
    main_statuses = {task["tid"]: task["status"] for task in scan_tasks()}
    rows = compute_ps_rows(
        events, effective, main_statuses,
        silent_minutes=args.silent_minutes,
        now=datetime.now().astimezone(),
    )
    if not args.all:
        rows = [row for row in rows if row["state"] not in ctx.ARCHIVED_STATUSES]
    if not rows:
        print("（无在飞 task；--all 显示已结束）")
        return
    headers = [
        "tid", "attempt", "execution_id", "executor", "model",
        "host_worker_id", "state", "last_activity", "note",
    ]
    cells = [[str(row.get(key) or "-") for key in headers] for row in rows]
    widths = [max(len(headers[i]), *(len(row[i]) for row in cells)) for i in range(len(headers))]
    print("  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))))
    for row in cells:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))


def cmd_reconcile(args):
    require_primary_worktree()
    events = ledger_read()
    schedule = compute_schedule()
    scope = set(parse_tid_list(args.tids, field="--tids")) if args.tids else None
    ladder = [item.strip() for item in args.model_ladder.split(">") if item.strip()] or None
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
        for key in (
            "attempt", "execution_id", "executor", "model", "host_worker_id", "mode"
        ):
            if action.get(key) is not None and action.get(key) != "":
                parts.append(f"{key}={action[key]}")
        print(" ".join(parts) + f" — {action['reason']}")
    if not plan["actions"]:
        print("plan 为空：静默告警保持暂停，不补 dispatch" if plan.get("silent_hold")
              else "plan 为空：无待执行动作（可安全空闲）")
    occupancy = plan["occupancy"]
    print(f"占槽 {occupancy['used']}/{occupancy['limit']}")
