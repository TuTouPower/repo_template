"""task.py start 的主仓协调、worktree 门禁与失败补偿（真实 git 仓库）。"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import task as task_mod
from task import parse_front_matter, write_front_matter


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """真实 git 主仓 + backlog task t001。"""
    repo = tmp_path / "repo"
    tasks = repo / "docs" / "tasks"
    archive = repo / "docs" / "archive" / "tasks"
    template = tasks / "task_template"
    scripts = repo / "scripts"
    template.mkdir(parents=True)
    archive.mkdir(parents=True)
    scripts.mkdir()
    shutil.copy2(SCRIPTS_DIR / "task.py", scripts / "task.py")
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
        task_mod, "AUDIT_PATH", repo / "docs" / "archive" / "tasks_audit.log")
    monkeypatch.setattr(task_mod, "REPO_ROOT", repo)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _start(repo):
    task_mod.cmd_start(argparse.Namespace(tid="t001"))


def _task_cli(repo, *args):
    return subprocess.run(
        [sys.executable, str(repo / "scripts" / "task.py"), *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def _worktree_path(repo):
    return repo.parent / f"{repo.name}_t001"


def test_start_creates_clean_worktree_from_primary_main(git_repo):
    initial_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    _start(git_repo)

    assert _git(git_repo, "branch", "--show-current").stdout.strip() == "main"
    assert _git(git_repo, "status", "--porcelain").stdout.strip() == ""
    worktree = _worktree_path(git_repo)
    assert worktree.is_dir()
    assert _git(worktree, "branch", "--show-current").stdout.strip() == "t001_alpha"
    assert _git(worktree, "status", "--porcelain").stdout.strip() == ""

    main_fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
    worktree_fm, _ = parse_front_matter(worktree / "docs/tasks/t001_alpha/task.md")
    assert main_fm == worktree_fm
    assert main_fm["status"] == "active"
    assert main_fm["branch"] == "t001_alpha"
    assert main_fm["worktree"] == f"../{git_repo.name}_t001"
    assert main_fm["diff_anchor"] == initial_head

    files = _git(git_repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert files == [
        "docs/archive/tasks_index.json",
        "docs/tasks/t001_alpha/task.md",
        "docs/tasks_index.json",
    ]


def test_start_rejects_non_primary_branch(git_repo):
    _git(git_repo, "switch", "-c", "feature")

    with pytest.raises(SystemExit, match="主干"):
        _start(git_repo)

    fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
    assert fm["status"] == "backlog"
    assert not _worktree_path(git_repo).exists()


def test_start_rejects_dirty_primary_worktree(git_repo):
    (git_repo / "unrelated.txt").write_text("dirty", encoding="utf-8")

    with pytest.raises(SystemExit, match="未提交改动"):
        _start(git_repo)

    assert not _worktree_path(git_repo).exists()


def test_start_rejects_existing_task_branch(git_repo):
    _git(git_repo, "branch", "t001_alpha")

    with pytest.raises(SystemExit, match="分支.*已存在"):
        _start(git_repo)

    fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
    assert fm["status"] == "backlog"
    assert not _worktree_path(git_repo).exists()


def test_start_rejects_existing_worktree_path(git_repo):
    _worktree_path(git_repo).mkdir()

    with pytest.raises(SystemExit, match="已存在"):
        _start(git_repo)

    fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
    assert fm["status"] == "backlog"


def test_start_rejects_task_worktree(git_repo):
    _start(git_repo)
    result = _task_cli(_worktree_path(git_repo), "start", "t001")

    assert result.returncode != 0
    assert "主工作区" in result.stderr


def test_list_rebuild_requires_primary_worktree(git_repo):
    _start(git_repo)
    result = _task_cli(_worktree_path(git_repo), "list", "--rebuild")

    assert result.returncode != 0
    assert "主工作区" in result.stderr


def test_finish_rejects_primary_and_runs_in_registered_worktree(git_repo):
    _start(git_repo)

    primary = _task_cli(git_repo, "finish", "t001")
    assert primary.returncode != 0
    assert "自身 worktree" in primary.stderr

    worktree = _worktree_path(git_repo)
    finished = _task_cli(worktree, "finish", "t001")
    assert finished.returncode == 0, finished.stderr

    fm, _ = parse_front_matter(
        worktree / "docs" / "archive" / "tasks" / "t001_alpha" / "task.md"
    )
    assert fm["status"] == "done"
    changed = _git(worktree, "diff", "--name-only").stdout.split()
    assert "docs/tasks_index.json" not in changed
    assert "docs/archive/tasks_index.json" not in changed


def test_start_compensates_when_worktree_creation_fails(git_repo, monkeypatch):
    initial_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    def fail_create(*args, **kwargs):
        raise task_mod.TaskDataError("模拟 worktree 创建失败")

    monkeypatch.setattr(task_mod, "create_worktree", fail_create)

    with pytest.raises(SystemExit, match="已恢复"):
        _start(git_repo)

    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == initial_head
    assert _git(git_repo, "status", "--porcelain").stdout.strip() == ""
    assert not _worktree_path(git_repo).exists()
    assert _git(git_repo, "branch", "--list", "t001_alpha").stdout.strip() == ""
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
    assert fm["status"] == "backlog"


def test_start_compensates_when_local_config_link_fails(git_repo, monkeypatch):
    initial_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    def fail_link(*args, **kwargs):
        raise OSError("模拟本地配置软链失败")

    monkeypatch.setattr(task_mod, "link_local_env", fail_link)

    with pytest.raises(SystemExit, match="已恢复"):
        _start(git_repo)

    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == initial_head
    assert _git(git_repo, "status", "--porcelain").stdout.strip() == ""
    assert not _worktree_path(git_repo).exists()
    assert _git(git_repo, "branch", "--list", "t001_alpha").stdout.strip() == ""


def test_rewind_from_primary_removes_registered_worktree(git_repo):
    _start(git_repo)
    worktree = _worktree_path(git_repo)

    task_mod.cmd_rewind(argparse.Namespace(tid="t001", to=None, reason="撤回", yes=True))

    assert not worktree.is_dir()
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
    assert fm["status"] == "backlog"
    assert fm["worktree"] == ""


def test_preflight_rejects_primary_for_active_task(git_repo, capsys):
    _start(git_repo)

    with pytest.raises(SystemExit):
        task_mod.cmd_preflight(argparse.Namespace(tid="t001"))

    assert "当前不在 task worktree" in capsys.readouterr().out
