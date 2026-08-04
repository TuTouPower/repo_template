"""调度控制面：账本读写、attempt 编号、record 校验、ps 状态判定、reconcile 行动计划。"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "repo_template"
sys.path.insert(0, str(SCRIPTS_DIR))

import task as task_mod
from task import (
    TaskDataError,
    compute_ps_rows,
    compute_reconcile_plan,
    is_stalled,
    ledger_append,
    ledger_next_attempt,
    ledger_read,
)

NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)


def _ts(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """账本路径重定向到临时目录。"""
    runtime = tmp_path / "docs" / "runtime"
    monkeypatch.setattr(task_mod, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(task_mod, "LEDGER_PATH", runtime / "dispatch_ledger.jsonl")
    return runtime / "dispatch_ledger.jsonl"


def _record(**kwargs):
    defaults = dict(
        event=None, tid=None, attempt=None, model=None,
        status=None, sha=None, fail_class=None, state=None, reason=None,
    )
    defaults.update(kwargs)
    task_mod.cmd_ledger_record(argparse.Namespace(**defaults))


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


def test_record_dispatch_auto_assigns_attempt(ledger, capsys):
    _record(event="dispatch", tid="t001", model="opus")
    _record(event="dispatch", tid="t001", model="haiku")

    events = ledger_read()
    assert [e["attempt"] for e in events] == [1, 2]
    out = capsys.readouterr().out
    assert "recorded: dispatch t001#1 model=opus" in out
    assert "recorded: dispatch t001#2 model=haiku" in out


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


def test_record_report_defaults_to_latest_attempt(ledger):
    _record(event="dispatch", tid="t001", model="opus")
    _record(event="report", tid="t001", status="done", sha="abc")

    events = ledger_read()
    assert events[1]["attempt"] == 1
    assert events[1]["status"] == "done"


def test_ledger_tail_filters_tid_and_reverses(ledger, capsys):
    _record(event="dispatch", tid="t001", attempt=1, model="opus")
    _record(event="failed", tid="t001", attempt=1, fail_class="infra", reason="boom")
    _record(event="dispatch", tid="t002", attempt=1, model="haiku")
    capsys.readouterr()  # 排空 record 的打印

    task_mod.cmd_ledger_tail(argparse.Namespace(tid="t001", n=20))

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert "failed t001#1" in lines[0] and "class=infra" in lines[0]
    assert "dispatch t001#1" in lines[1] and "model=opus" in lines[1]
    assert all("t002" not in line for line in lines)


# --------------------------------------------------------------------------
# stalled 判定（纯函数）
# --------------------------------------------------------------------------


def test_is_stalled_threshold():
    assert is_stalled(_ts(21), 20, NOW)
    assert not is_stalled(_ts(19), 20, NOW)
    assert not is_stalled("无法解析", 20, NOW)


# --------------------------------------------------------------------------
# ps 活表状态判定（合成账本 + 注入 observer）
# --------------------------------------------------------------------------


def _ps_rows(events, effective=None, main_statuses=None, activities=None,
             verify=("incomplete", "refs 未完成")):
    activities = activities or {}
    return compute_ps_rows(
        events,
        effective or {},
        main_statuses or {},
        stall_minutes=20,
        now=NOW,
        observer=lambda tid, tev: activities.get(tid),
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


def test_ps_progressing_and_stalled_from_activity():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(5)},
        {"event": "dispatch", "tid": "t002", "attempt": 1, "model": "opus", "ts": _ts(30)},
    ]
    rows = _ps_rows(
        events,
        activities={"t001": _ts(5), "t002": _ts(30)},
    )

    by_tid = {row["tid"]: row for row in rows}
    assert by_tid["t001"]["state"] == "progressing"
    assert by_tid["t001"]["model"] == "opus"
    assert by_tid["t002"]["state"] == "stalled?"


def test_ps_dispatched_without_observation_point():
    rows = _ps_rows(
        [{"event": "dispatch", "tid": "t001", "attempt": 1, "ts": _ts(5)}],
        activities={"t001": None},
    )

    assert rows[0]["state"] == "dispatched(无观察点)"


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
        limit=3, scope=None, ladder=None, stall_minutes=20,
        max_auto_retries=1, now=NOW,
        observer=lambda tid, tev: activities.get(tid),
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


def test_reconcile_stalled_redispatches_with_ladder_model():
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(30)},
    ]
    plan = _plan(
        events,
        activities={"t001": _ts(30)},
        ladder=["opus", "haiku"],
    )

    action, = plan["actions"]
    assert action["action"] == "redispatch"
    assert action["attempt"] == 2
    assert action["model"] == "haiku"
    assert "resource" in action["reason"]
    assert plan["occupancy"]["used"] == 1  # redispatch 原位补槽，escalate 才释放槽


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

    # t001 progressing 占槽但不在授权范围 → 无动作；t002 stalled → redispatch（补槽）；
    # 占槽 2/3，t003 在授权范围外不补位
    assert [(a["action"], a["tid"]) for a in plan["actions"]] == [("redispatch", "t002")]
    assert plan["occupancy"] == {"used": 2, "limit": 3}


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

    from task import breaker_states

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
    """worker 已在分支 block 但 report 未落账 → escalate（分支已 blocked），
    不算 stalled、不占重试额度。"""
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(60)},
    ]
    schedule = _schedule()
    schedule["tasks"] = {"t001": {"status": "blocked"}}
    plan = _plan(
        events, schedule,
        activities={"t001": _ts(60)},  # 即使超 stall 也不走 resource 重派
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


def test_reconcile_occupancy_caps_redispatch_plus_dispatch():
    """占槽语义：limit=3、3 个 stalled → redispatch×3 占满，不再补位 dispatch×3。"""
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
        ("redispatch", "t001"), ("redispatch", "t002"), ("redispatch", "t003"),
    ]
    assert plan["occupancy"] == {"used": 3, "limit": 3}


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


def test_is_stalled_naive_timestamp_tolerated():
    """naive（无 offset）时间戳按本地时区解释，不与 aware 比较 TypeError。"""
    naive_old = (NOW - timedelta(minutes=30)).astimezone().replace(tzinfo=None)
    naive_new = (NOW - timedelta(minutes=10)).astimezone().replace(tzinfo=None)

    assert is_stalled(naive_old.isoformat(timespec="seconds"), 20, NOW)
    assert not is_stalled(naive_new.isoformat(timespec="seconds"), 20, NOW)


# --------------------------------------------------------------------------
# 第三轮审阅修复回归：全局占槽、终态不阻塞、钳制范围、contract 现场、
# 多分支、record attempt 守卫、账本追加锁
# --------------------------------------------------------------------------


def test_reconcile_occupancy_counts_out_of_scope_in_flight():
    """占槽按全局在飞计：3 个 scope 外 stalled 占满 limit，scope 内 t004 不补位。"""
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

    assert plan["actions"] == []  # stalled 三个不在授权范围，无动作
    assert plan["occupancy"] == {"used": 3, "limit": 3}  # 但槽被占满，t004 无法补位


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


def test_reconcile_single_rung_stalled_redispatches_same_model():
    """单档阶梯 + stalled（resource）：允许同模型 redispatch，吃满重试额度。"""
    events = [
        {"event": "dispatch", "tid": "t001", "attempt": 1, "model": "opus", "ts": _ts(30)},
    ]
    plan = _plan(events, activities={"t001": _ts(30)}, ladder=["opus"], mode="resume")

    action, = plan["actions"]
    assert action["action"] == "redispatch"
    assert action["model"] == "opus"
    assert action["mode"] == "resume"


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


def test_record_report_without_any_dispatch_rejected(ledger):
    """report/failed/escalated 无法解析归属 attempt（从未 dispatch）→ 拒绝落账。"""
    for event in ("report", "failed", "escalated"):
        kwargs = dict(event=event, tid="t001")
        if event == "report":
            kwargs["status"] = "done"
        if event == "failed":
            kwargs["fail_class"] = "infra"
        with pytest.raises(SystemExit, match="从未 dispatch"):
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
