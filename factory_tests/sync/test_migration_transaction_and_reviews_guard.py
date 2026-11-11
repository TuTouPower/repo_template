"""G1 迁移跟进：migration_transaction 固定 common 目录；reviews 超 5 个一律删除回归。

根因 2 删除了旧 ``.scratch`` 事务预检与 migration_transaction 双位置分支：
旧 .scratch 事务阻断用例与「finished 旧事务沿用旧目录」用例整体删除，
补断言事务恒落在 Git common 状态目录，不受旧目录存在与否影响。

语义变更：用户裁定超过 5 个的旧 review 一律 rmtree，废除「被引用/未跟踪
保留」规则。原 C1「删除内容预检」与 P1-4「引用源解析」相关用例失去意义，
已整体删除并按新语义重写回归（禁止就地把旧预期改成新实现的输出）。

N2 顶层方案跟进：reviews_migration_status 曾把「待删除子集」(ordered[5:])
冒充「完整迁移集合」，导致 ≤5 个 archive review 的迁回被公开入口静默跳过、
非法目录名漏报。现全部入口从 frozen ReviewPlan 派生，新增 0–5 个 archive
公开迁回、非法名、live/archive 混合、同名冲突与计划不可变回归。
"""

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
from repo_task.file_transaction import SYNC_TRANSACTION_DIR
from template_sync import context as ctx
from template_sync.common import migration_transaction
from template_sync.migrate import cmd_migrate, migration_findings
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


@pytest.mark.parametrize("legacy_present", [False, True])
def test_migration_transaction_always_uses_common_state_dir(
    consumer: Path, legacy_present: bool
) -> None:
    legacy = consumer / ".scratch/repo_sync_rollback/transaction.json"
    if legacy_present:
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{"status": "applied"}\n', encoding="utf-8")
    sync = ctx.SyncContext.for_consumer(consumer)

    transaction = migration_transaction(sync)

    assert transaction.root == consumer
    assert transaction.backup == common_state_dir(consumer) / SYNC_TRANSACTION_DIR
    if legacy_present:
        assert legacy.is_file()


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
    assert migration_findings("reviews", sync) == [rel]

    reports = migrate_reviews(sync, write=True)

    assert f"删除旧 review：{rel}" in reports
    assert not oldest.exists()
    assert not (oldest / "_meta").exists()
    assert not (oldest / "notes.md").exists()
    assert sorted(path.name for path in reviews.glob("review_*")) == [
        f"review_2026010{index}_120000" for index in range(2, 7)
    ]
    assert reviews_migration_status(sync) == []
    assert migration_findings("reviews", sync) == []


def _write_archive_review(consumer: Path, name: str) -> Path:
    directory = consumer / "docs/archive/reviews" / name
    directory.mkdir(parents=True)
    (directory / "review.md").write_text("PASS\n", encoding="utf-8")
    return directory


@pytest.mark.parametrize("count", [1, 3, 5])
def test_reviews_archive_within_limit_moves_back_via_cmd_migrate(
    consumer: Path, count: int, capsys
) -> None:
    """N2 根因复现：≤5 个 archive review 全属 keep，必须 archive→live 迁回。

    旧 status 只返回 ordered[5:]（删除子集），≤5 个时为空，公开入口
    cmd_migrate 因 `if not status: return []` 静默跳过，报「已是当前格式」，
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
    assert migration_findings("reviews", sync) == status

    assert cmd_migrate(argparse.Namespace(kind="reviews", write=False, sync=sync)) == 0
    dry = capsys.readouterr().out
    assert "dry-run" in dry
    assert "已是当前格式" not in dry
    assert dry.count("->") >= count
    assert not (consumer / "docs/reviews").exists()
    assert all((archived / name).is_dir() for name in names)

    assert cmd_migrate(argparse.Namespace(kind="reviews", write=True, sync=sync)) == 0
    capsys.readouterr()
    live = consumer / "docs/reviews"
    assert sorted(path.name for path in live.glob("review_*")) == sorted(names)
    for name in names:
        assert not (archived / name).exists()
    assert not archived.exists()
    assert reviews_migration_status(sync) == []
    assert migration_findings("reviews", sync) == []


def test_reviews_no_dirs_is_noop(consumer: Path, capsys) -> None:
    """0 个 archive review：完整计划为空，status 空，公开入口不误报迁移。"""
    sync = ctx.SyncContext.for_consumer(consumer)

    assert reviews_migration_status(sync) == []
    assert migration_findings("reviews", sync) == []
    assert cmd_migrate(argparse.Namespace(kind="reviews", write=False, sync=sync)) == 0
    assert "已是当前格式" in capsys.readouterr().out
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

    assert cmd_migrate(argparse.Namespace(kind="reviews", write=True, sync=sync)) == 0
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
    cmd_migrate 提前返回「已是当前格式」，非法名漏报。现计划构造阶段统一校验，
    status 返回错误文本，migration_findings 暴露阻断，cmd_migrate 不再跳过。
    """
    bad = _write_archive_review(consumer, "review_no_date")
    sync = ctx.SyncContext.for_consumer(consumer)

    status = reviews_migration_status(sync)
    assert status
    assert "YYYYMMDD" in status[0]
    assert "review_no_date" in status[0]
    assert migration_findings("reviews", sync) == status

    with pytest.raises(ctx.SyncError, match="YYYYMMDD"):
        cmd_migrate(argparse.Namespace(kind="reviews", write=False, sync=sync))
    assert "已是当前格式" not in capsys.readouterr().out

    with pytest.raises(ctx.SyncError, match="YYYYMMDD"):
        cmd_migrate(argparse.Namespace(kind="reviews", write=True, sync=sync))
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
