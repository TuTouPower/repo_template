"""repo_sync 软链修复、状态检查与旧 merge guard 清理。

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


def test_repair_symlinks(env):
    src, consumer = env["src"], env["consumer"]
    dst = consumer / ".repo_template/skills/task-run"
    dst.mkdir(parents=True)
    (dst / "SKILL.md").write_text("run\n")
    changed: set[Path] = set()
    reports = repair_symlinks(changed, env["sync"])
    link = consumer / ".claude/skills/task-run"
    assert link.is_symlink()
    assert link.resolve() == dst.resolve()
    agents_link = consumer / ".agents/skills/task-run"
    assert agents_link.is_symlink()
    assert agents_link.resolve() == dst.resolve()
    assert not reports
    changed = set()
    repair_symlinks(changed, env["sync"])
    assert not changed
    foreign_src = consumer / ".repo_template/skills/foreign-skill"
    foreign_src.mkdir(parents=True)
    foreign = consumer / ".claude/skills/foreign-skill"
    foreign.write_text("not a symlink\n")
    changed = set()
    reports = repair_symlinks(changed, env["sync"])
    assert any("foreign-skill" in r for r in reports)
    assert foreign.exists()
    assert foreign not in changed

def test_repair_symlinks_converts_stale_real_dir(env):
    src, consumer = env["src"], env["consumer"]
    dst = consumer / ".repo_template/skills/task-run"
    dst.mkdir(parents=True)
    (dst / "SKILL.md").write_text("run\n")
    # 旧架构残留：.agents/skills/<name> 曾是模板同步进去的真实目录
    stale = consumer / ".agents/skills/task-run"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("stale old\n")

    changed: set[Path] = set()
    reports = repair_symlinks(changed, env["sync"])

    assert stale.is_symlink()
    assert stale.resolve() == dst.resolve()
    assert stale in changed
    assert any("旧真实目录已转软链" in r for r in reports)

def test_repair_symlinks_cleans_stale_managed_symlinks(env, tmp_path):
    src, consumer = env["src"], env["consumer"]
    active_dst = consumer / ".repo_template/skills/active-skill"
    active_dst.mkdir(parents=True)
    (active_dst / "SKILL.md").write_text("active\n")

    # 模拟有效 skill 初始软链
    changed = set()
    repair_symlinks(changed, env["sync"])
    assert (consumer / ".claude/skills/active-skill").is_symlink()
    assert (consumer / ".agents/skills/active-skill").is_symlink()

    # 模拟已被模板删除/更名的旧 skill 悬空软链
    old_claude = consumer / ".claude/skills/old-skill"
    old_claude.symlink_to("../../.repo_template/skills/old-skill")
    old_agents = consumer / ".agents/skills/old-skill"
    old_agents.symlink_to("../../.repo_template/skills/old-skill")

    # 模拟外部自定义软链（非 .repo_template 管理）
    ext_target = tmp_path / "ext_skill"
    ext_target.mkdir()
    ext_link = consumer / ".agents/skills/custom-ext"
    ext_link.symlink_to(ext_target)

    # 模拟手写本地目录
    custom_dir = consumer / ".agents/skills/custom-local"
    custom_dir.mkdir()

    # 校验 symlink_status 报告：两个宿主目录都报告，路径带宿主前缀
    status = symlink_status(env["sync"])
    for rel in (".claude/skills/active-skill", ".agents/skills/active-skill"):
        assert {"name": "active-skill", "rel": rel, "state": "ok"} in status
    for rel in (".claude/skills/old-skill", ".agents/skills/old-skill"):
        assert {"name": "old-skill", "rel": rel, "state": "cleanup"} in status

    # 执行 repair_symlinks
    changed = set()
    reports = repair_symlinks(changed, env["sync"])

    # 旧模板软链应被清理
    assert not old_claude.is_symlink()
    assert not old_agents.is_symlink()
    assert old_claude in changed
    assert old_agents in changed
    assert any(".claude/skills/old-skill 已清理" in r for r in reports)
    assert any(".agents/skills/old-skill 已清理" in r for r in reports)

    # 有效 skill、外部软链、本地目录保留
    assert (consumer / ".claude/skills/active-skill").is_symlink()
    assert ext_link.is_symlink()
    assert ext_link not in changed
    assert custom_dir.is_dir()
    assert custom_dir not in changed

def test_symlink_status_reports_agents_host_missing_and_broken(env, capsys):
    consumer = env["consumer"]
    skill = consumer / ".repo_template/skills/task-run"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("run\n", encoding="utf-8")
    _write_public_registry(consumer, "task-run")
    repair_symlinks(set(), env["sync"])

    agents_link = consumer / ".agents/skills/task-run"
    agents_link.unlink()
    status = symlink_status(env["sync"])
    assert {"name": "task-run", "rel": ".agents/skills/task-run", "state": "repair"} in status
    assert {"name": "task-run", "rel": ".claude/skills/task-run", "state": "ok"} in status

    agents_link.symlink_to("../../.repo_template/skills/missing")
    status = symlink_status(env["sync"])
    assert {"name": "task-run", "rel": ".agents/skills/task-run", "state": "repair"} in status

    assert cmd_plan(Namespace(sync=env["sync"])) == 0
    out = capsys.readouterr().out
    assert ".agents/skills/task-run" in out and ".claude/skills/task-run" in out

def test_skill_status_reports_stale_real_dir(env):
    src, consumer = env["src"], env["consumer"]
    dst = consumer / ".repo_template/skills/task-run"
    dst.mkdir(parents=True)
    (dst / "SKILL.md").write_text("run\n")
    stale = consumer / ".agents/skills/task-run"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("stale\n")

    items = skill_status(src, env["sync"])
    assert {"name": "task-run", "action": "stale"} in items

def test_repair_symlinks_removes_retired_managed_merge_guard(env):
    consumer = env['consumer']
    guard = consumer / '.claude/hooks/merge_guard.py'
    guard.parent.mkdir(parents=True)
    guard.symlink_to('../../.repo_template/hooks/merge_guard.py')
    reports = migrate_retired_hooks(env["sync"])
    assert not guard.exists() and not guard.is_symlink()
    assert any('已退役并移除' in line for line in reports)

def test_repair_symlinks_preserves_manual_merge_guard_file(env):
    consumer = env['consumer']
    guard = consumer / '.claude/hooks/merge_guard.py'
    guard.parent.mkdir(parents=True)
    guard.write_text('manual\n')
    changed: set[Path] = set()
    repair_symlinks(changed, env["sync"])
    assert guard.read_text() == 'manual\n'
    assert guard not in changed

def test_repair_symlinks_preserves_manual_guard_setting(env):
    consumer = env['consumer']
    settings = consumer / '.claude/settings.json'
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        'hooks': {'PreToolUse': [{
            'matcher': 'Bash',
            'hooks': [
                {'type': 'command', 'command': _RETIRED_MERGE_GUARD_COMMAND},
                {'type': 'command', 'command': 'python3 scripts/custom_merge_guard.py'},
            ],
        }]},
    }))
    guard = consumer / '.claude/hooks/merge_guard.py'
    guard.parent.mkdir(parents=True, exist_ok=True)
    guard.write_text('manual\n')
    changed: set[Path] = set()
    repair_symlinks(changed, env["sync"])
    data = json.loads(settings.read_text())
    commands = [entry['command'] for entry in data['hooks']['PreToolUse'][0]['hooks']]
    assert commands == [_RETIRED_MERGE_GUARD_COMMAND, 'python3 scripts/custom_merge_guard.py']
    assert guard.read_text() == 'manual\n'
    assert settings not in changed and guard not in changed

def test_repair_symlinks_removes_exact_retired_setting_when_link_is_missing(env):
    consumer = env['consumer']
    settings = consumer / '.claude/settings.json'
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        'hooks': {'PreToolUse': [{
            'matcher': 'Bash',
            'hooks': [
                {'type': 'command', 'command': _RETIRED_MERGE_GUARD_COMMAND},
                {'type': 'command', 'command': 'python3 scripts/custom_merge_guard.py'},
            ],
        }]},
    }))
    reports = migrate_retired_hooks(env["sync"])
    data = json.loads(settings.read_text())
    assert data['hooks']['PreToolUse'][0]['hooks'] == [
        {'type': 'command', 'command': 'python3 scripts/custom_merge_guard.py'},
    ]
    assert reports

def test_repair_symlinks_removes_retired_guard_setting_and_link(env):
    consumer = env['consumer']
    settings = consumer / '.claude/settings.json'
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        'hooks': {
            'PreToolUse': [{
                'matcher': 'Bash',
                'hooks': [
                    {'type': 'command', 'command': 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/merge_guard.py"'},
                    {'type': 'command', 'command': 'python3 scripts/custom_merge_guard.py'},
                    {'type': 'command', 'command': 'echo keep'},
                ],
            }],
            'PostToolUse': [{'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': 'echo post'}]}],
        },
    }))
    guard = consumer / '.claude/hooks/merge_guard.py'
    guard.parent.mkdir(parents=True, exist_ok=True)
    guard.symlink_to('../../.repo_template/hooks/merge_guard.py')
    reports = migrate_retired_hooks(env["sync"])
    data = json.loads(settings.read_text())
    assert data['hooks']['PreToolUse'][0]['hooks'] == [
        {'type': 'command', 'command': 'python3 scripts/custom_merge_guard.py'},
        {'type': 'command', 'command': 'echo keep'},
    ]
    assert 'PostToolUse' in data['hooks']
    assert not guard.is_symlink()
    assert reports

def test_repair_symlinks_refuses_malformed_settings_before_unlink(env):
    consumer = env['consumer']
    settings = consumer / '.claude/settings.json'
    settings.parent.mkdir(parents=True)
    settings.write_text('{bad')
    guard = consumer / '.claude/hooks/merge_guard.py'
    guard.parent.mkdir(parents=True, exist_ok=True)
    guard.symlink_to('../../.repo_template/hooks/merge_guard.py')
    with pytest.raises(ctx.SyncError, match='不能安全移除'):
        migrate_retired_hooks(env["sync"])
    assert guard.is_symlink()
    assert settings.read_text() == '{bad'
