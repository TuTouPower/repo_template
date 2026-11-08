"""purge 拒绝护栏的真实 git 测试（Issue #82）。"""

import argparse
import sys
from pathlib import Path

import pytest

from test_task_start_flow import git_repo, _git, _task_cli
from repo_task import context as ctx
from repo_task import lifecycle
from repo_task.documents import parse_front_matter, write_front_matter


def _task_dir(repo: Path, tid: str) -> Path:
    return next((repo / "docs/tasks").glob(f"{tid}_*"))


def test_purge_rejects_scheduled_reference(git_repo):
    result = _task_cli(git_repo, "edit", "t002", "--depends-on", "t001")
    assert result.returncode == 0, result.stderr
    target = _task_dir(git_repo, "t001")
    result = _task_cli(git_repo, "purge", "t001", "--reason", "miscreated")
    assert result.returncode != 0
    assert "仍被调度图引用" in result.stderr
    assert target.exists()


def test_purge_rejects_tracked_task_dir(git_repo):
    target = _task_dir(git_repo, "t001")
    result = _task_cli(git_repo, "purge", "t001", "--reason", "miscreated")
    assert result.returncode != 0
    assert "已被任一分支跟踪" in result.stderr
    assert (git_repo / "docs/tasks/t001_alpha").exists()
    assert target.exists()


def test_purge_rejects_branch_with_unmerged_commit(git_repo):
    added = _task_cli(git_repo, "add", "--title", "delta", "--slug", "delta")
    assert added.returncode == 0, added.stderr
    tid = "t004"
    target = _task_dir(git_repo, tid)
    note = target / "progress.md"
    fm, body = parse_front_matter(note)
    fm["branch"] = "t004_delta"
    write_front_matter(note, fm, body)

    _git(git_repo, "checkout", "-b", "t004_delta")
    (git_repo / "unmerged.txt").write_text("x\n", encoding="utf-8")
    _git(git_repo, "add", "unmerged.txt")
    _git(git_repo, "commit", "-m", "unmerged work")
    _git(git_repo, "checkout", "main")

    result = _task_cli(git_repo, "purge", tid, "--reason", "miscreated")
    assert result.returncode != 0
    assert "有未合并 commit" in result.stderr
    assert target.exists()


def test_purge_rejects_worktree_removal_failure(git_repo, monkeypatch):
    added = _task_cli(git_repo, "add", "--title", "epsilon", "--slug", "epsilon")
    assert added.returncode == 0, added.stderr
    tid = "t004"
    target = _task_dir(git_repo, tid)
    monkeypatch.setattr(lifecycle, "remove_worktree", lambda *a, **k: (False, "simulated worktree busy"))
    with pytest.raises(SystemExit, match="worktree 未清理"):
        lifecycle.cmd_purge(argparse.Namespace(
            tid=tid, reason="miscreated", repo=ctx.RepoContext.for_root(git_repo),
        ))
    assert target.exists()


def test_purge_rejects_non_main_branch(git_repo):
    _git(git_repo, "checkout", "-b", "feature")
    target = _task_dir(git_repo, "t001")
    result = _task_cli(git_repo, "purge", "t001", "--reason", "miscreated")
    assert result.returncode != 0
    assert "只能在主干" in result.stderr
    assert target.exists()
