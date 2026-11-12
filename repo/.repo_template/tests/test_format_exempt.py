"""模板自有资产 format 豁免契约：根锚定、真实 ignore 语义与提交前断言。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from template_sync import context as ctx
from template_sync.shared_files import (
    _git_ignored,
    _prettierignore_rules,
    assert_format_exempt,
    merge_prettierignore,
)

pytestmark = pytest.mark.contract


def _sync(tmp_path):
    return ctx.SyncContext.for_consumer(tmp_path)


def _toolkit(tmp_path):
    (tmp_path / ".repo_template").mkdir()
    (tmp_path / ".repo_template/entity_schema.json").write_text('{"version": 1}\n')
    (tmp_path / ".repo_template/future_asset.json").write_text('{"x": 1}\n')
    merge_prettierignore(set(), _sync(tmp_path))


def test_rules_declare_root_anchored_boundary():
    rules = ctx.PRETTIERIGNORE_TEMPLATE_RULES
    assert "/.repo_template/" in rules
    assert "docs/**/handoff.json" in rules
    assert ".opencode/package.json" in rules
    assert ".opencode/package-lock.json" in rules
    assert not any(r != "/.repo_template/" and r.lstrip("/").startswith(".repo_template/") for r in rules)


def test_real_engine_keeps_root_anchor():
    rules = "\n".join(ctx.PRETTIERIGNORE_TEMPLATE_RULES) + "\n"
    nested = ["src/.repo_template/probe.json", "tests/fixtures/.repo_template/probe.json"]
    assert _git_ignored(rules, nested) == set()
    assert _git_ignored(rules, [".repo_template/entity_schema.json"]) == {
        ".repo_template/entity_schema.json"
    }


def test_real_engine_keeps_rule_order_and_negation():
    """否定目录 + 否定文件可重新包含工具链 JSON；仅否定文件无效。"""
    only_file = "/.repo_template/\n!.repo_template/entity_schema.json\n"
    assert _git_ignored(only_file, [".repo_template/entity_schema.json"]) == {
        ".repo_template/entity_schema.json"
    }
    negated = "/.repo_template/\n!.repo_template/\n!.repo_template/entity_schema.json\n"
    assert _git_ignored(negated, [".repo_template/entity_schema.json"]) == set()


def test_new_toolkit_json_needs_no_rule_registration(tmp_path):
    _toolkit(tmp_path)
    assert "/.repo_template/" in _prettierignore_rules(tmp_path / ".prettierignore")
    assert_format_exempt(tmp_path)


def test_negation_rule_is_blocked_before_commit(tmp_path):
    _toolkit(tmp_path)
    pi = tmp_path / ".prettierignore"
    pi.write_text(pi.read_text() + "!.repo_template/\n!.repo_template/entity_schema.json\n")
    with pytest.raises(ctx.SyncError, match="未被 format 豁免覆盖"):
        assert_format_exempt(tmp_path)


def test_missing_rules_fail_closed(tmp_path):
    (tmp_path / ".repo_template").mkdir()
    (tmp_path / ".repo_template/entity_schema.json").write_text('{"version": 1}\n')
    with pytest.raises(ctx.SyncError, match="format 豁免规则缺失"):
        assert_format_exempt(tmp_path)


def test_actual_consumer_rules_with_old_unanchored_rule_are_blocked(tmp_path):
    """消费仓实际 .prettierignore 残留旧未锚定规则时，嵌套目录范围核验必须阻断。"""
    (tmp_path / ".repo_template").mkdir()
    (tmp_path / ".repo_template/entity_schema.json").write_text('{"version": 1}\n')
    (tmp_path / ".prettierignore").write_text(
        ".repo_template/\n" + "\n".join(ctx.PRETTIERIGNORE_TEMPLATE_RULES) + "\n"
    )
    with pytest.raises(ctx.SyncError, match="嵌套同名目录被误豁免"):
        assert_format_exempt(tmp_path)
