"""repo_sync 机械合并：gitignore / prettierignore / MCP / AGENTS 两区。

从 test_repo_sync 拆分；fixture 与 helper 仍由 test_repo_sync 提供。"""

import contextlib
import json
import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

from factory_paths import FACTORY_ROOT
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = FACTORY_ROOT / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from template_sync import context as ctx
from template_sync.agents_merge import _apply_agents_sectioned, _replace_agents_force, agents_section_status
from template_sync.apply import _assert_distributable_tree, cmd_apply
from template_sync.assets import retired_template_warnings, sync_dir, sync_file
from template_sync.cli import _parse_decisions, main as repo_sync_main
from template_sync.common import _detect_json_indent, _git as sync_git
from template_sync.conventions_migration import conventions_migration_status, migrate_conventions
from template_sync.gates import _run_tests
from template_sync.hooks import (
    _RETIRED_MERGE_GUARD_COMMAND,
    cmd_install_hooks,
    migrate_retired_hooks,
)
from template_sync.shared_files import (
    _PRETTIERIGNORE_HEADER,
    _PRETTIERIGNORE_OLD_HEADER,
    _sanitize_mcp_servers,
    merge_gitignore,
    merge_mcp,
    merge_prettierignore,
)
from template_sync.skills_links import (
    cmd_link_skills,
    opencode_commands_status,
    repair_symlinks,
    skill_status,
    symlink_status,
    sync_opencode_commands,
)
from template_sync.source import assert_not_self, resolve_src, source_identity
from template_sync.state import (
    active_prompts,
    cmd_init,
    cmd_prompt,
    prompt_substrings,
    read_state,
    write_state,
)
from template_sync.status_plan import cmd_plan, cmd_status
from template_sync.workflow_migration import (
    _GUIDE_CLOSE,
    _GUIDE_OPEN,
    _guide_blocks_by_heading,
    _implementation_guidance,
    _migrate_task_text,
    _rename_legacy_title,
    _replace_guide_blocks,
    force_migrate_workflow_tasks,
    workflow_migration_status,
)
from export_template import export_template
from review_support import configure_project_verification


from test_repo_sync import (
    CONSUMER_AGENTS, _add_prompt, _bind_repo_task_context, _commit_consumer_baseline,
    _ensure_hook, _git, _install_current_workflow_templates, _old_task_documents,
    _task_branch, _write_public_registry, env,
)


def test_gitignore_merge_with_prompt_block(env):
    src, consumer = env["src"], env["consumer"]
    gi = consumer / ".gitignore"
    gi.write_text("node_modules/\n.env\n")
    _add_prompt(env["sync"], ".env 不要 ignore", ".gitignore,.env")

    changed: set[Path] = set()
    info = merge_gitignore(src, prompt_substrings(read_state(env["sync"])), changed, env["sync"])
    text = gi.read_text()
    assert "node_modules/" in text
    assert ".env" in text.splitlines()          # 块外消费规则不被删除
    assert "*.log" in text
    assert "*.log" in info["added"]
    assert info["removed"] == []
    assert gi in changed

def test_gitignore_merge_keeps_negation_rules(env):
    src, consumer = env["src"], env["consumer"]
    (src / ".gitignore").write_text(
        "# repo-template:begin\nnode_modules/\n*.log\n!config/.env.example\n# repo-template:end\n",
        encoding="utf-8",
    )
    gi = consumer / ".gitignore"
    gi.write_text("node_modules/\n.env\n!.env.example\n", encoding="utf-8")

    changed: set[Path] = set()
    info = merge_gitignore(src, [".env"], changed, env["sync"])

    lines = gi.read_text(encoding="utf-8").splitlines()
    assert "!.env.example" in lines
    assert "!config/.env.example" in lines
    assert ".env" in lines
    assert info["removed"] == []
    assert "!config/.env.example" in info["added"]

def test_prettierignore_merge_creates_and_dedupes(env):
    consumer = env["consumer"]
    pi = consumer / ".prettierignore"

    changed: set[Path] = set()
    info = merge_prettierignore(changed, env["sync"])
    text = pi.read_text()
    for rule in ctx.PRETTIERIGNORE_TEMPLATE_RULES:
        assert rule in text.splitlines()
    assert pi in changed

    # 二次合并：去重，无改动
    changed = set()
    info = merge_prettierignore(changed, env["sync"])
    assert info["added"] == []
    assert changed == set()
    assert text == pi.read_text()

def test_prettierignore_preserves_consumer_rules(env):
    consumer = env["consumer"]
    pi = consumer / ".prettierignore"
    pi.write_text("pnpm-lock.yaml\n*.md\n")

    changed: set[Path] = set()
    merge_prettierignore(changed, env["sync"])
    lines = pi.read_text().splitlines()
    assert "pnpm-lock.yaml" in lines             # 消费独有保留
    assert "*.md" in lines
    for rule in ctx.PRETTIERIGNORE_TEMPLATE_RULES:
        assert rule in lines
    assert pi in changed

def test_prettierignore_old_header_matches_stripped_line(env):
    pi = env["consumer"] / ".prettierignore"
    pi.write_text("  " + _PRETTIERIGNORE_OLD_HEADER + "  \n", encoding="utf-8")

    merge_prettierignore(set(), env["sync"])

    lines = pi.read_text(encoding="utf-8").splitlines()
    stripped = [line.strip() for line in lines]
    assert _PRETTIERIGNORE_OLD_HEADER not in stripped
    assert stripped.count(_PRETTIERIGNORE_HEADER[0]) == 1
    for rule in ctx.PRETTIERIGNORE_TEMPLATE_RULES:
        assert rule in lines

def test_prettierignore_new_header_presence_matches_stripped_line(env):
    pi = env["consumer"] / ".prettierignore"
    pi.write_text("  " + _PRETTIERIGNORE_HEADER[0] + "  \n", encoding="utf-8")

    merge_prettierignore(set(), env["sync"])

    lines = pi.read_text(encoding="utf-8").splitlines()
    assert sum(1 for line in lines if line.strip() == _PRETTIERIGNORE_HEADER[0]) == 1
    for rule in ctx.PRETTIERIGNORE_TEMPLATE_RULES:
        assert rule in lines

def test_prettierignore_covers_sync_state_and_handoff_without_derived_indexes(env):
    """prettierignore 包含 sync_state 与 handoff.json，不再包含已废除的派生 index 规则。"""
    rules = set(ctx.PRETTIERIGNORE_TEMPLATE_RULES)
    assert ".repo_template/sync_state.json" not in rules
    assert "docs/**/handoff.json" in rules
    assert "docs/tasks_index.json" not in rules
    assert "docs/archive/tasks_index.json" not in rules

def test_prettierignore_omits_undistributed_test_paths(env):
    # G1：测试移出产物后消费仓不再收到这两个文件，规则随之删除，新同步不再追加
    undistributed = (
        ".repo_template/tests/package.json",
        ".repo_template/tests/test_chain_plan_cases.js",
    )
    rules = set(ctx.PRETTIERIGNORE_TEMPLATE_RULES)
    assert ".repo_template/scripts/package.json" in rules
    for rule in undistributed:
        assert rule not in rules
    pi = env["consumer"] / ".prettierignore"
    merge_prettierignore(set(), env["sync"])
    lines = pi.read_text().splitlines()
    for rule in undistributed:
        assert rule not in lines

def test_detect_json_indent(env):
    assert _detect_json_indent('{\n  "a": 1\n}\n') == 2
    assert _detect_json_indent('{\n    "a": 1\n}\n') == 4
    assert _detect_json_indent('{\n\t"a": 1\n}\n') == "\t"
    assert _detect_json_indent('{"a": 1}') == 2
    assert _detect_json_indent('') == 2

def test_mcp_merge_preserves_consumer_indent(env):
    src, consumer = env["src"], env["consumer"]
    (src / ".mcp.json").write_text(json.dumps({"mcpServers": {"tpl-server": {"command": "x"}}}))
    dmcp = consumer / ".mcp.json"
    dmcp.write_text('{\n    "mcpServers": {\n        "consumer-server": {"command": "y"}\n    }\n}\n')

    changed: set[Path] = set()
    merge_mcp(src, changed, env["sync"])
    raw = dmcp.read_text()
    assert json.loads(raw)["mcpServers"]["tpl-server"] == {"command": "x"}
    assert '\n    "mcpServers"' in raw  # 消费仓 4 空格体例保留，未被重排成 2 空格
    assert dmcp in changed

def test_settings_rewrite_preserves_consumer_indent(env):
    consumer = env["consumer"]
    settings = consumer / '.claude/settings.json'
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        'hooks': {'PreToolUse': [{
            'matcher': 'Bash',
            'hooks': [
                {'type': 'command', 'command': _RETIRED_MERGE_GUARD_COMMAND},
                {'type': 'command', 'command': 'echo keep'},
            ],
        }]},
    }, ensure_ascii=False, indent=4) + "\n")
    reports = migrate_retired_hooks(env["sync"])
    raw = settings.read_text()
    assert json.loads(raw)["hooks"]["PreToolUse"][0]["hooks"] == [
        {"type": "command", "command": "echo keep"},
    ]
    assert '\n    "hooks"' in raw  # 退役项移除，但 4 空格体例保留
    assert reports

def test_retired_template_warnings(env):
    consumer = env["consumer"]
    assert retired_template_warnings(env["sync"]) == []
    stale = consumer / ".github/workflows/repo-template-ci.yml"
    stale.parent.mkdir(parents=True)
    stale.write_text("name: repo-template\n")
    warnings = retired_template_warnings(env["sync"])
    assert len(warnings) == 1
    assert ".github/workflows/repo-template-ci.yml" in warnings[0]

def test_mcp_merge_keywise(env):
    src, consumer = env["src"], env["consumer"]
    (src / ".mcp.json").write_text(json.dumps({"mcpServers": {"tpl-server": {"command": "x"}}}))
    dmcp = consumer / ".mcp.json"
    dmcp.write_text(json.dumps({"mcpServers": {"consumer-server": {"command": "y"}}}))

    changed: set[Path] = set()
    merge_mcp(src, changed, env["sync"])
    ddata = json.loads(dmcp.read_text())
    assert "tpl-server" in ddata["mcpServers"]
    assert "consumer-server" in ddata["mcpServers"]
    # 已有键不覆盖（禁冲密钥）
    (src / ".mcp.json").write_text(json.dumps({"mcpServers": {"tpl-server": {"command": "OVERWRITE"}}}))
    changed = set()
    merge_mcp(src, changed, env["sync"])
    assert json.loads(dmcp.read_text())["mcpServers"]["tpl-server"] == {"command": "x"}

def test_mcp_create_and_merge_sanitizes_environment_variables(env):
    """验证新建 MCP 与新增 server 键时清理非占位环境变量，并提供审阅提示（Issue #54）。"""
    src, consumer = env["src"], env["consumer"]
    tpl_server = {
        "command": "node",
        "args": ["server.js"],
        "env": {"SECRET_TOKEN": "super_secret_value", "ENDPOINT": "https://api.internal.corp"},
    }
    (src / ".mcp.json").write_text(json.dumps({"mcpServers": {"demo-server": tpl_server}}))
    dmcp = consumer / ".mcp.json"
    assert not dmcp.exists()

    changed: set[Path] = set()
    reports = merge_mcp(src, changed, env["sync"])

    assert dmcp in changed
    assert any("需审阅" in r or "占位" in r for r in reports)
    data = json.loads(dmcp.read_text(encoding="utf-8"))
    env_vars = data["mcpServers"]["demo-server"]["env"]
    assert env_vars["SECRET_TOKEN"] == "<SECRET_TOKEN_PLACEHOLDER>"
    assert env_vars["ENDPOINT"] == "<ENDPOINT_PLACEHOLDER>"

def test_sectioned_agents_replaces_template_zone_only(env):
    src, consumer = env["src"], env["consumer"]
    (src / "AGENTS.md").write_text(
        "模板项目介绍。\n\n<!-- repo-template:begin -->\n新模板区\n<!-- repo-template:end -->\n"
    )
    (consumer / "AGENTS.md").write_text(CONSUMER_AGENTS)
    changed: set[Path] = set()
    assert _apply_agents_sectioned(src, changed, env["sync"]) is True
    result = (consumer / "AGENTS.md").read_text()
    assert result == "消费仓项目介绍与项目规则。\n\n<!-- repo-template:begin -->\n新模板区\n<!-- repo-template:end -->\n"
    assert consumer / "AGENTS.md" in changed

def test_sectioned_agents_missing_markers_fails_closed(env):
    src, consumer = env["src"], env["consumer"]
    (src / "AGENTS.md").write_text("<!-- repo-template:begin -->\n规则\n<!-- repo-template:end -->\n")
    (consumer / "AGENTS.md").write_text("旧格式\n")
    with pytest.raises(ctx.SyncError, match="migrate agents-zones"):
        _apply_agents_sectioned(src, set(), env["sync"])

def test_agents_section_status_reports_zones(env):
    src, consumer = env["src"], env["consumer"]
    source = "模板\n<!-- repo-template:begin -->\n规则\n<!-- repo-template:end -->\n"
    (src / "AGENTS.md").write_text(source)
    (consumer / "AGENTS.md").write_text(CONSUMER_AGENTS)
    status = agents_section_status(src, env["sync"])
    assert status["template"] == "different"
    assert status["project"] == "protected"

def test_mcp_merge_tolerates_non_dict_structure(env, capsys):
    """验证遇到合法 JSON 但非对象的错误 MCP 结构时不崩溃并告警跳过（Issue #26）。"""
    src, consumer = env["src"], env["consumer"]
    (src / ".mcp.json").write_text(json.dumps({"mcpServers": {"tpl": {"command": "x"}}}))
    dmcp = consumer / ".mcp.json"

    # 1. 消费端为数组
    dmcp.write_text("[]\n", encoding="utf-8")
    changed = set()
    merge_mcp(src, changed, env["sync"])
    err = capsys.readouterr().err
    assert "非 JSON 对象" in err
    assert dmcp not in changed

    # 2. 消费端 mcpServers 为数组
    dmcp.write_text(json.dumps({"mcpServers": [1, 2, 3]}), encoding="utf-8")
    changed = set()
    merge_mcp(src, changed, env["sync"])
    err = capsys.readouterr().err
    assert "mcpServers 非对象" in err
