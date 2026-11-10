"""F01 回归：破坏性 worktree 操作强制校验 tid（工厂集成测试，不进产物）。"""

from pathlib import Path

import pytest

from test_task_start_flow import _task_cli, git_repo
from git_repo_support import git_run
from repo_task import context as ctx
from repo_task import worktrees
from repo_task.documents import parse_front_matter, write_front_matter


def _task_dir(repo: Path, tid: str) -> Path:
    return next((repo / "docs/tasks").glob(f"{tid}_*"))


def test_purge_rejects_foreign_worktree_and_branch(git_repo):
    """A 的 worktree/branch 同源指向 B 时 purge 拒绝，B 资源原样保留。"""
    added = _task_cli(git_repo, "add", "--title", "delta", "--slug", "delta")
    assert added.returncode == 0, added.stderr
    tid_a = "t004"
    target_a = _task_dir(git_repo, tid_a)

    b_rel = f"../{git_repo.name}_t002"
    b_path = git_repo.parent / f"{git_repo.name}_t002"
    git_run(git_repo, "worktree", "add", "-b", "t002_beta", b_rel)
    b_head = git_run(git_repo, "rev-parse", "t002_beta").stdout.strip()
    try:
        note = target_a / "progress.md"
        fm, body = parse_front_matter(note)
        fm["worktree"] = b_rel
        fm["branch"] = "t002_beta"
        write_front_matter(note, fm, body)

        result = _task_cli(git_repo, "purge", tid_a, "--reason", "miscreated")

        assert result.returncode != 0
        assert "拒绝操作非归属 worktree" in result.stderr
        assert target_a.exists()
        assert b_path.exists()
        assert git_run(git_repo, "rev-parse", "t002_beta").stdout.strip() == b_head
    finally:
        git_run(git_repo, "worktree", "remove", "--force", str(b_path), check=False)


def test_destructive_worktree_helpers_require_tid(git_repo):
    """remove/discard/归属校验缺 tid 时由签名报类型错误，不留弱校验路径。"""
    repo = ctx.RepoContext.for_root(git_repo)
    with pytest.raises(TypeError, match="tid"):
        worktrees.remove_worktree("", repo=repo)
    with pytest.raises(TypeError, match="tid"):
        worktrees.discard_worktree("", repo=repo)
    with pytest.raises(TypeError, match="tid"):
        worktrees._check_worktree_ownership("../repo_t001", None, repo=repo)
    with pytest.raises(TypeError):
        worktrees._check_worktree_ownership("../repo_t001", None, "t001", repo=repo)
