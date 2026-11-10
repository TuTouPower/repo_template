"""repo_sync install-hooks 设置与幂等。

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


def test_install_hooks_sets_and_idempotent(tmp_path, capsys):
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _git(consumer, "init", "-b", "main")
    _ensure_hook(consumer)
    sync = ctx.SyncContext.for_consumer(consumer)

    assert cmd_install_hooks(Namespace(sync=sync)) == 0
    out = capsys.readouterr().out
    assert "已设为" in out
    assert _git(consumer, "config", "--get", "core.hooksPath").stdout.strip() \
        == ".repo_template/hooks"

    # 幂等：已指向目标 → no-op
    assert cmd_install_hooks(Namespace(sync=sync)) == 0
    assert "无需改动" in capsys.readouterr().out

def test_install_hooks_uses_consumer_lock_for_both_paths(tmp_path, monkeypatch):
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _git(consumer, "init", "-b", "main")
    _ensure_hook(consumer)
    sync = ctx.SyncContext.for_consumer(consumer)
    calls = []

    def fake_lock(root, name):
        calls.append((root, name))
        return contextlib.nullcontext()

    monkeypatch.setattr("template_sync.common.git_common_lock", fake_lock)
    assert cmd_install_hooks(Namespace(sync=sync)) == 0
    assert [root for root, _ in calls] == [consumer]

def test_install_hooks_refuses_foreign_path(tmp_path, capsys):
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _git(consumer, "init", "-b", "main")
    _git(consumer, "config", "core.hooksPath", ".git/hooks")
    _ensure_hook(consumer)
    sync = ctx.SyncContext.for_consumer(consumer)

    assert cmd_install_hooks(Namespace(sync=sync)) == 0
    err = capsys.readouterr().err
    assert "不覆盖" in err
    assert _git(consumer, "config", "--get", "core.hooksPath").stdout.strip() \
        == ".git/hooks"

def test_install_hooks_force_overwrites(tmp_path, capsys):
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _git(consumer, "init", "-b", "main")
    _git(consumer, "config", "core.hooksPath", ".git/hooks")
    _ensure_hook(consumer)
    sync = ctx.SyncContext.for_consumer(consumer)

    assert cmd_install_hooks(Namespace(sync=sync, force=True)) == 0
    assert _git(consumer, "config", "--get", "core.hooksPath").stdout.strip() \
        == ".repo_template/hooks"

def test_install_hooks_nested_product_uses_toplevel_relative(tmp_path, capsys):
    """工厂仓：git 顶层在上、产物在 repo/ 时，hooksPath 须相对顶层。"""
    factory = tmp_path / "factory"
    product = factory / "repo"
    product.mkdir(parents=True)
    _git(factory, "init", "-b", "main")
    _ensure_hook(product)
    sync = ctx.SyncContext.for_consumer(product)

    assert cmd_install_hooks(Namespace(sync=sync)) == 0
    assert _git(factory, "config", "--get", "core.hooksPath").stdout.strip() \
        == "repo/.repo_template/hooks"
    assert "repo/.repo_template/hooks" in capsys.readouterr().out

def test_install_hooks_rejects_missing_or_nonexec(tmp_path, capsys):
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _git(consumer, "init", "-b", "main")
    sync = ctx.SyncContext.for_consumer(consumer)

    assert cmd_install_hooks(Namespace(sync=sync)) == 1
    assert "缺 hook 脚本" in capsys.readouterr().err

    _ensure_hook(consumer, executable=False)
    assert cmd_install_hooks(Namespace(sync=sync)) == 1
    assert "不可执行" in capsys.readouterr().err
    assert _git(consumer, "config", "--get", "core.hooksPath", check=False).stdout.strip() \
        == ""
