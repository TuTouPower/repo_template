"""ADR 迁移（decisions.md -> 一条一文件）工厂测试。

覆盖：围栏外非 NNN H2 一律停止；NNN 唯一校验（重复即停止）；前言语法
（只许 H1 与正文，出现 H3+ 停止）；零条目即空文件退役；文件名统一为
YYYYMMDD_<slug>.md（同名冲突追加 _2/_3 后缀、supersedes 指向带后缀文件）。
全部失败路径断言停止早于写盘、零写入。
"""

from __future__ import annotations

import subprocess
import sys
from argparse import Namespace
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS = FACTORY_ROOT / "repo/.repo_template/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from template_sync import context as ctx
from template_sync.adr_migration import adr_migration_status, migrate_adr
from migration_support import exercise_migrations


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


def test_duplicate_nnn_status_reports_and_migrate_fails_without_writes(consumer):
    text = (
        "# ADR\n\n## 001 采用方案（2026-01-02）\n\n- 结论：A\n\n"
        "## 001 重复编号（2026-01-03）\n\n- 结论：B\n"
    )
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    status = adr_migration_status(sync)
    assert any("编号 001 重复（第 3、7 行）" in item for item in status), status
    assert any("拒绝迁移，请先改号" in item for item in status)
    with pytest.raises(ctx.SyncError, match="编号 001 重复"):
        migrate_adr(sync, write=True)
    _assert_stopped_before_write(consumer, source, text)


def test_duplicate_nnn_dry_run_reports_and_writes_nothing(consumer):
    text = (
        "# ADR\n\n## 044 方案甲（2026-01-02）\n\n- 结论：A\n\n"
        "## 053 方案乙（2026-01-03）\n\n- 结论：B\n\n"
        "## 044 方案丙（2026-01-04）\n\n- 结论：C\n"
    )
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    with pytest.raises(ctx.SyncError, match="编号 044 重复（第 3、11 行）"):
        migrate_adr(sync, write=False)
    _assert_stopped_before_write(consumer, source, text)


TEMPLATE_ONLY_PREFACE = (
    "# 决策记录（ADR）\n\n只记录已经确认、影响后续工作的非显然决策。\n"
)


def test_preface_only_status_reports_removal_and_migrate_deletes_file(consumer):
    source = _write_decisions(consumer, TEMPLATE_ONLY_PREFACE)
    sync = ctx.SyncContext.for_consumer(consumer)
    status = adr_migration_status(sync)
    assert status[0] == "decisions.md 无条目，迁移将移除（前言见报告）", status
    assert any("只记录已经确认" in item for item in status)
    reports = migrate_adr(sync, write=True)
    assert not source.exists()
    assert not _decisions_dir(consumer).exists()
    assert any("只记录已经确认" in item for item in reports)


def test_preface_only_dry_run_writes_nothing(consumer):
    source = _write_decisions(consumer, TEMPLATE_ONLY_PREFACE)
    sync = ctx.SyncContext.for_consumer(consumer)
    reports = migrate_adr(sync, write=False)
    assert source.is_file()
    assert not _decisions_dir(consumer).exists()
    assert any("只记录已经确认" in item for item in reports)


def test_preface_h3_stops_without_writes(consumer):
    text = "# ADR\n\n### 前言小标题\n\n## 001 采用方案（2026-01-02）\n\n- 结论：A\n"
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    status = adr_migration_status(sync)
    assert any("第 3 行前言出现 H3+ 标题" in item for item in status), status
    with pytest.raises(ctx.SyncError, match="第 3 行前言出现 H3\\+ 标题"):
        migrate_adr(sync, write=True)
    _assert_stopped_before_write(consumer, source, text)


def test_preface_bare_h3_heading_stops_without_writes(consumer):
    text = "# ADR\n\n###\n\n## 001 采用方案（2026-01-02）\n\n- 结论：A\n"
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    status = adr_migration_status(sync)
    assert any("第 3 行前言出现 H3+ 标题" in item for item in status), status
    with pytest.raises(ctx.SyncError, match="第 3 行前言出现 H3\\+ 标题"):
        migrate_adr(sync, write=True)
    _assert_stopped_before_write(consumer, source, text)


def test_preface_fenced_h3_does_not_stop(consumer):
    text = (
        "# ADR\n\n```markdown\n### 格式示例（不是条目）\n```\n\n"
        "## 001 采用方案（2026-01-02）\n\n- 结论：A\n"
    )
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    reports = migrate_adr(sync, write=True)
    assert len(list(_decisions_dir(consumer).glob("*.md"))) == 1
    assert not source.exists()
    assert reports


def test_h3_inside_entry_body_does_not_stop(consumer):
    text = (
        "# ADR\n\n## 001 采用方案（2026-01-02）\n\n"
        "### 实现细节\n\n- 结论：A\n"
    )
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    reports = migrate_adr(sync, write=True)
    files = list(_decisions_dir(consumer).glob("*.md"))
    assert len(files) == 1
    assert "### 实现细节" in files[0].read_text(encoding="utf-8")
    assert not source.exists()
    assert reports


def test_migrated_filename_has_no_nnn_and_h1_keeps_nnn(consumer):
    text = (
        "# ADR\n\n## 001 采用方案（2026-01-02）\n\n- 结论：A\n\n"
        "## 002 替代方案（2026-01-03）\n\n- 替代：001\n"
    )
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    reports = migrate_adr(sync, write=True)
    files = sorted(path.name for path in _decisions_dir(consumer).glob("*.md"))
    assert files == ["20260102_entry.md", "20260103_entry.md"]
    first = (_decisions_dir(consumer) / "20260102_entry.md").read_text(encoding="utf-8")
    assert first.startswith("# 001 采用方案（2026-01-02）")
    second = (_decisions_dir(consumer) / "20260103_entry.md").read_text(encoding="utf-8")
    assert "supersedes: [20260102_entry.md]" in second
    assert not source.exists()
    assert any("20260102_entry.md" in item for item in reports)


def test_same_date_same_slug_gets_suffix_in_nnn_order(consumer):
    text = (
        "# ADR\n\n## 002 采用方案（2026-01-02）\n\n- 结论：B\n\n"
        "## 001 采用方案（2026-01-02）\n\n- 结论：A\n"
    )
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    migrate_adr(sync, write=True)
    files = sorted(path.name for path in _decisions_dir(consumer).glob("*.md"))
    assert files == ["20260102_entry.md", "20260102_entry_2.md"]
    assert "# 001 采用方案" in (_decisions_dir(consumer) / "20260102_entry.md").read_text(
        encoding="utf-8"
    )
    assert "# 002 采用方案" in (_decisions_dir(consumer) / "20260102_entry_2.md").read_text(
        encoding="utf-8"
    )
    assert not source.exists()


def test_supersedes_points_to_suffixed_filename(consumer):
    text = (
        "# ADR\n\n## 001 采用方案（2026-01-02）\n\n- 结论：A\n\n"
        "## 002 采用方案（2026-01-02）\n\n- 结论：B\n\n"
        "## 003 替代方案（2026-01-03）\n\n- 替代：002\n"
    )
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    migrate_adr(sync, write=True)
    files = sorted(path.name for path in _decisions_dir(consumer).glob("*.md"))
    assert files == ["20260102_entry.md", "20260102_entry_2.md", "20260103_entry.md"]
    third = (_decisions_dir(consumer) / "20260103_entry.md").read_text(encoding="utf-8")
    assert "supersedes: [20260102_entry_2.md]" in third
    assert not source.exists()


def test_conflict_with_existing_target_file_gets_suffix(consumer):
    target = _decisions_dir(consumer)
    target.mkdir(parents=True)
    existing = target / "20260102_entry.md"
    existing.write_text("# 000 已有文件\n", encoding="utf-8")
    text = "# ADR\n\n## 001 采用方案（2026-01-02）\n\n- 结论：A\n"
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    migrate_adr(sync, write=True)
    assert existing.read_text(encoding="utf-8") == "# 000 已有文件\n"
    migrated = target / "20260102_entry_2.md"
    assert migrated.is_file()
    assert "# 001 采用方案" in migrated.read_text(encoding="utf-8")
    assert not source.exists()


def test_entry_h2_with_extra_spaces_still_gets_canonical_h1(consumer):
    text = "# ADR\n\n##  001  采用方案（2026-01-02）\n\n- 结论：A\n"
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    migrate_adr(sync, write=True)
    files = list(_decisions_dir(consumer).glob("*.md"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8").startswith("# 001 采用方案（2026-01-02）")
    assert not source.exists()


def test_mixed_44_and_044_are_duplicate_number(consumer):
    text = (
        "# ADR\n\n"
        "## 44 方案甲（2026-01-02）\n\n- 结论：A\n\n"
        "## 044 方案乙（2026-01-03）\n\n- 结论：B\n"
    )
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    status = adr_migration_status(sync)
    assert any("重复" in item for item in status), status
    with pytest.raises(ctx.SyncError, match="重复"):
        migrate_adr(sync, write=True)
    _assert_stopped_before_write(consumer, source, text)


def test_h1_keeps_original_nnn_text_with_leading_zeros(consumer):
    text = "# ADR\n\n## 044 采用方案（2026-01-02）\n\n- 结论：A\n"
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    reports = migrate_adr(sync, write=True)
    files = list(_decisions_dir(consumer).glob("*.md"))
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    assert body.startswith("# 044 采用方案（2026-01-02）")
    assert not body.startswith("# 44 ")
    assert not source.exists()
    assert reports


def test_h3_to_h6_inside_entry_body_are_allowed(consumer):
    text = (
        "# ADR\n\n## 001 采用方案（2026-01-02）\n\n"
        "### 三级\n\n#### 四级\n\n##### 五级\n\n###### 六级\n\n"
        "- 结论：A\n"
    )
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    reports = migrate_adr(sync, write=True)
    files = list(_decisions_dir(consumer).glob("*.md"))
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    for heading in ("### 三级", "#### 四级", "##### 五级", "###### 六级"):
        assert heading in body
    assert body.startswith("# 001 采用方案（2026-01-02）")
    assert not source.exists()
    assert reports


# 旧 test_h1_inside_entry_body_stops_parsing 整体删除：它断言迁移成功并丢弃
# H1 后全部源内容，违背正文只许 H3+、非法结构必须拒绝且零写入的契约。
@pytest.mark.parametrize("heading", ["# 下一份记录", "   # 下一份记录", "#"])
@pytest.mark.parametrize("write", [False, True])
def test_entry_body_h1_rejects_entire_migration_without_writes(consumer, heading, write):
    text = (
        "# ADR\n\n## 001 采用方案（2026-01-02）\n\n"
        "- 结论：A\n\n### 细节\n\n- 细节内容\n\n"
        f"{heading}\n\n## 002 后续方案（2026-01-03）\n\n- 结论：B\n"
    )
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    status = adr_migration_status(sync)
    assert len(status) == 1
    assert "第 11 行" in status[0] and "H1" in status[0]
    _assert_stopped_before_write(consumer, source, text)
    with pytest.raises(ctx.SyncError, match="第 11 行.*H1"):
        migrate_adr(sync, write=write)
    _assert_stopped_before_write(consumer, source, text)


@pytest.mark.parametrize("write", [False, True])
def test_migrate_adr_command_rejects_body_h1_without_losing_source(consumer, write):
    subprocess.run(["git", "init", "-b", "main", str(consumer)], check=True, capture_output=True)
    text = (
        "## 001 采用方案（2026-01-02）\n\n# 非法标题\n\n"
        "## 002 后续方案（2026-01-03）\n\n必须保留的后续内容。\n"
    )
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    with pytest.raises(ctx.SyncError, match="第 3 行.*H1"):
        exercise_migrations(Namespace(kind="adr", write=write, sync=sync))
    _assert_stopped_before_write(consumer, source, text)


@pytest.mark.parametrize("fence", ["```markdown", "~~~markdown"])
def test_fenced_h1_in_entry_body_is_preserved_with_later_entries(consumer, fence):
    close = fence[:3]
    example = f"{fence}\n# 示例标题\n## 999 示例条目\n{close}"
    text = (
        "# ADR\n\n## 001 采用方案（2026-01-02）\n\n"
        f"{example}\n\n"
        "## 002 后续方案（2026-01-03）\n\n- 结论：B\n"
    )
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    assert any("2 条" in item for item in adr_migration_status(sync))
    migrate_adr(sync, write=True)
    files = sorted(_decisions_dir(consumer).glob("*.md"))
    assert len(files) == 2
    assert example in files[0].read_text(encoding="utf-8")
    assert "结论：B" in files[1].read_text(encoding="utf-8")
    assert not source.exists()


def test_non_nnn_h2_inside_entry_body_still_stops(consumer):
    text = (
        "# ADR\n\n## 001 采用方案（2026-01-02）\n\n"
        "- 结论：A\n\n### 细节\n\n"
        "## 无编号小标题\n\n- 混入内容\n"
    )
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    with pytest.raises(ctx.SyncError, match="围栏外 H2"):
        migrate_adr(sync, write=True)
    _assert_stopped_before_write(consumer, source, text)


def test_supersedes_matches_by_int_identity_across_nnn_text(consumer):
    text = (
        "# ADR\n\n"
        "## 44 采用方案（2026-01-02）\n\n- 结论：A\n\n"
        "## 053 替代方案（2026-01-03）\n\n- 替代：044\n"
    )
    source = _write_decisions(consumer, text)
    sync = ctx.SyncContext.for_consumer(consumer)
    reports = migrate_adr(sync, write=True)
    files = sorted(path.name for path in _decisions_dir(consumer).glob("*.md"))
    assert files == ["20260102_entry.md", "20260103_entry.md"]
    second = (_decisions_dir(consumer) / "20260103_entry.md").read_text(encoding="utf-8")
    assert "supersedes: [20260102_entry.md]" in second
    assert not source.exists()
    assert reports
