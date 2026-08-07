"""task.py 调度图算法纯函数。"""
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "repo_template"
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task.context import TaskDataError
from repo_task.scheduling import _dependency_cycle


def test_dependency_cycle_returns_stable_path():
    dependencies = {
        "t001": ["t003"],
        "t002": [],
        "t003": ["t005"],
        "t005": ["t001"],
    }

    assert _dependency_cycle(dependencies) == ["t001", "t003", "t005", "t001"]


"""以下为依赖×冲突合并图死锁检测与调度停滞哨兵（真实 git 仓库）。"""
import argparse
import subprocess

from repo_task import context as ctx
from repo_task.control import cmd_view
from repo_task.documents import write_front_matter
from repo_task.scheduling import _scheduling_deadlock_cycle, compute_schedule


def test_deadlock_cycle_detects_dep_conflict_inversion():
    """t350 依赖 t353 且序号更小，双方又互画冲突边 → 依赖方向与冲突
    优先级方向相反，构成死锁环。"""
    backlog = {"t350", "t353"}
    dependencies = {"t350": ["t353"], "t353": []}
    conflicts = {"t350": {"t353"}, "t353": {"t350"}}

    cycle = _scheduling_deadlock_cycle(backlog, dependencies, conflicts)

    assert cycle is not None
    assert set(cycle) == {"t350", "t353"}


def test_deadlock_cycle_ignores_healthy_chain():
    """纯依赖链、方向一致的依赖+冲突组合都不构成环。"""
    assert _scheduling_deadlock_cycle(
        {"t350", "t353"},
        {"t350": ["t353"], "t353": []},
        {"t350": set(), "t353": set()},
    ) is None
    # 冲突优先级方向与依赖方向一致（序号小者本来就是前置）
    assert _scheduling_deadlock_cycle(
        {"t350", "t353"},
        {"t350": [], "t353": ["t350"]},
        {"t350": {"t353"}, "t353": {"t350"}},
    ) is None


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=True,
    )


def _write_task(tasks_dir, tid, slug, *, depends_on="", conflicts_with="",
                schedule_status="scheduled"):
    task_dir = tasks_dir / f"{tid}_{slug}"
    task_dir.mkdir(parents=True)
    write_front_matter(
        task_dir / "task.md",
        {
            "tid": tid, "slug": slug, "title": slug, "status": "backlog",
            "branch": "", "worktree": "", "review_level": "full",
            "diff_anchor": "", "depends_on": depends_on,
            "conflicts_with": conflicts_with,
            "schedule_status": schedule_status, "note": "",
        },
        "# task\n",
    )


@pytest.fixture
def schedule_repo(tmp_path, monkeypatch):
    """真实 git 主仓；task 由各测试按需写入。"""
    repo = tmp_path / "repo"
    tasks = repo / "docs" / "tasks"
    archive = repo / "docs" / "archive" / "tasks"
    template = tasks / "task_template"
    template.mkdir(parents=True)
    archive.mkdir(parents=True)
    write_front_matter(
        template / "task.md",
        {"tid": "t000", "slug": "task_template", "title": "template",
         "status": "backlog"},
        "# template\n",
    )
    monkeypatch.setattr(ctx, "TASKS_DIR", tasks)
    monkeypatch.setattr(ctx, "ARCHIVE_TASKS_DIR", archive)
    monkeypatch.setattr(ctx, "TEMPLATE_DIR", template)
    monkeypatch.setattr(ctx, "ACTIVE_PATH", repo / "docs" / "tasks_index.json")
    monkeypatch.setattr(
        ctx, "ARCHIVE_PATH", repo / "docs" / "archive" / "tasks_index.json"
    )
    monkeypatch.setattr(
        ctx, "AUDIT_PATH", repo / "docs" / "archive" / "tasks_audit.log"
    )
    monkeypatch.setattr(ctx, "REPO_ROOT", repo)
    monkeypatch.setattr(ctx, "RUNTIME_DIR", repo / "docs" / "runtime")
    monkeypatch.setattr(
        ctx, "LEDGER_PATH", repo / "docs" / "runtime" / "dispatch_ledger.jsonl"
    )
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def test_compute_schedule_raises_on_deadlock_cycle(schedule_repo):
    tasks_dir = ctx.TASKS_DIR
    _write_task(tasks_dir, "t350", "alpha",
                depends_on="t353", conflicts_with="t353")
    _write_task(tasks_dir, "t353", "beta", conflicts_with="t350")

    with pytest.raises(TaskDataError, match="调度死锁环"):
        compute_schedule()


def test_compute_schedule_stall_flag_when_nothing_runnable(schedule_repo):
    """已排程 t001 等未排程前置 t002：无运行中、无可跑 → stalled。"""
    tasks_dir = ctx.TASKS_DIR
    _write_task(tasks_dir, "t001", "alpha", depends_on="t002")
    _write_task(tasks_dir, "t002", "beta", schedule_status="")

    schedule = compute_schedule()

    assert schedule["ready"] == []
    assert schedule["stalled"] is True
    assert schedule["stalled_backlog"] == ["t001"]


def test_compute_schedule_healthy_chain_not_stalled(schedule_repo):
    tasks_dir = ctx.TASKS_DIR
    _write_task(tasks_dir, "t001", "alpha")
    _write_task(tasks_dir, "t002", "beta", depends_on="t001")

    schedule = compute_schedule()

    assert schedule["ready"] == ["t001"]
    assert schedule["stalled"] is False
    assert schedule["stalled_backlog"] == []


def test_cmd_view_renders_stall_warning(schedule_repo, capsys):
    tasks_dir = ctx.TASKS_DIR
    _write_task(tasks_dir, "t001", "alpha", depends_on="t002")
    _write_task(tasks_dir, "t002", "beta", schedule_status="")

    cmd_view(argparse.Namespace(serve=False))

    out = capsys.readouterr().out
    assert "调度停滞" in out
    assert "t001" in out


"""L1 写时门禁：cmd_edit 拒绝与依赖路径冗余的冲突边。"""
from repo_task.lifecycle import cmd_edit


def _edit_args(tid, **overrides):
    fields = {
        "tid": tid, "title": None, "note": None, "note_append": None,
        "review_level": None, "depends_on": None, "depends_append": None,
        "depends_remove": None, "conflicts_with": None,
        "conflicts_append": None, "conflicts_remove": None,
        "schedule_status": None,
    }
    fields.update(overrides)
    return argparse.Namespace(**fields)


def test_edit_rejects_conflict_redundant_with_dependency(schedule_repo):
    tasks_dir = ctx.TASKS_DIR
    _write_task(tasks_dir, "t350", "alpha", depends_on="t353")
    _write_task(tasks_dir, "t353", "beta")

    with pytest.raises(SystemExit, match="冲突边与依赖路径冗余"):
        cmd_edit(_edit_args("t350", conflicts_append="t353"))


def test_edit_rejects_dependency_redundant_with_conflict(schedule_repo):
    tasks_dir = ctx.TASKS_DIR
    _write_task(tasks_dir, "t350", "alpha", conflicts_with="t353")
    _write_task(tasks_dir, "t353", "beta", conflicts_with="t350")

    with pytest.raises(SystemExit, match="冲突边与依赖路径冗余"):
        cmd_edit(_edit_args("t350", depends_append="t353"))


def test_edit_rejects_transitive_redundancy_both_directions(schedule_repo):
    tasks_dir = ctx.TASKS_DIR
    _write_task(tasks_dir, "t350", "alpha", depends_on="t351")
    _write_task(tasks_dir, "t351", "beta", depends_on="t353")
    _write_task(tasks_dir, "t353", "gamma")

    # 正向：t350 ⋯depends⋯→ t353（经 t351）
    with pytest.raises(SystemExit, match="冲突边与依赖路径冗余"):
        cmd_edit(_edit_args("t350", conflicts_append="t353"))
    # 反向：从链尾侧挂冲突同样被拦
    with pytest.raises(SystemExit, match="冲突边与依赖路径冗余"):
        cmd_edit(_edit_args("t353", conflicts_append="t350"))


def test_edit_allows_removing_redundant_conflict_on_dirty_graph(schedule_repo):
    """脏图（依赖+冲突双边）的增量修复路径必须畅通。"""
    tasks_dir = ctx.TASKS_DIR
    _write_task(tasks_dir, "t350", "alpha",
                depends_on="t353", conflicts_with="t353")
    _write_task(tasks_dir, "t353", "beta", conflicts_with="t350")

    cmd_edit(_edit_args("t353", conflicts_remove="t350"))

    # 反向边已同步、死锁环解除
    schedule = compute_schedule()
    assert "t350" not in schedule["conflicts"]["t353"]
    assert "t353" not in schedule["conflicts"]["t350"]


def test_edit_allows_unrelated_change_on_dirty_graph(schedule_repo):
    """未触碰依赖/冲突字段的编辑不做冗余校验，不拦无关维护。"""
    tasks_dir = ctx.TASKS_DIR
    _write_task(tasks_dir, "t350", "alpha",
                depends_on="t353", conflicts_with="t353")
    _write_task(tasks_dir, "t353", "beta", conflicts_with="t350")

    cmd_edit(_edit_args("t350", title="renamed"))

    task_md = tasks_dir / "t350_alpha" / "task.md"
    assert "renamed" in task_md.read_text(encoding="utf-8")
