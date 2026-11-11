"""同步入口共用预检：先收敛布局，再校验当前 spec 契约。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from repo_task.specs import validate_specs

from . import context as ctx
from .migrate import KINDS, migrate_command, migration_findings


@dataclass(frozen=True)
class SyncPreflight:
    migrations: tuple[tuple[str, tuple[str, ...]], ...]
    error: str | None = None


def sync_preflight(src: Path, sync: ctx.SyncContext) -> SyncPreflight:
    """status/plan/apply 的同一只读结论；有迁移待办时不检查新布局契约。"""
    migrations = tuple(
        (kind, tuple(findings))
        for kind in KINDS
        if (findings := migration_findings(kind, sync, src=src))
    )
    if migrations:
        entity_only = len(migrations) == 1 and migrations[0][0] == "entity-storage"
        label = "旧实体存储布局" if entity_only else "旧布局"
        kind = "entity-storage" if entity_only else "all"
        details = [
            f"{migration_kind} 旧布局：{item}"
            for migration_kind, findings in migrations for item in findings
        ]
        error = (
            f"检测到{label}；apply 不再自动迁移。先运行 "
            f"`{migrate_command(src, sync, kind)}`：\n- " + "\n- ".join(details)
        )
        return SyncPreflight(migrations, error)
    problems = validate_specs(sync.consumer)
    if problems:
        return SyncPreflight((), "docs/specs front matter 契约违规（同步中止，尚未写盘）：\n- "
                             + "\n- ".join(problems))
    return SyncPreflight(())
