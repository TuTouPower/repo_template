"""CLI control plane for attempts, ledger inspection, and monitoring."""

import json
import sys

import repo_task.context as ctx

from .attempts import (
    report_attempt,
    reserve_attempt,
    terminal_attempt,
)
from .documents import tid_sort_key
from .git_ops import require_primary_worktree
from .ledger import ledger_append, ledger_read
from .monitoring import compute_ps_rows
from .scheduling import compute_schedule
from .store import discover_effective_tasks, scan_tasks


def cmd_view(args):
    if getattr(args, "serve", False):
        from .view_server import serve
        serve(host=args.host, port=args.port)
        return
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
        if schedule["stalled"]:
            lines.extend([
                "",
                "  ⚠ 调度停滞：已排程 backlog 无可跑项且无运行中 task，不会自行恢复；"
                "检查前置是否未排程或调度图异常："
                + " ".join(schedule["stalled_backlog"]),
            ])
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
    _print_json(reserve_attempt(args.tid, args.executor, args.model))


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


def cmd_ledger_record(args):
    if args.event not in ctx.LEDGER_RECORDABLE_EVENTS:
        sys.exit(
            f"ledger record 不允许生命周期事件 {args.event!r}；请使用 task.py attempt 子命令"
        )
    event = {"event": args.event}
    if args.tid:
        event["tid"] = args.tid
    if args.reason is not None:
        event["text"] = args.reason
    final = ledger_append(event)
    parts = [f"recorded: {final['event']}"]
    for key in ("tid",):
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


def cmd_ps(args):
    require_primary_worktree()
    events = ledger_read()
    effective = discover_effective_tasks()
    main_statuses = {task["tid"]: task["status"] for task in scan_tasks()}
    rows = compute_ps_rows(events, effective, main_statuses)
    if not args.all:
        rows = [row for row in rows if row["state"] not in ctx.ARCHIVED_STATUSES]
    if not rows:
        print("（无在飞 task；--all 显示已结束）")
        return
    headers = [
        "tid", "attempt", "execution_id", "executor", "model",
        "state", "note",
    ]
    cells = [[str(row.get(key) or "-") for key in headers] for row in rows]
    # execution_id 32 位 hex 在 ps 表格中截断前 8 位展示；ledger tail 保留全量。
    for row in cells:
        if len(row[2]) > 8:
            row[2] = row[2][:8]
    widths = [max(len(headers[i]), *(len(row[i]) for row in cells)) for i in range(len(headers))]
    print("  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))))
    for row in cells:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))
