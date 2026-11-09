"""fix2/legacy 主题回归：P1-4/P1-5 与 P2 工具链修复。

覆盖旧总账 fence/缩进/孤儿保留、迁移并发幂等与软链拒绝、repo_hygiene 忽略过滤与回滚、
md_format 大小写黑名单、spike 规范编号、staging 回收、repo_state 精确计数、
GIT_* 剥离、cleanup 旧审计保护、kind=dir tree 校验。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

FACTORY_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = FACTORY_DIR.parent / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(FACTORY_DIR))

from git_repo_support import get_controlled_git_env  # noqa: E402


def _git(repo: Path, *args: str, check: bool = True):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=check, env=get_controlled_git_env(),
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "docs/pending").mkdir(parents=True)
    (repo / "docs/pending/.gitkeep").write_text("", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


# ---------- P1-4 ----------

def test_legacy_sibling_heading_retained():
    from _id_scan import legacy_ledger_entries
    text = "## p001 first\n\nbody1\n\n## Related links\n\nkeep me\n\n## p002 second\n\nbody2\n"
    entries = legacy_ledger_entries(text, "p")
    assert len(entries) == 2
    assert "Related" in entries[0][2] and "keep me" in entries[0][2]


def test_legacy_indented_heading_parsed():
    from _id_scan import legacy_ledger_entries
    text = "## p001 a\n\nx\n\n   ## p002 indented\n\ny\n"
    entries = legacy_ledger_entries(text, "p")
    assert [n for n, _, _ in entries] == [1, 2]
    assert entries[1][1] == "indented"


def test_legacy_fence_headings_ignored():
    from _id_scan import legacy_ledger_entries
    text = "## p001 real\n\nbody\n\n```\n## p999 fake\n```\n\n## p002 real2\n\nb2\n"
    entries = legacy_ledger_entries(text, "p")
    assert [n for n, _, _ in entries] == [1, 2]


def test_legacy_preamble_retained_not_lost():
    from _id_scan import legacy_ledger_entries
    text = "# ledger intro\n\nintro text\n\n## p001 a\n\nx\n"
    entries = legacy_ledger_entries(text, "p")
    assert len(entries) == 1
    assert "intro text" in entries[0][2]


# ---------- P1-5 ----------

def test_migrate_no_overwrite_when_target_exists(tmp_path):
    from _id_scan import IdScanError, plan_legacy_migration, apply_legacy_migration
    repo = _init_repo(tmp_path)
    (repo / "docs/findings").mkdir()
    (repo / "docs/findings/d012_other.md").write_text("# d012 existing\n", encoding="utf-8")
    (repo / "docs/findings.md").write_text(
        "# 发现\n\n## d012 new entry\n\n- 现状：有效\n", encoding="utf-8")
    items = plan_legacy_migration(repo, prefix="d", ledger_rel="docs/findings.md",
                                  target_dir=repo / "docs/findings")
    with pytest.raises(IdScanError, match="已有条目文件|已存在"):
        apply_legacy_migration(items, repo_root=repo, prefix="d",
                               ledger_rel="docs/findings.md",
                               render=lambda i, s, t, b: f"# {i} {t}\n\n{b}\n")
    assert (repo / "docs/findings/d012_other.md").read_text(encoding="utf-8") == "# d012 existing\n"
    assert (repo / "docs/findings.md").is_file()


def test_migrate_idempotent_when_ledger_already_gone(tmp_path):
    from _id_scan import plan_legacy_migration, apply_legacy_migration
    repo = _init_repo(tmp_path)
    (repo / "docs/findings.md").write_text("# 发现\n\n## d012 uv lock\n\n- 现状：有效\n",
                                           encoding="utf-8")
    items = plan_legacy_migration(repo, prefix="d", ledger_rel="docs/findings.md",
                                  target_dir=repo / "docs/findings")
    created = apply_legacy_migration(items, repo_root=repo, prefix="d",
                                     ledger_rel="docs/findings.md",
                                     render=lambda i, s, t, b: f"# {i} {t}\n\n{b}\n")
    assert len(created) == 1 and not (repo / "docs/findings.md").exists()
    again = apply_legacy_migration(items, repo_root=repo, prefix="d",
                                   ledger_rel="docs/findings.md",
                                   render=lambda i, s, t, b: f"# {i} {t}\n\n{b}\n")
    assert [p.name for p in again] == [p.name for p in created]
    assert created[0].is_file()


def test_migrate_rejects_symlinked_target_dir(tmp_path):
    from _id_scan import IdScanError, plan_legacy_migration, apply_legacy_migration
    repo = _init_repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "docs/pending.md").write_text("# 待办\n\n## p001 hello\n\n- 处理：未开\n",
                                          encoding="utf-8")
    (repo / "docs/pending/.gitkeep").unlink(missing_ok=True)
    import shutil
    shutil.rmtree(repo / "docs/pending")
    (repo / "docs/pending").symlink_to(outside, target_is_directory=True)
    items = plan_legacy_migration(repo, prefix="p", ledger_rel="docs/pending.md",
                                  target_dir=repo / "docs/pending")
    with pytest.raises(IdScanError, match="软链"):
        apply_legacy_migration(items, repo_root=repo, prefix="p",
                               ledger_rel="docs/pending.md",
                               render=lambda i, s, t, b: f"# {i}\n\n{b}\n")
    assert list(outside.iterdir()) == []
    assert (repo / "docs/pending.md").is_file()


def test_stale_second_migrate_does_not_delete_first(tmp_path):
    from _id_scan import plan_legacy_migration, apply_legacy_migration
    repo = _init_repo(tmp_path)
    (repo / "docs/findings.md").write_text("# 发现\n\n## d012 uv lock\n\n- 现状：有效\n",
                                           encoding="utf-8")
    stale = plan_legacy_migration(repo, prefix="d", ledger_rel="docs/findings.md",
                                  target_dir=repo / "docs/findings")
    fresh = plan_legacy_migration(repo, prefix="d", ledger_rel="docs/findings.md",
                                  target_dir=repo / "docs/findings")
    apply_legacy_migration(fresh, repo_root=repo, prefix="d", ledger_rel="docs/findings.md",
                           render=lambda i, s, t, b: f"# {i} {t}\n\n{b}\n")
    first_target = repo / "docs/findings/d012_uv_lock.md"
    assert first_target.is_file()
    # stale 视图拿着已消失的旧总账重试：须幂等返回且不删除已发布条目
    again = apply_legacy_migration(stale, repo_root=repo, prefix="d",
                                   ledger_rel="docs/findings.md",
                                   render=lambda i, s, t, b: f"# {i} {t}\n\n{b}\n")
    assert first_target.is_file()
    assert again and again[0].is_file()


# ---------- P2 md_format ----------

def test_md_format_blacklist_case_insensitive():
    import md_format as mdf
    assert mdf._is_blacklisted("Docs/Archive/keep.md")
    assert mdf._is_blacklisted("DOCS/ARCHIVE/keep.md")
    assert mdf._is_blacklisted(".SCRATCH/x.md")


def test_md_format_excludes_staging_tmp(tmp_path, monkeypatch):
    import md_format as mdf
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(mdf, "REPO_ROOT", repo)
    (repo / "docs/pending/p001_x.md").write_text("x\n", encoding="utf-8")
    (repo / "docs/pending/.tmp_p001_x_abc.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init2")
    assert ".tmp_p001_x_abc.md" not in " ".join(mdf.collect_changed())
    assert all(not p.endswith(".tmp_x.md") for p in mdf.collect_all())


def test_md_format_git_timeout_wrapped(tmp_path, monkeypatch):
    import md_format as mdf
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(mdf, "REPO_ROOT", repo)

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=30)

    monkeypatch.setattr(mdf.subprocess, "run", boom)
    with pytest.raises(mdf.MdFormatError, match="超时"):
        mdf.collect_all()


# ---------- P2 spikes sid ----------

def test_spike_sid_s0001_rejected_and_not_matched(tmp_path):
    from repo_task.spikes import find_spike_dir, SpikeMetadataError
    repo = _init_repo(tmp_path)
    bad = repo / "docs/spikes/s0001_bad"
    bad.mkdir(parents=True)
    (bad / "report.md").write_text(
        '---\nsid: "s001"\nslug: "bad"\nstatus: "active"\n---\n# Spike\n', encoding="utf-8")
    with pytest.raises(SpikeMetadataError, match="未找到 spike|规范编号"):
        find_spike_dir(repo, "s001")
    with pytest.raises(SpikeMetadataError, match="规范编号|格式非法"):
        find_spike_dir(repo, "s0001")


def test_spikes_list_validates_status(tmp_path, monkeypatch, capsys):
    import spikes as spikes_mod
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(spikes_mod, "REPO_ROOT", repo)
    monkeypatch.setattr(spikes_mod, "SPIKES_DIR", repo / "docs/spikes")
    d = repo / "docs/spikes/s001_broken"
    d.mkdir(parents=True)
    (d / "report.md").write_text(
        '---\nsid: "s001"\nslug: "broken"\nstatus: "weird"\n---\n# Spike\n',
        encoding="utf-8")
    spikes_mod.main(["list", "--state", "all"])
    captured = capsys.readouterr()
    assert "s001_broken" in captured.out
    assert "unknown" in captured.out
    assert "警告" in captured.err or "非法" in captured.err


# ---------- P2 pending 诊断 ----------

def test_pending_find_entry_reports_malformed(tmp_path, monkeypatch):
    import pending as pending_mod
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(pending_mod, "REPO_ROOT", repo)
    monkeypatch.setattr(pending_mod, "PENDING_DIR", repo / "docs/pending")
    (repo / "docs/pending/p0001_bad.md").write_text("x\n", encoding="utf-8")
    with pytest.raises(Exception, match="不规范|未找到"):
        pending_mod.find_entry(1)


def test_pending_list_warns_malformed(tmp_path, monkeypatch, capsys):
    import pending as pending_mod
    from argparse import Namespace
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(pending_mod, "REPO_ROOT", repo)
    monkeypatch.setattr(pending_mod, "PENDING_DIR", repo / "docs/pending")
    (repo / "docs/pending/p0001_bad.md").write_text("x\n", encoding="utf-8")
    pending_mod.cmd_list(Namespace(state="all"))
    assert "警告" in capsys.readouterr().err or "不规范" in capsys.readouterr().err


def test_pending_symlink_parent_rejected(tmp_path, monkeypatch):
    import pending as pending_mod
    from _id_scan import IdScanError
    repo = _init_repo(tmp_path)
    outside = tmp_path / "outside_p"
    outside.mkdir()
    import shutil
    shutil.rmtree(repo / "docs/pending")
    (repo / "docs/pending").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(pending_mod, "REPO_ROOT", repo)
    monkeypatch.setattr(pending_mod, "PENDING_DIR", repo / "docs/pending")
    with pytest.raises(IdScanError, match="软链"):
        pending_mod.find_entry(1)


# ---------- P2 staging / fsync ----------

def test_allocate_reclaims_regular_tmp_but_keeps_symlink(tmp_path):
    from _id_scan import allocate
    repo = _init_repo(tmp_path)
    target = repo / "docs/pending"
    stale = target / ".tmp_stale_abc.md"
    stale.write_text("stale\n", encoding="utf-8")
    link = target / ".tmp_evil_link.md"
    outside = tmp_path / "outside.md"
    outside.write_text("keep\n", encoding="utf-8")
    link.symlink_to(outside)
    path = allocate(repo, prefix="p", dirs=("docs/pending",), target_dir=target,
                    slug="fresh", body="# {id}\n")
    assert path.is_file()
    assert not stale.exists()
    assert link.is_symlink()
    assert outside.read_text(encoding="utf-8") == "keep\n"


def test_fsync_dir_ok_and_fail_closed(tmp_path):
    from _id_scan import _fsync_dir, IdScanError
    d = tmp_path / "d"
    d.mkdir()
    _fsync_dir(d)
    with pytest.raises(IdScanError):
        _fsync_dir(tmp_path / "no_such_dir_xyz")


# ---------- P2 repo_state ----------

def test_repo_state_untracked_vanish_is_nonzero(tmp_path, monkeypatch):
    import repo_state
    from argparse import Namespace
    repo = _init_repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "gone.txt").write_text("x\n", encoding="utf-8")
    real_read = Path.read_text

    def denied(self, *a, **k):
        if self.name == "gone.txt":
            raise FileNotFoundError("gone")
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", denied)
    monkeypatch.chdir(repo)
    rc = repo_state.cmd_added_lines(Namespace(baseline=baseline, exclude=[]))
    assert rc == 1


def test_git_env_stripping_id_scan(tmp_path, monkeypatch):
    from _id_scan import scan_max_id
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("GIT_DIR", "/nonexistent_git_dir")
    monkeypatch.setenv("GIT_WORK_TREE", "/nonexistent_wt")
    assert scan_max_id(repo, "p", ("docs/pending",)) == 0


# ---------- P2 cleanup / hygiene / kind=dir ----------

def test_cleanup_hard_protected_archive_audit():
    import repo_cleanup as rc
    assert "docs/archive/tasks_audit.log" in rc.HARD_PROTECTED


def test_hygiene_ignores_filtered_and_rollback(tmp_path, monkeypatch):
    import repo_hygiene as rh
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@e.c")
    _git(root, "config", "user.name", "t")
    for name, value in {"REPO_ROOT": root, "REVIEWS_DIR": root / "docs/reviews",
                        "ARCHIVE_REVIEWS": root / "docs/archive/reviews"}.items():
        monkeypatch.setattr(rh, name, value)
    d = root / "docs/reviews/review_ok"
    d.mkdir(parents=True)
    (d / "review_general.md").write_text("PASS\n", encoding="utf-8")
    (d / "debug.log").write_text("log\n", encoding="utf-8")
    (root / ".gitignore").write_text("*.log\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    rh.cmd_archive_review(__import__("argparse").Namespace(dir="review_ok", write=True))
    dest = root / "docs/archive/reviews/review_ok"
    assert (dest / "debug.log").is_file()
    assert "debug.log" not in _git(root, "ls-files").stdout

    d2 = root / "docs/reviews/review_fail"
    d2.mkdir(parents=True)
    (d2 / "review_general.md").write_text("PASS\n", encoding="utf-8")
    real_git = rh._git

    def flaky(*a, **k):
        if a and a[0] == "add":
            return subprocess.CompletedProcess(a, 1, "", "simulated add failure")
        return real_git(*a, **k)

    monkeypatch.setattr(rh, "_git", flaky)
    with pytest.raises(rh.HygieneError, match="已回滚"):
        rh.cmd_archive_review(__import__("argparse").Namespace(dir="review_fail", write=True))
    assert (d2 / "review_general.md").is_file()
    assert not (root / "docs/archive/reviews/review_fail").exists()


def test_kind_dir_file_does_not_occupy_number(tmp_path):
    from _id_scan import scan_max_id
    repo = _init_repo(tmp_path)
    (repo / "docs/spikes").mkdir(parents=True, exist_ok=True)
    (repo / "docs/spikes/s005_note").write_text("file not dir\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "file note")
    assert scan_max_id(repo, "s", ("docs/spikes",), kind="dir") == 0
