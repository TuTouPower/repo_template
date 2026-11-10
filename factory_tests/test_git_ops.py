"""git_ops 主干探测失败必须显式报错（F08）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from git_repo_support import git_run
from repo_task import context as ctx
from repo_task.git_ops import default_branch


def test_default_branch_fails_with_tried_sources_when_only_task_branch_exists(
    tmp_path: Path,
) -> None:
    """只有 task 分支且无任何主干配置时，报错须列出探测来源与修复命令。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    git_run(repo, "init", "-b", "t001_only")
    # 覆盖宿主全局 init.defaultBranch，确保本用例只保留 task 分支。
    git_run(repo, "config", "init.defaultBranch", "")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    git_run(repo, "add", "README.md")
    git_run(repo, "commit", "-m", "init")

    with pytest.raises(ctx.TaskDataError) as excinfo:
        default_branch(root=repo)

    message = str(excinfo.value)
    assert "无法确定主干分支" in message
    assert "origin/HEAD" in message
    assert "init.defaultBranch" in message
    assert "git remote set-head origin --auto" in message
    assert "git config init.defaultBranch <name>" in message


def test_default_branch_uses_unborn_head(tmp_path: Path) -> None:
    """仅 git init、尚无 commit 时，未出生 HEAD 可作为主干。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    git_run(repo, "init", "-b", "main")
    git_run(repo, "config", "init.defaultBranch", "")
    assert default_branch(root=repo) == "main"


def test_default_branch_does_not_use_current_feature_branch(tmp_path: Path) -> None:
    """HEAD 已出生但存在多个非 task 分支时，不得把当前 feature 分支当主干。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    git_run(repo, "init", "-b", "trunk")
    git_run(repo, "config", "init.defaultBranch", "")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    git_run(repo, "add", "README.md")
    git_run(repo, "commit", "-m", "init")
    git_run(repo, "branch", "dev")
    git_run(repo, "branch", "feature-x")
    git_run(repo, "checkout", "feature-x")
    with pytest.raises(ctx.TaskDataError, match="无法确定主干分支") as excinfo:
        default_branch(root=repo)
    assert "feature-x" not in str(excinfo.value)
