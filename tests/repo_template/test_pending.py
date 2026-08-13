"""_id_scan.py 跨分支/跨 worktree 编号扫描、互斥分配与 pending/findings CLI。

条目为「一条目一文件」，编号来自文件名。测试用临时 git 仓库（tmp_path 下 init）
模拟多分支、多 worktree 与未提交条目，覆盖扫描、并发取号、状态迁移与 CLI 行为。
"""

import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "repo_template"
sys.path.insert(0, str(SCRIPTS_DIR))

from _id_scan import IdScanError, allocate, allocate_dir, scan_max_id
import pending as pending_mod
import findings as findings_mod
import spikes as spikes_mod

PENDING_DIRS = pending_mod.SCAN_DIRS
FINDING_DIRS = findings_mod.SCAN_DIRS
SPIKE_DIRS = spikes_mod.SCAN_DIRS


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8", errors="replace",
        check=True,
    )


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "docs" / "pending" / "todo").mkdir(parents=True)
    (repo / "docs" / "pending" / "todo" / ".gitkeep").write_text("", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _commit_entry(repo, rel, text="# entry\n", msg="add entry"):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", msg)


def _bind(monkeypatch, repo):
    """把 pending/findings 模块的路径常量重绑到临时仓库。"""
    monkeypatch.setattr(pending_mod, "REPO_ROOT", repo)
    monkeypatch.setattr(pending_mod, "TODO_DIR", repo / "docs/pending/todo")
    monkeypatch.setattr(pending_mod, "PARKED_DIR", repo / "docs/pending/parked")
    monkeypatch.setattr(pending_mod, "ARCHIVE_DIR", repo / "docs/archive/pending")
    monkeypatch.setattr(
        pending_mod,
        "STATE_DIRS",
        {
            "todo": repo / "docs/pending/todo",
            "parked": repo / "docs/pending/parked",
            "archived": repo / "docs/archive/pending",
        },
    )
    monkeypatch.setattr(findings_mod, "REPO_ROOT", repo)
    monkeypatch.setattr(findings_mod, "FINDINGS_DIR", repo / "docs/findings")
    monkeypatch.setattr(spikes_mod, "REPO_ROOT", repo)
    monkeypatch.setattr(spikes_mod, "SPIKES_DIR", repo / "docs/spikes")
    monkeypatch.setattr(spikes_mod, "ARCHIVE_DIR", repo / "docs/archive/spikes")
    monkeypatch.setattr(
        spikes_mod, "TEMPLATE_PATH", repo / "docs/spikes/report_template.md"
    )


# ---------- 编号扫描 ----------


def test_empty_history_starts_at_zero(tmp_path):
    repo = _init_repo(tmp_path)
    assert scan_max_id(repo, "p", PENDING_DIRS) == 0


def test_todo_entries_drive_max(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/pending/todo/p007_bug.md")
    _commit_entry(repo, "docs/pending/todo/p002_debt.md")
    assert scan_max_id(repo, "p", PENDING_DIRS) == 7


def test_archived_entries_count(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/pending/todo/p002_open.md")
    _commit_entry(repo, "docs/archive/pending/p027_closed.md")
    assert scan_max_id(repo, "p", PENDING_DIRS) == 27


def test_parked_entries_count(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/pending/parked/p013_later.md")
    assert scan_max_id(repo, "p", PENDING_DIRS) == 13


def test_non_entry_filenames_ignored(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/pending/README.md")
    _commit_entry(repo, "docs/pending/todo/p01_short.md")
    _commit_entry(repo, "docs/pending/todo/p012_Upper.md")
    _commit_entry(repo, "docs/pending/todo/p013_ok.md")
    assert scan_max_id(repo, "p", PENDING_DIRS) == 13


def test_ids_can_exceed_999(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/pending/todo/p1024_big.md")
    assert scan_max_id(repo, "p", PENDING_DIRS) == 1024


def test_other_branch_bigger_id_is_picked_up(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/pending/todo/p003_main.md")
    _git(repo, "checkout", "-b", "feature")
    _commit_entry(repo, "docs/pending/todo/p005_feature.md")
    _git(repo, "checkout", "main")
    assert scan_max_id(repo, "p", PENDING_DIRS) == 5


def test_worktree_uncommitted_entry_picked_up(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/pending/todo/p003_main.md")
    worktree = tmp_path / "wt"
    _git(repo, "worktree", "add", "--detach", str(worktree))
    (worktree / "docs/pending/todo/p009_wip.md").write_text("x\n", encoding="utf-8")
    assert scan_max_id(repo, "p", PENDING_DIRS) == 9


def test_duplicate_within_source_fails(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/pending/todo/p010_open.md")
    _commit_entry(repo, "docs/archive/pending/p010_closed.md")
    with pytest.raises(IdScanError, match="p010"):
        scan_max_id(repo, "p", PENDING_DIRS)


def test_duplicate_across_sources_not_reported(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/pending/todo/p010_main.md")
    _git(repo, "checkout", "-b", "feature")
    _git(repo, "rm", "-q", "docs/pending/todo/p010_main.md")
    _commit_entry(repo, "docs/pending/todo/p010_feature.md")
    _git(repo, "checkout", "main")
    assert scan_max_id(repo, "p", PENDING_DIRS) == 10


def test_git_failure_wrapped(tmp_path):
    not_a_repo = tmp_path / "norepo"
    not_a_repo.mkdir()
    with pytest.raises(IdScanError):
        scan_max_id(not_a_repo, "p", PENDING_DIRS)


# ---------- 互斥分配 ----------


def _allocate_one(repo_and_slug):
    repo, slug = repo_and_slug
    repo = Path(repo)
    path = allocate(
        repo,
        prefix="p",
        dirs=PENDING_DIRS,
        target_dir=repo / "docs/pending/todo",
        slug=slug,
        body="# {id}\n",
    )
    return path.name


def test_concurrent_allocation_yields_unique_ids(tmp_path):
    repo = _init_repo(tmp_path)
    slugs = [f"job_{i}" for i in range(8)]
    with ProcessPoolExecutor(max_workers=8) as pool:
        names = list(pool.map(_allocate_one, [(str(repo), slug) for slug in slugs]))
    numbers = sorted(int(name[1:4]) for name in names)
    assert numbers == list(range(1, 9))


def test_allocate_writes_id_into_body(tmp_path):
    repo = _init_repo(tmp_path)
    path = allocate(
        repo,
        prefix="p",
        dirs=PENDING_DIRS,
        target_dir=repo / "docs/pending/todo",
        slug="demo",
        body="# {id} title\n",
    )
    assert path.name == "p001_demo.md"
    assert path.read_text(encoding="utf-8") == "# p001 title\n"


def test_allocate_rejects_bad_slug(tmp_path):
    repo = _init_repo(tmp_path)
    with pytest.raises(IdScanError, match="slug"):
        allocate(
            repo,
            prefix="p",
            dirs=PENDING_DIRS,
            target_dir=repo / "docs/pending/todo",
            slug="Bad-Slug",
            body="# {id}\n",
        )


# ---------- 状态迁移 ----------


def test_park_then_revive_round_trip(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])

    pending_mod.main(["park", "p001", "--reason", "2026-08-03 等依赖", "--write"])
    parked = repo / "docs/pending/parked/p001_demo.md"
    assert parked.is_file()
    text = parked.read_text(encoding="utf-8")
    assert "- 处理：不办" in text
    assert "- 暂搁：2026-08-03 等依赖" in text

    pending_mod.main(["revive", "p001", "--write"])
    revived = repo / "docs/pending/todo/p001_demo.md"
    text = revived.read_text(encoding="utf-8")
    assert "- 处理：未开" in text
    assert "暂搁" not in text


def test_archive_writes_fix_ref(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])
    pending_mod.main(["archive", "p001", "--fix-ref", "t012", "--write"])
    archived = repo / "docs/archive/pending/p001_demo.md"
    assert "- 处理：t012" in archived.read_text(encoding="utf-8")


def test_archive_defaults_to_dry_run(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])
    pending_mod.main(["archive", "p001", "--fix-ref", "t012"])
    assert (repo / "docs/pending/todo/p001_demo.md").is_file()
    assert not (repo / "docs/archive/pending/p001_demo.md").exists()


def test_archive_rejects_parked_entry(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])
    pending_mod.main(["park", "p001", "--reason", "later", "--write"])
    with pytest.raises(SystemExit, match="revive"):
        pending_mod.main(["archive", "p001", "--fix-ref", "t012", "--write"])


def test_archive_rejects_already_archived(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])
    pending_mod.main(["archive", "p001", "--fix-ref", "t012", "--write"])
    with pytest.raises(SystemExit, match="重复归档"):
        pending_mod.main(["archive", "p001", "--fix-ref", "t013", "--write"])


def test_archived_id_not_reused(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "first"])
    pending_mod.main(["archive", "p001", "--fix-ref", "t012", "--write"])
    pending_mod.main(["new", "--slug", "second"])
    assert (repo / "docs/pending/todo/p002_second.md").is_file()


def test_missing_entry_reports_error(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    with pytest.raises(SystemExit, match="未找到 p001"):
        pending_mod.main(["archive", "p001", "--fix-ref", "t012", "--write"])


def test_archive_partial_failure_rolls_back_moved_and_content(tmp_path, monkeypatch):
    """F19：批量迁移第二条失败时，第一条已迁移条目回滚位置并恢复被 set_field 改过的正文。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "one"])
    pending_mod.main(["new", "--slug", "two"])
    one = repo / "docs/pending/todo/p001_one.md"
    two = repo / "docs/pending/todo/p002_two.md"
    orig_one = one.read_bytes()
    assert one.exists() and two.exists()

    real_move = pending_mod.move_entry
    calls = {"n": 0}

    def flaky(path, target):
        calls["n"] += 1
        if calls["n"] == 2:
            raise IdScanError("模拟第二条迁移失败")
        return real_move(path, target)

    monkeypatch.setattr(pending_mod, "move_entry", flaky)
    with pytest.raises(SystemExit, match="模拟第二条迁移失败"):
        pending_mod.main(["archive", "p001", "p002", "--fix-ref", "t001", "--write"])
    # p001 已移回 todo，且正文恢复（未被 set_field 改成「处理：t001」）
    assert one.exists()
    assert one.read_bytes() == orig_one
    assert not (repo / "docs/archive/pending/p001_one.md").exists()


# ---------- CLI ----------


def test_pending_new_prints_path(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])
    assert capsys.readouterr().out == "docs/pending/todo/p001_demo.md\n"


def test_pending_new_bug_template(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "crash", "--kind", "bug"])
    text = (repo / "docs/pending/todo/p001_crash.md").read_text(encoding="utf-8")
    assert "- 现象：" in text
    assert "- 测试缺口：" in text


def test_findings_new_prints_path(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    findings_mod.main(["new", "--slug", "uv_lock_marker"])
    assert capsys.readouterr().out == "docs/findings/d001_uv_lock_marker.md\n"


def test_findings_list_empty(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    findings_mod.main(["list"])
    assert capsys.readouterr().out == "(no findings)\n"


def test_pending_list_shows_state(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])
    capsys.readouterr()
    pending_mod.main(["list"])
    out = capsys.readouterr().out
    assert "todo" in out
    assert "p001_demo" in out


def test_cli_rejects_missing_slug():
    with pytest.raises(SystemExit) as exc:
        pending_mod.main(["new"])
    assert exc.value.code == 2


# ---------- rename ----------


def test_pending_rename_dry_run_prints_target(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])
    _git(repo, "add", "-A")

    pending_mod.main(["rename", "p001", "--slug", "renamed"])

    out = capsys.readouterr().out
    assert "p001_demo.md" in out
    assert "p001_renamed.md" in out
    assert (repo / "docs/pending/todo/p001_demo.md").is_file()
    assert not (repo / "docs/pending/todo/p001_renamed.md").exists()


def test_pending_rename_writes_and_keeps_state_dir(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])
    pending_mod.main(["park", "p001", "--reason", "later", "--write"])
    _git(repo, "add", "-A")

    pending_mod.main(["rename", "p001", "--slug", "renamed", "--write"])

    renamed = repo / "docs/pending/parked/p001_renamed.md"
    assert renamed.is_file()
    assert not (repo / "docs/pending/parked/p001_demo.md").exists()


def test_pending_rename_rejects_bad_slug(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])

    with pytest.raises(SystemExit, match="slug 非法"):
        pending_mod.main(["rename", "p001", "--slug", "Bad-Slug", "--write"])


def test_pending_rename_rejects_same_slug(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])

    with pytest.raises(SystemExit, match="相同"):
        pending_mod.main(["rename", "p001", "--slug", "demo", "--write"])


# 旧用例 test_pending_rename_rejects_existing_target 已删除：目标名 p001_occupied.md
# 被占用必然意味着同号多文件，rename 现在由 find_entry 的歧义检测前置拦截
# （见 test_pending_rename_rejects_duplicated_id），「目标文件已存在」分支不再可达。


def test_findings_rename_dry_run_prints_target(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    findings_mod.main(["new", "--slug", "demo"])
    _git(repo, "add", "-A")

    findings_mod.main(["rename", "d001", "--slug", "renamed"])

    out = capsys.readouterr().out
    assert "d001_demo.md" in out
    assert "d001_renamed.md" in out
    assert (repo / "docs/findings/d001_demo.md").is_file()
    assert not (repo / "docs/findings/d001_renamed.md").exists()


def test_findings_rename_writes(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    findings_mod.main(["new", "--slug", "demo"])
    _git(repo, "add", "-A")

    findings_mod.main(["rename", "d001", "--slug", "renamed", "--write"])

    assert (repo / "docs/findings/d001_renamed.md").is_file()
    assert not (repo / "docs/findings/d001_demo.md").exists()


def test_pending_rename_untracked_entry_uses_plain_rename(tmp_path, monkeypatch):
    """新建条目尚未 git add 时 rename 退化为普通改名，不经 git mv。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])

    pending_mod.main(["rename", "p001", "--slug", "renamed", "--write"])

    renamed = repo / "docs/pending/todo/p001_renamed.md"
    assert renamed.is_file()
    assert not (repo / "docs/pending/todo/p001_demo.md").exists()


def test_findings_rename_untracked_entry_uses_plain_rename(tmp_path, monkeypatch):
    """findings 的 rename 同样对未入库条目退化为普通改名。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    findings_mod.main(["new", "--slug", "demo"])

    findings_mod.main(["rename", "d001", "--slug", "renamed", "--write"])

    assert (repo / "docs/findings/d001_renamed.md").is_file()
    assert not (repo / "docs/findings/d001_demo.md").exists()


def test_pending_rename_rejects_duplicated_id(tmp_path, monkeypatch):
    """同号存在于 todo 与 parked 两处时 rename 拒绝并列出全部候选路径。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])
    parked = repo / "docs/pending/parked"
    parked.mkdir(parents=True, exist_ok=True)
    (parked / "p001_demo.md").write_text("# p001 副本\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        pending_mod.main(["rename", "p001", "--slug", "final", "--write"])

    message = str(exc.value)
    assert "同时存在于多处" in message
    assert "docs/pending/todo/p001_demo.md" in message
    assert "docs/pending/parked/p001_demo.md" in message
    # 两侧文件均保持原样，歧义不被静默固化
    assert (repo / "docs/pending/todo/p001_demo.md").is_file()
    assert (parked / "p001_demo.md").is_file()
    assert not (repo / "docs/pending/todo/p001_final.md").exists()


def test_findings_rename_rejects_duplicated_id(tmp_path, monkeypatch):
    """同号多文件时 findings rename 拒绝并列出全部候选路径。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    findings_mod.main(["new", "--slug", "demo"])
    (repo / "docs/findings/d001_copy.md").write_text("# d001 副本\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        findings_mod.main(["rename", "d001", "--slug", "final", "--write"])

    message = str(exc.value)
    assert "同时存在于多处" in message
    assert "docs/findings/d001_demo.md" in message
    assert "docs/findings/d001_copy.md" in message
    assert (repo / "docs/findings/d001_demo.md").is_file()
    assert not (repo / "docs/findings/d001_final.md").exists()


# ---------- 目录型条目（spike） ----------


def _init_spikes(repo):
    template = repo / "docs/spikes/report_template.md"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text("# {id} spike 报告\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add spike template")


def test_spike_dirs_drive_max(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/spikes/s004_probe/report.md")
    _commit_entry(repo, "docs/archive/spikes/s009_old/report.md")
    assert scan_max_id(repo, "s", SPIKE_DIRS, kind="dir") == 9


def test_spike_scan_ignores_template_and_loose_files(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/spikes/report_template.md")
    _commit_entry(repo, "docs/spikes/s003_probe/report.md")
    assert scan_max_id(repo, "s", SPIKE_DIRS, kind="dir") == 3


def test_spike_nested_files_counted_once(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/spikes/s002_probe/report.md")
    _commit_entry(repo, "docs/spikes/s002_probe/code/run.md")
    assert scan_max_id(repo, "s", SPIKE_DIRS, kind="dir") == 2


def _allocate_spike(repo_and_slug):
    repo, slug = repo_and_slug
    repo = Path(repo)
    path = allocate_dir(
        repo,
        prefix="s",
        dirs=SPIKE_DIRS,
        target_dir=repo / "docs/spikes",
        slug=slug,
        files={"report.md": "# {id}\n"},
    )
    return path.name


def test_concurrent_spike_allocation_yields_unique_ids(tmp_path):
    repo = _init_repo(tmp_path)
    slugs = [f"probe_{i}" for i in range(6)]
    with ProcessPoolExecutor(max_workers=6) as pool:
        names = list(pool.map(_allocate_spike, [(str(repo), slug) for slug in slugs]))
    numbers = sorted(int(name[1:4]) for name in names)
    assert numbers == list(range(1, 7))


def test_spikes_new_creates_dir_from_template(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    _init_spikes(repo)
    _bind(monkeypatch, repo)
    spikes_mod.main(["new", "--slug", "uv_lock"])
    assert capsys.readouterr().out == "docs/spikes/s001_uv_lock\n"
    report = repo / "docs/spikes/s001_uv_lock/report.md"
    assert report.read_text(encoding="utf-8") == "# s001 spike 报告\n"


def test_spikes_new_requires_template(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    with pytest.raises(SystemExit, match="缺模板"):
        spikes_mod.main(["new", "--slug", "uv_lock"])


def test_spikes_list_reports_state(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    _init_spikes(repo)
    _bind(monkeypatch, repo)
    spikes_mod.main(["new", "--slug", "uv_lock"])
    (repo / "docs/archive/spikes/s000_old").mkdir(parents=True)
    capsys.readouterr()
    spikes_mod.main(["list"])
    out = capsys.readouterr().out
    assert "active    s001_uv_lock" in out
    assert "archived  s000_old" in out


# ---------- git mv 迁移（已入库条目） ----------


def test_archive_uses_git_mv_and_preserves_history(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add p001")

    pending_mod.main(["archive", "p001", "--fix-ref", "t012", "--write"])

    staged = _git(repo, "diff", "--cached", "--name-status", "-M").stdout
    assert "docs/pending/todo/p001_demo.md" in staged
    assert "docs/archive/pending/p001_demo.md" in staged
    assert not (repo / "docs/pending/todo/p001_demo.md").exists()

    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "archive p001")
    history = _git(
        repo, "log", "--follow", "--format=%s", "--", "docs/archive/pending/p001_demo.md"
    ).stdout
    assert "add p001" in history


def test_park_uses_git_mv_for_committed_entry(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add p001")

    pending_mod.main(["park", "p001", "--reason", "later", "--write"])

    staged = _git(repo, "diff", "--cached", "--name-status", "-M").stdout
    assert "docs/pending/parked/p001_demo.md" in staged
    assert (repo / "docs/pending/parked/p001_demo.md").is_file()


def test_move_refuses_to_overwrite_existing_destination(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])
    archived = repo / "docs/archive/pending"
    archived.mkdir(parents=True, exist_ok=True)
    (archived / "p001_demo.md").write_text("occupied\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="同时存在于多处"):
        pending_mod.main(["archive", "p001", "--fix-ref", "t012", "--write"])


# ---------- legacy 单文件正文编号（旧格式分支） ----------


def test_branch_legacy_pending_body_numbers_counted(tmp_path):
    """未合并旧分支的 docs/pending.md 小节标题编号计入最大值；正文引用不计。"""
    repo = _init_repo(tmp_path)
    _git(repo, "checkout", "-b", "legacy")
    _commit_entry(
        repo,
        "docs/pending.md",
        text=(
            "# 待办与不办总账\n\n## 待办\n\n"
            "### p047 cli 退出码（2026-07-01 发现）\n\n"
            "- 现象：退出码恒为 0\n"
            "- 线索：关联 p099 一并处理\n"
            "- 处理：未开\n"
        ),
    )
    _git(repo, "checkout", "main")
    assert scan_max_id(repo, "p", PENDING_DIRS) == 47


def test_branch_legacy_findings_body_numbers_counted(tmp_path):
    """未合并旧分支的 docs/findings.md 小节标题编号计入最大值。"""
    repo = _init_repo(tmp_path)
    _git(repo, "checkout", "-b", "legacy")
    _commit_entry(
        repo,
        "docs/findings.md",
        text="# 发现总账\n\n## d012 uv lock 平台标记（2026-07-01）\n\n- 现状：有效\n",
    )
    _git(repo, "checkout", "main")
    assert scan_max_id(repo, "d", FINDING_DIRS) == 12


def test_branch_legacy_archive_ledger_body_numbers_counted(tmp_path):
    """旧格式归档总账 docs/archive/pending.md 的正文编号同样计入。"""
    repo = _init_repo(tmp_path)
    _git(repo, "checkout", "-b", "legacy")
    _commit_entry(
        repo,
        "docs/archive/pending.md",
        text="# 已闭环待办\n\n### p051 旧事（2026-06-01 闭环）\n\n- 处理：t001\n",
    )
    _git(repo, "checkout", "main")
    assert scan_max_id(repo, "p", PENDING_DIRS) == 51


def test_allocate_skips_legacy_body_numbers(tmp_path, monkeypatch):
    """旧分支单文件里的编号不会被 new 重复分配。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    _git(repo, "checkout", "-b", "legacy")
    _commit_entry(
        repo,
        "docs/pending.md",
        text="# 待办\n\n### p047 cli 退出码（2026-07-01 发现）\n\n- 处理：未开\n",
    )
    _git(repo, "checkout", "main")
    pending_mod.main(["new", "--slug", "demo"])
    assert (repo / "docs/pending/todo/p048_demo.md").is_file()


def test_branch_legacy_body_number_conflicts_with_entry_file(tmp_path):
    """legacy 正文编号与同分支条目文件同号时报重复（迁移残留）。"""
    repo = _init_repo(tmp_path)
    _commit_entry(
        repo,
        "docs/pending.md",
        text="# 待办\n\n### p010 旧事（2026-06-01 发现）\n\n- 处理：未开\n",
    )
    _commit_entry(repo, "docs/pending/todo/p010_open.md")
    with pytest.raises(IdScanError, match="p010"):
        scan_max_id(repo, "p", PENDING_DIRS)


# ---------- 分支内 active/archive 同号迁移残留 ----------


def test_spike_duplicate_active_archive_within_branch_fails(tmp_path):
    """分支内同号 spike 目录同时出现在 active 与 archive 两侧时报重复。

    迁移是移动而非复制，即使两侧内容一致也属于残留；与工作区扫描口径一致。
    """
    repo = _init_repo(tmp_path)
    _git(repo, "checkout", "-b", "feature")
    _commit_entry(repo, "docs/spikes/s012_probe/report.md", text="# s012\n")
    _commit_entry(repo, "docs/archive/spikes/s012_probe/report.md", text="# s012\n")
    _git(repo, "checkout", "main")
    with pytest.raises(IdScanError, match="s012"):
        scan_max_id(repo, "s", SPIKE_DIRS, kind="dir")
