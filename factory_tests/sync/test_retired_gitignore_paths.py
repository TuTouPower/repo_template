"""退役 .gitignore 托管规则的回归：迁移后不留 archive 残留。

覆盖两条规则：
- ``docs/archive/tasks_index.json``：entity-storage 迁移显式删除并报告；
- ``docs/archive/reviews/review_*/_meta/``：reviews 合并 archive 后以最近
  5 个为上限，超出 5 个的旧目录一律连同 ``_meta/`` 删除，被引用或未跟踪
  都不保留，archive 清理后不留残留。

N2 顶层方案跟进：status 现从完整 frozen ReviewPlan 派生，archive 中属于 keep
的目录同时列为迁回项，不再只是「待删除子集」。下列两条 reviews 用例按 TDD
整体重写（禁止就地改旧预期），改为校验「迁回项 + 超额删除项」并存的完整计划。
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
from template_sync.reviews_migration import migrate_reviews, reviews_migration_status


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


def test_reviews_migration_deletes_referenced_archive_review_with_meta(tmp_path: Path) -> None:
    """超出 5 个的被引用旧目录连同 _meta/ 一律 rmtree，archive 不留残留。

    按 N2 顶层方案整体重写：status 现从完整 frozen ReviewPlan 派生，archive
    中属于 keep 的目录同时列为迁回项，不再只是「待删除子集」。旧断言
    `status == [rel]`（把删除子集冒充完整计划）已失效，改为校验完整计划：
    5 条迁回 + 1 条超额删除并存，写盘后收敛为空。保留规则（引用/未跟踪）
    依旧废除：验证引用不阻止删除、dry-run 零写入、_meta/ 无残留。
    """
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

    rel = referenced.relative_to(root).as_posix()
    status = reviews_migration_status(sync)
    assert rel in status
    assert len(status) == 6
    assert sum("->" in item for item in status) == 5

    reports = migrate_reviews(sync, write=False)
    assert f"删除旧 review：{rel}" in reports
    assert sum("->" in report for report in reports) == 5
    assert referenced.is_dir()
    assert (referenced / "_meta/run.log").is_file()

    migrate_reviews(sync, write=True)

    assert not referenced.exists()
    assert not (referenced / "_meta").exists()
    assert not (referenced / "_meta/run.log").exists()
    assert sorted(path.name for path in (root / "docs/reviews").glob("review_*")) == [
        f"review_2026010{index}_120000" for index in range(2, 7)
    ]
    assert not (root / "docs/archive/reviews").exists()
    assert _untracked_meta(root) == []
    assert reviews_migration_status(sync) == []


def test_reviews_migration_deletes_untracked_archive_review_with_meta(tmp_path: Path) -> None:
    """整体未跟踪的旧目录连同 _meta/ 一律 rmtree，未跟踪不再跳过。

    按 N2 顶层方案整体重写：status 现为「5 条迁回 + 1 条超额删除」的完整
    计划，旧断言 `status == [rel]` 已失效，整体改写（禁止就地改旧预期）。
    保留规则废除：未跟踪内容随超额目录一并删除，keep 从 archive 迁回 live。
    """
    root = _init_consumer(tmp_path)
    for index in range(2, 7):
        _write_review(root, f"review_2026010{index}_120000", with_meta=False)
    _commit(root, "chore: archive reviews")
    untracked = _write_review(root, "review_20260101_120000")
    sync = ctx.SyncContext.for_consumer(root)

    rel = untracked.relative_to(root).as_posix()
    status = reviews_migration_status(sync)
    assert rel in status
    assert len(status) == 6
    assert sum("->" in item for item in status) == 5

    reports = migrate_reviews(sync, write=True)

    assert f"删除旧 review：{rel}" in reports
    assert sum("->" in report for report in reports) == 5
    assert not untracked.exists()
    assert not (untracked / "_meta").exists()
    assert not (untracked / "_meta/run.log").exists()
    assert sorted(path.name for path in (root / "docs/reviews").glob("review_*")) == [
        f"review_2026010{index}_120000" for index in range(2, 7)
    ]
    assert not (root / "docs/archive/reviews").exists()
    assert _untracked_meta(root) == []
    assert reviews_migration_status(sync) == []
