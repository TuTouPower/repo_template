"""task.py 目录扫描与原位状态一致性。

通过 monkeypatch 把 task.py 全局路径指向 tmp_path 下临时目录，
不依赖真实仓库结构、不触发 git。task 状态（backlog/active/done/dropped）
原位表达在 docs/tasks/，不迁入 archive。
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import pytest
from repo_task import context as ctx
from repo_task.context import RepoContext
from repo_task import lifecycle, worktrees
from repo_task.context import TaskDataError
from repo_task.documents import parse_front_matter, write_front_matter
from repo_task.store import scan_tasks
from review_support import configure_project_verification

pytestmark = pytest.mark.contract


@pytest.fixture
def fake_repo(tmp_path):
    """把 task.py 全局路径指向 tmp_path 下临时目录，并建空模板目录。"""
    tasks = tmp_path / "docs" / "tasks"
    template = tasks / "task_template"
    tasks.mkdir(parents=True)
    template.mkdir()
    configure_project_verification(tmp_path)
    # 模板目录必须含 progress.md，scan_tasks 跳过 task_template/
    write_front_matter(
        template / "progress.md",
        {"tid": "t000", "slug": "example", "status": "backlog"},
        "模板正文\n",
    )
    return tmp_path


@pytest.fixture
def repo_ctx(fake_repo):
    return RepoContext.for_root(fake_repo)


def _docs_snapshot(root: Path) -> set[str]:
    """docs/ 全树文件集合快照（含目录），用于验证只读命令零派生写入。"""
    docs = root / "docs"
    if not docs.is_dir():
        return set()
    return {p.relative_to(root).as_posix() for p in docs.rglob("*")}


def _make_task(tasks_dir, tid, slug, status):
    d = tasks_dir / f"{tid}_{slug}"
    d.mkdir()
    write_front_matter(
        d / "progress.md",
        {"tid": tid, "slug": slug, "status": status},
        "body\n\n- specs_index: not_applicable: isolated archive lifecycle test\n",
    )
    (d / "task.md").write_text("# isolated task\n", encoding="utf-8")
    return d


def test_scan_empty(fake_repo, repo_ctx):
    assert scan_tasks(repo=repo_ctx) == []


def test_scan_finds_active_task(fake_repo, repo_ctx):
    _make_task(repo_ctx.tasks_dir, "t001", "alpha", "active")
    rows = scan_tasks(repo=repo_ctx)
    assert len(rows) == 1
    assert rows[0]["tid"] == "t001"
    assert rows[0]["status"] == "active"


def test_scan_finds_done_and_dropped_tasks(fake_repo, repo_ctx):
    _make_task(repo_ctx.tasks_dir, "t002", "beta", "done")
    _make_task(repo_ctx.tasks_dir, "t003", "gamma", "dropped")
    rows = scan_tasks(repo=repo_ctx)
    assert len(rows) == 2
    assert [(r["tid"], r["status"]) for r in rows] == [("t002", "done"), ("t003", "dropped")]


def test_scan_rejects_invalid_tid(fake_repo, repo_ctx):
    _make_task(repo_ctx.tasks_dir, "x99", "bad", "backlog")
    with pytest.raises(TaskDataError, match="tid 非法"):
        scan_tasks(repo=repo_ctx)


def test_scan_rejects_dirname_mismatch(fake_repo, repo_ctx):
    d = repo_ctx.tasks_dir / "t003_wrong"
    d.mkdir()
    write_front_matter(
        d / "progress.md",
        {"tid": "t003", "slug": "right", "status": "backlog"},
        "x\n",
    )
    with pytest.raises(TaskDataError, match="目录名与 front matter 不符"):
        scan_tasks(repo=repo_ctx)


def test_scan_rejects_invalid_status(fake_repo, repo_ctx):
    _make_task(repo_ctx.tasks_dir, "t004", "gamma", "weird")
    with pytest.raises(TaskDataError, match="status 非法"):
        scan_tasks(repo=repo_ctx)


def test_scan_all_valid_statuses_in_tasks_dir(fake_repo, repo_ctx):
    _make_task(repo_ctx.tasks_dir, "t001", "a", "backlog")
    _make_task(repo_ctx.tasks_dir, "t002", "b", "active")
    _make_task(repo_ctx.tasks_dir, "t003", "c", "done")
    _make_task(repo_ctx.tasks_dir, "t004", "d", "dropped")
    rows = scan_tasks(repo=repo_ctx)
    assert [(r["tid"], r["status"]) for r in rows] == [
        ("t001", "backlog"), ("t002", "active"), ("t003", "done"), ("t004", "dropped")
    ]


def test_scan_accepts_done_in_tasks_dir(fake_repo, repo_ctx):
    _make_task(repo_ctx.tasks_dir, "t007", "zeta", "done")
    rows = scan_tasks(repo=repo_ctx)
    assert [(row["tid"], row["status"], row["dir"]) for row in rows] == [
        ("t007", "done", "docs/tasks/t007_zeta")
    ]


def test_scan_accepts_dropped_in_tasks_dir(fake_repo, repo_ctx):
    _make_task(repo_ctx.tasks_dir, "t008", "eta", "dropped")
    rows = scan_tasks(repo=repo_ctx)
    assert [(row["tid"], row["status"], row["dir"]) for row in rows] == [
        ("t008", "dropped", "docs/tasks/t008_eta")
    ]


def test_scan_detects_dup_tid(fake_repo, repo_ctx):
    _make_task(repo_ctx.tasks_dir, "t006", "eps", "backlog")
    d = repo_ctx.tasks_dir / "t006_dup"
    d.mkdir()
    write_front_matter(
        d / "progress.md",
        {"tid": "t006", "slug": "dup", "status": "done"},
        "x\n",
    )
    with pytest.raises(TaskDataError, match="重复 tid"):
        scan_tasks(repo=repo_ctx)


def test_scan_orders_by_tid_numerically(fake_repo, repo_ctx):
    _make_task(repo_ctx.tasks_dir, "t010", "ten", "backlog")
    _make_task(repo_ctx.tasks_dir, "t002", "two", "backlog")
    _make_task(repo_ctx.tasks_dir, "t100", "hundred", "backlog")
    rows = scan_tasks(repo=repo_ctx)
    tids = [r["tid"] for r in rows]
    assert tids == ["t002", "t010", "t100"]


def test_unlink_managed_links_only_removes_script_links(fake_repo, repo_ctx):
    source = fake_repo / ".env"
    source.write_text("secret", encoding="utf-8")
    worktree = fake_repo / "worktree"
    worktree.mkdir()

    assert worktrees.link_local_files(worktree, repo=repo_ctx) == [".env"]
    managed = worktree / ".env"
    assert worktrees.is_managed_link(worktree, managed, repo=repo_ctx)

    nested = worktree / "config"
    nested.mkdir()
    other = fake_repo / "other.env"
    other.write_text("other", encoding="utf-8")
    unmanaged = nested / ".env"
    unmanaged.symlink_to(other)
    assert not worktrees.is_managed_link(worktree, unmanaged, repo=repo_ctx)

    worktrees.unlink_managed_links(worktree, repo=repo_ctx)
    assert not managed.is_symlink()
    assert unmanaged.is_symlink()


def test_managed_env_link_stays_identifiable_after_source_removed(fake_repo, repo_ctx):
    source = fake_repo / ".env"
    source.write_text("secret", encoding="utf-8")
    worktree = fake_repo / "worktree"
    worktree.mkdir()
    worktrees.link_local_files(worktree, repo=repo_ctx)
    source.unlink()

    link = worktree / ".env"
    assert worktrees.is_managed_link(worktree, link, repo=repo_ctx)
    worktrees.unlink_managed_links(worktree, repo=repo_ctx)
    assert not link.is_symlink()


def test_link_local_files_does_not_query_git_worktrees(fake_repo, repo_ctx, monkeypatch):
    """链接本地配置不依赖 git worktree 发现：临时目录落在 git 仓库内也必须判绿。"""
    def forbidden():
        raise AssertionError("link_local_files 不应查询 git worktree 登记表")

    monkeypatch.setattr(worktrees, "worktree_paths", forbidden)
    source = fake_repo / ".env"
    source.write_text("secret", encoding="utf-8")
    worktree = fake_repo / "worktree"
    worktree.mkdir()
    assert worktrees.link_local_files(worktree, repo=repo_ctx) == [".env"]


def test_link_local_files_skips_sources_inside_target_worktree(fake_repo, repo_ctx):
    """目标 worktree 落在主仓内时，其内部的 .env 不得被当成主仓源链入。"""
    source = fake_repo / ".env"
    source.write_text("secret", encoding="utf-8")
    worktree = fake_repo / "worktree"
    worktree.mkdir()
    nested = worktree / "nested"
    nested.mkdir()
    (nested / ".env").write_text("nested", encoding="utf-8")

    assert worktrees.link_local_files(worktree, repo=repo_ctx) == [".env"]
    assert not (worktree / "nested" / ".env").is_symlink()


def test_close_task_in_place_drop(fake_repo, repo_ctx, monkeypatch):
    """drop 原位写 status: dropped，目录保留在 docs/tasks/{tid}_{slug}。"""
    import argparse

    d = _make_task(repo_ctx.tasks_dir, "t001", "alpha", "active")
    write_front_matter(
        d / "progress.md",
        {
            "tid": "t001", "slug": "alpha", "status": "active",
            "worktree": "worktrees/t001_wt", "note": "original note",
        },
        "body\n",
    )
    monkeypatch.setattr(lifecycle, "require_own_task_worktree", lambda fm, **kwargs: None)
    monkeypatch.setattr(lifecycle, "in_own_task_worktree", lambda fm, **kwargs: False)
    monkeypatch.setattr(lifecycle, "remove_worktree", lambda wt, **kw: (True, ""))

    lifecycle._close_task(argparse.Namespace(tid="t001"), "dropped", "dropped: reason", repo=repo_ctx)
    fm, _ = parse_front_matter(d / "progress.md", repo=repo_ctx)
    assert fm["status"] == "dropped"
    assert "dropped: reason" in fm["note"]
    assert d.is_dir()
    rows = scan_tasks(repo=repo_ctx)
    assert len(rows) == 1
    assert rows[0]["status"] == "dropped"
    assert rows[0]["dir"] == "docs/tasks/t001_alpha"


def test_list_is_readonly_and_never_writes_index(fake_repo, repo_ctx, capsys):
    """A14：task 文件是唯一权威；list 只读，不生成任何派生索引文件（DS L294）。"""
    import argparse

    _make_task(repo_ctx.tasks_dir, "t001", "alpha", "backlog")
    before = _docs_snapshot(fake_repo)
    lifecycle.cmd_list(argparse.Namespace(status=None, ref=None, json=False, repo=repo_ctx))
    capsys.readouterr()
    assert _docs_snapshot(fake_repo) == before
    assert not (fake_repo / "docs" / "tasks_index.json").exists()
    assert not (fake_repo / "docs/archive/tasks_index.json").exists()


def test_list_json_exports_to_stdout_without_writing(fake_repo, repo_ctx, capsys):
    """A14：机器可读导出只到 stdout，不落盘（DS L294）。"""
    import argparse

    _make_task(repo_ctx.tasks_dir, "t001", "alpha", "backlog")
    before = _docs_snapshot(fake_repo)
    lifecycle.cmd_list(argparse.Namespace(status=None, ref=None, json=True, repo=repo_ctx))
    payload = json.loads(capsys.readouterr().out)
    assert [task["tid"] for task in payload["tasks"]] == ["t001"]
    assert _docs_snapshot(fake_repo) == before
    assert not (fake_repo / "docs" / "tasks_index.json").exists()
    assert not (fake_repo / "docs/archive/tasks_index.json").exists()


def test_purge_tombstone_cleanup_failure_warns_and_completes_purge(fake_repo, repo_ctx, capsys, monkeypatch):
    """验证 purge 最终临时文件清理失败时区分逻辑完成与物理清理，并告警残留位置（Issue #65）。"""
    import argparse
    from contextlib import contextmanager

    @contextmanager
    def noop_lock(*args, **kwargs):
        yield

    monkeypatch.setattr("repo_task.locks.git_common_lock", noop_lock)

    d = _make_task(repo_ctx.tasks_dir, "t001", "alpha", "backlog")
    monkeypatch.setattr(lifecycle, "require_primary_worktree", lambda **kwargs: None)
    monkeypatch.setattr(lifecycle, "task_schedule_references", lambda tid, **kwargs: [])
    monkeypatch.setattr(lifecycle, "tracked_anywhere", lambda p, **kwargs: False)
    monkeypatch.setattr(lifecycle, "has_unmerged_commits", lambda b, **kwargs: False)
    monkeypatch.setattr(lifecycle, "remove_worktree", lambda wt, **kw: (True, ""))
    audit_file = repo_ctx.audit_path

    def boom_rmtree(path):
        raise OSError("locked tombstone")

    monkeypatch.setattr(lifecycle, "shutil", SimpleNamespace(**{**vars(lifecycle.shutil), "rmtree": boom_rmtree}))

    # 运行 purge，不应抛出未捕获异常退出
    lifecycle.cmd_purge(argparse.Namespace(tid="t001", reason="miscreated", repo=repo_ctx))
    out = capsys.readouterr().out

    # 逻辑完成：tid 已释放，审计日志已记录
    assert "purged（tid 已释放" in out
    assert "警告：临时文件清理失败" in out
    assert "purge_t001_alpha" in out
    assert not d.exists()
    assert audit_file.exists()
    assert "miscreated" in audit_file.read_text(encoding="utf-8")


def test_purge_audit_failure_restores_directory(fake_repo, repo_ctx, monkeypatch):
    """验证 purge 在审计阶段失败时恢复目录，不留已删但无审计的现场。"""
    import argparse
    from contextlib import contextmanager

    @contextmanager
    def noop_lock(*args, **kwargs):
        yield

    monkeypatch.setattr("repo_task.locks.git_common_lock", noop_lock)

    d = _make_task(repo_ctx.tasks_dir, "t001", "alpha", "backlog")

    monkeypatch.setattr(lifecycle, "require_primary_worktree", lambda **kwargs: None)
    monkeypatch.setattr(lifecycle, "task_schedule_references", lambda tid, **kwargs: [])
    monkeypatch.setattr(lifecycle, "tracked_anywhere", lambda p, **kwargs: False)
    monkeypatch.setattr(lifecycle, "has_unmerged_commits", lambda b, **kwargs: False)
    monkeypatch.setattr(lifecycle, "remove_worktree", lambda wt, **kw: (True, ""))

    def boom_audit(*args, **kwargs):
        raise ctx.TaskDataError("simulated audit failure")

    monkeypatch.setattr(lifecycle, "append_audit", boom_audit)

    with pytest.raises(SystemExit, match="purge 收尾失败.*已完整恢复"):
        lifecycle.cmd_purge(argparse.Namespace(tid="t001", reason="miscreated", repo=repo_ctx))

    # 目录恢复，权威数据仍在 task 文件
    assert d.exists()
    assert [row["tid"] for row in scan_tasks(repo=repo_ctx)] == ["t001"]


def test_close_task_in_place_done(fake_repo, repo_ctx, monkeypatch):
    """done 原位写 status: done，目录保留在 docs/tasks/{tid}_{slug}。"""
    import argparse

    d = _make_task(repo_ctx.tasks_dir, "t001", "alpha", "active")
    write_front_matter(
        d / "progress.md",
        {"tid": "t001", "slug": "alpha", "status": "active", "worktree": "worktrees/t001_wt", "note": "base note"},
        "body\n",
    )
    monkeypatch.setattr(lifecycle, "require_own_task_worktree", lambda fm, **kwargs: None)
    monkeypatch.setattr(lifecycle, "in_own_task_worktree", lambda fm, **kwargs: True)
    monkeypatch.setattr(lifecycle, "_check_done_ready", lambda tid, d, repo=None: None)

    lifecycle._close_task(argparse.Namespace(tid="t001"), "done", None, repo=repo_ctx)

    fm, _ = parse_front_matter(d / "progress.md", repo=repo_ctx)
    assert fm["status"] == "done"
    assert d.is_dir()
    rows = scan_tasks(repo=repo_ctx)
    assert len(rows) == 1
    assert rows[0]["status"] == "done"
    assert rows[0]["dir"] == "docs/tasks/t001_alpha"


