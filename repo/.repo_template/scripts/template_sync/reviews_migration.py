"""一次性 review 裁剪：完整不可变迁移计划，只保留最近 5 个，超出的一律删除。

单一真相源是 _plan_reviews 构造的 frozen ReviewPlan；status、dry-run、write
与事务验收全部从该计划派生。archive 中属于 keep 的目录一律迁回 docs/reviews，
超额目录（含被引用、未跟踪、_meta/）一律 rmtree，不设保留子集。
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import context as ctx

LIVE = "docs/reviews"
ARCHIVE = "docs/archive/reviews"
DATE_RE = re.compile(r"^review_(\d{8})")


@dataclass(frozen=True)
class ReviewPlan:
    """一次 review 迁移的完整不可变计划；构造阶段完成全部校验与冲突检查。"""

    keep: tuple[Path, ...]
    moves: tuple[tuple[Path, Path], ...]
    deletions: tuple[Path, ...]
    findings: tuple[str, ...]
    reports: tuple[str, ...]


def _dirs(root: Path, rel: str) -> list[Path]:
    base = root / rel
    return [path for path in base.glob("review_*") if path.is_dir()] if base.is_dir() else []


def _validate_dates(sync: ctx.SyncContext, paths: list[Path]) -> None:
    invalid = sorted(sync.rel(path) for path in paths if not DATE_RE.match(path.name))
    if invalid:
        raise ctx.SyncError(
            "review 目录名缺 YYYYMMDD，停止迁移：\n- " + "\n- ".join(invalid)
        )


def _plan_reviews(sync: ctx.SyncContext) -> ReviewPlan:
    live_dir = sync.consumer / LIVE
    all_dirs = _dirs(sync.consumer, LIVE) + _dirs(sync.consumer, ARCHIVE)
    _validate_dates(sync, all_dirs)
    ordered = sorted(all_dirs, key=lambda path: (path.name[:15], path.name), reverse=True)
    keep = tuple(ordered[:5])
    deletions = tuple(ordered[5:])

    moves: list[tuple[Path, Path]] = []
    for path in keep:
        if path.parent == live_dir:
            continue
        target = live_dir / path.name
        if target.exists():
            raise ctx.SyncError(f"review 同名冲突，停止迁移：{path} -> {target}")
        moves.append((path, target))

    move_lines = tuple(f"{sync.rel(src)} -> {LIVE}/{src.name}" for src, _ in moves)
    findings = move_lines + tuple(sync.rel(path) for path in deletions)
    reports = move_lines + tuple(f"删除旧 review：{sync.rel(path)}" for path in deletions)
    return ReviewPlan(
        keep=keep,
        moves=tuple(moves),
        deletions=deletions,
        findings=findings,
        reports=reports,
    )


def reviews_migration_status(sync: ctx.SyncContext) -> list[str]:
    try:
        plan = _plan_reviews(sync)
    except ctx.SyncError as error:
        return [str(error)]
    return list(plan.findings)


def migrate_reviews(sync: ctx.SyncContext, *, write: bool = False) -> list[str]:
    plan = _plan_reviews(sync)
    reports = list(plan.reports)
    if not write:
        return reports
    for path, target in plan.moves:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
    for path in plan.deletions:
        shutil.rmtree(path)
    archive = sync.consumer / ARCHIVE
    if archive.is_dir() and not any(archive.iterdir()):
        archive.rmdir()
    return reports
