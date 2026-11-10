"""一次性 review 裁剪：合并 archive reviews，只保留最近 5 个。"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from repo_task.specs import SpecError

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


def _git_paths(root: Path, *args: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise ctx.SyncError(
            f"git ls-files {' '.join(args)} 失败：{result.stderr.strip()}"
        )
    return [item for item in result.stdout.split("\0") if item]


def _tracked(root: Path, rel: str) -> bool:
    return bool(_git_paths(root, "--", rel))


def _untracked_paths(root: Path, rel: str) -> list[str]:
    paths = set(_git_paths(root, "--others", "--exclude-standard", "--", rel))
    paths.update(_git_paths(root, "--others", "--ignored", "--exclude-standard", "--", rel))
    return sorted(paths)


def _meta_report(root: Path, path: Path) -> list[str]:
    """返回待删除的 _meta 过程目录报告（目录存在才有报告）。"""
    rel = path.relative_to(root).as_posix()
    return [f"删除 _meta 过程文件：{rel}/_meta"] if (path / "_meta").is_dir() else []


def _deletion_content(root: Path, path: Path) -> tuple[list[str], list[str]]:
    """返回 (将删除的 _meta 报告, 非 _meta 未跟踪文件)；后者写入时阻断。"""
    rel = path.relative_to(root).as_posix()
    meta_rel = f"{rel}/_meta"
    untracked = [
        item for item in _untracked_paths(root, rel)
        if not item.startswith(meta_rel + "/")
    ]
    return _meta_report(root, path), untracked


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
    live_dir = sync.consumer / LIVE
    all_dirs = _dirs(sync.consumer, LIVE) + _dirs(sync.consumer, ARCHIVE)
    _validate_dates(sync, all_dirs)
    ordered = sorted(all_dirs, key=lambda path: (path.name[:15], path.name), reverse=True)
    keep = ordered[:5]

    moves: list[tuple[Path, Path]] = []
    for path in keep:
        target = live_dir / path.name
        if path.parent == live_dir:
            continue
        if target.exists():
            raise ctx.SyncError(f"review 同名冲突，停止迁移：{path} -> {target}")
        moves.append((path, target))

    archive_dir = sync.consumer / ARCHIVE
    deletions: list[tuple[Path, bool, list[str], list[str], str]] = []
    untracked_blockers: list[str] = []
    for path in ordered[5:]:
        rel = path.relative_to(sync.consumer).as_posix()
        refs = _referenced(sync.consumer, rel)
        # archive 内被保留（引用/未跟踪）的 review 仍可能带 _meta 过程目录；
        # 归档区不再承载过程文件，写入时清掉并在 dry-run 报告，避免成为未跟踪噪音。
        retained_meta = _meta_report(sync.consumer, path) if path.parent == archive_dir else []
        if refs:
            deletions.append(
                (path, False, retained_meta, [], f"保留被引用 review：{rel}（{', '.join(refs)}）")
            )
            continue
        if not _tracked(sync.consumer, rel):
            deletions.append((path, False, retained_meta, [], f"跳过未跟踪 review：{rel}"))
            continue
        meta_reports, untracked = _deletion_content(sync.consumer, path)
        untracked_blockers.extend(untracked)
        deletions.append((path, True, meta_reports, untracked, f"删除旧 review：{rel}"))

    reports = [
        f"{path.relative_to(sync.consumer).as_posix()} -> {LIVE}/{path.name}"
        for path, _target in moves
    ]
    for _path, _remove, meta_reports, untracked, label in deletions:
        reports.append(label)
        reports.extend(meta_reports)
        reports.extend(f"未跟踪文件（写入将停止）：{item}" for item in untracked)

    if write and untracked_blockers:
        raise ctx.SyncError(
            "review 删除前发现未跟踪文件，停止迁移（未删除任何 review 目录）：\n- "
            + "\n- ".join(untracked_blockers)
        )
    if write:
        for path, target in moves:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
        for path, remove, _meta_reports, _untracked, _label in deletions:
            if remove:
                shutil.rmtree(path)
            elif _meta_reports and (path / "_meta").is_dir():
                shutil.rmtree(path / "_meta")
        archive = sync.consumer / ARCHIVE
        if archive.is_dir() and not any(archive.iterdir()):
            archive.rmdir()
    return reports
