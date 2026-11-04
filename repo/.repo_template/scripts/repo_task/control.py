"""CLI control plane for attempts, ledger inspection, and monitoring."""

import argparse
import contextlib
import json
import sys

import repo_task.context as ctx

from .attempts import (
    attempt_for_identity,
    attempts_for_tid,
    claim_execution,
    report_attempt,
    reserve_attempt,
    resolve_identity,
    terminal_attempt,
)
from .documents import tid_sort_key
from .git_ops import _git, require_primary_worktree, worktree_paths
from .integration import _cleanup_worktree, _resolve_integrate_branch, cmd_start
from .ledger import ledger_append, ledger_read
from .locks import control_locked
from .monitoring import compute_ps_rows, verify_integrate_ready
from .plan import build_board_model, chain_letter, compute_batch_plan
from .scheduling import compute_schedule
from .store import discover_effective_sources, discover_effective_tasks, scan_tasks


def cmd_effective_status(args):
    """只读输出每个有效 task 的状态与读取来源（worktree / branch / main）。"""
    require_primary_worktree()
    entries = discover_effective_sources()
    rows = [
        e for e in entries.values()
        if not getattr(args, "status", None) or e["status"] in args.status
    ]
    if not rows:
        print("(no effective tasks)")
        return
    print("| tid    | status    | source  | read_at                           | note |")
    print("|--------|-----------|---------|-----------------------------------|------|")
    for e in sorted(rows, key=lambda r: tid_sort_key(r["tid"])):
        read_at = e["read_at"] or "main"
        print(
            f"| {e['tid']:<6} | {e['status']:<8} | {e['source']:<7} | {read_at:<33} | {(e['note'] or '')[:40]} |"
        )


def cmd_view(args):
    if getattr(args, "serve", False):
        from .view_server import serve
        serve(
            host=args.host,
            port=args.port,
            allow_non_loopback=bool(getattr(args, "allow_non_loopback", False)),
        )
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
        selected_set = set(selected)
        ready_conflicts = sorted(
            {
                tuple(sorted((tid, peer), key=tid_sort_key))
                for tid in selected
                for peer in conflicts[tid] & selected_set
                if tid != peer
            },
            key=lambda pair: (tid_sort_key(pair[0]), tid_sort_key(pair[1])),
        )
        groups = (
            ("▸ 下一批可跑（冲突项勿并行，分链以 plan 为准）",
             [(tid, tasks[tid]["title"]) for tid in selected]),
            ("▸ 可跑但互相冲突", ready_conflicts),
            ("▸ 被依赖阻塞", waiting_deps),
            ("▸ 被冲突阻塞", blocked_conflicts),
        )
        for heading, rows in groups:
            if not rows:
                continue
            lines.extend(["", f"  {heading}"])
            for left, right in rows:
                if heading == "▸ 被依赖阻塞":
                    lines.append(f"    {left} → {right}")
                elif heading == "▸ 可跑但互相冲突":
                    lines.append(f"    {left} ↔ {right}  — 不要并行启动")
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
        # 本波链摘要：算法权威 = plan.compute_batch_plan
        board = build_board_model(schedule)
        batch = compute_batch_plan(board)
        n_chains = len(batch.get("chains") or [])
        if n_chains:
            lines.extend([
                "",
                f"[本波] {n_chains} 条并发链（详情 / 含 title：task.py plan）",
            ])
            for chain in batch["chains"]:
                seq = " -> ".join(chain["taskIds"])
                lines.append(f"  {chain_letter(chain['name'])}: {seq}")
        else:
            lines.extend(["", "[本波] 无可推荐链（task.py plan）"])
        print("\n".join(lines))
    except ctx.TaskDataError as error:
        message = str(error)
        if not message.startswith(("invalid_graph:", "invalid_done:")):
            message = f"invalid_graph: {message}"
        sys.exit(f"view=FAIL：{message}")


def _print_json(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False))


@control_locked
def cmd_attempt_reserve(args):
    require_primary_worktree()
    _print_json(reserve_attempt(args.tid, args.model))


@control_locked
def cmd_attempt_claim(args):
    require_primary_worktree()
    attempt, execution_id = resolve_identity(args.tid, args.attempt, args.execution_id)
    _print_json(claim_execution(args.tid, attempt, execution_id, args.owner_id,
                               replace_owner=args.replace_owner,
                               confirm_stopped=args.confirm_stopped))


@control_locked
def cmd_attempt_terminal(args):
    require_primary_worktree()
    attempt, execution_id = resolve_identity(args.tid, args.attempt, args.execution_id)
    _print_json(terminal_attempt(args.tid, attempt, execution_id, args.status))


def require_report_done_ready(tid: str, attempt: int, execution_id: str) -> None:
    """report=done 的门禁：最终提交须带 exact handoff 与同内容的 review PASS 证据。"""
    suffix = "；先按 recovery 判定（review_repair / review_mismatch）处理后重跑 run-close"
    try:
        verdict, _kind, detail = verify_integrate_ready(tid, attempt, execution_id)
    except ctx.TaskDataError as error:
        sys.exit(f"report=done 拒绝：{error}{suffix}")
    if verdict != "ready":
        sys.exit(f"report=done 拒绝：{detail}{suffix}")


@control_locked
def cmd_attempt_report(args):
    require_primary_worktree()
    attempt, execution_id = resolve_identity(args.tid, args.attempt, args.execution_id)
    if args.status == "done":
        require_report_done_ready(args.tid, attempt, execution_id)
    _print_json(report_attempt(
        args.tid,
        attempt,
        execution_id,
        args.status,
        sha=args.sha,
        fail_class=args.fail_class,
        reason=args.reason,
    ))


def _recovery_hint(tid: str) -> str:
    return f"运行 `python3 .repo_template/scripts/task.py recovery {tid}` 判定现场"


def _require_fresh_run(tid: str) -> None:
    """run-begin 只接受从未执行过的 backlog task；既有现场一律交给 recovery。"""
    hint = _recovery_hint(tid)
    task = discover_effective_tasks().get(tid)
    if task is None:
        sys.exit(f"run-begin 拒绝：{tid} 不存在")
    if task["status"] != "backlog":
        sys.exit(f"run-begin 拒绝：{tid} 有效状态为 {task['status']}（仅 backlog 可新启动）；{hint}，不做隐式恢复")
    if attempts_for_tid(tid, ledger_read(tid=tid)):
        sys.exit(f"run-begin 拒绝：{tid} 已有 attempt 记录；{hint}，不做隐式恢复")
    rel = ctx.worktree_rel_path(tid)
    path = (ctx.REPO_ROOT / rel).resolve()
    if path.exists() or str(path) in worktree_paths():
        sys.exit(f"run-begin 拒绝：worktree {rel} 已存在；{hint}，不做隐式恢复")
    branch = f"{tid}_{task['slug']}"
    if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode == 0:
        sys.exit(f"run-begin 拒绝：分支 {branch!r} 已存在；{hint}，不做隐式恢复")


@control_locked
def cmd_run_begin(args):
    """start → reserve → claim 在同一控制面锁内完成；失败时现场原样保留给 recovery。"""
    require_primary_worktree()
    tid, owner_id = args.tid, (args.owner_id or "").strip()
    if not ctx.TID_RE.fullmatch(tid):
        sys.exit(f"tid 非法：{tid!r}")
    if not owner_id:
        sys.exit("owner_id 必须是非空会话标识")
    _require_fresh_run(tid)
    step = "start"
    try:
        # start 的进度文本走 stderr，保持 stdout 仅输出 JSON identity
        with contextlib.redirect_stdout(sys.stderr):
            cmd_start(argparse.Namespace(tid=tid, base=args.base))
        step = "reserve"
        reserved = reserve_attempt(tid)
        step = "claim"
        claim_execution(tid, reserved["attempt"], reserved["execution_id"], owner_id)
    except (SystemExit, ctx.TaskDataError, OSError) as error:
        detail = error.code if isinstance(error, SystemExit) else error
        sys.exit(
            f"run-begin 失败于步骤 {step}：{detail}\n"
            f"run-begin 不回滚已完成的步骤；{_recovery_hint(tid)}，勿重复 run-begin"
        )
    _print_json({
        "tid": tid,
        "attempt": reserved["attempt"],
        "execution_id": reserved["execution_id"],
        "branch": f"{tid}_{discover_effective_tasks()[tid]['slug']}",
        "worktree": str((ctx.REPO_ROOT / ctx.worktree_rel_path(tid)).resolve()),
        "owner_id": owner_id,
    })


# --result → (terminal status, report status)
_RUN_CLOSE_PLAN = {
    "done": ("completed", "done"),
    "blocked": ("stopped", "blocked"),
    "failed": ("failed", "failed"),
}


@control_locked
def cmd_run_close(args):
    """按结果写 terminal + report（done 另 exact cleanup）；已写事件不重复写。"""
    require_primary_worktree()
    tid, owner_id = args.tid, (args.owner_id or "").strip()
    if not ctx.TID_RE.fullmatch(tid):
        sys.exit(f"tid 非法：{tid!r}")
    terminal_status, report_status = _RUN_CLOSE_PLAN[args.result]
    try:
        attempt, execution_id = resolve_identity(tid)
        record = attempt_for_identity(tid, attempt, execution_id, ledger_read(tid=tid))
    except ctx.TaskDataError as error:
        sys.exit(f"run-close 拒绝：{error}")
    if not owner_id or record["owner_id"] != owner_id:
        sys.exit(
            f"run-close 拒绝：owner_id 与 ledger claim 不一致（claim={record['owner_id']!r}）；"
            f"{_recovery_hint(tid)}。跨会话接管走 recovery 的 claim 规程"
        )
    summary = {"tid": tid, "attempt": attempt, "execution_id": execution_id, "result": args.result}
    if record["state"] == "integrated":
        if args.result != "done":
            sys.exit(f"run-close 拒绝：{tid} attempt={attempt} 已 integrated，不能按 {args.result} 关闭")
        _print_json({**summary, "written": [], "note": "已 integrated（幂等）"})
        return
    terminal, report = record.get("terminal"), record.get("report")
    if terminal and record["terminal_status"] != terminal_status:
        sys.exit(
            f"run-close 拒绝：{tid} attempt={attempt} 已写 terminal={record['terminal_status']}，"
            f"与 --result {args.result} 冲突；{_recovery_hint(tid)}"
        )
    if report and report.get("status") != report_status:
        sys.exit(
            f"run-close 拒绝：{tid} attempt={attempt} 已写 report={report.get('status')}，"
            f"与 --result {args.result} 冲突；{_recovery_hint(tid)}"
        )
    if not terminal and record["state"] != "running":
        sys.exit(f"run-close 拒绝：{tid} attempt={attempt} state={record['state']!r}，无法关闭")
    sha = None
    if args.result == "done":
        # 先验证再写：任何 ledger 写入前确认分支 tip 已是带 exact handoff 的实现 commit
        try:
            _, sha = _resolve_integrate_branch(tid)
        except ctx.TaskDataError as error:
            sys.exit(f"report=done 拒绝：{error}；先在 worktree 执行 finish")
        require_report_done_ready(tid, attempt, execution_id)
    written = []
    step = "terminal"
    try:
        if not terminal:
            terminal_attempt(tid, attempt, execution_id, terminal_status)
            written.append("terminal")
        step = "report"
        if not report:
            report_attempt(
                tid, attempt, execution_id, report_status,
                sha=sha,
                fail_class=args.fail_class if args.result == "failed" else None,
                reason=args.reason,
            )
            written.append("report")
        step = "cleanup"
        if args.result == "done":
            print(_cleanup_worktree(tid, attempt, execution_id), file=sys.stderr)
            written.append("cleanup")
    except (SystemExit, ctx.TaskDataError, OSError) as error:
        detail = error.code if isinstance(error, SystemExit) else error
        sys.exit(
            f"run-close 失败于步骤 {step}（本次已写：{', '.join(written) or '无'}）：{detail}\n"
            f"已写事件不回滚；排除原因后重跑同一 run-close（重入安全），或{_recovery_hint(tid)}"
        )
    if sha:
        summary["sha"] = sha
    _print_json({**summary, "written": written})


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
