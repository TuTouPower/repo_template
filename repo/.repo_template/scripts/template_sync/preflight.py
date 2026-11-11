"""status/plan/apply 共用只读迁移与数据预检。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from repo_task.specs import validate_specs

from . import context as ctx
from .migrations import MIGRATIONS
from .state_root_migration import _registered_worktrees


@dataclass(frozen=True)
class SyncPreflight:
    migrations: tuple[tuple[str, tuple[str, ...]], ...]
    error: str | None = None


def sync_preflight(src: Path, sync: ctx.SyncContext) -> SyncPreflight:
    migrations = []
    blockers = []
    try:
        worktrees = _registered_worktrees(sync)
    except ctx.SyncError as error:
        blockers.append(f"worktree: {error}")
    else:
        if worktrees:
            blockers.append("仍有已登记 worktree；先收尾：" + "、".join(worktrees))
    for migration in MIGRATIONS:
        try:
            findings = migration.findings(sync, src)
        except (ctx.SyncError, ctx.TaskDataError, OSError, ValueError) as error:
            blockers.append(f"{migration.kind}: {error}")
            continue
        # entity-storage 会生成 runtime 审计日志，必须同轮迁入 common。
        if migration.kind == "state-root" and (sync.consumer / "docs/archive/tasks_audit.log").is_file():
            findings.append("entity-storage 产生的审计日志待迁入 common 状态目录")
            from repo_task.context import common_state_dir
            if (common_state_dir(sync.consumer) / "tasks_audit.log").exists():
                blockers.append("state-root: common tasks_audit.log 已存在；请先解决审计日志目标冲突")
        if findings:
            migrations.append((migration.kind, tuple(findings)))
            blockers.extend(f"{migration.kind}: {item}" for item in migration.blockers(sync, src))
    # specs 迁移计划负责旧布局数据验证；当前布局必须直接通过正式契约。
    if not any(kind == "specs-layout" for kind, _ in migrations):
        blockers.extend("docs/specs front matter 契约违规：" + item for item in validate_specs(sync.consumer))
    if sync.legacy_state_path().exists() and (sync.consumer / ".repo_template/sync_state.json").exists():
        blockers.append("legacy-state / state-root: 两份旧 state 并存；请先核对并合并来源与 user_prompts，保留一份后再同步")
    if blockers:
        return SyncPreflight(tuple(migrations), "数据阻断项（尚未写盘）：\n- " + "\n- ".join(blockers)
                             + "\n修复方案：核对并修复以上数据，单独批准并提交修复后，再运行 plan / apply。")
    return SyncPreflight(tuple(migrations))
