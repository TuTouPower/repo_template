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
