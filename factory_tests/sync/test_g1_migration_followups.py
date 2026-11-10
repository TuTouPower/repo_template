"""G1 迁移跟进：B2 旧 .scratch 事务预检；C1 review 删除内容预检。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS = FACTORY_ROOT / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS))

from repo_task.context import common_state_dir
from template_sync import context as ctx
from template_sync.common import migration_transaction
from template_sync.entity_storage_migration import MigrationError, migrate_entity_storage
from template_sync.migrate import cmd_migrate
from template_sync.reviews_migration import migrate_reviews
from template_sync.transaction import cmd_transaction

LEGACY_TRANSACTION = ".scratch/repo_sync_rollback"


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


def _write_legacy_transaction(consumer: Path, status: str = "applied") -> Path:
    journal = consumer / LEGACY_TRANSACTION / "transaction.json"
    journal.parent.mkdir(parents=True)
    journal.write_text(
        json.dumps({"version": 1, "status": status, "paths": {}}) + "\n", encoding="utf-8"
    )
    return journal


def _make_reviews(consumer: Path, count: int) -> Path:
    reviews = consumer / "docs/reviews"
    for index in range(1, count + 1):
        directory = reviews / f"review_2026010{index}_120000"
        directory.mkdir(parents=True)
        (directory / "review_general.md").write_text("PASS\n", encoding="utf-8")
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "reviews")
    return reviews


def test_migrate_all_write_blocks_on_open_legacy_transaction_before_any_write(
    consumer: Path,
) -> None:
    journal = _write_legacy_transaction(consumer)
    todo = consumer / "docs/pending/todo/p001_x.md"
    todo.parent.mkdir(parents=True)
    todo.write_text("# p001 x\n", encoding="utf-8")
    sync = ctx.SyncContext.for_consumer(consumer)

    with pytest.raises(ctx.SyncError, match="repo_sync_rollback") as error:
        cmd_migrate(argparse.Namespace(kind="all", write=True, sync=sync))

    assert "transaction accept" in str(error.value)
    assert "transaction rollback" in str(error.value)
    assert todo.is_file()
    assert not (consumer / "docs/pending/p001_x.md").exists()
    assert not (consumer / ".repo_template/entity_schema.json").exists()
    assert json.loads(journal.read_text(encoding="utf-8"))["status"] == "applied"


def test_single_kind_write_blocks_on_open_legacy_transaction(consumer: Path) -> None:
    _write_legacy_transaction(consumer)
    reviews = _make_reviews(consumer, 6)
    sync = ctx.SyncContext.for_consumer(consumer)

    with pytest.raises(ctx.SyncError, match="repo_sync_rollback"):
        cmd_migrate(argparse.Namespace(kind="reviews", write=True, sync=sync))

    assert sorted(path.name for path in reviews.glob("review_*")) == [
        f"review_2026010{index}_120000" for index in range(1, 7)
    ]
    assert not (common_state_dir(consumer) / "sync_transaction").exists()


def test_entity_storage_migration_blocks_on_open_legacy_transaction(consumer: Path) -> None:
    _write_legacy_transaction(consumer, "applying")
    sync = ctx.SyncContext.for_consumer(consumer)

    with pytest.raises(MigrationError, match="repo_sync_rollback") as error:
        migrate_entity_storage(sync, write=True)

    assert "新 transaction" in str(error.value)
    assert not (consumer / ".repo_template/entity_schema.json").exists()


def test_migration_transaction_allows_finished_legacy_transaction(consumer: Path) -> None:
    _write_legacy_transaction(consumer, "accepted")
    sync = ctx.SyncContext.for_consumer(consumer)

    transaction = migration_transaction(sync)

    assert transaction.backup == consumer / LEGACY_TRANSACTION


def test_transaction_status_reports_open_legacy_transaction(consumer: Path, capsys) -> None:
    _write_legacy_transaction(consumer, "restore_failed")
    sync = ctx.SyncContext.for_consumer(consumer)

    assert cmd_transaction(argparse.Namespace(sync=sync, action="status")) == 0

    captured = capsys.readouterr()
    assert "null" in captured.out
    assert LEGACY_TRANSACTION in captured.err
    assert "restore_failed" in captured.err
    assert "看不到" in captured.err



def test_reviews_dry_run_lists_meta_and_untracked_files(consumer: Path) -> None:
    reviews = _make_reviews(consumer, 6)
    oldest = reviews / "review_20260101_120000"
    (oldest / "_meta").mkdir()
    (oldest / "_meta/run.log").write_text("process\n", encoding="utf-8")
    (oldest / "notes.md").write_text("user notes\n", encoding="utf-8")
    sync = ctx.SyncContext.for_consumer(consumer)

    reports = migrate_reviews(sync, write=False)
    joined = "\n".join(reports)

    assert f"删除旧 review：docs/reviews/{oldest.name}" in joined
    assert f"docs/reviews/{oldest.name}/_meta" in joined
    assert f"docs/reviews/{oldest.name}/notes.md" in joined
    assert oldest.is_dir()
    assert (oldest / "notes.md").is_file()


def test_reviews_write_stops_on_untracked_non_meta_file(consumer: Path) -> None:
    reviews = _make_reviews(consumer, 6)
    oldest = reviews / "review_20260101_120000"
    (oldest / "_meta").mkdir()
    (oldest / "_meta/run.log").write_text("process\n", encoding="utf-8")
    (oldest / "notes.md").write_text("user notes\n", encoding="utf-8")
    sync = ctx.SyncContext.for_consumer(consumer)

    with pytest.raises(ctx.SyncError, match="未跟踪文件") as error:
        migrate_reviews(sync, write=True)

    assert "notes.md" in str(error.value)
    assert len(list(reviews.glob("review_*"))) == 6
    assert (oldest / "notes.md").read_text(encoding="utf-8") == "user notes\n"
    assert (oldest / "_meta/run.log").is_file()


def test_reviews_write_deletes_meta_only_untracked_content(consumer: Path) -> None:
    reviews = _make_reviews(consumer, 6)
    oldest = reviews / "review_20260101_120000"
    (oldest / "_meta").mkdir()
    (oldest / "_meta/run.log").write_text("process\n", encoding="utf-8")
    sync = ctx.SyncContext.for_consumer(consumer)

    reports = migrate_reviews(sync, write=True)

    assert any("_meta" in report for report in reports)
    assert not oldest.exists()
    assert sorted(path.name for path in reviews.glob("review_*")) == [
        f"review_2026010{index}_120000" for index in range(2, 7)
    ]


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
