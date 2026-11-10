"""repo_sync state 字段原子更新、user_prompts 与硬同步删除。

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
    _task_branch, _write_public_registry, env, repo_state_file,
)


def test_sync_state_atomic_update_preserves_unknown_keys(env):
    state_file = env["state_file"]
    data = json.loads(state_file.read_text())
    data["future_field"] = {"x": 1}
    data["nested_unknown"] = ["a", "b"]
    state_file.write_text(json.dumps(data))

    write_state({**read_state(env["sync"]), "last_synced_at": "2026-01-01T00:00:00+08:00"}, env["sync"])
    reloaded = json.loads(state_file.read_text())
    assert reloaded["future_field"] == {"x": 1}
    assert reloaded["nested_unknown"] == ["a", "b"]
    assert reloaded["last_synced_at"] == "2026-01-01T00:00:00+08:00"
    assert "last_synced_commit" in reloaded

def test_prompt_add_supersede_revoke(env):
    _add_prompt(env["sync"], ".env 不要 ignore", ".gitignore,.env")
    assert len(read_state(env["sync"])["user_prompts"]) == 1
    # 同 tag 新条 supersede 旧条
    _add_prompt(env["sync"], "新版 .env 指令", ".env")
    state = read_state(env["sync"])
    assert len(state["user_prompts"]) == 2
    assert state["user_prompts"][0]["revoked"] is True
    assert state["user_prompts"][1]["revoked"] is False
    # 不同 tag 不 supersede
    _add_prompt(env["sync"], "MCP 保留", "mcp")
    state = read_state(env["sync"])
    assert state["user_prompts"][1]["revoked"] is False
    assert len(state["user_prompts"]) == 3
    # revoke
    cmd_prompt(Namespace(sync=env["sync"], action="revoke", text=None, tags=None, id=2))
    assert read_state(env["sync"])["user_prompts"][2]["revoked"] is True
    assert len(active_prompts(read_state(env["sync"]))) == 1

def test_prompt_substrings_only_active(env):
    _add_prompt(env["sync"], ".env 不要 ignore", ".gitignore,.env")
    _add_prompt(env["sync"], "MCP 保留", "mcp")
    assert set(prompt_substrings(read_state(env["sync"]))) == {".gitignore", ".env", "mcp"}
    cmd_prompt(Namespace(sync=env["sync"], action="revoke", text=None, tags=None, id=1))
    assert set(prompt_substrings(read_state(env["sync"]))) == {".gitignore", ".env"}

def test_prompt_does_not_persist_source_override(env, tmp_path):
    """来源覆盖收进 SyncContext 显式构造：覆盖仅作用于本次读取，不写盘。"""
    original = read_state(env["sync"])["template_source"]
    other = tmp_path / "other_src"
    (other / ".repo_template/scripts").mkdir(parents=True)
    (other / ".repo_template/scripts/task.py").write_text("print('other')\n")

    overridden = ctx.SyncContext.for_consumer(env["consumer"], source_override=str(other))
    assert read_state(overridden)["template_source"]["value"] == str(other.resolve())
    _add_prompt(overridden, "临时来源下的 prompt", ".gitignore")

    on_disk = json.loads(env["state_file"].read_text(encoding="utf-8"))
    assert on_disk["template_source"] == original
    assert "_original_template_source" not in on_disk
    assert read_state(env["sync"])["template_source"] == original

def test_prompt_refuses_ephemeral_source_without_persisted_template_source(env, tmp_path):
    state_file = env["state_file"]
    state_file.write_text(json.dumps({"last_synced_commit": None, "user_prompts": []}), encoding="utf-8")
    other = tmp_path / "other_src"
    (other / ".repo_template/scripts").mkdir(parents=True)
    (other / ".repo_template/scripts/task.py").write_text("print('other')\n", encoding="utf-8")

    overridden = ctx.SyncContext.for_consumer(env["consumer"], source_override=str(other))
    with pytest.raises(ctx.SyncError, match="state 未记录来源"):
        cmd_prompt(Namespace(sync=overridden, action="add", text="x", tags=None, id=None, scope="local"))

    on_disk = json.loads(state_file.read_text(encoding="utf-8"))
    assert "template_source" not in on_disk

def test_corrupt_sync_state_raises_sync_error_with_recovery_guidance(env):
    """验证 sync_state.json 损坏或格式错误时抛出 SyncError 并附带恢复指引（Issue #49）。"""
    consumer = env["consumer"]
    state_file = repo_state_file(consumer, "sync_state.json")

    # 1. 截断/非法 JSON
    state_file.write_text("{corrupt: json truncated...", encoding="utf-8")
    with pytest.raises(ctx.SyncError, match="损坏"):
        read_state(env["sync"])

    # 2. 非 JSON 对象（如 JSON 数组）
    state_file.write_text("[1, 2, 3]\n", encoding="utf-8")
    with pytest.raises(ctx.SyncError, match="格式错误.*预期为 JSON 对象"):
        read_state(env["sync"])

def test_hard_sync_override_and_delete(env):
    src, consumer = env["src"], env["consumer"]
    task = consumer / ".repo_template/scripts/task.py"
    task.parent.mkdir(parents=True)
    task.write_text("print('OLD')\n")
    extra = consumer / ".repo_template/tests/extra_old.py"
    extra.parent.mkdir(parents=True)
    extra.write_text("x = 1\n")

    changed: set[Path] = set()
    sync_dir(src / ".repo_template", consumer / ".repo_template", changed, env["sync"])

    assert task.read_text() == "print('task')\n"
    assert (consumer / ".repo_template/tests/test_x.py").exists()
    assert not extra.exists()
    assert task in changed
    assert extra in changed

def test_sync_file_delete_when_src_missing(env):
    src, consumer = env["src"], env["consumer"]
    dst = consumer / ".repo_template/docs/spike_report_template.md"
    dst.parent.mkdir(parents=True)
    dst.write_text("old\n")
    changed: set[Path] = set()
    sync_file(src / ".repo_template/docs/spike_report_template.md", dst, changed, env["sync"])
    assert dst.read_text() == "# report\n"
    # src 缺失 → 删 dst
    orphan = consumer / ".claude/hooks/merge_guard.py"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("x\n")
    changed = set()
    sync_file(src / ".claude/hooks/nonexistent_guard.py", orphan, changed, env["sync"])
    assert not orphan.exists()
    assert orphan in changed

def test_sync_dir_rejects_symlink_destination(env, tmp_path):
    """验证 sync_dir 目标为软链时拒绝同步，防止越界覆盖外部文件（Issue #43）。"""
    src, consumer = env["src"], env["consumer"]
    src_dir = src / ".repo_template/sub"
    src_dir.mkdir(parents=True)
    (src_dir / "file.txt").write_text("template data\n")

    # 外部哨兵目录
    sentinel = tmp_path / "sentinel_dir"
    sentinel.mkdir()
    (sentinel / "outside.txt").write_text("precious\n")

    # 消费仓内软链指向哨兵目录
    dst_link = consumer / ".repo_template/sub"
    dst_link.parent.mkdir(parents=True, exist_ok=True)
    dst_link.symlink_to(sentinel)

    changed = set()
    with pytest.raises(ctx.SyncError, match="目录软链.*拒绝"):
        sync_dir(src_dir, dst_link, changed, env["sync"])

    # 哨兵目录内容完好
    assert (sentinel / "outside.txt").read_text() == "precious\n"
    assert not (sentinel / "file.txt").exists()
