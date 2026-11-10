"""task.py rewind 回退 流程用例。

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
    git_repo, state_file,
)


def test_rewind_discards_uncommitted_activation_and_removes_empty_branch(git_repo):
    _start(git_repo)
    worktree = _worktree_path(git_repo)

    lifecycle.cmd_rewind(
        argparse.Namespace(
            tid="t001", to="backlog", reason="撤回", yes=True,
            repo=ctx.RepoContext.for_root(git_repo),
        )
    )

    assert not worktree.exists()
    assert _git(git_repo, "branch", "--list", "t001_alpha").stdout.strip() == ""
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/progress.md")
    assert fm["status"] == "backlog"
    assert fm["worktree"] == ""
    assert "rewound: effective=active -> backlog" in fm["note"]
    assert "main 记录为 backlog" in fm["note"]

def test_rewind_keeps_branch_with_own_commits_and_guides(git_repo, capsys):
    """T1：rewind 后分支有 task commit 时保留分支并输出恢复指引。"""
    _start(git_repo)
    worktree = _worktree_path(git_repo)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "checkpoint")

    _rewind(git_repo)

    # 分支保留（own_commits=True，不删除）
    assert _git(git_repo, "branch", "--list", "t001_alpha").stdout.strip() == "t001_alpha"
    assert not worktree.exists()
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/progress.md")
    assert fm["status"] == "backlog"
    assert fm["branch"] == ""

def test_rewind_without_yes_warns_branch_kept_and_recovery(git_repo, monkeypatch, capsys):
    """rewind 交互确认时输出分支保留与恢复指引。"""
    _start(git_repo)
    worktree = _worktree_path(git_repo)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "checkpoint")
    monkeypatch.setattr("builtins.input", lambda: "y")

    _rewind(git_repo, yes=False)

    err = capsys.readouterr().err
    assert "将保留" in err
    assert "git branch -D t001_alpha" in err
    assert "git worktree add" in err

def test_rewind_backlog_not_covered_by_stale_branch(git_repo):
    """A31：rewind 保留分支后，view 读到 main 的 backlog，而非旧分支 active。"""
    _start(git_repo)
    worktree = _worktree_path(git_repo)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "checkpoint")
    _rewind(git_repo, "t001")
    # 分支保留（own commit），main 已显式回 backlog
    assert _git(git_repo, "branch", "--list", "t001_alpha").stdout.strip() == "t001_alpha"
    assert not worktree.exists()

    result = _task_cli(git_repo, "view")

    assert result.returncode == 0, result.stderr
    assert "[运行中] active 0" in result.stdout
    assert "t001  alpha" in result.stdout

def test_rewind_rejects_foreign_registered_branch(git_repo):
    """rewind 拒绝强制删除登记为其他分支的 worktree。"""
    _start(git_repo)
    worktree = _worktree_path(git_repo)
    # 把 worktree 切到另一个分支（模拟路径被其他分支占用）
    _git(worktree, "switch", "-c", "t999_other")

    with pytest.raises(SystemExit, match="不符"):
        _rewind(git_repo)

    assert worktree.exists()

def test_rewind_audit_failure_persists_trace_and_reports_real_state(git_repo, monkeypatch):
    """审计失败时按实际状态提示，并把结果写入 note 以便追溯（Issue #76）。"""
    _start(git_repo, "t001")

    def fail_audit(*args, **kwargs):
        raise ctx.TaskDataError("audit unavailable")

    monkeypatch.setattr(lifecycle, "append_audit", fail_audit)
    with pytest.raises(SystemExit, match="审计写入失败"):
        lifecycle.cmd_rewind(
            argparse.Namespace(
                tid="t001", to=None, reason="audit failure", yes=True,
                repo=ctx.RepoContext.for_root(git_repo),
            )
        )
    note = git_repo / "docs/tasks/t001_alpha/progress.md"
    fm, body = parse_front_matter(note)
    assert fm["status"] == "backlog"
    assert "rewind audit failed" in fm["note"]
    assert not (git_repo / "worktrees/t001_alpha").exists()

def test_rewind_write_failure_appends_failed_audit_marker(git_repo, monkeypatch):
    """验证 rewind 状态写入失败时记录 rewind_failed 审计标记，权威状态与审计不分叉（Issue #36）。"""
    from repo_task import lifecycle
    import argparse

    _start(git_repo, "t001")
    audit_file = state_file(git_repo, "tasks_audit.log")

    def fail_write(*args, **kwargs):
        raise OSError("injected disk failure")

    monkeypatch.setattr(lifecycle, "write_front_matter", fail_write)

    # 尝试 rewind，预期失败退出
    with pytest.raises(SystemExit, match="rewind 状态写入失败"):
        lifecycle.cmd_rewind(argparse.Namespace(
            tid="t001", to=None, reason="test failure", yes=True,
            repo=ctx.RepoContext.for_root(git_repo),
        ))

    audit_text = audit_file.read_text(encoding="utf-8")
    assert "rewind_failed" in audit_text
    assert "injected disk failure" in audit_text
