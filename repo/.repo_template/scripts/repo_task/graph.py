"""Atomic graph rewrites for backlog consolidation."""

import sys
from argparse import Namespace
from pathlib import Path

from . import context as ctx
from .documents import dump_tid_list, parse_front_matter, parse_tid_list, write_front_matter_many
from .git_ops import require_primary_worktree
from .locks import CONTROL_LOCK_NAME, git_common_lock
from .scheduling import _dependency_cycle, compute_schedule
from .store import load_task, scan_tasks, task_effective_state
from .file_transaction import FileTransaction


def cmd_merge_backlog(args):
    repo = args.repo
    require_primary_worktree(repo=repo)
    sources = set(parse_tid_list(args.sources, field="--sources", allow_empty=False))
    group = sources | {args.tid}
    if len(group) < 2:
        sys.exit("合并至少需要两个不同 backlog 成员")
    with git_common_lock(repo.root, CONTROL_LOCK_NAME):
        schedule = compute_schedule(repo=repo)
        main_tasks = {task["tid"]: task for task in scan_tasks(repo=repo)}
        tasks = schedule["tasks"]
        for tid in group:
            if tid not in main_tasks or tasks[tid]["status"] != "backlog" or task_effective_state(tid, main_tasks[tid], repo=repo):
                sys.exit(f"{tid} 必须是尚未执行的 main backlog")
        from .ledger import ledger_read
        if any(event.get("tid") in group and event.get("attempt") for event in ledger_read(repo=repo)):
            sys.exit("来源或目标已有执行历史，不可作为 backlog 合并成员")
        dependencies = {tid: set(deps) for tid, deps in schedule["dependencies"].items()}
        conflicts = {tid: set(peers) for tid, peers in schedule["conflicts"].items()}
        affected = group | {tid for tid in dependencies if dependencies[tid] & group or conflicts[tid] & group}
        dependencies[args.tid] = set().union(*(dependencies[tid] for tid in group)) - group
        conflicts[args.tid] = set().union(*(conflicts[tid] for tid in group)) - group
        for tid in tasks:
            if tasks[tid]["status"] in ctx.TERMINAL_STATUSES:
                continue
            if tid in group - {args.tid}:
                dependencies[tid] = set()
                conflicts[tid] = set()
            elif tid != args.tid:
                if dependencies[tid] & group:
                    dependencies[tid] = (dependencies[tid] - group) | {args.tid}
                if conflicts[tid] & group:
                    conflicts[tid] = (conflicts[tid] - group) | {args.tid}
        cycle = _dependency_cycle(dependencies)
        if cycle:
            sys.exit(f"合并会形成依赖环：{' -> '.join(cycle)}；未写入")
        updates = []
        for tid, task in tasks.items():
            if tid not in affected or task["status"] in ctx.TERMINAL_STATUSES:
                continue
            deps = dump_tid_list(sorted(dependencies[tid]))
            peers = dump_tid_list(sorted(conflicts[tid]))
            if deps == task.get("depends_on", "") and peers == task.get("conflicts_with", ""):
                continue
            if tid not in main_tasks or task["status"] != "backlog" or task_effective_state(tid, main_tasks[tid], repo=repo):
                sys.exit(f"合并需要改写不可编辑的 {tid}；未写入")
            _, path, fm, body = load_task(tid, repo=repo)
            fm.update(depends_on=deps, conflicts_with=peers)
            updates.append((path, fm, body))
        transaction = FileTransaction(repo.root, ".scratch/task_graph_transaction")
        retired = sorted(group - {args.tid})
        retired_paths = [f"{main_tasks[tid]['dir']}/{ctx.PROGRESS_FILE}" for tid in retired]
        update_paths = [str(path.relative_to(repo.root)) for path, _, _ in updates]
        paths = sorted(set(retired_paths + update_paths))
        try:
            transaction.begin(paths)
        except ValueError as error:
            raise ctx.TaskDataError(str(error)) from error
        try:
            write_front_matter_many(updates)
            from .lifecycle import _close_task
            for tid in retired:
                _close_task(
                    Namespace(tid=tid), "dropped", f"merged into {args.tid}", repo=repo
                )
            for path, fm, _body in updates:
                actual, _ = parse_front_matter(path)
                if (
                    actual.get("depends_on", "") != fm["depends_on"]
                    or actual.get("conflicts_with", "") != fm["conflicts_with"]
                ):
                    raise ctx.TaskDataError(
                        f"合并后校验失败：{repo.rel(path)} 依赖/冲突与预期不符"
                    )
            for tid in retired:
                progress_file = repo.root / main_tasks[tid]["dir"] / ctx.PROGRESS_FILE
                if not progress_file.is_file():
                    raise ctx.TaskDataError(f"合并后校验失败：{main_tasks[tid]['dir']}/{ctx.PROGRESS_FILE} 不存在")
                actual_fm, _ = parse_front_matter(progress_file)
                if actual_fm.get("status") != "dropped":
                    raise ctx.TaskDataError(
                        f"合并后校验失败：{tid} status 应为 dropped（实际为 {actual_fm.get('status')}）"
                    )
            transaction.applied()
            transaction.accept()
        except (OSError, ctx.TaskDataError, SystemExit) as error:
            transaction.rollback(automatic=True)
            raise ctx.TaskDataError(f"backlog 合并失败，已恢复图与来源元数据：{error}") from error
    print(f"graph merged into {args.tid}: {', '.join(sorted(group))}；依赖与冲突已改指，来源已标记 dropped")


def cmd_graph_transaction(args):
    repo = args.repo
    require_primary_worktree(repo=repo)
    with git_common_lock(repo.root, CONTROL_LOCK_NAME):
        transaction = FileTransaction(repo.root, ".scratch/task_graph_transaction")
        if args.action == "status":
            import json
            print(json.dumps(transaction.read(), ensure_ascii=False, indent=2))
        else:
            try:
                transaction.rollback(confirm_restore=args.confirm_restore)
            except ValueError as error:
                raise ctx.TaskDataError(str(error)) from error
