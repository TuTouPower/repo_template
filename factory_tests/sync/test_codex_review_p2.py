"""Codex 复审 P2 逐项回归（R01–R07）：每项先以负例锁定缺陷，再改生产代码。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS = FACTORY_ROOT / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS))

from template_sync import context as ctx
from template_sync.entity_storage_migration import (
    MigrationError,
    build_migration_plan,
    entity_storage_migration_status,
    migrate_entity_storage,
    validate_current_entities,
)


def _git(repo: Path, *args: str, check: bool = True, env: dict[str, str] | None = None):
    base = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    base.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_TERMINAL_PROMPT": "0",
    })
    if env:
        base.update(env)
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=check, env=base,
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
    for d in ("docs/findings", "docs/pending", "docs/spikes", "docs/tasks"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / ".gitignore").write_text(".scratch/\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    return root


def _commit_all(root: Path, msg: str, *, at: str | None = None) -> None:
    env = None
    if at:
        env = {"GIT_AUTHOR_DATE": at, "GIT_COMMITTER_DATE": at}
    _git(root, "add", "-A")
    _git(root, "commit", "-m", msg, env=env)


def _finding(root: Path, name: str, eid: str, extra: str = "") -> None:
    (root / "docs/findings" / name).write_text(
        f"# {eid} {name[:-3]}\n\n- 结论：fact\n- 证据：data\n- 影响：无\n- 现状：有效\n{extra}",
        encoding="utf-8",
    )


def _pending(root: Path, rel: str, eid: str, slug: str, extra: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\npid: "{eid}"\nslug: "{slug}"\nstatus: "todo"\n---\n# {eid} {slug}\n{extra}',
        encoding="utf-8",
    )


def _sync(root: Path) -> ctx.SyncContext:
    return ctx.SyncContext.for_consumer(root)


# ---------------------------------------------------------------------------
# R01：无条件替换旧目录前缀会改写无关外站 URL；没有实体迁移也会触发。
# ---------------------------------------------------------------------------


def test_r01_external_url_untouched_when_no_migration_sources(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    readme = (
        "[upstream](https://example.com/docs/archive/tasks/guide.md)\n\n"
        "Similar bare prefix: docs/archive/tasks/legacy-note.md\n"
    )
    (root / "README.md").write_text(readme, encoding="utf-8")
    _commit_all(root, "readme with external url")

    status = entity_storage_migration_status(_sync(root))
    assert not any("更新普通文档精确路径引用" in s for s in status), status
    migrate_entity_storage(_sync(root), write=True)
    assert (root / "README.md").read_text(encoding="utf-8") == readme


def test_r01_real_source_reference_rewritten_but_url_not(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    _pending(root, "docs/pending/todo/p001_x.md", "p001", "x")
    (root / "docs/handoff.md").write_text(
        "- 待办：docs/pending/todo/p001_x.md\n", encoding="utf-8"
    )
    (root / "README.md").write_text(
        "https://example.com/docs/pending/todo/guide.md\n", encoding="utf-8"
    )
    _commit_all(root, "legacy pending with outside refs")

    migrate_entity_storage(_sync(root), write=True)
    assert "docs/pending/p001_x.md" in (root / "docs/handoff.md").read_text(encoding="utf-8")
    assert "https://example.com/docs/pending/todo/guide.md" in (
        root / "README.md"
    ).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# R02：移动实体文档时用最终位置解析旧相对链接，改号+旧目录转换并存留断链。
# ---------------------------------------------------------------------------


def test_r02_old_dir_relative_link_follows_renumber(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    _pending(root, "docs/pending/p001_keep.md", "p001", "keep")
    _pending(root, "docs/pending/todo/p001_moved.md", "p001", "moved")
    _pending(
        root, "docs/pending/todo/p002_peer.md", "p002", "peer",
        extra="\n[moved](p001_moved.md#detail)\n",
    )
    _commit_all(root, "old dir peer link")

    migrate_entity_storage(_sync(root), write=True)

    peer = (root / "docs/pending/p002_peer.md").read_text(encoding="utf-8")
    assert "p001_moved.md" not in peer, peer
    assert "p003_moved.md#detail" in peer, peer
    assert (root / "docs/pending/p003_moved.md").is_file()
    assert entity_storage_migration_status(_sync(root)) == []


# ---------------------------------------------------------------------------
# R03：按 basename 排除全部 report.md 漏掉普通项目文档中的精确旧路径引用。
# ---------------------------------------------------------------------------


def test_r03_outside_report_md_reference_updated_and_declared(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    _finding(root, "d001_a.md", "d001")
    _finding(root, "d001_b.md", "d001")
    (root / "docs/design").mkdir(parents=True)
    (root / "docs/design/report.md").write_text(
        "See docs/findings/d001_b.md\n", encoding="utf-8"
    )
    _commit_all(root, "dup findings + outside report")

    plan = build_migration_plan(_sync(root))
    assert "docs/design/report.md" in plan.transaction_extra_rels

    migrate_entity_storage(_sync(root), write=True)
    text = (root / "docs/design/report.md").read_text(encoding="utf-8")
    assert "docs/findings/d002_b.md" in text, text
    assert "docs/findings/d001_b.md" not in text, text


# ---------------------------------------------------------------------------
# R04：旧总账条目把「#条目位置」当 Git 路径，保留原号规则不按真实历史执行。
# ---------------------------------------------------------------------------


def _ledger_item(plan, *, from_ledger: bool):
    return [f for f in plan.findings if f.from_ledger is from_ledger][0]


def test_r04_older_ledger_keeps_original_number(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    (root / "docs/findings.md").write_text(
        "# 发现\n\n## d001 Old ledger\n\n- 现状：有效\n", encoding="utf-8"
    )
    _commit_all(root, "old ledger", at="2026-01-02T00:00:00+08:00")
    _finding(root, "d001_new_file.md", "d001")
    _commit_all(root, "new file", at="2026-01-03T00:00:00+08:00")

    plan = build_migration_plan(_sync(root))
    assert _ledger_item(plan, from_ledger=True).final_id == "d001"
    assert _ledger_item(plan, from_ledger=False).final_id == "d002"


def test_r04_later_ledger_yields_to_earlier_file(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    _finding(root, "d001_early.md", "d001")
    _commit_all(root, "early file", at="2026-01-02T00:00:00+08:00")
    (root / "docs/findings.md").write_text(
        "# 发现\n\n## d001 Late ledger\n\n- 现状：有效\n", encoding="utf-8"
    )
    _commit_all(root, "late ledger", at="2026-01-03T00:00:00+08:00")

    plan = build_migration_plan(_sync(root))
    assert _ledger_item(plan, from_ledger=False).final_id == "d001"
    assert _ledger_item(plan, from_ledger=True).final_id == "d002"


# ---------------------------------------------------------------------------
# R05：裸编号待复核报告只收 finding，pending 与 spike 的歧义引用被静默漏掉。
# ---------------------------------------------------------------------------


def test_r05_pending_and_spike_bare_references_reported(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    _finding(root, "d001_a.md", "d001")
    _finding(root, "d001_b.md", "d001")
    _pending(
        root, "docs/pending/p001_probe.md", "p001", "probe",
        extra="\nEvidence d001.\n",
    )
    spike_dir = root / "docs/spikes/s001_probe"
    spike_dir.mkdir(parents=True)
    (spike_dir / "report.md").write_text(
        '---\nsid: "s001"\nslug: "probe"\nstatus: "active"\n---\n'
        "# Spike s001\n\nEvidence d001.\n",
        encoding="utf-8",
    )
    _commit_all(root, "cross bare refs")

    plan = build_migration_plan(_sync(root))
    reports = plan.reference_reports
    assert any("p001_probe" in r and "d001" in r for r in reports), reports
    assert any("s001_probe" in r and "d001" in r for r in reports), reports

    migrate_entity_storage(_sync(root), write=True)
    assert "Evidence d001." in (root / "docs/pending/p001_probe.md").read_text(encoding="utf-8")
    assert "Evidence d001." in (root / "docs/spikes/s001_probe/report.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# R06：受保护 handoff.json 在扩展名过滤时就被排除，历史引用报告永远覆盖不到。
# ---------------------------------------------------------------------------


def test_r06_handoff_json_reported_and_never_written(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    _finding(root, "d001_a.md", "d001")
    _finding(root, "d001_b.md", "d001")
    task_dir = root / "docs/tasks/t001_probe"
    task_dir.mkdir(parents=True)
    (task_dir / "progress.md").write_text(
        '---\ntid: "t001"\nslug: "probe"\ntitle: "probe"\nstatus: "backlog"\n---\n'
        "## 实施笔记\n\n无\n",
        encoding="utf-8",
    )
    (task_dir / "task.md").write_text("# t001 probe\n", encoding="utf-8")
    handoff = task_dir / "handoff.json"
    handoff.write_text(
        '{"findings": ["docs/findings/d001_b.md"]}\n', encoding="utf-8"
    )
    _commit_all(root, "dup + protected handoff")
    before = handoff.read_bytes()

    plan = build_migration_plan(_sync(root))
    assert any(
        "handoff.json" in r and "d001" in r for r in plan.reference_reports
    ), plan.reference_reports

    migrate_entity_storage(_sync(root), write=True)
    assert handoff.read_bytes() == before


# ---------------------------------------------------------------------------
# R07：finding 软链被两层静默跳过，严格验收判合法但取号仍因重复阻塞。
# ---------------------------------------------------------------------------


def test_r07_finding_symlink_blocked_consistently(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    _finding(root, "d001_a.md", "d001")
    (root / "docs/findings/d001_b.md").symlink_to("d001_a.md")
    _commit_all(root, "symlink finding")

    status = entity_storage_migration_status(_sync(root))
    assert any("BLOCKED" in s or "软链" in s for s in status), status
    with pytest.raises(MigrationError, match="软链"):
        validate_current_entities(_sync(root))
    with pytest.raises(MigrationError):
        build_migration_plan(_sync(root))


# ---------------------------------------------------------------------------
# R02 残留：搬迁文档指向「未搬迁目标」的相对链接仍会失效（round2）。
# ---------------------------------------------------------------------------


def test_r02b_relocated_pending_link_to_unmoved_target(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    _finding(root, "d001_fact.md", "d001")
    _pending(
        root, "docs/pending/todo/p001_probe.md", "p001", "probe",
        extra="\n[fact](../../findings/d001_fact.md#evidence)\n",
    )
    _commit_all(root, "todo link to unmoved finding")
    # 迁移前：从旧位置解析，目标真实存在
    assert (root / "docs/pending/todo" / "../../findings/d001_fact.md").resolve().is_file()

    migrate_entity_storage(_sync(root), write=True)

    text = (root / "docs/pending/p001_probe.md").read_text(encoding="utf-8")
    assert "../../findings/d001_fact.md" not in text, text
    assert "../findings/d001_fact.md#evidence" in text, text
    # 迁移后：从最终位置解析，仍指向同一个存在目标
    assert (root / "docs/pending" / "../findings/d001_fact.md").resolve().is_file()
    assert entity_storage_migration_status(_sync(root)) == []


def test_r02b_relocated_spike_link_to_unmoved_target(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    _finding(root, "d001_fact.md", "d001")
    spike_dir = root / "docs/archive/spikes/s001_probe"
    spike_dir.mkdir(parents=True)
    (spike_dir / "report.md").write_text(
        '---\nsid: "s001"\nslug: "probe"\nstatus: "completed"\n---\n'
        "# Spike s001\n\n## 结论\n采纳，依据 d001。\n\n"
        "[fact](../../../findings/d001_fact.md#evidence)\n",
        encoding="utf-8",
    )
    _commit_all(root, "archive spike link to unmoved finding")
    assert (
        root / "docs/archive/spikes/s001_probe" / "../../../findings/d001_fact.md"
    ).resolve().is_file()

    migrate_entity_storage(_sync(root), write=True)

    text = (root / "docs/spikes/s001_probe/report.md").read_text(encoding="utf-8")
    assert "../../../findings/d001_fact.md" not in text, text
    assert "../../findings/d001_fact.md#evidence" in text, text
    assert (
        root / "docs/spikes/s001_probe" / "../../findings/d001_fact.md"
    ).resolve().is_file()
    assert entity_storage_migration_status(_sync(root)) == []


# ---------------------------------------------------------------------------
# R04 残留：同一总账内 ordinal 两位数时，字符串排序把 #10 排在 #2 前（round2）。
# ---------------------------------------------------------------------------


def _write_two_digit_ledger(root: Path, rel: str, prefix: str) -> None:
    entries = [
        (20, "zero"), (21, "one"), (1, "earlier"),
        (22, "f2"), (23, "f3"), (24, "f4"), (25, "f5"),
        (26, "f6"), (27, "f7"), (28, "f8"), (1, "later"),
    ]
    lines = ["# 总账\n"]
    for num, title in entries:
        lines.append(f"\n## {prefix}{num:03d} {title}\n\n- 现状：有效\n")
    (root / rel).write_text("".join(lines), encoding="utf-8")


def test_r04b_findings_ledger_two_digit_ordinal(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    _write_two_digit_ledger(root, "docs/findings.md", "d")
    _commit_all(root, "findings ledger #2/#10")

    plan = build_migration_plan(_sync(root))
    by_rel = {f.source_rel: f for f in plan.findings if f.from_ledger}
    assert by_rel["docs/findings.md#2"].final_id == "d001", {
        k: v.final_id for k, v in by_rel.items() if "#" in k and ("#2" in k or "#10" in k)
    }
    assert "earlier" in by_rel["docs/findings.md#2"].content
    assert by_rel["docs/findings.md#10"].final_id == "d029"
    assert "later" in by_rel["docs/findings.md#10"].content


def test_r04b_pending_ledger_two_digit_ordinal(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    _write_two_digit_ledger(root, "docs/pending.md", "p")
    _commit_all(root, "pending ledger #2/#10")

    plan = build_migration_plan(_sync(root))
    by_rel = {p.source_rel: p for p in plan.pendings if p.from_ledger}
    assert by_rel["docs/pending.md#2"].final_id == "p001", {
        k: v.final_id for k, v in by_rel.items() if "#" in k and ("#2" in k or "#10" in k)
    }
    assert by_rel["docs/pending.md#10"].final_id == "p029"
