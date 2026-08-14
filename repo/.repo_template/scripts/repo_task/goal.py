"""Goal 模式：冻结队列快照 + 只读终态判定。

goal 模式的提示词必须是可机器判定的终态，而不是过程指令。本模块提供一对命令：

- ``goal``：查看或冻结 ``docs/runtime/goal_queue.json``（已 gitignore，仅主仓）。
  无参且已有快照时只读展示，不改顺序；首次无快照才按 backlog ∪ active 升序冻结。
  重建须显式 tid 或 ``--reset``；覆盖且与旧队列不一致时须确认（或 ``--yes``）。
- ``goal-check``：只读判定器。权威 = ledger 投影 + 主干状态 + worktree 登记，
  不看 transcript。输出逐 tid 状态行与一个总结 marker：

  - ``GOAL_QUEUE_COMPLETE``（exit 0）：全部 closed/integrated
  - ``GOAL_QUEUE_STOPPED``（exit 3）：任一 blocked/failed，合法停止
  - ``GOAL_QUEUE_INCOMPLETE``（exit 2）：其余，goal 应继续

合并授权不在判定范围：merge 是 goal 结束后的人工步骤。
"""

import json
import os
import sys
from datetime import datetime

import repo_task.context as ctx

from .attempts import attempts_for_tid
from .documents import tid_sort_key
from .git_ops import require_primary_worktree, worktree_paths
from .ledger import ledger_read
from .monitoring import verify_integrate_ready
from .store import discover_effective_tasks, scan_tasks

QUEUE_PATH = ctx.RUNTIME_DIR / "goal_queue.json"

CLOSED_STATES = {"closed", "integrated"}
STOP_STATES = {"blocked", "failed", "dropped"}

GOAL_LINE_TEMPLATE = (
    '/goal 按 task-run skill 链式串行执行冻结队列 [{queue}]'
    "（快照 docs/runtime/goal_queue.json，禁止变更队列成员）。"
    "队列执行授权已给出：禁止逐 task 征求确认、禁止进入 plan mode；"
    "停止条件仅限 task-run skill「停止条件」列举项，task blocked 属合法停止，"
    "按 skill 汇报后停。整链完成后按 skill 询问一次合并授权。"
    "终态判定：在主仓根目录运行 python3 .repo_template/scripts/task.py goal-check——"
    "输出 GOAL_QUEUE_COMPLETE 或 GOAL_QUEUE_STOPPED 即本 goal 结束；"
    "GOAL_QUEUE_INCOMPLETE 表示继续。"
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
                raise ctx.TaskDataError(f"{tid} 已归档（{status}）；done/dropped 永不入队")
            if status == "blocked":
                raise ctx.TaskDataError(
                    f"{tid} 处于 blocked；先由用户决策（resume/drop）再生成 goal 队列"
                )
            queue.append(tid)
        return queue
    queue = [
        tid for tid, task in effective.items()
        if task["status"] in ("backlog", "active")
    ]
    return sorted(queue, key=tid_sort_key)


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


def _write_snapshot(queue: list[str]) -> dict:
    snapshot = {
        "version": 1,
        "created_at": datetime.now(ctx.TZ_CN).isoformat(timespec="seconds"),
        "queue": queue,
    }
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 临时文件 + os.replace 原子写，防并发/崩溃截断（F41/RT-007）
    temporary = QUEUE_PATH.with_name(QUEUE_PATH.name + ".tmp")
    temporary.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, QUEUE_PATH)
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

    if existing is not None:
        _confirm_overwrite(
            existing["queue"],
            queue,
            existing.get("created_at") or "",
            yes,
        )

    if not queue:
        _clear_snapshot()
        return

    snapshot = _write_snapshot(queue)
    action = "队列已重新冻结" if existing is not None else "队列已冻结"
    print(f"{action}：{_format_queue(queue)}")
    print(f"冻结时间：{snapshot['created_at']}")
    print(f"快照：{ctx._rel(QUEUE_PATH)}（goal 模式同时只服务一个队列）")
    print()
    _print_goal_line(queue)


def _parse_snapshot(data: object) -> dict:
    if not isinstance(data, dict):
        raise ctx.TaskDataError(
            f"队列快照 {ctx._rel(QUEUE_PATH)} 结构非法；重新运行 task.py goal --reset"
        )
    queue = data.get("queue")
    if (
        not isinstance(queue, list)
        or not queue
        or not all(isinstance(tid, str) and ctx.TID_RE.fullmatch(tid) for tid in queue)
    ):
        raise ctx.TaskDataError(
            f"队列快照 {ctx._rel(QUEUE_PATH)} 结构非法；重新运行 task.py goal --reset"
        )
    created_at = data.get("created_at")
    if created_at is not None and not isinstance(created_at, str):
        created_at = None
    return {
        "version": data.get("version", 1),
        "created_at": created_at or "",
        "queue": queue,
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


def _load_snapshot() -> list[str]:
    if not QUEUE_PATH.is_file():
        raise ctx.TaskDataError(
            f"队列快照 {ctx._rel(QUEUE_PATH)} 不存在；先运行 task.py goal 冻结队列"
        )
    return _read_snapshot()["queue"]


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
        verdict, detail = verify_integrate_ready(
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


def cmd_goal_check(args) -> None:
    require_primary_worktree()
    try:
        queue = _load_snapshot()
    except ctx.TaskDataError as error:
        sys.exit(str(error))
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
        detail = " ".join(f"{tid}={state}" for tid, state in stopped)
        print(f"GOAL_QUEUE_STOPPED: {detail}")
        sys.exit(3)
    if all(state in CLOSED_STATES for state in states):
        print("GOAL_QUEUE_COMPLETE")
        return
    closed_count = sum(1 for state in states if state in CLOSED_STATES)
    print(f"GOAL_QUEUE_INCOMPLETE: {closed_count}/{len(states)} closed")
    sys.exit(2)
