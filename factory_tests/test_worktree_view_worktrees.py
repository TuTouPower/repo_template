"""视图/worktree 主题：worktrees 路径规范化与失败语义回归（工厂集成测试，不进产物）。

覆盖采纳项：DS L266、L270、L271、L276。
"""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task import context as ctx
from repo_task import worktrees


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@e.c")
    _git(repo, "config", "user.name", "t")
    (repo / "base.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def test_normalize_rel_preserves_dot_prefix():
    """lstrip('./') 会剥掉 .venv/.env 点前缀；规范化只去显式 './' 与尾 '/'. """
    assert worktrees._normalize_rel(".venv/") == ".venv"
    assert worktrees._normalize_rel(".env") == ".env"
    assert worktrees._normalize_rel("./.env") == ".env"
    assert worktrees._normalize_rel("vendor/") == "vendor"
    assert worktrees._normalize_rel("./vendor/") == "vendor"


def test_find_unignored_env_symlinks_detects_dot_prefixed_dir_rule(git_repo, tmp_path):
    """点前缀软链命中 .venv/ / .env/ 目录规则时应被诊断（Issue #91 边界）。"""
    (git_repo / ".gitignore").write_text(".venv/\n.env/\n", encoding="utf-8")
    _git(git_repo, "add", ".gitignore")
    _git(git_repo, "commit", "-m", "ignore dot env dirs")

    ext = tmp_path / "external"
    ext.mkdir()
    (git_repo / ".venv").symlink_to(ext)
    (git_repo / ".env").symlink_to(ext)

    diagnostics = worktrees.find_unignored_env_symlinks(git_repo, [".venv", ".env"])
    assert ".venv" in diagnostics
    assert ".venv/" in diagnostics[".venv"]
    assert ".env" in diagnostics
    assert ".env/" in diagnostics[".env"]


def test_remove_worktree_reports_orphan_dir(git_repo):
    """未登记且目录残留时 remove_worktree 返回 False，不静默成功。"""
    orphan = git_repo / "worktrees" / "t001_orphan"
    orphan.mkdir(parents=True)
    ok, message = worktrees.remove_worktree(
        "worktrees/t001_orphan", repo=ctx.RepoContext.for_root(git_repo), tid="t001"
    )
    assert ok is False
    assert orphan.exists()
    assert "仍存在" in message


def test_discard_worktree_reports_orphan_dir(git_repo):
    """discard_worktree 同样在 prune 后校验路径，拒绝静默返回成功。"""
    orphan = git_repo / "worktrees" / "t002_orphan"
    orphan.mkdir(parents=True)
    ok, message = worktrees.discard_worktree(
        "worktrees/t002_orphan", repo=ctx.RepoContext.for_root(git_repo), tid="t002"
    )
    assert ok is False
    assert orphan.exists()
    assert "仍存在" in message


def test_remove_worktree_succeeds_when_path_absent(git_repo):
    """未登记且路径确实不存在时 prune 后返回成功。"""
    ok, _ = worktrees.remove_worktree(
        "worktrees/already_gone", repo=ctx.RepoContext.for_root(git_repo), tid="t009"
    )
    assert ok is True


def test_link_local_files_fails_closed_on_git_error(git_repo, monkeypatch):
    """git 查询失败不再静默 pass，TaskDataError 带诊断向上传播。"""
    (git_repo / ".env").write_text("X=1\n", encoding="utf-8")
    worktree = git_repo / "wt"
    worktree.mkdir()

    def boom(*args, **kwargs):
        raise ctx.TaskDataError("git ls-files 失败：模拟故障")

    monkeypatch.setattr(worktrees, "_untracked_in", boom)
    with pytest.raises(ctx.TaskDataError, match="模拟故障"):
        worktrees.link_local_files(worktree, repo=ctx.RepoContext.for_root(git_repo))


def test_ensure_links_ignored_fails_closed_without_git_root(tmp_path):
    """缺少 .git 时不再静默 return；误配置由后续 git 调用显式报错。"""
    root = tmp_path / "repo"
    root.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    with pytest.raises(ctx.TaskDataError):
        worktrees._ensure_links_ignored(
            worktree, ["ext_data"], repo=ctx.RepoContext.for_root(root)
        )
