"""template_sync 升级链路集成测试（来源仓 + 消费仓物理分离）。

覆盖：
- P1-5：所有 helper/action 显式使用 --consumer 的 SyncContext，不回退来源脚本所在仓；
- P1-6：migrate all --write 在前序步骤已落盘后仍能完成 entity 步；
- P1-8：retired-hooks 事务根为目录，软链子项可快照；
- P1-9：apply/status/plan/link-skills 入口统一阻断旧布局 state；
- DS L235：settings.json 顶层非对象转 SyncError，不留 applying 事务；
- DS L238：status/plan 报告与 apply/migrate 同一可处理集合；
- DS L244：dry-run 末尾明确「未写盘」。
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from template_sync import context as ctx
from template_sync.apply import cmd_apply
from template_sync.hooks import (
    _RETIRED_MERGE_GUARD_COMMAND,
    retired_hooks_status,
)
from template_sync.migrate import cmd_migrate
from template_sync.skills_links import cmd_link_skills
from template_sync.status_plan import cmd_plan, cmd_status
from template_sync.workflow_migration import (
    force_migrate_workflow_tasks,
    workflow_migration_status,
)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=check,
    )


def _make_source(path: Path) -> Path:
    """最小来源产物树：PRODUCT_MARKER + 真实 task_template。"""
    path.mkdir(parents=True)
    (path / ".repo_template/scripts").mkdir(parents=True)
    (path / ".repo_template/scripts/task.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    template_dir = path / ".repo_template/docs/task_template"
    template_dir.mkdir(parents=True)
    real = SCRIPTS_DIR.parent / "docs/task_template"
    for name in ("task.md", "progress.md"):
        shutil.copy(real / name, template_dir / name)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Tester")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "init source")
    return path


def _make_consumer(path: Path, src: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Tester")
    state = path / ".repo_template/sync_state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({
        "template_source": {"kind": "path", "value": str(src)},
        "last_synced_commit": None,
        "last_synced_at": None,
        "user_prompts": [],
    }), encoding="utf-8")
    (path / ".gitignore").write_text(".scratch/\n", encoding="utf-8")
    (path / "README.md").write_text("# consumer\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "init consumer")
    return path


def _legacy_workflow_task(consumer: Path) -> Path:
    task_dir = consumer / "docs/tasks/t001_old"
    task_dir.mkdir(parents=True)
    (task_dir / "spec.md").write_text("# spec\n", encoding="utf-8")
    (task_dir / "task.md").write_text(
        '---\ntid: "t001"\nslug: "old"\nstatus: "blocked"\n---\n# Task 过程总账\n',
        encoding="utf-8",
    )
    return task_dir


def _retired_guard(consumer: Path) -> tuple[Path, Path]:
    retired = consumer / ".repo_template/hooks/merge_guard.py"
    retired.parent.mkdir(parents=True, exist_ok=True)
    retired.write_text("# guard\n", encoding="utf-8")
    guard = consumer / ".claude/hooks/merge_guard.py"
    guard.parent.mkdir(parents=True, exist_ok=True)
    guard.symlink_to("../../.repo_template/hooks/merge_guard.py")
    return guard, retired


def test_workflow_status_reads_consumer_even_when_script_repo_differs(tmp_path):
    """P1-5：helper 显式用 --consumer 的 sync，绝不回退来源脚本所在仓。"""
    src = _make_source(tmp_path / "source")
    consumer = _make_consumer(tmp_path / "consumer", src)
    _legacy_workflow_task(consumer)
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "legacy workflow")
    sync = ctx.SyncContext.for_consumer(consumer)

    # 显式 sync 即唯一真相源：helper 只读传入身份，不再回退模块级默认。
    # （历史 monkeypatch ctx.CONSUMER/STATE_PATH/SKILLS_SRC 已随模块级可变状态删除。）

    migrations, _, errors = workflow_migration_status(src, sync=sync)
    assert errors == []
    assert any("t001_old" in item for item in migrations)

    changed = set()
    force_migrate_workflow_tasks(
        changed,
        template_template_dir=src / ".repo_template/docs/task_template",
        sync=sync,
    )
    assert (consumer / "docs/tasks/t001_old/progress.md").is_file()
    assert not (src / "docs/tasks").exists()


def test_migrate_all_write_succeeds_with_entity_and_prior_writes(tmp_path, capsys):
    """P1-6：前序步骤改写已跟踪文件后工作区变脏，entity 步不得再报「工作区必须干净」。"""
    src = _make_source(tmp_path / "source")
    consumer = _make_consumer(tmp_path / "consumer", src)
    sync = ctx.SyncContext.for_consumer(consumer)

    conv = consumer / "docs/blueprint/conventions.md"
    conv.parent.mkdir(parents=True, exist_ok=True)
    conv.write_text("### 原子写\n- from task import _atomic_write_text 旧写法\n", encoding="utf-8")
    todo = consumer / "docs/pending/todo/p001_x.md"
    todo.parent.mkdir(parents=True, exist_ok=True)
    todo.write_text("# p001 x\n\n- 来源：测试\n", encoding="utf-8")
    (consumer / "docs/pending/todo/.gitkeep").write_text("", encoding="utf-8")
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "legacy layout")

    assert cmd_migrate(argparse.Namespace(kind="all", write=True, sync=sync)) == 0
    out = capsys.readouterr().out
    assert "迁移完成" in out
    assert "from repo_task.documents import atomic_write_text" in conv.read_text(encoding="utf-8")
    assert (consumer / "docs/pending/p001_x.md").is_file()
    assert not (consumer / "docs/pending/todo").exists()


def test_migrate_retired_hooks_uses_directory_transaction_root(tmp_path):
    """P1-8：事务根为 .claude/hooks 目录，软链子项可快照，迁移后 status 收敛。"""
    src = _make_source(tmp_path / "source")
    consumer = _make_consumer(tmp_path / "consumer", src)
    sync = ctx.SyncContext.for_consumer(consumer)

    guard, _ = _retired_guard(consumer)
    settings = consumer / ".claude/settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({
        "hooks": {"PreToolUse": [{
            "hooks": [{"type": "command", "command": _RETIRED_MERGE_GUARD_COMMAND}],
        }]},
    }), encoding="utf-8")
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "legacy hooks")

    assert retired_hooks_status(sync=sync)
    assert cmd_migrate(argparse.Namespace(kind="retired-hooks", write=True, sync=sync)) == 0
    assert not guard.is_symlink()
    assert retired_hooks_status(sync=sync) == []


def test_apply_status_plan_link_skills_block_legacy_state(tmp_path):
    """P1-9：四个写入口统一阻断旧布局 state，且绝不删除用户数据。"""
    src = _make_source(tmp_path / "source")
    consumer = _make_consumer(tmp_path / "consumer", src)
    sync = ctx.SyncContext.for_consumer(consumer)
    legacy = consumer / ".agents/skills/repo-template-sync/sync_state.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({
        "user_prompts": [{"text": "keep me"}],
        "last_synced_commit": "abc",
    }), encoding="utf-8")

    cases = [
        (cmd_status, argparse.Namespace(sync=sync, fetch=False)),
        (cmd_plan, argparse.Namespace(sync=sync, fetch=False)),
        (cmd_apply, argparse.Namespace(sync=sync, skip_tests=True, decisions={})),
        (cmd_link_skills, argparse.Namespace(sync=sync)),
    ]
    for cmd, args in cases:
        with pytest.raises(ctx.SyncError, match="migrate legacy-state"):
            cmd(args)
    assert legacy.is_file()
    assert json.loads(legacy.read_text(encoding="utf-8"))["last_synced_commit"] == "abc"


def test_retired_hooks_status_rejects_non_object_settings(tmp_path):
    """DS L235：settings 顶层非对象转 SyncError，不抛 AttributeError。"""
    src = _make_source(tmp_path / "source")
    consumer = _make_consumer(tmp_path / "consumer", src)
    sync = ctx.SyncContext.for_consumer(consumer)
    settings = consumer / ".claude/settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text("[1, 2, 3]\n", encoding="utf-8")

    with pytest.raises(ctx.SyncError, match="不能安全移除"):
        retired_hooks_status(sync=sync)
    assert settings.read_text(encoding="utf-8") == "[1, 2, 3]\n"


def test_plan_reports_entity_and_retired_hooks_backlog(tmp_path, capsys):
    """DS L238：status/plan 补齐 entity-storage 与 retired-hooks，且给出可执行命令。"""
    src = _make_source(tmp_path / "source")
    consumer = _make_consumer(tmp_path / "consumer", src)
    sync = ctx.SyncContext.for_consumer(consumer)
    todo = consumer / "docs/pending/todo/p001_x.md"
    todo.parent.mkdir(parents=True, exist_ok=True)
    todo.write_text("# p001 x\n", encoding="utf-8")
    _retired_guard(consumer)
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "legacy layout")

    assert cmd_plan(argparse.Namespace(sync=sync, fetch=False)) == 0
    out = capsys.readouterr().out
    assert "迁移待办" in out
    assert "entity-storage" in out
    assert "retired-hooks" in out
    assert "migrate entity-storage --write" in out


def test_migrate_dry_run_reports_not_written(tmp_path, capsys):
    """DS L244：dry-run 末尾明确「未写盘」，不误导为已落盘。"""
    src = _make_source(tmp_path / "source")
    consumer = _make_consumer(tmp_path / "consumer", src)
    sync = ctx.SyncContext.for_consumer(consumer)

    assert cmd_migrate(argparse.Namespace(kind="all", write=False, sync=sync)) == 0
    assert "未写盘" in capsys.readouterr().out
