"""Goal 模式：冻结队列快照 + 只读终态判定。

goal 模式的提示词必须是可机器判定的终态，而不是过程指令。本模块提供一对命令：

- ``goal``：按 task-run 入队规则冻结队列到 ``docs/runtime/goal_queue.json``
  （已 gitignore，仅主仓），打印 ready-to-paste 的 ``/goal`` 行。
- ``goal-check``：只读判定器。权威 = ledger 投影 + 主干状态 + worktree 登记，
  不看 transcript。输出逐 tid 状态行与一个总结 marker：

  - ``GOAL_QUEUE_COMPLETE``（exit 0）：全部 closed/integrated
  - ``GOAL_QUEUE_STOPPED``（exit 3）：任一 blocked/failed，合法停止
  - ``GOAL_QUEUE_INCOMPLETE``（exit 2）：其余，goal 应继续

合并授权不在判定范围：merge 是 goal 结束后的人工步骤。
"""

import json
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
    "终态判定：在主仓根目录运行 python3 scripts/repo_template/task.py goal-check——"
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


def cmd_goal(args) -> None:
    require_primary_worktree()
    try:
        queue = _compute_queue(args)
    except ctx.TaskDataError as error:
        sys.exit(str(error))
    if not queue:
        if QUEUE_PATH.exists():
            QUEUE_PATH.unlink()
            print("旧队列快照已清除。")
        print("队列为空：没有 backlog/active task；不生成 goal。")
        return
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "version": 1,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "queue": queue,
    }
    QUEUE_PATH.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"队列已冻结：{', '.join(queue)}")
    print(f"快照：{ctx._rel(QUEUE_PATH)}（覆盖式；goal 模式同时只服务一个队列）")
    print()
    print("粘贴以下 /goal 行启动自治执行：")
    print()
    print("```text")
    print(GOAL_LINE_TEMPLATE.format(queue=", ".join(queue)))
    print("```")


def _load_snapshot() -> list[str]:
    if not QUEUE_PATH.is_file():
        raise ctx.TaskDataError(
            f"队列快照 {ctx._rel(QUEUE_PATH)} 不存在；先运行 task.py goal 冻结队列"
        )
    try:
        snapshot = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ctx.TaskDataError(
            f"队列快照 {ctx._rel(QUEUE_PATH)} 损坏（{error}）；重新运行 task.py goal"
        ) from None
    queue = snapshot.get("queue")
    if (
        not isinstance(queue, list)
        or not queue
        or not all(isinstance(tid, str) and ctx.TID_RE.fullmatch(tid) for tid in queue)
    ):
        raise ctx.TaskDataError(
            f"队列快照 {ctx._rel(QUEUE_PATH)} 结构非法；重新运行 task.py goal"
        )
    return queue


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
        return "dropped", "已 dropped；队列快照过期，重新 task.py goal"
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
