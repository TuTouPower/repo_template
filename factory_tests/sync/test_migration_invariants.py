"""迁移后合法日常编辑不重建遗留物；reviews 动态保留策略单独验证。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

from factory_paths import FACTORY_ROOT

SCRIPTS = FACTORY_ROOT / "repo/.repo_template/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from repo_task.specs import validate_specs
from template_sync import context as ctx
from migration_support import exercise_migrations, findings
from template_sync.specs_migration import migrate_specs_layout, specs_migration_status
from git_repo_support import install_fake_md_kx
from test_repo_sync import _git, _install_current_workflow_templates, env
from test_consumer_migration_chain import _build_988fd06_consumer
from migration_support import migration

def _migrate(root, kind):
    migration(kind).execute(ctx.SyncContext.for_consumer(root), FACTORY_ROOT / "repo")
from test_migration_interfaces import _legacy_workflow_task, _retired_guard

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
    "legacy-state", "workflow-schema", "pending-ledger", "findings-ledger",
    "conventions", "retired-hooks",
])
def test_migrated_layout_stays_current_after_daily_edit(env, tmp_path, monkeypatch, kind):
    root, sync = env["consumer"], env["sync"]
    install_fake_md_kx(tmp_path, monkeypatch)
    if kind == "legacy-state":
        path = sync.legacy_state_path()
        path.parent.mkdir(parents=True)
        path.write_text('{"user_prompts": [{"text": "legacy prompt"}]}\n', encoding="utf-8")
    elif kind == "workflow-schema":
        _install_current_workflow_templates(env["src"])
        _legacy_workflow_task(root)
    elif kind in {"pending-ledger", "findings-ledger"}:
        ledger, prefix = ("pending", "p") if kind == "pending-ledger" else ("findings", "d")
        (root / "docs" / f"{ledger}.md").write_text(
            f"# Ledger\n\n## {prefix}001 Feature\n\n原始说明。\n", encoding="utf-8",
        )
    elif kind == "conventions":
        path = root / "docs/blueprint/conventions.md"
        path.write_text("# 项目约定\n\n## 原子写\n", encoding="utf-8")
    else:
        _retired_guard(root)
    assert findings(kind, sync, src=env["src"])
    assert exercise_migrations(argparse.Namespace(sync=sync, kind=kind, write=True)) is not None
    assert findings(kind, sync, src=env["src"]) == []

    if kind == "legacy-state":
        data = json.loads(sync.state_path.read_text(encoding="utf-8"))
        data["user_prompts"].append({"text": "daily prompt"})
        sync.state_path.write_text(json.dumps(data), encoding="utf-8")
    elif kind == "workflow-schema":
        path = root / "docs/tasks/t001_old/progress.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n实施笔记补充。\n", encoding="utf-8")
    elif kind in {"pending-ledger", "findings-ledger"}:
        path = next((root / "docs" / ledger).glob("*.md"))
        path.write_text(path.read_text(encoding="utf-8") + "\n补充验证结果。\n", encoding="utf-8")
    elif kind == "conventions":
        path.write_text(path.read_text(encoding="utf-8") + "\n## 项目约定\n\n业务使用四空格缩进。\n", encoding="utf-8")
    else:
        path = root / ".claude/settings.json"
        path.write_text('{"permissions": {"allow": ["Bash(pytest *)"]}}\n', encoding="utf-8")
    assert findings(kind, sync, src=env["src"]) == []


@pytest.mark.parametrize("kind", ["entity-storage", "agents-zones", "adr", "reviews"])
def test_real_consumer_migration_and_daily_edit(tmp_path, kind):
    root = _build_988fd06_consumer(tmp_path)
    sync = ctx.SyncContext.for_consumer(root)
    assert findings(kind, sync)
    if kind == "agents-zones":
        _migrate(root, "entity-storage")
        _git(root, "add", "-A")
        _git(root, "commit", "--no-verify", "-m", "migrate entity storage")
        _migrate(root, "state-root")
    _migrate(root, kind)
    assert findings(kind, sync) == []
    if kind == "entity-storage":
        path = root / "docs/pending/p001_fix_cli.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n补充任务边界。\n", encoding="utf-8")
    elif kind == "agents-zones":
        path = root / "AGENTS.md"
        path.write_text("项目使用 Python。\n\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    elif kind == "adr":
        path = next((root / "docs/blueprint/decisions").glob("*.md"))
        path.write_text(path.read_text(encoding="utf-8") + "\n补充决策理由。\n", encoding="utf-8")
    else:
        path = next((root / "docs/reviews").glob("review_*/review_general.md"))
        path.write_text("PASS\n\n补充审阅证据。\n", encoding="utf-8")
    assert findings(kind, sync) == []
    if kind == "reviews":
        # 超过五个再次要求显式清理是用户已裁定的策略，不能伪称单调为空。
        directory = root / "docs/reviews/review_20260201_120000"
        directory.mkdir()
        (directory / "review_general.md").write_text("PASS\n", encoding="utf-8")
        assert findings(kind, sync)
