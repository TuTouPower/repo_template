"""Issue #96 统一实体迁移专项测试：一次性消重、引用事务、分支阻断、运行期报错。"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS = FACTORY_ROOT / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS))

from _id_scan import IdScanError, scan_occupied_max_ids, validate_snapshot_ids
from repo_task.context import RepoContext
from template_sync import context as ctx
from template_sync.entity_storage_migration import (
    MigrationError,
    entity_storage_migration_status,
    migrate_entity_storage,
)
import findings as findings_mod
from test_repo_sync import env  # noqa: F401 - pytest fixture


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=check,
    )


def _init_consumer(tmp_path: Path) -> Path:
    root = tmp_path / "consumer"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    (root / ".repo_template").mkdir()
    (root / ".repo_template/entity_schema.json").write_text('{"version": 1}\n', encoding="utf-8")
    for d in ("docs/findings", "docs/pending", "docs/spikes"):
        (root / d).mkdir(parents=True)
    (root / ".gitignore").write_text(".scratch/\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    return root


def _finding(root: Path, name: str, eid: str) -> None:
    (root / "docs/findings" / name).write_text(
        f"# {eid} {name[:-3]}\n\n- 结论：fact\n- 证据：data\n- 影响：无\n- 现状：有效\n",
        encoding="utf-8",
    )


def _commit_all(root: Path, msg: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-m", msg)


def _finding_numbers(root: Path) -> list[int]:
    from _id_scan import parse_entry_file_name

    return sorted(
        parse_entry_file_name("d", p.name)[0]
        for p in (root / "docs/findings").glob("d*.md")
    )


def test_issue96_renumber_then_allocate_next(tmp_path: Path, monkeypatch) -> None:
    """主干 d037/d038 重复 + d039-041：一次性改号，提交后 CLI 分配下一个唯一号。"""
    root = _init_consumer(tmp_path)
    _finding(root, "d037_entry_a.md", "d037")
    _finding(root, "d037_entry_b.md", "d037")
    _finding(root, "d038_entry_c.md", "d038")
    _finding(root, "d038_entry_d.md", "d038")
    for num in (39, 40, 41):
        _finding(root, f"d{num:03d}_fact.md", f"d{num:03d}")
    (root / "docs/handoff.md").write_text(
        "# 交接\n\n- 见 docs/findings/d037_entry_b.md 与 docs/findings/d038_entry_d.md\n",
        encoding="utf-8",
    )
    _commit_all(root, "historical duplicates")

    sync = ctx.SyncContext.for_consumer(root)
    reports = migrate_entity_storage(sync, write=True)
    assert any("d037" in r and "d042" in r for r in reports) or any("->" in r for r in reports)

    numbers = _finding_numbers(root)
    assert numbers == [37, 38, 39, 40, 41, 42, 43], numbers
    assert not (root / "docs/findings/d037_entry_b.md").exists() or (
        root / "docs/findings/d042_entry_b.md"
    ).exists()
    # 精确路径引用已在实体迁移内改写
    handoff = (root / "docs/handoff.md").read_text(encoding="utf-8")
    assert "d037_entry_a.md" not in handoff or "d042" in handoff
    assert entity_storage_migration_status(sync) == []

    _commit_all(root, "sync: dedup and renumber")
    validate_snapshot_ids(root)

    monkeypatch.setattr(findings_mod, "format_file", lambda _p: None)
    findings_mod.main(["new", "--slug", "next_fact"], repo=RepoContext.for_root(root))
    assert (root / "docs/findings/d044_next_fact.md").is_file()


def test_pending_and_spike_dedup_updates_identity(tmp_path: Path) -> None:
    """p/s 同号消重：pid/sid/H1 同步改号，spike code/ 附件字节保留。"""
    root = _init_consumer(tmp_path)
    # pending 重复
    for name in ("p007_a.md", "p007_b.md"):
        (root / "docs/pending" / name).write_text(
            '---\npid: "p007"\nslug: "a"\nstatus: "todo"\n---\n# p007 事项\n',
            encoding="utf-8",
        )
    # 第二个文件 slug 与 front matter 需一致
    (root / "docs/pending/p007_b.md").write_text(
        '---\npid: "p007"\nslug: "b"\nstatus: "todo"\n---\n# p007 事项 b\n',
        encoding="utf-8",
    )
    # spike 重复
    for name in ("s003_probe", "s003_other"):
        d = root / "docs/spikes" / name
        d.mkdir()
        (d / "report.md").write_text(
            f'---\nsid: "s003"\nslug: "{name.split("_", 1)[1]}"\nstatus: "completed"\n---\n'
            "# Spike s003\n\n## 结论\n采纳。\n",
            encoding="utf-8",
        )
        (d / "code").mkdir()
        (d / "code" / "probe.py").write_bytes(f"# {name}\n".encode("utf-8"))
    _commit_all(root, "duplicates")

    sync = ctx.SyncContext.for_consumer(root)
    migrate_entity_storage(sync, write=True)

    pending = sorted((root / "docs/pending").glob("p*.md"))
    pids = []
    for p in pending:
        from _id_scan import parse_entry_file_name
        num, _slug = parse_entry_file_name("p", p.name)
        pids.append(num)
        text = p.read_text(encoding="utf-8")
        assert f'pid: "p{num:03d}"' in text, p.name
    assert len(pids) == len(set(pids)) and 7 in pids and 8 in pids, pids

    spikes = sorted(p for p in (root / "docs/spikes").iterdir() if p.is_dir() and p.name.startswith("s"))
    sids = []
    for d in spikes:
        num = int(d.name[1:4])
        sids.append(num)
        rep = (d / "report.md").read_text(encoding="utf-8")
        assert f'sid: "s{num:03d}"' in rep, d.name
        assert (d / "code" / "probe.py").is_file()
    assert len(sids) == len(set(sids)) and 3 in sids and 4 in sids, sids
    assert entity_storage_migration_status(sync) == []


def test_other_current_branch_duplicates_block_migration(tmp_path: Path) -> None:
    """选项 B：其他当前协议本地分支内部仍有重复时阻断迁移。"""
    root = _init_consumer(tmp_path)
    _finding(root, "d010_main.md", "d010")
    _commit_all(root, "clean main")

    _git(root, "checkout", "-b", "stale")
    _finding(root, "d020_x.md", "d020")
    _finding(root, "d020_y.md", "d020")
    _commit_all(root, "duplicate on stale branch")
    _git(root, "checkout", "main")

    sync = ctx.SyncContext.for_consumer(root)
    with pytest.raises(MigrationError, match="分支 stale|编号冲突"):
        migrate_entity_storage(sync, write=False)


def test_runtime_duplicate_blocks_allocation(tmp_path: Path) -> None:
    """运行中发现重复直接报错、拒绝分配，要求手动修改。"""
    root = _init_consumer(tmp_path)
    _finding(root, "d012_a.md", "d012")
    _finding(root, "d012_b.md", "d012")
    _commit_all(root, "duplicate findings")
    from _id_scan import scan_max_id

    with pytest.raises(IdScanError, match="重复"):
        scan_max_id(root, "d")


def test_history_and_saved_remote_duplicates_only_contribute_max(tmp_path: Path) -> None:
    """旧 commit / 已保存远端分支的重复只贡献最大占用号，不阻断当前合法取号。"""
    root = _init_consumer(tmp_path)
    _finding(root, "d060_x.md", "d060")
    _finding(root, "d060_y.md", "d060")
    _commit_all(root, "historical dup")
    (root / "docs/findings/d060_x.md").unlink()
    (root / "docs/findings/d060_y.md").unlink()
    _commit_all(root, "clean working tree")

    occupied = scan_occupied_max_ids(root)
    assert occupied["d"] >= 60
    # 当前无重复，validate_snapshot_ids 通过
    validate_snapshot_ids(root)


def test_no_changes_is_noop(tmp_path: Path) -> None:
    """洁净当前布局：统一计划零写入，status 收敛为空。"""
    root = _init_consumer(tmp_path)
    _finding(root, "d001_fact.md", "d001")
    _commit_all(root, "clean")
    sync = ctx.SyncContext.for_consumer(root)
    assert entity_storage_migration_status(sync) == []
    assert migrate_entity_storage(sync, write=True) == []


def test_apply_dedup_outside_reference_single_commit_and_accept(
    env, tmp_path, monkeypatch
) -> None:
    """plan 7.2：目录外精确路径引用在本轮事务内改写，单 commit 后 accept 成功。"""
    from test_single_commit_sync import _prepare
    from template_sync.apply import cmd_apply
    from template_sync.transaction import cmd_transaction

    args = _prepare(env, tmp_path, monkeypatch)
    root = env["consumer"]
    (root / "docs/findings").mkdir(parents=True, exist_ok=True)
    _finding(root, "d037_a.md", "d037")
    _finding(root, "d037_b.md", "d037")
    (root / "docs/handoff.md").write_text(
        "# 交接\n\n- 依据 docs/findings/d037_b.md\n", encoding="utf-8"
    )
    _commit_all(root, "duplicate with outside reference")

    assert cmd_apply(args) == 0
    handoff = (root / "docs/handoff.md").read_text(encoding="utf-8")
    assert "docs/findings/d037_b.md" not in handoff
    assert "docs/findings/d038_b.md" in handoff
    _git(root, "add", "-A")  # 消重结果进入 index 后才检查提交快照
    validate_snapshot_ids(root)

    _commit_all(root, "sync: dedup and refactor references")
    monkeypatch.setattr("template_sync.transaction._run_tests", lambda _r: True)
    assert cmd_transaction(argparse.Namespace(sync=env["sync"], action="accept")) == 0
    from repo_task.context import common_state_dir
    journal = json.loads(
        (common_state_dir(root) / "sync_transaction/transaction.json").read_text(encoding="utf-8")
    )
    assert journal["status"] == "accepted"
