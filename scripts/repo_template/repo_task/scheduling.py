"""Canonical scheduling implementation for the task toolchain."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import repo_task.context as ctx

from .documents import parse_tid_list, tid_sort_key
from .git_ops import require_primary_worktree
from .store import discover_effective_tasks, scan_tasks

def _dependency_cycle(dependencies: dict[str, list[str]]) -> list[str] | None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(tid: str) -> list[str] | None:
        state[tid] = 1
        stack.append(tid)
        for dependency in dependencies.get(tid, []):
            if dependency not in dependencies:
                continue
            if state.get(dependency, 0) == 0:
                cycle = visit(dependency)
                if cycle:
                    return cycle
            elif state.get(dependency) == 1:
                start = stack.index(dependency)
                return stack[start:] + [dependency]
        stack.pop()
        state[tid] = 2
        return None

    for tid in sorted(dependencies, key=tid_sort_key):
        if state.get(tid, 0) == 0:
            cycle = visit(tid)
            if cycle:
                return cycle
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

    invalid_schedule = [
        f"{tid}={task.get('schedule_status', '')!r}"
        for tid, task in tasks.items()
        if task["status"] == "backlog"
        and task.get("schedule_status", "") not in ("", *ctx.SCHEDULE_STATUSES)
    ]
    if invalid_schedule:
        raise ctx.TaskDataError(
            "invalid_graph: schedule_status 非法 " + ",".join(invalid_schedule)
        )

    # done 分两种语义：
    # - main_done_set：已合入 main 的 done（scan_tasks 读当前工作区=main 视角），
    #   用于解依赖/解冲突——只有前置真正入 main，下游才算可跑。
    # - effective done（tasks 里 status=done）：含未合并分支的 done，仅计数展示。
    main_tasks = {task["tid"]: task for task in scan_tasks()}
    main_done_set = {
        tid for tid, task in main_tasks.items() if task["status"] == "done"
    }
    effective_done_set = {
        tid for tid, task in tasks.items() if task["status"] == "done"
    }
    unmerged_done = sorted(
        effective_done_set - main_done_set, key=tid_sort_key
    )
    done_set = main_done_set  # 调度判断用 main 视角
    dropped_set = {
        tid for tid, task in tasks.items() if task["status"] == "dropped"
    }
    active_list = sorted(
        (
            tid for tid, task in tasks.items()
            if task["status"] in ("active", "blocked")
        ),
        key=tid_sort_key,
    )
    backlog_tasks = {
        tid: task for tid, task in tasks.items()
        if task["status"] == "backlog"
    }

    # 按阻塞原因分组
    ready: list[str] = []
    waiting_deps: list[tuple[str, str]] = []  # (前置, 后继)
    blocked_conflicts: list[tuple[str, str]] = []  # (tid, active 对端)
    pending_clarify: list[str] = []
    unscheduled: list[str] = []

    active_set = set(active_list)
    for tid in sorted(backlog_tasks, key=tid_sort_key):
        task = backlog_tasks[tid]
        schedule = task.get("schedule_status", "")
        if schedule == "pending_clarification":
            pending_clarify.append(tid)
            continue
        if not schedule:
            unscheduled.append(tid)
            continue
        # scheduled：检查依赖
        missing_deps = [
            dep for dep in dependencies.get(tid, []) if dep not in done_set
        ]
        if missing_deps:
            for dep in sorted(missing_deps, key=tid_sort_key):
                waiting_deps.append((dep, tid))
            continue
        # 检查冲突：peer 真正占资源（active/blocked/未合 main 的 done）才阻塞；
        # peer 是 backlog 时，序号小者优先，序号大者被序号小者阻塞（强制择一）。
        blocking = []
        for peer in conflicts[tid]:
            if peer in main_done_set or peer in dropped_set:
                continue
            peer_status = tasks[peer]["status"]
            if peer_status in ("active", "blocked"):
                blocking.append(peer)
            elif peer_status == "done":
                # 未合 main 的 done：占住资源，阻塞
                blocking.append(peer)
            elif peer_status == "backlog":
                # backlog peer：序号小者优先，序号大者被阻塞
                if tid_sort_key(peer) < tid_sort_key(tid):
                    blocking.append(peer)
        if blocking:
            for peer in sorted(blocking, key=tid_sort_key):
                blocked_conflicts.append((tid, peer))
            continue
        ready.append(tid)

    # 下一批：ready 中互相冲突的择优（保留原 next-batch 选择逻辑）
    selected = []
    for tid in ready:
        if any(peer in conflicts[tid] for peer in selected):
            continue
        selected.append(tid)

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
        "pending_clarify": pending_clarify,
        "unscheduled": unscheduled,
        "selected": selected,
    }
