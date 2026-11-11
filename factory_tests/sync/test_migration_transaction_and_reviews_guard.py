"""迁移接口与 reviews 完整计划：事务由调用方持有，超额目录统一删除。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS = FACTORY_ROOT / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS))

from repo_task.context import common_state_dir
from repo_task.file_transaction import SYNC_TRANSACTION_DIR, state_transaction
from template_sync import context as ctx
from template_sync.common import finished_legacy_transactions
from migration_support import exercise_migrations, findings
from template_sync.reviews_migration import (
    ReviewPlan,
    _plan_reviews,
    migrate_reviews,
    reviews_migration_status,
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


@pytest.fixture
def consumer(tmp_path: Path) -> Path:
    root = tmp_path / "consumer"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")
    (root / ".repo_template").mkdir()
    (root / ".repo_template/sync_state.json").write_text("{}\n", encoding="utf-8")
    (root / ".gitignore").write_text(
        ".scratch/\ndocs/reviews/review_*/_meta/\n", encoding="utf-8"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    return root


def test_finished_legacy_does_not_change_current_transaction_location(consumer: Path) -> None:
    legacy = consumer / ".scratch/repo_sync_rollback/transaction.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"status": "accepted"}\n', encoding="utf-8")
    sync = ctx.SyncContext.for_consumer(consumer)

    transaction = state_transaction(sync.consumer, SYNC_TRANSACTION_DIR)

    assert transaction.root == consumer
    assert transaction.backup == common_state_dir(consumer) / SYNC_TRANSACTION_DIR
    assert legacy.is_file()  # 删除只由显式 state-root 迁移执行。


def test_reviews_lists_all_undated_directories_at_once(consumer: Path) -> None:
    reviews = consumer / "docs/reviews"
    archived = consumer / "docs/archive/reviews"
    for base, name in (
        (reviews, "review_alpha"),
        (reviews, "review_beta"),
        (archived, "review_gamma"),
    ):
        (base / name).mkdir(parents=True)
        (base / name / "review_general.md").write_text("PASS\n", encoding="utf-8")
    (reviews / "review_20260101_120000").mkdir()
    (reviews / "review_20260101_120000/review_general.md").write_text("PASS\n", encoding="utf-8")
    sync = ctx.SyncContext.for_consumer(consumer)

    with pytest.raises(ctx.SyncError, match="YYYYMMDD") as error:
        migrate_reviews(sync, write=False)

    message = str(error.value)
    assert "review_alpha" in message
    assert "review_beta" in message
    assert "review_gamma" in message


def test_reviews_write_deletes_all_removable_including_referenced_untracked_meta(
    consumer: Path,
) -> None:
    """超过 5 个一律 rmtree：被引用、未跟踪、含 _meta/ 的旧目录全部删除。

    用户裁定机械化删除，废除引用/未跟踪保留规则。旧「保留被引用 review」
    「跳过未跟踪 review」「未跟踪文件写入前停止」用例已整体删除（保留规则
    不复存在，不能就地改预期）。本用例验证 status、dry-run、write 共用同一
    可删除项集合：迁移后 status 与 apply 门禁的 reviews findings 均收敛为空。
    """
    reviews = consumer / "docs/reviews"
    for index in range(2, 7):
        directory = reviews / f"review_2026010{index}_120000"
        directory.mkdir(parents=True)
        (directory / "review_general.md").write_text("PASS\n", encoding="utf-8")
    (consumer / "docs/handoff.md").write_text(
        "[Evidence](reviews/review_20260101_120000/review_general.md)\n", encoding="utf-8"
    )
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "reviews")

    oldest = reviews / "review_20260101_120000"
    oldest.mkdir()
    (oldest / "review_general.md").write_text("PASS\n", encoding="utf-8")
    (oldest / "_meta").mkdir()
    (oldest / "_meta/run.log").write_text("process\n", encoding="utf-8")
    (oldest / "notes.md").write_text("user notes\n", encoding="utf-8")
    sync = ctx.SyncContext.for_consumer(consumer)

    rel = oldest.relative_to(consumer).as_posix()
    assert reviews_migration_status(sync) == [rel]
    assert findings("reviews", sync) == [rel]

    reports = migrate_reviews(sync, write=True)

    assert f"删除旧 review：{rel}" in reports
    assert not oldest.exists()
    assert not (oldest / "_meta").exists()
    assert not (oldest / "notes.md").exists()
    assert sorted(path.name for path in reviews.glob("review_*")) == [
        f"review_2026010{index}_120000" for index in range(2, 7)
    ]
    assert reviews_migration_status(sync) == []
    assert findings("reviews", sync) == []


def _write_archive_review(consumer: Path, name: str) -> Path:
    directory = consumer / "docs/archive/reviews" / name
    directory.mkdir(parents=True)
    (directory / "review.md").write_text("PASS\n", encoding="utf-8")
    return directory


@pytest.mark.parametrize("count", [1, 3, 5])
def test_reviews_archive_within_limit_moves_back_via_exercise_migrations(
    consumer: Path, count: int, capsys
) -> None:
    """N2 根因复现：≤5 个 archive review 全属 keep，必须 archive→live 迁回。

    旧 status 只返回 ordered[5:]（删除子集），≤5 个时为空，公开入口
    旧 CLI 因 `if not status: return []` 静默跳过，报「已是当前格式」，
    源目录仍在 archive、live 目标未创建。现 status 从完整 ReviewPlan 派生，
    迁回项非空，dry-run / --write / 验收全部从计划派生，写盘后收敛为空。
    """
    archived = consumer / "docs/archive/reviews"
    names = [f"review_2026110{index}_120000" for index in range(1, count + 1)]
    for name in names:
        _write_archive_review(consumer, name)
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "archive reviews")
    sync = ctx.SyncContext.for_consumer(consumer)

    status = reviews_migration_status(sync)
    assert status
    assert len(status) == count
    assert all("->" in item for item in status)
    assert findings("reviews", sync) == status

    assert exercise_migrations(argparse.Namespace(kind="reviews", write=False, sync=sync)) is not None
    dry = capsys.readouterr().out
    assert "未写盘" in dry
    assert "已是当前格式" not in dry
    assert dry.count("->") >= count
    assert not (consumer / "docs/reviews").exists()
    assert all((archived / name).is_dir() for name in names)

    assert exercise_migrations(argparse.Namespace(kind="reviews", write=True, sync=sync)) is not None
    capsys.readouterr()
    live = consumer / "docs/reviews"
    assert sorted(path.name for path in live.glob("review_*")) == sorted(names)
    for name in names:
        assert not (archived / name).exists()
    assert not archived.exists()
    assert reviews_migration_status(sync) == []
    assert findings("reviews", sync) == []


def test_reviews_no_dirs_is_noop(consumer: Path, capsys) -> None:
    """0 个 archive review：完整计划为空，status 空，公开入口不误报迁移。"""
    sync = ctx.SyncContext.for_consumer(consumer)

    assert reviews_migration_status(sync) == []
    assert findings("reviews", sync) == []
    assert exercise_migrations(argparse.Namespace(kind="reviews", write=False, sync=sync)) is not None
    assert "未写盘" in capsys.readouterr().out
    assert not (consumer / "docs/reviews").exists()


def test_reviews_mixed_live_archive_within_limit(consumer: Path) -> None:
    """live/archive 混合且总数 ≤5：archive 的 keep 迁回，live 不动，收敛为空。"""
    live = consumer / "docs/reviews"
    for index in range(1, 3):
        directory = live / f"review_2026110{index}_120000"
        directory.mkdir(parents=True)
        (directory / "review.md").write_text("PASS\n", encoding="utf-8")
    for index in range(3, 6):
        _write_archive_review(consumer, f"review_2026110{index}_120000")
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "mixed reviews")
    sync = ctx.SyncContext.for_consumer(consumer)

    status = reviews_migration_status(sync)
    assert len(status) == 3
    assert all("->" in item for item in status)

    assert exercise_migrations(argparse.Namespace(kind="reviews", write=True, sync=sync)) is not None
    assert sorted(path.name for path in live.glob("review_*")) == [
        f"review_2026110{index}_120000" for index in range(1, 6)
    ]
    assert not (consumer / "docs/archive/reviews").exists()
    assert reviews_migration_status(sync) == []


def test_reviews_invalid_name_within_limit_reports_and_blocks(
    consumer: Path, capsys
) -> None:
    """≤5 个但目录名非法：status 把校验错误作为 finding 暴露，公开入口不跳过。

    旧 `_validate_dates` 只在 migrate_reviews 调用，≤5 个时 status 为空、
    旧 CLI 提前返回「已是当前格式」，非法名漏报。现计划构造阶段统一校验，
    status 返回错误文本，findings 暴露阻断，旧 CLI 不再跳过。
    """
    bad = _write_archive_review(consumer, "review_no_date")
    sync = ctx.SyncContext.for_consumer(consumer)

    status = reviews_migration_status(sync)
    assert status
    assert "YYYYMMDD" in status[0]
    assert "review_no_date" in status[0]
    assert findings("reviews", sync) == status

    with pytest.raises(ctx.SyncError, match="YYYYMMDD"):
        exercise_migrations(argparse.Namespace(kind="reviews", write=False, sync=sync))
    assert "已是当前格式" not in capsys.readouterr().out

    with pytest.raises(ctx.SyncError, match="YYYYMMDD"):
        exercise_migrations(argparse.Namespace(kind="reviews", write=True, sync=sync))
    assert bad.is_dir()
    assert not (consumer / "docs/reviews").exists()


def test_reviews_same_name_conflict_blocks_with_zero_writes(consumer: Path) -> None:
    """archive keep 与 live 同名 → 计划构造阶段冲突检查抛 SyncError，零写入。"""
    live = consumer / "docs/reviews"
    name = "review_20261105_120000"
    (live / name).mkdir(parents=True)
    (live / name / "review.md").write_text("LIVE\n", encoding="utf-8")
    archived = _write_archive_review(consumer, name)
    (archived / "review.md").write_text("ARCHIVE\n", encoding="utf-8")
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "conflict")
    sync = ctx.SyncContext.for_consumer(consumer)

    status = reviews_migration_status(sync)
    assert status
    assert "同名冲突" in status[0]

    with pytest.raises(ctx.SyncError, match="同名冲突"):
        migrate_reviews(sync, write=False)
    with pytest.raises(ctx.SyncError, match="同名冲突"):
        migrate_reviews(sync, write=True)
    assert (live / name / "review.md").read_text(encoding="utf-8") == "LIVE\n"
    assert (archived / "review.md").read_text(encoding="utf-8") == "ARCHIVE\n"


def test_review_plan_is_frozen_single_source(consumer: Path) -> None:
    """完整不可变计划：frozen dataclass，status/dry-run/write 全部从同一计划派生。"""
    from dataclasses import FrozenInstanceError

    assert ReviewPlan.__dataclass_params__.frozen
    names = [f"review_2026110{index}_120000" for index in range(1, 4)]
    for name in names:
        _write_archive_review(consumer, name)
    sync = ctx.SyncContext.for_consumer(consumer)

    plan = _plan_reviews(sync)
    assert isinstance(plan.keep, tuple) and len(plan.keep) == 3
    assert isinstance(plan.moves, tuple) and len(plan.moves) == 3
    assert isinstance(plan.deletions, tuple) and plan.deletions == ()
    assert isinstance(plan.findings, tuple) and isinstance(plan.reports, tuple)
    assert list(plan.findings) == reviews_migration_status(sync)
    assert list(plan.reports) == migrate_reviews(sync, write=False)
    with pytest.raises(FrozenInstanceError):
        plan.keep = ()
