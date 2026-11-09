"""test_id_scan.py - _id_scan.py 跨分支/跨 worktree 编号扫描、互斥分配与 pending/findings CLI。

条目为「一条目一文件」，编号来自文件名。测试用临时 git 仓库（tmp_path 下 init）
模拟多分支、多 worktree 与未提交条目，覆盖扫描、并发取号、状态迁移与 CLI 行为。
"""

import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from _id_scan import IdScanError, allocate, allocate_dir, scan_max_id
import pending as pending_mod
import findings as findings_mod
import spikes as spikes_mod

PENDING_DIRS = ("docs/pending",)
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
    # 显式 -b main：CI runner 常默认 master，否则后续 checkout main 失败。
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "docs" / "pending").mkdir(parents=True)
    (repo / "docs" / "pending" / ".gitkeep").write_text("", encoding="utf-8")
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
    monkeypatch.setattr(pending_mod, "PENDING_DIR", repo / "docs/pending")
    monkeypatch.setattr(pending_mod, "SCAN_DIRS", ("docs/pending",))
    monkeypatch.setattr(findings_mod, "REPO_ROOT", repo)
    monkeypatch.setattr(findings_mod, "FINDINGS_DIR", repo / "docs/findings")
    monkeypatch.setattr(spikes_mod, "REPO_ROOT", repo)
    monkeypatch.setattr(spikes_mod, "SPIKES_DIR", repo / "docs/spikes")
    monkeypatch.setattr(
        spikes_mod, "TEMPLATE_PATH", repo / ".repo_template/docs/spike_report_template.md"
    )


# ---------- 编号扫描 ----------


def test_empty_history_starts_at_zero(tmp_path):
    repo = _init_repo(tmp_path)
    assert scan_max_id(repo, "p", PENDING_DIRS) == 0


def test_todo_entries_drive_max(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/pending/p007_bug.md")
    _commit_entry(repo, "docs/pending/p002_debt.md")
    assert scan_max_id(repo, "p", PENDING_DIRS) == 7


def test_closed_entries_count(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/pending/p002_open.md")
    _commit_entry(repo, "docs/pending/p027_closed.md")
    assert scan_max_id(repo, "p", PENDING_DIRS) == 27


def test_parked_entries_count(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/pending/p013_later.md")
    assert scan_max_id(repo, "p", PENDING_DIRS) == 13


def test_non_entry_filenames_ignored(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/pending/README.md")
    _commit_entry(repo, "docs/pending/p01_short.md")
    _commit_entry(repo, "docs/pending/p012_Upper.md")
    _commit_entry(repo, "docs/pending/p013_ok.md")
    assert scan_max_id(repo, "p", PENDING_DIRS) == 13


def test_ids_can_exceed_999(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/pending/p1024_big.md")
    assert scan_max_id(repo, "p", PENDING_DIRS) == 1024


def test_other_branch_bigger_id_is_picked_up(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/pending/p003_main.md")
    _git(repo, "checkout", "-b", "feature")
    _commit_entry(repo, "docs/pending/p005_feature.md")
    _git(repo, "checkout", "main")
    assert scan_max_id(repo, "p", PENDING_DIRS) == 5


def test_worktree_uncommitted_entry_picked_up(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/pending/p003_main.md")
    worktree = tmp_path / "wt"
    _git(repo, "worktree", "add", "--detach", str(worktree))
    (worktree / "docs/pending/p009_wip.md").write_text("x\n", encoding="utf-8")
    assert scan_max_id(repo, "p", PENDING_DIRS) == 9


def test_duplicate_within_source_fails(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/pending/p010_open.md")
    _commit_entry(repo, "docs/pending/p010_closed.md")
    with pytest.raises(IdScanError, match="p010"):
        scan_max_id(repo, "p", PENDING_DIRS)


def test_duplicate_across_sources_not_reported(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_entry(repo, "docs/pending/p010_main.md")
    _git(repo, "checkout", "-b", "feature")
    _git(repo, "rm", "-q", "docs/pending/p010_main.md")
    _commit_entry(repo, "docs/pending/p010_feature.md")
    _git(repo, "checkout", "main")
    assert scan_max_id(repo, "p", PENDING_DIRS) == 10


def test_git_failure_wrapped(tmp_path):
    not_a_repo = tmp_path / "norepo"
    not_a_repo.mkdir()
    with pytest.raises(IdScanError):
        scan_max_id(not_a_repo, "p", PENDING_DIRS)


# ---------- 互斥分配 ----------


def test_allocate_creates_file_and_returns_path(tmp_path):
    repo = _init_repo(tmp_path)
    path = allocate(
        repo,
        prefix="p",
        dirs=PENDING_DIRS,
        target_dir=repo / "docs/pending",
        slug="new_item",
        body="# {id} demo\n",
    )
    assert path == repo / "docs/pending/p001_new_item.md"
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == "# p001 demo\n"


def test_allocate_increments_sequentially(tmp_path):
    repo = _init_repo(tmp_path)
    p1 = allocate(
        repo,
        prefix="p",
        dirs=PENDING_DIRS,
        target_dir=repo / "docs/pending",
        slug="first",
        body="# {id}\n",
    )
    p2 = allocate(
        repo,
        prefix="p",
        dirs=PENDING_DIRS,
        target_dir=repo / "docs/pending",
        slug="second",
        body="# {id}\n",
    )
    assert p1.name == "p001_first.md"
    assert p2.name == "p002_second.md"


def test_allocate_rejects_bad_slug(tmp_path):
    repo = _init_repo(tmp_path)
    for bad in ("Upper", "has space", "dash-separated", "p001-prefix", ""):
        with pytest.raises(IdScanError):
            allocate(
                repo,
                prefix="p",
                dirs=PENDING_DIRS,
                target_dir=repo / "docs/pending",
                slug=bad,
                body="# {id}\n",
            )


# ---------- 状态迁移 ----------


def test_park_then_revive_round_trip(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])

    pending_mod.main(["park", "p001", "--reason", "2026-08-03 等依赖", "--write"])
    parked = repo / "docs/pending/p001_demo.md"
    assert parked.is_file()
    text = parked.read_text(encoding="utf-8")
    assert "- 处理：不办" in text
    assert "- 暂搁：2026-08-03 等依赖" in text

    pending_mod.main(["revive", "p001", "--write"])
    revived = repo / "docs/pending/p001_demo.md"
    text = revived.read_text(encoding="utf-8")
    assert "- 处理：未开" in text
    assert "- 暂搁：2026-08-03 等依赖" in text


def test_close_writes_fix_ref(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])
    pending_mod.main(["close", "p001", "--reason", "已核实处理结果", "--fix-ref", "t012", "--write"])
    closed = repo / "docs/pending/p001_demo.md"
    assert "- 处理：t012" in closed.read_text(encoding="utf-8")


def test_close_defaults_to_dry_run(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])
    pending_mod.main(["close", "p001", "--reason", "已核实处理结果", "--fix-ref", "t012"])
    fm, _ = pending_mod.parse_pending_file(repo / "docs/pending/p001_demo.md")
    assert fm["status"] == "todo"


def test_close_rejects_parked_entry(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])
    pending_mod.main(["park", "p001", "--reason", "later", "--write"])
    with pytest.raises(SystemExit, match="revive"):
        pending_mod.main(["close", "p001", "--reason", "已核实处理结果", "--fix-ref", "t012", "--write"])


def test_close_rejects_already_closed(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])
    pending_mod.main(["close", "p001", "--reason", "已核实处理结果", "--fix-ref", "t012", "--write"])
    with pytest.raises(SystemExit, match="重复闭环"):
        pending_mod.main(["close", "p001", "--reason", "已核实处理结果", "--fix-ref", "t013", "--write"])


def test_closed_id_not_reused(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "first"])
    pending_mod.main(["close", "p001", "--reason", "已核实处理结果", "--fix-ref", "t012", "--write"])
    pending_mod.main(["new", "--slug", "second"])
    assert (repo / "docs/pending/p002_second.md").is_file()


def test_missing_entry_reports_error(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    with pytest.raises(SystemExit, match="未找到 p001"):
        pending_mod.main(["close", "p001", "--reason", "已核实处理结果", "--fix-ref", "t012", "--write"])


def test_close_partial_failure_rolls_back_moved_and_content(tmp_path, monkeypatch):
    """F19：批量迁移第二条失败时，第一条已更新条目原位回滚并恢复正文。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "one"])
    pending_mod.main(["new", "--slug", "two"])
    one = repo / "docs/pending/p001_one.md"
    two = repo / "docs/pending/p002_two.md"
    orig_one = one.read_bytes()
    assert one.exists() and two.exists()

    real_write = pending_mod.atomic_write_text
    calls = {"n": 0}

    def flaky(path, content, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise IdScanError("模拟第二条更新失败")
        return real_write(path, content, *args, **kwargs)

    monkeypatch.setattr(pending_mod, "atomic_write_text", flaky)
    with pytest.raises(SystemExit, match="模拟第二条更新失败"):
        pending_mod.main(["close", "p001", "p002", "--reason", "已核实处理结果", "--fix-ref", "t001", "--write"])
    assert one.exists()
    assert one.read_bytes() == orig_one
    fm, _ = pending_mod.parse_pending_file(one)
    assert fm["status"] == "todo"


def test_close_partial_io_failure_rolls_back_all(tmp_path, monkeypatch):
    """验证批量更新遇到底层 OSError 时依然完整回滚已更新条目（Issue #20）。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "one"])
    pending_mod.main(["new", "--slug", "two"])
    one = repo / "docs/pending/p001_one.md"
    two = repo / "docs/pending/p002_two.md"
    orig_one = one.read_bytes()

    real_write = pending_mod.atomic_write_text
    calls = {"n": 0}

    def flaky(path, content, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated disk io error on second entry")
        return real_write(path, content, *args, **kwargs)

    monkeypatch.setattr(pending_mod, "atomic_write_text", flaky)
    with pytest.raises(SystemExit, match="simulated disk io error"):
        pending_mod.main(["close", "p001", "p002", "--reason", "test io fail", "--write"])
    assert one.exists()
    assert one.read_bytes() == orig_one
    fm, _ = pending_mod.parse_pending_file(one)
    assert fm["status"] == "todo"


def test_close_reports_partial_rollback_when_restore_fails(tmp_path, monkeypatch, capsys):
    """回滚自身失败时不得报告完整回滚；未恢复条目与原因可见（Issue #78）。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "one"])
    pending_mod.main(["new", "--slug", "two"])

    real_write = pending_mod.atomic_write_text
    calls = {"n": 0}

    def flaky_write(path, content, *args, **kwargs):
        calls["n"] += 1
        p = Path(path)
        if calls["n"] == 2:
            raise IdScanError("模拟第二条更新失败")
        if calls["n"] >= 3 and p.name.startswith("p001_"):
            raise OSError("模拟正文恢复失败")
        return real_write(path, content, *args, **kwargs)

    monkeypatch.setattr(pending_mod, "atomic_write_text", flaky_write)
    with pytest.raises(SystemExit, match="回滚不完整"):
        pending_mod.main(
            ["close", "p001", "p002", "--reason", "已核实处理结果", "--write"]
        )
    stderr = capsys.readouterr().err
    assert "成功回滚 0/1 条" in stderr
    assert "p001_one.md" in stderr
    assert "模拟正文恢复失败" in stderr


def test_close_rejects_entry_migrated_before_write_lock(tmp_path, monkeypatch):
    """锁外定位的条目在锁内路径变化时拒绝落盘（Issue #78）。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "one"])
    one = repo / "docs/pending/p001_one.md"
    moved = repo / "docs/pending/p001_other.md"

    real_find = pending_mod.find_entry
    calls = {"n": 0}

    def find_then_move(number):
        calls["n"] += 1
        state, path = real_find(number)
        if calls["n"] == 2:
            path.rename(moved)
            return state, moved
        return state, path

    monkeypatch.setattr(pending_mod, "find_entry", find_then_move)
    with pytest.raises(SystemExit, match="路径已变"):
        pending_mod.main(
            ["close", "p001", "--reason", "已核实处理结果", "--write"]
        )
    assert moved.exists()


def test_close_rejects_entry_concurrently_modified_before_write_lock(tmp_path, monkeypatch):
    """锁外读取后条目正文在加锁前被修改，摘要比对失败拒绝落盘。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "one"])
    one = repo / "docs/pending/p001_one.md"

    real_find = pending_mod.find_entry
    calls = {"n": 0}

    def find_then_modify(number):
        calls["n"] += 1
        state, path = real_find(number)
        if calls["n"] == 2:
            with path.open("a", encoding="utf-8") as f:
                f.write("\n- 外部追加说明\n")
        return state, path

    monkeypatch.setattr(pending_mod, "find_entry", find_then_modify)
    with pytest.raises(SystemExit, match="内容已被并发修改|内容摘要不一致"):
        pending_mod.main(
            ["close", "p001", "--reason", "已核实处理结果", "--write"]
        )


def test_close_and_park_reject_multiline_inputs(tmp_path, monkeypatch):
    """验证 close 与 park 显式拒绝包含换行符的 reason 或 fix-ref（Issue #52）。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "clean_entry"])
    entry = repo / "docs/pending/p001_clean_entry.md"
    orig_content = entry.read_text(encoding="utf-8")

    # 1. close 带多行 reason 被拒绝，原内容不变
    with pytest.raises(SystemExit, match="不能包含换行符"):
        pending_mod.main(["close", "p001", "--reason", "line1\nline2", "--write"])
    assert entry.is_file()
    assert entry.read_text(encoding="utf-8") == orig_content

    # 2. close 带多行 fix-ref 被拒绝
    with pytest.raises(SystemExit, match="不能包含换行符"):
        pending_mod.main(["close", "p001", "--reason", "valid", "--fix-ref", "t001\nfake", "--write"])
    assert entry.is_file()
    assert entry.read_text(encoding="utf-8") == orig_content

    # 3. park 带多行 reason 被拒绝
    with pytest.raises(SystemExit, match="不能包含换行符"):
        pending_mod.main(["park", "p001", "--reason", "wait\nmore", "--write"])
    assert entry.is_file()
    assert entry.read_text(encoding="utf-8") == orig_content


# ---------- CLI ----------


def test_pending_new_prints_path(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])
    assert capsys.readouterr().out == "docs/pending/p001_demo.md\n"


def test_pending_new_bug_template(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "crash", "--kind", "bug"])
    text = (repo / "docs/pending/p001_crash.md").read_text(encoding="utf-8")
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


# ---------- 固定身份不变量：rename 命令已删除 ----------


def test_pending_rename_command_is_removed(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    with pytest.raises(SystemExit):
        pending_mod.main(["rename", "p001", "--slug", "renamed"])


def test_findings_rename_command_is_removed(tmp_path, monkeypatch):
    """固定身份不变量：findings 与 pending 一致，不提供 slug 改名接口。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    with pytest.raises(SystemExit):
        findings_mod.main(["rename", "d001", "--slug", "renamed", "--write"])


# ---------- spikes 目录条目支持 ----------


def _init_spikes(repo):
    (repo / "docs/spikes").mkdir(parents=True, exist_ok=True)
    template = repo / ".repo_template/docs/spike_report_template.md"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text(
        "---\nsid: \"{id}\"\nslug: \"{slug}\"\nstatus: \"active\"\n---\n\n# Spike {id}: {title}\n",
        encoding="utf-8",
    )


def test_spikes_new_creates_dir_from_template(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    _init_spikes(repo)
    _bind(monkeypatch, repo)
    spikes_mod.main(["new", "--slug", "uv_lock"])
    out = capsys.readouterr().out.strip()
    assert out == "docs/spikes/s001_uv_lock"
    report = repo / out / "report.md"
    assert report.is_file()
    assert "# Spike s001" in report.read_text(encoding="utf-8")


def test_pending_new_format_failure_publishes_nothing(tmp_path, monkeypatch, capsys):
    """A18：格式化失败不得发布正式条目，也不输出创建成功。"""
    from md_format import MdFormatError

    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)

    def fail_format(_path):
        raise MdFormatError("simulated format failure")

    monkeypatch.setattr(pending_mod, "format_file", fail_format)

    with pytest.raises(SystemExit, match="simulated format failure"):
        pending_mod.main(["new", "--slug", "atomic_check"])

    assert not (repo / "docs/pending/p001_atomic_check.md").exists()
    assert capsys.readouterr().out == ""
    assert scan_max_id(repo, "p", PENDING_DIRS) == 0


def test_spikes_new_format_failure_publishes_no_directory(tmp_path, monkeypatch, capsys):
    """A18：目录条目在临时目录准备并格式化，失败不发布、不残留临时目录。"""
    from md_format import MdFormatError

    repo = _init_repo(tmp_path)
    _init_spikes(repo)
    _bind(monkeypatch, repo)

    def fail_format(_path):
        raise MdFormatError("simulated spike format failure")

    monkeypatch.setattr(spikes_mod, "format_file", fail_format)

    with pytest.raises(SystemExit, match="simulated spike format failure"):
        spikes_mod.main(["new", "--slug", "atomic_spike"])

    assert not (repo / "docs/spikes/s001_atomic_spike").exists()
    assert capsys.readouterr().out == ""
    assert scan_max_id(repo, "s", SPIKE_DIRS, kind="dir") == 0


def test_spikes_new_formats_report_file(tmp_path, monkeypatch):
    """验证 spikes new 传入的具体格式化目标为 report.md 文件而非目录（Issue #58）。"""
    repo = _init_repo(tmp_path)
    _init_spikes(repo)
    _bind(monkeypatch, repo)

    formatted_target = []

    def mock_format(path):
        formatted_target.append(path)

    monkeypatch.setattr(spikes_mod, "format_file", mock_format)
    spikes_mod.main(["new", "--slug", "verify_format"])

    assert len(formatted_target) == 1
    assert formatted_target[0].name == "report.md"


def test_allocate_dir_formatter_failure_cleans_up(tmp_path):
    """A18：allocate_dir 在 formatter 抛错时原子回滚临时目录。"""
    repo = _init_repo(tmp_path)
    target = repo / "docs/spikes"
    target.mkdir(parents=True)

    def explode(_path):
        raise ValueError("formatter failed")

    with pytest.raises(ValueError, match="formatter failed"):
        allocate_dir(
            repo, prefix="s", dirs=SPIKE_DIRS, target_dir=target,
            slug="flaky", files={"report.md": "# {id}\n"},
            formatter=explode,
        )

    assert not (target / "s001_flaky").exists()
    assert list(target.glob(".tmp_*")) == []
    assert scan_max_id(repo, "s", SPIKE_DIRS, kind="dir") == 0


def test_allocate_formatter_failure_cleans_up(tmp_path):
    """A18：allocate 在 formatter 抛错时清理临时文件并不占号。"""
    repo = _init_repo(tmp_path)
    target = repo / "docs/pending"
    target.mkdir(parents=True, exist_ok=True)

    def explode(_path):
        raise ValueError("formatter failed")

    with pytest.raises(ValueError, match="formatter failed"):
        allocate(repo, prefix="p", dirs=PENDING_DIRS, target_dir=target, slug="flaky", body="# {id}\n", formatter=explode)

    assert not (target / "p001_flaky.md").exists()
    assert list(target.glob(".tmp_*")) == []
    assert scan_max_id(repo, "p", PENDING_DIRS) == 0


def test_spikes_new_requires_template(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    with pytest.raises(SystemExit, match="缺.*模板"):
        spikes_mod.main(["new", "--slug", "uv_lock"])


def test_spikes_list_reports_state(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    _init_spikes(repo)
    _bind(monkeypatch, repo)
    spikes_mod.main(["new", "--slug", "uv_lock"])
    capsys.readouterr()
    spikes_mod.main(["list"])
    out = capsys.readouterr().out
    assert "active" in out
    assert "s001_uv_lock" in out


def test_close_and_park_do_not_auto_git_add(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "demo"])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add p001")

    pending_mod.main(["close", "p001", "--reason", "已核实处理结果", "--fix-ref", "t012", "--write"])

    staged = _git(repo, "diff", "--cached", "--name-status").stdout
    assert staged == ""
    unstaged = _git(repo, "diff", "--name-status").stdout
    assert "docs/pending/p001_demo.md" in unstaged


# ---------- A11：旧单文件总账必须显式迁移，运行期不再解析正文 ----------


def test_allocation_blocks_on_legacy_ledger_and_hints_migration(tmp_path, monkeypatch):
    """A11：存在旧总账时拒绝分配编号；提示显式迁移，不静默跳号。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    _commit_entry(
        repo,
        "docs/pending.md",
        text="# 待办\n\n### p047 cli 退出码（2026-07-01 发现）\n\n- 处理：未开\n",
    )

    with pytest.raises(IdScanError, match="migrate-legacy"):
        allocate(repo, prefix="p", dirs=PENDING_DIRS, target_dir=repo / "docs/pending",
                 slug="demo", body="# pNNN demo\n")
    with pytest.raises(SystemExit, match="migrate-legacy"):
        pending_mod.main(["new", "--slug", "demo"])
    assert scan_max_id(repo, "p", PENDING_DIRS) == 0


def test_migrate_legacy_findings_splits_entries(tmp_path, monkeypatch):
    """A11：findings 旧总账同样显式迁移。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    (repo / "docs/findings.md").write_text(
        "# 发现总账\n\n## d012 uv lock 平台标记\n\n- 现状：有效\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "legacy findings")

    findings_mod.main(["migrate-legacy", "--write"])
    assert not (repo / "docs/findings.md").exists()
    assert (repo / "docs/findings/d012_uv_lock.md").is_file()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "migrate legacy findings")

    findings_mod.main(["new", "--slug", "next_fact"])
    assert (repo / "docs/findings/d013_next_fact.md").is_file()


def test_migrate_legacy_reports_other_sources_blocking(tmp_path, monkeypatch):
    """A11：其它分支仍有旧总账时，迁移后分配继续阻断并列出阻塞来源。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    _git(repo, "checkout", "-b", "legacy")
    _commit_entry(repo, "docs/pending.md", text="# 待办\n\n### p010 旧事\n")
    _git(repo, "checkout", "main")

    with pytest.raises(SystemExit, match="分支 legacy"):
        pending_mod.main(["new", "--slug", "demo"])


def test_pending_migrate_legacy_splits_and_unblocks_allocation(tmp_path, monkeypatch):
    """P1-11：pending migrate-legacy 真实可用，迁移后分配不再被旧总账阻断。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    (repo / "docs/pending.md").write_text(
        "# 待办\n\n### p047 cli 退出码（2026-07-01 发现）\n\n- 处理：未开\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "legacy pending")

    with pytest.raises(SystemExit, match="migrate-legacy"):
        pending_mod.main(["new", "--slug", "blocked"])

    pending_mod.main(["migrate-legacy", "--write"])
    migrated = repo / "docs/pending/p047_cli.md"
    assert migrated.is_file()
    fm, _ = pending_mod.parse_pending_file(migrated)
    assert fm == {"pid": "p047", "slug": "cli", "status": "todo"}
    assert not (repo / "docs/pending.md").exists()

    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "migrate legacy pending")
    pending_mod.main(["new", "--slug", "next"])
    assert (repo / "docs/pending/p048_next.md").is_file()


def test_pending_migrate_legacy_dry_run_is_read_only(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    (repo / "docs/pending.md").write_text("# 待办\n\n### p001 old\n", encoding="utf-8")
    before = (repo / "docs/pending.md").read_bytes()

    pending_mod.main(["migrate-legacy"])

    assert "dry-run" in capsys.readouterr().err
    assert (repo / "docs/pending.md").read_bytes() == before
    assert not (repo / "docs/pending/p001_old.md").exists()


def test_migrate_legacy_rejects_same_number_occupied(tmp_path, monkeypatch):
    """同号不同 slug 的既有条目也必须阻断，否则生成同号多文件（DS L253）。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    (repo / "docs/findings").mkdir(parents=True)
    (repo / "docs/findings/d012_other.md").write_text("# d012 既有\n", encoding="utf-8")
    (repo / "docs/findings.md").write_text(
        "# 发现\n\n## d012 uv lock 平台标记\n\n- 现状：有效\n", encoding="utf-8"
    )

    with pytest.raises(SystemExit, match="已有条目文件"):
        findings_mod.main(["migrate-legacy", "--write"])
    assert not (repo / "docs/findings/d012_uv_lock.md").exists()
    assert (repo / "docs/findings.md").is_file()


def test_migrate_legacy_cleans_up_on_format_failure(tmp_path, monkeypatch):
    """格式化失败时已写入 target 必须清理，避免重试永久中止（DS L253）。"""
    from md_format import MdFormatError

    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    (repo / "docs/findings.md").write_text(
        "# 发现\n\n## d012 uv lock 平台标记\n\n- 现状：有效\n", encoding="utf-8"
    )

    def fail_format(_path):
        raise MdFormatError("simulated format failure")

    monkeypatch.setattr(findings_mod, "format_file", fail_format)
    with pytest.raises(SystemExit, match="simulated format failure"):
        findings_mod.main(["migrate-legacy", "--write"])
    assert not (repo / "docs/findings/d012_uv_lock.md").exists()
    assert (repo / "docs/findings.md").is_file()


def test_migrate_legacy_keeps_all_heading_levels(tmp_path, monkeypatch):
    """旧总账含 # d013 / ###### d014 时不得静默丢条目（DS L254）。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    (repo / "docs/findings.md").write_text(
        "# 发现\n\n# d013 first level\n\n- 现状：有效\n\n"
        "###### d014 sixth level\n\n- 现状：有效\n",
        encoding="utf-8",
    )

    findings_mod.main(["migrate-legacy", "--write"])
    assert (repo / "docs/findings/d013_first_level.md").is_file()
    assert (repo / "docs/findings/d014_sixth_level.md").is_file()
    assert not (repo / "docs/findings.md").exists()


# ---------- 分支内 active/archive 同号迁移残留 ----------


def test_spike_duplicate_active_archive_within_branch_fails(tmp_path):
    repo = _init_repo(tmp_path)
    _git(repo, "checkout", "-b", "feature")
    _commit_entry(repo, "docs/spikes/s012_probe/report.md", text="# s012\n")
    _commit_entry(repo, "docs/spikes/s012_other/report.md", text="# s012\n")
    _git(repo, "checkout", "main")
    with pytest.raises(IdScanError, match="s012"):
        scan_max_id(repo, "s", SPIKE_DIRS, kind="dir")


@pytest.mark.parametrize("reference", [None, "main", "abc123", "PR #42"])
def test_close_requires_only_explanation_and_accepts_optional_non_task_reference(tmp_path, monkeypatch, reference):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "main_fix"])
    args = ["close", "p001", "--reason", "按用户要求在 main 修复，专项测试通过", "--write"]
    if reference is not None:
        args += ["--fix-ref", reference]
    pending_mod.main(args)
    closed = repo / "docs/pending/p001_main_fix.md"
    text = closed.read_text(encoding="utf-8")
    assert "resolution=resolved" in text
    assert "reason=按用户要求在 main 修复，专项测试通过" in text
    if reference is not None:
        assert f"处理：{reference}；" in text


@pytest.mark.parametrize("resolution", ["transferred", "resolved", "obsolete", "duplicate"])
def test_all_close_resolutions_allow_no_reference_but_reject_blank_explanation(tmp_path, monkeypatch, resolution):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "closure"])
    source = repo / "docs/pending/p001_closure.md"
    original = source.read_bytes()
    with pytest.raises(SystemExit, match="非空 --reason"):
        pending_mod.main(["close", "p001", "--resolution", resolution, "--reason", "  ", "--write"])
    assert source.read_bytes() == original
    pending_mod.main(["close", "p001", "--resolution", resolution, "--reason", "已核实处理依据并记录结果", "--write"])
    text = (repo / "docs/pending/p001_closure.md").read_text(encoding="utf-8")
    assert f"resolution={resolution}" in text
    assert "reason=已核实处理依据并记录结果" in text


@pytest.mark.parametrize("reference_args", [[], ["--fix-ref", "t012"]])
def test_close_missing_explanation_is_rejected(tmp_path, monkeypatch, capsys, reference_args):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    pending_mod.main(["new", "--slug", "unexplained"])
    source = repo / "docs/pending/p001_unexplained.md"
    original = source.read_bytes()
    with pytest.raises(SystemExit) as failure:
        pending_mod.main(["close", "p001", *reference_args, "--write"])
    assert failure.value.code == 2
    assert "--reason" in capsys.readouterr().err
    assert source.read_bytes() == original


def _allocate_worker(repo_str: str, slug: str) -> str:
    """进程池 worker：跨进程调用 allocate，验证 id_lock 的互斥取号。"""
    sys.path.insert(0, str(SCRIPTS_DIR))
    from _id_scan import allocate

    repo = Path(repo_str)
    return allocate(
        repo,
        prefix="p",
        dirs=PENDING_DIRS,
        target_dir=repo / "docs/pending",
        slug=slug,
        body="# {id}\n",
    ).name


def test_concurrent_allocation_yields_unique_ids(tmp_path):
    """并发 worker 不会撞号：编号连续、文件名唯一、每个条目都落盘。"""
    repo = _init_repo(tmp_path)
    slugs = [f"worker_{index:02d}" for index in range(8)]
    with ProcessPoolExecutor(max_workers=4) as pool:
        names = list(pool.map(_allocate_worker, [str(repo)] * len(slugs), slugs))

    assert len(set(names)) == len(slugs)
    assert sorted(int(name[1:4]) for name in names) == list(range(1, len(slugs) + 1))
    for name in names:
        assert (repo / "docs/pending" / name).is_file()


def test_allocate_dir_rejects_paths_escaping_target(tmp_path, monkeypatch):
    """files 键不得为绝对路径或含 ..，且失败时不在外部留文件（Issue #80）。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    target = repo / "docs/spikes"
    target.mkdir(parents=True, exist_ok=True)
    for bad in ("../escape.txt", "/tmp/escape.txt", "a/../../escape.txt", "a\\b.txt"):
        with pytest.raises(IdScanError, match="文件名非法"):
            allocate_dir(
                repo,
                prefix="s",
                dirs=SPIKE_DIRS,
                target_dir=target,
                slug="safe",
                files={bad: "x"},
            )
    assert not (repo / "docs/escape.txt").exists()
    assert not (repo.parent / "escape.txt").exists()
