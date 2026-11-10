"""sync_deep 第二轮补修回归：registry fail-closed、显式 SyncContext、零模块级状态。

覆盖 DS L245（registry 缺失 fail-open）与 DS L246（模块级 override 与
``current_sync`` 回退消除）的新语义：
- skills/registry.json 缺失或结构非法时，命令薄层状态与生成一律 fail-closed；
- 全部 helper/action 必须显式传入 SyncContext，无默认回退；
- context 模块不再暴露任何可变同步身份。
"""

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS_DIR = FACTORY_ROOT / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from template_sync import context as ctx
from template_sync.apply import cmd_apply
from template_sync.migrate import cmd_migrate
from template_sync.skills_links import opencode_commands_status, sync_opencode_commands
from template_sync.source import resolve_src
from template_sync.status_plan import cmd_plan, cmd_status
from git_repo_support import state_file


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )


@pytest.fixture
def pair(tmp_path: Path) -> dict:
    """最小消费仓 + 来源产物：均为 git 仓，state 指向来源。"""
    src = tmp_path / "src"
    (src / ".repo_template/scripts").mkdir(parents=True)
    (src / ".repo_template/scripts/task.py").write_text("print('task')\n", encoding="utf-8")
    (src / ".repo_template/skills").mkdir(parents=True)
    _git(src, "init", "-b", "main")
    _git(src, "add", "-A")
    _git(src, "commit", "-m", "src")
    consumer = tmp_path / "consumer"
    (consumer / ".repo_template").mkdir(parents=True)
    (state_file(consumer, "sync_state.json")).write_text(json.dumps({
        "template_source": {"kind": "path", "value": str(src)},
        "last_synced_commit": None,
        "last_synced_at": None,
        "user_prompts": [],
    }), encoding="utf-8")
    _git(consumer, "init", "-b", "main")
    sync = ctx.SyncContext.for_consumer(consumer)
    return {"src": src, "consumer": consumer, "sync": sync}


def _skill(consumer: Path, name: str) -> None:
    d = consumer / ".repo_template/skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} desc\n---\nbody\n", encoding="utf-8"
    )


def test_missing_registry_fails_closed(pair):
    """registry 缺失不再放行全部 skill：状态与生成均 fail-closed。"""
    sync, consumer = pair["sync"], pair["consumer"]
    _skill(consumer, "task-run")
    with pytest.raises(ctx.SyncError, match="registry.json 缺失"):
        opencode_commands_status(sync)
    with pytest.raises(ctx.SyncError, match="registry.json 缺失"):
        sync_opencode_commands(set(), sync)


def test_malformed_registry_fails_closed(pair):
    """registry 结构非法（skills 非对象）同样 fail-closed。"""
    sync, consumer = pair["sync"], pair["consumer"]
    _skill(consumer, "task-run")
    (consumer / ".repo_template/skills/registry.json").write_text(
        json.dumps({"skills": ["task-run"]}), encoding="utf-8"
    )
    with pytest.raises(ctx.SyncError, match="结构错误"):
        opencode_commands_status(sync)


def test_private_role_skill_generates_no_command(pair):
    """非 public 角色不生成命令：fail-closed 只放行登记为 public 的 skill。"""
    sync, consumer = pair["sync"], pair["consumer"]
    _skill(consumer, "internal-tool")
    (consumer / ".repo_template/skills/registry.json").write_text(
        json.dumps({"skills": {"internal-tool": {"role": "internal"}}}), encoding="utf-8"
    )
    assert opencode_commands_status(sync) == []
    changed: set[Path] = set()
    assert sync_opencode_commands(changed, sync) == []
    assert changed == set()
    assert not (consumer / ".opencode/commands/internal-tool.md").exists()


def test_cmd_without_sync_is_rejected(pair):
    """cmd_* 无 args.sync 时拒绝执行，不回退任何默认身份。"""
    with pytest.raises(ctx.SyncError, match="显式 SyncContext"):
        cmd_status(Namespace())
    with pytest.raises(ctx.SyncError, match="显式 SyncContext"):
        cmd_plan(Namespace())
    with pytest.raises(ctx.SyncError, match="显式 SyncContext"):
        cmd_migrate(Namespace(kind="all", write=False))


def test_helper_without_sync_is_a_type_error(pair):
    """helper 的 sync 为必填位置参数：缺失即 TypeError，无静默回退。"""
    with pytest.raises(TypeError):
        opencode_commands_status()
    with pytest.raises(TypeError):
        sync_opencode_commands(set())


def test_no_module_level_sync_state():
    """模块级可变同步身份彻底消除：无 current_sync、无 override、无派生路径。"""
    for name in (
        "current_sync", "CONSUMER", "STATE_PATH", "SKILLS_SRC", "SKILLS_AGENTS",
        "SKILLS_CLAUDE", "COMMANDS_OPENCODE", "CONVENTIONS_PATH",
        "SOURCE_OVERRIDE", "CHECKOUT_OVERRIDE", "_rel",
    ):
        assert not hasattr(ctx, name), f"ctx.{name} 残留"


def test_overrides_live_in_sync_context(pair, tmp_path):
    """覆盖语义收进 SyncContext：checkout 覆盖改变解析产物。"""
    sync = pair["sync"]
    other = tmp_path / "other"
    (other / ".repo_template/scripts").mkdir(parents=True)
    (other / ".repo_template/scripts/task.py").write_text("print('other')\n", encoding="utf-8")
    pinned = ctx.SyncContext.for_consumer(pair["consumer"], checkout_override=other)
    assert resolve_src(
        {"template_source": {"kind": "path", "value": str(pair["src"])}},
        sync=pinned,
    ) == other.resolve()
    assert cmd_apply is not None and cmd_status is not None
