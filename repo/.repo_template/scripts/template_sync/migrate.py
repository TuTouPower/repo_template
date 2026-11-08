"""显式迁移入口：把旧布局一次性收敛到当前 schema。

日常 `apply` 不再自动迁移；发现旧布局时阻断并指向本入口。
每次迁移都在持久文件事务内执行：先备份，失败整体回滚，成功后校验目标数据。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from repo_task.file_transaction import FileTransaction

from . import context as ctx
from .conventions_migration import conventions_migration_status, migrate_conventions
from .entity_storage_migration import (
    entity_storage_migration_status,
    migrate_entity_storage,
)
from .hooks import migrate_retired_hooks, retired_hooks_status
from .specs_index_migration import specs_index_migration_status, specs_index_report, migrate_specs_index
from .source import assert_not_self, resolve_src
from .state import cmd_migrate as state_migrate, read_state
from .workflow_migration import force_migrate_workflow_tasks, workflow_migration_status

KINDS = (
    "legacy-state",
    "workflow-schema",
    "conventions",
    "specs-index",
    "retired-hooks",
    "entity-storage",
)


def _transactional(paths: list[str], action, verify, label: str, sync=None) -> list[str]:
    sync = sync or ctx.current_sync()
    transaction = FileTransaction(sync.consumer, ".scratch/repo_sync_rollback")
    try:
        transaction.begin(paths)
    except ValueError as error:
        raise ctx.SyncError(str(error)) from error
    try:
        reports = action(set())
        problems = verify()
        if problems:
            raise ctx.SyncError(
                f"{label} 迁移后仍有不可机械转换的数据：\n- " + "\n- ".join(problems)
            )
        transaction.applied()
        transaction.accept()
    except (OSError, ValueError, ctx.SyncError) as error:
        try:
            transaction.rollback(automatic=True)
        except Exception as rollback_error:  # noqa: BLE001 - 恢复失败必须显式报告
            raise ctx.SyncError(
                f"{label} 迁移失败（{error}）；自动回滚失败：{rollback_error}；"
                "备份保留在 .scratch/repo_sync_rollback，请人工恢复"
            ) from error
        raise ctx.SyncError(f"{label} 迁移失败，已回滚：{error}") from error
    return reports


def _resolve_source(sync) -> "Path":
    state = read_state(sync)
    if not state:
        raise ctx.SyncError("未初始化：无同步来源，无法读取模板 schema")
    src = resolve_src(state, sync=sync)
    assert_not_self(src, sync=sync)
    return src


def _migrate_workflow(src: "Path", sync) -> list[str]:
    migrations, worktrees, errors = workflow_migration_status(src, sync=sync)
    blockers = list(errors)
    if worktrees:
        blockers.extend(f"已登记 task worktree {branch} @ {root}" for root, branch in worktrees)
    if blockers:
        raise ctx.SyncError("workflow schema 迁移前须处理现场：\n- " + "\n- ".join(blockers))
    if not migrations:
        return []
    template_dir = src / ".repo_template/docs/task_template"
    return _transactional(
        ["docs/tasks", "docs/archive/tasks"],
        lambda changed: force_migrate_workflow_tasks(
            changed, template_template_dir=template_dir, sync=sync
        ),
        lambda: [path for path in workflow_migration_status(src, sync=sync)[0]],
        "workflow schema",
        sync=sync,
    )


def _migrate_conventions(sync) -> list[str]:
    if not conventions_migration_status(sync=sync):
        return []
    return _transactional(
        ["docs/blueprint/conventions.md"],
        migrate_conventions,
        lambda: conventions_migration_status(sync=sync),
        "conventions 原子写约定",
        sync=sync,
    )


def _migrate_specs_index(sync) -> list[str]:
    if not specs_index_migration_status(sync=sync):
        return []
    return _transactional(
        ["docs/specs_index.md"],
        migrate_specs_index,
        lambda: specs_index_migration_status(sync=sync) + specs_index_report(sync=sync),
        "specs_index schema",
        sync=sync,
    )


def _migrate_retired_hooks(sync) -> list[str]:
    if not retired_hooks_status(sync=sync):
        return []
    return _transactional(
        [".claude/hooks/merge_guard.py", ".claude/settings.json"],
        lambda _changed: migrate_retired_hooks(sync=sync),
        lambda: retired_hooks_status(sync=sync),
        "退役 hooks 清理",
        sync=sync,
    )


_STEPS = {
    "workflow-schema": _migrate_workflow,
    "conventions": _migrate_conventions,
    "specs-index": _migrate_specs_index,
    "retired-hooks": _migrate_retired_hooks,
    "entity-storage": lambda sync, write=False: migrate_entity_storage(sync=sync, write=write),
}


def cmd_migrate(args: argparse.Namespace) -> int:
    """执行显式迁移；`all` 按固定顺序逐项执行，宁可停下也不猜测数据。"""
    sync = getattr(args, "sync", None) or ctx.current_sync()
    if args.kind == "legacy-state":
        return state_migrate(argparse.Namespace(kind="legacy-state", sync=sync))
    kinds = KINDS[1:] if args.kind == "all" else (args.kind,)
    write = getattr(args, "write", False)
    for kind in kinds:
        step = _STEPS.get(kind)
        if step is None:
            raise ctx.SyncError(f"未知迁移目标：{kind}")
        if kind == "workflow-schema":
            reports = step(_resolve_source(sync), sync)
        elif kind == "entity-storage":
            reports = step(sync, write=write)
        else:
            reports = step(sync)
        if not reports:
            print(f"{kind}: 已是当前格式，无需迁移")
            continue
        mode_str = "落盘" if write or kind != "entity-storage" else "dry-run 拟定"
        print(f"{kind}: {mode_str}完成 {len(reports)} 项")
        for line in reports:
            print(f"  {line}")
    print("迁移完成；校验并提交迁移结果")
    return 0
