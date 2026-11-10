"""sync 复修轮回归：registry 降级、异常回滚、口径统一、事务收敛、命令 pin、clean  scope、占位清理、血缘移除、prompt 加固、legacy 干层、超时包装。"""

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS_DIR = FACTORY_ROOT / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from template_sync import context as ctx
from template_sync.apply import cmd_apply
from template_sync.entity_storage_migration import (
    MigrationError,
    build_migration_plan,
    entity_storage_migration_status,
    migrate_entity_storage,
)
from template_sync.migrate import cmd_migrate, migrate_command
from template_sync.skills_links import cmd_link_skills, opencode_commands_status
from template_sync.state import cmd_prompt
from template_sync.status_plan import cmd_plan, cmd_status
from git_repo_support import state_file


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=check,
    )


def _pair(tmp_path: Path):
    src = tmp_path / "src"
    (src / ".repo_template/scripts").mkdir(parents=True)
    (src / ".repo_template/scripts/task.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    (src / ".repo_template/skills").mkdir(parents=True)
    _git(src, "init", "-b", "main")
    _git(src, "config", "user.email", "t@e.c")
    _git(src, "config", "user.name", "T")
    _git(src, "add", "-A")
    _git(src, "commit", "-m", "src")
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _git(consumer, "init", "-b", "main")
    _git(consumer, "config", "user.email", "t@e.c")
    _git(consumer, "config", "user.name", "T")
    (consumer / ".repo_template").mkdir(parents=True)
    (consumer / ".repo_template/entity_schema.json").write_text('{"version": 1}\n', encoding="utf-8")
    (state_file(consumer, "sync_state.json")).write_text(json.dumps({
        "template_source": {"kind": "path", "value": str(src)},
        "last_synced_commit": None, "last_synced_at": None, "user_prompts": [],
    }), encoding="utf-8")
    (consumer / ".gitignore").write_text(".scratch/\n", encoding="utf-8")
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "init")
    return src, consumer, ctx.SyncContext.for_consumer(consumer)


def test_status_plan_downgrade_missing_registry_to_blocked(tmp_path, capsys):
    src, consumer, sync = _pair(tmp_path)
    (consumer / ".repo_template/skills/a").mkdir(parents=True)
    (consumer / ".repo_template/skills/a/SKILL.md").write_text(
        "---\nname: a\ndescription: d\n---\nbody\n", encoding="utf-8")
    # status/plan 不抛错，展示 BLOCKED 待办
    assert cmd_status(argparse.Namespace(sync=sync, fetch=False)) == 0
    out = capsys.readouterr().out
    assert "BLOCKED" in out and "registry.json" in out
    assert cmd_plan(argparse.Namespace(sync=sync, fetch=False)) == 0
    out = capsys.readouterr().out
    assert "BLOCKED" in out and "补登记" in out
    # apply / link-skills 仍 fail-closed
    with pytest.raises(ctx.SyncError, match="registry.json 缺失"):
        opencode_commands_status(sync)
    with pytest.raises(ctx.SyncError, match="registry.json|未登记"):
        cmd_link_skills(argparse.Namespace(sync=sync))


def test_registry_malformed_entry_raises_sync_error_not_attribute_error(tmp_path):
    _, consumer, sync = _pair(tmp_path)
    (consumer / ".repo_template/skills/a").mkdir(parents=True)
    (consumer / ".repo_template/skills/a/SKILL.md").write_text(
        "---\nname: a\ndescription: d\n---\nbody\n", encoding="utf-8")
    (consumer / ".repo_template/skills/registry.json").write_text(
        json.dumps({"skills": {"a": "not-a-dict"}}), encoding="utf-8")
    with pytest.raises(ctx.SyncError, match="条目结构错误"):
        opencode_commands_status(sync)


def test_status_rejects_self_source(tmp_path):
    src, consumer, _ = _pair(tmp_path)
    # 让消费仓自身成为合法 product，再把来源指向自身，触发 assert_not_self
    (consumer / ".repo_template/scripts/task.py").parent.mkdir(parents=True, exist_ok=True)
    (consumer / ".repo_template/scripts/task.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    sync_self = ctx.SyncContext.for_consumer(consumer)
    # state 指向自身消费仓：status 须阻断而非成功输出
    (state_file(consumer, "sync_state.json")).write_text(json.dumps({
        "template_source": {"kind": "path", "value": str(consumer)},
        "last_synced_commit": None, "last_synced_at": None, "user_prompts": [],
    }), encoding="utf-8")
    with pytest.raises(ctx.SyncError, match="禁止在模板仓"):
        cmd_status(argparse.Namespace(sync=sync_self, fetch=False))


def test_state_malformed_template_source_is_sync_error(tmp_path):
    _, consumer, sync = _pair(tmp_path)
    (state_file(consumer, "sync_state.json")).write_text(json.dumps({
        "template_source": "not-a-dict",
        "last_synced_commit": None, "user_prompts": [],
    }), encoding="utf-8")
    with pytest.raises(ctx.SyncError, match="template_source 结构错误"):
        cmd_status(argparse.Namespace(sync=sync, fetch=False))


def test_pending_identity_mismatch_blocks_status_and_migrate(tmp_path):
    src, consumer, sync = _pair(tmp_path)
    bad = consumer / "docs/pending/todo/p001_x.md"
    bad.parent.mkdir(parents=True)
    bad.write_text('---\npid: "p002"\nslug: "x"\nstatus: "todo"\n---\n# hi\n', encoding="utf-8")
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "bad pid")
    problems = entity_storage_migration_status(sync=sync)
    assert any("身份与文件名矛盾" in p for p in problems)
    with pytest.raises(MigrationError, match="身份与文件名矛盾"):
        build_migration_plan(sync=sync)


def test_symlink_entity_reported_in_status(tmp_path):
    _, consumer, sync = _pair(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = consumer / "docs/spikes/s002_network"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside, target_is_directory=True)
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "symlink")
    problems = entity_storage_migration_status(sync=sync)
    assert any("软链" in p for p in problems)


def test_audit_target_conflict_status_matches_migrate(tmp_path):
    _, consumer, sync = _pair(tmp_path)
    (consumer / "docs/archive").mkdir(parents=True)
    (consumer / "docs/runtime").mkdir(parents=True)
    (consumer / "docs/archive/tasks_audit.log").write_bytes(b"log")
    (consumer / "docs/runtime/tasks_audit.log").write_bytes(b"log")
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "audit")
    problems = entity_storage_migration_status(sync=sync)
    assert any("目标占用" in p for p in problems)


def test_placeholder_only_dir_listed_for_cleanup(tmp_path):
    _, consumer, sync = _pair(tmp_path)
    d = consumer / "docs/archive/tasks"
    d.mkdir(parents=True)
    (d / ".gitkeep").write_text("", encoding="utf-8")
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "placeholder")
    problems = entity_storage_migration_status(sync=sync)
    assert any("仅含占位文件待清理" in p for p in problems)
    # 迁移须收敛该目录
    assert migrate_entity_storage(sync=sync, write=True) is not None
    assert not d.exists()
    assert entity_storage_migration_status(sync=sync) == []


def test_apply_transaction_scope_excludes_user_docs():
    import inspect

    from template_sync import apply as apply_mod

    src = inspect.getsource(apply_mod.cmd_apply)
    assert '"docs/tasks"' not in src
    assert '"docs/blueprint/conventions.md"' not in src
    assert '"docs/specs_index.md"' not in src
    assert '".claude/settings.json"' not in src
    assert '".claude/hooks"' not in src


def test_migrate_command_pins_source_checkout_with_quote(tmp_path):
    src, consumer, sync = _pair(tmp_path)
    cmd = migrate_command(src, sync, "entity-storage")
    assert "--consumer" in cmd and "--source" in cmd and "--checkout" in cmd
    assert shlex.quote(str(src)) in cmd
    assert shlex.quote(str(sync.consumer)) in cmd
    assert "migrate entity-storage --write" in cmd
    # 含空格路径仍可解析
    spaced = tmp_path / "with space"
    spaced.mkdir()
    sync2 = ctx.SyncContext.for_consumer(spaced)
    (spaced / ".repo_template").mkdir(parents=True)
    (state_file(spaced, "sync_state.json")).write_text(json.dumps({
        "template_source": {"kind": "path", "value": str(src)},
        "last_synced_commit": None, "last_synced_at": None, "user_prompts": [],
    }), encoding="utf-8")
    cmd2 = migrate_command(src, sync2, "all")
    assert shlex.quote(str(spaced)) in cmd2


def test_migrate_all_clean_only_for_entity(tmp_path, capsys):
    src, consumer, sync = _pair(tmp_path)
    conv = consumer / "docs/blueprint/conventions.md"
    conv.parent.mkdir(parents=True)
    conv.write_text("### 原子写\n- from task import _atomic_write_text 旧写法\n", encoding="utf-8")
    (consumer / "README.md").write_text("# consumer\n", encoding="utf-8")
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "legacy conv")
    # 脏工作区但 entity 无待迁移：all --write 不应被 clean 阻断
    (consumer / "README.md").write_text("dirty\n", encoding="utf-8")
    assert cmd_migrate(argparse.Namespace(kind="all", write=True, sync=sync)) == 0
    capsys.readouterr()
    # 脏工作区且 entity 有待迁移：须阻断
    _git(consumer, "checkout", "--", "README.md")
    todo = consumer / "docs/pending/todo/p001_x.md"
    todo.parent.mkdir(parents=True, exist_ok=True)
    todo.write_text("# p001\n", encoding="utf-8")
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "legacy entity")
    (consumer / "README.md").write_text("dirty2\n", encoding="utf-8")
    with pytest.raises(MigrationError, match="工作区必须干净"):
        cmd_migrate(argparse.Namespace(kind="all", write=True, sync=sync))


def test_missing_handoff_does_not_block_migrate_but_blocks_use(tmp_path):
    from repo_task.context import RepoContext
    from repo_task.integration import _dependency_implementations
    from repo_task.documents import parse_front_matter_text  # noqa: F401

    _, consumer, sync = _pair(tmp_path)
    t1 = consumer / "docs/archive/tasks/t001_alpha"
    t1.mkdir(parents=True)
    (t1 / "task.md").write_text("# t001\n", encoding="utf-8")
    (t1 / "progress.md").write_text(
        '---\ntid: "t001"\nslug: "alpha"\nstatus: "done"\n---\n# 进展\n', encoding="utf-8")
    # 无 handoff.json 的 done 目录
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "legacy done without handoff")
    migrate_entity_storage(sync=sync, write=True)
    assert (consumer / "docs/tasks/t001_alpha").is_dir()
    assert not (consumer / "docs/tasks/t001_alpha/handoff.json").exists()
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "migrated")
    with pytest.raises(Exception, match="缺依赖实现的可验证 SHA|handoff|实现"):
        _dependency_implementations("t001", repo=RepoContext.for_root(consumer))


def test_manifest_only_prompt_add_blocked(tmp_path):
    _, consumer, sync = _pair(tmp_path)
    (state_file(consumer, "sync_state.json")).unlink()
    (consumer / ".repo_template/sync_manifest.json").write_text(
        json.dumps({"version": 1, "resolved_commit": "abc"}), encoding="utf-8")
    with pytest.raises(ctx.SyncError, match="未记录来源"):
        cmd_prompt(argparse.Namespace(
            sync=sync, action="add", text="hi", tags="", id=None, scope="local"))
    assert not (state_file(consumer, "sync_state.json")).exists()


def test_migrate_all_dry_run_legacy_reports_and_skips_rest(tmp_path, capsys):
    _, consumer, sync = _pair(tmp_path)
    legacy = consumer / ".agents/skills/repo-template-sync/sync_state.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({
        "template_source": {"kind": "path", "value": str(consumer)},
        "last_synced_commit": "abc", "user_prompts": [],
    }), encoding="utf-8")
    assert cmd_migrate(argparse.Namespace(kind="all", write=False, sync=sync)) == 0
    out = capsys.readouterr().out
    assert "legacy-state" in out and "先单独执行" in out


def test_entity_git_timeout_wrapped(tmp_path, monkeypatch):
    import subprocess as sp

    _, consumer, sync = _pair(tmp_path)
    todo = consumer / "docs/pending/todo/p001_x.md"
    todo.parent.mkdir(parents=True, exist_ok=True)
    todo.write_text("# p001\n", encoding="utf-8")
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "legacy")
    mod = sys.modules["template_sync.entity_storage_migration"]

    def boom(*a, **k):
        raise sp.TimeoutExpired(cmd="git", timeout=30)

    monkeypatch.setattr("template_sync.common.subprocess.run", boom)
    with pytest.raises(ctx.SyncError, match="超时"):
        mod._check_clean_working_copy(consumer)
