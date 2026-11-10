"""一次性 review 裁剪：合并 archive reviews，只保留最近 5 个。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from repo_task.specs import SpecError

from . import context as ctx

LIVE = "docs/reviews"
ARCHIVE = "docs/archive/reviews"
DATE_RE = re.compile(r"^review_(\d{8})")


def _dirs(root: Path, rel: str) -> list[Path]:
    base = root / rel
    return [path for path in base.glob("review_*") if path.is_dir()] if base.is_dir() else []


def _date(path: Path) -> str:
    match = DATE_RE.match(path.name)
    if not match:
        raise ctx.SyncError(f"review 目录名缺 YYYYMMDD：{path}")
    return match.group(1)


def _referenced(root: Path, directory: str) -> list[str]:
    refs = []
    for rel in ("docs/tasks", "docs/blueprint", "docs/handoff.md"):
        base = root / rel
        files = [base] if base.is_file() else (base.rglob("*") if base.is_dir() else [])
        for path in files:
            if path.is_file():
                try:
                    if directory in path.read_text(encoding="utf-8"):
                        refs.append(path.relative_to(root).as_posix())
                except (OSError, UnicodeError):
                    continue
    return refs


def _tracked(root: Path, rel: str) -> bool:
    import subprocess

    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--", rel],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def reviews_migration_status(sync: ctx.SyncContext) -> list[str]:
    live = _dirs(sync.consumer, LIVE)
    archived = _dirs(sync.consumer, ARCHIVE)
    if not archived and len(live) <= 5:
        return []
    findings = []
    for path in live + archived:
        findings.append(path.relative_to(sync.consumer).as_posix())
    return findings


def migrate_reviews(sync: ctx.SyncContext, *, write: bool = False) -> list[str]:
    live = _dirs(sync.consumer, LIVE)
    archived = _dirs(sync.consumer, ARCHIVE)
    all_dirs = live + archived
    for path in all_dirs:
        _date(path)
    ordered = sorted(all_dirs, key=lambda path: (path.name[:15], path.name), reverse=True)
    keep = ordered[:5]
    reports = []
    for path in keep:
        target = sync.consumer / LIVE / path.name
        if path.parent == sync.consumer / LIVE:
            continue
        if target.exists():
            raise ctx.SyncError(f"review 同名冲突，停止迁移：{path} -> {target}")
        reports.append(f"{path.relative_to(sync.consumer).as_posix()} -> {LIVE}/{path.name}")
        if write:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
    for path in ordered[5:]:
        rel = path.relative_to(sync.consumer).as_posix()
        refs = _referenced(sync.consumer, rel)
        if refs:
            reports.append(f"保留被引用 review：{rel}（{', '.join(refs)}）")
            continue
        if not _tracked(sync.consumer, rel):
            reports.append(f"跳过未跟踪 review：{rel}")
            continue
        reports.append(f"删除旧 review：{rel}")
        if write:
            shutil.rmtree(path)
    if write:
        archive = sync.consumer / ARCHIVE
        if archive.is_dir() and not any(archive.iterdir()):
            archive.rmdir()
    return reports
