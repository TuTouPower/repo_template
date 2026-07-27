"""task.py start 的主仓干净性与后续 merge/rewind（真实 git 仓库）。

回归自审阅发现：start 曾在主仓留下未提交的 task.md/index 改动，
导致收尾回主仓 `git merge --no-ff` 必然 abort；修复后主仓保持干净，
worktree 字段从主仓 front matter 消失，rewind/close 按命名约定推导。
"""
import argparse
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import pytest
import task as task_mod
from task import parse_front_matter, write_front_matter


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """真实 git 仓库 + monkeypatch 后的 task.py 全局路径；含 backlog task t001。"""
    repo = tmp_path / "repo"
    tasks = repo / "docs" / "tasks"
    archive = repo / "docs" / "archive" / "tasks"
    template = tasks / "task_template"
    template.mkdir(parents=True)
    archive.mkdir(parents=True)
    # 模板目录必须含 task.md，scan_tasks 跳过 task_template/
    write_front_matter(
        template / "task.md",
        {"tid": "t000", "slug": "example", "status": "backlog"},
        "模板正文\n",
    )
    task_dir = tasks / "t001_alpha"
    task_dir.mkdir()
    write_front_matter(
        task_dir / "task.md",
        {"tid": "t001", "slug": "alpha", "title": "x", "status": "backlog"},
        "body\n",
    )
    monkeypatch.setattr(task_mod, "TASKS_DIR", tasks)
    monkeypatch.setattr(task_mod, "ARCHIVE_TASKS_DIR", archive)
    monkeypatch.setattr(task_mod, "TEMPLATE_DIR", template)
    monkeypatch.setattr(task_mod, "ACTIVE_PATH", repo / "docs" / "tasks_index.json")
    monkeypatch.setattr(
        task_mod, "ARCHIVE_PATH", repo / "docs" / "archive" / "tasks_index.json"
    )
    monkeypatch.setattr(
        task_mod, "AUDIT_PATH", repo / "docs" / "archive" / "tasks_audit.log"
    )
    monkeypatch.setattr(task_mod, "REPO_ROOT", repo)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _start(repo):
    task_mod.cmd_start(argparse.Namespace(tid="t001", no_worktree=False, force=False))


def _worktree_path(repo):
    return repo.parent / f"{repo.name}_t001"


def test_start_leaves_main_repo_clean(git_repo):
    _start(git_repo)
    assert _git(git_repo, "status", "--porcelain").stdout.strip() == ""
    # start commit 同时含 task.md 与两个派生缓存
    files = _git(git_repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert "docs/tasks/t001_alpha/task.md" in files
    assert "docs/tasks_index.json" in files
    assert "docs/archive/tasks_index.json" in files


def test_start_fixes_worktree_copy(git_repo):
    _start(git_repo)
    fm, _ = parse_front_matter(
        _worktree_path(git_repo) / "docs/tasks/t001_alpha/task.md"
    )
    head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()  # 全量 hash
    assert fm["status"] == "active"
    assert fm["worktree"] == f"../{git_repo.name}_t001"
    assert fm["diff_anchor"] == head


def test_merge_back_after_branch_archive_succeeds(git_repo):
    """H1 回归：分支上归档移动 + commit 后，回主仓 merge 不再被脏文件挡住。"""
    _start(git_repo)
    wt = _worktree_path(git_repo)
    (wt / "docs/archive/tasks").mkdir(parents=True, exist_ok=True)
    _git(wt, "mv", "docs/tasks/t001_alpha", "docs/archive/tasks/t001_alpha")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-m", "task(t001): done")
    r = _git(git_repo, "merge", "--no-ff", "t001_alpha", check=False)
    assert r.returncode == 0, r.stderr


def test_rewind_from_main_removes_derived_worktree(git_repo):
    """主仓 task.md 不带 worktree 字段；rewind 须按命名约定推导并移除 worktree。"""
    _start(git_repo)
    wt = _worktree_path(git_repo)
    assert wt.is_dir()
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
    assert not fm.get("worktree")  # 主仓副本保持 start commit 内容
    # worktree 副本的字段补齐是 start 留下的未提交改动；丢弃以模拟未动过的工作区
    # （worktree 有改动时 remove 不加 --force 会拒绝，属既有安全行为）
    _git(wt, "checkout", "--", ".")
    task_mod.cmd_rewind(argparse.Namespace(tid="t001", to=None, reason="撤回", yes=False))
    assert not wt.is_dir()
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
    assert fm["status"] == "backlog"


def test_start_warns_on_non_main_branch(git_repo, capsys):
    """start 的 chore commit 落非主干分支时给警告。"""
    _git(git_repo, "switch", "-c", "feature")
    _start(git_repo)
    assert "非主干" in capsys.readouterr().err


def _make_unmerged_commit(git_repo):
    """在 worktree 里提交一笔，让分支领先主干（触发 rewind 确认路径）。"""
    wt = _worktree_path(git_repo)
    (wt / "docs/tasks/t001_alpha/note.txt").write_text("work\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-m", "task(t001): wip")
    return wt


def test_rewind_yes_skips_unmerged_prompt(git_repo):
    _start(git_repo)
    wt = _make_unmerged_commit(git_repo)
    task_mod.cmd_rewind(argparse.Namespace(tid="t001", to=None, reason="x", yes=True))
    assert not wt.is_dir()
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
    assert fm["status"] == "backlog"


def test_rewind_without_yes_aborts_when_declined(git_repo, monkeypatch):
    _start(git_repo)
    _make_unmerged_commit(git_repo)
    monkeypatch.setattr("builtins.input", lambda: "n")
    with pytest.raises(SystemExit, match="aborted"):
        task_mod.cmd_rewind(argparse.Namespace(tid="t001", to=None, reason="x", yes=False))
