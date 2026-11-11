"""模板自有资产 format 豁免契约：整目录边界、覆盖判定与提交前断言。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from template_sync import context as ctx
from template_sync.shared_files import (
    _prettierignore_rules,
    _rule_covers,
    assert_format_exempt,
    merge_prettierignore,
)

pytestmark = pytest.mark.contract


def _sync(tmp_path):
    return ctx.SyncContext.for_consumer(tmp_path)


def test_rules_declare_owned_directory_boundary():
    rules = ctx.PRETTIERIGNORE_TEMPLATE_RULES
    assert ".repo_template/" in rules
    assert "docs/**/handoff.json" in rules
    assert ".opencode/package.json" in rules
    assert ".opencode/package-lock.json" in rules
    assert not any(r != ".repo_template/" and r.startswith(".repo_template/") for r in rules)


def test_rule_covers_directory_and_glob():
    assert _rule_covers(".repo_template/", ".repo_template/entity_schema.json")
    assert _rule_covers(".repo_template/", ".repo_template/skills/registry.json")
    assert _rule_covers("docs/**/handoff.json", "docs/tasks/t001_x/handoff.json")
    assert not _rule_covers(".repo_template/sync_manifest.json", ".repo_template/entity_schema.json")


def test_new_toolkit_json_needs_no_rule_registration(tmp_path):
    (tmp_path / ".repo_template").mkdir()
    (tmp_path / ".repo_template/entity_schema.json").write_text('{"version": 1}\n')
    (tmp_path / ".repo_template/future_asset.json").write_text('{"x": 1}\n')
    merge_prettierignore(set(), _sync(tmp_path))
    assert ".repo_template/" in _prettierignore_rules(tmp_path / ".prettierignore")
    assert_format_exempt(tmp_path)


def test_enum_style_rules_fail_closed(tmp_path, monkeypatch):
    (tmp_path / ".repo_template").mkdir()
    (tmp_path / ".repo_template/entity_schema.json").write_text('{"version": 1}\n')
    monkeypatch.setattr(ctx, "PRETTIERIGNORE_TEMPLATE_RULES", (".repo_template/sync_manifest.json",))
    merge_prettierignore(set(), _sync(tmp_path))
    with pytest.raises(ctx.SyncError, match="未被 format 豁免覆盖"):
        assert_format_exempt(tmp_path)


def test_missing_rules_fail_closed(tmp_path):
    (tmp_path / ".repo_template").mkdir()
    (tmp_path / ".repo_template/entity_schema.json").write_text('{"version": 1}\n')
    with pytest.raises(ctx.SyncError, match="format 豁免规则缺失"):
        assert_format_exempt(tmp_path)
