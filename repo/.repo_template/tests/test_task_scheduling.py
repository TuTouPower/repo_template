"""Simplified dependency scheduling: hard dependencies, conflict hints, and cycles."""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
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
        task_dir / "task.md",
        {
            "tid": tid, "slug": slug, "title": slug, "status": status,
            "branch": "", "worktree": "", "review_level": "full",
            "review_limit": "5", "verify_limit": "5", "diff_anchor": "",
            "depends_on": depends_on, "conflicts_with": conflicts_with, "note": "",
        },
        "# task\n",
    )


@pytest.fixture
def schedule_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    tasks = repo / "docs/tasks"
    archive = repo / "docs/archive/tasks"
    template = tasks / "task_template"
    template.mkdir(parents=True)
    archive.mkdir(parents=True)
    write_front_matter(
        template / "task.md",
        {"tid": "t000", "slug": "task_template", "title": "template", "status": "backlog"},
        "# template\n",
    )
    for name, value in {
        "TASKS_DIR": tasks, "ARCHIVE_TASKS_DIR": archive, "TEMPLATE_DIR": template,
        "ACTIVE_PATH": repo / "docs/tasks_index.json",
        "ARCHIVE_PATH": repo / "docs/archive/tasks_index.json",
        "AUDIT_PATH": repo / "docs/archive/tasks_audit.log",
        "REPO_ROOT": repo, "RUNTIME_DIR": repo / "docs/runtime",
        "LEDGER_PATH": repo / "docs/runtime/dispatch_ledger.jsonl",
    }.items():
        monkeypatch.setattr(ctx, name, value)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def test_dependency_cycle_returns_stable_path():
    dependencies = {"t001": ["t003"], "t002": [], "t003": ["t005"], "t005": ["t001"]}
    assert _dependency_cycle(dependencies) == ["t001", "t003", "t005", "t001"]


def test_dependency_controls_readiness(schedule_repo):
    _write_task(ctx.TASKS_DIR, "t001", "alpha")
    _write_task(ctx.TASKS_DIR, "t002", "beta", depends_on="t001")
    schedule = compute_schedule()
    assert schedule["selected"] == ["t001"]
    assert schedule["waiting_deps"] == [("t001", "t002")]


def test_backlog_conflict_is_hint_not_tie_break(schedule_repo):
    _write_task(ctx.TASKS_DIR, "t001", "alpha", conflicts_with="t002")
    _write_task(ctx.TASKS_DIR, "t002", "beta")
    schedule = compute_schedule()
    assert schedule["selected"] == ["t001", "t002"]
    assert schedule["blocked_conflicts"] == []
    assert schedule["conflicts"]["t002"] == {"t001"}


def test_active_conflict_blocks_parallel_recommendation(schedule_repo):
    _write_task(ctx.TASKS_DIR, "t001", "alpha", status="active", conflicts_with="t002")
    _write_task(ctx.TASKS_DIR, "t002", "beta")
    schedule = compute_schedule()
    assert schedule["selected"] == []
    assert schedule["blocked_conflicts"] == [("t002", "t001")]
