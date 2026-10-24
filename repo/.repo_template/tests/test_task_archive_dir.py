"""task.py 目录扫描与归档一致性。

通过 monkeypatch 把 task.py 全局路径指向 tmp_path 下临时目录，
不依赖真实仓库结构、不触发 git。
"""
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import pytest
from repo_task import context as ctx
from repo_task import lifecycle, store, worktrees
from repo_task.context import TaskDataError
from repo_task.documents import parse_front_matter, write_front_matter
from repo_task.store import rebuild_index, scan_tasks
from review_support import configure_project_verification

pytestmark = pytest.mark.contract


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
    configure_project_verification(tmp_path)
    # 模板目录必须含 note.md，scan_tasks 跳过 task_template/
    write_front_matter(
        template / "note.md",
        {"tid": "t000", "slug": "example", "status": "backlog"},
        "模板正文\n",
    )
    monkeypatch.setattr(ctx, "TASKS_DIR", tasks)
    monkeypatch.setattr(ctx, "ARCHIVE_TASKS_DIR", archive)
    monkeypatch.setattr(ctx, "TEMPLATE_DIR", template)
    monkeypatch.setattr(ctx, "ACTIVE_PATH", active_json)
    monkeypatch.setattr(ctx, "ARCHIVE_PATH", archive_json)
    monkeypatch.setattr(ctx, "REPO_ROOT", tmp_path)
    return tmp_path


def _make_task(tasks_dir, tid, slug, status):
    d = tasks_dir / f"{tid}_{slug}"
    d.mkdir()
    write_front_matter(
        d / "note.md",
        {"tid": tid, "slug": slug, "status": status},
        "body\n\n- specs_index: not_applicable: isolated archive lifecycle test\n",
    )
    (d / "task.md").write_text("# isolated task\n", encoding="utf-8")
    return d


def test_scan_empty(fake_repo):
    assert scan_tasks() == []


def test_scan_finds_active_task(fake_repo):
    _make_task(ctx.TASKS_DIR, "t001", "alpha", "active")
    rows = scan_tasks()
    assert len(rows) == 1
    assert rows[0]["tid"] == "t001"
    assert rows[0]["status"] == "active"


def test_scan_finds_archived_task(fake_repo):
    _make_task(ctx.ARCHIVE_TASKS_DIR, "t002", "beta", "done")
    rows = scan_tasks()
    assert len(rows) == 1
    assert rows[0]["tid"] == "t002"
    assert rows[0]["status"] == "done"


def test_scan_rejects_invalid_tid(fake_repo):
    _make_task(ctx.TASKS_DIR, "x99", "bad", "backlog")
    with pytest.raises(TaskDataError, match="tid 非法"):
        scan_tasks()


def test_scan_rejects_dirname_mismatch(fake_repo):
    d = ctx.TASKS_DIR / "t003_wrong"
    d.mkdir()
    write_front_matter(
        d / "note.md",
        {"tid": "t003", "slug": "right", "status": "backlog"},
        "x\n",
    )
    with pytest.raises(TaskDataError, match="目录名与 front matter 不符"):
        scan_tasks()


def test_scan_rejects_invalid_status(fake_repo):
    _make_task(ctx.TASKS_DIR, "t004", "gamma", "weird")
    with pytest.raises(TaskDataError, match="status 非法"):
        scan_tasks()


def test_scan_rejects_status_location_mismatch(fake_repo):
    # active 状态出现在归档目录
    _make_task(ctx.ARCHIVE_TASKS_DIR, "t005", "delta", "active")
    with pytest.raises(TaskDataError, match="与所在目录不符"):
        scan_tasks()


def test_scan_detects_dup_tid(fake_repo):
    _make_task(ctx.TASKS_DIR, "t006", "eps", "backlog")
    _make_task(ctx.ARCHIVE_TASKS_DIR, "t006", "eps", "done")
    with pytest.raises(TaskDataError, match="重复 tid"):
        scan_tasks()


def test_scan_orders_by_tid_numerically(fake_repo):
    _make_task(ctx.TASKS_DIR, "t010", "ten", "backlog")
    _make_task(ctx.TASKS_DIR, "t002", "two", "backlog")
    _make_task(ctx.TASKS_DIR, "t100", "hundred", "backlog")
    rows = scan_tasks()
    tids = [r["tid"] for r in rows]
    assert tids == ["t002", "t010", "t100"]


def test_rebuild_index_splits_active_archive(fake_repo):
    _make_task(ctx.TASKS_DIR, "t001", "alpha", "backlog")
    _make_task(ctx.TASKS_DIR, "t002", "beta", "active")
    _make_task(ctx.ARCHIVE_TASKS_DIR, "t003", "gamma", "done")
    rebuild_index()
    active = json.loads(ctx.ACTIVE_PATH.read_text(encoding="utf-8"))
    archive = json.loads(ctx.ARCHIVE_PATH.read_text(encoding="utf-8"))
    assert [t["tid"] for t in active["tasks"]] == ["t001", "t002"]
    assert [t["tid"] for t in archive["tasks"]] == ["t003"]
    assert active["authority"] == "docs/tasks/{tid}_{slug}/note.md front matter"
    assert archive["authority"] == (
        "docs/archive/tasks/{tid}_{slug}/note.md front matter"
    )


def test_rebuild_index_uses_utf8_and_lf(fake_repo):
    _make_task(ctx.TASKS_DIR, "t001", "alpha", "backlog")
    rebuild_index()
    raw = ctx.ACTIVE_PATH.read_bytes()
    assert b"\r\n" not in raw
    # ensure_ascii=False：中文不转义
    text = raw.decode("utf-8")
    assert '"tasks"' in text


def test_unlink_managed_env_links_only_removes_script_links(fake_repo):
    source = fake_repo / ".env"
    source.write_text("secret", encoding="utf-8")
    worktree = fake_repo / "worktree"
    worktree.mkdir()

    assert worktrees.link_local_env(worktree) == [".env"]
    managed = worktree / ".env"
    assert worktrees.is_managed_env_link(worktree, managed)

    nested = worktree / "config"
    nested.mkdir()
    other = fake_repo / "other.env"
    other.write_text("other", encoding="utf-8")
    unmanaged = nested / ".env"
    unmanaged.symlink_to(other)
    assert not worktrees.is_managed_env_link(worktree, unmanaged)

    worktrees.unlink_managed_env_links(worktree)
    assert not managed.is_symlink()
    assert unmanaged.is_symlink()


def test_managed_env_link_stays_identifiable_after_source_removed(fake_repo):
    source = fake_repo / ".env"
    source.write_text("secret", encoding="utf-8")
    worktree = fake_repo / "worktree"
    worktree.mkdir()
    worktrees.link_local_env(worktree)
    source.unlink()

    link = worktree / ".env"
    assert worktrees.is_managed_env_link(worktree, link)
    worktrees.unlink_managed_env_links(worktree)
    assert not link.is_symlink()


def test_close_task_rolls_back_when_move_fails(fake_repo, monkeypatch):
    """归档 move 失败时完整回滚 front matter（含 status/worktree/note），避免中间态（Issue #51）。"""
    import argparse

    d = _make_task(ctx.TASKS_DIR, "t001", "alpha", "active")
    write_front_matter(
        d / "note.md",
        {
            "tid": "t001", "slug": "alpha", "status": "active",
            "worktree": "worktrees/t001_wt", "note": "original note",
        },
        "body\n",
    )

    def boom(src, dst):
        raise OSError("模拟移动失败")

    monkeypatch.setattr(lifecycle.shutil, "move", boom)
    monkeypatch.setattr(lifecycle, "require_own_task_worktree", lambda fm: None)
    monkeypatch.setattr(lifecycle, "in_own_task_worktree", lambda fm: False)
    monkeypatch.setattr(lifecycle, "remove_worktree", lambda wt, **kw: (True, ""))
    monkeypatch.setattr(worktrees, "worktree_paths", lambda: {})
    with pytest.raises(SystemExit, match="归档移动失败"):
        lifecycle._close_task(argparse.Namespace(tid="t001"), "dropped", "dropped: reason")
    fm, _ = parse_front_matter(d / "note.md")
    # 全部字段完整回滚原状
    assert fm["status"] == "active"
    assert fm["worktree"] == "worktrees/t001_wt"
    assert fm["note"] == "original note"
    assert d.is_dir()  # 目录仍在活跃区


def test_list_is_readonly_by_default(fake_repo, capsys):
    """list 只罗列，不写派生 index。"""
    import argparse

    _make_task(ctx.TASKS_DIR, "t001", "alpha", "backlog")
    assert not ctx.ACTIVE_PATH.exists()
    lifecycle.cmd_list(argparse.Namespace(status=None, rebuild=False, ref=None))
    capsys.readouterr()
    assert not ctx.ACTIVE_PATH.exists()


def test_list_rebuild_writes_index(fake_repo, capsys, monkeypatch):
    import argparse

    _make_task(ctx.TASKS_DIR, "t001", "alpha", "backlog")
    monkeypatch.setattr(lifecycle, "require_primary_worktree", lambda: None)
    lifecycle.cmd_list(argparse.Namespace(status=None, rebuild=True, ref=None))
    out = capsys.readouterr().out
    assert ctx.ACTIVE_PATH.exists()
    assert "index rebuilt" in out


def test_purge_tombstone_cleanup_failure_warns_and_completes_purge(fake_repo, capsys, monkeypatch):
    """验证 purge 最终临时文件清理失败时区分逻辑完成与物理清理，并告警残留位置（Issue #65）。"""
    import argparse
    from contextlib import contextmanager

    @contextmanager
    def noop_lock(*args, **kwargs):
        yield

    monkeypatch.setattr("repo_task.locks.git_common_lock", noop_lock)

    d = _make_task(ctx.TASKS_DIR, "t001", "alpha", "backlog")
    monkeypatch.setattr(lifecycle, "require_primary_worktree", lambda: None)
    monkeypatch.setattr(lifecycle, "task_schedule_references", lambda tid: [])
    monkeypatch.setattr(lifecycle, "tracked_anywhere", lambda p: False)
    monkeypatch.setattr(lifecycle, "has_unmerged_commits", lambda b: False)
    monkeypatch.setattr(lifecycle, "remove_worktree", lambda wt, **kw: (True, ""))
    audit_file = fake_repo / "docs/archive/tasks_audit.log"
    monkeypatch.setattr(ctx, "AUDIT_PATH", audit_file)

    def boom_rmtree(path):
        raise OSError("locked tombstone")

    monkeypatch.setattr(lifecycle.shutil, "rmtree", boom_rmtree)

    # 运行 purge，不应抛出未捕获异常退出
    lifecycle.cmd_purge(argparse.Namespace(tid="t001", reason="miscreated"))
    out = capsys.readouterr().out

    # 逻辑完成：tid 已释放，审计日志已记录
    assert "purged（tid 已释放" in out
    assert "警告：临时文件清理失败" in out
    assert "purge_t001_alpha" in out
    assert not d.exists()
    assert audit_file.exists()
    assert "miscreated" in audit_file.read_text(encoding="utf-8")

