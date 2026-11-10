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

from git_repo_support import state_dir, state_file
from repo_task.context import common_state_dir
from template_sync import context as ctx
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
    (runtime / "tasks_audit.log").write_text("audit\n", encoding="utf-8")
    (runtime / "goal_queue.json").write_text('{"queue": ["t001"]}\n', encoding="utf-8")
    (runtime / "statusline_workdir.jsonl").write_text("{}\n", encoding="utf-8")
    (root / ".repo_template").mkdir()
    (root / ".repo_template/sync_state.json").write_text('{"template_source": {"kind": "path", "value": "x"}}\n', encoding="utf-8")
    (root / ".scratch/repo_sync_rollback").mkdir(parents=True)
    (root / ".scratch/repo_sync_rollback/transaction.json").write_text('{"status": "accepted"}\n', encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    return root


def test_dry_run_writes_nothing(consumer):
    sync = ctx.SyncContext.for_consumer(consumer)
    reports = migrate_state_root(sync)
    assert reports
    assert (consumer / "docs/runtime/dispatch_ledger.jsonl").is_file()
    assert not state_file(consumer, "ledger.jsonl").exists()


def test_write_moves_state_and_preserves_bytes(consumer):
    sync = ctx.SyncContext.for_consumer(consumer)
    assert state_root_migration_status(sync)
    migrate_state_root(sync, write=True)
    state = common_state_dir(consumer)
    assert (state / "ledger.jsonl").read_text(encoding="utf-8") == '{"event": "start"}\n'
    assert (state / "tasks_audit.log").read_text(encoding="utf-8") == "audit\n"
    assert (state / "sync_transaction/transaction.json").is_file()
    assert not (consumer / "docs/runtime").exists()
    assert not (consumer / ".repo_template/sync_state.json").exists()
    assert not state_root_migration_status(sync)


def test_registered_worktree_blocks(consumer, tmp_path):
    worktree = tmp_path / "wt"
    _git(consumer, "worktree", "add", "-b", "wt", str(worktree))
    sync = ctx.SyncContext.for_consumer(consumer)
    with pytest.raises(ctx.SyncError, match="worktree"):
        migrate_state_root(sync, write=True)
    assert (consumer / "docs/runtime/dispatch_ledger.jsonl").is_file()
