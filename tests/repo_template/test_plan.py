"""repo_task.plan.compute_batch_plan 行为级回归（对齐原 chain_plan.js 用例）。"""

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "repo_template"
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task.plan import (  # noqa: E402
    compute_batch_plan,
    compute_serial_plan,
    format_plan_text,
)


def node(tid, category, schedule_status="", title=None):
    return {
        "id": tid,
        "title": title or tid,
        "category": category,
        "schedule_status": schedule_status,
        "status": "active" if category == "active" else "backlog",
        "depends_on": [],
        "conflicts_with": [],
    }


def dep(src, dst):
    return {"type": "dep", "from": src, "to": dst}


def conflict(a, b):
    return {"type": "conflict", "from": a, "to": b}


def plan_of(nodes, edges):
    plan = compute_batch_plan({"nodes": nodes, "edges": edges})
    return {
        "chains": [c["taskIds"] for c in plan["chains"]],
        "unassigned": sorted(plan["unassigned"]),
        "deferred": sorted(d["taskId"] for d in plan["deferred"]),
    }


def test_blocked_conflict_not_chain_head():
    assert plan_of(
        [
            node("t001", "blocked_deps", "scheduled"),
            node("t002", "blocked_conflict", "scheduled"),
            node("t003", "backlog", "scheduled"),
        ],
        [dep("t003", "t001"), conflict("t001", "t002")],
    ) == {"chains": [], "unassigned": ["t001", "t002", "t003"], "deferred": []}


def test_unscheduled_successor_not_in_chain():
    assert plan_of(
        [node("t001", "runnable", "scheduled"), node("t002", "backlog", "")],
        [dep("t001", "t002")],
    ) == {"chains": [["t001"]], "unassigned": ["t002"], "deferred": []}


def test_pending_clarification_successor_not_in_chain():
    assert plan_of(
        [
            node("t001", "runnable", "scheduled"),
            node("t002", "backlog", "pending_clarification"),
        ],
        [dep("t001", "t002")],
    ) == {"chains": [["t001"]], "unassigned": ["t002"], "deferred": []}


def test_successor_conflicting_with_active_not_in_chain():
    assert plan_of(
        [
            node("t001", "runnable", "scheduled"),
            node("t002", "backlog", "scheduled"),
            node("t003", "active", ""),
        ],
        [dep("t001", "t002"), conflict("t002", "t003")],
    ) == {"chains": [["t001"], ["t003"]], "unassigned": ["t002"], "deferred": []}


def test_in_chain_conflict_serialized():
    assert plan_of(
        [node("t001", "runnable", "scheduled"), node("t002", "backlog", "scheduled")],
        [dep("t001", "t002"), conflict("t001", "t002")],
    ) == {"chains": [["t001", "t002"]], "unassigned": [], "deferred": []}


def test_successor_conflicting_other_head_not_in_chain():
    assert plan_of(
        [
            node("t001", "runnable", "scheduled"),
            node("t002", "backlog", "scheduled"),
            node("t003", "runnable", "scheduled"),
        ],
        [dep("t001", "t002"), conflict("t002", "t003")],
    ) == {"chains": [["t001"], ["t003"]], "unassigned": ["t002"], "deferred": []}


def test_healthy_dependency_chain_full():
    assert plan_of(
        [
            node("t001", "runnable", "scheduled"),
            node("t002", "blocked_deps", "scheduled"),
            node("t003", "blocked_deps", "scheduled"),
        ],
        [dep("t001", "t002"), dep("t002", "t003")],
    ) == {"chains": [["t001", "t002", "t003"]], "unassigned": [], "deferred": []}


def test_join_point_stops_parallel_chains():
    """两条链在汇流点前停；汇流点进 unassigned，重算后可成新链首。"""
    nodes = [
        node("t023", "runnable", "scheduled", "a1"),
        node("t024", "blocked_deps", "scheduled", "a2"),
        node("t025", "runnable", "scheduled", "b1"),
        node("t026", "blocked_deps", "scheduled", "b2"),
        node("t028", "blocked_deps", "scheduled", "join"),
    ]
    edges = [
        dep("t023", "t024"),
        dep("t025", "t026"),
        dep("t024", "t028"),
        dep("t026", "t028"),
    ]
    first = plan_of(nodes, edges)
    assert first["chains"] == [["t023", "t024"], ["t025", "t026"]]
    assert "t028" in first["unassigned"]

    # 模拟链 A/B 完成：t024/t026 变 done 后不在未完成子图；t028 变 runnable
    after = plan_of(
        [node("t028", "runnable", "scheduled", "join")],
        [],
    )
    assert after["chains"] == [["t028"]]


def test_format_includes_titles_and_copy_mode():
    data = {
        "nodes": [
            node("t001", "runnable", "scheduled", "扣减 owner"),
            node("t002", "blocked_deps", "scheduled", "复用 owner"),
        ],
        "edges": [dep("t001", "t002")],
    }
    plan = compute_batch_plan(data)
    plan["titles"] = {"t001": "扣减 owner", "t002": "复用 owner"}
    plan["mode"] = "batch"
    plan["unlocks"] = []
    text = format_plan_text(plan)
    assert "扣减 owner" in text
    assert "/task-run t001 -> t002" in text
    assert plan["chains"][0].get("head_kind") == "start"
    copy = format_plan_text(plan, copy_only=True)
    assert copy.strip() == "/task-run t001 -> t002"
    assert "扣减" not in copy


def test_serial_orders_by_deps_and_conflict_tid():
    data = {
        "nodes": [
            node("t001", "runnable", "scheduled", "a"),
            node("t002", "runnable", "scheduled", "b"),
            node("t003", "blocked_deps", "scheduled", "c"),
        ],
        "edges": [dep("t001", "t003"), conflict("t001", "t002")],
    }
    plan = compute_serial_plan(data)
    assert plan["chains"][0]["taskIds"] == ["t001", "t002", "t003"]


def test_active_head_kind_continue_and_copy_annotation():
    data = {
        "nodes": [
            node("t001", "active", "", "进行中"),
            node("t002", "blocked_deps", "scheduled", "后继"),
        ],
        "edges": [dep("t001", "t002")],
    }
    plan = compute_batch_plan(data)
    assert plan["chains"][0]["taskIds"] == ["t001", "t002"]
    assert plan["chains"][0]["head_kind"] == "continue"
    plan["titles"] = {"t001": "进行中", "t002": "后继"}
    plan["mode"] = "batch"
    plan["unlocks"] = []
    text = format_plan_text(plan)
    assert "[接续]" in text
    copy = format_plan_text(plan, copy_only=True)
    assert "/task-run t001 -> t002" in copy
    assert "接续 active" in copy


def test_unlock_rows_and_stop_reason_for_join():
    from repo_task.plan import _unlock_rows

    nodes = [
        node("t023", "runnable", "scheduled", "a1"),
        node("t024", "blocked_deps", "scheduled", "a2"),
        node("t025", "runnable", "scheduled", "b1"),
        node("t026", "blocked_deps", "scheduled", "b2"),
        node("t028", "blocked_deps", "scheduled", "join"),
    ]
    edges = [
        dep("t023", "t024"),
        dep("t025", "t026"),
        dep("t024", "t028"),
        dep("t026", "t028"),
    ]
    data = {"nodes": nodes, "edges": edges}
    plan = compute_batch_plan(data)
    assert "汇合点 t028" in (plan["chains"][0].get("stop_reason") or "")
    assert "汇合点 t028" in (plan["chains"][1].get("stop_reason") or "")
    unlocks = _unlock_rows(plan, data)
    by_id = {row["taskId"]: row for row in unlocks}
    assert "t028" in by_id
    assert set(by_id["t028"]["requires"]) == {"t024", "t026"}
    assert by_id["t028"]["join"] is True
    assert any("链" in d for d in by_id["t028"]["requires_desc"])
    plan["titles"] = {n["id"]: n["title"] for n in nodes}
    plan["unlocks"] = unlocks
    plan["mode"] = "batch"
    text = format_plan_text(plan)
    assert "全部完成 → t028 可开（汇流）" in text
    assert "integrate-chain" in text


def test_serial_copy_single_line():
    data = {
        "nodes": [
            node("t001", "runnable", "scheduled", "a"),
            node("t002", "blocked_deps", "scheduled", "b"),
        ],
        "edges": [dep("t001", "t002")],
    }
    plan = compute_serial_plan(data)
    plan["titles"] = {"t001": "a", "t002": "b"}
    copy = format_plan_text(plan, copy_only=True)
    assert copy.strip() == "/task-run t001 -> t002"


def test_serial_conflict_sort_tolerates_non_tid_keys():
    """冲突边排序统一走 _tid_key，非规范 id 不 raise。"""
    data = {
        "nodes": [
            {**node("t001", "runnable", "scheduled"), "id": "t001"},
            {
                "id": "weird",
                "title": "w",
                "category": "runnable",
                "schedule_status": "scheduled",
                "status": "backlog",
                "depends_on": [],
                "conflicts_with": [],
            },
        ],
        "edges": [conflict("t001", "weird")],
    }
    plan = compute_serial_plan(data)
    assert set(plan["chains"][0]["taskIds"]) == {"t001", "weird"}
