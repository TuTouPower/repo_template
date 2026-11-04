"""Goal 模式：冻结队列快照 + 只读终态判定。

goal 模式的提示词必须是可机器判定的终态，而不是过程指令。本模块提供一对命令：

- ``goal``：查看或冻结 ``docs/runtime/goal_queue.json``（已 gitignore，仅主仓）。
  无参且已有快照时只读展示，不改顺序；首次无快照才按 backlog ∪ active 依赖拓扑冻结。
  重建须显式 tid 或 ``--reset``；显式 tid 覆盖且与旧队列不一致时须确认（或 ``--yes``），
  ``--reset`` 直接覆盖免确认。
- ``goal-check``：只读判定器。权威 = ledger 投影 + 主干状态 + worktree 登记，
  不看 transcript。输出逐 tid 状态行与一个总结 marker：

  - ``GOAL_QUEUE_COMPLETE``（exit 0）：全部 closed/integrated
  - ``GOAL_QUEUE_STOPPED``（exit 3）：存在 blocked/failed/dropped 成员，且没有可继续的成员，
    合法停止
  - ``GOAL_QUEUE_INCOMPLETE``（exit 2）：其余，goal 应继续

阻塞只停所在链（链 = 队列内 depends_on / conflicts_with 无向连通分量，见 plan.group_queue）：
存在停止成员时输出 ``stopped_at=<tid> state=<state> skipped=<tid,...|->``，列出停止成员与
因它被跳过的同链后续成员。可继续成员 = 未闭环、非停止且未被跳过的成员（其它链中待执行
或执行中的成员）；仍有可继续成员时判 INCOMPLETE，保留 stopped_at 行供 agent 得知阻塞点。

合并授权不在判定范围：merge 是 goal 结束后的人工步骤。
"""

import json
import os
import sys
from datetime import datetime

import repo_task.context as ctx

from .attempts import attempts_for_tid
from .documents import atomic_write_text, tid_sort_key
from .git_ops import require_primary_worktree, worktree_paths
from .ledger import ledger_read
from .locks import control_locked
from .monitoring import verify_integrate_ready
from .plan import build_execution_plan, group_queue
from .store import discover_effective_tasks, scan_tasks

QUEUE_PATH = ctx.RUNTIME_DIR / "goal_queue.json"

CLOSED_STATES = {"closed", "integrated"}
STOP_STATES = {"blocked", "failed", "dropped"}

GOAL_LINE_TEMPLATE = (
    '/goal 按 task-run skill 链式串行执行冻结队列 [{queue}]'
    "（快照 docs/runtime/goal_queue.json，禁止变更队列成员）。"
    "队列执行授权已给出：禁止逐 task 征求确认、禁止进入 plan mode；"
    "停止条件仅限 task-run skill「停止条件」列举项；attempt report=blocked 只停所在链："
    "按 skill 跳过该链后续成员并继续其它链。"
    "队列处理完后按 skill 一次性列出待合分支并询问合并授权。"
    "终态判定：在主仓根目录运行 python3 .repo_template/scripts/task.py goal-check——"
    "输出 GOAL_QUEUE_COMPLETE 或 GOAL_QUEUE_STOPPED 即本 goal 结束；"
    "GOAL_QUEUE_INCOMPLETE 表示继续（含已有阻塞点但其它链仍可继续执行的情况，"
    "stopped_at 行标出阻塞点与被跳过成员）。"
)


def _compute_queue(args) -> list[str]:
    effective = discover_effective_tasks()
    if args.tids:
        queue = []
        seen = set()
        for tid in args.tids:
            if not ctx.TID_RE.fullmatch(tid):
                raise ctx.TaskDataError(f"tid 非法：{tid!r}")
            if tid in seen:
                raise ctx.TaskDataError(f"队列重复 tid：{tid}")
            seen.add(tid)
            task = effective.get(tid)
            if task is None:
                raise ctx.TaskDataError(f"{tid} 不存在")
            status = task["status"]
            if status in ctx.ARCHIVED_STATUSES:
                raise ctx.TaskDataError(f"{tid} 已是终态（{status}）；done/dropped 永不入队")
            queue.append(tid)
        build_execution_plan(mode="serial", queue=queue)
        return queue
    plan = build_execution_plan(mode="serial")
    return [tid for chain in plan["chains"] for tid in chain["taskIds"]]


def _format_queue(queue: list[str]) -> str:
    return ", ".join(queue)


def _print_goal_line(queue: list[str]) -> None:
    print("粘贴以下 /goal 行启动自治执行：")
    print()
    print("```text")
    print(GOAL_LINE_TEMPLATE.format(queue=_format_queue(queue)))
    print("```")


def _stale_members(queue: list[str]) -> list[str]:
    statuses = {task["tid"]: task["status"] for task in scan_tasks()}
    stale = []
    for tid in queue:
        status = statuses.get(tid)
        if status is None or status in ctx.ARCHIVED_STATUSES:
            stale.append(tid)
    return stale


def _show_snapshot(snapshot: dict) -> None:
    queue = snapshot["queue"]
    created_at = snapshot.get("created_at") or "未知"
    print(f"当前冻结队列：{_format_queue(queue)}")
    print(f"冻结时间：{created_at}")
    print(f"快照：{ctx._rel(QUEUE_PATH)}（只读展示；重建默认队列用 --reset，指定顺序用显式 tid）")
    stale = _stale_members(queue)
    if stale:
        print(
            f"注意：队列含已归档或不存在成员（{_format_queue(stale)}），"
            "/goal 行不可直接执行"
        )
    print()
    _print_goal_line(queue)


def _write_snapshot(queue: list[str], edges: list[dict]) -> dict:
    snapshot = {
        "version": 1,
        "created_at": datetime.now(ctx.TZ_CN).isoformat(timespec="seconds"),
        "queue": queue,
        "edges": edges,
    }
    # 临时文件 + fsync + os.replace 原子写，防并发/崩溃截断（F41/RT-007）
    atomic_write_text(
        QUEUE_PATH,
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
    )
    return snapshot


def _confirm_overwrite(
    old_queue: list[str],
    new_queue: list[str],
    created_at: str,
    yes: bool,
) -> None:
    if old_queue == new_queue:
        return
    frozen_at = created_at or "未知"
    print(
        "WARNING: 新队列与已冻结快照不一致，覆盖将丢失当前顺序。\n"
        f"  已冻结（{frozen_at}）：{_format_queue(old_queue)}\n"
        f"  新队列：{_format_queue(new_queue) or '（空）'}\n"
        "覆盖？(y/N)",
        file=sys.stderr,
    )
    if yes:
        return
    if not sys.stdin.isatty():
        sys.exit("新队列与已冻结快照不一致；确认覆盖请加 --yes")
    try:
        answer = input()
    except EOFError:
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        sys.exit("goal aborted: 保留已冻结队列")


def _clear_snapshot() -> None:
    if QUEUE_PATH.exists():
        QUEUE_PATH.unlink()
        print("旧队列快照已清除。")
    print("队列为空：没有 backlog/active task；不生成 goal。")


@control_locked
def cmd_goal(args) -> None:
    require_primary_worktree()
    reset = bool(getattr(args, "reset", False))
    yes = bool(getattr(args, "yes", False))
    explicit = bool(args.tids)
    if reset and explicit:
        sys.exit("goal --reset 与显式 tid 互斥")

    existing = None
    if QUEUE_PATH.is_file():
        try:
            existing = _read_snapshot()
        except ctx.TaskDataError as error:
            if not reset and not explicit:
                sys.exit(str(error))
            existing = None

    if not reset and not explicit:
        if existing is not None:
            _show_snapshot(existing)
            return

    try:
        queue = _compute_queue(args)
    except ctx.TaskDataError as error:
        sys.exit(str(error))

    if existing is not None and not reset:
        _confirm_overwrite(
            existing["queue"],
            queue,
            existing.get("created_at") or "",
            yes,
        )

    if not queue:
        _clear_snapshot()
        return

    snapshot = _write_snapshot(queue, _queue_edges(queue, scan_tasks()))
    action = "队列已重新冻结" if existing is not None else "队列已冻结"
    print(f"{action}：{_format_queue(queue)}")
    print(f"冻结时间：{snapshot['created_at']}")
    print(f"快照：{ctx._rel(QUEUE_PATH)}（goal 模式同时只服务一个队列）")
    print()
    _print_goal_line(queue)


def _snapshot_error(detail: str) -> ctx.TaskDataError:
    return ctx.TaskDataError(
        f"队列快照 {ctx._rel(QUEUE_PATH)} 结构非法（{detail}）；"
        "重新运行 task.py goal --reset"
    )


def _parse_edges(value: object, queue: list[str]) -> list[dict]:
    if not isinstance(value, list):
        raise _snapshot_error("缺少 edges")
    members = set(queue)
    edges = []
    for edge in value:
        if not isinstance(edge, dict):
            raise _snapshot_error("edges 成员须为对象")
        kind = edge.get("type")
        src = edge.get("from")
        dst = edge.get("to")
        if kind not in ("dep", "conflict"):
            raise _snapshot_error(f"edges.type 非法：{kind!r}")
        if not all(
            isinstance(tid, str) and ctx.TID_RE.fullmatch(tid) for tid in (src, dst)
        ):
            raise _snapshot_error("edges.from/to 非法")
        if src not in members or dst not in members:
            raise _snapshot_error(f"edges 引用队列外成员：{src} -> {dst}")
        edges.append({"type": kind, "from": src, "to": dst})
    return edges


def _parse_snapshot(data: object) -> dict:
    if not isinstance(data, dict):
        raise _snapshot_error("快照须为对象")
    queue = data.get("queue")
    if (
        not isinstance(queue, list)
        or not queue
        or not all(isinstance(tid, str) and ctx.TID_RE.fullmatch(tid) for tid in queue)
    ):
        raise _snapshot_error("queue 非法")
    created_at = data.get("created_at")
    if created_at is not None and not isinstance(created_at, str):
        created_at = None
    return {
        "version": data.get("version", 1),
        "created_at": created_at or "",
        "queue": queue,
        "edges": _parse_edges(data.get("edges"), queue),
    }


def _read_snapshot() -> dict:
    try:
        data = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ctx.TaskDataError(
            f"队列快照 {ctx._rel(QUEUE_PATH)} 不存在；先运行 task.py goal 冻结队列"
        ) from None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as error:
        raise ctx.TaskDataError(
            f"队列快照 {ctx._rel(QUEUE_PATH)} 损坏（{error}）；重新运行 task.py goal --reset"
        ) from None
    return _parse_snapshot(data)


def _load_snapshot() -> dict:
    if not QUEUE_PATH.is_file():
        raise ctx.TaskDataError(
            f"队列快照 {ctx._rel(QUEUE_PATH)} 不存在；先运行 task.py goal 冻结队列"
        )
    return _read_snapshot()


def _worktree_registered(tid: str, registered: dict[str, str]) -> bool:
    path = str((ctx.REPO_ROOT / ctx.worktree_rel_path(tid)).resolve())
    return path in registered


def _classify(
    tid: str,
    events: list[dict],
    main_statuses: dict[str, str],
    registered: dict[str, str],
) -> tuple[str, str]:
    main_status = main_statuses.get(tid)
    if main_status == "done":
        return "integrated", "主干已归档"
    if main_status == "dropped":
        return "dropped", "已 dropped；队列快照过期，重新 task.py goal --reset"
    records = attempts_for_tid(tid, events)
    record = records[-1] if records else None
    if record is None:
        return "pending", "无 attempt 记录"
    if record["state"] == "integrated":
        return "integrated", "attempt 已 integrated"
    if record["state"] in ("reserved", "running"):
        return "running", f"attempt={record['attempt']} 执行中"
    report = record.get("report") or {}
    if report.get("status") == "blocked":
        return "blocked", report.get("reason", "")
    if record["terminal_status"] in ("failed", "stopped") or report.get("status") == "failed":
        fail_class = report.get("class", "task")
        return "failed", f"class={fail_class} {report.get('reason', '')}".strip()
    if record["terminal_status"] == "completed" and report.get("status") == "done":
        verdict, _kind, detail = verify_integrate_ready(
            tid, record["attempt"], record["execution_id"]
        )
        if verdict != "ready":
            return "unverified", detail
        if _worktree_registered(tid, registered):
            return "cleanup_pending", "worktree 仍登记，exact cleanup 未完成"
        return "closed", "执行闭环（terminal+report+handoff+cleanup）"
    return "incomplete", (
        f"terminal={record['terminal_status'] or '缺失'} "
        f"report={report.get('status') or '缺失'}"
    )


def _tid_items(value: object) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _queue_edges(queue: list[str], tasks: list[dict]) -> list[dict]:
    """队列成员间的 depends_on / conflicts_with 边。

    宽松解析主干 task 记录：goal-check 只读判定，不因图校验失败而失效。
    """
    members = set(queue)
    by_tid = {task["tid"]: task for task in tasks}
    edges = []
    for tid in queue:
        task = by_tid.get(tid) or {}
        for dep in _tid_items(task.get("depends_on")):
            if dep in members:
                edges.append({"type": "dep", "from": dep, "to": tid})
        for peer in _tid_items(task.get("conflicts_with")):
            if peer in members:
                edges.append({"type": "conflict", "from": tid, "to": peer})
    return edges


def _stops_with_skipped(
    queue: list[str], edges: list[dict], states: dict[str, str]
) -> list[tuple[str, str, list[str]]]:
    """每个停止成员与因它被跳过的成员：同链中排在它之后、尚未闭环的成员。

    同链出现多个停止成员时，被跳过成员归属最近的前一个停止成员。
    """
    stops = []
    for chain in group_queue(queue, edges):
        current = None
        for tid in chain:
            if states[tid] in STOP_STATES:
                current = (tid, states[tid], [])
                stops.append(current)
            elif current is not None and states[tid] not in CLOSED_STATES:
                current[2].append(tid)
    position = {tid: index for index, tid in enumerate(queue)}
    return sorted(stops, key=lambda item: position[item[0]])


def cmd_goal_check(args) -> None:
    require_primary_worktree()
    try:
        snapshot = _load_snapshot()
    except ctx.TaskDataError as error:
        sys.exit(str(error))
    queue = snapshot["queue"]
    edges = snapshot["edges"]
    events = ledger_read()
    main_statuses = {task["tid"]: task["status"] for task in scan_tasks()}
    registered = worktree_paths()
    rows = []
    for tid in queue:
        state, note = _classify(tid, events, main_statuses, registered)
        rows.append((tid, state, note))
        line = f"{tid} {state}"
        if note:
            line += f" — {note}"
        print(line)
    states = [state for _, state, _ in rows]
    stopped = [(tid, state) for tid, state, _ in rows if state in STOP_STATES]
    if stopped:
        states_by_tid = {tid: state for tid, state, _ in rows}
        stops = _stops_with_skipped(queue, edges, states_by_tid)
        for tid, state, skipped in stops:
            print(f"stopped_at={tid} state={state} skipped={','.join(skipped) or '-'}")
        skipped_all = {tid for _, _, skipped in stops for tid in skipped}
        can_continue = [
            tid for tid, state in states_by_tid.items()
            if state not in CLOSED_STATES | STOP_STATES and tid not in skipped_all
        ]
        if not can_continue:
            detail = " ".join(f"{tid}={state}" for tid, state in stopped)
            print(f"GOAL_QUEUE_STOPPED: {detail}")
            sys.exit(3)
    if all(state in CLOSED_STATES for state in states):
        awaiting = [tid for tid, state, _ in rows if state == "closed"]
        print(f"execution_complete=true integrated={len(rows) - len(awaiting)}/{len(rows)}")
        print(f"awaiting_merge={len(awaiting)} tids={','.join(awaiting) or '-'}")
        print("GOAL_QUEUE_COMPLETE")
        return
    closed_count = sum(1 for state in states if state in CLOSED_STATES)
    print(f"GOAL_QUEUE_INCOMPLETE: {closed_count}/{len(states)} closed")
    sys.exit(2)
