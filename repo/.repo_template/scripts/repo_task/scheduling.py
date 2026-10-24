"""Canonical scheduling implementation for the task toolchain."""

from __future__ import annotations

import repo_task.context as ctx

from .documents import parse_tid_list, tid_sort_key
from .git_ops import require_primary_worktree
from .store import discover_effective_tasks, scan_tasks


def _dependency_cycle(dependencies: dict[str, list[str]]) -> list[str] | None:
    state: dict[str, int] = {}

    for start_tid in sorted(dependencies, key=tid_sort_key):
        if state.get(start_tid, 0) != 0:
            continue
        stack = [start_tid]
        state[start_tid] = 1
        it_stack = [iter(dependencies.get(start_tid, []))]
        while stack:
            parent = stack[-1]
            children_it = it_stack[-1]
            try:
                dep = next(children_it)
                if dep not in dependencies:
                    continue
                dep_state = state.get(dep, 0)
                if dep_state == 1:
                    idx = stack.index(dep)
                    return stack[idx:] + [dep]
                elif dep_state == 0:
                    state[dep] = 1
                    stack.append(dep)
                    it_stack.append(iter(dependencies.get(dep, [])))
            except StopIteration:
                stack.pop()
                it_stack.pop()
                state[parent] = 2
    return None

def compute_schedule() -> dict:
    """可跑集与分组计算：cmd_view 渲染与 reconcile 行动计划共用的只读调度图。"""
    require_primary_worktree()
    tasks = discover_effective_tasks()

    dependencies: dict[str, list[str]] = {}
    conflicts: dict[str, set[str]] = {tid: set() for tid in tasks}
    for tid, task in tasks.items():
        if task["status"] in ctx.ARCHIVED_STATUSES:
            continue
        task_dependencies = parse_tid_list(
            task.get("depends_on", ""), field=f"{tid}.depends_on"
        )
        task_conflicts = parse_tid_list(
            task.get("conflicts_with", ""), field=f"{tid}.conflicts_with"
        )
        for field, references in (
            ("depends_on", task_dependencies),
            ("conflicts_with", task_conflicts),
        ):
            if tid in references:
                raise ctx.TaskDataError(f"invalid_graph: {tid}.{field} 引用自身")
            missing = [reference for reference in references if reference not in tasks]
            if missing:
                raise ctx.TaskDataError(
                    f"invalid_graph: {tid}.{field} 引用不存在 task "
                    f"{','.join(missing)}"
                )
            dropped = [
                reference for reference in references
                if tasks[reference]["status"] == "dropped"
            ]
            if dropped:
                raise ctx.TaskDataError(
                    f"invalid_graph: {tid}.{field} 引用 dropped task "
                    f"{','.join(dropped)}"
                )
        dependencies[tid] = task_dependencies
        for peer in task_conflicts:
            conflicts[tid].add(peer)
            conflicts[peer].add(tid)

    cycle = _dependency_cycle(dependencies)
    if cycle:
        raise ctx.TaskDataError(
            "invalid_graph: depends_on cycle " + " -> ".join(cycle)
        )

    main_tasks = {task["tid"]: task for task in scan_tasks()}
    main_done_set = {tid for tid, task in main_tasks.items() if task["status"] == "done"}
    effective_done_set = {tid for tid, task in tasks.items() if task["status"] == "done"}
    unmerged_done = sorted(effective_done_set - main_done_set, key=tid_sort_key)
    dropped_set = {tid for tid, task in tasks.items() if task["status"] == "dropped"}
    active_list = sorted(
        (tid for tid, task in tasks.items() if task["status"] == "active"),
        key=tid_sort_key,
    )
    active_set = set(active_list)
    backlog_tasks = {tid: task for tid, task in tasks.items() if task["status"] == "backlog"}

    ready: list[str] = []
    waiting_deps: list[tuple[str, str]] = []
    blocked_conflicts: list[tuple[str, str]] = []
    for tid in sorted(backlog_tasks, key=tid_sort_key):
        missing = [dep for dep in dependencies.get(tid, []) if dep not in effective_done_set]
        if missing:
            waiting_deps.extend((dep, tid) for dep in sorted(missing, key=tid_sort_key))
            continue
        blocking = sorted(conflicts[tid] & active_set, key=tid_sort_key)
        if blocking:
            blocked_conflicts.extend((tid, peer) for peer in blocking)
            continue
        ready.append(tid)

    # Every dependency-ready task is a valid head. conflicts_with is only a
    # parallel-planning hint; plan.py decides how to separate conflicting heads.
    selected = list(ready)
    return {
        "tasks": tasks,
        "dependencies": dependencies,
        "conflicts": conflicts,
        "main_done_set": main_done_set,
        "effective_done_set": effective_done_set,
        "unmerged_done": unmerged_done,
        "dropped_set": dropped_set,
        "active_list": active_list,
        "active_set": active_set,
        "backlog_tasks": backlog_tasks,
        "ready": ready,
        "waiting_deps": waiting_deps,
        "blocked_conflicts": blocked_conflicts,
        "selected": selected,
    }
