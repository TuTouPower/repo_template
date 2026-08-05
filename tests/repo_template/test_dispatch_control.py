"""调度控制面：账本读写、attempt 编号、record 校验、ps 状态判定、reconcile 行动计划。"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "repo_template"
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task import context as ctx
from repo_task import control
from repo_task.ledger import ledger_append, ledger_next_attempt, ledger_read
from repo_task.monitoring import (
    breaker_states,
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
    return runtime / "dispatch_ledger.jsonl"


def _record(**kwargs):
    defaults = dict(
        event=None, tid=None, attempt=None, model=None, worker_id=None,
        status=None, sha=None, fail_class=None, state=None, fingerprint=None, reason=None,
    )
    defaults.update(kwargs)
    control.cmd_ledger_record(argparse.Namespace(**defaults))


# --------------------------------------------------------------------------
# 账本基础设施
# --------------------------------------------------------------------------


def test_ledger_append_read_roundtrip(ledger):
    first = ledger_append({"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus"})
    ledger_append({"event": "note", "text": "中文备注"})

    events = ledger_read()

    assert [e["event"] for e in events] == ["dispatch", "note"]
    assert first["tid"] == "t001" and first["attempt"] == 1
    assert "ts" in first
    assert events[1]["text"] == "中文备注"


def test_ledger_append_sanitizes_newlines(ledger):
    ledger_append({"event": "failed", "tid": "t001", "attempt": 1,
                   "class": "infra", "reason": "第一行\n第二行\r第三行"})

    raw_lines = ledger.read_text(encoding="utf-8").splitlines()

    assert len(raw_lines) == 1
    assert ledger_read()[0]["reason"] == "第一行 第二行 第三行"


def test_ledger_read_missing_file_returns_empty(ledger):
    assert ledger_read() == []


def test_ledger_read_corrupted_line_self_heals(ledger, capsys):
    """截断/损坏行：stderr 警告（含行号）并跳过，返回有效事件，不抛错。"""
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        '{"event": "note", "text": "好行1"}\n{"event": "disp\ntruncated\n',
        encoding="utf-8",
    )

    events = ledger_read()

    assert [e["text"] for e in events] == ["好行1"]
    err = capsys.readouterr().err
    assert "第 2 行" in err and "已跳过" in err


def test_ledger_next_attempt_increments_per_tid():
    events = [
        {"tid": "t001", "attempt": 1},
        {"tid": "t002", "attempt": 1},
        {"tid": "t001", "attempt": 3},
        {"tid": "t001"},  # 无 attempt 字段不计
    ]

    assert ledger_next_attempt("t001", events) == 4
    assert ledger_next_attempt("t002", events) == 2
    assert ledger_next_attempt("t003", events) == 1


# --------------------------------------------------------------------------
# ledger record / tail 命令
# --------------------------------------------------------------------------


def test_record_dispatch_auto_assigns_attempt_and_worker_id(ledger, capsys):
    _record(event="dispatch", tid="t001", model="opus", worker_id="task-123")
    _record(
        event="worker_terminal", tid="t001", attempt=1,
        worker_id="task-123", status="completed",
    )
    _record(event="dispatch", tid="t001", model="haiku", worker_id="task-456")

    events = [event for event in ledger_read() if event["event"] == "dispatch"]
    assert [event["attempt"] for event in events] == [1, 2]
    assert [event["worker_id"] for event in events] == ["task-123", "task-456"]
    out = capsys.readouterr().out
    assert "recorded: dispatch t001#1 model=opus worker_id=task-123" in out
    assert "recorded: dispatch t001#2 model=haiku worker_id=task-456" in out


def test_parallel_dispatch_and_escalate_require_prior_terminal(ledger):
    _record(event="dispatch", tid="t001", model="opus", worker_id="worker-1")

    with pytest.raises(SystemExit, match="禁止派发新的并行 attempt"):
        _record(event="dispatch", tid="t001", model="haiku", worker_id="worker-2")
    with pytest.raises(SystemExit, match="禁止结束仍运行的并行 worker"):
        _record(event="escalated", tid="t001", attempt=1, reason="still running")

    _record(event="worker_terminal", tid="t001", attempt=1,
            worker_id="worker-1", status="stopped")
    _record(event="dispatch", tid="t001", model="haiku", worker_id="worker-2")
    assert [event["attempt"] for event in ledger_read() if event["event"] == "dispatch"] == [1, 2]


def test_record_validation(ledger):
    with pytest.raises(SystemExit, match="必须给 --status"):
        _record(event="report", tid="t001", attempt=1)
    with pytest.raises(SystemExit, match="必须给 --class"):
        _record(event="failed", tid="t001", attempt=1)
    for event in ("dispatch", "report", "failed", "escalated"):
        with pytest.raises(SystemExit, match="必须给 --tid"):
            _record(event=event, status="done" if event == "report" else None,
                    fail_class="infra" if event == "failed" else None)
    assert ledger_read() == []


def test_record_note_uses_text_field(ledger):
    _record(event="note", reason="自由备注")

    assert ledger_read()[0]["text"] == "自由备注"


@pytest.mark.parametrize("event", ("report", "failed", "escalated", "silent_alerted"))
def test_record_attempt_events_require_explicit_attempt_even_after_dispatch(ledger, event):
    _record(event="dispatch", tid="t001", model="opus")
    kwargs = dict(event=event, tid="t001")
    if event == "report":
        kwargs["status"] = "done"
    elif event == "failed":
        kwargs["fail_class"] = "infra"
    elif event == "silent_alerted":
        kwargs["fingerprint"] = "abc123"

    with pytest.raises(SystemExit, match=rf"--event {event} 必须显式给 --attempt"):
        _record(**kwargs)

    assert [item["event"] for item in ledger_read()] == ["dispatch"]


def test_ledger_tail_filters_tid_and_reverses(ledger, capsys):
    _record(event="dispatch", tid="t001", attempt=1, model="opus")
    _record(event="failed", tid="t001", attempt=1, fail_class="infra", reason="boom")
    _record(event="dispatch", tid="t002", attempt=1, model="haiku")
    capsys.readouterr()  # 排空 record 的打印

    control.cmd_ledger_tail(argparse.Namespace(tid="t001", n=20))

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert "failed t001#1" in lines[0] and "class=infra" in lines[0]
    assert "dispatch t001#1" in lines[1] and "model=opus" in lines[1]
    assert all("t002" not in line for line in lines)


# --------------------------------------------------------------------------
# silent 判定（纯函数）
# --------------------------------------------------------------------------


def test_is_silent_threshold():
    assert is_silent(_ts(21), 20, NOW)
    assert not is_silent(_ts(19), 20, NOW)
    assert not is_silent("无法解析", 20, NOW)


# --------------------------------------------------------------------------
# ps 活表状态判定（合成账本 + 注入 observer）
# --------------------------------------------------------------------------


def _observation(activities, events, attempt):
    tid = next((event.get("tid") for event in events if event.get("tid")), None)
    observed_at = activities.get(tid)
    if observed_at is None:
        return None
    return {
        "event": "observation", "tid": tid, "attempt": attempt,
        "ts": observed_at, "fingerprint": f"fp-{tid}", "dirty": "clean",
    }


def _ps_rows(events, effective=None, main_statuses=None, activities=None,
             verify=("incomplete", "refs 未完成")):
    activities = activities or {}
    return compute_ps_rows(
        events,
        effective or {},
        main_statuses or {},
        silent_minutes=20,
        now=NOW,
        observer=lambda tev, attempt: _observation(activities, tev, attempt),
        verifier=lambda tid: verify,
    )


def test_ps_pending_backlog_never_dispatched():
    rows = _ps_rows(
        [{"event": "note", "tid": "t009", "ts": _ts(1)}],
        effective={"t001": {"status": "backlog"}},
        main_statuses={"t001": "backlog"},
    )

    by_tid = {row["tid"]: row for row in rows}
    assert by_tid["t001"]["state"] == "pending"


def test_ps_progressing_and_silent_from_observation():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "worker_id": "w1", "ts": _ts(5)},
        {"event": "dispatch", "tid": "t002", "attempt": 1, "model": "opus", "ts": _ts(30)},
    ]
    rows = _ps_rows(
        events,
        activities={"t001": _ts(5), "t002": _ts(30)},
    )

    by_tid = {row["tid"]: row for row in rows}
    assert by_tid["t001"]["state"] == "progressing"
    assert by_tid["t001"]["model"] == "opus"
    assert by_tid["t001"]["worker_id"] == "w1"
    assert by_tid["t002"]["state"] == "silent?"


def test_ps_dispatched_without_observation():
    rows = _ps_rows(
        [{"event": "dispatch", "tid": "t001", "attempt": 1, "ts": _ts(5)}],
        activities={"t001": None},
    )

    assert rows[0]["state"] == "dispatched(未观察)"


def test_ps_failed_blocked_reported_terminal_states():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "ts": _ts(10)},
        {"event": "failed", "tid": "t001", "attempt": 1, "class": "infra",
         "reason": "provider 不兼容", "ts": _ts(9)},
        {"event": "dispatch", "tid": "t002", "attempt": 1, "ts": _ts(10)},
        {"event": "report", "tid": "t002", "attempt": 1, "status": "blocked",
         "reason": "review 满轮", "ts": _ts(8)},
        {"event": "dispatch", "tid": "t003", "attempt": 1, "ts": _ts(10)},
        {"event": "report", "tid": "t003", "attempt": 1, "status": "done",
         "sha": "abc123", "ts": _ts(8)},
        {"event": "dispatch", "tid": "t004", "attempt": 1, "ts": _ts(10)},
        {"event": "integrated", "tid": "t004", "merge_sha": "def456", "ts": _ts(7)},
    ]
    rows = _ps_rows(events, main_statuses={"t004": "done"})

    by_tid = {row["tid"]: row for row in rows}
    assert by_tid["t001"]["state"] == "failed:infra"
    assert by_tid["t001"]["note"] == "provider 不兼容"
    assert by_tid["t002"]["state"] == "blocked"
    assert by_tid["t003"]["state"] == "reported(未验证)"
    assert by_tid["t003"]["note"] == "abc123"
    assert by_tid["t004"]["state"] == "done"


# --------------------------------------------------------------------------
# reconcile 行动计划（合成账本 + 合成调度图 + 注入 observer/verifier）
# --------------------------------------------------------------------------


def _schedule(selected=(), conflicts=None):
    return {
        "selected": list(selected),
        "conflicts": conflicts or {},
        "main_done_set": set(),
        "dropped_set": set(),
    }


def _plan(events, schedule=None, activities=None, verify=("incomplete", "refs 未完成"),
          mode="restart", **overrides):
    activities = activities or {}
    options = dict(
        limit=3, scope=None, ladder=None, silent_minutes=20,
        max_auto_retries=1, now=NOW,
        observer=lambda tev, attempt: _observation(activities, tev, attempt),
        verifier=lambda tid: verify,
        mode_probe=lambda tid, tev: mode,
    )
    options.update(overrides)
    return compute_reconcile_plan(events, schedule or _schedule(), **options)


def test_reconcile_empty_plan_when_nothing_in_flight():
    plan = _plan([])

    assert plan["actions"] == []
    assert plan["occupancy"] == {"used": 0, "limit": 3}


def test_reconcile_progressing_occupies_slot_and_fills_from_selected():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(5)},
    ]
    schedule = _schedule(selected=["t002", "t003"])
    plan = _plan(
        events, schedule,
        activities={"t001": _ts(5)},
        ladder=["opus", "haiku"],
    )

    assert plan["occupancy"]["used"] == 1
    assert [(a["action"], a["tid"]) for a in plan["actions"]] == [
        ("dispatch", "t002"), ("dispatch", "t003"),
    ]
    assert all(a["model"] == "opus" for a in plan["actions"])
    assert all(a["attempt"] == 1 for a in plan["actions"])


def test_reconcile_dispatch_skips_conflicts_with_in_flight():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "ts": _ts(5)},
    ]
    schedule = _schedule(
        selected=["t002", "t003"],
        conflicts={"t001": {"t002"}, "t002": {"t001"}, "t003": set()},
    )
    plan = _plan(events, schedule, activities={"t001": _ts(5)})

    assert [(a["action"], a["tid"]) for a in plan["actions"]] == [("dispatch", "t003")]


def test_reconcile_report_done_verified_is_integrate():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "ts": _ts(10)},
        {"event": "report", "tid": "t001", "attempt": 1, "status": "done", "ts": _ts(2)},
    ]
    plan = _plan(events, verify=("ready", "分支 tip done + handoff 齐备"))

    assert [(a["action"], a["tid"], a["attempt"]) for a in plan["actions"]] == [
        ("integrate", "t001", 1),
    ]
    assert plan["occupancy"]["used"] == 1


def test_reconcile_report_done_unverified_is_contract_retry():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(10)},
        {"event": "report", "tid": "t001", "attempt": 1, "status": "done", "ts": _ts(2)},
    ]
    plan = _plan(
        events, verify=("contract", "分支 tip 缺 handoff.json"),
        ladder=["opus", "haiku"], mode="resume",
    )

    action, = plan["actions"]
    assert action["action"] == "redispatch"
    assert action["attempt"] == 2
    # contract 不走阶梯降档：同模型 resume，补交接单
    assert action["model"] == "opus"
    assert action["mode"] == "resume"
    assert "contract" in action["reason"] and "补交接单" in action["reason"]


def test_reconcile_report_blocked_escalates():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "ts": _ts(10)},
        {"event": "report", "tid": "t001", "attempt": 1, "status": "blocked", "ts": _ts(2)},
    ]
    plan = _plan(events)

    action, = plan["actions"]
    assert action["action"] == "escalate"
    assert "blocked" in action["reason"]


def test_reconcile_silent_alerts_without_redispatch():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus",
         "worker_id": "worker-7", "ts": _ts(30)},
    ]
    plan = _plan(
        events,
        activities={"t001": _ts(30)},
        ladder=["opus", "haiku"],
    )

    action, = plan["actions"]
    assert action["action"] == "alert-silent"
    assert action["attempt"] == 1
    assert action["model"] == "opus"
    assert action["worker_id"] == "worker-7"
    assert action["fingerprint"] == "fp-t001"
    assert "30 分钟" in action["reason"]
    assert plan["occupancy"]["used"] == 1
    assert plan["silent_hold"] is True


def test_reconcile_failed_without_ladder_keeps_last_model():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(10)},
        {"event": "failed", "tid": "t001", "attempt": 1, "class": "infra",
         "reason": "API 错误", "ts": _ts(8)},
    ]
    plan = _plan(events)

    action, = plan["actions"]
    assert action["action"] == "redispatch"
    assert action["attempt"] == 2
    assert action["model"] == "opus"
    assert "infra" in action["reason"]


def test_reconcile_escalates_when_retry_budget_exhausted():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "ts": _ts(60)},
        {"event": "failed", "tid": "t001", "attempt": 1, "class": "infra", "ts": _ts(50)},
        {"event": "dispatch", "tid": "t001", "attempt": 2, "ts": _ts(40)},
        {"event": "failed", "tid": "t001", "attempt": 2, "class": "infra", "ts": _ts(30)},
    ]
    plan = _plan(events, max_auto_retries=1)

    action, = plan["actions"]
    assert action["action"] == "escalate"
    assert action["attempt"] == 2
    assert "额度" in action["reason"]


def test_reconcile_scope_limits_actions_but_not_occupancy():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "ts": _ts(5)},
        {"event": "dispatch", "tid": "t002", "attempt": 1, "ts": _ts(30)},
    ]
    schedule = _schedule(selected=["t003"])
    plan = _plan(
        events, schedule,
        activities={"t001": _ts(5), "t002": _ts(30)},
        scope={"t002"},
    )

    # t001 progressing 占槽但不在授权范围；t002 silent 只告警并继续占槽。
    assert [(a["action"], a["tid"]) for a in plan["actions"]] == [("alert-silent", "t002")]
    assert plan["occupancy"] == {"used": 2, "limit": 3}
    assert plan["silent_hold"] is True


def test_reconcile_integrated_and_escalated_end_flight():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "ts": _ts(10)},
        {"event": "integrated", "tid": "t001", "merge_sha": "abc", "ts": _ts(8)},
        {"event": "dispatch", "tid": "t002", "attempt": 1, "ts": _ts(10)},
        {"event": "escalated", "tid": "t002", "attempt": 1, "reason": "用户处理", "ts": _ts(8)},
    ]
    plan = _plan(events)

    assert plan["actions"] == []
    assert plan["occupancy"]["used"] == 0


# --------------------------------------------------------------------------
# refs 派生 READY_MERGE（report 从必要条件降为加速线索）
# --------------------------------------------------------------------------


def test_reconcile_refs_derived_integrate_without_report():
    """无 report 事件但 refs done + handoff 齐备 → integrate（refs 派生）。"""
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(10)},
    ]
    plan = _plan(
        events,
        activities={"t001": _ts(5)},
        verify=("ready", "分支 tip done + handoff 齐备"),
    )

    action, = plan["actions"]
    assert action["action"] == "integrate"
    assert action["tid"] == "t001" and action["attempt"] == 1
    assert "refs 派生" in action["reason"]
    assert plan["occupancy"]["used"] == 1


def test_reconcile_report_done_reason_differs_from_refs_derived():
    """有 report 时 integrate 的 reason 标注 report + refs 双来源。"""
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "ts": _ts(10)},
        {"event": "report", "tid": "t001", "attempt": 1, "status": "done", "ts": _ts(2)},
    ]
    plan = _plan(events, verify=("ready", "分支 tip done + handoff 齐备"))

    action, = plan["actions"]
    assert action["action"] == "integrate"
    assert "report done + refs 验证通过" in action["reason"]


def test_reconcile_refs_done_but_handoff_missing_is_contract_retry():
    """refs 终态但 handoff.json 缺失 → contract 重试（与 report 验证不过同处理）。"""
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(10)},
    ]
    plan = _plan(
        events,
        verify=("contract", "分支 tip 缺 docs/archive/tasks/t001_x/handoff.json"),
        ladder=["opus", "haiku"],
        mode="resume",  # 有现场：同模型 resume
    )

    action, = plan["actions"]
    assert action["action"] == "redispatch"
    assert action["attempt"] == 2 and action["model"] == "opus"  # contract 同模型 resume
    assert action["mode"] == "resume"
    assert "contract" in action["reason"] and "补交接单" in action["reason"]


def test_reconcile_report_blocked_wins_over_incomplete_refs():
    """refs 非终态时 report blocked 仍走 escalate，不被 refs 逻辑干扰（回归）。"""
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "ts": _ts(10)},
        {"event": "report", "tid": "t001", "attempt": 1, "status": "blocked", "ts": _ts(2)},
    ]
    plan = _plan(
        events,
        verify=("incomplete", "分支 tip status='active' 非终态"),
    )

    action, = plan["actions"]
    assert action["action"] == "escalate"
    assert "blocked" in action["reason"]


def test_ps_refs_ready_shows_done_pending_merge():
    rows = _ps_rows(
        [{"event": "dispatch", "tid": "t001", "attempt": 1, "ts": _ts(10)}],
        verify=("ready", "分支 tip done + handoff 齐备"),
    )

    assert rows[0]["state"] == "done待合并"
    assert rows[0]["note"] == "分支 tip done + handoff 齐备"


# --------------------------------------------------------------------------
# 模型熔断器（session 级）
# --------------------------------------------------------------------------


def test_record_breaker_requires_model(ledger):
    with pytest.raises(SystemExit, match="必须给 --model"):
        _record(event="breaker")
    assert ledger_read() == []


def test_record_breaker_defaults_open_and_accepts_closed(ledger):
    _record(event="breaker", model="opus", reason="provider 不兼容")
    _record(event="breaker", model="opus", state="closed", reason="恢复")

    events = ledger_read()
    assert events[0]["state"] == "open"
    assert events[1]["state"] == "closed"


def test_breaker_states_latest_event_wins():
    events = [
        {"event": "breaker", "model": "opus", "state": "open"},
        {"event": "breaker", "model": "haiku"},  # state 省略按 open
        {"event": "breaker", "model": "opus", "state": "closed"},
    ]

    assert breaker_states(events) == {"opus": "closed", "haiku": "open"}


def test_reconcile_dispatch_skips_broken_model_with_note():
    schedule = _schedule(selected=["t001"])
    events = [{"event": "breaker", "model": "opus", "state": "open"}]
    plan = _plan(events, schedule, ladder=["opus", "haiku"])

    action, = plan["actions"]
    assert action["action"] == "dispatch"
    assert action["model"] == "haiku"
    assert "opus 熔断" in action["reason"] and "降 haiku" in action["reason"]


def test_reconcile_redispatch_uses_next_unbroken_rung():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(10)},
        {"event": "failed", "tid": "t001", "attempt": 1, "class": "infra", "ts": _ts(8)},
        {"event": "breaker", "model": "haiku", "state": "open"},
    ]
    plan = _plan(events, ladder=["opus", "haiku", "sonnet"])

    action, = plan["actions"]
    assert action["action"] == "redispatch"
    assert action["attempt"] == 2
    assert action["model"] == "sonnet"  # 第 1 档 haiku 熔断，降到 sonnet
    assert "haiku 熔断" in action["reason"]


def test_reconcile_breaker_closed_restores_first_rung():
    schedule = _schedule(selected=["t001"])
    events = [
        {"event": "breaker", "model": "opus", "state": "open"},
        {"event": "breaker", "model": "opus", "state": "closed"},
    ]
    plan = _plan(events, schedule, ladder=["opus", "haiku"])

    action, = plan["actions"]
    assert action["action"] == "dispatch"
    assert action["model"] == "opus"
    assert "熔断" not in action["reason"]


def test_reconcile_all_models_broken_dispatch_escalates():
    schedule = _schedule(selected=["t001"])
    events = [
        {"event": "breaker", "model": "opus", "state": "open"},
        {"event": "breaker", "model": "haiku", "state": "open"},
    ]
    plan = _plan(events, schedule, ladder=["opus", "haiku"])

    action, = plan["actions"]
    assert action["action"] == "escalate"
    assert action["reason"] == "模型阶梯全部熔断"


def test_reconcile_all_models_broken_redispatch_escalates():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(10)},
        {"event": "failed", "tid": "t001", "attempt": 1, "class": "infra", "ts": _ts(8)},
        {"event": "breaker", "model": "opus", "state": "open"},
        {"event": "breaker", "model": "haiku", "state": "open"},
    ]
    plan = _plan(events, ladder=["opus", "haiku"])

    action, = plan["actions"]
    assert action["action"] == "escalate"
    assert "模型阶梯全部熔断" in action["reason"]


def test_reconcile_no_ladder_last_model_broken_escalates():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(10)},
        {"event": "failed", "tid": "t001", "attempt": 1, "class": "infra", "ts": _ts(8)},
        {"event": "breaker", "model": "opus", "state": "open"},
    ]
    plan = _plan(events)

    action, = plan["actions"]
    assert action["action"] == "escalate"
    assert "模型阶梯全部熔断" in action["reason"]


# --------------------------------------------------------------------------
# 审阅修复回归：处置事件、effective blocked、闩锁、占槽、阶梯钳制、mode、去重
# --------------------------------------------------------------------------


def test_reconcile_note_does_not_mask_failed():
    """failed 之后的 note 不掩盖失败：处置事件取该 attempt 最后一条 failed/report。"""
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(10)},
        {"event": "failed", "tid": "t001", "attempt": 1, "class": "infra",
         "reason": "provider 不兼容", "ts": _ts(8)},
        {"event": "note", "tid": "t001", "text": "随便一句", "ts": _ts(7)},
    ]
    plan = _plan(events, ladder=["opus", "haiku"], mode="restart")

    action, = plan["actions"]
    assert action["action"] == "redispatch"
    assert action["model"] == "haiku"
    assert "infra" in action["reason"]


def test_reconcile_report_status_failed_is_failure_path():
    """report status=failed 等价失败路径；class 取自 report，缺省 task。"""
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(10)},
        {"event": "report", "tid": "t001", "attempt": 1, "status": "failed",
         "reason": "黑盒满轮", "ts": _ts(8)},
    ]
    plan = _plan(events, ladder=["opus", "haiku"], mode="resume")

    action, = plan["actions"]
    assert action["action"] == "redispatch"
    assert action["model"] == "haiku"
    assert action["mode"] == "resume"
    assert "task 失败" in action["reason"] and "黑盒满轮" in action["reason"]


def test_ps_old_attempt_blocked_report_does_not_pollute_new_attempt():
    """reports 按当前 attempt 过滤：#1 的 blocked 报告不影响 #2 的显示。"""
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "ts": _ts(30)},
        {"event": "report", "tid": "t001", "attempt": 1, "status": "blocked", "ts": _ts(25)},
        {"event": "dispatch", "tid": "t001", "attempt": 2, "model": "haiku", "ts": _ts(5)},
    ]
    rows = _ps_rows(events, activities={"t001": _ts(5)})

    assert rows[0]["attempt"] == 2
    assert rows[0]["state"] == "progressing"


def test_reconcile_effective_blocked_escalates_without_retry_budget():
    """worker 已在分支 block 但 report 未落账时直接 escalate，不走静默判断。"""
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(60)},
    ]
    schedule = _schedule()
    schedule["tasks"] = {"t001": {"status": "blocked"}}
    plan = _plan(
        events, schedule,
        activities={"t001": _ts(60)},
    )

    action, = plan["actions"]
    assert action["action"] == "escalate"
    assert "分支已 blocked" in action["reason"]
    assert plan["occupancy"]["used"] == 0  # escalate 释放槽


def test_reconcile_escalate_latch_blocks_auto_redispatch():
    """闩锁：escalated 之后无新 dispatch 的 tid 不被补位重派。"""
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "ts": _ts(30)},
        {"event": "escalated", "tid": "t001", "attempt": 1, "reason": "用户处理", "ts": _ts(20)},
    ]
    schedule = _schedule(selected=["t001", "t002"])
    plan = _plan(events, schedule)

    assert [(a["action"], a["tid"]) for a in plan["actions"]] == [("dispatch", "t002")]


def test_reconcile_escalate_latch_released_by_new_dispatch():
    """闩锁解除：coordinator 手动重派并落账新 dispatch 后，该 tid 回到在飞管理。"""
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "ts": _ts(30)},
        {"event": "escalated", "tid": "t001", "attempt": 1, "reason": "用户处理", "ts": _ts(20)},
        {"event": "dispatch", "tid": "t001", "attempt": 2, "model": "haiku", "ts": _ts(5)},
    ]
    schedule = _schedule(selected=["t001", "t002"])
    plan = _plan(events, schedule, activities={"t001": _ts(5)})

    assert [(a["action"], a["tid"]) for a in plan["actions"]] == [("dispatch", "t002")]
    assert plan["occupancy"]["used"] == 1  # t001#2 progressing 占槽


def test_reconcile_silent_attempts_hold_all_slots_without_dispatch():
    """三个 silent attempt 各告警并继续占槽，不补位新 dispatch。"""
    events = [
        {"event": "dispatch", "tid": tid, "attempt": 1, "model": "opus", "ts": _ts(30)}
        for tid in ("t001", "t002", "t003")
    ]
    schedule = _schedule(selected=["t004", "t005"])
    plan = _plan(
        events, schedule,
        activities={tid: _ts(30) for tid in ("t001", "t002", "t003")},
        limit=3,
    )

    assert [(a["action"], a["tid"]) for a in plan["actions"]] == [
        ("alert-silent", "t001"), ("alert-silent", "t002"), ("alert-silent", "t003"),
    ]
    assert plan["occupancy"] == {"used": 3, "limit": 3}
    assert plan["silent_hold"] is True


def test_reconcile_ladder_clamp_same_model_escalates():
    """2 档阶梯 attempt 3 钳回父 attempt 同模型 → escalate（无未尝试模型），不同参重试。"""
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(60)},
        {"event": "failed", "tid": "t001", "attempt": 1, "class": "infra", "ts": _ts(50)},
        {"event": "dispatch", "tid": "t001", "attempt": 2, "model": "haiku", "ts": _ts(40)},
        {"event": "failed", "tid": "t001", "attempt": 2, "class": "infra", "ts": _ts(30)},
    ]
    plan = _plan(events, ladder=["opus", "haiku"], max_auto_retries=3)

    action, = plan["actions"]
    assert action["action"] == "escalate"
    assert "阶梯内已无未尝试模型" in action["reason"]


def test_reconcile_redispatch_mode_restart_vs_resume():
    """mode：无产出 restart（可直接 start）；有产出 resume（续跑）。"""
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(10)},
        {"event": "failed", "tid": "t001", "attempt": 1, "class": "infra", "ts": _ts(8)},
    ]
    restart = _plan(events, ladder=["opus", "haiku"], mode="restart")
    resume = _plan(events, ladder=["opus", "haiku"], mode="resume")

    assert restart["actions"][0]["mode"] == "restart"
    assert resume["actions"][0]["mode"] == "resume"


def test_reconcile_duplicate_dispatch_deduped():
    """同 (tid, attempt) 两条 dispatch → 只在飞一次，动作与占槽不双算。"""
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(10)},
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(9)},
    ]
    plan = _plan(events, activities={"t001": _ts(5)})

    assert plan["actions"] == []
    assert plan["occupancy"]["used"] == 1


def test_is_silent_naive_timestamp_tolerated():
    """naive（无 offset）时间戳按本地时区解释，不与 aware 比较 TypeError。"""
    naive_old = (NOW - timedelta(minutes=30)).astimezone().replace(tzinfo=None)
    naive_new = (NOW - timedelta(minutes=10)).astimezone().replace(tzinfo=None)

    assert is_silent(naive_old.isoformat(timespec="seconds"), 20, NOW)
    assert not is_silent(naive_new.isoformat(timespec="seconds"), 20, NOW)


# --------------------------------------------------------------------------
# 第三轮审阅修复回归：全局占槽、终态不阻塞、钳制范围、contract 现场、
# 多分支、record attempt 守卫、账本追加锁
# --------------------------------------------------------------------------


def test_reconcile_occupancy_counts_out_of_scope_in_flight():
    """scope 外 silent attempt 仍全局占槽并阻止 scope 内补位。"""
    events = [
        {"event": "dispatch", "tid": tid, "attempt": 1, "model": "opus", "ts": _ts(30)}
        for tid in ("t001", "t002", "t003")
    ]
    schedule = _schedule(selected=["t004"])
    plan = _plan(
        events, schedule,
        activities={tid: _ts(30) for tid in ("t001", "t002", "t003")},
        scope={"t004"}, limit=3,
    )

    assert plan["actions"] == []
    assert plan["occupancy"] == {"used": 3, "limit": 3}
    assert plan["silent_hold"] is True


def test_reconcile_terminal_on_main_does_not_block_conflict_peer():
    """主干已 done（手工合入、账本缺 integrated）的 tid 不阻塞冲突对端补位。"""
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(30)},
    ]
    schedule = _schedule(
        selected=["t002"],
        conflicts={"t001": {"t002"}, "t002": {"t001"}},
    )
    schedule["main_done_set"] = {"t001"}
    plan = _plan(events, schedule, activities={"t001": _ts(30)})

    assert [(a["action"], a["tid"]) for a in plan["actions"]] == [("dispatch", "t002")]


def test_reconcile_explicit_resource_failure_still_redispatches():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(30)},
        {"event": "failed", "tid": "t001", "attempt": 1, "class": "resource",
         "reason": "context exhausted", "ts": _ts(25)},
    ]
    plan = _plan(events, ladder=["opus"], mode="resume")

    action, = plan["actions"]
    assert action["action"] == "redispatch"
    assert action["model"] == "opus"
    assert action["mode"] == "resume"


def test_reconcile_same_fingerprint_silent_alert_not_repeated():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(40)},
        {"event": "silent_alerted", "tid": "t001", "attempt": 1,
         "fingerprint": "fp-t001", "ts": _ts(5)},
    ]
    schedule = _schedule(selected=["t002"])
    plan = _plan(events, schedule, activities={"t001": _ts(40)})

    assert plan["actions"] == []
    assert plan["silent_hold"] is True
    assert plan["occupancy"] == {"used": 1, "limit": 3}


def test_reconcile_new_fingerprint_restarts_silence_alert_cycle():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(60)},
        {"event": "silent_alerted", "tid": "t001", "attempt": 1,
         "fingerprint": "fp-old", "ts": _ts(20)},
    ]
    plan = _plan(events, activities={"t001": _ts(30)})

    action, = plan["actions"]
    assert action["action"] == "alert-silent"
    assert action["fingerprint"] == "fp-t001"


def test_reconcile_single_rung_infra_escalates():
    """单档阶梯 + infra：钳回同模型 → escalate（无未尝试模型），不同参重试。"""
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(10)},
        {"event": "failed", "tid": "t001", "attempt": 1, "class": "infra", "ts": _ts(8)},
    ]
    plan = _plan(events, ladder=["opus"])

    action, = plan["actions"]
    assert action["action"] == "escalate"
    assert "阶梯内已无未尝试模型" in action["reason"]


def test_reconcile_contract_without_site_escalates():
    """contract 且无现场（无 worktree、分支无未合并 commit）→ escalate。"""
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(10)},
    ]
    plan = _plan(
        events,
        verify=("contract", "分支 tip 缺 handoff.json"),
        ladder=["opus", "haiku"],
        mode="restart",  # mode_probe 判定无现场
    )

    action, = plan["actions"]
    assert action["action"] == "escalate"
    assert "无现场可续" in action["reason"]


def test_record_attempt_events_without_dispatch_still_require_explicit_attempt(ledger):
    """report/failed/escalated/silent_alerted 均不得推断或默认绑定 attempt。"""
    for event in ("report", "failed", "escalated", "silent_alerted"):
        kwargs = dict(event=event, tid="t001")
        if event == "report":
            kwargs["status"] = "done"
        elif event == "failed":
            kwargs["fail_class"] = "infra"
        elif event == "silent_alerted":
            kwargs["fingerprint"] = "abc123"
        with pytest.raises(SystemExit, match=rf"--event {event} 必须显式给 --attempt"):
            _record(**kwargs)
    assert ledger_read() == []


def test_ledger_append_concurrent_writes_no_torn_lines(ledger):
    """并发追加：两个线程各写 50 条，无交错坏行、无丢失。"""
    import threading

    def writer(tag):
        for i in range(50):
            ledger_append({"event": "note", "text": f"{tag}-{i}"})

    threads = [threading.Thread(target=writer, args=(tag,)) for tag in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    raw = ledger.read_text(encoding="utf-8").splitlines()
    assert len(raw) == 100
    events = ledger_read()  # 每行都可解析（无坏行警告）
    assert len(events) == 100
    assert {e["text"].split("-")[0] for e in events} == {"a", "b"}


# --------------------------------------------------------------------------
# attempt 生命周期闭环：worker terminal gate 与迟到事件隔离
# --------------------------------------------------------------------------


def test_worker_terminal_requires_explicit_attempt_owner_and_status(ledger):
    _record(event="dispatch", tid="t001", attempt=1, model="opus", worker_id="worker-1")

    with pytest.raises(SystemExit, match="必须显式给 --attempt"):
        _record(event="worker_terminal", tid="t001", worker_id="worker-1", status="completed")
    with pytest.raises(SystemExit, match="必须给 --worker-id"):
        _record(event="worker_terminal", tid="t001", attempt=1, status="completed")
    with pytest.raises(SystemExit, match="不匹配"):
        _record(event="worker_terminal", tid="t001", attempt=1, worker_id="worker-2", status="completed")
    with pytest.raises(SystemExit, match="必须是"):
        _record(event="worker_terminal", tid="t001", attempt=1, worker_id="worker-1", status="done")

    _record(event="worker_terminal", tid="t001", attempt=1, worker_id="worker-1", status="completed")
    terminal = ledger_read()[-1]
    assert terminal["event"] == "worker_terminal"
    assert terminal["attempt"] == 1
    assert terminal["worker_id"] == "worker-1"


def test_reconcile_ready_waits_for_running_worker_terminal():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus",
         "worker_id": "worker-1", "ts": _ts(5)},
    ]
    plan = _plan(events, verify=("ready", "分支 tip done + handoff 齐备"))

    assert plan["actions"][0]["action"] == "await-worker-terminal"
    assert plan["actions"][0]["attempt"] == 1
    assert plan["occupancy"] == {"used": 1, "limit": 3}


def test_reconcile_failed_and_contract_wait_for_running_worker_terminal():
    failed = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus",
         "worker_id": "worker-1", "ts": _ts(5)},
        {"event": "failed", "tid": "t001", "attempt": 1, "class": "infra", "ts": _ts(2)},
    ]
    failed_plan = _plan(failed, ladder=["opus", "haiku"])
    assert failed_plan["actions"][0]["action"] == "await-worker-terminal"
    assert failed_plan["occupancy"]["used"] == 1

    contract = [
        {"event": "dispatch", "tid": "t002", "attempt": 1, "model": "opus",
         "worker_id": "worker-2", "ts": _ts(5)},
    ]
    contract_plan = _plan(
        contract, verify=("contract", "handoff 缺失"), ladder=["opus", "haiku"],
    )
    assert contract_plan["actions"][0]["action"] == "await-worker-terminal"
    assert contract_plan["occupancy"]["used"] == 1


def test_reconcile_terminal_unlocks_ready_and_failed_paths():
    ready = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus",
         "worker_id": "worker-1", "ts": _ts(5)},
        {"event": "worker_terminal", "tid": "t001", "attempt": 1,
         "worker_id": "worker-1", "status": "completed", "ts": _ts(2)},
    ]
    ready_plan = _plan(ready, verify=("ready", "分支 tip done + handoff 齐备"))
    assert ready_plan["actions"][0]["action"] == "integrate"

    failed = [
        {"event": "dispatch", "tid": "t002", "attempt": 1, "model": "opus",
         "worker_id": "worker-2", "ts": _ts(5)},
        {"event": "failed", "tid": "t002", "attempt": 1, "class": "infra", "ts": _ts(3)},
        {"event": "worker_terminal", "tid": "t002", "attempt": 1,
         "worker_id": "worker-2", "status": "failed", "ts": _ts(2)},
    ]
    failed_plan = _plan(failed, ladder=["opus", "haiku"])
    assert failed_plan["actions"][0]["action"] == "redispatch"
    assert failed_plan["actions"][0]["attempt"] == 2


def test_late_attempt_one_events_do_not_end_or_latch_attempt_two():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus",
         "worker_id": "worker-1", "ts": _ts(30)},
        {"event": "worker_terminal", "tid": "t001", "attempt": 1,
         "worker_id": "worker-1", "status": "failed", "ts": _ts(20)},
        {"event": "dispatch", "tid": "t001", "attempt": 2, "model": "haiku",
         "worker_id": "worker-2", "ts": _ts(10)},
        {"event": "worker_terminal", "tid": "t001", "attempt": 2,
         "worker_id": "worker-2", "status": "completed", "ts": _ts(8)},
        {"event": "integrated", "tid": "t001", "attempt": 1, "merge_sha": "late", "ts": _ts(7)},
        {"event": "escalated", "tid": "t001", "attempt": 1, "reason": "late", "ts": _ts(6)},
    ]
    plan = _plan(events, activities={"t001": _ts(5)}, schedule=_schedule(selected=["t001", "t002"]))

    assert plan["occupancy"]["used"] == 1
    assert [(action["action"], action["tid"]) for action in plan["actions"]] == [
        ("dispatch", "t002"),
    ]


def test_dispatch_without_prior_terminal_is_illegal_overlap():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus",
         "worker_id": "worker-1", "ts": _ts(20)},
        {"event": "dispatch", "tid": "t001", "attempt": 2, "model": "haiku",
         "worker_id": "worker-2", "ts": _ts(10)},
    ]
    plan = _plan(events, activities={"t001": _ts(5)})

    assert {action["action"] for action in plan["actions"]} == {"await-worker-terminal"}
    assert {action["attempt"] for action in plan["actions"]} == {1, 2}
    assert plan["occupancy"] == {"used": 2, "limit": 3}
