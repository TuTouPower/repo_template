"""退役 .gitignore 托管规则的回归：迁移后不留 archive 残留。

覆盖两条规则：
- ``docs/archive/tasks_index.json``：entity-storage 迁移显式删除并报告；
- ``docs/archive/reviews/review_*/_meta/``：reviews 迁移只清理被引用且引用未落入
  ``_meta`` 的 archive review 过程目录；未跟踪 review 整体跳过，不删除任何内容；
  引用落入 ``_meta`` 时停止并报告。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS = FACTORY_ROOT / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS))

from template_sync import context as ctx
from template_sync.entity_storage_migration import (
    entity_storage_migration_status,
    migrate_entity_storage,
)
from template_sync.reviews_migration import migrate_reviews


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def _init_consumer(tmp_path: Path) -> Path:
    root = tmp_path / "consumer"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Tester")
    (root / ".repo_template").mkdir()
    (root / ".repo_template/entity_schema.json").write_text('{"version": 1}\n', encoding="utf-8")
    (root / "README.md").write_text("# Repo\n", encoding="utf-8")
    return root


def _commit(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-m", message)


def _write_review(root: Path, name: str, *, with_meta: bool = True) -> Path:
    directory = root / "docs/archive/reviews" / name
    directory.mkdir(parents=True)
    (directory / "review_general.md").write_text("PASS\n", encoding="utf-8")
    if with_meta:
        (directory / "_meta").mkdir()
        (directory / "_meta/run.log").write_text("process\n", encoding="utf-8")
    return directory


def _untracked_meta(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return [line for line in result.stdout.splitlines() if "_meta" in line]


def test_entity_storage_migration_removes_both_stale_derived_indexes(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    for rel in ("docs/tasks_index.json", "docs/archive/tasks_index.json"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[]\n", encoding="utf-8")
    _commit(root, "chore: stale derived indexes")
    sync = ctx.SyncContext.for_consumer(root)

    status = entity_storage_migration_status(sync=sync)
    assert any("docs/tasks_index.json" in item for item in status)
    assert any("docs/archive/tasks_index.json" in item for item in status)

    migrate_entity_storage(sync=sync, write=True)

    assert not (root / "docs/tasks_index.json").exists()
    assert not (root / "docs/archive/tasks_index.json").exists()
    assert entity_storage_migration_status(sync=sync) == []


def test_reviews_migration_cleans_meta_on_referenced_archive_review(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    for index in range(1, 7):
        _write_review(root, f"review_2026010{index}_120000", with_meta=False)
    referenced = root / "docs/archive/reviews/review_20260101_120000"
    (root / "docs/handoff.md").write_text(
        f"- 历史评审见 {referenced.relative_to(root).as_posix()}\n", encoding="utf-8"
    )
    _commit(root, "chore: archive reviews")
    (referenced / "_meta").mkdir()
    (referenced / "_meta/run.log").write_text("process\n", encoding="utf-8")
    sync = ctx.SyncContext.for_consumer(root)

    reports = migrate_reviews(sync, write=False)
    joined = "\n".join(reports)
    assert "保留被引用 review" in joined
    assert "review_20260101_120000/_meta" in joined

    migrate_reviews(sync, write=True)

    assert referenced.is_dir()
    assert (referenced / "review_general.md").is_file()
    assert not (referenced / "_meta").exists()
    assert _untracked_meta(root) == []


def test_reviews_migration_keeps_meta_on_untracked_archive_review(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    for index in range(2, 7):
        _write_review(root, f"review_2026010{index}_120000", with_meta=False)
    _commit(root, "chore: archive reviews")
    untracked = _write_review(root, "review_20260101_120000")
    sync = ctx.SyncContext.for_consumer(root)

    reports = migrate_reviews(sync, write=False)
    joined = "\n".join(reports)
    assert "跳过未跟踪 review" in joined
    assert "review_20260101_120000/_meta" not in joined

    migrate_reviews(sync, write=True)

    assert untracked.is_dir()
    assert (untracked / "review_general.md").is_file()
    assert (untracked / "_meta").is_dir()
    assert (untracked / "_meta/run.log").is_file()


def test_reviews_migration_stops_when_reference_points_inside_meta(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    for index in range(1, 7):
        _write_review(root, f"review_2026010{index}_120000", with_meta=False)
    referenced = root / "docs/archive/reviews/review_20260101_120000"
    (referenced / "_meta").mkdir()
    (referenced / "_meta/run.log").write_text("process\n", encoding="utf-8")
    (root / "docs/handoff.md").write_text(
        f"- 过程记录见 {referenced.relative_to(root).as_posix()}/_meta/run.log\n",
        encoding="utf-8",
    )
    _commit(root, "chore: archive reviews with meta reference")
    sync = ctx.SyncContext.for_consumer(root)

    with pytest.raises(ctx.SyncError, match="_meta|引用"):
        migrate_reviews(sync, write=False)
    with pytest.raises(ctx.SyncError, match="_meta|引用"):
        migrate_reviews(sync, write=True)

    assert referenced.is_dir()
    assert (referenced / "_meta/run.log").is_file()
