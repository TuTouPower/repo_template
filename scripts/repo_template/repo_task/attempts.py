"""Attempt lifecycle domain model and exact-identity commands."""

import uuid

import repo_task.context as ctx

from .ledger import (
    ledger_allocate_attempt,
    ledger_locked_append,
    ledger_locked_append_many,
    ledger_next_attempt,
)


OPEN_STATES = {"reserved", "running"}


def _valid_attempt(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _require_identity_values(attempt: int, execution_id: str) -> None:
    if not _valid_attempt(attempt):
        raise ctx.TaskDataError("attempt 必须是正整数且不能是 bool")
    if not isinstance(execution_id, str) or not execution_id:
        raise ctx.TaskDataError("execution_id 必须是非空字符串")


def project_attempts(events: list[dict]) -> dict[tuple[str, int, str], dict]:
    """Project immutable ledger events into exact attempt records."""
    records: dict[tuple[str, int, str], dict] = {}
    for event in events:
        tid = event.get("tid")
        attempt = event.get("attempt")
        execution_id = event.get("execution_id")
        if not (
            isinstance(tid, str)
            and _valid_attempt(attempt)
            and isinstance(execution_id, str)
            and execution_id
        ):
            continue
        key = (tid, attempt, execution_id)
        kind = event.get("event")
        if kind == "attempt_reserved":
            records[key] = {
                "tid": tid,
                "attempt": attempt,
                "execution_id": execution_id,
                "executor": event.get("executor", ""),
                "model": event.get("model", ""),
                "state": event.get("state", "running"),
                "terminal_status": "",
                "reserved": event,
                "terminal": None,
                "report": None,
                "integrated": None,
            }
            continue
        record = records.get(key)
        if record is None:
            continue
        if kind == "attempt_bound":
            # 兼容旧 ledger：bind 已退役，但旧 agent attempt 的 reserved→bound
            # 仍需转 running 才能继续 terminal/report/integrate。
            record["state"] = "running"
        elif kind == "attempt_terminal":
            record["state"] = "terminal"
            record["terminal_status"] = event.get("status", "")
            record["terminal"] = event
        elif kind == "report":
            record["report"] = event
        elif kind == "integrated":
            record["state"] = "integrated"
            record["integrated"] = event
    return records


def attempts_for_tid(tid: str, events: list[dict]) -> list[dict]:
    records = [record for record in project_attempts(events).values() if record["tid"] == tid]
    return sorted(records, key=lambda record: (record["attempt"], record["execution_id"]))


def current_attempt_record(tid: str, events: list[dict]) -> dict | None:
    records = attempts_for_tid(tid, events)
    return records[-1] if records else None


def current_attempt(tid: str, events: list[dict]) -> int | None:
    record = current_attempt_record(tid, events)
    return record["attempt"] if record else None


def current_identity(tid: str, events: list[dict]) -> tuple[int, str] | None:
    record = current_attempt_record(tid, events)
    if record is None:
        return None
    return record["attempt"], record["execution_id"]


def attempt_for_identity(
    tid: str, attempt: int, execution_id: str, events: list[dict]
) -> dict | None:
    if not _valid_attempt(attempt) or not isinstance(execution_id, str) or not execution_id:
        return None
    return project_attempts(events).get((tid, attempt, execution_id))


def overlapping_attempts(tid: str, events: list[dict]) -> set[int]:
    """Return attempt numbers that were involved in an illegal overlap.

    An overlap is recorded when an ``attempt_reserved`` arrives while another
    identity is still open (reserved/running). The ``invalid`` set is
    conservative: an attempt stays flagged until *every* identity it overlapped
    with has reached ``attempt_terminal``. Once all partner identities are
    closed, the flag is released so the tid can resume normal handling.
    """
    open_identities: dict[tuple[int, str], None] = {}
    invalid: set[int] = set()
    partners: dict[int, set[tuple[int, str]]] = {}
    for event in events:
        if event.get("tid") != tid:
            continue
        attempt = event.get("attempt")
        execution_id = event.get("execution_id")
        if not _valid_attempt(attempt) or not isinstance(execution_id, str) or not execution_id:
            continue
        identity = (attempt, execution_id)
        if event.get("event") == "attempt_reserved":
            if open_identities:
                invalid.add(attempt)
                partners.setdefault(attempt, set()).update(open_identities)
                for partner in open_identities:
                    invalid.add(partner[0])
                    partners.setdefault(partner[0], set()).add(identity)
            open_identities[identity] = None
        elif event.get("event") == "attempt_terminal":
            open_identities.pop(identity, None)
    for attempt_number in list(invalid):
        partner_set = partners.get(attempt_number, set())
        if partner_set and all(partner not in open_identities for partner in partner_set):
            invalid.discard(attempt_number)
    return invalid


def _require_exact_current(
    tid: str,
    attempt: int,
    execution_id: str,
    events: list[dict],
) -> dict:
    _require_identity_values(attempt, execution_id)
    current = current_attempt_record(tid, events)
    if current is None:
        raise ctx.TaskDataError(f"{tid} 尚未 reserve attempt")
    if current["attempt"] != attempt or current["execution_id"] != execution_id:
        raise ctx.TaskDataError(
            f"{tid} 当前 identity=({current['attempt']}, {current['execution_id']!r})，"
            f"收到旧或不匹配 identity=({attempt}, {execution_id!r})"
        )
    return current


def require_exact_terminal(
    tid: str,
    attempt: int,
    execution_id: str,
    events: list[dict],
    *,
    allow_integrated: bool = False,
) -> dict:
    record = _require_exact_current(tid, attempt, execution_id, events)
    allowed = {"terminal"}
    if allow_integrated:
        allowed.add("integrated")
    if record["state"] not in allowed:
        raise ctx.TaskDataError(
            f"{tid} attempt={attempt} execution_id={execution_id!r} state={record['state']!r}，"
            "须先 terminal"
        )
    if attempt in overlapping_attempts(tid, events):
        raise ctx.TaskDataError(f"{tid} attempt={attempt} 存在重叠 attempt，拒绝继续")
    return record


def reserve_attempt(tid: str, executor: str, model: str | None = None) -> dict:
    if not ctx.TID_RE.fullmatch(tid):
        raise ctx.TaskDataError(f"tid 非法：{tid!r}")
    if executor not in ctx.ATTEMPT_EXECUTORS:
        raise ctx.TaskDataError("executor 必须是 inline")

    # 领域层门禁：task 目录存在时校验 tid 存在、未归档，且必须已 start
    # （effective active + worktree 已登记）。防孤儿 running identity（RT-006）：
    # reserve 在 start 前执行会在 start 失败时留下无 worktree 的 running 记录，
    # 阻塞该 tid 后续执行。effective 状态（登记 worktree / 未合并分支 / main）为
    # active 即表示 start 已发生且 worktree 已登记——start 只写 worktree 副本，
    # main 视角恒为 backlog，不能用 scan_tasks()。
    if ctx.TASKS_DIR.is_dir():
        from .store import discover_effective_tasks
        task = discover_effective_tasks().get(tid)
        if task is None:
            raise ctx.TaskDataError(f"{tid} 不存在于 task 目录；拒绝 reserve 孤立 attempt")
        status = task.get("status", "")
        if status in ctx.ARCHIVED_STATUSES:
            raise ctx.TaskDataError(
                f"{tid} 已归档（{status}）；拒绝 reserve 新 attempt，"
                "需先 rewind 或显式恢复"
            )
        if status != "active":
            raise ctx.TaskDataError(
                f"{tid} 有效状态为 {status}；reserve 须在 start 之后"
                "（active + worktree 已登记）"
            )
        # worktree 必须真实登记：effective active 可能来自手改 main front matter
        # （无 worktree），那种情况 reserve 会产生孤儿 running identity，同样拒绝
        from .git_ops import worktree_paths
        wt_rel = ctx.worktree_rel_path(tid)
        if str((ctx.REPO_ROOT / wt_rel).resolve()) not in worktree_paths():
            raise ctx.TaskDataError(
                f"{tid} worktree {wt_rel} 未登记；reserve 须在 start 之后"
            )

    def build(attempt: int, events: list[dict]) -> dict:
        current = current_attempt_record(tid, events)
        if current:
            report = current.get("report") or {}
            if current["state"] in OPEN_STATES:
                raise ctx.TaskDataError(
                    f"{tid} 当前 attempt={current['attempt']} state={current['state']} 尚未 terminal；"
                    "拒绝 reserve 新 attempt"
                )
            if current["state"] == "integrated":
                raise ctx.TaskDataError(f"{tid} 当前 attempt 已 integrated；拒绝 reserve 新 attempt")
            if current["state"] == "terminal" and current.get("report") is None:
                raise ctx.TaskDataError(
                    f"{tid} 当前 attempt={current['attempt']} terminal 后尚未 report；"
                    "先 report 再 reserve 新 attempt"
                )
            retryable = (
                current.get("terminal_status") in {"failed", "stopped"}
                or report.get("status") in {"failed", "blocked"}
            )
            if not retryable:
                raise ctx.TaskDataError(
                    f"{tid} 当前 attempt={current['attempt']} terminal="
                    f"{current.get('terminal_status')!r} 尚待 integrate；"
                    "completed attempt 不可被新 reserve 顶掉"
                )
        execution_id = uuid.uuid4().hex
        event = {
            "event": "attempt_reserved",
            "execution_id": execution_id,
            "executor": executor,
            "state": "running",
        }
        if model:
            event["model"] = model
        return event

    return ledger_allocate_attempt(tid, build)

def terminal_attempt(
    tid: str, attempt: int, execution_id: str, status: str
) -> dict:
    if status not in ctx.LEDGER_TERMINAL_STATUSES:
        raise ctx.TaskDataError("terminal status 必须是 completed/failed/stopped")

    def build(events: list[dict]) -> dict:
        record = _require_exact_current(tid, attempt, execution_id, events)
        if record["state"] != "running":
            raise ctx.TaskDataError(f"attempt state={record['state']!r}，不能 terminal")
        return {
            "event": "attempt_terminal",
            "tid": tid,
            "attempt": attempt,
            "execution_id": execution_id,
            "status": status,
        }

    return ledger_locked_append(build)


def report_attempt(
    tid: str,
    attempt: int,
    execution_id: str,
    status: str,
    *,
    sha: str | None = None,
    fail_class: str | None = None,
    reason: str | None = None,
) -> dict:
    if status not in ctx.LEDGER_REPORT_STATUSES:
        raise ctx.TaskDataError("report status 必须是 done/blocked/failed")
    if fail_class is not None and fail_class not in ctx.LEDGER_FAIL_CLASSES:
        raise ctx.TaskDataError("report class 非法")

    def build(events: list[dict]) -> dict:
        record = _require_exact_current(tid, attempt, execution_id, events)
        if record["state"] != "terminal":
            raise ctx.TaskDataError(
                f"attempt state={record['state']!r}，report 必须在 terminal 后写入"
            )
        terminal_status = record.get("terminal_status", "")
        if status == "done" and terminal_status != "completed":
            raise ctx.TaskDataError(
                f"terminal_status={terminal_status!r} 不可写 report=done；"
                "done 仅匹配 terminal completed"
            )
        if record.get("report") is not None:
            raise ctx.TaskDataError("exact attempt 已存在 report；拒绝覆盖最终业务报告")
        event = {
            "event": "report",
            "tid": tid,
            "attempt": attempt,
            "execution_id": execution_id,
            "status": status,
        }
        if sha:
            event["sha"] = sha
        if fail_class:
            event["class"] = fail_class
        if reason:
            event["reason"] = reason
        return event

    return ledger_locked_append(build)


def append_integrated(
    tid: str,
    attempt: int,
    execution_id: str,
    merge_sha: str,
) -> dict:
    events = append_integrated_batch([{
        "tid": tid,
        "attempt": attempt,
        "execution_id": execution_id,
    }], merge_sha)
    if events:
        return events[0]
    raise ctx.TaskDataError(
        f"{tid} attempt={attempt} execution_id={execution_id!r} "
        "append_integrated_batch 返回空：所有 member 已 integrated 到同一 merge_sha"
    )


def append_integrated_batch(members: list[dict], merge_sha: str) -> list[dict]:
    """Atomically preflight and append every missing exact integrated event."""
    if not isinstance(merge_sha, str) or not merge_sha:
        raise ctx.TaskDataError("merge_sha 必须是非空字符串")

    def build(events: list[dict]) -> list[dict]:
        pending = []
        seen = set()
        for member in members:
            tid = member.get("tid")
            attempt = member.get("attempt")
            execution_id = member.get("execution_id")
            if not isinstance(tid, str) or not ctx.TID_RE.fullmatch(tid):
                raise ctx.TaskDataError(f"integrated member tid 非法：{tid!r}")
            _require_identity_values(attempt, execution_id)
            identity = (tid, attempt, execution_id)
            if identity in seen:
                raise ctx.TaskDataError(f"integrated batch 重复 identity：{identity!r}")
            seen.add(identity)
            record = _require_exact_current(tid, attempt, execution_id, events)
            if record["state"] == "integrated":
                existing_sha = (record.get("integrated") or {}).get("merge_sha")
                if existing_sha != merge_sha:
                    raise ctx.TaskDataError(
                        f"{tid} exact attempt 已 integrated 到 {existing_sha!r}，"
                        f"与 transaction merge_sha={merge_sha!r} 不符"
                    )
                continue
            record = require_exact_terminal(
                tid, attempt, execution_id, events
            )
            if record["terminal_status"] != "completed":
                raise ctx.TaskDataError(
                    f"{tid} terminal status={record['terminal_status']!r}，不能 integrated"
                )
            pending.append({
                "event": "integrated",
                "tid": tid,
                "attempt": attempt,
                "execution_id": execution_id,
                "merge_sha": merge_sha,
            })
        return pending

    return ledger_locked_append_many(build)


def in_flight_attempts(events: list[dict]) -> list[tuple[str, int, dict]]:
    """Return exact attempts that still occupy control-plane capacity."""
    rows = []
    tids = sorted({record["tid"] for record in project_attempts(events).values()})
    for tid in tids:
        records = attempts_for_tid(tid, events)
        current = records[-1] if records else None
        overlaps = overlapping_attempts(tid, events)
        for record in records:
            if record["state"] in {"reserved", "running", "terminal"} and (
                record is current or record["attempt"] in overlaps
            ):
                rows.append((tid, record["attempt"], record))
    return rows


def next_attempt(tid: str, events: list[dict]) -> int:
    return ledger_next_attempt(tid, events)
