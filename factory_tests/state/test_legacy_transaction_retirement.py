"""旧事务仅在明确结束后退役；删除纳入 common 标准事务，可回滚。"""

from __future__ import annotations

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest
from factory_paths import FACTORY_ROOT

sys.path.insert(0, str(FACTORY_ROOT / "repo/.repo_template/scripts"))

from repo_task.context import common_state_dir
from repo_task.file_transaction import SYNC_TRANSACTION_DIR, FileTransaction
from template_sync import context as ctx
from template_sync.common import migration_transaction
from template_sync.migrate import cmd_migrate
from template_sync.state_root_migration import migrate_state_root, state_root_migration_status
from template_sync.transaction import cmd_transaction

LEGACY_DIRS = (".scratch/repo_sync_rollback", ".scratch/task_graph_transaction")


@pytest.fixture
def consumer(tmp_path):
    root = tmp_path / "consumer"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def _record(root, rel, payload):
    directory = root / rel
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "transaction.json").write_text(payload, encoding="utf-8")
    (directory / "backup.bin").write_bytes(b"unique backup\x00")
    return directory


@pytest.mark.parametrize("rel", LEGACY_DIRS)
@pytest.mark.parametrize("payload", [
    '{"status":"applying"}', '{"status":"applied"}',
    '{"status":"restore_failed"}', '{"status":"preparing"}',
    '{"status":"unknown"}', '{"status":[]}', '{"status":null}', '{', '[]',
])
def test_open_or_corrupt_legacy_blocks_without_writes(consumer, rel, payload):
    directory = _record(consumer, rel, payload)
    sync = ctx.SyncContext.for_consumer(consumer)
    findings = state_root_migration_status(sync)
    assert any(rel in finding for finding in findings)
    for action in (lambda: migration_transaction(sync), lambda: migrate_state_root(sync),
                   lambda: migrate_state_root(sync, write=True)):
        with pytest.raises(ctx.SyncError, match="旧.*事务"):
            action()
        assert (directory / "transaction.json").read_text(encoding="utf-8") == payload
        assert (directory / "backup.bin").read_bytes() == b"unique backup\x00"
        assert not common_state_dir(consumer).exists()


@pytest.mark.parametrize("status", ["accepted", "rolled_back"])
def test_finished_legacy_only_migrates_via_public_entry_and_is_idempotent(consumer, status):
    for rel in LEGACY_DIRS:
        _record(consumer, rel, json.dumps({"status": status}))
    sync = ctx.SyncContext.for_consumer(consumer)
    assert len(state_root_migration_status(sync)) == 2
    assert cmd_migrate(Namespace(sync=sync, kind="state-root", write=False)) == 0
    assert all((consumer / rel).exists() for rel in LEGACY_DIRS)
    assert not common_state_dir(consumer).exists()
    assert cmd_migrate(Namespace(sync=sync, kind="state-root", write=True)) == 0
    assert all(not (consumer / rel).exists() for rel in LEGACY_DIRS)
    data = json.loads((common_state_dir(consumer) / SYNC_TRANSACTION_DIR / "transaction.json").read_text())
    assert data["status"] == "accepted"
    assert set(data["paths"]) == set(LEGACY_DIRS)
    assert data["state_paths"] == {}
    assert state_root_migration_status(sync) == []
    assert migrate_state_root(sync, write=True) == []


@pytest.mark.parametrize("rel", LEGACY_DIRS)
@pytest.mark.parametrize("unsafe", ["symlink_root", "symlink_parent", "symlink_journal", "missing_journal"])
def test_unsafe_legacy_path_blocks_zero_writes(consumer, tmp_path, rel, unsafe):
    external = tmp_path / "external"
    external.mkdir()
    (external / "transaction.json").write_text('{"status":"accepted"}')
    if unsafe == "symlink_parent":
        (consumer / ".scratch").symlink_to(external, target_is_directory=True)
        directory = _record(consumer, rel, '{"status":"accepted"}')
    else:
        directory = consumer / rel
        directory.parent.mkdir(parents=True)
        if unsafe == "symlink_root":
            directory.symlink_to(external, target_is_directory=True)
        else:
            directory.mkdir()
            if unsafe == "symlink_journal":
                (directory / "transaction.json").symlink_to(external / "transaction.json")
            else:
                (directory / "backup.bin").write_bytes(b"backup without journal")
    sync = ctx.SyncContext.for_consumer(consumer)
    with pytest.raises(ctx.SyncError):
        migrate_state_root(sync, write=True)
    assert directory.exists()
    assert (external / "transaction.json").read_text() == '{"status":"accepted"}'
    assert not common_state_dir(consumer).exists()


@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt])
def test_retirement_failure_or_crash_restores_legacy_from_standard_transaction(
    consumer, monkeypatch, error_type
):
    for rel in LEGACY_DIRS:
        _record(consumer, rel, '{"status":"accepted"}')
    sync = ctx.SyncContext.for_consumer(consumer)

    def fail_after_deletion(self, **kwargs):
        assert all(not (consumer / rel).exists() for rel in LEGACY_DIRS)
        raise error_type("injected after retirement")

    monkeypatch.setattr(FileTransaction, "applied", fail_after_deletion)
    expected = ctx.SyncError if error_type is OSError else KeyboardInterrupt
    with pytest.raises(expected, match="injected after retirement"):
        migrate_state_root(sync, write=True)
    if error_type is KeyboardInterrupt:
        assert cmd_transaction(Namespace(sync=sync, action="status")) == 0
        assert cmd_transaction(Namespace(sync=sync, action="rollback", confirm_restore=True)) == 0
    for rel in LEGACY_DIRS:
        assert (consumer / rel / "transaction.json").read_text() == '{"status":"accepted"}'
        assert (consumer / rel / "backup.bin").read_bytes() == b"unique backup\x00"


def test_empty_legacy_directories_retire_without_new_state_assets(consumer):
    for rel in LEGACY_DIRS:
        (consumer / rel).mkdir(parents=True)
    sync = ctx.SyncContext.for_consumer(consumer)
    assert len(state_root_migration_status(sync)) == 2
    migrate_state_root(sync, write=True)
    assert all(not (consumer / rel).exists() for rel in LEGACY_DIRS)
    assert state_root_migration_status(sync) == []
