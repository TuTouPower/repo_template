"""repo_sync apply 全流程、回滚与文件同步边界。

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
from template_sync.agents_merge import _apply_agents_sectioned, agents_section_status
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
    _task_branch, _write_public_registry, env, repo_state_dir,
)


def test_apply_flow_writes_and_advances_state(env, capsys):
    src, consumer, head = env["src"], env["consumer"], env["head"]
    old_agents = consumer / "AGENTS.md"
    old_agents.write_text(CONSUMER_AGENTS)

    cmd_apply(Namespace(sync=env["sync"], allow_dirty_source=True, decision=["AGENTS.md:update"], skip_tests=True, decisions={"AGENTS.md": "update"}))
    out = capsys.readouterr().out

    # 硬同步
    assert (consumer / ".repo_template/scripts/task.py").exists()
    assert (consumer / ".repo_template/docs/spike_report_template.md").read_text() == "# report\n"
    assert not (consumer / ".claude/hooks/merge_guard.py").exists()
    assert (consumer / ".repo_template/skills/task-run/SKILL.md").read_text().startswith("---")
    assert (consumer / ".claude/skills/task-run").is_symlink()
    assert (consumer / ".agents/skills/task-run").is_symlink()
    assert (consumer / ".opencode/commands/task-run.md").is_file()
    # AGENTS 项目区保留，模板区替换
    result = (consumer / "AGENTS.md").read_text()
    assert "消费仓项目介绍与项目规则。" in result
    assert "SRC AGENTS" in result
    # state 推进
    state = read_state(env["sync"])
    assert state["last_synced_commit"] == head
    assert state["last_synced_at"]
    # 改动清单输出（点名 add 依据）
    assert ".repo_template/scripts/task.py" in out
    assert "AGENTS.md" in out

def test_symlinked_agents_warned_in_status_plan_and_rejected_by_apply(env, tmp_path, capsys):
    consumer = env["consumer"]
    target = tmp_path / "shared_agents.md"
    target.write_text("shared\n", encoding="utf-8")
    (consumer / "AGENTS.md").symlink_to(target)
    warning = "apply 不支持软链 AGENTS.md，先替换为真实文件"

    assert cmd_status(Namespace(sync=env["sync"])) == 0
    assert warning in capsys.readouterr().out
    assert cmd_plan(Namespace(sync=env["sync"])) == 0
    assert warning in capsys.readouterr().out

    with pytest.raises(ctx.SyncError, match="apply 不支持软链 AGENTS.md"):
        cmd_apply(Namespace(sync=env["sync"], allow_dirty_source=True, skip_tests=True, decisions={}))
    assert not (repo_state_dir(consumer) / "sync_transaction" / "transaction.json").exists()

def test_apply_skips_unresolved_shared_unit(env):
    src, consumer = env["src"], env["consumer"]
    old_agents = consumer / "AGENTS.md"
    old_agents.write_text(CONSUMER_AGENTS)

    cmd_apply(Namespace(sync=env["sync"], allow_dirty_source=True, decision=[], skip_tests=True, decisions={}))
    result = (consumer / "AGENTS.md").read_text()
    assert "消费仓项目介绍与项目规则。" in result
    assert "SRC AGENTS" in result

def test_apply_src_dirty_does_not_advance_commit(env):
    src, consumer = env["src"], env["consumer"]
    (src / ".repo_template/scripts/dirty_extra.py").write_text("x\n")  # SRC 未提交改动
    _add_prompt(env["sync"], "跳过测试", "")
    cmd_apply(Namespace(sync=env["sync"], allow_dirty_source=True, decision=[], skip_tests=True, decisions={}))
    state = read_state(env["sync"])
    assert state["last_synced_commit"] is None
    assert state["last_synced_at"]

def test_apply_test_failure_rolls_back_keeps_backup_and_prints_changed(env, monkeypatch, capsys):
    """契约测试失败：完整回滚、保留备份、输出 changed 清单与可操作错误，state 不推进。"""
    src, consumer = env["src"], env["consumer"]
    old_agents = consumer / "AGENTS.md"
    old_agents.write_text(CONSUMER_AGENTS)

    monkeypatch.setattr("template_sync.apply._run_tests", lambda c: False)
    rc = cmd_apply(
        Namespace(sync=env["sync"], allow_dirty_source=True, decision=["AGENTS.md:update"], skip_tests=False, decisions={"AGENTS.md": "update"})
    )
    captured = capsys.readouterr()
    assert rc == 1
    # 已写入改动完整回滚
    assert old_agents.read_text() == CONSUMER_AGENTS
    assert not (consumer / ".repo_template/scripts/task.py").exists()
    # 备份保留（未被清理）
    assert (repo_state_dir(consumer) / "sync_transaction").exists()
    # changed 清单输出 + 可操作错误
    assert "本次写入清单" in captured.out
    assert ".repo_template/scripts/task.py" in captured.out
    assert "已完整回滚" in captured.err
    assert read_state(env["sync"])["last_synced_commit"] is None

def test_parse_decisions_rejects_duplicate_unknown_unit_and_disposition(env, capsys):
    with pytest.raises(ctx.SyncError, match="重复 unit"):
        _parse_decisions(["AGENTS.md:update", "AGENTS.md:keep"])
    with pytest.raises(ctx.SyncError, match="未知 unit"):
        _parse_decisions(["README.md:update"])
    with pytest.raises(ctx.SyncError, match="未知 disposition"):
        _parse_decisions(["AGENTS.md:overwrite"])
    assert _parse_decisions(["AGENTS.md:update", ".gitignore:keep"]) == {
        "AGENTS.md": "update",
        ".gitignore": "keep",
    }

    with pytest.raises(SystemExit) as exc:
        repo_sync_main(["apply", "--decision", "AGENTS.md:overwrite"])
    assert exc.value.code == 1
    assert "未知 disposition" in capsys.readouterr().err

def test_sync_file_rejects_directory_target(env):
    """目标同名目录：拒绝写入目录内部，保持目录内容原样。"""
    src, consumer = env["src"], env["consumer"]
    target = consumer / ".md_kx.toml"
    target.mkdir(parents=True)
    (target / "user_note.txt").write_text("keep\n", encoding="utf-8")

    with pytest.raises(ctx.SyncError, match="是目录"):
        sync_file(src / ".md_kx.toml", target, set(), env["sync"])
    assert (target / "user_note.txt").is_file()
    assert not (target / ".md_kx.toml").exists()

def test_sync_dir_rejects_file_target(env):
    """目标同名为文件：明确报错停止，不静默覆盖。"""
    src, consumer = env["src"], env["consumer"]
    target = consumer / ".repo_template" / "docs"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("i am a file\n", encoding="utf-8")

    with pytest.raises(ctx.SyncError, match="类型不符"):
        sync_dir(src / ".repo_template/docs", target, set(), env["sync"])
    assert target.read_text() == "i am a file\n"
