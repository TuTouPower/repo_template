"""WP1：state-root 一次性迁移。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS = FACTORY_ROOT / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from repo_task.context import common_state_dir
from template_sync import context as ctx
from template_sync import state_root_migration
from template_sync.state_root_migration import migrate_state_root, state_root_migration_status


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


@pytest.fixture
def consumer(tmp_path: Path) -> Path:
    root = tmp_path / "consumer"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")
    runtime = root / "docs/runtime"
    runtime.mkdir(parents=True)
    (runtime / "dispatch_ledger.jsonl").write_text('{"event": "start"}\n', encoding="utf-8")
    (runtime / "dispatch_ledger.lock").write_text("", encoding="utf-8")
    (runtime / "tasks_audit.log").write_text("audit\n", encoding="utf-8")
    (runtime / "goal_queue.json").write_text('{"queue": ["t001"]}\n', encoding="utf-8")
    (root / ".repo_template").mkdir()
    (root / ".repo_template/sync_state.json").write_text('{"template_source": {"kind": "path", "value": "x"}}\n', encoding="utf-8")
    (root / ".scratch").mkdir()
    (root / ".scratch/statusline_workdir.jsonl").write_text("{}\n", encoding="utf-8")
    (root / ".scratch/repo_sync_rollback").mkdir(parents=True)
    (root / ".scratch/repo_sync_rollback/transaction.json").write_text('{"status": "accepted"}\n', encoding="utf-8")
    (root / ".scratch/task_graph_transaction").mkdir()
    (root / ".scratch/task_graph_transaction/transaction.json").write_text('{"status": "accepted"}\n', encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    return root


def _originals(consumer: Path) -> dict[Path, bytes]:
    return {
        consumer / "docs/runtime/dispatch_ledger.jsonl": b'{"event": "start"}\n',
        consumer / "docs/runtime/tasks_audit.log": b"audit\n",
        consumer / "docs/runtime/goal_queue.json": b'{"queue": ["t001"]}\n',
        consumer / ".scratch/statusline_workdir.jsonl": b"{}\n",
        consumer / ".repo_template/sync_state.json": b'{"template_source": {"kind": "path", "value": "x"}}\n',
        consumer / ".scratch/repo_sync_rollback/transaction.json": b'{"status": "accepted"}\n',
        consumer / ".scratch/task_graph_transaction/transaction.json": b'{"status": "accepted"}\n',
    }


def test_dry_run_writes_nothing(consumer):
    sync = ctx.SyncContext.for_consumer(consumer)
    reports = migrate_state_root(sync)
    assert reports
    assert any("清理 docs/runtime/dispatch_ledger.lock" in item for item in reports)
    assert (consumer / "docs/runtime/dispatch_ledger.jsonl").is_file()
    assert (consumer / "docs/runtime/dispatch_ledger.lock").is_file()
    assert not common_state_dir(consumer).exists()


def test_write_moves_state_and_preserves_bytes(consumer):
    sync = ctx.SyncContext.for_consumer(consumer)
    assert state_root_migration_status(sync)
    migrate_state_root(sync, write=True)
    state = common_state_dir(consumer)
    assert (state / "ledger.jsonl").read_text(encoding="utf-8") == '{"event": "start"}\n'
    assert (state / "tasks_audit.log").read_text(encoding="utf-8") == "audit\n"
    assert (state / "statusline_workdir.jsonl").read_text(encoding="utf-8") == "{}\n"
    assert (state / "sync_transaction/transaction.json").is_file()
    assert (state / "graph_transaction/transaction.json").is_file()
    assert not (consumer / "docs/runtime").exists()
    assert not (consumer / ".scratch/statusline_workdir.jsonl").exists()
    assert not (consumer / ".repo_template/sync_state.json").exists()
    assert not state_root_migration_status(sync)


def test_registered_worktree_blocks(consumer, tmp_path):
    worktree = tmp_path / "wt"
    _git(consumer, "worktree", "add", "-b", "wt", str(worktree))
    sync = ctx.SyncContext.for_consumer(consumer)
    with pytest.raises(ctx.SyncError, match="worktree"):
        migrate_state_root(sync, write=True)
    assert (consumer / "docs/runtime/dispatch_ledger.jsonl").is_file()


def test_unknown_runtime_residual_stops_before_write(consumer):
    stray = consumer / "docs/runtime/mystery.bin"
    stray.write_text("unknown\n", encoding="utf-8")
    sync = ctx.SyncContext.for_consumer(consumer)
    status = state_root_migration_status(sync)
    assert any("mystery.bin" in item and "未知残留" in item for item in status)
    with pytest.raises(ctx.SyncError, match="未知残留"):
        migrate_state_root(sync, write=True)
    for path, expected in _originals(consumer).items():
        assert path.read_bytes() == expected
    assert stray.read_text(encoding="utf-8") == "unknown\n"
    assert (consumer / "docs/runtime/dispatch_ledger.lock").is_file()
    assert not common_state_dir(consumer).exists()


def test_runtime_and_scratch_statusline_conflict_stops(consumer):
    (consumer / "docs/runtime/statusline_workdir.jsonl").write_text("runtime\n", encoding="utf-8")
    sync = ctx.SyncContext.for_consumer(consumer)
    status = state_root_migration_status(sync)
    assert any("statusline_workdir.jsonl" in item and "同时存在" in item for item in status)
    with pytest.raises(ctx.SyncError, match="同时存在"):
        migrate_state_root(sync, write=True)
    assert (consumer / ".scratch/statusline_workdir.jsonl").read_text(encoding="utf-8") == "{}\n"
    assert (consumer / "docs/runtime/statusline_workdir.jsonl").read_text(encoding="utf-8") == "runtime\n"
    assert not common_state_dir(consumer).exists()


def test_copy_failure_rolls_back_and_rerun_succeeds(consumer, monkeypatch):
    sync = ctx.SyncContext.for_consumer(consumer)
    original = state_root_migration._copy_verify
    calls = 0

    def flaky(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 14:
            if source.is_dir():
                target.mkdir(parents=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("partial", encoding="utf-8")
            raise OSError("injected copy failure")
        original(source, target)

    monkeypatch.setattr(state_root_migration, "_copy_verify", flaky)
    with pytest.raises(ctx.SyncError, match="injected copy failure"):
        migrate_state_root(sync, write=True)
    for path, expected in _originals(consumer).items():
        assert path.read_bytes() == expected

    state = common_state_dir(consumer)
    assert not (state / "sync_transaction").exists()
    assert not (state / "graph_transaction").exists()
    assert (consumer / "docs/runtime/dispatch_ledger.lock").is_file()

    monkeypatch.setattr(state_root_migration, "_copy_verify", original)
    migrate_state_root(sync, write=True)
    assert (state / "ledger.jsonl").read_text(encoding="utf-8") == '{"event": "start"}\n'
    assert (state / "graph_transaction/transaction.json").is_file()
    assert not (consumer / "docs/runtime").exists()


def test_restore_failure_keeps_backup_and_reports_source(consumer, monkeypatch):
    sync = ctx.SyncContext.for_consumer(consumer)
    backup_root = common_state_dir(consumer) / state_root_migration.BACKUP_NAME
    first_source = consumer / "docs/runtime/dispatch_ledger.jsonl"

    original_remove = state_root_migration._remove_tree

    def failing_remove(path: Path) -> None:
        original_remove(path)
        if path == first_source:
            raise OSError("injected delete failure")

    original_copy = state_root_migration._copy_verify

    def failing_restore(source: Path, target: Path) -> None:
        if target == first_source and source.parent == backup_root:
            raise OSError("injected restore failure")
        original_copy(source, target)

    monkeypatch.setattr(state_root_migration, "_remove_tree", failing_remove)
    monkeypatch.setattr(state_root_migration, "_copy_verify", failing_restore)
    with pytest.raises(ctx.SyncError) as raised:
        migrate_state_root(sync, write=True)

    message = str(raised.value)
    assert "injected delete failure" in message
    assert "injected restore failure" in message
    assert str(first_source) in message
    assert f"备份保留在 {backup_root}" in message
    assert (backup_root / "0").read_text(encoding="utf-8") == '{"event": "start"}\n'


def test_unfinished_restore_state_machine_blocks_rerun(consumer, monkeypatch):
    sync = ctx.SyncContext.for_consumer(consumer)
    state_root = common_state_dir(consumer)
    backup_root = state_root / state_root_migration.BACKUP_NAME
    state_file = state_root / state_root_migration.STATE_FILE
    first_source = consumer / "docs/runtime/dispatch_ledger.jsonl"

    original_remove = state_root_migration._remove_tree
    original_copy = state_root_migration._copy_verify

    def failing_remove(path: Path) -> None:
        original_remove(path)
        if path == first_source:
            raise OSError("injected delete failure")

    def failing_restore(source: Path, target: Path) -> None:
        if target == first_source and source.parent == backup_root:
            raise OSError("injected restore failure")
        original_copy(source, target)

    monkeypatch.setattr(state_root_migration, "_remove_tree", failing_remove)
    monkeypatch.setattr(state_root_migration, "_copy_verify", failing_restore)
    with pytest.raises(ctx.SyncError):
        migrate_state_root(sync, write=True)

    payload = json.loads(state_file.read_text(encoding="utf-8"))
    assert payload["status"] == "restore_failed"
    assert payload["backup"] == str(backup_root)
    assert payload["sources"]
    assert (backup_root / "0").is_file()
    ledger_bytes = (backup_root / "0").read_bytes()

    monkeypatch.setattr(state_root_migration, "_remove_tree", original_remove)
    monkeypatch.setattr(state_root_migration, "_copy_verify", original_copy)

    with pytest.raises(ctx.SyncError, match="禁止创建新迁移") as raised:
        migrate_state_root(sync, write=True)
    assert str(state_file) in str(raised.value)
    assert (backup_root / "0").read_bytes() == ledger_bytes

    findings = state_root_migration_status(sync)
    assert any("恢复未完成" in item for item in findings)
    assert any(str(state_file) in item for item in findings)

    first_source.write_bytes(ledger_bytes)
    state_file.unlink()
    state_root_migration._remove_tree(backup_root)
    reports = migrate_state_root(sync, write=True)
    assert reports
    assert not state_file.exists()
    assert not backup_root.exists()
    assert (state_root / "ledger.jsonl").read_bytes() == ledger_bytes


def _backup_snapshot(backup_root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(backup_root).as_posix(): path.read_bytes()
        for path in sorted(backup_root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture
def interrupted_migration(consumer, monkeypatch):
    sync = ctx.SyncContext.for_consumer(consumer)
    state_root = common_state_dir(consumer)
    backup_root = state_root / state_root_migration.BACKUP_NAME
    state_file = state_root / state_root_migration.STATE_FILE
    first_source = consumer / "docs/runtime/dispatch_ledger.jsonl"

    original_remove = state_root_migration._remove_tree
    original_copy = state_root_migration._copy_verify

    def failing_remove(path: Path) -> None:
        original_remove(path)
        if path == first_source:
            raise OSError("injected delete failure")

    def failing_restore(source: Path, target: Path) -> None:
        if target == first_source and source.parent == backup_root:
            raise OSError("injected restore failure")
        original_copy(source, target)

    monkeypatch.setattr(state_root_migration, "_remove_tree", failing_remove)
    monkeypatch.setattr(state_root_migration, "_copy_verify", failing_restore)
    with pytest.raises(ctx.SyncError):
        migrate_state_root(sync, write=True)
    monkeypatch.setattr(state_root_migration, "_remove_tree", original_remove)
    monkeypatch.setattr(state_root_migration, "_copy_verify", original_copy)

    assert (backup_root / "0").is_file()
    assert json.loads(state_file.read_text(encoding="utf-8"))["status"] == "restore_failed"
    return sync, state_file, backup_root


@pytest.mark.parametrize(
    "kind,payload",
    [
        pytest.param("write", '{"status": "unknown"}', id="unknown_status"),
        pytest.param("write", "{}", id="missing_status_empty"),
        pytest.param(
            "write",
            json.dumps({"backup": "/tmp/backup", "sources": ["/tmp/source"]}),
            id="missing_status_with_fields",
        ),
        pytest.param("write", "[]", id="list_root"),
        pytest.param("write", '"x"', id="string_root"),
        pytest.param("write", "{ not json", id="invalid_json"),
        pytest.param("unreadable", None, id="unreadable"),
        pytest.param(
            "write",
            json.dumps({"status": 1, "backup": "/tmp/backup", "sources": []}),
            id="status_not_string",
        ),
        pytest.param(
            "write",
            json.dumps({"status": "in_progress", "sources": []}),
            id="missing_backup",
        ),
        pytest.param(
            "write",
            json.dumps({"status": "in_progress", "backup": 5, "sources": []}),
            id="backup_not_string",
        ),
        pytest.param(
            "write",
            json.dumps({"status": "in_progress", "backup": "/tmp/backup"}),
            id="missing_sources",
        ),
        pytest.param(
            "write",
            json.dumps({"status": "in_progress", "backup": "/tmp/backup", "sources": "x"}),
            id="sources_not_list",
        ),
        pytest.param(
            "write",
            json.dumps({"status": "in_progress", "backup": "/tmp/backup", "sources": [1]}),
            id="sources_element_not_string",
        ),
        pytest.param("keep", None, id="valid_restore_failed"),
    ],
)
def test_pending_state_records_block_rerun(interrupted_migration, kind, payload):
    sync, state_file, backup_root = interrupted_migration
    ledger_bytes = (backup_root / "0").read_bytes()
    before = _backup_snapshot(backup_root)

    if kind == "keep":
        pass
    elif kind == "unreadable":
        state_file.unlink()
        state_file.mkdir()
    else:
        state_file.write_text(payload, encoding="utf-8")

    with pytest.raises(ctx.SyncError, match="禁止创建新迁移"):
        migrate_state_root(sync, write=True)

    assert (backup_root / "0").read_bytes() == ledger_bytes
    assert _backup_snapshot(backup_root) == before
    assert backup_root.is_dir()


def test_successful_migration_clears_state_and_backup(consumer):
    sync = ctx.SyncContext.for_consumer(consumer)
    state_root = common_state_dir(consumer)
    state_file = state_root / state_root_migration.STATE_FILE
    backup_root = state_root / state_root_migration.BACKUP_NAME
    migrate_state_root(sync, write=True)
    assert not state_file.exists()
    assert not backup_root.exists()
    assert not state_root_migration_status(sync)
