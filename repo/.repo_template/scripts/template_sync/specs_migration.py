"""一次性迁移：specs_index.md + archive specs -> docs/specs front matter。"""

from __future__ import annotations

import shutil
from pathlib import Path

from repo_task.documents import atomic_write_text
from repo_task.specs import SpecError, SPEC_SLUG_RE, load_spec, load_specs, render_spec_text
from .specs_index_migration import parse_specs_index

from . import context as ctx

INDEX = "docs/specs_index.md"
ACTIVE_DIR = "docs/specs"
ARCHIVE_DIR = "docs/archive/specs"


def _index_map(sync: ctx.SyncContext) -> dict[str, list[str]]:
    path = sync.consumer / INDEX
    if not path.is_file():
        return {}
    try:
        return parse_specs_index(sync.consumer)
    except ValueError as error:
        raise ctx.SyncError(str(error)) from error


def _has_front_matter(path: Path) -> bool:
    return path.read_text(encoding="utf-8").startswith("---")


def specs_migration_status(sync: ctx.SyncContext) -> list[str]:
    findings = []
    if (sync.consumer / INDEX).is_file():
        findings.append(f"{INDEX} 待删除并迁移到 front matter")
    archive = sync.consumer / ARCHIVE_DIR
    if archive.is_dir():
        findings.extend(f"{path.relative_to(sync.consumer).as_posix()} 待迁回 docs/specs" for path in archive.glob("*.md"))
    for path in (sync.consumer / ACTIVE_DIR).glob("*.md"):
        if not _has_front_matter(path):
            findings.append(f"{path.relative_to(sync.consumer).as_posix()} 待补 front matter")
    return findings


def _validate_before_write(sync: ctx.SyncContext, index: dict[str, list[str]]) -> None:
    active = sync.consumer / ACTIVE_DIR
    for path in sorted(active.glob("*.md")):
        if not SPEC_SLUG_RE.fullmatch(path.stem):
            raise ctx.SyncError(f"spec slug 非法，停止迁移：{path.name}")
        if _has_front_matter(path):
            try:
                current = load_spec(path, root=sync.consumer)
            except SpecError as error:
                raise ctx.SyncError(str(error)) from error
            tasks = index.get(path.stem, ["baseline"])
            if current["tasks"] != tasks:
                raise ctx.SyncError(f"{path.name} front matter tasks={current['tasks']} 与索引 {tasks} 冲突")
    archive = sync.consumer / ARCHIVE_DIR
    for path in sorted(archive.glob("*.md")) if archive.is_dir() else []:
        if not SPEC_SLUG_RE.fullmatch(path.stem):
            raise ctx.SyncError(f"spec slug 非法，停止迁移：{path.name}")
        target = active / path.name
        if target.exists():
            raise ctx.SyncError(f"同名 spec 冲突，停止迁移：{path} -> {target}")


def migrate_specs_layout(sync: ctx.SyncContext, *, write: bool = False) -> list[str]:
    index = _index_map(sync)
    if write:
        _validate_before_write(sync, index)
    reports: list[str] = []
    moves: list[tuple[Path, Path]] = []
    archive = sync.consumer / ARCHIVE_DIR
    for path in sorted((sync.consumer / ACTIVE_DIR).glob("*.md")):
        slug = path.stem
        if slug in index:
            tasks, status = index[slug], "active"
        else:
            tasks, status = ["baseline"], "active"
            reports.append(f"{path.name} 无索引行，使用 tasks: [baseline]")
        if _has_front_matter(path):
            try:
                current = load_spec(path, root=sync.consumer)
            except SpecError as error:
                raise ctx.SyncError(str(error)) from error
            if current["tasks"] != tasks:
                raise ctx.SyncError(f"{path.name} front matter tasks={current['tasks']} 与索引 {tasks} 冲突")
            body = current["body"]
        else:
            text = path.read_text(encoding="utf-8")
            body = text
        if write:
            atomic_write_text(path, render_spec_text(slug, status, tasks, body))
        reports.append(f"{path.relative_to(sync.consumer).as_posix()} -> front matter")
    if archive.is_dir():
        for path in sorted(archive.glob("*.md")):
            target = sync.consumer / ACTIVE_DIR / path.name
            if target.exists():
                raise ctx.SyncError(f"同名 spec 冲突，停止迁移：{path} -> {target}")
            moves.append((path, target))
            tasks = index.get(path.stem, ["baseline"])
            reports.append(f"{path.relative_to(sync.consumer).as_posix()} -> docs/specs/{path.name}（retired）")
            if write:
                body = path.read_text(encoding="utf-8")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(target))
                atomic_write_text(target, render_spec_text(path.stem, "retired", tasks, body))
    if not write:
        return reports
    index_path = sync.consumer / INDEX
    if index_path.exists():
        index_path.unlink()
    if archive.is_dir() and not any(archive.iterdir()):
        archive.rmdir()
    for spec in load_specs(sync.consumer):
        if spec["slug"] != spec["path"].stem:
            raise ctx.SyncError(f"迁移后 slug 与文件名不一致：{spec['path']}")
    return reports
