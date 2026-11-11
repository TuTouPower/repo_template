"""常驻迁移门禁不变量：迁移完成 + 模拟日常编辑后，status 必须仍为空。

entity-storage 需 git/schema、workflow-schema 需 src，干净态不适用；
reviews 本轮不纳入：与用户已裁定的 612c0f1「超过 5 个一律删除」契约相关，
主 agent 已列为「需用户决策」；本轮不改 reviews 检测条件，显式跳过，不偷偷改。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from factory_paths import FACTORY_ROOT

SCRIPTS = FACTORY_ROOT / "repo/.repo_template/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from repo_task.specs import render_spec_text, validate_specs
from template_sync import context as ctx
from template_sync.migrate import migration_findings
from template_sync.specs_migration import migrate_specs_layout, specs_migration_status

INDEX_HEADER = "# specs\n\n|slug|task 清单|最后更新时间|\n|---|---|---|\n"


@pytest.fixture
def consumer(tmp_path: Path) -> Path:
    root = tmp_path / "consumer"
    (root / "docs/specs").mkdir(parents=True)
    return root


def _migrate_specs_layout(consumer: Path) -> ctx.SyncContext:
    (consumer / "docs/specs/feature.md").write_text("# Feature\n\n需求。\n", encoding="utf-8")
    archive = consumer / "docs/archive/specs"
    archive.mkdir(parents=True)
    (archive / "old.md").write_text("# Old\n\n历史需求。\n", encoding="utf-8")
    (consumer / "docs/specs_index.md").write_text(
        INDEX_HEADER + "|feature|t001, baseline|2026-01-01|\n|old|t002|2025-01-01|\n",
        encoding="utf-8",
    )
    sync = ctx.SyncContext.for_consumer(consumer)
    migrate_specs_layout(sync, write=True)
    assert specs_migration_status(sync) == []
    return sync


def _daily_edit(text: str, kind: str) -> str:
    if kind == "改正文":
        return text.replace("# Feature", "# Feature（已修订）") + "\n补充一段说明。\n"
    if kind == "加空行":
        return text.replace("---\n\n", "---\n\n\n", 1)
    if kind == "front matter 内加空行":
        return text.replace("status: active\n", "status: active\n\n")
    if kind == "换 tasks 写法（去方括号）":
        return text.replace("tasks: [t001, baseline]", "tasks: t001, baseline")
    if kind == "换 tasks 写法（去逗号空格）":
        return text.replace("tasks: [t001, baseline]", "tasks: [t001,baseline]")
    if kind == "改 title":
        return text.replace("# Feature", "# 长期需求 Feature")
    raise AssertionError(kind)


DAILY_EDITS = [
    "改正文", "加空行", "front matter 内加空行",
    "换 tasks 写法（去方括号）", "换 tasks 写法（去逗号空格）", "改 title",
]


@pytest.mark.parametrize("kind", DAILY_EDITS)
def test_specs_layout_status_stays_empty_after_daily_edit(consumer, kind):
    sync = _migrate_specs_layout(consumer)
    path = consumer / "docs/specs/feature.md"
    path.write_text(_daily_edit(path.read_text(encoding="utf-8"), kind), encoding="utf-8")
    assert validate_specs(consumer) == []
    assert specs_migration_status(sync) == []


def test_specs_layout_status_stays_empty_after_cumulative_edits(consumer):
    sync = _migrate_specs_layout(consumer)
    path = consumer / "docs/specs/feature.md"
    text = path.read_text(encoding="utf-8")
    for kind in DAILY_EDITS:
        text = _daily_edit(text, kind)
        path.write_text(text, encoding="utf-8")
        assert validate_specs(consumer) == []
        assert specs_migration_status(sync) == []


@pytest.mark.parametrize("kind", [
    "legacy-state", "pending-ledger", "findings-ledger",
    "state-root", "conventions", "agents-zones", "adr", "retired-hooks",
])
def test_clean_consumer_findings_stay_empty_after_unrelated_edit(consumer, kind):
    sync = ctx.SyncContext.for_consumer(consumer)
    assert migration_findings(kind, sync) == []
    (consumer / "docs/specs/feature.md").write_text(
        render_spec_text("feature", "active", ["baseline"], "# Feature\n"),
        encoding="utf-8",
    )
    assert migration_findings(kind, sync) == []


@pytest.mark.skip(
    reason="reviews 与用户已裁定的 612c0f1「超过 5 个一律删除」契约相关；"
           "主 agent 已列为「需用户决策」，本轮不改 reviews 检测条件"
)
def test_reviews_invariant_pending_user_decision(consumer):
    assert migration_findings("reviews", ctx.SyncContext.for_consumer(consumer)) == []
