"""task.py start / worktree 激活 流程用例。

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


def test_start_keeps_main_unchanged_and_activates_only_worktree(git_repo):
    initial_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    _start(git_repo)

    assert _git(git_repo, "branch", "--show-current").stdout.strip() == "main"
    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == initial_head
    assert _git(git_repo, "status", "--porcelain").stdout.strip() == ""
    worktree = _worktree_path(git_repo)
    assert worktree.is_dir()
    assert _git(worktree, "branch", "--show-current").stdout.strip() == "t001_alpha"
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == initial_head
    assert _git(worktree, "status", "--porcelain").stdout.split() == [
        "M",
        "docs/tasks/t001_alpha/progress.md",
    ]

    main_fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/progress.md")
    worktree_fm, _ = parse_front_matter(worktree / "docs/tasks/t001_alpha/progress.md")
    assert main_fm["status"] == "backlog"
    assert main_fm["branch"] == ""
    assert worktree_fm["status"] == "active"
    assert worktree_fm["branch"] == "t001_alpha"
    assert worktree_fm["worktree"] == f"../{git_repo.name}_t001"
    assert worktree_fm["diff_anchor"] == initial_head
    assert not (git_repo / "docs/tasks_index.json").exists()
    assert not (git_repo / "docs/archive/tasks_index.json").exists()

def test_start_rejects_non_primary_branch(git_repo):
    _git(git_repo, "switch", "-c", "feature")

    with pytest.raises(SystemExit, match="主干"):
        _start(git_repo)

    assert not _worktree_path(git_repo).exists()

def test_start_ignores_dirty_primary_worktree(git_repo):
    (git_repo / "unrelated.txt").write_text("dirty", encoding="utf-8")

    _start(git_repo)

    worktree = _worktree_path(git_repo)
    assert worktree.is_dir()
    assert not (worktree / "unrelated.txt").exists()
    assert (git_repo / "unrelated.txt").read_text(encoding="utf-8") == "dirty"
    worktree_fm, _ = parse_front_matter(worktree / "docs/tasks/t001_alpha/progress.md")
    assert worktree_fm["status"] == "active"

def test_start_rejects_existing_task_branch(git_repo):
    _git(git_repo, "branch", "t001_alpha")

    with pytest.raises(SystemExit, match="分支.*已存在"):
        _start(git_repo)

    assert not _worktree_path(git_repo).exists()

def test_start_rejects_existing_worktree_path(git_repo):
    _worktree_path(git_repo).mkdir()

    with pytest.raises(SystemExit, match="已存在"):
        _start(git_repo)

def test_start_rejects_task_worktree(git_repo):
    _start(git_repo)
    result = _task_cli(_worktree_path(git_repo), "start", "t002")

    assert result.returncode != 0
    assert "主工作区" in result.stderr

def test_list_from_task_worktree_reads_own_snapshot(git_repo):
    """A14：list 只读权威文件；在 task worktree 中读取该 worktree 自己的快照。"""
    _start(git_repo)
    result = _task_cli(_worktree_path(git_repo), "list")

    assert result.returncode == 0, result.stderr
    assert "t001" in result.stdout
    assert not (_worktree_path(git_repo) / "docs/tasks_index.json").exists()

def test_start_compensates_when_worktree_creation_fails(git_repo, monkeypatch):
    initial_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    def fail_create(*args, **kwargs):
        raise ctx.TaskDataError("模拟 worktree 创建失败")

    monkeypatch.setattr(integration, "create_worktree", fail_create)

    with pytest.raises(SystemExit, match="主仓.*未修改"):
        _start(git_repo)

    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == initial_head
    assert _git(git_repo, "status", "--porcelain").stdout.strip() == ""
    assert not _worktree_path(git_repo).exists()
    assert _git(git_repo, "branch", "--list", "t001_alpha").stdout.strip() == ""

def test_start_compensates_when_local_config_link_fails(git_repo, monkeypatch):
    initial_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    def fail_link(*args, **kwargs):
        raise OSError("模拟本地配置软链失败")

    monkeypatch.setattr(integration, "link_local_files", fail_link)

    with pytest.raises(SystemExit, match="主仓.*未修改"):
        _start(git_repo)

    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == initial_head
    assert _git(git_repo, "status", "--porcelain").stdout.strip() == ""
    assert not _worktree_path(git_repo).exists()
    assert _git(git_repo, "branch", "--list", "t001_alpha").stdout.strip() == ""

def test_start_always_forks_from_current_main_head(git_repo):
    main_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _start(git_repo, "t001")
    _start(git_repo, "t002")

    for tid, slug in (("t001", "alpha"), ("t002", "beta")):
        worktree = _worktree_path(git_repo, tid)
        fm, _ = parse_front_matter(worktree / f"docs/tasks/{tid}_{slug}/progress.md")
        assert fm["status"] == "active"
        assert fm["diff_anchor"] == main_head
        assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == main_head
    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == main_head

def test_start_picks_up_main_advanced_between_starts(git_repo):
    _start(git_repo, "t001")
    (git_repo / "parallel.txt").write_text("main advanced", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "chore: parallel work on main")
    advanced = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    _start(git_repo, "t002")

    worktree = _worktree_path(git_repo, "t002")
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == advanced
    assert (worktree / "parallel.txt").is_file()

def test_start_rejects_when_primary_head_differs_from_main(git_repo):
    _git(git_repo, "checkout", "-b", "feature")

    with pytest.raises(SystemExit, match="主干"):
        _start(git_repo, "t002")

def test_list_and_show_read_completed_state_from_branch(git_repo):
    _start(git_repo, "t001")
    _, branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")

    main_show = _task_cli(git_repo, "show", "t001")
    ref_show = _task_cli(git_repo, "show", "t001", "--ref", branch)
    ref_list = _task_cli(git_repo, "list", "--ref", branch, "--status", "done")

    assert "status" in main_show.stdout and "backlog" in main_show.stdout
    assert "progress_md" in main_show.stdout and "task_md" in main_show.stdout
    assert "task.md" in main_show.stdout and "progress.md" in main_show.stdout
    assert ref_show.returncode == 0 and "done" in ref_show.stdout
    assert "source_ref" in ref_show.stdout
    assert "task.md" in ref_show.stdout
    assert ref_list.returncode == 0 and "t001" in ref_list.stdout

@pytest.mark.parametrize('partial', ['branch', 'worktree'])
def test_start_timeout_reports_unconfirmed_resources_without_deleting_them(git_repo, monkeypatch, partial):
    from repo_task import worktrees
    original_git = worktrees._git

    def interrupted_add(args, **kwargs):
        if args[:2] != ['worktree', 'add']:
            return original_git(args, **kwargs)
        if partial == 'branch':
            original_git(['branch', args[3], args[-1]], **kwargs)
        else:
            assert original_git(args, **kwargs).returncode == 0
        raise ctx.TaskDataError('模拟 worktree add 超时')

    monkeypatch.setattr(worktrees, '_git', interrupted_add)
    head = _git(git_repo, 'rev-parse', 'HEAD').stdout.strip()
    with pytest.raises(SystemExit, match='创建结果未确认'):
        integration.cmd_start(argparse.Namespace(tid='t001', base=None, repo=ctx.RepoContext.for_root(git_repo)))
    assert _git(git_repo, 'rev-parse', 'HEAD').stdout.strip() == head
    assert _git(git_repo, 'rev-parse', '--verify', 'refs/heads/t001_alpha').stdout.strip() == head
    assert _worktree_path(git_repo, 't001').exists() == (partial == 'worktree')
    assert parse_front_matter(git_repo / 'docs/tasks/t001_alpha/progress.md')[0]['status'] == 'backlog'

def test_start_failure_does_not_remove_preexisting_worktree(git_repo):
    """验证并发/重复 start 失败时绝不移除已存在会话的合法 worktree 和分支（Issue #32）。"""
    # 会话 A 成功启动
    r1 = _task_cli(git_repo, "start", "t001")
    assert r1.returncode == 0
    wt1 = _worktree_path(git_repo, "t001")
    assert wt1.is_dir()

    # 会话 B 尝试再次启动同一 task，预期因 worktree/分支已存在而失败
    r2 = _task_cli(git_repo, "start", "t001")
    assert r2.returncode != 0

    # 会话 A 的 worktree 和分支必须完好保留
    assert wt1.is_dir()
    branch_ref = _git(git_repo, "rev-parse", "--verify", "refs/heads/t001_alpha")
    assert branch_ref.returncode == 0
