"""task.py 目录扫描与归档一致性。

通过 monkeypatch 把 task.py 全局路径指向 tmp_path 下临时目录，
不依赖真实仓库结构、不触发 git。
"""
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import pytest
import task as task_mod
from task import TaskDataError, rebuild_index, scan_tasks, write_front_matter


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """把 task.py 全局路径指向 tmp_path 下临时目录，并建空模板目录。"""
    tasks = tmp_path / "docs" / "tasks"
    archive = tmp_path / "docs" / "archive" / "tasks"
    template = tasks / "task_template"
    active_json = tmp_path / "docs" / "tasks_index.json"
    archive_json = tmp_path / "docs" / "archive" / "tasks_index.json"
    tasks.mkdir(parents=True)
    archive.mkdir(parents=True)
    template.mkdir()
    # 模板目录必须含 task.md，scan_tasks 跳过 task_template/
    write_front_matter(
        template / "task.md",
        {"tid": "t000", "slug": "example", "status": "backlog"},
        "模板正文\n",
    )
    monkeypatch.setattr(task_mod, "TASKS_DIR", tasks)
    monkeypatch.setattr(task_mod, "ARCHIVE_TASKS_DIR", archive)
    monkeypatch.setattr(task_mod, "TEMPLATE_DIR", template)
    monkeypatch.setattr(task_mod, "ACTIVE_PATH", active_json)
    monkeypatch.setattr(task_mod, "ARCHIVE_PATH", archive_json)
    monkeypatch.setattr(task_mod, "REPO_ROOT", tmp_path)
    return tmp_path


def _make_task(tasks_dir, tid, slug, status):
    d = tasks_dir / f"{tid}_{slug}"
    d.mkdir()
    write_front_matter(
        d / "task.md",
        {"tid": tid, "slug": slug, "status": status},
        "body\n",
    )
    return d


def test_scan_empty(fake_repo):
    assert scan_tasks() == []


def test_scan_finds_active_task(fake_repo):
    _make_task(task_mod.TASKS_DIR, "t001", "alpha", "active")
    rows = scan_tasks()
    assert len(rows) == 1
    assert rows[0]["tid"] == "t001"
    assert rows[0]["status"] == "active"


def test_scan_finds_archived_task(fake_repo):
    _make_task(task_mod.ARCHIVE_TASKS_DIR, "t002", "beta", "done")
    rows = scan_tasks()
    assert len(rows) == 1
    assert rows[0]["tid"] == "t002"
    assert rows[0]["status"] == "done"


def test_scan_rejects_invalid_tid(fake_repo):
    _make_task(task_mod.TASKS_DIR, "x99", "bad", "backlog")
    with pytest.raises(TaskDataError, match="tid 非法"):
        scan_tasks()


def test_scan_rejects_dirname_mismatch(fake_repo):
    d = task_mod.TASKS_DIR / "t003_wrong"
    d.mkdir()
    write_front_matter(
        d / "task.md",
        {"tid": "t003", "slug": "right", "status": "backlog"},
        "x\n",
    )
    with pytest.raises(TaskDataError, match="目录名与 front matter 不符"):
        scan_tasks()


def test_scan_rejects_invalid_status(fake_repo):
    _make_task(task_mod.TASKS_DIR, "t004", "gamma", "weird")
    with pytest.raises(TaskDataError, match="status 非法"):
        scan_tasks()


def test_scan_rejects_status_location_mismatch(fake_repo):
    # active 状态出现在归档目录
    _make_task(task_mod.ARCHIVE_TASKS_DIR, "t005", "delta", "active")
    with pytest.raises(TaskDataError, match="与所在目录不符"):
        scan_tasks()


def test_scan_detects_dup_tid(fake_repo):
    _make_task(task_mod.TASKS_DIR, "t006", "eps", "backlog")
    _make_task(task_mod.ARCHIVE_TASKS_DIR, "t006", "eps", "done")
    with pytest.raises(TaskDataError, match="重复 tid"):
        scan_tasks()


def test_scan_orders_by_tid_numerically(fake_repo):
    _make_task(task_mod.TASKS_DIR, "t010", "ten", "backlog")
    _make_task(task_mod.TASKS_DIR, "t002", "two", "backlog")
    _make_task(task_mod.TASKS_DIR, "t100", "hundred", "backlog")
    rows = scan_tasks()
    tids = [r["tid"] for r in rows]
    assert tids == ["t002", "t010", "t100"]


def test_rebuild_index_splits_active_archive(fake_repo):
    _make_task(task_mod.TASKS_DIR, "t001", "alpha", "backlog")
    _make_task(task_mod.TASKS_DIR, "t002", "beta", "active")
    _make_task(task_mod.ARCHIVE_TASKS_DIR, "t003", "gamma", "done")
    rebuild_index()
    active = json.loads(task_mod.ACTIVE_PATH.read_text(encoding="utf-8"))
    archive = json.loads(task_mod.ARCHIVE_PATH.read_text(encoding="utf-8"))
    assert [t["tid"] for t in active["tasks"]] == ["t001", "t002"]
    assert [t["tid"] for t in archive["tasks"]] == ["t003"]
    assert active["authority"] == "docs/tasks/{tid}_{slug}/task.md front matter"
    assert archive["authority"] == (
        "docs/archive/tasks/{tid}_{slug}/task.md front matter"
    )


def test_rebuild_index_uses_utf8_and_lf(fake_repo):
    _make_task(task_mod.TASKS_DIR, "t001", "alpha", "backlog")
    rebuild_index()
    raw = task_mod.ACTIVE_PATH.read_bytes()
    assert b"\r\n" not in raw
    # ensure_ascii=False：中文不转义
    text = raw.decode("utf-8")
    assert '"tasks"' in text


def test_unlink_managed_env_links_only_removes_script_links(fake_repo):
    source = fake_repo / ".env"
    source.write_text("secret", encoding="utf-8")
    worktree = fake_repo / "worktree"
    worktree.mkdir()

    assert task_mod.link_local_env(worktree) == [".env"]
    managed = worktree / ".env"
    assert task_mod.is_managed_env_link(worktree, managed)

    nested = worktree / "config"
    nested.mkdir()
    other = fake_repo / "other.env"
    other.write_text("other", encoding="utf-8")
    unmanaged = nested / ".env"
    unmanaged.symlink_to(other)
    assert not task_mod.is_managed_env_link(worktree, unmanaged)

    task_mod.unlink_managed_env_links(worktree)
    assert not managed.is_symlink()
    assert unmanaged.is_symlink()


def test_managed_env_link_stays_identifiable_after_source_removed(fake_repo):
    source = fake_repo / ".env"
    source.write_text("secret", encoding="utf-8")
    worktree = fake_repo / "worktree"
    worktree.mkdir()
    task_mod.link_local_env(worktree)
    source.unlink()

    link = worktree / ".env"
    assert task_mod.is_managed_env_link(worktree, link)
    task_mod.unlink_managed_env_links(worktree)
    assert not link.is_symlink()


def test_close_task_rolls_back_when_move_fails(fake_repo, monkeypatch):
    """归档移动失败时 front matter 回滚，避免「已写 done、目录未归档」死锁。"""
    import argparse

    d = _make_task(task_mod.TASKS_DIR, "t001", "alpha", "active")

    def boom(src, dst):
        raise OSError("模拟移动失败")

    monkeypatch.setattr(task_mod.shutil, "move", boom)
    monkeypatch.setattr(task_mod, "require_own_task_worktree", lambda fm: None)
    with pytest.raises(SystemExit, match="归档移动失败"):
        task_mod._close_task(argparse.Namespace(tid="t001"), "done", None)
    fm, _ = task_mod.parse_front_matter(d / "task.md")
    assert fm["status"] == "active"  # 已回滚
    assert d.is_dir()  # 目录仍在活跃区


def test_list_is_readonly_by_default(fake_repo, capsys):
    """list 只罗列，不写派生 index。"""
    import argparse

    _make_task(task_mod.TASKS_DIR, "t001", "alpha", "backlog")
    assert not task_mod.ACTIVE_PATH.exists()
    task_mod.cmd_list(argparse.Namespace(status=None, rebuild=False, ref=None))
    capsys.readouterr()
    assert not task_mod.ACTIVE_PATH.exists()


def test_list_rebuild_writes_index(fake_repo, capsys, monkeypatch):
    import argparse

    _make_task(task_mod.TASKS_DIR, "t001", "alpha", "backlog")
    monkeypatch.setattr(task_mod, "require_primary_worktree", lambda: None)
    task_mod.cmd_list(argparse.Namespace(status=None, rebuild=True, ref=None))
    out = capsys.readouterr().out
    assert task_mod.ACTIVE_PATH.exists()
    assert "index rebuilt" in out


def test_task_worktree_state_updates_do_not_rebuild_index(fake_repo, monkeypatch):
    import argparse

    _make_task(task_mod.TASKS_DIR, "t001", "alpha", "active")
    monkeypatch.setattr(task_mod, "require_own_task_worktree", lambda fm: None)
    task_mod.cmd_block(argparse.Namespace(tid="t001", reason="review"))

    assert not task_mod.ACTIVE_PATH.exists()
    fm, _ = task_mod.parse_front_matter(task_mod.TASKS_DIR / "t001_alpha/task.md")
    assert fm["status"] == "blocked"
