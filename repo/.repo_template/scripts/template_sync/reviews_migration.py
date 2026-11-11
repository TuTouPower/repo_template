"""一次性 review 裁剪：合并 archive reviews，只保留最近 5 个，超出的一律删除。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import context as ctx

LIVE = "docs/reviews"
ARCHIVE = "docs/archive/reviews"
DATE_RE = re.compile(r"^review_(\d{8})")


def _dirs(root: Path, rel: str) -> list[Path]:
    base = root / rel
    return [path for path in base.glob("review_*") if path.is_dir()] if base.is_dir() else []


def _validate_dates(sync: ctx.SyncContext, paths: list[Path]) -> None:
    invalid = sorted(
        path.relative_to(sync.consumer).as_posix()
        for path in paths
        if not DATE_RE.match(path.name)
    )
    if invalid:
        raise ctx.SyncError(
            "review 目录名缺 YYYYMMDD，停止迁移：\n- " + "\n- ".join(invalid)
        )


def _ordered(sync: ctx.SyncContext) -> list[Path]:
    all_dirs = _dirs(sync.consumer, LIVE) + _dirs(sync.consumer, ARCHIVE)
    return sorted(all_dirs, key=lambda path: (path.name[:15], path.name), reverse=True)


def _removable(sync: ctx.SyncContext) -> list[Path]:
    return _ordered(sync)[5:]


def reviews_migration_status(sync: ctx.SyncContext) -> list[str]:
    return [
        path.relative_to(sync.consumer).as_posix()
        for path in _removable(sync)
    ]


def migrate_reviews(sync: ctx.SyncContext, *, write: bool = False) -> list[str]:
    live_dir = sync.consumer / LIVE
    ordered = _ordered(sync)
    _validate_dates(sync, ordered)
    keep = ordered[:5]
    removable = _removable(sync)

    moves: list[tuple[Path, Path]] = []
    for path in keep:
        target = live_dir / path.name
        if path.parent == live_dir:
            continue
        if target.exists():
            raise ctx.SyncError(f"review 同名冲突，停止迁移：{path} -> {target}")
        moves.append((path, target))

    reports = [
        f"{path.relative_to(sync.consumer).as_posix()} -> {LIVE}/{path.name}"
        for path, _target in moves
    ]
    reports.extend(
        f"删除旧 review：{path.relative_to(sync.consumer).as_posix()}"
        for path in removable
    )

    if write:
        for path, target in moves:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
        for path in removable:
            shutil.rmtree(path)
        archive = sync.consumer / ARCHIVE
        if archive.is_dir() and not any(archive.iterdir()):
            archive.rmdir()
    return reports
