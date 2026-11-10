"""repo_sync 初始化、legacy state 迁移与来源校验。

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


def test_assert_not_self(env):
    src = env["src"]
    # 无关路径 → 不拒绝
    other = env["consumer"].parent / "unrelated"
    assert_not_self(other, sync=env["sync"])
    # 以 src 为消费仓的显式身份 → 同一路径拒绝
    with pytest.raises(ctx.SyncError):
        assert_not_self(src, sync=ctx.SyncContext.for_consumer(src))

def test_init_writes_template_source(env):
    cmd_init(Namespace(sync=env["sync"], source=str(env["src"])))
    state = read_state(env["sync"])
    assert state["template_source"]["kind"] == "path"
    assert state["template_source"]["value"] == str(env["src"])

def test_init_refuses_legacy_state_and_requires_explicit_migration(env):
    """A11：只有旧布局 state 时普通 init 拒绝，并指向显式迁移入口。"""
    consumer = env["consumer"]
    legacy = consumer / ".agents/skills/repo-template-sync/sync_state.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({
        "template_source": {"kind": "path", "value": str(env["src"])},
        "last_synced_commit": "abc123",
        "user_prompts": [{"at": "2026-01-01T00:00:00+08:00", "text": "旧规则", "tags": []}],
    }), encoding="utf-8")
    env["state_file"].unlink()

    with pytest.raises(ctx.SyncError, match="migrate legacy-state"):
        cmd_init(Namespace(sync=env["sync"], source=str(env["src"])))
    assert legacy.is_file()
    assert not env["state_file"].exists()

def test_migrate_legacy_state_preserves_provenance_and_prompts(env):
    """A11：显式 migrate legacy-state 保留来源、同步记录与 prompts，并删除旧文件。"""
    consumer = env["consumer"]
    legacy = consumer / ".agents/skills/repo-template-sync/sync_state.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({
        "template_source": {"kind": "path", "value": str(env["src"])},
        "last_synced_commit": "abc123",
        "last_synced_at": "2026-01-01T00:00:00+08:00",
        "user_prompts": [{"at": "2026-01-01T00:00:00+08:00", "text": "旧规则", "tags": []}],
    }), encoding="utf-8")
    env["state_file"].unlink()

    from template_sync.migrate import cmd_migrate

    # dry-run
    assert cmd_migrate(Namespace(sync=env["sync"], kind="legacy-state", write=False)) == 0
    assert legacy.exists()
    assert not env["state_file"].exists()

    # write
    assert cmd_migrate(Namespace(sync=env["sync"], kind="legacy-state", write=True)) == 0
    assert not legacy.exists()
    data = json.loads(env["state_file"].read_text(encoding="utf-8"))
    assert data["last_synced_commit"] == "abc123"
    assert data["template_source"]["value"] == str(env["src"])
    assert any(p["text"] == "旧规则" for p in data["user_prompts"])

def test_migrate_legacy_state_rollback_after_write_is_byte_identical(env, monkeypatch):
    """写入后、删除旧文件前失败：事务回滚，新旧 state 均恢复原字节。"""
    consumer = env["consumer"]
    legacy = consumer / ".agents/skills/repo-template-sync/sync_state.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({
        "template_source": {"kind": "path", "value": str(env["src"])},
        "last_synced_commit": "abc123",
        "last_synced_at": "2026-01-01T00:00:00+08:00",
        "user_prompts": [{"at": "2026-01-01T00:00:00+08:00", "text": "旧规则", "tags": []}],
    }, indent=2), encoding="utf-8")
    state_before = env["state_file"].read_bytes()
    legacy_before = legacy.read_bytes()

    from template_sync import state as state_module
    from template_sync.migrate import cmd_migrate

    real_write = state_module.write_state

    def write_then_fail(data, sync):
        real_write(data, sync)
        raise OSError("injected failure before legacy unlink")

    monkeypatch.setattr(state_module, "write_state", write_then_fail)
    with pytest.raises(ctx.SyncError, match="已回滚"):
        cmd_migrate(Namespace(sync=env["sync"], kind="legacy-state", write=True))

    assert env["state_file"].read_bytes() == state_before
    assert legacy.read_bytes() == legacy_before

def test_migrate_legacy_state_success_readback_matches_merged_state(env):
    """迁移成功后回读新 state 与合并预期逐字段一致，事务接受且备份保留。"""
    consumer = env["consumer"]
    legacy = consumer / ".agents/skills/repo-template-sync/sync_state.json"
    legacy.parent.mkdir(parents=True)
    prompt = {"at": "2026-01-01T00:00:00+08:00", "text": "旧规则", "tags": ["lang"]}
    legacy.write_text(json.dumps({
        "template_source": {"kind": "path", "value": "/legacy/source"},
        "last_synced_commit": "abc123",
        "last_synced_at": "2026-01-01T00:00:00+08:00",
        "user_prompts": [prompt],
    }, indent=2), encoding="utf-8")

    from template_sync.migrate import cmd_migrate

    assert cmd_migrate(Namespace(sync=env["sync"], kind="legacy-state", write=True)) == 0
    assert not legacy.exists()
    data = json.loads(env["state_file"].read_text(encoding="utf-8"))
    assert data == {
        "template_source": {"kind": "path", "value": str(env["src"])},
        "last_synced_commit": "abc123",
        "last_synced_at": "2026-01-01T00:00:00+08:00",
        "user_prompts": [prompt],
    }

def test_file_transaction_refuses_orphan_backup_without_journal(env):
    """缺少 journal 的非空事务目录不得被下一次 begin 直接删除。"""
    consumer = env["consumer"]
    from repo_task.file_transaction import SYNC_TRANSACTION_DIR, state_transaction

    transaction = state_transaction(consumer, SYNC_TRANSACTION_DIR)
    transaction.backup.mkdir(parents=True, exist_ok=True)
    leftover = transaction.backup / "leftover.txt"
    leftover.write_text("backup\n", encoding="utf-8")

    with pytest.raises(ValueError, match="缺少 journal"):
        transaction.begin(["README.md"])

    assert leftover.is_file()

def test_resolve_src_rejects_invalid(env):
    state = read_state(env["sync"])
    state["template_source"]["value"] = "/nonexistent/not_a_template"
    with pytest.raises(ctx.SyncError):
        resolve_src(state, sync=env["sync"])

def test_source_identity_rejects_option_like_string():
    """验证 source 参数为 option-like 格式时被明确拒绝（Issue #40）。"""
    with pytest.raises(ctx.SyncError, match="option 格式参数"):
        source_identity("--upload-pack")
    with pytest.raises(ctx.SyncError, match="option 格式参数"):
        source_identity("-o")
    with pytest.raises(ctx.SyncError, match="不能为空"):
        source_identity("")

def test_init_rejects_non_template_local_directory(env, tmp_path):
    """验证 init 传入非模板的普通目录时拒绝并保留原有有效 state（Issue #23）。"""
    not_a_template = tmp_path / "empty_dir"
    not_a_template.mkdir()

    orig_state = read_state(env["sync"])
    with pytest.raises(ctx.SyncError, match="不是有效模板源"):
        source_identity(str(not_a_template))

    # 验证原有 state 中的 template_source 保持不变
    assert read_state(env["sync"])["template_source"] == orig_state["template_source"]
