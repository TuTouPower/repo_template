"""Simplified dependency scheduling: hard dependencies, conflict hints, and cycles."""
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest
from review_support import configure_project_verification

SCRIPTS_DIR = FACTORY_ROOT / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task import context as ctx
from repo_task.documents import write_front_matter
from repo_task.scheduling import _dependency_cycle, compute_schedule


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    )


def _write_task(tasks_dir, tid, slug, *, status="backlog", depends_on="", conflicts_with=""):
    task_dir = tasks_dir / f"{tid}_{slug}"
    task_dir.mkdir(parents=True)
    write_front_matter(
        task_dir / "progress.md",
        {
            "tid": tid, "slug": slug, "title": slug, "status": status,
            "branch": "", "worktree": "", "review_level": "full",
            "review_limit": "5", "verify_limit": "5", "diff_anchor": "",
            "depends_on": depends_on, "conflicts_with": conflicts_with, "note": "",
        },
        "# task\n",
    )


@pytest.fixture
def schedule_repo(tmp_path):
    repo = tmp_path / "repo"
    tasks = repo / "docs/tasks"
    archive = repo / "docs/archive/tasks"
    template = tasks / "task_template"
    template.mkdir(parents=True)
    archive.mkdir(parents=True)
    write_front_matter(
        template / "progress.md",
        {"tid": "t000", "slug": "task_template", "title": "template", "status": "backlog"},
        "# template\n",
    )
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    configure_project_verification(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return ctx.RepoContext.for_root(repo)


def test_dependency_cycle_returns_stable_path():
    dependencies = {"t001": ["t003"], "t002": [], "t003": ["t005"], "t005": ["t001"]}
    assert _dependency_cycle(dependencies) == ["t001", "t003", "t005", "t001"]


def test_dependency_deep_chain_no_recursion_error():
    """验证深依赖链使用迭代 DFS 遍历，不会触发 RecursionError（Issue #64）。"""
    # 构造超过 Python 默认递归限制 1000 的深依赖链
    deps = {f"t{i:04d}": [f"t{i+1:04d}"] for i in range(1, 1200)}
    deps["t1200"] = []
    assert _dependency_cycle(deps) is None

    # 深依赖链包含环时，可稳定返回环路径
    deps["t1200"] = ["t1100"]
    cycle = _dependency_cycle(deps)
    assert cycle is not None
    assert cycle[0] == "t1100" and cycle[-1] == "t1100"



def test_dependency_controls_readiness(schedule_repo):
    _write_task(schedule_repo.tasks_dir, "t001", "alpha")
    _write_task(schedule_repo.tasks_dir, "t002", "beta", depends_on="t001")
    schedule = compute_schedule(repo=schedule_repo)
    assert schedule["selected"] == ["t001"]
    assert schedule["waiting_deps"] == [("t001", "t002")]


def test_backlog_conflict_is_hint_not_tie_break(schedule_repo):
    _write_task(schedule_repo.tasks_dir, "t001", "alpha", conflicts_with="t002")
    _write_task(schedule_repo.tasks_dir, "t002", "beta")
    schedule = compute_schedule(repo=schedule_repo)
    assert schedule["selected"] == ["t001", "t002"]
    assert schedule["blocked_conflicts"] == []
    assert schedule["conflicts"]["t002"] == {"t001"}


def test_active_conflict_blocks_parallel_recommendation(schedule_repo):
    _write_task(schedule_repo.tasks_dir, "t001", "alpha", status="active", conflicts_with="t002")
    _write_task(schedule_repo.tasks_dir, "t002", "beta")
    schedule = compute_schedule(repo=schedule_repo)
    assert schedule["selected"] == []
    assert schedule["blocked_conflicts"] == [("t002", "t001")]
