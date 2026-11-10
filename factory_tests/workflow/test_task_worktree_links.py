"""task.py local 文件链接与 worktree 清理 流程用例。

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


def test_link_local_files_excludes_foreign_and_worktree_envs(git_repo):
    """验证 link_local_files 排除已存在 worktree、artifacts、scratch 等目录内的 .env（Issue #46）。"""
    from repo_task import worktrees

    # 1. 主仓根目录合法 .env
    root_env = git_repo / ".env"
    root_env.write_text("ROOT=1\n")

    # 2. 其它目录中的无关 .env
    (git_repo / ".scratch").mkdir(parents=True, exist_ok=True)
    (git_repo / ".scratch/.env").write_text("SCRATCH=1\n")
    (git_repo / "artifacts").mkdir(parents=True, exist_ok=True)
    (git_repo / "artifacts/.env").write_text("ARTIFACTS=1\n")
    (git_repo / "docs").mkdir(parents=True, exist_ok=True)
    (git_repo / "docs/.env").write_text("DOCS=1\n")

    # 3. 其它已登记 worktree 内部的 .env
    other_wt = git_repo / "worktrees/t001_alpha"
    other_wt.mkdir(parents=True, exist_ok=True)
    (other_wt / ".env").write_text("OTHER_WT=1\n")

    new_wt = git_repo / "worktrees/t002_beta"
    new_wt.mkdir(parents=True, exist_ok=True)

    linked = worktrees.link_local_files(new_wt, repo=ctx.RepoContext.for_root(git_repo))

    # 只有根目录的 .env 被链入
    assert linked == [".env"]
    assert (new_wt / ".env").is_symlink()
    assert not (new_wt / ".scratch/.env").exists()
    assert not (new_wt / "artifacts/.env").exists()
    assert not (new_wt / "worktrees/t001_alpha/.env").exists()

def test_link_local_files_skips_private_local_dir(git_repo):
    """仓库根 local/ 是用户私有目录：其中备份的 .env 不得被默认 .env 扫描链入 worktree。"""
    from repo_task import worktrees

    (git_repo / ".env").write_text("ROOT=1\n")
    (git_repo / "local/backup").mkdir(parents=True)
    (git_repo / "local/backup/.env").write_text("BACKUP=1\n")
    new_wt = git_repo / "worktrees/t002_beta"
    new_wt.mkdir(parents=True, exist_ok=True)

    linked = worktrees.link_local_files(new_wt, repo=ctx.RepoContext.for_root(git_repo))

    assert linked == [".env"]
    assert not (new_wt / "local").exists()

def test_start_links_worktree_include_ignored_paths(git_repo):
    """.worktreeinclude 命中的 gitignore 文件与整目录软链进 worktree；
    未被忽略的未跟踪文件、控制面目录与已入库文件不链接。"""
    _write_worktree_include(git_repo)

    _start(git_repo)

    wt = _worktree_path(git_repo)
    for rel in (".env.local", "config/local.yaml", "data"):
        assert (wt / rel).is_symlink(), rel
    assert (wt / "data/db/state.db").read_text(encoding="utf-8") == "rows\n"
    assert not (wt / "notes.txt").exists()
    assert (wt / "docs/runtime").is_symlink()
    assert (wt / "docs/runtime/probe.json").read_text(encoding="utf-8") == "{}\n"
    assert not (wt / "config/app.yaml").is_symlink()
    # 目录软链不匹配 `/data/` 这类仅目录规则；须仍被忽略，不能被 git add 误暂存。
    status = _git(wt, "status", "--porcelain").stdout.splitlines()
    assert status == [" M docs/tasks/t001_alpha/progress.md"]
    # 托管 exclude 只补软链路径：主仓未被忽略的在途文件仍可见。
    assert _git(git_repo, "status", "--porcelain").stdout.splitlines() == ["?? notes.txt"]
    common = Path(_git(git_repo, "rev-parse", "--git-common-dir").stdout.strip())
    exclude = (common if common.is_absolute() else git_repo / common) / "info/exclude"
    assert "/data" in exclude.read_text(encoding="utf-8").splitlines()

def test_worktree_removal_keeps_linked_primary_data(git_repo):
    """清理 worktree 只删软链本身，主仓被链接的目录与文件完好。"""
    from repo_task import worktrees

    _write_worktree_include(git_repo)
    _start(git_repo)
    wt = _worktree_path(git_repo)
    repo_ctx = ctx.RepoContext.for_root(git_repo)
    rel = repo_ctx.worktree_rel("t001")
    _git(wt, "commit", "-am", "active")

    removed, message = worktrees.remove_worktree(
        rel, expected_branch="t001_alpha", repo=repo_ctx, tid="t001",
    )

    assert removed, message
    assert not wt.exists()
    assert (git_repo / "data/db/state.db").read_text(encoding="utf-8") == "rows\n"
    assert (git_repo / ".env.local").read_text(encoding="utf-8") == "LOCAL=1\n"

def test_forced_worktree_discard_does_not_follow_unmanaged_dir_link(git_repo):
    """即便遗留非托管目录软链，强制丢弃 worktree 也不穿透删除主仓目标。"""
    from repo_task import worktrees

    _write_worktree_include(git_repo)
    _start(git_repo)
    wt = _worktree_path(git_repo)
    (wt / "extra_data").symlink_to(git_repo / "data")

    repo_ctx = ctx.RepoContext.for_root(git_repo)
    removed, message = worktrees.discard_worktree(
        repo_ctx.worktree_rel("t001"), repo=repo_ctx, tid="t001",
    )

    assert removed, message
    assert not wt.exists()
    assert (git_repo / "data/db/state.db").read_text(encoding="utf-8") == "rows\n"
