"""repo_task.plan.compute_batch_plan 行为级回归（批计划算法唯一权威）。"""

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import repo_task.context as ctx  # noqa: E402
from repo_task.plan import (  # noqa: E402
    compute_batch_plan,
    compute_queue_topology,
    compute_serial_plan,
    format_plan_text,
    group_queue,
)

pytestmark = pytest.mark.contract


def node(tid, category, title=None):
    return {
        "id": tid,
        "title": title or tid,
        "category": category,
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
            node("t001", "blocked_deps"),
            node("t002", "blocked_conflict"),
            node("t003", "backlog"),
        ],
        [dep("t003", "t001"), conflict("t001", "t002")],
    ) == {"chains": [], "unassigned": ["t001", "t002", "t003"], "deferred": []}






def test_successor_conflicting_with_active_not_in_chain():
    assert plan_of(
        [
            node("t001", "runnable"),
            node("t002", "backlog"),
            node("t003", "active"),
        ],
        [dep("t001", "t002"), conflict("t002", "t003")],
    ) == {"chains": [["t001"], ["t003"]], "unassigned": ["t002"], "deferred": []}


def test_in_chain_conflict_serialized():
    assert plan_of(
        [node("t001", "runnable"), node("t002", "backlog")],
        [dep("t001", "t002"), conflict("t001", "t002")],
    ) == {"chains": [["t001", "t002"]], "unassigned": [], "deferred": []}


def test_successor_conflicting_other_head_not_in_chain():
    assert plan_of(
        [
            node("t001", "runnable"),
            node("t002", "backlog"),
            node("t003", "runnable"),
        ],
        [dep("t001", "t002"), conflict("t002", "t003")],
    ) == {"chains": [["t001"], ["t003"]], "unassigned": ["t002"], "deferred": []}


def test_healthy_dependency_chain_full():
    assert plan_of(
        [
            node("t001", "runnable"),
            node("t002", "blocked_deps"),
            node("t003", "blocked_deps"),
        ],
        [dep("t001", "t002"), dep("t002", "t003")],
    ) == {"chains": [["t001", "t002", "t003"]], "unassigned": [], "deferred": []}


def test_join_point_stops_parallel_chains():
    """两条链在汇流点前停；汇流点进 unassigned，重算后可成新链首。"""
    nodes = [
        node("t023", "runnable", title="a1"),
        node("t024", "blocked_deps", title="a2"),
        node("t025", "runnable", title="b1"),
        node("t026", "blocked_deps", title="b2"),
        node("t028", "blocked_deps", title="join"),
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
        [node("t028", "runnable", title="join")],
        [],
    )
    assert after["chains"] == [["t028"]]


def test_format_includes_titles_and_copy_mode():
    data = {
        "nodes": [
            node("t001", "runnable", title="扣减 owner"),
            node("t002", "blocked_deps", title="复用 owner"),
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
            node("t001", "runnable", title="a"),
            node("t002", "runnable", title="b"),
            node("t003", "blocked_deps", title="c"),
        ],
        "edges": [dep("t001", "t003"), conflict("t001", "t002")],
    }
    plan = compute_serial_plan(data)
    assert plan["chains"][0]["taskIds"] == ["t001", "t002", "t003"]


def test_active_head_kind_continue_and_copy_annotation():
    data = {
        "nodes": [
            node("t001", "active", title="进行中"),
            node("t002", "blocked_deps", title="后继"),
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
        node("t023", "runnable", title="a1"),
        node("t024", "blocked_deps", title="a2"),
        node("t025", "runnable", title="b1"),
        node("t026", "blocked_deps", title="b2"),
        node("t028", "blocked_deps", title="join"),
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
            node("t001", "runnable", title="a"),
            node("t002", "blocked_deps", title="b"),
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
            {**node("t001", "runnable"), "id": "t001"},
            {
                "id": "weird",
                "title": "w",
                "category": "runnable",
                "status": "backlog",
                "depends_on": [],
                "conflicts_with": [],
            },
        ],
        "edges": [conflict("t001", "weird")],
    }
    plan = compute_serial_plan(data)
    assert set(plan["chains"][0]["taskIds"]) == {"t001", "weird"}


def test_plan_dependency_with_reverse_conflict_satisfies_hard_dependency():
    """验证较小编号 task 硬依赖较大会话且登记反向冲突时，硬依赖始终在前（Issue #33）。"""
    # t001 depends on t002, t001 conflicts with t002
    data = {
        "nodes": [
            node("t001", "blocked_deps", title="dependent"),
            node("t002", "runnable", title="dependency"),
        ],
        "edges": [
            dep("t002", "t001"),
            conflict("t001", "t002"),
        ],
    }
    plan = compute_serial_plan(data)
    tasks = plan["chains"][0]["taskIds"]
    assert tasks.index("t002") < tasks.index("t001")



# --- E1：队列按关系分链，每链内以紧邻前一成员为 base --------------------------


def topology(queue, edges):
    return compute_queue_topology(
        queue,
        edges,
        default_branch="main",
        branch_of=lambda tid: f"{tid}_slug",
    )


def bases_of(result):
    return {
        row["taskId"]: (row["chain"], row["base"]["tid"], row["base"]["branch"])
        for row in result["members"]
    }


def test_unrelated_members_each_start_from_default_branch():
    result = topology(["t001", "t002", "t003"], [])
    assert bases_of(result) == {
        "t001": (1, None, "main"),
        "t002": (2, None, "main"),
        "t003": (3, None, "main"),
    }
    assert [c["taskIds"] for c in result["queue_chains"]] == [["t001"], ["t002"], ["t003"]]
    assert [c["tail"] for c in result["queue_chains"]] == ["t001", "t002", "t003"]


def test_depends_on_puts_members_in_one_chain_with_previous_as_base():
    result = topology(["t001", "t002", "t003"], [dep("t001", "t002"), dep("t002", "t003")])
    assert bases_of(result) == {
        "t001": (1, None, "main"),
        "t002": (1, "t001", "t001_slug"),
        "t003": (1, "t002", "t002_slug"),
    }
    assert result["queue_chains"] == [
        {"chain": 1, "taskIds": ["t001", "t002", "t003"], "tail": "t003"},
    ]


@pytest.mark.parametrize("edge", [conflict("t001", "t002"), conflict("t002", "t001")])
def test_one_sided_conflicts_with_chains_members_in_either_direction(edge):
    result = topology(["t001", "t002"], [edge])
    assert bases_of(result) == {
        "t001": (1, None, "main"),
        "t002": (1, "t001", "t001_slug"),
    }


def test_member_related_to_two_earlier_members_merges_them_into_one_chain():
    """t003 同时依赖 t001、与 t002 冲突：t001/t002 互无关系，三者仍成一条链。"""
    result = topology(
        ["t001", "t002", "t003"],
        [dep("t001", "t003"), conflict("t003", "t002")],
    )
    assert bases_of(result) == {
        "t001": (1, None, "main"),
        "t002": (1, "t001", "t001_slug"),
        "t003": (1, "t002", "t002_slug"),
    }


def test_interleaved_queue_splits_chains_and_keeps_queue_order_inside_each():
    result = topology(
        ["t001", "t002", "t003", "t004"],
        [dep("t001", "t003"), conflict("t002", "t004")],
    )
    assert bases_of(result) == {
        "t001": (1, None, "main"),
        "t002": (2, None, "main"),
        "t003": (1, "t001", "t001_slug"),
        "t004": (2, "t002", "t002_slug"),
    }
    assert [(c["chain"], c["taskIds"], c["tail"]) for c in result["queue_chains"]] == [
        (1, ["t001", "t003"], "t003"),
        (2, ["t002", "t004"], "t004"),
    ]
    assert group_queue(
        ["t001", "t002", "t003", "t004"],
        [dep("t001", "t003"), conflict("t002", "t004")],
    ) == [["t001", "t003"], ["t002", "t004"]]


def test_relations_to_members_outside_queue_do_not_link_chains():
    """只看队列内成员：经队列外 t009 间接关联的 t001/t003 仍相互独立。"""
    result = topology(
        ["t001", "t003"],
        [dep("t001", "t009"), conflict("t003", "t009")],
    )
    assert bases_of(result) == {
        "t001": (1, None, "main"),
        "t003": (2, None, "main"),
    }


def test_queue_with_dependency_after_dependent_is_rejected():
    with pytest.raises(ctx.TaskDataError, match="t002.*t001"):
        topology(["t001", "t002"], [dep("t002", "t001")])
    # 非相邻、非首尾也一样：依赖 t001 排在依赖者 t003 之后
    with pytest.raises(ctx.TaskDataError, match="t001.*t003"):
        topology(["t003", "t002", "t001"], [dep("t001", "t003")])


def test_explicit_queue_keeps_given_order_and_rejects_unlisted_members():
    data = {
        "nodes": [
            node("t001", "runnable"),
            node("t002", "runnable"),
            node("t003", "blocked_deps"),
            node("t004", "done"),
        ],
        "edges": [dep("t001", "t003")],
    }
    plan = compute_serial_plan(data, queue=["t002", "t001", "t003"])
    assert plan["chains"][0]["taskIds"] == ["t002", "t001", "t003"]
    with pytest.raises(ctx.TaskDataError, match="t004"):
        compute_serial_plan(data, queue=["t001", "t004"])
    with pytest.raises(ctx.TaskDataError, match="t009"):
        compute_serial_plan(data, queue=["t009"])
    with pytest.raises(ctx.TaskDataError, match="重复"):
        compute_serial_plan(data, queue=["t001", "t001"])


def test_serial_text_shows_chain_and_base_per_member_and_merge_commands():
    data = {
        "nodes": [
            node("t001", "runnable", title="a"),
            node("t002", "runnable", title="b"),
            node("t003", "blocked_deps", title="c"),
        ],
        "edges": [dep("t001", "t003")],
    }
    plan = compute_serial_plan(data)
    plan.update(topology(plan["chains"][0]["taskIds"], data["edges"]))
    plan["titles"] = {"t001": "a", "t002": "b", "t003": "c"}
    text = format_plan_text(plan)
    assert "t001  a  [链1 base=main]" in text
    assert "t002  b  [链2 base=main]" in text
    assert "t003  c  [链1 base=t001_slug]" in text
    assert "integrate-chain t003" in text
    assert "integrate t002" in text
    # 复制模式仍是单行队列
    assert format_plan_text(plan, copy_only=True).strip() == "/task-run t001 -> t002 -> t003"
