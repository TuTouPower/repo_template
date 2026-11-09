"""repo_hygiene.py 机械迁移动作的真实 git 测试。

monkeypatch 模块级路径常量到 tmp_path 构造的 git 仓库，覆盖 review 目录 git mv 迁移、
dry-run 零写盘、歧义与格式校验，并断言 archive-handoff 与 archive-spike 已退役。
"""

import argparse
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from git_repo_support import get_controlled_git_env
import repo_hygiene as rh


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=check,
        env=get_controlled_git_env(),
    )


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@e.c")
    _git(root, "config", "user.name", "t")
    for name, value in {
        "REPO_ROOT": root,
        "REVIEWS_DIR": root / "docs/reviews",
        "ARCHIVE_REVIEWS": root / "docs/archive/reviews",
    }.items():
        monkeypatch.setattr(rh, name, value)
    return root


def test_archive_handoff_command_removed(repo):
    with pytest.raises(SystemExit):
        rh.main(["archive-handoff"])


def test_archive_spike_command_removed(repo):
    with pytest.raises(SystemExit):
        rh.main(["archive-spike", "s001"])


def test_archive_review_moves_dir(repo):
    d = repo / "docs/reviews/review_my_check"
    d.mkdir(parents=True)
    (d / "review_general.md").write_text("PASS\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "docs(review): add my_check")
    rh.cmd_archive_review(argparse.Namespace(dir="review_my_check", write=True))
    assert (repo / "docs/archive/reviews/review_my_check/review_general.md").is_file()
    assert not (repo / "docs/reviews/review_my_check").exists()


def test_archive_review_moves_gitignore_meta(repo):
    d = repo / "docs/reviews/review_my_check"
    d.mkdir(parents=True)
    (d / "review_general.md").write_text("PASS\n", encoding="utf-8")
    meta = d / "_meta" / "notes.json"
    meta.parent.mkdir()
    meta.write_text('{"x": 1}\n', encoding="utf-8")
    (repo / ".gitignore").write_text(
        "docs/reviews/review_*/_meta/\ndocs/archive/reviews/review_*/_meta/\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "docs(review): add my_check")
    assert (d / "_meta/notes.json").is_file()
    assert "_meta" not in _git(repo, "ls-files").stdout
    rh.cmd_archive_review(argparse.Namespace(dir="review_my_check", write=True))
    dest = repo / "docs/archive/reviews/review_my_check"
    assert (dest / "review_general.md").is_file()
    assert (dest / "_meta/notes.json").is_file()
    assert not (repo / "docs/reviews/review_my_check").exists()


def test_archive_review_rejects_bad_prefix(repo):
    with pytest.raises(rh.HygieneError):
        rh.cmd_archive_review(argparse.Namespace(dir="prompts", write=True))
    with pytest.raises(rh.HygieneError):
        rh.cmd_archive_review(argparse.Namespace(dir="review_nonexistent", write=True))


def test_archive_review_rejects_path_traversal_and_separators(repo):
    """验证 archive-review 拒绝包含分隔符与父目录引用的参数（Issue #28）。"""
    with pytest.raises(rh.HygieneError, match="不能包含路径分隔符"):
        rh.cmd_archive_review(argparse.Namespace(dir="review_foo/../../sentinel", write=False))
    with pytest.raises(rh.HygieneError, match="不能包含路径分隔符"):
        rh.cmd_archive_review(argparse.Namespace(dir="review_sub/nested", write=False))


def test_repo_hygiene_git_timeout(monkeypatch):
    """验证 git 调用超时时抛出 HygieneError 并包含超时时限信息（Issue #66）。"""
    def mock_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=30)

    monkeypatch.setattr(rh, "subprocess", SimpleNamespace(**{**vars(subprocess), "run": mock_run}))
    with pytest.raises(rh.HygieneError, match="超时（30s）"):
        rh._git("status")


def test_archive_review_rolls_back_when_git_add_fails(repo, monkeypatch):
    """git add 失败时整目录回滚，源与目标都不残留，可重试（DS L261）。"""
    d = repo / "docs/reviews/review_my_check"
    d.mkdir(parents=True)
    (d / "review_general.md").write_text("PASS\n", encoding="utf-8")
    meta = d / "_meta" / "notes.json"
    meta.parent.mkdir()
    meta.write_text('{"x": 1}\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add my_check")

    real_git = rh._git

    def flaky_git(*args, **kwargs):
        if args and args[0] == "add":
            return subprocess.CompletedProcess(args, 1, "", "simulated git add failure")
        return real_git(*args, **kwargs)

    monkeypatch.setattr(rh, "_git", flaky_git)
    with pytest.raises(rh.HygieneError, match="已回滚目录"):
        rh.cmd_archive_review(argparse.Namespace(dir="review_my_check", write=True))
    assert (d / "review_general.md").is_file()
    assert meta.is_file()
    assert not (repo / "docs/archive/reviews/review_my_check").exists()

    monkeypatch.setattr(rh, "_git", real_git)
    rh.cmd_archive_review(argparse.Namespace(dir="review_my_check", write=True))
    dest = repo / "docs/archive/reviews/review_my_check"
    assert (dest / "review_general.md").is_file()
    assert (dest / "_meta/notes.json").is_file()
    assert not d.exists()


def test_archive_review_rejects_existing_target(repo):
    d = repo / "docs/reviews/review_my_check"
    d.mkdir(parents=True)
    (d / "review_general.md").write_text("PASS\n", encoding="utf-8")
    dest = repo / "docs/archive/reviews/review_my_check"
    dest.mkdir(parents=True)
    (dest / "sentinel.md").write_text("keep\n", encoding="utf-8")

    with pytest.raises(rh.HygieneError, match="目标已存在"):
        rh.cmd_archive_review(argparse.Namespace(dir="review_my_check", write=True))
    assert (dest / "sentinel.md").is_file()
    assert (d / "review_general.md").is_file()

