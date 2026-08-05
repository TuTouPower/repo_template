"""调度控制面：统一 attempt 生命周期、账本、ps、reconcile 测试。"""

import argparse
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import sys

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "repo_template"
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task import context as ctx
from repo_task import control
from repo_task.attempts import (
    append_integrated_batch,
    bind_attempt,
    current_attempt_record,
    escalate_attempt,
    project_attempts,
    report_attempt,
    reserve_attempt,
    silent_alert_attempt,
    terminal_attempt,
)
from repo_task.ledger import ledger_append, ledger_next_attempt, ledger_read
from repo_task.monitoring import (
    compute_ps_rows,
    compute_reconcile_plan,
    is_silent,
)

NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)


def _ts(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """账本路径重定向到临时目录。"""
    runtime = tmp_path / "docs" / "runtime"
    monkeypatch.setattr(ctx, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(ctx, "LEDGER_PATH", runtime / "dispatch_ledger.jsonl")
    # 裸账本单测：task 目录指向不存在的临时路径，跳过 reserve_attempt 的
    # 领域层 tid 校验（生产环境该目录必存在，校验生效）。
    monkeypatch.setattr(ctx, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(ctx, "ARCHIVE_TASKS_DIR", tmp_path / "archive_tasks")
    return runtime / "dispatch_ledger.jsonl"


def _identity(event):
    return event["attempt"], event["execution_id"]


def _eid(tid: str, attempt: int) -> str:
    return f"exec-{tid}-{attempt}"


def _reserved(
    tid: str,
    attempt: int = 1,
    *,
    executor: str = "inline",
    model: str | None = None,
    execution_id: str | None = None,
    ts: str | None = None,
) -> dict:
    event = {
        "event": "attempt_reserved",
        "tid": tid,
        "attempt": attempt,
        "execution_id": execution_id or _eid(tid, attempt),
        "executor": executor,
        "state": "running" if executor == "inline" else "reserved",
    }
    if model:
        event["model"] = model
    if ts:
        event["ts"] = ts
    return event


def _bound(
    tid: str,
    attempt: int = 1,
    *,
    execution_id: str | None = None,
    host_worker_id: str = "worker-1",
    ts: str | None = None,
) -> dict:
    event = {
        "event": "attempt_bound",
        "tid": tid,
        "attempt": attempt,
        "execution_id": execution_id or _eid(tid, attempt),
        "host_worker_id": host_worker_id,
    }
    if ts:
        event["ts"] = ts
    return event


def _running(
    tid: str,
    attempt: int = 1,
    *,
    model: str | None = None,
    host_worker_id: str | None = None,
    execution_id: str | None = None,
    ts: str | None = None,
) -> list[dict]:
    execution_id = execution_id or _eid(tid, attempt)
    if host_worker_id is None:
        return [_reserved(
            tid, attempt, executor="inline", model=model,
            execution_id=execution_id, ts=ts,
        )]
    return [
        _reserved(
            tid, attempt, executor="agent", model=model,
            execution_id=execution_id, ts=ts,
        ),
        _bound(
            tid, attempt, execution_id=execution_id,
            host_worker_id=host_worker_id, ts=ts,
        ),
    ]


def _terminal(
    tid: str,
    attempt: int = 1,
    *,
    status: str = "completed",
    model: str | None = None,
    host_worker_id: str | None = None,
    execution_id: str | None = None,
    reserved_ts: str | None = None,
    terminal_ts: str | None = None,
) -> list[dict]:
    execution_id = execution_id or _eid(tid, attempt)
    events = _running(
        tid, attempt, model=model, host_worker_id=host_worker_id,
        execution_id=execution_id, ts=reserved_ts,
    )
    event = {
        "event": "attempt_terminal",
        "tid": tid,
        "attempt": attempt,
        "execution_id": execution_id,
        "status": status,
    }
    if terminal_ts:
        event["ts"] = terminal_ts
    events.append(event)
    return events


def _report(
    tid: str,
    attempt: int = 1,
    *,
    status: str,
    execution_id: str | None = None,
    sha: str | None = None,
    fail_class: str | None = None,
    reason: str | None = None,
    ts: str | None = None,
) -> dict:
    event = {
        "event": "report",
        "tid": tid,
        "attempt": attempt,
        "execution_id": execution_id or _eid(tid, attempt),
        "status": status,
    }
    if sha:
        event["sha"] = sha
    if fail_class:
        event["class"] = fail_class
    if reason:
        event["reason"] = reason
    if ts:
        event["ts"] = ts
    return event


def _integrated(tid: str, attempt: int = 1, *, execution_id: str | None = None) -> dict:
    return {
        "event": "integrated",
        "tid": tid,
        "attempt": attempt,
        "execution_id": execution_id or _eid(tid, attempt),
        "merge_sha": f"merge-{tid}-{attempt}",
        "ts": _ts(1),
    }


def _escalated(tid: str, attempt: int = 1, *, execution_id: str | None = None) -> dict:
    return {
        "event": "escalated",
        "tid": tid,
        "attempt": attempt,
        "execution_id": execution_id or _eid(tid, attempt),
        "reason": "用户处理",
        "ts": _ts(1),
    }


def _record(**kwargs):
    defaults = dict(event=None, tid=None, reason=None)
    defaults.update(kwargs)
    control.cmd_ledger_record(argparse.Namespace(**defaults))


# --------------------------------------------------------------------------
# 账本基础设施
# --------------------------------------------------------------------------


def test_ledger_append_read_roundtrip(ledger):
    first = ledger_append(_reserved("t001", model="opus"))
    ledger_append({"event": "note", "text": "中文备注"})

    events = ledger_read()

    assert [event["event"] for event in events] == ["attempt_reserved", "note"]
    assert first["tid"] == "t001" and first["attempt"] == 1
    assert first["execution_id"] == _eid("t001", 1)
    assert "ts" in first
    assert events[1]["text"] == "中文备注"


def test_ledger_append_sanitizes_newlines(ledger):
    ledger_append({"event": "note", "text": "第一行\n第二行\r第三行"})

    raw_lines = ledger.read_text(encoding="utf-8").splitlines()

    assert len(raw_lines) == 1
    assert ledger_read()[0]["text"] == "第一行 第二行 第三行"


def test_ledger_read_missing_file_returns_empty(ledger):
    assert ledger_read() == []


def test_ledger_read_corrupted_line_self_heals(ledger, capsys):
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        '{"event": "note", "text": "好行1"}\n{"event": "note"\ntruncated\n',
        encoding="utf-8",
    )

    events = ledger_read()

    assert [event["text"] for event in events] == ["好行1"]
    err = capsys.readouterr().err
    assert "第 2 行" in err and "已跳过" in err


def test_ledger_next_attempt_increments_per_tid():
    events = [
        _reserved("t001", 1),
        _reserved("t002", 1),
        _reserved("t001", 3),
        {"event": "note", "tid": "t001"},
    ]

    assert ledger_next_attempt("t001", events) == 4
    assert ledger_next_attempt("t002", events) == 2
    assert ledger_next_attempt("t003", events) == 1


# --------------------------------------------------------------------------
# ledger record / tail 与 attempt 命令边界
# --------------------------------------------------------------------------


def test_reserve_assigns_next_attempt_only_after_retryable_terminal(ledger):
    first = reserve_attempt("t001", "agent", "opus")
    bind_attempt("t001", *_identity(first), "task-123")
    terminal_attempt("t001", *_identity(first), "failed")
    report_attempt("t001", *_identity(first), "failed", fail_class="infra")
    second = reserve_attempt("t001", "agent", "haiku")
    bind_attempt("t001", *_identity(second), "task-456")

    records = project_attempts(ledger_read())
    assert [first["attempt"], second["attempt"]] == [1, 2]
    assert records[("t001", 1, first["execution_id"])]["host_worker_id"] == "task-123"
    assert records[("t001", 2, second["execution_id"])]["host_worker_id"] == "task-456"


def test_parallel_dispatch_and_escalate_require_prior_terminal(ledger):
    first = reserve_attempt("t001", "inline", "opus")

    with pytest.raises(ctx.TaskDataError, match="尚未 terminal"):
        reserve_attempt("t001", "inline", "haiku")
    with pytest.raises(ctx.TaskDataError, match="须先 terminal"):
        escalate_attempt("t001", *_identity(first), "still running")

    terminal_attempt("t001", *_identity(first), "stopped")
    second = reserve_attempt("t001", "inline", "haiku")
    assert second["attempt"] == 2


def test_record_validation(ledger):
    for event in (
        "attempt_reserved", "attempt_bound", "attempt_terminal", "report",
        "escalated", "integrated", "silent_alerted",
    ):
        with pytest.raises(SystemExit, match="不允许生命周期事件"):
            _record(event=event, tid="t001")
    assert ledger_read() == []


def test_record_note_uses_text_field(ledger):
    _record(event="note", reason="自由备注")

    assert ledger_read()[0]["text"] == "自由备注"


@pytest.mark.parametrize("event", ("report", "escalated", "silent_alerted", "attempt_terminal"))
def test_record_attempt_events_require_explicit_attempt_even_after_dispatch(ledger, event):
    reserved = reserve_attempt("t001", "inline", "opus")

    with pytest.raises(SystemExit, match="不允许生命周期事件"):
        _record(event=event, tid="t001")

    assert [item["event"] for item in ledger_read()] == ["attempt_reserved"]
    assert reserved["execution_id"]


def test_ledger_tail_filters_tid_and_reverses(ledger, capsys):
    ledger_append(_reserved("t001", model="opus"))
    ledger_append(_report(
        "t001", status="failed", fail_class="infra", reason="boom",
    ))
    ledger_append(_reserved("t002", model="haiku"))

    control.cmd_ledger_tail(argparse.Namespace(tid="t001", n=20))

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert "report t001#1" in lines[0] and "class=infra" in lines[0]
    assert "attempt_reserved t001#1" in lines[1] and "model=opus" in lines[1]
    assert all("t002" not in line for line in lines)


# --------------------------------------------------------------------------
# silent 判定与 ps 活表
# --------------------------------------------------------------------------


def test_is_silent_threshold():
    assert is_silent(_ts(21), 20, NOW)
    assert not is_silent(_ts(19), 20, NOW)
    assert not is_silent("无法解析", 20, NOW)


def _observation(activities, events, attempt, execution_id):
    tid = next((event.get("tid") for event in events if event.get("tid")), None)
    observed_at = activities.get(tid)
    if observed_at is None:
        return None
    return {
        "event": "observation",
        "tid": tid,
        "attempt": attempt,
        "execution_id": execution_id,
        "ts": observed_at,
        "fingerprint": f"fp-{tid}",
        "dirty": "clean",
    }


def _ps_rows(
    events,
    effective=None,
    main_statuses=None,
    activities=None,
    verify=("incomplete", "refs 未完成"),
):
    activities = activities or {}
    return compute_ps_rows(
        events,
        effective or {},
        main_statuses or {},
        silent_minutes=20,
        now=NOW,
        observer=lambda tev, attempt, execution_id: _observation(
            activities, tev, attempt, execution_id
        ),
        verifier=lambda *_: verify,
    )


def test_ps_pending_backlog_never_dispatched():
    rows = _ps_rows(
        [{"event": "note", "tid": "t009", "ts": _ts(1)}],
        effective={"t001": {"status": "backlog"}},
        main_statuses={"t001": "backlog"},
    )

    assert rows == []


def test_ps_progressing_and_silent_from_agent_observation():
    events = [
        *_running("t001", model="opus", host_worker_id="w1", ts=_ts(5)),
        *_running("t002", model="opus", host_worker_id="w2", ts=_ts(30)),
    ]
    rows = _ps_rows(events, activities={"t001": _ts(5), "t002": _ts(30)})

    by_tid = {row["tid"]: row for row in rows}
    assert by_tid["t001"]["state"] == "progressing"
    assert by_tid["t001"]["model"] == "opus"
    assert by_tid["t001"]["host_worker_id"] == "w1"
    assert by_tid["t002"]["state"] == "silent?"


def test_ps_inline_running_never_uses_silence_observer():
    rows = _ps_rows(_running("t001", ts=_ts(5)), activities={"t001": _ts(30)})

    assert rows[0]["state"] == "running(inline)"


def test_ps_failed_blocked_reported_terminal_states():
    events = [
        *_terminal("t001", status="failed", reserved_ts=_ts(10), terminal_ts=_ts(9)),
        _report(
            "t001", status="failed", fail_class="infra",
            reason="provider 不兼容", ts=_ts(8),
        ),
        *_terminal("t002", reserved_ts=_ts(10), terminal_ts=_ts(9)),
        _report("t002", status="blocked", reason="review 满轮", ts=_ts(8)),
        *_terminal("t003", reserved_ts=_ts(10), terminal_ts=_ts(9)),
        _report("t003", status="done", sha="abc123", ts=_ts(8)),
        *_terminal("t004", reserved_ts=_ts(10), terminal_ts=_ts(9)),
        _integrated("t004"),
    ]
    rows = _ps_rows(events, main_statuses={"t004": "done"})

    by_tid = {row["tid"]: row for row in rows}
    assert by_tid["t001"]["state"] == "failed:infra"
    assert by_tid["t001"]["note"] == "provider 不兼容"
    assert by_tid["t002"]["state"] == "blocked"
    assert by_tid["t003"]["state"] == "terminal:completed"
    assert by_tid["t004"]["state"] == "done"


# --------------------------------------------------------------------------
# reconcile 行动计划
# --------------------------------------------------------------------------


def _schedule(selected=(), conflicts=None):
    return {
        "selected": list(selected),
        "conflicts": conflicts or {},
        "main_done_set": set(),
        "dropped_set": set(),
        "tasks": {},
    }


def _plan(
    events,
    schedule=None,
    activities=None,
    verify=("incomplete", "refs 未完成"),
    mode="restart",
    **overrides,
):
    activities = activities or {}
    options = dict(
        limit=3,
        scope=None,
        silent_minutes=20,
        max_auto_retries=1,
        now=NOW,
        observer=lambda tev, attempt, execution_id: _observation(
            activities, tev, attempt, execution_id
        ),
        verifier=lambda *_: verify,
        mode_probe=lambda *_: mode,
    )
    options.update(overrides)
    return compute_reconcile_plan(events, schedule or _schedule(), **options)


def test_reconcile_empty_plan_when_nothing_in_flight():
    plan = _plan([])

    assert plan["actions"] == []
    assert plan["occupancy"] == {"used": 0, "limit": 3}


def test_reconcile_progressing_occupies_slot_and_fills_from_selected():
    events = _running("t001", model="opus", ts=_ts(5))
    schedule = _schedule(selected=["t002", "t003"])
    plan = _plan(
        events, schedule, activities={"t001": _ts(5)},
    )

    assert plan["occupancy"]["used"] == 1
    assert [(action["action"], action["tid"]) for action in plan["actions"]] == [
        ("dispatch", "t002"), ("dispatch", "t003"),
    ]
    assert all(action["model"] is None for action in plan["actions"])
    assert all(action["attempt"] == 1 for action in plan["actions"])
    assert all("execution_id" not in action for action in plan["actions"])


def test_reconcile_dispatch_skips_conflicts_with_in_flight():
    events = _running("t001", ts=_ts(5))
    schedule = _schedule(
        selected=["t002", "t003"],
        conflicts={"t001": {"t002"}, "t002": {"t001"}, "t003": set()},
    )
    plan = _plan(events, schedule, activities={"t001": _ts(5)})

    assert [(action["action"], action["tid"]) for action in plan["actions"]] == [
        ("dispatch", "t003")
    ]


def test_reconcile_report_done_verified_is_integrate():
    events = [
        *_terminal("t001", reserved_ts=_ts(10), terminal_ts=_ts(3)),
        _report("t001", status="done", ts=_ts(2)),
    ]
    plan = _plan(events, verify=("ready", "分支 tip terminal + exact handoff"))

    assert [(a["action"], a["tid"], a["attempt"]) for a in plan["actions"]] == [
        ("integrate", "t001", 1),
    ]
    assert plan["actions"][0]["execution_id"] == _eid("t001", 1)
    assert plan["occupancy"]["used"] == 1


def test_reconcile_completed_contract_escalates_without_retry():
    events = [
        *_terminal("t001", model="opus", reserved_ts=_ts(10), terminal_ts=_ts(3)),
        _report("t001", status="done", ts=_ts(2)),
    ]
    plan = _plan(
        events,
        verify=("contract", "分支 tip 缺 handoff.json"),
        mode="resume",
    )

    action, = plan["actions"]
    assert action["action"] == "escalate"
    assert action["attempt"] == 1
    assert action["execution_id"] == _eid("t001", 1)
    assert "completed attempt" in action["reason"]
    assert "禁止自动 retry" in action["reason"]


def test_reconcile_report_blocked_escalates():
    events = [
        *_terminal("t001", reserved_ts=_ts(10), terminal_ts=_ts(3)),
        _report("t001", status="blocked", reason="review 满轮", ts=_ts(2)),
    ]
    plan = _plan(events)

    action, = plan["actions"]
    assert action["action"] == "escalate"
    assert action["execution_id"] == _eid("t001", 1)
    assert "blocked" in action["reason"]


def test_reconcile_silent_alerts_without_redispatch():
    events = _running(
        "t001", model="opus", host_worker_id="worker-7", ts=_ts(30),
    )
    plan = _plan(events, activities={"t001": _ts(30)})

    action, = plan["actions"]
    assert action["action"] == "alert-silent"
    assert action["attempt"] == 1
    assert action["execution_id"] == _eid("t001", 1)
    assert action["model"] == "opus"
    assert action["host_worker_id"] == "worker-7"
    assert action["fingerprint"] == "fp-t001"
    assert "30 分钟" in action["reason"]
    assert plan["occupancy"]["used"] == 1
    assert plan["silent_hold"] is True


def test_reconcile_failed_infra_retries_same_model():
    events = [
        *_terminal(
            "t001", status="failed", model="opus",
            reserved_ts=_ts(10), terminal_ts=_ts(7),
        ),
        _report(
            "t001", status="failed", fail_class="infra",
            reason="API 错误", ts=_ts(8),
        ),
    ]
    plan = _plan(events)

    action, = plan["actions"]
    assert action["action"] == "dispatch"
    assert action["attempt"] == 2
    assert action["model"] == "opus"
    assert "infra" in action["reason"]


def test_reconcile_escalates_when_retry_budget_exhausted():
    events = [
        *_terminal(
            "t001", 1, status="failed", model="opus",
            reserved_ts=_ts(60), terminal_ts=_ts(50),
        ),
        _report("t001", 1, status="failed", fail_class="infra", ts=_ts(49)),
        *_terminal(
            "t001", 2, status="failed", model="haiku",
            reserved_ts=_ts(40), terminal_ts=_ts(30),
        ),
        _report("t001", 2, status="failed", fail_class="infra", ts=_ts(29)),
    ]
    plan = _plan(events, max_auto_retries=1)

    action, = plan["actions"]
    assert action["action"] == "escalate"
    assert action["attempt"] == 2
    assert action["execution_id"] == _eid("t001", 2)
    assert "额度" in action["reason"]


def test_reconcile_scope_limits_actions_but_not_occupancy():
    events = [
        *_running("t001", host_worker_id="w1", ts=_ts(5)),
        *_running("t002", host_worker_id="w2", ts=_ts(30)),
    ]
    schedule = _schedule(selected=["t003"])
    plan = _plan(
        events,
        schedule,
        activities={"t001": _ts(5), "t002": _ts(30)},
        scope={"t002"},
    )

    assert [(a["action"], a["tid"]) for a in plan["actions"]] == [
        ("alert-silent", "t002")
    ]
    assert plan["occupancy"] == {"used": 2, "limit": 3}
    assert plan["silent_hold"] is True


def test_reconcile_integrated_and_escalated_end_flight():
    events = [
        *_terminal("t001", reserved_ts=_ts(10), terminal_ts=_ts(8)),
        _integrated("t001"),
        *_terminal("t002", reserved_ts=_ts(10), terminal_ts=_ts(8)),
        _escalated("t002"),
    ]
    plan = _plan(events)

    assert plan["actions"] == []
    assert plan["occupancy"]["used"] == 0


# --------------------------------------------------------------------------
# refs 派生 READY_MERGE
# --------------------------------------------------------------------------


def test_reconcile_refs_derived_integrate_without_report():
    events = _terminal(
        "t001", model="opus", reserved_ts=_ts(10), terminal_ts=_ts(2),
    )
    plan = _plan(events, verify=("ready", "terminal + exact handoff 齐备"))

    action, = plan["actions"]
    assert action["action"] == "integrate"
    assert action["tid"] == "t001" and action["attempt"] == 1
    assert action["execution_id"] == _eid("t001", 1)
    assert "terminal completed + refs 验证通过" in action["reason"]
    assert plan["occupancy"]["used"] == 1


def test_reconcile_report_done_reason_differs_from_refs_derived():
    events = [
        *_terminal("t001", reserved_ts=_ts(10), terminal_ts=_ts(3)),
        _report("t001", status="done", ts=_ts(2)),
    ]
    plan = _plan(events, verify=("ready", "terminal + exact handoff 齐备"))

    action, = plan["actions"]
    assert action["action"] == "integrate"
    assert "terminal completed + refs 验证通过" in action["reason"]


def test_reconcile_completed_missing_handoff_escalates_without_retry():
    events = _terminal(
        "t001", model="opus", reserved_ts=_ts(10), terminal_ts=_ts(2),
    )
    plan = _plan(
        events,
        verify=("contract", "分支 tip 缺 docs/archive/tasks/t001_x/handoff.json"),
        mode="resume",
    )

    action, = plan["actions"]
    assert action["action"] == "escalate"
    assert action["attempt"] == 1
    assert action["execution_id"] == _eid("t001", 1)
    assert "completed attempt" in action["reason"]


def test_reconcile_report_blocked_wins_over_incomplete_refs():
    events = [
        *_terminal("t001", reserved_ts=_ts(10), terminal_ts=_ts(3)),
        _report("t001", status="blocked", ts=_ts(2)),
    ]
    plan = _plan(events, verify=("incomplete", "分支 tip status='active' 非终态"))

    action, = plan["actions"]
    assert action["action"] == "escalate"
    assert "blocked" in action["reason"]


def test_ps_refs_ready_shows_done_pending_merge():
    rows = _ps_rows(
        _terminal("t001", reserved_ts=_ts(10), terminal_ts=_ts(2)),
        verify=("ready", "terminal + exact handoff 齐备"),
    )

    assert rows[0]["state"] == "done待合并"
    assert rows[0]["note"] == "terminal + exact handoff 齐备"


# --------------------------------------------------------------------------
# 处置、闩锁、占槽、mode 与去重回归
# --------------------------------------------------------------------------


def test_reconcile_note_does_not_mask_failed():
    events = [
        *_terminal(
            "t001", status="failed", model="opus",
            reserved_ts=_ts(10), terminal_ts=_ts(7),
        ),
        _report(
            "t001", status="failed", fail_class="infra",
            reason="provider 不兼容", ts=_ts(8),
        ),
        {"event": "note", "tid": "t001", "text": "随便一句", "ts": _ts(6)},
    ]
    plan = _plan(events, mode="restart")

    action, = plan["actions"]
    assert action["action"] == "dispatch"
    assert action["model"] == "opus"
    assert "infra" in action["reason"]


def test_reconcile_report_status_failed_is_failure_path():
    events = [
        *_terminal(
            "t001", status="failed", model="opus",
            reserved_ts=_ts(10), terminal_ts=_ts(7),
        ),
        _report("t001", status="failed", reason="黑盒满轮", ts=_ts(8)),
    ]
    plan = _plan(events, mode="resume")

    action, = plan["actions"]
    assert action["action"] == "dispatch"
    assert action["model"] == "opus"
    assert action["mode"] == "resume"
    assert "task 失败" in action["reason"] and "黑盒满轮" in action["reason"]


def test_ps_old_attempt_blocked_report_does_not_pollute_new_attempt():
    events = [
        *_terminal("t001", 1, reserved_ts=_ts(30), terminal_ts=_ts(26)),
        _report("t001", 1, status="blocked", ts=_ts(25)),
        *_running("t001", 2, model="haiku", ts=_ts(5)),
    ]
    rows = _ps_rows(events, activities={"t001": _ts(5)})

    assert rows[0]["attempt"] == 2
    assert rows[0]["execution_id"] == _eid("t001", 2)
    assert rows[0]["state"] == "running(inline)"


def test_reconcile_effective_blocked_escalates_without_retry_budget():
    events = _terminal(
        "t001", model="opus", reserved_ts=_ts(60), terminal_ts=_ts(2),
    )
    schedule = _schedule()
    schedule["tasks"] = {"t001": {"status": "blocked"}}
    plan = _plan(events, schedule)

    action, = plan["actions"]
    assert action["action"] == "escalate"
    assert action["execution_id"] == _eid("t001", 1)
    assert "blocked" in action["reason"]
    assert plan["occupancy"]["used"] == 0


def test_reconcile_escalate_latch_blocks_auto_redispatch():
    events = [
        *_terminal("t001", reserved_ts=_ts(30), terminal_ts=_ts(21)),
        _escalated("t001"),
    ]
    schedule = _schedule(selected=["t001", "t002"])
    plan = _plan(events, schedule)

    assert [(a["action"], a["tid"]) for a in plan["actions"]] == [
        ("dispatch", "t002")
    ]


def test_reconcile_escalate_latch_released_by_new_dispatch():
    events = [
        *_terminal("t001", 1, reserved_ts=_ts(30), terminal_ts=_ts(21)),
        _escalated("t001", 1),
        *_running("t001", 2, model="haiku", ts=_ts(5)),
    ]
    schedule = _schedule(selected=["t001", "t002"])
    plan = _plan(events, schedule, activities={"t001": _ts(5)})

    assert [(a["action"], a["tid"]) for a in plan["actions"]] == [
        ("dispatch", "t002")
    ]
    assert plan["occupancy"]["used"] == 1


def test_reconcile_silent_attempts_hold_all_slots_without_dispatch():
    events = [
        event
        for tid in ("t001", "t002", "t003")
        for event in _running(
            tid, model="opus", host_worker_id=f"worker-{tid}", ts=_ts(30)
        )
    ]
    schedule = _schedule(selected=["t004", "t005"])
    plan = _plan(
        events,
        schedule,
        activities={tid: _ts(30) for tid in ("t001", "t002", "t003")},
        limit=3,
    )

    assert [(a["action"], a["tid"]) for a in plan["actions"]] == [
        ("alert-silent", "t001"),
        ("alert-silent", "t002"),
        ("alert-silent", "t003"),
    ]
    assert plan["occupancy"] == {"used": 3, "limit": 3}
    assert plan["silent_hold"] is True


def test_reconcile_escalates_when_retry_budget_exhausted_same_model():
    events = [
        *_terminal(
            "t001", 1, status="failed", model="opus",
            reserved_ts=_ts(60), terminal_ts=_ts(50),
        ),
        _report("t001", 1, status="failed", fail_class="infra", ts=_ts(49)),
        *_terminal(
            "t001", 2, status="failed", model="opus",
            reserved_ts=_ts(40), terminal_ts=_ts(30),
        ),
        _report("t001", 2, status="failed", fail_class="infra", ts=_ts(29)),
    ]
    plan = _plan(events, max_auto_retries=1)

    action, = plan["actions"]
    assert action["action"] == "escalate"
    assert "额度" in action["reason"]


def test_reconcile_redispatch_mode_restart_vs_resume():
    events = [
        *_terminal(
            "t001", status="failed", model="opus",
            reserved_ts=_ts(10), terminal_ts=_ts(7),
        ),
        _report("t001", status="failed", fail_class="infra", ts=_ts(8)),
    ]
    restart = _plan(events, mode="restart")
    resume = _plan(events, mode="resume")

    assert restart["actions"][0]["mode"] == "restart"
    assert resume["actions"][0]["mode"] == "resume"


def test_reconcile_duplicate_dispatch_deduped():
    duplicate = _reserved("t001", model="opus", ts=_ts(10))
    events = [duplicate, {**duplicate, "ts": _ts(9)}]
    plan = _plan(events, activities={"t001": _ts(5)})

    action, = plan["actions"]
    assert action["action"] == "await-terminal"
    assert action["attempt"] == 1
    assert action["execution_id"] == _eid("t001", 1)
    assert plan["occupancy"]["used"] == 1


def test_is_silent_naive_timestamp_tolerated():
    naive_old = (NOW - timedelta(minutes=30)).astimezone().replace(tzinfo=None)
    naive_new = (NOW - timedelta(minutes=10)).astimezone().replace(tzinfo=None)

    assert is_silent(naive_old.isoformat(timespec="seconds"), 20, NOW)
    assert not is_silent(naive_new.isoformat(timespec="seconds"), 20, NOW)


def test_reconcile_occupancy_counts_out_of_scope_in_flight():
    events = [
        event
        for tid in ("t001", "t002", "t003")
        for event in _running(
            tid, model="opus", host_worker_id=f"worker-{tid}", ts=_ts(30)
        )
    ]
    schedule = _schedule(selected=["t004"])
    plan = _plan(
        events,
        schedule,
        activities={tid: _ts(30) for tid in ("t001", "t002", "t003")},
        scope={"t004"},
        limit=3,
    )

    assert plan["actions"] == []
    assert plan["occupancy"] == {"used": 3, "limit": 3}
    assert plan["silent_hold"] is True


def test_reconcile_terminal_on_main_does_not_block_conflict_peer():
    events = _running("t001", model="opus", ts=_ts(30))
    schedule = _schedule(
        selected=["t002"],
        conflicts={"t001": {"t002"}, "t002": {"t001"}},
    )
    schedule["main_done_set"] = {"t001"}
    plan = _plan(events, schedule, activities={"t001": _ts(30)})

    assert [(a["action"], a["tid"]) for a in plan["actions"]] == [
        ("dispatch", "t002")
    ]


def test_reconcile_explicit_task_failure_still_redispatches():
    events = [
        *_terminal(
            "t001", status="failed", model="opus",
            reserved_ts=_ts(30), terminal_ts=_ts(24),
        ),
        _report(
            "t001", status="failed", fail_class="task",
            reason="blackbox timeout", ts=_ts(25),
        ),
    ]
    plan = _plan(events, mode="resume")

    action, = plan["actions"]
    assert action["action"] == "dispatch"
    assert action["model"] == "opus"
    assert action["mode"] == "resume"


def test_reconcile_same_fingerprint_silent_alert_not_repeated():
    events = [
        *_running("t001", model="opus", host_worker_id="worker-1", ts=_ts(40)),
        {
            "event": "silent_alerted",
            "tid": "t001",
            "attempt": 1,
            "execution_id": _eid("t001", 1),
            "fingerprint": "fp-t001",
            "ts": _ts(5),
        },
    ]
    schedule = _schedule(selected=["t002"])
    plan = _plan(events, schedule, activities={"t001": _ts(40)})

    assert plan["actions"] == []
    assert plan["silent_hold"] is True
    assert plan["occupancy"] == {"used": 1, "limit": 3}


def test_reconcile_new_fingerprint_restarts_silence_alert_cycle():
    events = [
        *_running("t001", model="opus", host_worker_id="worker-1", ts=_ts(60)),
        {
            "event": "silent_alerted",
            "tid": "t001",
            "attempt": 1,
            "execution_id": _eid("t001", 1),
            "fingerprint": "fp-old",
            "ts": _ts(20),
        },
    ]
    plan = _plan(events, activities={"t001": _ts(30)})

    action, = plan["actions"]
    assert action["action"] == "alert-silent"
    assert action["fingerprint"] == "fp-t001"
    assert action["execution_id"] == _eid("t001", 1)


def test_reconcile_first_infra_retries_same_model():
    events = [
        *_terminal(
            "t001", status="failed", model="opus",
            reserved_ts=_ts(10), terminal_ts=_ts(7),
        ),
        _report("t001", status="failed", fail_class="infra", ts=_ts(8)),
    ]
    plan = _plan(events)

    action, = plan["actions"]
    assert action["action"] == "dispatch"
    assert action["model"] == "opus"


def test_reconcile_contract_without_site_escalates():
    events = _terminal(
        "t001", model="opus", reserved_ts=_ts(10), terminal_ts=_ts(2),
    )
    plan = _plan(
        events,
        verify=("contract", "分支 tip 缺 handoff.json"),
        mode="restart",
    )

    action, = plan["actions"]
    assert action["action"] == "escalate"
    assert action["execution_id"] == _eid("t001", 1)
    assert "completed attempt" in action["reason"]
    assert "禁止自动 retry" in action["reason"]


def test_reconcile_terminal_failed_contract_resume_redispatches(ledger):
    """terminal failed + class=contract + mode=resume：同模型 resume 补交接单。"""
    events = [
        *_terminal(
            "t001", status="failed", model="opus", host_worker_id="worker-1",
            reserved_ts=_ts(30), terminal_ts=_ts(20),
        ),
        _report("t001", status="failed", fail_class="contract", ts=_ts(21)),
    ]
    plan = _plan(events, mode="resume")

    action, = plan["actions"]
    assert action["action"] == "dispatch"
    assert action["attempt"] == 2
    assert action["model"] == "opus"
    assert action["mode"] == "resume"
    assert "补交接单" in action["reason"]


def test_record_attempt_events_without_dispatch_still_require_explicit_attempt(ledger):
    for event in ("report", "escalated", "silent_alerted", "attempt_terminal"):
        with pytest.raises(SystemExit, match="不允许生命周期事件"):
            _record(event=event, tid="t001")
    assert ledger_read() == []


def test_ledger_append_concurrent_writes_no_torn_lines(ledger):
    def writer(tag):
        for index in range(50):
            ledger_append({"event": "note", "text": f"{tag}-{index}"})

    threads = [threading.Thread(target=writer, args=(tag,)) for tag in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    raw = ledger.read_text(encoding="utf-8").splitlines()
    assert len(raw) == 100
    events = ledger_read()
    assert len(events) == 100
    assert {event["text"].split("-")[0] for event in events} == {"a", "b"}


# --------------------------------------------------------------------------
# attempt 生命周期闭环与迟到事件隔离
# --------------------------------------------------------------------------


def test_attempt_terminal_requires_explicit_identity_and_status(ledger):
    reserved = reserve_attempt("t001", "agent", "opus")
    attempt, execution_id = _identity(reserved)

    with pytest.raises(ctx.TaskDataError, match="尚未 bind"):
        terminal_attempt("t001", attempt, execution_id, "completed")
    with pytest.raises(ctx.TaskDataError, match="旧或不匹配"):
        bind_attempt("t001", attempt, "wrong", "worker-1")
    with pytest.raises(ctx.TaskDataError, match="必须是 completed/failed/stopped"):
        terminal_attempt("t001", attempt, execution_id, "done")

    bind_attempt("t001", attempt, execution_id, "worker-1")
    terminal = terminal_attempt("t001", attempt, execution_id, "completed")
    assert terminal["event"] == "attempt_terminal"
    assert terminal["attempt"] == 1
    assert terminal["execution_id"] == execution_id


def test_reconcile_ready_waits_for_running_attempt_terminal():
    events = _running(
        "t001", model="opus", host_worker_id="worker-1", ts=_ts(5),
    )
    plan = _plan(events, verify=("ready", "分支 tip terminal + exact handoff"))

    assert plan["actions"][0]["action"] == "await-terminal"
    assert plan["actions"][0]["attempt"] == 1
    assert plan["actions"][0]["execution_id"] == _eid("t001", 1)
    assert plan["occupancy"] == {"used": 1, "limit": 3}


def test_reconcile_running_contract_waits_for_terminal():
    contract = _running(
        "t002", model="opus", host_worker_id="worker-2", ts=_ts(5),
    )
    contract_plan = _plan(
        contract,
        verify=("contract", "handoff 缺失"),
    )
    assert contract_plan["actions"][0]["action"] == "await-terminal"
    assert contract_plan["occupancy"]["used"] == 1


def test_reconcile_terminal_unlocks_ready_and_failed_paths():
    ready = _terminal(
        "t001", model="opus", host_worker_id="worker-1",
        reserved_ts=_ts(5), terminal_ts=_ts(2),
    )
    ready_plan = _plan(ready, verify=("ready", "分支 tip terminal + exact handoff"))
    assert ready_plan["actions"][0]["action"] == "integrate"
    assert ready_plan["actions"][0]["execution_id"] == _eid("t001", 1)

    failed = [
        *_terminal(
            "t002", status="failed", model="opus", host_worker_id="worker-2",
            reserved_ts=_ts(5), terminal_ts=_ts(2),
        ),
        _report("t002", status="failed", fail_class="infra", ts=_ts(3)),
    ]
    failed_plan = _plan(failed)
    assert failed_plan["actions"][0]["action"] == "dispatch"
    assert failed_plan["actions"][0]["attempt"] == 2
    assert "execution_id" not in failed_plan["actions"][0]


def test_late_attempt_one_events_do_not_end_or_latch_attempt_two():
    events = [
        *_terminal(
            "t001", 1, status="failed", model="opus", host_worker_id="worker-1",
            reserved_ts=_ts(30), terminal_ts=_ts(20),
        ),
        *_terminal(
            "t001", 2, model="haiku", host_worker_id="worker-2",
            reserved_ts=_ts(10), terminal_ts=_ts(8),
        ),
        _integrated("t001", 1),
        _escalated("t001", 1),
    ]
    plan = _plan(
        events,
        activities={"t001": _ts(5)},
        schedule=_schedule(selected=["t001", "t002"]),
    )

    assert plan["occupancy"]["used"] == 0
    assert [(action["action"], action["tid"]) for action in plan["actions"]] == [
        ("escalate", "t001"),
        ("dispatch", "t002"),
    ]
    # late events 是 identity 混淆高风险路径：escalate 必须绑定 attempt 2 的
    # 身份，不得引用 attempt 1 的 execution_id。
    escalate = plan["actions"][0]
    assert escalate["attempt"] == 2
    assert escalate["execution_id"] == _eid("t001", 2)


def test_dispatch_without_prior_terminal_is_illegal_overlap():
    events = [
        *_running(
            "t001", 1, model="opus", host_worker_id="worker-1", ts=_ts(20),
        ),
        *_running(
            "t001", 2, model="haiku", host_worker_id="worker-2", ts=_ts(10),
        ),
    ]
    plan = _plan(events, activities={"t001": _ts(5)})

    assert {action["action"] for action in plan["actions"]} == {"await-terminal"}
    assert {action["attempt"] for action in plan["actions"]} == {1, 2}
    assert {action["execution_id"] for action in plan["actions"]} == {
        _eid("t001", 1), _eid("t001", 2),
    }
    assert plan["occupancy"] == {"used": 2, "limit": 3}


# --------------------------------------------------------------------------
# 当前新增：原子 reserve、inline/agent、exact identity、silent exact
# --------------------------------------------------------------------------


def test_ledger_roundtrip_and_newline_sanitization(ledger):
    ledger_append({"event": "note", "text": "第一行\n第二行\r第三行"})
    assert ledger_read()[0]["text"] == "第一行 第二行 第三行"
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


def test_inline_reserve_is_running_and_blocks_second_reserve_until_terminal(ledger):
    first = reserve_attempt("t001", "inline", "opus")
    record = current_attempt_record("t001", ledger_read())
    assert record["state"] == "running"
    assert record["executor"] == "inline"
    assert record["model"] == "opus"

    with pytest.raises(ctx.TaskDataError, match="尚未 terminal"):
        reserve_attempt("t001", "inline")

    terminal_attempt("t001", *_identity(first), "completed")
    with pytest.raises(ctx.TaskDataError, match="不可被新 reserve 顶掉"):
        reserve_attempt("t001", "inline")
    escalate_attempt("t001", *_identity(first), "用户决定重试")
    second = reserve_attempt("t001", "inline")
    assert second["attempt"] == 2
    assert second["execution_id"] != first["execution_id"]


def test_agent_reserve_waits_bind_and_separates_host_worker_id(ledger):
    reserved = reserve_attempt("t001", "agent", "opus")
    attempt, execution_id = _identity(reserved)
    record = current_attempt_record("t001", ledger_read())
    assert record["state"] == "reserved"
    assert record["execution_id"] == execution_id
    assert record["host_worker_id"] == ""

    with pytest.raises(ctx.TaskDataError, match="尚未 bind"):
        terminal_attempt("t001", attempt, execution_id, "completed")

    bound = bind_attempt("t001", attempt, execution_id, "host-task-7")
    assert bound["host_worker_id"] == "host-task-7"
    record = current_attempt_record("t001", ledger_read())
    assert record["state"] == "running"
    assert record["host_worker_id"] == "host-task-7"
    assert record["execution_id"] == execution_id

    terminal_attempt("t001", attempt, execution_id, "completed")
    assert current_attempt_record("t001", ledger_read())["state"] == "terminal"


def test_bind_and_terminal_reject_wrong_or_old_identity(ledger):
    first = reserve_attempt("t001", "agent")
    attempt, execution_id = _identity(first)
    with pytest.raises(ctx.TaskDataError, match="不匹配 identity"):
        bind_attempt("t001", attempt, "wrong")
    bind_attempt("t001", attempt, execution_id, "host-1")
    terminal_attempt("t001", attempt, execution_id, "failed")
    second = reserve_attempt("t001", "inline")
    with pytest.raises(ctx.TaskDataError, match="旧或不匹配"):
        report_attempt("t001", attempt, execution_id, "failed")
    assert current_attempt_record("t001", ledger_read())["execution_id"] == second["execution_id"]


@pytest.mark.parametrize("bad_attempt", [True, False, 0, -1])
def test_attempt_identity_rejects_bool_zero_and_negative(ledger, bad_attempt):
    reserved = reserve_attempt("t001", "agent")
    with pytest.raises(ctx.TaskDataError, match="正整数"):
        bind_attempt("t001", bad_attempt, reserved["execution_id"], "host-1")


def test_completed_attempt_requires_integrate_or_escalate_before_reserve(ledger):
    reserved = reserve_attempt("t001", "inline")
    terminal_attempt("t001", *_identity(reserved), "completed")
    report_attempt("t001", *_identity(reserved), "done")
    with pytest.raises(ctx.TaskDataError, match="不可被新 reserve 顶掉"):
        reserve_attempt("t001", "inline")
    escalate_attempt("t001", *_identity(reserved), "用户批准重新执行")
    assert reserve_attempt("t001", "inline")["attempt"] == 2


def test_report_requires_terminal_and_completed_must_close_before_retry(ledger):
    reserved = reserve_attempt("t001", "inline")
    attempt, execution_id = _identity(reserved)
    with pytest.raises(ctx.TaskDataError, match="report 必须在 terminal 后"):
        report_attempt(
            "t001", attempt, execution_id, "failed",
            fail_class="infra", reason="provider unavailable",
        )
    with pytest.raises(ctx.TaskDataError, match="须先 terminal"):
        escalate_attempt("t001", attempt, execution_id, "manual")

    terminal_attempt("t001", attempt, execution_id, "completed")
    report = report_attempt(
        "t001", attempt, execution_id, "failed",
        fail_class="infra", reason="provider unavailable",
    )
    assert report["class"] == "infra"
    retried = reserve_attempt("t001", "inline")
    assert retried["attempt"] == 2


def test_silent_alert_requires_bound_agent_and_exact_fingerprint(ledger):
    inline = reserve_attempt("t001", "inline")
    with pytest.raises(ctx.TaskDataError, match="已 bind 的 agent"):
        silent_alert_attempt("t001", *_identity(inline), "fp-inline")
    terminal_attempt("t001", *_identity(inline), "stopped")

    reserved = reserve_attempt("t001", "agent")
    attempt, execution_id = _identity(reserved)
    bind_attempt("t001", attempt, execution_id, "host-1")
    ledger_append({
        "event": "observation",
        "tid": "t001",
        "attempt": attempt,
        "execution_id": execution_id,
        "fingerprint": "fp-current",
    })
    with pytest.raises(ctx.TaskDataError, match="fingerprint"):
        silent_alert_attempt("t001", attempt, execution_id, "unknown")
    alerted = silent_alert_attempt("t001", attempt, execution_id, "fp-current")
    assert alerted["fingerprint"] == "fp-current"


def test_atomic_reserve_race_allows_only_one_open_attempt(ledger):
    successes = []
    failures = []

    def worker():
        try:
            successes.append(reserve_attempt("t001", "inline"))
        except ctx.TaskDataError as error:
            failures.append(str(error))

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(successes) == 1
    assert len(failures) == 11
    assert successes[0]["attempt"] == 1
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


def test_projection_ignores_late_old_attempt_events_for_current_attempt(ledger):
    first = reserve_attempt("t001", "inline")
    terminal_attempt("t001", *_identity(first), "failed")
    second = reserve_attempt("t001", "inline")
    ledger_append({
        "event": "report",
        "tid": "t001",
        "attempt": first["attempt"],
        "execution_id": first["execution_id"],
        "status": "blocked",
        "reason": "late",
    })
    ledger_append({
        "event": "integrated",
        "tid": "t001",
        "attempt": first["attempt"],
        "execution_id": first["execution_id"],
        "merge_sha": "late",
    })
    records = project_attempts(ledger_read())
    current = current_attempt_record("t001", ledger_read())
    assert current["attempt"] == second["attempt"]
    assert current["state"] == "running"
    assert current["report"] is None
    assert records[("t001", 1, first["execution_id"])]["state"] == "integrated"


def test_integrated_batch_preflights_all_members_before_append(ledger):
    first = reserve_attempt("t001", "inline")
    terminal_attempt("t001", *_identity(first), "completed")
    second = reserve_attempt("t002", "inline")
    terminal_attempt("t002", *_identity(second), "failed")
    members = [
        {"tid": "t001", "attempt": first["attempt"], "execution_id": first["execution_id"]},
        {"tid": "t002", "attempt": second["attempt"], "execution_id": second["execution_id"]},
    ]

    with pytest.raises(ctx.TaskDataError, match="不能 integrated"):
        append_integrated_batch(members, "merge-1")
    assert not [event for event in ledger_read() if event["event"] == "integrated"]


def test_integrated_batch_is_idempotent_under_one_lock(ledger):
    members = []
    for tid in ("t001", "t002"):
        reserved = reserve_attempt(tid, "inline")
        terminal_attempt(tid, *_identity(reserved), "completed")
        members.append({
            "tid": tid,
            "attempt": reserved["attempt"],
            "execution_id": reserved["execution_id"],
        })

    appended = append_integrated_batch(members, "merge-1")
    repeated = append_integrated_batch(members, "merge-1")
    assert len(appended) == 2
    assert repeated == []
    assert len([event for event in ledger_read() if event["event"] == "integrated"]) == 2


def test_reconcile_silent_alert_contains_exact_identity_and_never_redispatches(ledger):
    reserved = reserve_attempt("t001", "agent", "opus")
    attempt, execution_id = _identity(reserved)
    bind_attempt("t001", attempt, execution_id, "host-9")
    old = (NOW - timedelta(minutes=45)).isoformat(timespec="seconds")
    events = ledger_read()

    def observer(_events, _attempt, _execution_id):
        return {
            "event": "observation",
            "tid": "t001",
            "attempt": attempt,
            "execution_id": execution_id,
            "fingerprint": "fp-1",
            "ts": old,
            "head": "abc",
            "worktree": "../repo_t001",
            "dirty": "clean",
        }

    plan = compute_reconcile_plan(
        events,
        _schedule(selected=("t002",)),
        limit=3,
        scope=None,
        silent_minutes=20,
        max_auto_retries=1,
        now=NOW,
        observer=observer,
        verifier=lambda *_: ("incomplete", "active"),
    )
    assert [(action["action"], action["tid"]) for action in plan["actions"]] == [
        ("alert-silent", "t001")
    ]
    action = plan["actions"][0]
    assert action["execution_id"] == execution_id
    assert action["host_worker_id"] == "host-9"
    assert action["fingerprint"] == "fp-1"
    assert plan["silent_hold"] is True
    assert plan["occupancy"] == {"used": 1, "limit": 3}


def test_reconcile_same_silent_fingerprint_dedupes_without_dispatch(ledger):
    reserved = reserve_attempt("t001", "agent")
    attempt, execution_id = _identity(reserved)
    bind_attempt("t001", attempt, execution_id, "host-1")
    old = (NOW - timedelta(minutes=45)).isoformat(timespec="seconds")
    ledger_append({
        "event": "silent_alerted",
        "tid": "t001",
        "attempt": attempt,
        "execution_id": execution_id,
        "fingerprint": "fp-1",
    })
    plan = compute_reconcile_plan(
        ledger_read(),
        _schedule(selected=("t002",)),
        limit=3,
        scope=None,
        silent_minutes=20,
        max_auto_retries=1,
        now=NOW,
        observer=lambda *_: {
            "event": "observation",
            "tid": "t001",
            "attempt": attempt,
            "execution_id": execution_id,
            "fingerprint": "fp-1",
            "ts": old,
        },
        verifier=lambda *_: ("incomplete", "active"),
    )
    assert plan["actions"] == []
    assert plan["silent_hold"] is True


def test_reconcile_unreserved_dispatch_is_suggestion_without_execution_id(ledger):
    plan = compute_reconcile_plan(
        ledger_read(),
        _schedule(selected=("t001",)),
        limit=1,
        scope=None,
        silent_minutes=20,
        max_auto_retries=1,
        now=NOW,
        verifier=lambda *_: ("incomplete", ""),
    )
    action, = plan["actions"]
    assert action["action"] == "dispatch"
    assert action["attempt"] == 1
    assert "execution_id" not in action


def test_ps_uses_attempt_projection_and_exposes_executor_and_host(ledger):
    reserved = reserve_attempt("t001", "agent", "opus")
    bind_attempt("t001", *_identity(reserved), "host-1")
    rows = compute_ps_rows(
        ledger_read(),
        {},
        {},
        silent_minutes=20,
        now=NOW,
        observer=lambda *_: None,
        verifier=lambda *_: ("incomplete", "active"),
    )
    assert rows[0]["execution_id"] == reserved["execution_id"]
    assert rows[0]["executor"] == "agent"
    assert rows[0]["host_worker_id"] == "host-1"
    assert rows[0]["state"] == "running(未观察)"


def test_ledger_record_rejects_lifecycle_event_even_when_called_directly(ledger):
    with pytest.raises(SystemExit, match="不允许生命周期事件"):
        control.cmd_ledger_record(argparse.Namespace(
            event="attempt_reserved", tid="t001", reason=None,
        ))
    assert ledger_read() == []
