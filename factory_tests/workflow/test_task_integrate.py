"""task.py integrate / chain 合并 流程用例。

从 test_task_start_flow 拆分；fixture 与 helper 仍由 test_task_start_flow 提供。"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT
from types import SimpleNamespace

import pytest


SCRIPTS_DIR = FACTORY_ROOT / "repo/.repo_template/scripts"
TASK_TEMPLATE_DIR = SCRIPTS_DIR.parent / "docs" / "task_template"
sys.path.insert(0, str(SCRIPTS_DIR))

from review_support import ensure_review_evidence, configure_project_verification, valid_spec
from task_flow_support import mark_done, finish_cli, prepare_finish

from repo_task import context as ctx
from repo_task import attempts, control, integration, lifecycle, store
from repo_task.monitoring import review_scope_fingerprint
from repo_task.documents import parse_front_matter, write_front_matter


from test_task_start_flow import (
    _check_ac, _finish_commit_cleanup, _git, _handoff, _identity_args, _ledger,
    _reserve, _rewind, _set_spec, _start, _task_cli, _terminal, _valid_spec,
    _valid_task_body, _worktree_path, _write_handoff, _write_worktree_include,
    git_repo,
)


def test_prepare_merge_checks_merge_tree_capability_before_git_merge(monkeypatch):
    calls = []

    def unsupported(_branch, **_kwargs):
        raise ctx.TaskDataError('内容门禁需要 Git >= 2.38')

    def record_git(args, **_kwargs):
        calls.append(args)
        raise AssertionError('git merge must not run after capability failure')

    monkeypatch.setattr(integration, '_expected_auto_merge', unsupported)
    monkeypatch.setattr(integration, '_git', record_git)
    with pytest.raises(SystemExit, match='merge 尚未开始.*Git >= 2.38'):
        integration._prepare_native_merge(
            't001_alpha', 'merge(t001): t001_alpha', ['t001_alpha'],
            repo=ctx.RepoContext.for_root(Path.cwd()),
        )
    assert calls == []

def test_integrate_keeps_branch_when_requested(git_repo):
    _start(git_repo, "t001")
    identity, branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")

    rejected = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity), "--keep-branch"
    )
    assert rejected.returncode != 0
    assert "只在 --continue" in rejected.stderr
    prepared = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity))
    assert prepared.returncode == 0, prepared.stderr
    result = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity),
        "--continue", "--keep-branch",
    )
    assert result.returncode == 0, result.stderr
    assert _git(git_repo, "branch", "--list", branch).stdout.strip() == branch

def test_integrate_rejects_unfinished_task(git_repo):
    _start(git_repo, "t001")
    identity = _reserve(git_repo, "t001")
    assert _task_cli(
        git_repo, "attempt", "terminal", "t001", *_identity_args(identity),
        "--status", "completed",
    ).returncode == 0
    # 未 finish 的 active 分支：report=done 先被同一门禁拒绝，integrate 仍拒绝
    not_done_report = _task_cli(
        git_repo, "attempt", "report", "t001", *_identity_args(identity),
        "--status", "done",
        "--sha", _git(git_repo, "rev-parse", "HEAD").stdout.strip(),
    )
    assert not_done_report.returncode != 0
    assert "非终态" in not_done_report.stderr

    result = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity)
    )

    assert result.returncode != 0

def test_integrate_rejects_registered_worktree(git_repo):
    _start(git_repo, "t001")
    identity = _reserve(git_repo, "t001")
    worktree = _worktree_path(git_repo)
    _check_ac(worktree, "t001", "alpha")
    mark_done(worktree, "t001")
    base_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    _write_handoff(worktree, "t001", "alpha", identity, base_sha)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "feat(t001): complete alpha")
    _terminal(git_repo, "t001", identity)

    result = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity)
    )

    assert result.returncode != 0
    assert "cleanup-worktree" in result.stderr

def test_integrate_rejects_tracked_dirty_primary_worktree(git_repo):
    tracked = git_repo / "tracked.txt"
    tracked.write_text("base", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "chore: add tracked file")
    _start(git_repo, "t001")
    identity, _, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    tracked.write_text("modified", encoding="utf-8")

    result = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity)
    )

    assert result.returncode != 0
    assert "已跟踪文件未提交" in result.stderr

def test_integrate_ignores_untracked_files(git_repo):
    _start(git_repo, "t001")
    identity, _, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    (git_repo / "scratch_note.txt").write_text("untracked", encoding="utf-8")

    result = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity)
    )

    assert result.returncode == 0, result.stderr
    assert (git_repo / "scratch_note.txt").is_file()

def test_integrate_detects_merge_state_from_any_cwd(git_repo, tmp_path):
    """_merge_in_progress 用绝对 git-dir；从仓库外调用不得误判为无 merge。"""
    shared = git_repo / "shared.txt"
    shared.write_text("base\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "chore: add shared file")
    _start(git_repo, "t001")
    worktree = _worktree_path(git_repo)
    (worktree / "shared.txt").write_text("from task\n", encoding="utf-8")
    identity, _, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    shared.write_text("from main\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "chore: edit shared on main")
    assert _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity)
    ).returncode != 0

    outside = tmp_path / "outside"
    outside.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(git_repo / ".repo_template" / "scripts" / "task.py"),
            "integrate",
            "t001",
            *_identity_args(identity),
        ],
        cwd=outside,
        capture_output=True,
        text=True,
        encoding="utf-8", errors="replace",
    )

    assert result.returncode != 0
    assert "进行中的 merge" in result.stderr

def test_integrate_reports_conflict_and_continues_after_resolution(git_repo):
    shared = git_repo / "shared.txt"
    shared.write_text("base\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "chore: add shared file")

    _start(git_repo, "t001")
    worktree = _worktree_path(git_repo)
    (worktree / "shared.txt").write_text("from task\n", encoding="utf-8")
    identity, branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")

    shared.write_text("from main\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "chore: edit shared on main")

    conflicted = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity)
    )
    assert conflicted.returncode != 0
    assert "shared.txt" in conflicted.stderr
    assert "--continue" in conflicted.stderr

    premature = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity), "--continue"
    )
    assert premature.returncode != 0
    assert "未解决冲突" in premature.stderr

    shared.write_text("resolved\n", encoding="utf-8")
    _git(git_repo, "add", "shared.txt")
    resumed = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity), "--continue"
    )

    assert resumed.returncode == 0, resumed.stderr
    assert shared.read_text(encoding="utf-8") == "resolved\n"
    assert _git(git_repo, "branch", "--list", branch).stdout.strip() == ""

def test_chain_start_from_previous_completed_branch(git_repo):
    """串行：t002 从 t001 已完成分支创建，继承其成果。"""
    _start(git_repo, "t001")
    _, first_branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")

    _start(git_repo, "t002", base=first_branch)

    second = _worktree_path(git_repo, "t002")
    assert second.is_dir()
    fm, _ = parse_front_matter(second / "docs/tasks/t002_beta/progress.md")
    assert fm["status"] == "active"
    # t002 分支从 t001 分支创建，包含其 commit
    assert _git(
        second, "merge-base", "--is-ancestor", first_branch, "HEAD", check=False
    ).returncode == 0

def test_chain_integrate_rejects_mid_chain_undone(git_repo):
    """链上有未完成的 task 时拒绝合并。"""
    _start(git_repo, "t001")
    _, first_branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    _start(git_repo, "t002", base=first_branch)  # t002 active 未 finish
    _reserve(git_repo, "t002")

    result = _task_cli(git_repo, "integrate-chain", "t002")

    assert result.returncode != 0
    assert "须为 done/dropped" in result.stderr

def test_chain_integrate_rejects_mid_chain_registered_worktree(git_repo):
    """链上有 task 仍挂 worktree 时拒绝合并。"""
    _start(git_repo, "t001")
    _, first_branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    _start(git_repo, "t002", base=first_branch)
    identity = _reserve(git_repo, "t002")
    # t002 finish + handoff + commit + terminal，但不 cleanup-worktree
    worktree = _worktree_path(git_repo, "t002")
    _check_ac(worktree, "t002", "beta")
    mark_done(worktree, "t002")
    base_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    _write_handoff(worktree, "t002", "beta", identity, base_sha)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "feat(t002): beta")
    _terminal(git_repo, "t002", identity)

    result = _task_cli(git_repo, "integrate-chain", "t002")

    assert result.returncode != 0
    assert "仍登记 worktree" in result.stderr

def test_integrate_requires_primary_worktree(git_repo):
    _start(git_repo, "t001")
    identity = _reserve(git_repo, "t001")
    worktree = _worktree_path(git_repo)

    result = _task_cli(
        worktree, "integrate", "t001", *_identity_args(identity)
    )

    assert result.returncode != 0
    assert "主工作区" in result.stderr
