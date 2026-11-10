"""task.py finish / cleanup worktree 流程用例。

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


def test_finish_rejects_uncompleted_ac(git_repo):
    """finish 拦截未打勾的 AC，且零写入。"""
    begun = _task_cli(git_repo, "run-begin", "t001", "--owner-id", "owner-a")
    assert begun.returncode == 0, begun.stderr
    worktree = _worktree_path(git_repo)
    message = prepare_finish(worktree, "t001", "alpha", tick_ac=False)
    before = _git(worktree, "status", "--porcelain").stdout

    finished = finish_cli(worktree, "t001", "owner-a", message)

    assert finished.returncode != 0
    assert "存在未完成 AC（未打勾）：AC-001" in finished.stderr
    assert _git(worktree, "status", "--porcelain").stdout == before
    assert not (worktree / "docs/archive/tasks/t001_alpha").exists()

def test_cleanup_worktree_requires_clean_commit_and_is_idempotent(git_repo):
    _start(git_repo)
    identity = _reserve(git_repo, "t001")
    assert _task_cli(
        git_repo, "attempt", "terminal", "t001", *_identity_args(identity),
        "--status", "completed",
    ).returncode == 0
    # 未 finish 的 active 分支：report=done 先被同一门禁拒绝
    not_done_report = _task_cli(
        git_repo, "attempt", "report", "t001", *_identity_args(identity),
        "--status", "done",
        "--sha", _git(git_repo, "rev-parse", "HEAD").stdout.strip(),
    )
    assert not_done_report.returncode != 0
    assert "非终态" in not_done_report.stderr
    not_done = _task_cli(
        git_repo, "cleanup-worktree", "t001", *_identity_args(identity)
    )
    assert not_done.returncode != 0

    # 已 finish 且提交（分支 ref=done），但 worktree 新增脏改动：拒绝
    worktree = _worktree_path(git_repo)
    _check_ac(worktree, "t001", "alpha")
    mark_done(worktree, "t001")
    base_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    _write_handoff(worktree, "t001", "alpha", identity, base_sha)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "feat(t001): complete alpha")
    head = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    assert _task_cli(
        git_repo, "attempt", "report", "t001", *_identity_args(identity),
        "--status", "done", "--sha", head,
    ).returncode == 0
    (worktree / "dirty.txt").write_text("x", encoding="utf-8")
    dirty = _task_cli(
        git_repo, "cleanup-worktree", "t001", *_identity_args(identity)
    )
    assert dirty.returncode != 0
    assert "未提交改动" in dirty.stderr

    # 清掉脏改动后 cleanup 成功且幂等
    (worktree / "dirty.txt").unlink()
    branch = "t001_alpha"
    cleaned = _task_cli(
        git_repo, "cleanup-worktree", "t001", *_identity_args(identity)
    )
    assert cleaned.returncode == 0, cleaned.stderr
    assert not worktree.exists()
    repeated = _task_cli(
        git_repo, "cleanup-worktree", "t001", *_identity_args(identity)
    )
    assert repeated.returncode == 0
    assert "幂等" in repeated.stdout
    assert _git(git_repo, "branch", "--list", branch).stdout.strip() == branch

def test_cleanup_worktree_rejects_wrong_registered_branch(git_repo):
    _start(git_repo)
    identity = _reserve(git_repo, "t001")
    worktree = _worktree_path(git_repo)
    _check_ac(worktree, "t001", "alpha")
    mark_done(worktree, "t001")
    base_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    _write_handoff(worktree, "t001", "alpha", identity, base_sha)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "feat(t001): complete alpha")
    _terminal(git_repo, "t001", identity)
    _git(worktree, "switch", "-c", "t999_other")

    result = _task_cli(
        git_repo, "cleanup-worktree", "t001", *_identity_args(identity)
    )

    assert result.returncode != 0
    assert "登记分支" in result.stderr
    assert "预期 't001_alpha'" in result.stderr

def test_cleanup_worktree_rejects_unregistered_directory(git_repo):
    _start(git_repo)
    identity, _, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    unknown = _worktree_path(git_repo)
    unknown.mkdir()
    marker = unknown / "keep.txt"
    marker.write_text("user data\n", encoding="utf-8")

    result = _task_cli(
        git_repo, "cleanup-worktree", "t001", *_identity_args(identity)
    )

    assert result.returncode != 0
    assert "未登记为 git worktree" in result.stderr
    assert marker.read_text(encoding="utf-8") == "user data\n"

def test_cleanup_worktree_rejects_active_even_when_clean(git_repo):
    """T2：active task 有 checkpoint commit 使 worktree clean 时，cleanup 仍拒绝。"""
    _start(git_repo)
    identity = _reserve(git_repo, "t001")
    worktree = _worktree_path(git_repo)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "checkpoint")
    assert _git(worktree, "status", "--porcelain").stdout.strip() == ""
    assert _task_cli(
        git_repo, "attempt", "terminal", "t001", *_identity_args(identity),
        "--status", "completed",
    ).returncode == 0
    # 未 finish 的 active 分支：report=done 先被同一门禁拒绝，cleanup 仍拒绝
    not_done_report = _task_cli(
        git_repo, "attempt", "report", "t001", *_identity_args(identity),
        "--status", "done",
        "--sha", _git(git_repo, "rev-parse", "HEAD").stdout.strip(),
    )
    assert not_done_report.returncode != 0
    assert "非终态" in not_done_report.stderr

    result = _task_cli(
        git_repo, "cleanup-worktree", "t001", *_identity_args(identity)
    )

    assert result.returncode != 0
    assert worktree.exists()

def test_cleanup_worktree_rejects_prefix_collision_branch(git_repo):
    """cleanup 拒绝 t001_scratch 这类仅共享前缀的非 task 分支。"""
    _start(git_repo)
    identity = _reserve(git_repo, "t001")
    worktree = _worktree_path(git_repo)
    _check_ac(worktree, "t001", "alpha")
    mark_done(worktree, "t001")
    base_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    _write_handoff(worktree, "t001", "alpha", identity, base_sha)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "feat(t001): complete alpha")
    _terminal(git_repo, "t001", identity)
    _git(worktree, "switch", "-c", "t001_scratch")

    result = _task_cli(
        git_repo, "cleanup-worktree", "t001", *_identity_args(identity)
    )

    assert result.returncode != 0
    assert "存在多个本地 task 分支" in result.stderr
    assert "t001_scratch" in result.stderr
    assert worktree.exists()
