"""本波并发链规划：确定性、只读、不落盘。

算法对齐原看板 chain_plan.js computeBatchPlan：链首 = active/runnable
（runnable = compute_schedule 的 selected），冲突裁决最大化并行链数，
向下游延伸直至汇流点或冲突边界。状态变化后重新调用即得下一批。
"""

from __future__ import annotations

import json
import sys
from typing import Any

import repo_task.context as ctx

from .documents import tid_sort_key
from .scheduling import compute_schedule

CHAIN_COLORS = [
    "#0284C7", "#7C3AED", "#D97706", "#059669", "#DB2777",
    "#4F46E5", "#0D9488", "#EA580C", "#65A30D", "#9333EA",
]
CHAIN_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
UNFINISHED = frozenset({
    "active", "runnable", "blocked_deps", "blocked_conflict", "backlog",
})
CATEGORIES = (
    "active",
    "runnable",
    "blocked_deps",
    "blocked_conflict",
    "backlog",
    "done",
    "dropped",
)


def is_unfinished(category: str | None) -> bool:
    return category in UNFINISHED


def chain_name(index: int) -> str:
    if index < len(CHAIN_LETTERS):
        return f"链 {CHAIN_LETTERS[index]}"
    return f"链 {index + 1}"


def chain_letter(name: str) -> str:
    return str(name).replace("链 ", "", 1)


def chain_of_map(chains: list[dict]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for chain in chains:
        for tid in chain["taskIds"]:
            mapping[tid] = chain["id"]
    return mapping


def chain_text(chain: dict) -> str:
    return f"{chain_letter(chain['name'])}: {' '.join(chain['taskIds'])}"


def _tid_key(tid: str) -> int:
    """排序键；非规范 tid 排 0，避免 tid_sort_key raise。"""
    return tid_sort_key(tid) if ctx.TID_RE.fullmatch(tid) else 0


def _task_run_line(task_ids: list[str]) -> str:
    return "/task-run " + " -> ".join(task_ids)


def _chain_head_kind(data: dict, head_tid: str) -> str:
    """链首语义：active=接续运行中；runnable=新启动。"""
    for node in data.get("nodes") or []:
        if node["id"] == head_tid:
            if node.get("category") == "active":
                return "continue"
            return "start"
    return "start"


def classify_node(tid: str, tasks: dict, schedule: dict) -> str:
    """节点分类：与 view_server 看板同源。"""
    task = tasks.get(tid)
    if not task:
        return "backlog"
    status = task["status"]
    if status in ("active", "blocked"):
        return "active"
    if status in ctx.ARCHIVED_STATUSES:
        return "done" if status == "done" else "dropped"
    if tid in schedule["selected"]:
        return "runnable"
    if tid in [row[1] for row in schedule["waiting_deps"]]:
        return "blocked_deps"
    if tid in [row[0] for row in schedule["blocked_conflicts"]]:
        return "blocked_conflict"
    return "backlog"


def build_board_model(schedule: dict | None = None) -> dict:
    """调度图 → 看板/plan 共用模型（nodes + edges + summary）。"""
    schedule = schedule if schedule is not None else compute_schedule()
    tasks = schedule["tasks"]
    nodes = []
    for tid, task in tasks.items():
        nodes.append({
            "id": tid,
            "title": task.get("title") or tid,
            "status": task["status"],
            "category": classify_node(tid, tasks, schedule),
            "schedule_status": task.get("schedule_status", ""),
            "depends_on": [
                item.strip()
                for item in str(task.get("depends_on", "")).split(",")
                if item.strip()
            ],
            "conflicts_with": [
                item.strip()
                for item in str(task.get("conflicts_with", "")).split(",")
                if item.strip()
            ],
        })
    edges: list[dict] = []
    seen: set = set()
    id_set = {node["id"] for node in nodes}
    for node in nodes:
        for dep in node["depends_on"]:
            key = ("dep", dep, node["id"])
            if key not in seen and dep in id_set:
                edges.append({"type": "dep", "from": dep, "to": node["id"]})
                seen.add(key)
        for peer in node["conflicts_with"]:
            key = ("conflict", tuple(sorted([node["id"], peer])))
            if key not in seen and peer in id_set:
                edges.append({"type": "conflict", "from": node["id"], "to": peer})
                seen.add(key)
    summary = {category: 0 for category in CATEGORIES}
    for node in nodes:
        summary[node["category"]] += 1
    return {
        "project": ctx.REPO_ROOT.name,
        "nodes": nodes,
        "edges": edges,
        "summary": summary,
    }


def compute_crossings(chains: list[dict], data: dict) -> list[dict]:
    chain_of = chain_of_map(chains)
    cat_of = {node["id"]: node["category"] for node in data["nodes"]}
    crossings = []
    for edge in data["edges"]:
        if edge.get("type") != "dep":
            continue
        to_id = edge["to"]
        to_cat = cat_of.get(to_id)
        if not to_cat or not is_unfinished(to_cat):
            continue
        from_chain = chain_of.get(edge["from"])
        if not from_chain:
            continue
        to_chain = chain_of.get(to_id)
        if to_chain == from_chain:
            continue
        crossings.append({
            "chainId": to_chain or "future",
            "nodeId": to_id,
            "dependsOnChainId": from_chain,
            "dependsOnNodeId": edge["from"],
        })
    crossings.sort(
        key=lambda item: (
            _tid_key(item["dependsOnNodeId"]),
            _tid_key(item["nodeId"]),
        )
    )
    return crossings


def _build_subgraph(data: dict) -> dict:
    id_set: set[str] = set()
    cat_of: dict[str, str] = {}
    sched_of: dict[str, str] = {}
    for node in data["nodes"]:
        if is_unfinished(node["category"]):
            id_set.add(node["id"])
            cat_of[node["id"]] = node["category"]
            sched_of[node["id"]] = node.get("schedule_status") or ""
    ids = sorted(id_set, key=_tid_key)

    upstream: dict[str, list[str]] = {tid: [] for tid in ids}
    downstream: dict[str, list[str]] = {tid: [] for tid in ids}
    indegree: dict[str, int] = {tid: 0 for tid in ids}
    conflict_of: dict[str, list[str]] = {tid: [] for tid in ids}

    for edge in data["edges"]:
        if edge.get("type") == "dep":
            src, dst = edge["from"], edge["to"]
            if src not in id_set or dst not in id_set:
                continue
            downstream[src].append(dst)
            upstream[dst].append(src)
            indegree[dst] = indegree.get(dst, 0) + 1
        else:
            a, b = edge["from"], edge["to"]
            if a in id_set and b in id_set:
                if b not in conflict_of[a]:
                    conflict_of[a].append(b)
                if a not in conflict_of[b]:
                    conflict_of[b].append(a)

    for tid in ids:
        downstream[tid].sort(key=_tid_key)
    return {
        "ids": ids,
        "id_set": id_set,
        "cat_of": cat_of,
        "sched_of": sched_of,
        "upstream": upstream,
        "downstream": downstream,
        "indegree": indegree,
        "conflict_of": conflict_of,
    }


def chain_stop_info(chain: dict, plan: dict, data: dict) -> str:
    """链停止原因（人类可读）。"""
    task_ids = chain.get("taskIds") or []
    if not task_ids:
        return ""
    tail = task_ids[-1]
    cat_of = {node["id"]: node["category"] for node in data["nodes"]}
    chain_set = set(task_ids)
    chain_of = chain_of_map(plan["chains"])
    name_of = {c["id"]: c["name"] for c in plan["chains"]}
    deferred_set = {item["taskId"] for item in plan.get("deferred") or []}

    upstream: dict[str, list[str]] = {}
    for edge in data["edges"]:
        if edge.get("type") != "dep":
            continue
        upstream.setdefault(edge["to"], []).append(edge["from"])

    successors = [
        edge["to"]
        for edge in data["edges"]
        if edge.get("type") == "dep"
        and edge["from"] == tail
        and is_unfinished(cat_of.get(edge["to"]))
    ]
    if not successors:
        return "已到 DAG 末端"

    parts: list[str] = []
    for successor in successors:
        if successor in chain_set:
            continue
        if successor in deferred_set:
            parts.append(f"后继 {successor} 本轮冲突暂缓")
        elif successor in chain_of:
            parts.append(f"后继 {successor} 属于{name_of.get(chain_of[successor])}")
        else:
            others = [
                parent
                for parent in upstream.get(successor, [])
                if is_unfinished(cat_of.get(parent)) and parent not in chain_set
            ]
            if others:
                desc = "、".join(
                    f"{name_of[chain_of[parent]]}的 {parent}"
                    if parent in chain_of
                    else parent
                    for parent in others
                )
                parts.append(f"停在汇合点 {successor}(还需 {desc})")
            else:
                parts.append(f"{successor} 下批可跑")
    return ";".join(parts)


def compute_batch_plan(data: dict) -> dict:
    """计算当下可执行批次。

    返回 {chains, unassigned, deferred, crossings}；每条 chain 含 stop_reason。
    """
    sub = _build_subgraph(data)

    candidates = [
        tid for tid in sub["ids"]
        if sub["indegree"].get(tid, 0) == 0
        and sub["cat_of"].get(tid) in ("active", "runnable")
    ]
    active_heads = [tid for tid in candidates if sub["cat_of"].get(tid) == "active"]
    active_set = set(active_heads)
    rest = [tid for tid in candidates if tid not in active_set]

    deferred_list: list[dict] = []
    for tid in rest:
        partners = sorted(
            (peer for peer in sub["conflict_of"].get(tid, []) if peer in active_set),
            key=_tid_key,
        )
        if partners:
            deferred_list.append({"taskId": tid, "partners": partners})
    deferred_set = {item["taskId"] for item in deferred_list}
    rest = [tid for tid in rest if tid not in deferred_set]

    while True:
        remaining = [tid for tid in rest if tid not in deferred_set]
        degree: dict[str, int] = {}
        max_deg = 0
        for tid in remaining:
            deg = sum(
                1 for peer in sub["conflict_of"].get(tid, []) if peer in remaining
            )
            degree[tid] = deg
            if deg > max_deg:
                max_deg = deg
        if max_deg == 0:
            break
        victims = sorted(
            (tid for tid in remaining if degree.get(tid, 0) == max_deg),
            key=_tid_key,
        )
        victim = victims[0]
        partners = sorted(
            (peer for peer in sub["conflict_of"].get(victim, []) if peer in remaining),
            key=_tid_key,
        )
        deferred_list.append({"taskId": victim, "partners": partners})
        deferred_set.add(victim)

    winners = [tid for tid in rest if tid not in deferred_set]
    heads = sorted(active_heads + winners, key=_tid_key)
    head_set = set(heads)
    assigned: set[str] = set()
    chains: list[dict] = []

    for head in heads:
        if head in assigned:
            continue
        task_ids = [head]
        chain_set = {head}
        assigned.add(head)

        while True:
            tail = task_ids[-1]
            is_crossing = any(
                y in sub["id_set"]
                and y not in chain_set
                and any(parent not in chain_set for parent in sub["upstream"].get(y, []))
                for y in sub["downstream"].get(tail, [])
            )
            if is_crossing:
                break
            cands = []
            for tid in sub["downstream"].get(tail, []):
                if tid in assigned:
                    continue
                if not all(parent in chain_set for parent in sub["upstream"].get(tid, [])):
                    continue
                if sub["sched_of"].get(tid) != "scheduled":
                    continue
                conflicts = sub["conflict_of"].get(tid, [])
                blocked = False
                for peer in conflicts:
                    if peer in chain_set:
                        continue
                    if sub["cat_of"].get(peer) == "active":
                        blocked = True
                        break
                    if peer in assigned or peer in head_set:
                        blocked = True
                        break
                if blocked:
                    continue
                cands.append(tid)
            if not cands:
                break
            cands.sort(key=_tid_key)
            nxt = cands[0]
            task_ids.append(nxt)
            chain_set.add(nxt)
            assigned.add(nxt)

        idx = len(chains)
        chains.append({
            "id": f"c{idx + 1}",
            "name": chain_name(idx),
            "color": CHAIN_COLORS[idx % len(CHAIN_COLORS)],
            "taskIds": task_ids,
            "head_kind": _chain_head_kind(data, head),
        })

    unassigned = [
        tid for tid in sub["ids"]
        if tid not in assigned and tid not in deferred_set
    ]
    head_set = set(heads)
    deferred = []
    for item in deferred_list:
        blocked_by = [
            peer for peer in item["partners"]
            if peer in head_set or peer in active_set
        ]
        shown = blocked_by if blocked_by else list(item["partners"])
        deferred.append({
            "taskId": item["taskId"],
            "blockedBy": shown,
            "reason": (
                f"与 {'、'.join(shown)} 冲突,本轮暂缓(优先让可并行的链最多)"
            ),
        })
    deferred.sort(key=lambda item: _tid_key(item["taskId"]))

    plan = {
        "chains": chains,
        "unassigned": unassigned,
        "deferred": deferred,
        "crossings": compute_crossings(chains, data),
    }
    for chain in chains:
        chain["stop_reason"] = chain_stop_info(chain, plan, data)
    return plan


def compute_serial_plan(data: dict) -> dict:
    """全部未完成已排程/运行中 task 排成单链（依赖序 + 冲突对序号小者先）。"""
    unfinished = [
        node for node in data["nodes"] if is_unfinished(node["category"])
    ]
    # 可进串行队列：active 或 scheduled backlog 类
    eligible = []
    for node in unfinished:
        cat = node["category"]
        if cat == "active":
            eligible.append(node["id"])
        elif node.get("schedule_status") == "scheduled":
            eligible.append(node["id"])
    eligible_set = set(eligible)

    deps: dict[str, set[str]] = {tid: set() for tid in eligible}
    for edge in data["edges"]:
        if edge.get("type") != "dep":
            continue
        src, dst = edge["from"], edge["to"]
        if src in eligible_set and dst in eligible_set:
            deps[dst].add(src)

    # 冲突：序号小者优先 → 大者依赖小者（串行强制序）
    for edge in data["edges"]:
        if edge.get("type") == "dep":
            continue
        a, b = edge["from"], edge["to"]
        if a not in eligible_set or b not in eligible_set:
            continue
        if _tid_key(a) < _tid_key(b):
            deps[b].add(a)
        elif _tid_key(b) < _tid_key(a):
            deps[a].add(b)

    remaining = set(eligible)
    ordered: list[str] = []
    while remaining:
        ready = sorted(
            (
                tid for tid in remaining
                if all(dep not in remaining for dep in deps.get(tid, ()))
            ),
            key=_tid_key,
        )
        if not ready:
            # 环或异常：退化为 tid 序
            ready = sorted(remaining, key=_tid_key)
        pick = ready[0]
        ordered.append(pick)
        remaining.remove(pick)

    chains = []
    if ordered:
        head_kind = _chain_head_kind(data, ordered[0])
        chains.append({
            "id": "c1",
            "name": chain_name(0),
            "color": CHAIN_COLORS[0],
            "taskIds": ordered,
            "stop_reason": "全串行一条链",
            "head_kind": head_kind,
        })
    return {
        "chains": chains,
        "unassigned": [],
        "deferred": [],
        "crossings": [],
        "mode": "serial",
    }


def _title_map(data: dict) -> dict[str, str]:
    return {
        node["id"]: (node.get("title") or node["id"])
        for node in data["nodes"]
    }


def _unlock_rows(plan: dict, data: dict) -> list[dict]:
    """汇流/未来节点的解锁提示。"""
    titles = _title_map(data)
    cat_of = {node["id"]: node["category"] for node in data["nodes"]}
    chain_of = chain_of_map(plan["chains"])
    name_of = {c["id"]: c["name"] for c in plan["chains"]}

    upstream: dict[str, list[str]] = {}
    for edge in data["edges"]:
        if edge.get("type") != "dep":
            continue
        if not is_unfinished(cat_of.get(edge["to"])):
            continue
        upstream.setdefault(edge["to"], []).append(edge["from"])

    # 未进本波且有未完成前置的节点
    assigned = {tid for chain in plan["chains"] for tid in chain["taskIds"]}
    deferred_set = {item["taskId"] for item in plan.get("deferred") or []}
    targets = set(plan.get("unassigned") or [])
    for crossing in plan.get("crossings") or []:
        targets.add(crossing["nodeId"])

    rows = []
    for tid in sorted(targets, key=_tid_key):
        if tid in assigned or tid in deferred_set:
            continue
        parents = [
            parent for parent in upstream.get(tid, [])
            if is_unfinished(cat_of.get(parent))
        ]
        if not parents:
            continue
        parents = sorted(parents, key=_tid_key)
        parent_desc = []
        for parent in parents:
            if parent in chain_of:
                parent_desc.append(f"{name_of[chain_of[parent]]} 的 {parent}")
            else:
                parent_desc.append(parent)
        multi_chain = len({
            chain_of[parent] for parent in parents if parent in chain_of
        }) >= 2
        rows.append({
            "taskId": tid,
            "title": titles.get(tid, tid),
            "requires": parents,
            "requires_desc": parent_desc,
            "join": multi_chain or len(parents) >= 2,
        })
    return rows


def build_execution_plan(*, mode: str = "batch") -> dict:
    """从当前仓库状态构建完整执行计划。"""
    schedule = compute_schedule()
    data = build_board_model(schedule)
    if mode == "serial":
        plan = compute_serial_plan(data)
    else:
        plan = compute_batch_plan(data)
        plan["mode"] = "batch"
    plan["titles"] = _title_map(data)
    plan["unlocks"] = _unlock_rows(plan, data)
    plan["project"] = data.get("project", "")
    # 供看板直接注入
    plan["nodes"] = data["nodes"]
    plan["edges"] = data["edges"]
    plan["summary"] = data["summary"]
    return plan


def format_plan_text(plan: dict, *, copy_only: bool = False) -> str:
    """人类可读 plan 输出。

    active 链首语义：输出仍含 `/task-run`（task-run 可接续已有 worktree）；
    非 copy 模式标注「接续」；copy 模式在链行尾注释 `# 接续`。
    """
    titles = plan.get("titles") or {}
    chains = plan.get("chains") or []
    mode = plan.get("mode") or "batch"

    if copy_only:
        if not chains:
            return ""
        lines = []
        for chain in chains:
            line = _task_run_line(chain["taskIds"])
            if chain.get("head_kind") == "continue":
                line += "  # 接续 active"
            lines.append(line)
        return "\n".join(lines) + "\n"

    lines: list[str] = []
    if mode == "serial":
        lines.append("== 全串行一条链 ==")
    else:
        lines.append("== 本波可并发 ==")
        lines.append("（链首 active = 接续运行中；runnable = 新启动。task-run 均可粘贴。）")

    if not chains:
        lines.append("（无可推荐链）")
    else:
        for chain in chains:
            cmd = _task_run_line(chain["taskIds"])
            head_title = titles.get(chain["taskIds"][0], "")
            kind_tag = "  [接续]" if chain.get("head_kind") == "continue" else ""
            subtitle = f"  · {head_title}" if head_title else ""
            lines.append(f"{chain['name']}  {cmd}{kind_tag}{subtitle}")
            for tid in chain["taskIds"]:
                lines.append(f"  {tid}  {titles.get(tid, tid)}")
            stop = chain.get("stop_reason") or ""
            if stop and mode != "serial":
                lines.append(f"  停因：{stop}")
            lines.append("")

    deferred = plan.get("deferred") or []
    if deferred:
        lines.append("== 暂缓（冲突）==")
        for item in deferred:
            tid = item["taskId"]
            lines.append(
                f"  {tid}  {titles.get(tid, tid)}  — {item.get('reason', '')}"
            )
        lines.append("")

    unlocks = plan.get("unlocks") or []
    if unlocks and mode != "serial":
        lines.append("== 下一解锁 ==")
        for row in unlocks:
            req = " + ".join(row.get("requires_desc") or row["requires"])
            tag = "（汇流）" if row.get("join") else ""
            lines.append(
                f"  {req} 全部完成 → {row['taskId']} 可开{tag}"
                f"  {row.get('title', '')}"
            )
            if row.get("join"):
                lines.append(
                    "    注：分叉汇流时 start 须 --base 指向最后完成的前置分支；"
                    "其余前置宜先 integrate-chain 合主干"
                )
        lines.append("")

    unassigned = plan.get("unassigned") or []
    if unassigned and mode != "serial":
        lines.append("== 未进本波 ==")
        for tid in unassigned:
            lines.append(f"  {tid}  {titles.get(tid, tid)}")
        lines.append("")

    if mode == "batch":
        lines.append("状态变化后重跑 task.py plan 得下一批；看板：task.py view --serve")
    return "\n".join(lines).rstrip() + "\n"


def cmd_plan(args: Any) -> None:
    mode = "serial" if getattr(args, "serial", False) else "batch"
    try:
        plan = build_execution_plan(mode=mode)
    except ctx.TaskDataError as error:
        message = str(error)
        if not message.startswith(("invalid_graph:", "invalid_done:")):
            message = f"invalid_graph: {message}"
        sys.exit(f"plan=FAIL：{message}")

    if getattr(args, "json", False):
        # JSON 不嵌完整 nodes/edges 过大时仍有用；保留 plan 核心字段
        payload = {
            "mode": plan.get("mode"),
            "chains": plan.get("chains"),
            "deferred": plan.get("deferred"),
            "unassigned": plan.get("unassigned"),
            "crossings": plan.get("crossings"),
            "unlocks": plan.get("unlocks"),
            "titles": plan.get("titles"),
            "summary": plan.get("summary"),
            "project": plan.get("project"),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(format_plan_text(plan, copy_only=bool(getattr(args, "copy", False))), end="")
