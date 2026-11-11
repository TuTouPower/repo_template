"""A2：specs 迁移重构为单一计划函数，缺字段按索引补齐、索引缺行沿用 front matter。

六种 front matter 形态（只含 slug、slug+tid、完整字段+有索引一致、
完整字段+有索引冲突、完整字段+无索引行、slug 与文件名不一致）；
另补非法 status（active/archive）与 archive 同名目标冲突的失败路径。
全部失败路径断言零写入。
P1-7 计划即检测回归：status 与执行共用完整计划、规范文本比对、计划失败转 finding。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS = FACTORY_ROOT / "repo/.repo_template/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from repo_task.specs import load_specs, render_spec_text
from template_sync import context as ctx
from template_sync.migrate import cmd_migrate
from template_sync.specs_migration import migrate_specs_layout, specs_migration_status

INDEX_HEADER = "# specs\n\n|slug|task 清单|最后更新时间|\n|---|---|---|\n"


@pytest.fixture
def consumer(tmp_path: Path) -> Path:
    root = tmp_path / "consumer"
    (root / "docs/specs").mkdir(parents=True)
    return root


def _write_spec(consumer: Path, name: str, text: str) -> Path:
    path = consumer / "docs/specs" / name
    path.write_text(text, encoding="utf-8")
    return path


def _write_index(consumer: Path, rows: str) -> Path:
    path = consumer / "docs/specs_index.md"
    path.write_text(INDEX_HEADER + rows, encoding="utf-8")
    return path


def _snapshot(consumer: Path) -> dict[Path, bytes]:
    return {
        path: path.read_bytes()
        for path in sorted(consumer.rglob("*"))
        if path.is_file()
    }


def _assert_zero_write(consumer: Path, before: dict[Path, bytes]) -> None:
    assert _snapshot(consumer) == before


def test_only_slug_uses_index_tasks(consumer):
    _write_spec(consumer, "feature.md", "---\nslug: feature\n---\n\n# Feature\n")
    _write_index(consumer, "|feature|t001, t002|2026-01-01|\n")
    sync = ctx.SyncContext.for_consumer(consumer)
    reports = migrate_specs_layout(sync, write=True)
    specs = {spec["slug"]: spec for spec in load_specs(consumer)}
    assert specs["feature"]["status"] == "active"
    assert specs["feature"]["tasks"] == ["t001", "t002"]
    assert specs["feature"]["body"].strip() == "# Feature"
    assert any("docs/specs/feature.md -> front matter" in line for line in reports)


def test_slug_plus_tid_drops_extra_field_and_reports(consumer):
    _write_spec(
        consumer, "feature.md",
        "---\nslug: feature\ntid: t001\n---\n\n# Feature\n",
    )
    _write_index(consumer, "|feature|t001|2026-01-01|\n")
    sync = ctx.SyncContext.for_consumer(consumer)
    reports = migrate_specs_layout(sync, write=True)
    specs = {spec["slug"]: spec for spec in load_specs(consumer)}
    assert specs["feature"]["status"] == "active"
    assert specs["feature"]["tasks"] == ["t001"]
    assert not (consumer / "docs/specs_index.md").exists()
    assert any("丢弃多余字段" in line and "tid=t001" in line for line in reports)


def test_full_front_matter_matching_index(consumer):
    _write_spec(
        consumer, "feature.md",
        "---\nslug: feature\nstatus: active\ntasks: [t001, t002]\n---\n\n# Feature\n",
    )
    _write_index(consumer, "|feature|t001, t002|2026-01-01|\n")
    sync = ctx.SyncContext.for_consumer(consumer)
    migrate_specs_layout(sync, write=True)
    specs = {spec["slug"]: spec for spec in load_specs(consumer)}
    assert specs["feature"]["status"] == "active"
    assert specs["feature"]["tasks"] == ["t001", "t002"]
    assert specs["feature"]["body"].strip() == "# Feature"


def test_full_front_matter_conflicting_with_index_aborts_without_writes(consumer):
    source = _write_spec(
        consumer, "feature.md",
        "---\nslug: feature\nstatus: active\ntasks: [t001]\n---\n\n# Feature\n",
    )
    index_path = _write_index(consumer, "|feature|t002|2026-01-01|\n")
    before = _snapshot(consumer)
    sync = ctx.SyncContext.for_consumer(consumer)
    with pytest.raises(ctx.SyncError, match="与索引 .* 冲突"):
        migrate_specs_layout(sync, write=True)
    _assert_zero_write(consumer, before)
    assert source.is_file() and index_path.is_file()


def test_full_front_matter_without_index_row_keeps_tasks(consumer):
    _write_spec(
        consumer, "feature.md",
        "---\nslug: feature\nstatus: active\ntasks: [t003]\n---\n\n# Feature\n",
    )
    sync = ctx.SyncContext.for_consumer(consumer)
    reports = migrate_specs_layout(sync, write=True)
    specs = {spec["slug"]: spec for spec in load_specs(consumer)}
    assert specs["feature"]["status"] == "active"
    assert specs["feature"]["tasks"] == ["t003"]
    assert any("无索引行，沿用 front matter tasks: [t003]" in line for line in reports)


def test_slug_mismatch_aborts_without_writes(consumer):
    source = _write_spec(
        consumer, "feature.md",
        "---\nslug: other\nstatus: active\ntasks: [t001]\n---\n\n# Feature\n",
    )
    _write_index(consumer, "|feature|t001|2026-01-01|\n")
    before = _snapshot(consumer)
    sync = ctx.SyncContext.for_consumer(consumer)
    with pytest.raises(ctx.SyncError, match="与文件名 .* 不一致"):
        migrate_specs_layout(sync, write=True)
    _assert_zero_write(consumer, before)
    assert source.is_file()


def test_missing_tasks_without_index_row_defaults_baseline_and_reports(consumer):
    _write_spec(consumer, "feature.md", "---\nslug: feature\n---\n\n# Feature\n")
    sync = ctx.SyncContext.for_consumer(consumer)
    reports = migrate_specs_layout(sync, write=True)
    specs = {spec["slug"]: spec for spec in load_specs(consumer)}
    assert specs["feature"]["tasks"] == ["baseline"]
    assert any(
        "无索引行，使用 tasks: [baseline]" in line and "人工确认" in line
        for line in reports
    )


def test_archive_front_matter_forces_retired_and_drops_extra_field(consumer):
    archive = consumer / "docs/archive/specs"
    archive.mkdir(parents=True)
    (archive / "old.md").write_text(
        "---\nslug: old\nstatus: active\ntasks: [t002]\ntid: t002\n---\n\n# Old\n",
        encoding="utf-8",
    )
    _write_index(consumer, "|old|t002|2025-01-01|\n")
    sync = ctx.SyncContext.for_consumer(consumer)
    reports = migrate_specs_layout(sync, write=True)
    specs = {spec["slug"]: spec for spec in load_specs(consumer)}
    assert specs["old"]["status"] == "retired"
    assert specs["old"]["tasks"] == ["t002"]
    assert not (consumer / "docs/archive/specs").exists()
    assert any("由 active 改为 retired" in line for line in reports)
    assert any("丢弃多余字段" in line and "tid=t002" in line for line in reports)


def test_archive_illegal_status_aborts_without_writes(consumer):
    archive = consumer / "docs/archive/specs"
    archive.mkdir(parents=True)
    source = archive / "old.md"
    source.write_text(
        "---\nslug: old\nstatus: bogus\ntasks: [t002]\n---\n\n# Old\n",
        encoding="utf-8",
    )
    _write_index(consumer, "|old|t002|2025-01-01|\n")
    before = _snapshot(consumer)
    sync = ctx.SyncContext.for_consumer(consumer)
    with pytest.raises(ctx.SyncError, match="status 只允许 active/retired"):
        migrate_specs_layout(sync, write=True)
    _assert_zero_write(consumer, before)
    assert source.is_file()


def test_active_illegal_status_aborts_without_writes(consumer):
    source = _write_spec(
        consumer, "feature.md",
        "---\nslug: feature\nstatus: bogus\ntasks: [t001]\n---\n\n# Feature\n",
    )
    _write_index(consumer, "|feature|t001|2026-01-01|\n")
    before = _snapshot(consumer)
    sync = ctx.SyncContext.for_consumer(consumer)
    with pytest.raises(ctx.SyncError, match="status 只允许 active/retired"):
        migrate_specs_layout(sync, write=True)
    _assert_zero_write(consumer, before)
    assert source.is_file()


def test_archive_same_name_conflict_aborts_without_writes(consumer):
    active_source = _write_spec(
        consumer, "dup.md",
        "---\nslug: dup\nstatus: active\ntasks: [t001]\n---\n\n# Dup\n",
    )
    archive = consumer / "docs/archive/specs"
    archive.mkdir(parents=True)
    archive_source = archive / "dup.md"
    archive_source.write_text(
        "---\nslug: dup\nstatus: retired\ntasks: [t001]\n---\n\n# Dup old\n",
        encoding="utf-8",
    )
    _write_index(consumer, "|dup|t001|2026-01-01|\n")
    before = _snapshot(consumer)
    sync = ctx.SyncContext.for_consumer(consumer)
    with pytest.raises(ctx.SyncError, match="同名 spec 冲突"):
        migrate_specs_layout(sync, write=True)
    _assert_zero_write(consumer, before)
    assert active_source.is_file() and archive_source.is_file()


def test_dry_run_returns_reports_without_writes(consumer):
    source = _write_spec(
        consumer, "feature.md",
        "---\nslug: feature\ntid: t001\n---\n\n# Feature\n",
    )
    _write_index(consumer, "|feature|t001|2026-01-01|\n")
    before = _snapshot(consumer)
    sync = ctx.SyncContext.for_consumer(consumer)
    reports = migrate_specs_layout(sync, write=False)
    _assert_zero_write(consumer, before)
    assert source.read_text(encoding="utf-8").startswith("---\nslug: feature\ntid: t001")
    assert any("丢弃多余字段" in line for line in reports)


def test_corrupt_front_matter_aborts_with_sync_error_without_writes(consumer):
    source = _write_spec(
        consumer, "feature.md",
        "---\nslug: feature\n未闭合\n\n# Feature\n",
    )
    before = _snapshot(consumer)
    sync = ctx.SyncContext.for_consumer(consumer)
    with pytest.raises(ctx.SyncError, match="front matter 未闭合"):
        migrate_specs_layout(sync, write=True)
    _assert_zero_write(consumer, before)
    assert source.is_file()


def test_status_detects_slug_only_spec_and_public_migrate_repairs(consumer):
    subprocess.run(["git", "init", "-q", str(consumer)], check=True, capture_output=True)
    _write_spec(consumer, "feature.md", "---\nslug: feature\n---\n\n# Feature\n")
    sync = ctx.SyncContext.for_consumer(consumer)
    assert specs_migration_status(sync)
    assert cmd_migrate(argparse.Namespace(kind="specs-layout", write=True, sync=sync)) == 0
    specs = {spec["slug"]: spec for spec in load_specs(consumer)}
    assert specs["feature"]["status"] == "active"
    assert specs["feature"]["tasks"] == ["baseline"]
    assert specs_migration_status(sync) == []


def test_status_detects_extra_front_matter_field(consumer):
    _write_spec(
        consumer, "feature.md",
        "---\nslug: feature\ntid: t001\n---\n\n# Feature\n",
    )
    sync = ctx.SyncContext.for_consumer(consumer)
    assert specs_migration_status(sync)
    migrate_specs_layout(sync, write=True)
    specs = {spec["slug"]: spec for spec in load_specs(consumer)}
    assert "tid" not in specs["feature"]["fm"]
    assert specs_migration_status(sync) == []


def test_status_empty_and_zero_write_for_canonical_spec(consumer):
    _write_spec(
        consumer, "feature.md",
        render_spec_text("feature", "active", ["t001"], "# Feature\n"),
    )
    sync = ctx.SyncContext.for_consumer(consumer)
    assert specs_migration_status(sync) == []
    before = _snapshot(consumer)
    migrate_specs_layout(sync, write=True)
    _assert_zero_write(consumer, before)
    assert specs_migration_status(sync) == []


def test_status_surfaces_plan_errors_as_findings(consumer):
    cases = [
        (
            "---\nslug: feature\nstatus: bogus\ntasks: [t001]\n---\n\n# Feature\n",
            "status 只允许 active/retired",
        ),
        (
            "---\nslug: other\nstatus: active\ntasks: [t001]\n---\n\n# Feature\n",
            "与文件名",
        ),
    ]
    for text, expected in cases:
        _write_spec(consumer, "feature.md", text)
        sync = ctx.SyncContext.for_consumer(consumer)
        findings = specs_migration_status(sync)
        assert findings
        assert any(expected in item for item in findings)
