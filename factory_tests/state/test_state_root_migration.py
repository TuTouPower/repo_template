"""state-root 迁移：在标准 FileTransaction 内执行，崩溃恢复统一走 transaction rollback。

N1 那套「恢复未完成」状态机（``state_root_migration.json`` + 手写备份 +
``restore_failed`` 文本 + 参数化 pending 用例）、``_backup_snapshot`` 与
``interrupted_migration`` fixture 整体删除：根因 2  abolishes 手写事务协议，
迁移改由 FileTransaction 的 journal/备份机制持久保护，独立恢复入口与状态文案
不复存在，旧状态机测试断言的对象已不存在，禁止就地改预期掩盖语义变化。
保留前置阻断（worktree、执行上下文、未知残留、statusline 双来源、目标拒绝覆盖），
新增标准事务执行、失败/崩溃恢复、status 只报遗留物、日常编辑后单调为空等覆盖。
旧「事务记录保留原地」用例整体删除：已结束记录须在标准事务备份后删除，
退役与故障恢复行为由 test_legacy_transaction_retirement.py 独立覆盖。
"""

from __future__ import annotations

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS = FACTORY_ROOT / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from repo_task.context import common_state_dir
from repo_task.file_transaction import SYNC_TRANSACTION_DIR
from template_sync import context as ctx
from template_sync import state_root_migration
from template_sync.state import write_state
from template_sync.state_root_migration import migrate_state_root, state_root_migration_status
from template_sync.transaction import cmd_transaction


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


def _legacy_sources() -> dict[str, bytes]:
    return {
        "docs/runtime/dispatch_ledger.jsonl": b'{"event": "start"}\n',
        "docs/runtime/dispatch_ledger.lock": b"",
        "docs/runtime/tasks_audit.log": b"audit\n",
        "docs/runtime/goal_queue.json": b'{"queue": ["t001"]}\n',
        ".scratch/statusline_workdir.jsonl": b"{}\n",
        ".repo_template/sync_state.json": b'{"template_source": {"kind": "path", "value": "x"}}\n',
    }


def _legacy_records() -> dict[str, bytes]:
    return {
        ".scratch/repo_sync_rollback/transaction.json": b'{"status": "accepted"}\n',
        ".scratch/task_graph_transaction/transaction.json": b'{"status": "accepted"}\n',
    }


def _assert_old_layout_intact(consumer: Path) -> None:
    for rel, expected in {**_legacy_sources(), **_legacy_records()}.items():
        path = consumer / rel
        assert path.is_file(), rel
        assert path.read_bytes() == expected, rel


def _assert_no_common_state_files(state: Path) -> None:
    for name in (
        "ledger.jsonl", "tasks_audit.log", "goal_queue.json",
        "statusline_workdir.jsonl", "sync_state.json",
    ):
        assert not (state / name).exists(), name


def test_dry_run_writes_nothing(consumer):
    sync = ctx.SyncContext.for_consumer(consumer)
    reports = migrate_state_root(sync)
    assert reports
    assert any("清理 docs/runtime/dispatch_ledger.lock" in item for item in reports)
    _assert_old_layout_intact(consumer)
    assert not common_state_dir(consumer).exists()


def test_write_moves_state_and_preserves_bytes(consumer):
    sync = ctx.SyncContext.for_consumer(consumer)
    assert state_root_migration_status(sync)
    migrate_state_root(sync, write=True)
    state = common_state_dir(consumer)
    assert (state / "ledger.jsonl").read_text(encoding="utf-8") == '{"event": "start"}\n'
    assert (state / "tasks_audit.log").read_text(encoding="utf-8") == "audit\n"
    assert (state / "goal_queue.json").read_text(encoding="utf-8") == '{"queue": ["t001"]}\n'
    assert (state / "statusline_workdir.jsonl").read_text(encoding="utf-8") == "{}\n"
    assert json.loads((state / "sync_state.json").read_text(encoding="utf-8"))["template_source"]["value"] == "x"
    assert not (consumer / "docs/runtime").exists()
    assert not (consumer / ".scratch/statusline_workdir.jsonl").exists()
    assert not (consumer / ".repo_template/sync_state.json").exists()
    assert state_root_migration_status(sync) == []


def test_write_executes_standard_file_transaction(consumer):
    sync = ctx.SyncContext.for_consumer(consumer)
    migrate_state_root(sync, write=True)
    journal = common_state_dir(consumer) / SYNC_TRANSACTION_DIR / "transaction.json"
    data = json.loads(journal.read_text(encoding="utf-8"))
    assert data["status"] == "accepted"
    assert data["gates_verified"] is True
    assert isinstance(data["after"], str) and data["after"]
    assert set(data["paths"]) == set(_legacy_sources()) | {
        ".scratch/repo_sync_rollback", ".scratch/task_graph_transaction",
    }
    assert all(existed is True for existed in data["paths"].values())
    assert len(data["state_paths"]) == 5
    assert all(existed is False for existed in data["state_paths"].values())


def test_registered_worktree_blocks(consumer, tmp_path):
    worktree = tmp_path / "wt"
    _git(consumer, "worktree", "add", "-b", "wt", str(worktree))
    sync = ctx.SyncContext.for_consumer(consumer)
    with pytest.raises(ctx.SyncError, match="worktree"):
        migrate_state_root(sync, write=True)
    _assert_old_layout_intact(consumer)
    assert not common_state_dir(consumer).exists()


def test_unknown_runtime_residual_stops_before_write(consumer):
    stray = consumer / "docs/runtime/mystery.bin"
    stray.write_text("unknown\n", encoding="utf-8")
    sync = ctx.SyncContext.for_consumer(consumer)
    status = state_root_migration_status(sync)
    assert any("mystery.bin" in item and "未知残留" in item for item in status)
    with pytest.raises(ctx.SyncError, match="未知残留"):
        migrate_state_root(sync, write=True)
    assert stray.read_text(encoding="utf-8") == "unknown\n"
    _assert_old_layout_intact(consumer)
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


def test_target_exists_blocks_without_transaction(consumer):
    sync = ctx.SyncContext.for_consumer(consumer)
    state = common_state_dir(consumer)
    state.mkdir(parents=True)
    (state / "ledger.jsonl").write_text("existing\n", encoding="utf-8")
    with pytest.raises(ctx.SyncError, match="拒绝覆盖"):
        migrate_state_root(sync, write=True)
    _assert_old_layout_intact(consumer)
    assert (state / "ledger.jsonl").read_text(encoding="utf-8") == "existing\n"
    assert not (state / SYNC_TRANSACTION_DIR).exists()


def test_status_reports_only_residuals_not_restore_state(consumer):
    sync = ctx.SyncContext.for_consumer(consumer)
    state = common_state_dir(consumer)
    state.mkdir(parents=True)
    (state / "state_root_migration.json").write_text(
        '{"status": "restore_failed", "backup": "/tmp/x", "sources": []}\n', encoding="utf-8"
    )
    status = state_root_migration_status(sync)
    assert status
    assert not any("恢复未完成" in item for item in status)
    assert all("state_root_migration.json" not in item for item in status)
    reports = migrate_state_root(sync, write=True)
    assert reports
    assert state_root_migration_status(sync) == []


def test_failure_auto_rollback_restores_old_layout(consumer, monkeypatch):
    sync = ctx.SyncContext.for_consumer(consumer)
    original = state_root_migration._copy_verify
    calls = 0

    def flaky(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("injected copy failure")
        original(source, target)

    monkeypatch.setattr(state_root_migration, "_copy_verify", flaky)
    with pytest.raises(ctx.SyncError, match="injected copy failure"):
        migrate_state_root(sync, write=True)
    state = common_state_dir(consumer)
    journal = state / SYNC_TRANSACTION_DIR / "transaction.json"
    assert json.loads(journal.read_text(encoding="utf-8"))["status"] == "rolled_back"
    _assert_old_layout_intact(consumer)
    _assert_no_common_state_files(state)


def test_crash_recovery_via_transaction_rollback(consumer, monkeypatch, capsys):
    sync = ctx.SyncContext.for_consumer(consumer)
    original = state_root_migration._copy_verify
    calls = 0

    def crash_on_second(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt("simulated crash")
        original(source, target)

    monkeypatch.setattr(state_root_migration, "_copy_verify", crash_on_second)
    with pytest.raises(KeyboardInterrupt):
        migrate_state_root(sync, write=True)
    monkeypatch.setattr(state_root_migration, "_copy_verify", original)

    state = common_state_dir(consumer)
    journal = state / SYNC_TRANSACTION_DIR / "transaction.json"
    crashed = json.loads(journal.read_text(encoding="utf-8"))
    assert crashed["status"] == "applying"
    assert "after" not in crashed
    assert crashed["state_paths"]

    assert cmd_transaction(Namespace(sync=sync, action="status")) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["status"] == "applying"
    assert shown["state_paths"]

    assert cmd_transaction(Namespace(sync=sync, action="rollback", confirm_restore=True)) == 0
    assert "同步事务已回滚" in capsys.readouterr().out
    assert json.loads(journal.read_text(encoding="utf-8"))["status"] == "rolled_back"
    _assert_old_layout_intact(consumer)
    _assert_no_common_state_files(state)


def test_daily_edit_after_migration_keeps_status_empty(consumer):
    sync = ctx.SyncContext.for_consumer(consumer)
    migrate_state_root(sync, write=True)
    assert state_root_migration_status(sync) == []
    state = common_state_dir(consumer)
    (state / "ledger.jsonl").write_text('{"event": "daily"}\n', encoding="utf-8")
    (state / "goal_queue.json").write_text('{"queue": []}\n', encoding="utf-8")
    write_state({"last_synced_commit": "deadbeef", "user_prompts": []}, sync)
    assert (state / "sync_state.json").is_file()
    assert not (consumer / ".repo_template/sync_state.json").exists()
    assert state_root_migration_status(sync) == []
