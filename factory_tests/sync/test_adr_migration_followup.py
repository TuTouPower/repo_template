"""A3 follow-up：围栏外无编号 H2 一律停止，且停止早于任何写盘。"""

from __future__ import annotations

import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS = FACTORY_ROOT / "repo/.repo_template/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from template_sync import context as ctx
from template_sync.adr_migration import adr_migration_status, migrate_adr


@pytest.fixture
def consumer(tmp_path: Path) -> Path:
    root = tmp_path / "consumer"
    (root / "docs/blueprint").mkdir(parents=True)
    return root


def _write_decisions(consumer: Path, text: str) -> Path:
    path = consumer / "docs/blueprint/decisions.md"
    path.write_text(text, encoding="utf-8")
    return path


def _decisions_dir(consumer: Path) -> Path:
    return consumer / "docs/blueprint/decisions"


def _assert_stopped_before_write(consumer: Path, source: Path, original: str) -> None:
    assert source.is_file()
    assert source.read_text(encoding="utf-8") == original
    assert not _decisions_dir(consumer).exists()


CBROWSER_PREFIX = (
    "# 决策记录（ADR）\n\n"
    "只记录已经确认、影响后续工作的非显然决策。\n\n"
    "## 产品历史勘误入口与 ADR 传播约定（t024）\n\n"
    "- 历史审阅/决策文档的事实勘误集中在独立勘误文件中。\n\n"
)


def test_unnumbered_h2_before_first_nnn_stops_without_writes(consumer):
    text = CBROWSER_PREFIX + "## 001 采用方案（2026-01-02）\n\n- 结论：A\n"
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    with pytest.raises(ctx.SyncError, match="围栏外 H2"):
        migrate_adr(sync, write=True)
    _assert_stopped_before_write(consumer, source, text)


def test_unnumbered_h2_between_nnn_entries_stops_without_writes(consumer):
    text = (
        "# ADR\n\n## 001 采用方案（2026-01-02）\n\n- 结论：A\n\n"
        "## 产品历史勘误入口与 ADR 传播约定（t024）\n\n- 真实规则\n\n"
        "## 002 替代方案（2026-01-03）\n\n- 替代：001\n"
    )
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    with pytest.raises(ctx.SyncError, match="围栏外 H2"):
        migrate_adr(sync, write=True)
    _assert_stopped_before_write(consumer, source, text)


def test_unnumbered_h2_after_last_nnn_stops_without_writes(consumer):
    text = (
        "# ADR\n\n## 001 采用方案（2026-01-02）\n\n- 结论：A\n\n"
        "## 附：实施备注\n\n- 不属于任何 ADR 的规则\n"
    )
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    with pytest.raises(ctx.SyncError, match="围栏外 H2"):
        migrate_adr(sync, write=True)
    _assert_stopped_before_write(consumer, source, text)


def test_indented_unnumbered_h2_stops_without_writes(consumer):
    text = "# ADR\n\n  ## 无编号条目\n\n## 001 采用方案（2026-01-02）\n\n- 结论：A\n"
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    with pytest.raises(ctx.SyncError, match="围栏外 H2"):
        migrate_adr(sync, write=True)
    _assert_stopped_before_write(consumer, source, text)


def test_fenced_unnumbered_h2_does_not_stop_and_stays_in_adr(consumer):
    text = (
        "# ADR\n\n## 001 采用方案（2026-01-02）\n\n"
        "```markdown\n## 产品历史勘误入口与 ADR 传播约定（t024）\n```\n"
    )
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    reports = migrate_adr(sync, write=True)
    files = sorted(_decisions_dir(consumer).glob("*.md"))
    assert len(files) == 1
    assert "## 产品历史勘误入口与 ADR 传播约定（t024）" in files[0].read_text(encoding="utf-8")
    assert not source.exists()
    assert reports


def test_fenced_unnumbered_h2_in_preface_does_not_stop(consumer):
    text = (
        "# ADR\n\n```markdown\n## 格式示例（不是条目）\n```\n\n"
        "## 001 采用方案（2026-01-02）\n\n- 结论：A\n"
    )
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    reports = migrate_adr(sync, write=True)
    assert len(list(_decisions_dir(consumer).glob("*.md"))) == 1
    assert not source.exists()
    assert any("格式示例（不是条目）" in item for item in reports)


def test_non_h2_preface_is_reported(consumer):
    text = (
        "# 决策记录\n\n这段前言不得丢失。\n\n"
        "## 001 采用方案（2026-01-02）\n\n- 结论：A\n"
    )
    _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    assert any("这段前言不得丢失" in item for item in adr_migration_status(sync))
    assert any("这段前言不得丢失" in item for item in migrate_adr(sync, write=False))


def test_invalid_supersedes_stops_before_writing(consumer):
    text = (
        "# ADR\n\n## 001 采用方案（2026-01-02）\n\n- 结论：A\n\n"
        "## 002 替代旧案（2026-01-03）\n\n- 替代：999\n"
    )
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    with pytest.raises(ctx.SyncError, match="替代 999"):
        migrate_adr(sync, write=True)
    _assert_stopped_before_write(consumer, source, text)


def test_preface_report_says_content_is_removed_after_migration(consumer):
    path = consumer / "docs/blueprint/decisions.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# 决策记录\n\n迁移前言内容。\n\n## 001 选择方案（2026-01-02）\n\n- 结论：A\n",
        encoding="utf-8",
    )
    sync = ctx.SyncContext.for_consumer(consumer)
    reports = migrate_adr(sync, write=False)
    text = "\n".join(reports)
    assert "不生成 ADR" in text
    assert "迁移后从仓库移除" in text
    assert "安全保留" not in text
    assert "迁移前言内容。" in text
