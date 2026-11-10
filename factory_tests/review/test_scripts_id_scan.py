"""_id_scan 统一发布原语与统一扫描口径的回归测试。

覆盖：可预测临时路径 symlink 覆写（P0-3）、fsync/软链/异常包装发布原语（L251）、
旧布局目录阻断（L250）、kind=dir 嵌套口径（L252）、旧总账 heading 级别（L254）、
规范 padding（L258）、真实迁移命令提示（P1-11）。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

FACTORY_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = FACTORY_DIR.parent / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(FACTORY_DIR))

from _id_scan import (  # noqa: E402
    IdScanError,
    allocate,
    allocate_dir,
    canonical_entry_id,
    parse_entry_dir_name,
    parse_entry_file_name,
    scan_max_id,
)
from git_repo_support import get_controlled_git_env  # noqa: E402

from template_sync.legacy_ledgers import legacy_ledger_entries

PENDING_DIRS = ("docs/pending",)
SPIKE_DIRS = ("docs/spikes",)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
        env=get_controlled_git_env(),
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "docs/pending").mkdir(parents=True)
    (repo / "docs/pending/.gitkeep").write_text("", encoding="utf-8")
    (repo / ".repo_template").mkdir(exist_ok=True)
    (repo / ".repo_template/entity_schema.json").write_text('{"version": 1}\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _commit_entry(repo: Path, rel: str, text: str = "# entry\n") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if rel in {"docs/pending.md", "docs/findings.md"}:
        (repo / ".repo_template/entity_schema.json").unlink(missing_ok=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"add {rel}")


# ---------------------------------------------------------------------------
# P0-3 / L251：统一发布原语
# ---------------------------------------------------------------------------


def test_allocate_does_not_follow_predictable_tmp_symlink(tmp_path: Path):
    """旧实现写固定 .tmp_p001_evil.md 会跟随软链覆写仓外文件；新实现用唯一命名。"""
    repo = _init_repo(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("CRITICAL OUTSIDE DATA\n", encoding="utf-8")
    tmp_link = repo / "docs/pending/.tmp_p001_evil.md"
    tmp_link.symlink_to(outside)

    path = allocate(
        repo, prefix="p", dirs=PENDING_DIRS, target_dir=repo / "docs/pending",
        slug="evil", body="# {id} evil\n",
    )

    assert path == repo / "docs/pending/p001_evil.md"
    assert path.is_file()
    assert outside.read_text(encoding="utf-8") == "CRITICAL OUTSIDE DATA\n"
    assert tmp_link.is_symlink()
    assert list((repo / "docs/pending").glob(".tmp_*")) == [tmp_link]


def test_allocate_rejects_symlinked_target_dir(tmp_path: Path):
    repo = _init_repo(tmp_path)
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    (repo / "docs/pending/.gitkeep").unlink()
    (repo / "docs/pending").rmdir()
    (repo / "docs/pending").symlink_to(outside_dir, target_is_directory=True)

    with pytest.raises(IdScanError, match="软链"):
        allocate(
            repo, prefix="p", dirs=PENDING_DIRS, target_dir=repo / "docs/pending",
            slug="evil", body="# {id} evil\n",
        )
    assert list(outside_dir.iterdir()) == []


def test_publish_staged_file_rejects_symlinked_target(tmp_path: Path):
    """发布前显式拒绝目标软链，不得 os.replace 到软链上覆写仓外文件。"""
    import _id_scan

    parent = tmp_path / "staging_area"
    parent.mkdir()
    staging = parent / "staging.md"
    staging.write_text("# content\n", encoding="utf-8")
    outside = tmp_path / "outside_victim.md"
    outside.write_text("CRITICAL OUTSIDE DATA\n", encoding="utf-8")
    target = parent / "p001_evil.md"
    target.symlink_to(outside)

    with pytest.raises(IdScanError, match="软链|已存在"):
        _id_scan._publish_staged_file(staging, target)
    assert outside.read_text(encoding="utf-8") == "CRITICAL OUTSIDE DATA\n"
    assert staging.is_file()


def test_allocate_dir_rejects_symlinked_target_dir(tmp_path: Path):
    repo = _init_repo(tmp_path)
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    (repo / "docs/spikes").symlink_to(outside_dir, target_is_directory=True)

    with pytest.raises(IdScanError, match="软链"):
        allocate_dir(
            repo, prefix="s", dirs=SPIKE_DIRS, target_dir=repo / "docs/spikes",
            slug="evil", files={"report.md": "# {id}\n"},
        )
    assert list(outside_dir.iterdir()) == []


def test_allocate_dir_does_not_follow_predictable_tmp_symlink(tmp_path: Path):
    repo = _init_repo(tmp_path)
    (repo / "docs/spikes").mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("CRITICAL OUTSIDE DATA\n", encoding="utf-8")
    tmp_link = repo / "docs/spikes/.tmp_s001_evil"
    tmp_link.symlink_to(outside)

    path = allocate_dir(
        repo, prefix="s", dirs=SPIKE_DIRS, target_dir=repo / "docs/spikes",
        slug="evil", files={"report.md": "# {id}\n"},
    )

    assert path == repo / "docs/spikes/s001_evil"
    assert (path / "report.md").is_file()
    assert outside.read_text(encoding="utf-8") == "CRITICAL OUTSIDE DATA\n"
    assert tmp_link.is_symlink()


def test_publish_io_error_is_wrapped_as_id_scan_error(tmp_path: Path):
    """formatter 抛底层 OSError 时统一包装为 IdScanError，且清理临时文件。"""
    repo = _init_repo(tmp_path)
    target = repo / "docs/pending"

    def formatter(_path):
        raise IsADirectoryError("simulated directory where file expected")

    with pytest.raises(IdScanError, match="I/O 失败"):
        allocate(
            repo, prefix="p", dirs=PENDING_DIRS, target_dir=target,
            slug="atomic", body="# {id}\n", formatter=formatter,
        )
    assert not list(target.glob(".tmp_*"))
    assert not (target / "p001_atomic.md").exists()


def test_publish_business_exception_propagates_unchanged(tmp_path: Path):
    """formatter 的业务异常不属于 I/O 失败，须原样传播以便调用方分类处理。"""
    repo = _init_repo(tmp_path)

    def explode(_path):
        raise ValueError("formatter failed")

    with pytest.raises(ValueError, match="formatter failed"):
        allocate(
            repo, prefix="p", dirs=PENDING_DIRS, target_dir=repo / "docs/pending",
            slug="flaky", body="# {id}\n", formatter=explode,
        )
    assert not list((repo / "docs/pending").glob(".tmp_*"))


# ---------------------------------------------------------------------------
# L250：旧布局目录阻断
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "legacy_rel",
    [
        "docs/pending/todo/p001_old.md",
        "docs/pending/parked/p001_old.md",
    ],
)
def test_legacy_layout_dir_blocks_allocation_and_hints_real_command(
    tmp_path: Path, legacy_rel: str
):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, legacy_rel)

    with pytest.raises(IdScanError) as exc:
        allocate(
            repo, prefix="p", dirs=PENDING_DIRS, target_dir=repo / "docs/pending",
            slug="next", body="# {id}\n",
        )
    assert "migrate" in str(exc.value)
    assert "显式" in str(exc.value)


def test_legacy_layout_dir_in_other_branch_blocks_allocation(tmp_path: Path):
    repo = _init_repo(tmp_path)
    _git(repo, "checkout", "-b", "legacy")
    _commit_entry(repo, "docs/pending/todo/p001_old.md")
    _git(repo, "checkout", "main")

    with pytest.raises(IdScanError, match="嵌套条目|legacy"):
        allocate(
            repo, prefix="p", dirs=PENDING_DIRS, target_dir=repo / "docs/pending",
            slug="next", body="# {id}\n",
        )


def test_legacy_ledger_hints_real_pending_command(tmp_path: Path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/pending.md", text="# 待办\n\n### p047 old\n")

    with pytest.raises(IdScanError) as exc:
        allocate(
            repo, prefix="p", dirs=PENDING_DIRS, target_dir=repo / "docs/pending",
            slug="next", body="# {id}\n",
        )
    assert "repo_sync.py migrate all --write" in str(exc.value)


# ---------------------------------------------------------------------------
# L252 / L254 / L258：统一扫描口径
# ---------------------------------------------------------------------------


def test_branch_dir_scan_ignores_nested_spike_dirs(tmp_path: Path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/spikes/misc/s005_fixture/report.md", text="# s005\n")
    assert scan_max_id(repo, "s", SPIKE_DIRS, kind="dir") == 0

    _commit_entry(repo, "docs/spikes/s007_real/report.md", text="# s007\n")
    assert scan_max_id(repo, "s", SPIKE_DIRS, kind="dir") == 7


def test_worktree_and_branch_dir_scan_agree_on_nesting(tmp_path: Path):
    repo = _init_repo(tmp_path)
    (repo / "docs/spikes/misc/s005_fixture").mkdir(parents=True)
    (repo / "docs/spikes/misc/s005_fixture/report.md").write_text("# s005\n", encoding="utf-8")
    assert scan_max_id(repo, "s", SPIKE_DIRS, kind="dir") == 0


def test_legacy_ledger_entries_accept_all_heading_levels():
    text = "\n".join(
        [
            "# d001 一级标题",
            "正文一",
            "### p047 三级标题（2026-07-01 发现）",
            "正文二",
            "###### d006 六级标题",
            "正文三",
        ]
    )
    parsed = legacy_ledger_entries(text, "d")
    assert [number for number, _, _ in parsed] == [1, 6]
    assert parsed[0][1] == "一级标题"
    assert legacy_ledger_entries(text, "p")[0][0] == 47


def test_legacy_ledger_entries_abort_when_heading_unparsed(monkeypatch):
    """候选标题数与解析数不一致即中止，杜绝 git rm 旧总账后条目静默丢失。"""
    from template_sync import legacy_ledgers

    original = legacy_ledgers._LEGACY_HEADING_RE
    monkeypatch.setattr(legacy_ledgers, "_LEGACY_HEADING_RE", re.compile(r"^(?!x)x"))
    try:
        with pytest.raises(IdScanError, match="标题解析不完整"):
            legacy_ledgers.legacy_ledger_entries("## d012 uv lock\n\n- 现状：有效\n", "d")
    finally:
        monkeypatch.setattr(legacy_ledgers, "_LEGACY_HEADING_RE", original)


def test_canonical_padding_helpers():
    assert canonical_entry_id("p", 1) == "p001"
    assert canonical_entry_id("p", 1024) == "p1024"
    assert parse_entry_file_name("p", "p001_demo.md") == (1, "demo")
    assert parse_entry_file_name("p", "p1024_big.md") == (1024, "big")
    assert parse_entry_file_name("p", "p0001_bad.md") is None
    assert parse_entry_file_name("p", "p01_short.md") is None
    assert parse_entry_dir_name("s", "s001_uv_lock") == (1, "uv_lock")
    assert parse_entry_dir_name("s", "s0001_bad") is None


def test_non_canonical_padding_is_ignored_by_scan(tmp_path: Path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/pending/p0001_pad.md")
    assert scan_max_id(repo, "p", PENDING_DIRS) == 0
