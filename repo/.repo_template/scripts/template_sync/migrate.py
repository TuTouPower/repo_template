"""显式迁移入口：把旧布局一次性收敛到当前 schema。

日常 `apply` 不再自动迁移；发现旧布局时阻断并指向本入口。
每次迁移都在持久文件事务内执行：先备份，失败整体回滚，成功后校验目标数据。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from repo_task.file_transaction import FileTransaction

from . import context as ctx
from .common import consumer_locked
from .conventions_migration import conventions_migration_status, migrate_conventions
from .entity_storage_migration import (
    _check_clean_working_copy,
    entity_storage_migration_status,
    migrate_entity_storage,
)
from .hooks import migrate_retired_hooks, retired_hooks_status
from .specs_index_migration import specs_index_migration_status, specs_index_report, migrate_specs_index
from .source import assert_not_self, resolve_src
from .state import migrate_legacy_state, read_state
from .workflow_migration import force_migrate_workflow_tasks, workflow_migration_status

KINDS = (
    "legacy-state",
    "workflow-schema",
    "conventions",
    "specs-index",
    "retired-hooks",
    "entity-storage",
)


def migration_findings(kind: str, sync: ctx.SyncContext, src: Path | None = None) -> list[str]:
    """单一来源：某类迁移的待处理项；status / plan / apply / migrate 共用。

    空列表表示已是当前格式。消除「plan 显示一致但 apply 阻断」的分叉。
    """
    if kind == "entity-storage":
        return entity_storage_migration_status(sync=sync)
    if kind == "legacy-state":
        legacy = sync.legacy_state_path()
        return [f"旧布局 state {sync.rel(legacy)} 待迁移"] if legacy.exists() else []
    if kind == "workflow-schema":
        if src is None:
            return []
        migrations, worktrees, errors = workflow_migration_status(src, sync=sync)
        findings = list(errors)
        findings.extend(f"已登记 task worktree {branch} @ {root}" for root, branch in worktrees)
        findings.extend(migrations)
        return findings
    if kind == "conventions":
        return conventions_migration_status(sync=sync)
    if kind == "specs-index":
        return specs_index_migration_status(sync=sync)
    if kind == "retired-hooks":
        return retired_hooks_status(sync=sync)
    raise ctx.SyncError(f"未知迁移目标：{kind}")


def migrate_command(src: Path, sync: ctx.SyncContext, kind: str = "all") -> str:
    """基于固定来源 product 与消费仓身份的可直接执行迁移命令。"""
    script = src / ".repo_template/scripts/repo_sync.py"
    return f"python3 {script} --consumer {sync.consumer} migrate {kind} --write"


def _transactional(paths: list[str], action, verify, label: str, *, sync: ctx.SyncContext) -> list[str]:
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
    except Exception as error:
        # 任意异常（含 AttributeError / IdScanError 等）都必须回滚，绝不让 journal
        # 停留在 applying。KeyboardInterrupt / SystemExit 属 BaseException，不吞。
        try:
            transaction.rollback(automatic=True)
        except Exception as rollback_error:  # noqa: BLE001 - 恢复失败必须显式报告
            raise ctx.SyncError(
                f"{label} 迁移失败（{error}）；自动回滚失败：{rollback_error}；"
                "备份保留在 .scratch/repo_sync_rollback，请人工恢复"
            ) from error
        raise ctx.SyncError(f"{label} 迁移失败，已回滚：{error}") from error
    return reports


def _resolve_source(sync: ctx.SyncContext) -> Path:
    state = read_state(sync)
    if not state:
        raise ctx.SyncError("未初始化：无同步来源，无法读取模板 schema")
    src = resolve_src(state, sync=sync)
    assert_not_self(src, sync=sync)
    return src


def _migrate_workflow(sync: ctx.SyncContext, write: bool = False) -> list[str]:
    src = _resolve_source(sync)
    migrations, worktrees, errors = workflow_migration_status(src, sync=sync)
    blockers = list(errors)
    if worktrees:
        blockers.extend(f"已登记 task worktree {branch} @ {root}" for root, branch in worktrees)
    if blockers:
        raise ctx.SyncError("workflow schema 迁移前须处理现场：\n- " + "\n- ".join(blockers))
    if not migrations:
        return []
    if not write:
        return [f"{path} 拟强制迁移到当前 workflow schema" for path in migrations]
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


def _migrate_conventions(sync: ctx.SyncContext, write: bool = False) -> list[str]:
    status = conventions_migration_status(sync=sync)
    if not status:
        return []
    if not write:
        return [
            f"{path} 拟自动迁移原子写约定为 from repo_task.documents import atomic_write_text（保留消费仓自定义内容）"
            for path in status
        ]
    return _transactional(
        ["docs/blueprint/conventions.md"],
        lambda changed: migrate_conventions(changed, sync=sync),
        lambda: conventions_migration_status(sync=sync),
        "conventions 原子写约定",
        sync=sync,
    )


def _migrate_specs_index(sync: ctx.SyncContext, write: bool = False) -> list[str]:
    status = specs_index_migration_status(sync=sync)
    if not status:
        return []
    if not write:
        return [
            f"{path} 拟迁移 specs_index 旧说明句/无数据 supersedes 列（保留其余内容）"
            for path in status
        ]
    return _transactional(
        ["docs/specs_index.md"],
        lambda changed: migrate_specs_index(changed, sync=sync),
        lambda: specs_index_migration_status(sync=sync) + specs_index_report(sync=sync),
        "specs_index schema",
        sync=sync,
    )


def _migrate_retired_hooks(sync: ctx.SyncContext, write: bool = False) -> list[str]:
    status = retired_hooks_status(sync=sync)
    if not status:
        return []
    if not write:
        return [f"{path} 拟退役并移除/清理" for path in status]
    # 事务根必须是 .claude/hooks 目录（可快照子软链），不能用软链文件作资产根：
    # FileTransaction.begin 明确拒绝软链资产根。
    return _transactional(
        [".claude/hooks", ".claude/settings.json"],
        lambda _changed: migrate_retired_hooks(sync=sync),
        lambda: retired_hooks_status(sync=sync),
        "退役 hooks 清理",
        sync=sync,
    )


_STEPS = {
    "legacy-state": lambda sync, write=False, require_clean=True: migrate_legacy_state(sync=sync, write=write),
    "workflow-schema": lambda sync, write=False, require_clean=True: _migrate_workflow(sync, write=write),
    "conventions": lambda sync, write=False, require_clean=True: _migrate_conventions(sync, write=write),
    "specs-index": lambda sync, write=False, require_clean=True: _migrate_specs_index(sync, write=write),
    "retired-hooks": lambda sync, write=False, require_clean=True: _migrate_retired_hooks(sync, write=write),
    "entity-storage": lambda sync, write=False, require_clean=True: migrate_entity_storage(
        sync=sync, write=write, require_clean=require_clean
    ),
}


@consumer_locked
def cmd_migrate(args: argparse.Namespace) -> int:
    """执行显式迁移；`all` 按固定顺序逐项执行，宁可停下也不猜测数据。

    逐项事务、已完成步骤保留的语义不变；只在 all 入口统一做一次干净检查，
    避免前序步骤落盘后 entity 步再查干净造成顺序死锁。
    """
    sync: ctx.SyncContext = args.sync
    kinds = KINDS if args.kind == "all" else (args.kind,)
    write = getattr(args, "write", False)
    if write and args.kind == "all":
        _check_clean_working_copy(sync.consumer)
    for kind in kinds:
        step = _STEPS.get(kind)
        if step is None:
            raise ctx.SyncError(f"未知迁移目标：{kind}")
        reports = step(sync, write=write, require_clean=(args.kind != "all"))
        if not reports:
            print(f"{kind}: 已是当前格式，无需迁移")
            continue
        mode_str = "落盘" if write else "dry-run 拟定"
        print(f"{kind}: {mode_str}完成 {len(reports)} 项")
        for line in reports:
            print(f"  {line}")
    if write:
        print("迁移完成；校验并提交迁移结果")
    else:
        print("迁移完成（dry-run，未写盘；加 --write 执行落盘）")
    return 0
