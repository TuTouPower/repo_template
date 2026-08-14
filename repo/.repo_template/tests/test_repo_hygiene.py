"""repo_hygiene.py 机械迁移动作的真实 git 测试。

monkeypatch 模块级路径常量到 tmp_path 构造的 git 仓库，覆盖 handoff 分段
迁移（append 保留最新）、spike/review 目录 git mv 迁移、dry-run 零写盘、
歧义与格式校验。
"""

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import repo_hygiene as rh


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=check,
    )


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@e.c")
    _git(root, "config", "user.name", "t")
    for name, value in {
        "REPO_ROOT": root,
        "SPIKES_DIR": root / "docs/spikes",
        "ARCHIVE_SPIKES": root / "docs/archive/spikes",
        "REVIEWS_DIR": root / "docs/reviews",
        "ARCHIVE_REVIEWS": root / "docs/archive/reviews",
        "HANDOFF": root / "docs/handoff.md",
        "ARCHIVE_HANDOFF": root / "docs/archive/handoff.md",
    }.items():
        monkeypatch.setattr(rh, name, value)
    return root


def _handoff_two_sections(repo: Path) -> None:
    (repo / "docs/handoff.md").write_text(
        "# 项目交接记录（最新）\n\n说明。\n\n"
        "## 2026-08-01 09:00 UTC+8 a → b\n\n- branch：`t001_x`\n"
        "- head_commit：`aaa111`\n\n"
        "## 2026-08-13 16:00 UTC+8 b → c\n\n- branch：`t002_y`\n"
        "- head_commit：`bbb222`\n",
        encoding="utf-8",
    )


def test_archive_handoff_moves_stale_keeps_latest(repo):
    _handoff_two_sections(repo)
    rh.cmd_archive_handoff(argparse.Namespace(write=True))
    handoff = (repo / "docs/handoff.md").read_text(encoding="utf-8")
    archive = (repo / "docs/archive/handoff.md").read_text(encoding="utf-8")
    assert "2026-08-13 16:00" in handoff
    assert "2026-08-01 09:00" not in handoff
    assert "2026-08-01 09:00" in archive
    assert "2026-08-13 16:00" not in archive
    assert "t002_y" in handoff


def test_archive_handoff_preserves_existing_archive_body(repo):
    archive = repo / "docs/archive/handoff.md"
    archive.parent.mkdir(parents=True)
    archive.write_text(
        "# 历史交接\n\n自定义备注，禁止改写。\n\n"
        "## 2026-07-01 旧段\n\n- note：keep me\n",
        encoding="utf-8",
    )
    _handoff_two_sections(repo)
    rh.cmd_archive_handoff(argparse.Namespace(write=True))
    text = archive.read_text(encoding="utf-8")
    assert "自定义备注，禁止改写。" in text
    assert "2026-07-01 旧段" in text
    assert "2026-08-01 09:00" in text
    assert text.index("自定义备注") < text.index("2026-08-01")


def test_archive_handoff_append_not_truncate(repo):
    _handoff_two_sections(repo)
    rh.cmd_archive_handoff(argparse.Namespace(write=True))
    # 第二轮：上一轮保留的 08-13 节 + 新 08-20 节 → 迁 08-13，追加归档不覆盖旧归档
    (repo / "docs/handoff.md").write_text(
        "# 项目交接记录（最新）\n\n说明。\n\n"
        "## 2026-08-13 16:00 UTC+8 b → c\n\n- head_commit：`bbb222`\n\n"
        "## 2026-08-20 10:00 UTC+8 c → d\n\n- head_commit：`ccc333`\n",
        encoding="utf-8",
    )
    rh.cmd_archive_handoff(argparse.Namespace(write=True))
    archive = (repo / "docs/archive/handoff.md").read_text(encoding="utf-8")
    assert "2026-08-01 09:00" in archive
    assert "2026-08-13 16:00" in archive
    assert "2026-08-20 10:00" not in archive  # 最新节保留在 handoff
    assert "2026-08-20 10:00" in (repo / "docs/handoff.md").read_text(encoding="utf-8")


def test_archive_handoff_dry_run_does_not_write(repo):
    _handoff_two_sections(repo)
    rh.cmd_archive_handoff(argparse.Namespace(write=False))
    assert not (repo / "docs/archive/handoff.md").exists()
    assert "2026-08-01 09:00" in (repo / "docs/handoff.md").read_text(encoding="utf-8")


def test_archive_handoff_noop_single_section(repo):
    (repo / "docs/handoff.md").write_text(
        "# 项目交接记录（最新）\n\n说明。\n\n"
        "## 2026-08-13 16:00 UTC+8 b → c\n\n- head_commit：`bbb222`\n",
        encoding="utf-8",
    )
    rh.cmd_archive_handoff(argparse.Namespace(write=True))
    assert not (repo / "docs/archive/handoff.md").exists()


def _make_spike(repo: Path, name: str) -> None:
    d = repo / "docs/spikes" / name
    d.mkdir(parents=True)
    (d / "report.md").write_text("# report\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"feat(spike): add {name}")


def test_archive_spike_moves_dir_with_git_mv(repo):
    _make_spike(repo, "s001_uv_lock")
    rh.cmd_archive_spike(argparse.Namespace(sid="s001", write=True))
    assert not (repo / "docs/spikes/s001_uv_lock").exists()
    assert (repo / "docs/archive/spikes/s001_uv_lock/report.md").is_file()
    # git 跟踪：git ls-files 确认迁移被记录
    tracked = _git(repo, "ls-files").stdout
    assert "docs/archive/spikes/s001_uv_lock/report.md" in tracked
    assert "docs/spikes/s001_uv_lock/report.md" not in tracked


def test_archive_spike_dry_run_does_not_move(repo):
    _make_spike(repo, "s001_uv_lock")
    rh.cmd_archive_spike(argparse.Namespace(sid="s001", write=False))
    assert (repo / "docs/spikes/s001_uv_lock").is_dir()


def test_archive_spike_ambiguous_or_missing(repo):
    _make_spike(repo, "s001_uv")
    _make_spike(repo, "s001_lock")
    with pytest.raises(rh.HygieneError):
        rh.cmd_archive_spike(argparse.Namespace(sid="s001", write=True))
    with pytest.raises(rh.HygieneError):
        rh.cmd_archive_spike(argparse.Namespace(sid="s999", write=True))


def test_archive_review_moves_dir(repo):
    d = repo / "docs/reviews/review_my_check"
    d.mkdir(parents=True)
    (d / "review_general.md").write_text("PASS\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "docs(review): add my_check")
    rh.cmd_archive_review(argparse.Namespace(dir="review_my_check", write=True))
    assert (repo / "docs/archive/reviews/review_my_check/review_general.md").is_file()
    assert not (repo / "docs/reviews/review_my_check").exists()


def test_archive_review_moves_gitignore_meta(repo):
    d = repo / "docs/reviews/review_my_check"
    d.mkdir(parents=True)
    (d / "review_general.md").write_text("PASS\n", encoding="utf-8")
    meta = d / "_meta" / "notes.json"
    meta.parent.mkdir()
    meta.write_text('{"x": 1}\n', encoding="utf-8")
    (repo / ".gitignore").write_text(
        "docs/reviews/review_*/_meta/\ndocs/archive/reviews/review_*/_meta/\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "docs(review): add my_check")
    assert (d / "_meta/notes.json").is_file()
    assert "_meta" not in _git(repo, "ls-files").stdout
    rh.cmd_archive_review(argparse.Namespace(dir="review_my_check", write=True))
    dest = repo / "docs/archive/reviews/review_my_check"
    assert (dest / "review_general.md").is_file()
    assert (dest / "_meta/notes.json").is_file()
    assert not (repo / "docs/reviews/review_my_check").exists()


def test_archive_review_rejects_bad_prefix(repo):
    with pytest.raises(rh.HygieneError):
        rh.cmd_archive_review(argparse.Namespace(dir="prompts", write=True))
    with pytest.raises(rh.HygieneError):
        rh.cmd_archive_review(argparse.Namespace(dir="review_nonexistent", write=True))
