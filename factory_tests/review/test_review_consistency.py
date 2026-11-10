"""2026-11-09 合并审阅回归：入口一致性、迁移模型与事务恢复。"""
from __future__ import annotations

import json
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

from git_repo_support import get_controlled_git_env, git_run
from git_repo_support import state_file
from repo_task import commit_txn, privacy, statusline
from repo_task.context import RepoContext, TaskDataError
from repo_task.monitoring import git_tree_entries, git_worktree_entries, review_scope_fingerprint, validate_task_core_asset_changes
from template_sync.context import SyncContext
from template_sync.entity_storage_migration import (
    MigrationError, entity_storage_migration_status, migrate_entity_storage,
)


SCRIPTS = FACTORY_ROOT / "repo/.repo_template/scripts"


@pytest.fixture
def consumer(tmp_path, monkeypatch):
    import os
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    for key, value in get_controlled_git_env().items():
        if key.startswith("GIT_"):
            monkeypatch.setenv(key, value)
    root = tmp_path / "consumer"
    root.mkdir()
    git_run(root, "init", "-b", "main")
    git_run(root, "config", "user.name", "review")
    git_run(root, "config", "user.email", "review@example.com")
    (root / ".gitignore").write_text(".scratch/\n")
    (root / ".repo_template").mkdir()
    (root / ".repo_template/entity_schema.json").write_text('{"version": 1}\n')
    git_run(root, "add", "-A")
    git_run(root, "commit", "-m", "initial")
    return root


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be", "utf-16", "utf-32"])
def test_staged_and_commit_use_identical_multiline_privacy_policy(consumer, encoding):
    token = "/".join(("", "Users", "private_user", "project"))
    (consumer / "wide.txt").write_bytes(("header\n" + token + "\n").encode(encoding))
    git_run(consumer, "add", "wide.txt")
    staged = privacy.scan_privacy_changes(consumer)
    assert any("wide.txt:2:" in item for item in staged)
    git_run(consumer, "commit", "-m", "wide text")
    assert privacy.scan_commit_introduced_privacy(consumer, "HEAD") == staged
    assert privacy.scan_privacy_changes(consumer, base="HEAD^", target="HEAD^{tree}") == staged


def test_real_precommit_allows_unchanged_historical_leak(consumer):
    token = "/".join(("", "home", "old_user", "project"))
    path = consumer / "document.txt"
    path.write_text(token + "\nold line\n")
    git_run(consumer, "add", "document.txt")
    git_run(consumer, "commit", "-m", "historical content")
    git_run(consumer, "config", "core.hooksPath", str(SCRIPTS.parent / "hooks"))
    path.write_text(token + "\nnew clean line\n")
    git_run(consumer, "add", "document.txt")
    assert privacy.scan_privacy_changes(consumer) == []
    git_run(consumer, "commit", "-m", "unrelated maintenance")
    assert privacy.scan_commit_introduced_privacy(consumer, "HEAD") == []
    assert privacy.scan_git_tree_privacy(consumer, "HEAD")


def test_unknown_binary_uses_same_policy_for_index_tree_and_tip(consumer):
    (consumer / "payload.npy").write_bytes(bytes(range(256)) * 4)
    git_run(consumer, "add", "payload.npy")
    assert privacy.scan_privacy_changes(consumer) == []
    git_run(consumer, "commit", "-m", "binary data")
    assert privacy.scan_commit_introduced_privacy(consumer, "HEAD") == []
    assert privacy.scan_privacy_changes(consumer, base="HEAD^", target="HEAD^{tree}") == []


def test_old_active_spike_alone_requires_and_completes_migration(consumer):
    (consumer / ".repo_template/entity_schema.json").unlink()
    report = consumer / "docs/spikes/s001_sample/report.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Active spike\n\nStill investigating.\n")
    git_run(consumer, "add", "-A")
    git_run(consumer, "commit", "-m", "legacy spike")
    sync = SyncContext.for_consumer(consumer)
    assert entity_storage_migration_status(sync)
    migrate_entity_storage(sync, write=True)
    assert report.read_text().startswith("---\n")
    assert entity_storage_migration_status(sync) == []
    assert migrate_entity_storage(sync, write=True) == []


@pytest.mark.parametrize("field,value", [("tid", "t999"), ("slug", "other")])
def test_migration_rejects_archived_task_identity_mismatch(consumer, field, value):
    source = consumer / "docs/archive/tasks/t001_alpha"
    source.mkdir(parents=True)
    fm = {"tid": "t001", "slug": "alpha", "status": "done"}
    fm[field] = value
    from repo_task.documents import dump_front_matter
    (source / "progress.md").write_text(dump_front_matter(fm) + "\n# Progress\n")
    git_run(consumer, "add", "-A")
    git_run(consumer, "commit", "-m", "invalid archive")
    with pytest.raises(MigrationError, match="目录名与 front matter 不符"):
        migrate_entity_storage(SyncContext.for_consumer(consumer), write=True)
    assert source.is_dir()
    assert not (consumer / "docs/tasks/t001_alpha").exists()


def _begin(root):
    task = root / "docs/tasks/t001_alpha"
    task.mkdir(parents=True)
    (task / "progress.md").write_text("active\n")
    git_run(root, "add", "-A")
    git_run(root, "commit", "-m", "active task")
    head = git_run(root, "rev-parse", "HEAD").stdout.strip()
    return commit_txn.begin(root, tid="t001", attempt=1, execution_id="exec-1", branch="main",
                            diff_anchor=head, review_scope="0" * 16,
                            task_rel="docs/tasks/t001_alpha", message_file=".scratch/message.txt")


def test_own_partial_finish_write_is_recoverable(consumer, monkeypatch):
    txn = _begin(consumer)
    commit_txn.write_file(consumer, txn, "progress.md", "done\n")
    original_write = commit_txn.atomic_write_text

    def fail_handoff(path, text):
        if Path(path).name == "handoff.json":
            raise OSError("handoff write failed")
        original_write(path, text)

    monkeypatch.setattr(commit_txn, "atomic_write_text", fail_handoff)
    with pytest.raises(OSError):
        commit_txn.write_file(consumer, txn, "handoff.json", "{}\n")
    persisted = commit_txn.load(consumer)
    assert commit_txn.judge(consumer, persisted)[0] == "rollback_safe"
    assert commit_txn.rollback(consumer, persisted) == []
    assert (consumer / txn["task_rel"] / "progress.md").read_text() == "active\n"
    assert not (consumer / txn["task_rel"] / "handoff.json").exists()


@pytest.mark.parametrize("phase", ["prepared", "writing", "staging"])
def test_external_commit_before_commit_phase_is_not_claimed(consumer, phase):
    txn = _begin(consumer)
    commit_txn.mark_phase(consumer, txn, phase)
    (consumer / "external.txt").write_text("external work\n")
    git_run(consumer, "add", "external.txt")
    git_run(consumer, "commit", "-m", "external")
    assert commit_txn.judge(consumer, txn)[0] == "unknown"
    assert commit_txn.load(consumer) is not None
    with pytest.raises(TaskDataError):
        commit_txn.rollback(consumer, txn)


def test_worktree_core_asset_hash_uses_git_eol_normalization(consumer):
    task = consumer / "docs/tasks/t002_other"
    task.mkdir(parents=True)
    path = task / "task.md"
    path.write_bytes(b"first\nsecond\n")
    git_run(consumer, "add", "-A")
    git_run(consumer, "commit", "-m", "other task")
    git_run(consumer, "config", "core.autocrlf", "true")
    path.write_bytes(b"first\r\nsecond\r\n")
    repo = RepoContext.for_root(consumer)
    before = git_tree_entries("HEAD", repo=repo)
    after = git_worktree_entries(consumer, repo=repo)
    validate_task_core_asset_changes(before, after, {"t001_alpha"})
    assert review_scope_fingerprint("HEAD", "docs/tasks/t001_alpha", repo=repo) == review_scope_fingerprint("HEAD", "docs/tasks/t001_alpha", ref="HEAD", repo=repo)


def test_statusline_host_session_is_distinct_from_execution_owner(tmp_path):
    project = tmp_path / "project"
    worktree = tmp_path / "project_t001"
    worktree.mkdir()
    ledger = state_file(project, "ledger.jsonl")
    events = [
        {"tid": "t001", "event": "start", "worktree": "../project_t001"},
        {"tid": "t001", "event": "attempt_reserved", "attempt": 1, "execution_id": "exec-1"},
        {"tid": "t001", "event": "execution_claimed", "attempt": 1, "execution_id": "exec-1", "owner_id": "worker-owner"},
    ]
    ledger.write_text("".join(json.dumps(event) + "\n" for event in events))
    assert statusline.bind(str(project), "host-session", "t001", 1, "exec-1", owner_id="worker-owner")
    assert statusline.current_worktree(str(project), "host-session") == str(worktree)
    assert statusline.current_worktree(str(project), "worker-owner") == ""


def test_native_merge_hook_rejects_source_without_current_protocol(consumer):
    git_run(consumer, "checkout", "-b", "historical")
    (consumer / ".repo_template/entity_schema.json").unlink()
    (consumer / "historical.txt").write_text("historical branch\n")
    git_run(consumer, "add", "-A")
    git_run(consumer, "commit", "-m", "historical protocol")
    git_run(consumer, "checkout", "main")
    (consumer / "current.txt").write_text("current branch\n")
    git_run(consumer, "add", "current.txt")
    git_run(consumer, "commit", "-m", "current protocol")
    before = git_run(consumer, "rev-parse", "HEAD").stdout
    git_run(consumer, "config", "core.hooksPath", str(SCRIPTS.parent / "hooks"))
    result = git_run(consumer, "merge", "--no-ff", "historical", check=False)
    assert result.returncode != 0
    assert "未完成实体协议切换" in result.stderr
    assert git_run(consumer, "rev-parse", "HEAD").stdout == before


def test_final_snapshot_rejects_duplicate_entry_ids(consumer):
    from _id_scan import IdScanError, validate_snapshot_ids
    entries = consumer / "docs/pending"
    entries.mkdir(parents=True)
    (entries / "p001_first.md").write_text("first\n")
    (entries / "p001_second.md").write_text("second\n")
    git_run(consumer, "add", "-A")
    with pytest.raises(IdScanError, match="编号重复"):
        validate_snapshot_ids(consumer)


def test_dependency_git_failure_preserves_cause(consumer, monkeypatch):
    import subprocess
    from repo_task import integration
    from repo_task.documents import dump_front_matter
    task = consumer / "docs/tasks/t001_alpha"
    task.mkdir(parents=True)
    base = git_run(consumer, "rev-parse", "HEAD").stdout.strip()
    (task / "progress.md").write_text(dump_front_matter({"tid": "t001", "slug": "alpha", "status": "done"}))
    receipt = {
        "tid": "t001", "attempt": 1, "execution_id": "exec-1", "status": "done", "branch": "t001_alpha",
        "base_sha": base, "spec_source_sha": base, "tests": "passed", "blackbox": "passed", "review": "passed",
        "review_scope": "0" * 16, "pending": [], "findings": [],
    }
    (task / "handoff.json").write_text(json.dumps(receipt))
    git_run(consumer, "add", "-A")
    git_run(consumer, "commit", "-m", "done dependency")
    original = integration._git

    def fail_log(args, **kwargs):
        if args[0] == "log":
            return subprocess.CompletedProcess(args, 128, "", "object database unavailable")
        return original(args, **kwargs)

    monkeypatch.setattr(integration, "_git", fail_log)
    with pytest.raises(TaskDataError, match="object database unavailable"):
        integration._dependency_implementations("t001", repo=RepoContext.for_root(consumer))
