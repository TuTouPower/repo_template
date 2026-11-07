"""repo_sync.py 机械化同步器的 real-git 测试。

SRC（模板）与 CONSUMER（消费项目）都在 tmp_path 下构造真实目录，monkeypatch
模块级路径常量（CONSUMER / STATE_PATH / SKILLS_AGENTS / SKILLS_CLAUDE）重绑定，
覆盖 state 字段级原子更新、user_prompts 管理、硬同步覆盖与多余删除、skill 覆盖
与 sync_state.json 保护、软链、.gitignore / .prettierignore / MCP 机械合并、apply 全流程与改动清单。
"""

import contextlib
import json
import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from template_sync import context as ctx
from template_sync.agents_merge import _apply_agents_sectioned, _replace_agents_force, agents_section_status
from template_sync.apply import _assert_distributable_tree, cmd_apply
from template_sync.assets import retired_template_warnings, sync_dir, sync_file
from template_sync.cli import _parse_decisions, main as repo_sync_main
from template_sync.common import _detect_json_indent, _git as sync_git
from template_sync.conventions_migration import conventions_migration_status, migrate_conventions
from template_sync.gates import _run_tests
from template_sync.specs_index_migration import (
    migrate_specs_index,
    specs_index_migration_status,
    specs_index_report,
)
from template_sync.hooks import _RETIRED_MERGE_GUARD_COMMAND, cmd_install_hooks
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


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=check,
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-b", "main")
    _git(src, "config", "user.email", "test@example.com")
    _git(src, "config", "user.name", "test")

    toolkit = src / ".repo_template"
    (toolkit / "scripts").mkdir(parents=True)
    (toolkit / "scripts/task.py").write_text("print('task')\n")
    (toolkit / "tests").mkdir(parents=True)
    (toolkit / "tests/test_x.py").write_text("def test_x():\n    pass\n")
    (toolkit / "docs/task_template").mkdir(parents=True)
    (toolkit / "docs/task_template/task.md").write_text("# task\n")
    (toolkit / "docs/task_template/progress.md").write_text("---\ntid: t000\n---\n## 实施笔记\n无\n")
    (toolkit / "docs/review_prompts").mkdir(parents=True)
    (toolkit / "docs/review_prompts/general_prompt.txt").write_text("general\n")
    (toolkit / "docs/spike_report_template.md").write_text("# report\n")
    (toolkit / "docs/architecture.md").write_text("# arch\n")
    (toolkit / "hooks").mkdir(parents=True)
    hook = toolkit / "hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o755)
    (toolkit / "skills/task-run").mkdir(parents=True)
    (toolkit / "skills/task-run/SKILL.md").write_text(
        "---\nname: task-run\ndescription: none\ndisable-model-invocation: true\n---\nrun\n"
    )
    (src / ".md_kx.toml").write_text("table_mode = \"compact\"\n", encoding="utf-8")
    (src / "AGENTS.md").write_text("SRC AGENTS\n")
    (src / ".gitignore").write_text("node_modules/\n*.log\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-m", "init")
    head = _git(src, "rev-parse", "HEAD").stdout.strip()

    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _git(consumer, "init", "-b", "main")
    configure_project_verification(consumer)
    state_file = consumer / ".repo_template/sync_state.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(json.dumps({
        "template_source": {"kind": "path", "value": str(src)},
        "last_synced_commit": None,
        "last_synced_at": None,
        "user_prompts": [],
    }, indent=2))

    monkeypatch.setattr(ctx, "CONVENTIONS_PATH", consumer / "docs/blueprint/conventions.md")
    monkeypatch.setattr(ctx, "SOURCE_OVERRIDE", None)
    monkeypatch.setattr(ctx, "CHECKOUT_OVERRIDE", None)
    monkeypatch.setattr(ctx, "CONSUMER", consumer)
    monkeypatch.setattr(ctx, "STATE_PATH", state_file)
    monkeypatch.setattr(ctx, "SKILLS_SRC", consumer / ".repo_template/skills")
    monkeypatch.setattr(ctx, "SKILLS_AGENTS", consumer / ".agents/skills")
    monkeypatch.setattr(ctx, "SKILLS_CLAUDE", consumer / ".claude/skills")
    monkeypatch.setattr(ctx, "COMMANDS_OPENCODE", consumer / ".opencode/commands")
    return {"src": src, "consumer": consumer, "head": head, "state_file": state_file}


def _add_prompt(text: str, tags: str = "") -> None:
    cmd_prompt(Namespace(action="add", text=text, tags=tags, id=None))


# ---------------------------------------------------------------------------
# state 字段级原子更新
# ---------------------------------------------------------------------------

def test_sync_state_atomic_update_preserves_unknown_keys(env):
    state_file = env["state_file"]
    data = json.loads(state_file.read_text())
    data["future_field"] = {"x": 1}
    data["nested_unknown"] = ["a", "b"]
    state_file.write_text(json.dumps(data))

    write_state({**read_state(), "last_synced_at": "2026-01-01T00:00:00+08:00"})
    reloaded = json.loads(state_file.read_text())
    assert reloaded["future_field"] == {"x": 1}
    assert reloaded["nested_unknown"] == ["a", "b"]
    assert reloaded["last_synced_at"] == "2026-01-01T00:00:00+08:00"
    assert "last_synced_commit" in reloaded


# ---------------------------------------------------------------------------
# user_prompts 管理
# ---------------------------------------------------------------------------

def test_prompt_add_supersede_revoke(env):
    _add_prompt(".env 不要 ignore", ".gitignore,.env")
    assert len(read_state()["user_prompts"]) == 1
    # 同 tag 新条 supersede 旧条
    _add_prompt("新版 .env 指令", ".env")
    state = read_state()
    assert len(state["user_prompts"]) == 2
    assert state["user_prompts"][0]["revoked"] is True
    assert state["user_prompts"][1]["revoked"] is False
    # 不同 tag 不 supersede
    _add_prompt("MCP 保留", "mcp")
    state = read_state()
    assert state["user_prompts"][1]["revoked"] is False
    assert len(state["user_prompts"]) == 3
    # revoke
    cmd_prompt(Namespace(action="revoke", text=None, tags=None, id=2))
    assert read_state()["user_prompts"][2]["revoked"] is True
    assert len(active_prompts(read_state())) == 1


def test_prompt_substrings_only_active(env):
    _add_prompt(".env 不要 ignore", ".gitignore,.env")
    _add_prompt("MCP 保留", "mcp")
    assert set(prompt_substrings(read_state())) == {".gitignore", ".env", "mcp"}
    cmd_prompt(Namespace(action="revoke", text=None, tags=None, id=1))
    assert set(prompt_substrings(read_state())) == {".gitignore", ".env"}


def test_prompt_does_not_persist_source_override(env, tmp_path, monkeypatch):
    original = read_state()["template_source"]
    other = tmp_path / "other_src"
    (other / ".repo_template/scripts").mkdir(parents=True)
    (other / ".repo_template/scripts/task.py").write_text("print('other')\n")

    monkeypatch.setattr(ctx, "SOURCE_OVERRIDE", str(other))
    assert read_state()["template_source"]["value"] == str(other.resolve())
    _add_prompt("临时来源下的 prompt", ".gitignore")

    on_disk = json.loads(env["state_file"].read_text(encoding="utf-8"))
    assert on_disk["template_source"] == original
    assert "_original_template_source" not in on_disk
    monkeypatch.setattr(ctx, "SOURCE_OVERRIDE", None)
    assert read_state()["template_source"] == original


def test_prompt_refuses_ephemeral_source_without_persisted_template_source(env, tmp_path, monkeypatch):
    state_file = env["state_file"]
    state_file.write_text(json.dumps({"last_synced_commit": None, "user_prompts": []}), encoding="utf-8")
    other = tmp_path / "other_src"
    (other / ".repo_template/scripts").mkdir(parents=True)
    (other / ".repo_template/scripts/task.py").write_text("print('other')\n", encoding="utf-8")

    monkeypatch.setattr(ctx, "SOURCE_OVERRIDE", str(other))
    with pytest.raises(ctx.SyncError, match="state 未记录来源"):
        cmd_prompt(Namespace(action="add", text="x", tags=None, id=None, scope="local"))

    on_disk = json.loads(state_file.read_text(encoding="utf-8"))
    assert "template_source" not in on_disk


def test_corrupt_sync_state_raises_sync_error_with_recovery_guidance(env):
    """验证 sync_state.json 损坏或格式错误时抛出 SyncError 并附带恢复指引（Issue #49）。"""
    consumer = env["consumer"]
    state_file = consumer / ".repo_template/sync_state.json"

    # 1. 截断/非法 JSON
    state_file.write_text("{corrupt: json truncated...", encoding="utf-8")
    with pytest.raises(ctx.SyncError, match="损坏"):
        read_state()

    # 2. 非 JSON 对象（如 JSON 数组）
    state_file.write_text("[1, 2, 3]\n", encoding="utf-8")
    with pytest.raises(ctx.SyncError, match="格式错误.*预期为 JSON 对象"):
        read_state()


# ---------------------------------------------------------------------------
# 硬同步覆盖与多余删除
# ---------------------------------------------------------------------------

def test_hard_sync_override_and_delete(env):
    src, consumer = env["src"], env["consumer"]
    task = consumer / ".repo_template/scripts/task.py"
    task.parent.mkdir(parents=True)
    task.write_text("print('OLD')\n")
    extra = consumer / ".repo_template/tests/extra_old.py"
    extra.parent.mkdir(parents=True)
    extra.write_text("x = 1\n")

    changed: set[Path] = set()
    sync_dir(src / ".repo_template", consumer / ".repo_template", changed)

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
    sync_file(src / ".repo_template/docs/spike_report_template.md", dst, changed)
    assert dst.read_text() == "# report\n"
    # src 缺失 → 删 dst
    orphan = consumer / ".claude/hooks/merge_guard.py"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("x\n")
    changed = set()
    sync_file(src / ".claude/hooks/nonexistent_guard.py", orphan, changed)
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
        sync_dir(src_dir, dst_link, changed)

    # 哨兵目录内容完好
    assert (sentinel / "outside.txt").read_text() == "precious\n"
    assert not (sentinel / "file.txt").exists()



# ---------------------------------------------------------------------------
# 软链
# ---------------------------------------------------------------------------

def test_repair_symlinks(env):
    src, consumer = env["src"], env["consumer"]
    dst = consumer / ".repo_template/skills/task-run"
    dst.mkdir(parents=True)
    (dst / "SKILL.md").write_text("run\n")
    changed: set[Path] = set()
    reports = repair_symlinks(changed)
    link = consumer / ".claude/skills/task-run"
    assert link.is_symlink()
    assert link.resolve() == dst.resolve()
    agents_link = consumer / ".agents/skills/task-run"
    assert agents_link.is_symlink()
    assert agents_link.resolve() == dst.resolve()
    assert not reports
    changed = set()
    repair_symlinks(changed)
    assert not changed
    foreign_src = consumer / ".repo_template/skills/foreign-skill"
    foreign_src.mkdir(parents=True)
    foreign = consumer / ".claude/skills/foreign-skill"
    foreign.write_text("not a symlink\n")
    changed = set()
    reports = repair_symlinks(changed)
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
    reports = repair_symlinks(changed)

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
    repair_symlinks(changed)
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
    status = symlink_status()
    for rel in (".claude/skills/active-skill", ".agents/skills/active-skill"):
        assert {"name": "active-skill", "rel": rel, "state": "ok"} in status
    for rel in (".claude/skills/old-skill", ".agents/skills/old-skill"):
        assert {"name": "old-skill", "rel": rel, "state": "cleanup"} in status

    # 执行 repair_symlinks
    changed = set()
    reports = repair_symlinks(changed)

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
    repair_symlinks(set())

    agents_link = consumer / ".agents/skills/task-run"
    agents_link.unlink()
    status = symlink_status()
    assert {"name": "task-run", "rel": ".agents/skills/task-run", "state": "repair"} in status
    assert {"name": "task-run", "rel": ".claude/skills/task-run", "state": "ok"} in status

    agents_link.symlink_to("../../.repo_template/skills/missing")
    status = symlink_status()
    assert {"name": "task-run", "rel": ".agents/skills/task-run", "state": "repair"} in status

    assert cmd_plan(Namespace()) == 0
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

    items = skill_status(src)
    assert {"name": "task-run", "action": "stale"} in items


def test_sync_opencode_commands(env):
    src, consumer = env["src"], env["consumer"]
    dst = consumer / ".repo_template/skills/task-run"
    dst.mkdir(parents=True)
    (dst / "SKILL.md").write_text("---\nname: task-run\ndescription: Run tasks\n---\nrun\n")
    nodesc = consumer / ".repo_template/skills/nodesc"
    nodesc.mkdir(parents=True)
    (nodesc / "SKILL.md").write_text("---\nname: nodesc\ndescription: none\n---\nrun\n")

    changed: set[Path] = set()
    reports = sync_opencode_commands(changed)
    assert not reports
    cmd = consumer / ".opencode/commands/task-run.md"
    assert cmd.is_file()
    text = cmd.read_text()
    assert 'description: "Run tasks"' in text
    assert "`task-run`" in text and "skill" in text and "$ARGUMENTS" in text
    fallback = (consumer / ".opencode/commands/nodesc.md").read_text()
    assert 'description: "Run nodesc skill"' in fallback
    assert {cmd, consumer / ".opencode/commands/nodesc.md"} <= changed

    # 幂等
    changed = set()
    assert sync_opencode_commands(changed) == []
    assert not changed

    # 带标记残留删、手写保留、手写同名跳过并报告
    (consumer / ".opencode/commands/old-x.md").write_text(
        "---\ndescription: x\n---\n> Auto-generated by repo_sync.py from skill 'old-x'.\n"
    )
    (consumer / ".opencode/commands/mine.md").write_text("---\ndescription: mine\n---\ncustom\n")
    (consumer / ".opencode/commands/task-run.md").write_text("---\ndescription: mine-takeover\n---\ncustom\n")
    changed = set()
    reports = sync_opencode_commands(changed)
    assert not (consumer / ".opencode/commands/old-x.md").exists()
    assert (consumer / ".opencode/commands/mine.md").read_text().endswith("custom\n")
    assert (consumer / ".opencode/commands/task-run.md").read_text().endswith("custom\n")
    assert any("task-run" in r for r in reports)


def test_opencode_commands_status_detects_description_drift(env, capsys):
    """验证 skill 文案修改后 status/plan 能检出 opencode 命令薄层漂移（Issue #67）。"""
    src, consumer = env["src"], env["consumer"]
    skill_dir = consumer / ".repo_template/skills/task-sample"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: task-sample\ndescription: Original description\n---\nbody\n")

    # 1. 首次状态应为 create
    items = opencode_commands_status()
    sample_item = next(it for it in items if it["name"] == "task-sample")
    assert sample_item["state"] == "create"

    # 2. 生成命令薄层后状态应为 same
    changed = set()
    sync_opencode_commands(changed)
    items = opencode_commands_status()
    sample_item = next(it for it in items if it["name"] == "task-sample")
    assert sample_item["state"] == "same"

    # 3. 仅修改 skill description，不执行 link/sync
    (skill_dir / "SKILL.md").write_text("---\nname: task-sample\ndescription: Updated new description\n---\nbody\n")

    # 4. opencode_commands_status 必须检出 update 漂移
    items = opencode_commands_status()
    sample_item = next(it for it in items if it["name"] == "task-sample")
    assert sample_item["state"] == "update"
    assert "漂移" in sample_item["desc"]

    # 5. cmd_status 能打印提示
    cmd_status(Namespace())
    out = capsys.readouterr().out
    assert "opencode 命令薄层" in out
    assert "update" in out and "/task-sample" in out

    # 6. sync 之后恢复一致
    changed = set()
    sync_opencode_commands(changed)
    items = opencode_commands_status()
    sample_item = next(it for it in items if it["name"] == "task-sample")
    assert sample_item["state"] == "same"


def test_status_and_plan_agree_on_unmanaged_opencode_commands(env, capsys):
    consumer = env["consumer"]
    skill_dir = consumer / ".repo_template/skills/task-sample"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: task-sample\ndescription: sample\n---\nbody\n", encoding="utf-8"
    )
    command = consumer / ".opencode/commands/task-sample.md"
    command.parent.mkdir(parents=True)
    command.write_text("---\ndescription: hand written\n---\ncustom\n", encoding="utf-8")

    assert cmd_status(Namespace()) == 0
    status_out = capsys.readouterr().out.split("opencode 命令薄层", 1)[1]
    assert "/task-sample" not in status_out

    assert cmd_plan(Namespace()) == 0
    plan_out = capsys.readouterr().out
    assert "齐全一致" in plan_out


def test_shipped_skills_have_real_descriptions():
    """模板分发的每个 skill 必须有真实 description（命令文案与按描述路由依赖）。"""
    skills_dir = Path(__file__).resolve().parents[1] / "repo/.repo_template/skills"
    missing: list[str] = []
    for skill in sorted(skills_dir.iterdir()):
        if not skill.is_dir():
            continue
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        desc = ""
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                for line in text[3:end].splitlines():
                    if line.startswith("description:"):
                        desc = line.split(":", 1)[1].strip()
        if not desc or desc.lower() == "none":
            missing.append(skill.name)
    assert missing == []


def test_gitignore_merge_with_prompt_block(env):
    src, consumer = env["src"], env["consumer"]
    gi = consumer / ".gitignore"
    gi.write_text("node_modules/\n.env\n")
    _add_prompt(".env 不要 ignore", ".gitignore,.env")

    changed: set[Path] = set()
    info = merge_gitignore(src, prompt_substrings(read_state()), changed)
    text = gi.read_text()
    assert "node_modules/" in text              # 消费独有保留
    assert ".env" not in text.splitlines()      # prompt 禁止 ignore 行被删
    assert "*.log" in text                      # 模板独有追加
    assert info["added"] == ["*.log"]
    assert info["removed"] == [".env"]
    assert gi in changed


def test_gitignore_merge_keeps_negation_rules(env):
    src, consumer = env["src"], env["consumer"]
    (src / ".gitignore").write_text(
        "node_modules/\n*.log\n!config/.env.example\n", encoding="utf-8"
    )
    gi = consumer / ".gitignore"
    gi.write_text("node_modules/\n.env\n!.env.example\n", encoding="utf-8")

    changed: set[Path] = set()
    info = merge_gitignore(src, [".env"], changed)

    lines = gi.read_text(encoding="utf-8").splitlines()
    assert "!.env.example" in lines
    assert "!config/.env.example" in lines
    assert ".env" not in lines
    assert info["removed"] == [".env"]
    assert "!config/.env.example" in info["added"]


def test_prettierignore_merge_creates_and_dedupes(env):
    consumer = env["consumer"]
    pi = consumer / ".prettierignore"

    changed: set[Path] = set()
    info = merge_prettierignore(changed)
    text = pi.read_text()
    for rule in ctx.PRETTIERIGNORE_TEMPLATE_RULES:
        assert rule in text.splitlines()
    assert pi in changed

    # 二次合并：去重，无改动
    changed = set()
    info = merge_prettierignore(changed)
    assert info["added"] == []
    assert changed == set()
    assert text == pi.read_text()


def test_prettierignore_preserves_consumer_rules(env):
    consumer = env["consumer"]
    pi = consumer / ".prettierignore"
    pi.write_text("pnpm-lock.yaml\n*.md\n")

    changed: set[Path] = set()
    merge_prettierignore(changed)
    lines = pi.read_text().splitlines()
    assert "pnpm-lock.yaml" in lines             # 消费独有保留
    assert "*.md" in lines
    for rule in ctx.PRETTIERIGNORE_TEMPLATE_RULES:
        assert rule in lines
    assert pi in changed


def test_prettierignore_old_header_matches_stripped_line(env):
    pi = env["consumer"] / ".prettierignore"
    pi.write_text("  " + _PRETTIERIGNORE_OLD_HEADER + "  \n", encoding="utf-8")

    merge_prettierignore(set())

    lines = pi.read_text(encoding="utf-8").splitlines()
    stripped = [line.strip() for line in lines]
    assert _PRETTIERIGNORE_OLD_HEADER not in stripped
    assert stripped.count(_PRETTIERIGNORE_HEADER[0]) == 1
    for rule in ctx.PRETTIERIGNORE_TEMPLATE_RULES:
        assert rule in lines


def test_prettierignore_new_header_presence_matches_stripped_line(env):
    pi = env["consumer"] / ".prettierignore"
    pi.write_text("  " + _PRETTIERIGNORE_HEADER[0] + "  \n", encoding="utf-8")

    merge_prettierignore(set())

    lines = pi.read_text(encoding="utf-8").splitlines()
    assert sum(1 for line in lines if line.strip() == _PRETTIERIGNORE_HEADER[0]) == 1
    for rule in ctx.PRETTIERIGNORE_TEMPLATE_RULES:
        assert rule in lines


def test_prettierignore_covers_generated_artifacts(env):
    # 同步状态与任务产物需豁免；派生索引已退出 Git，不再列出
    rules = set(ctx.PRETTIERIGNORE_TEMPLATE_RULES)
    assert ".repo_template/sync_state.json" in rules
    assert "docs/tasks_index.json" not in rules
    assert "docs/archive/tasks_index.json" not in rules
    assert "docs/**/handoff.json" in rules


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
    merge_prettierignore(set())
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
    merge_mcp(src, changed)
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
    changed: set[Path] = set()
    repair_symlinks(changed)
    raw = settings.read_text()
    assert json.loads(raw)["hooks"]["PreToolUse"][0]["hooks"] == [
        {"type": "command", "command": "echo keep"},
    ]
    assert '\n    "hooks"' in raw  # 退役项移除，但 4 空格体例保留
    assert settings in changed


def test_retired_template_warnings(env):
    consumer = env["consumer"]
    assert retired_template_warnings() == []
    stale = consumer / ".github/workflows/repo-template-ci.yml"
    stale.parent.mkdir(parents=True)
    stale.write_text("name: repo-template\n")
    warnings = retired_template_warnings()
    assert len(warnings) == 1
    assert ".github/workflows/repo-template-ci.yml" in warnings[0]


def test_mcp_merge_keywise(env):
    src, consumer = env["src"], env["consumer"]
    (src / ".mcp.json").write_text(json.dumps({"mcpServers": {"tpl-server": {"command": "x"}}}))
    dmcp = consumer / ".mcp.json"
    dmcp.write_text(json.dumps({"mcpServers": {"consumer-server": {"command": "y"}}}))

    changed: set[Path] = set()
    merge_mcp(src, changed)
    ddata = json.loads(dmcp.read_text())
    assert "tpl-server" in ddata["mcpServers"]
    assert "consumer-server" in ddata["mcpServers"]
    # 已有键不覆盖（禁冲密钥）
    (src / ".mcp.json").write_text(json.dumps({"mcpServers": {"tpl-server": {"command": "OVERWRITE"}}}))
    changed = set()
    merge_mcp(src, changed)
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
    reports = merge_mcp(src, changed)

    assert dmcp in changed
    assert any("需审阅" in r or "占位" in r for r in reports)
    data = json.loads(dmcp.read_text(encoding="utf-8"))
    env_vars = data["mcpServers"]["demo-server"]["env"]
    assert env_vars["SECRET_TOKEN"] == "<SECRET_TOKEN_PLACEHOLDER>"
    assert env_vars["ENDPOINT"] == "<ENDPOINT_PLACEHOLDER>"



# ---------------------------------------------------------------------------
# AGENTS.md 标题分区协议
# ---------------------------------------------------------------------------

def test_sectioned_agents_force_merge_intro_boundaries(env):
    src, consumer = env["src"], env["consumer"]
    source = """项目介绍模板，不能覆盖消费内容。

## 目录与读写规则
模板目录规则

## 开发原则
模板开发原则 v2
"""
    target = """消费仓项目介绍和自定义规则。

## 目录与读写规则
消费仓目录规则
自定义目录规则

## 开发原则
旧开发原则
"""
    (src / "AGENTS.md").write_text(source)
    (consumer / "AGENTS.md").write_text(target)
    changed: set[Path] = set()
    assert _apply_agents_sectioned(src, changed) is True
    result = (consumer / "AGENTS.md").read_text()
    assert "消费仓项目介绍和自定义规则。" in result
    assert "消费仓目录规则" in result and "模板目录规则" not in result
    assert "模板开发原则 v2" in result and "旧开发原则" not in result
    assert consumer / "AGENTS.md" in changed


def test_sectioned_agents_requires_headings(env):
    src, consumer = env["src"], env["consumer"]
    (src / "AGENTS.md").write_text("legacy")
    (consumer / "AGENTS.md").write_text("legacy")
    # 旧版模板源继续交回旧的整文件裁定逻辑。
    assert _apply_agents_sectioned(src, set()) is False


def test_agents_section_status_missing_headings_returns_none(env):
    src, consumer = env["src"], env["consumer"]
    sectioned = "介绍\n\n## 目录与读写规则\n规则\n\n## 开发原则\n原则\n"
    (src / "AGENTS.md").write_text(sectioned)
    (consumer / "AGENTS.md").write_text("介绍\n\n## 目录与读写规则\n规则\n")
    assert agents_section_status(src) is None


@pytest.mark.parametrize("missing_side", ["模板源", "消费仓"])
def test_plan_diagnoses_missing_agents_headings(env, missing_side, capsys):
    src, consumer = env["src"], env["consumer"]
    sectioned = "介绍\n\n## 目录与读写规则\n规则\n\n## 开发原则\n原则\n"
    legacy = "介绍\n\n## 其它\n旧内容\n"
    if missing_side == "模板源":
        (src / "AGENTS.md").write_text(legacy)
        (consumer / "AGENTS.md").write_text(sectioned)
    else:
        (src / "AGENTS.md").write_text(sectioned)
        (consumer / "AGENTS.md").write_text(legacy)

    assert cmd_plan(Namespace()) == 0
    out = capsys.readouterr().out
    assert "未识别到完整标题协议" in out
    assert f"{missing_side} AGENTS.md 缺" in out


def test_apply_rejects_consumer_agents_missing_headings_with_guidance(env, capsys):
    src, consumer = env["src"], env["consumer"]
    (src / "AGENTS.md").write_text(
        "介绍\n\n## 目录与读写规则\n规则\n\n## 开发原则\n原则\n"
    )
    (consumer / "AGENTS.md").write_text("旧格式\n")

    assert cmd_apply(Namespace(
        allow_dirty_source=True, skip_tests=True, decisions={"AGENTS.md": "update"},
    )) == 1
    err = capsys.readouterr().err
    assert "按模板补齐 ## 目录与读写规则、## 开发原则 后重跑" in err
    assert (consumer / "AGENTS.md").read_text() == "旧格式\n"


def test_plan_reports_forbidden_and_warning_for_extra_agents_headings(env, capsys):
    src, consumer = env["src"], env["consumer"]
    (src / "AGENTS.md").write_text("介绍\n\n## 目录与读写规则\n规则\n\n## 开发原则\n原则\n")
    consumer_agents = (
        "消费介绍\n\n## 核心铁律\n铁律\n\n## 目录与读写规则\n消费规则\n\n"
        "## 开发原则\n原则\n\n## 下载与兜底约定\n私有规则\n"
    )
    (consumer / "AGENTS.md").write_text(consumer_agents)

    assert cmd_plan(Namespace()) == 0
    out = capsys.readouterr().out
    assert "额外标题: ## 下载与兜底约定" in out
    assert "forbidden" in out
    assert "在 ## 开发原则 之后存在额外标题" in out
    assert "项目专属规则须移至 ## 目录与读写规则 之前（项目介绍区）" in out


def test_plan_reports_forbidden_for_headings_between_merge_and_force(env, capsys):
    src, consumer = env["src"], env["consumer"]
    (src / "AGENTS.md").write_text("介绍\n\n## 目录与读写规则\n规则\n\n## 开发原则\n原则\n")
    consumer_agents = (
        "消费介绍\n\n## 目录与读写规则\n消费规则\n\n"
        "## 开发工作流\n旧工作流\n\n## 开发原则\n原则\n"
    )
    (consumer / "AGENTS.md").write_text(consumer_agents)

    assert cmd_plan(Namespace()) == 0
    out = capsys.readouterr().out
    assert "额外标题: ## 开发工作流" in out
    assert "forbidden" in out
    assert "在 ## 目录与读写规则 和 ## 开发原则 之间存在额外标题" in out


def test_apply_rejects_consumer_agents_with_extra_headings_after_force(env, capsys):
    src, consumer = env["src"], env["consumer"]
    (src / "AGENTS.md").write_text("介绍\n\n## 目录与读写规则\n规则\n\n## 开发原则\n新原则\n")
    consumer_agents = (
        "消费介绍\n\n## 目录与读写规则\n消费规则\n\n"
        "## 开发原则\n旧原则\n\n## 下载与兜底约定\n私有规则\n"
    )
    (consumer / "AGENTS.md").write_text(consumer_agents)

    assert cmd_apply(Namespace(
        allow_dirty_source=True, skip_tests=True, decisions={"AGENTS.md": "update"},
    )) == 1
    err = capsys.readouterr().err
    assert "在 ## 开发原则 之后存在额外标题" in err
    assert "## 下载与兜底约定" in err
    assert (consumer / "AGENTS.md").read_text() == consumer_agents


def test_apply_rejects_candidate_agents_file_with_extra_headings(env, capsys):
    src, consumer = env["src"], env["consumer"]
    (src / "AGENTS.md").write_text("介绍\n\n## 目录与读写规则\n规则\n\n## 开发原则\n新原则\n")
    consumer_agents = "消费介绍\n\n## 目录与读写规则\n消费规则\n\n## 开发原则\n旧原则\n"
    (consumer / "AGENTS.md").write_text(consumer_agents)
    candidate_agents = (
        "消费介绍\n\n## 目录与读写规则\n合并规则\n\n## 开发原则\n旧原则\n\n## 项目门牌与硬约束\n私有\n"
    )
    candidate_file = consumer / ".scratch/candidate.md"
    candidate_file.parent.mkdir(parents=True, exist_ok=True)
    candidate_file.write_text(candidate_agents)

    assert cmd_apply(Namespace(
        allow_dirty_source=True, skip_tests=True, decisions={}, agents_file=str(candidate_file),
    )) == 1
    err = capsys.readouterr().err
    assert "候选" in err
    assert "在 ## 开发原则 之后存在额外标题" in err
    assert "## 项目门牌与硬约束" in err


def test_agents_headings_in_code_fences_are_ignored(env, capsys):
    src, consumer = env["src"], env["consumer"]
    source = "介绍\n\n## 目录与读写规则\n规则\n\n## 开发原则\n原则\n"
    target = (
        "消费介绍\n\n## 目录与读写规则\n规则\n```bash\n## 这是代码块内的注释\n```\n\n## 开发原则\n原则\n"
    )
    (src / "AGENTS.md").write_text(source)
    (consumer / "AGENTS.md").write_text(target)

    status = agents_section_status(src)
    assert status is not None
    assert "extra" not in status
    assert cmd_plan(Namespace()) == 0
    out = capsys.readouterr().out
    assert "forbidden" not in out


def test_replace_agents_force_replaces_cleanly_to_eof():
    src_text = "模板介绍\n\n## 目录与读写规则\n模板目录\n\n## 开发原则\n新原则1\n新原则2\n"
    dst_text = "消费介绍\n\n## 目录与读写规则\n消费目录\n\n## 开发原则\n旧原则\n旧段落\n"
    result, force_changed, merge_diff, intro_diff = _replace_agents_force(src_text, dst_text)
    assert force_changed is True
    assert merge_diff is True
    assert intro_diff is True
    assert result == "消费介绍\n\n## 目录与读写规则\n消费目录\n\n## 开发原则\n新原则1\n新原则2\n"


# ---------------------------------------------------------------------------
# apply 全流程 + 改动清单
# ---------------------------------------------------------------------------

def test_apply_flow_writes_and_advances_state(env, capsys):
    src, consumer, head = env["src"], env["consumer"], env["head"]
    old_agents = consumer / "AGENTS.md"
    old_agents.write_text("CONSUMER OLD\n")

    cmd_apply(Namespace(allow_dirty_source=True, decision=["AGENTS.md:update"], skip_tests=True, decisions={"AGENTS.md": "update"}))
    out = capsys.readouterr().out

    # 硬同步
    assert (consumer / ".repo_template/scripts/task.py").exists()
    assert (consumer / ".repo_template/docs/spike_report_template.md").read_text() == "# report\n"
    assert not (consumer / ".claude/hooks/merge_guard.py").exists()
    assert (consumer / ".repo_template/skills/task-run/SKILL.md").read_text().startswith("---")
    assert (consumer / ".claude/skills/task-run").is_symlink()
    assert (consumer / ".agents/skills/task-run").is_symlink()
    assert (consumer / ".opencode/commands/task-run.md").is_file()
    # 裁定单元 update
    assert (consumer / "AGENTS.md").read_text() == "SRC AGENTS\n"
    # state 推进
    state = read_state()
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

    assert cmd_status(Namespace()) == 0
    assert warning in capsys.readouterr().out
    assert cmd_plan(Namespace()) == 0
    assert warning in capsys.readouterr().out

    with pytest.raises(ctx.SyncError, match="apply 不支持软链 AGENTS.md"):
        cmd_apply(Namespace(allow_dirty_source=True, skip_tests=True, decisions={}))
    assert not (consumer / ".scratch/repo_sync_rollback/transaction.json").exists()


def test_apply_skips_unresolved_shared_unit(env):
    src, consumer = env["src"], env["consumer"]
    old_agents = consumer / "AGENTS.md"
    old_agents.write_text("CONSUMER OLD\n")

    cmd_apply(Namespace(allow_dirty_source=True, decision=[], skip_tests=True, decisions={}))
    # 无决策 → AGENTS.md 不动（ask_user 语义）
    assert (consumer / "AGENTS.md").read_text() == "CONSUMER OLD\n"


def test_apply_src_dirty_does_not_advance_commit(env):
    src, consumer = env["src"], env["consumer"]
    (src / ".repo_template/scripts/dirty_extra.py").write_text("x\n")  # SRC 未提交改动
    _add_prompt("跳过测试", "")
    cmd_apply(Namespace(allow_dirty_source=True, decision=[], skip_tests=True, decisions={}))
    state = read_state()
    assert state["last_synced_commit"] is None
    assert state["last_synced_at"]


def test_apply_test_failure_rolls_back_keeps_backup_and_prints_changed(env, monkeypatch, capsys):
    """契约测试失败：完整回滚、保留备份、输出 changed 清单与可操作错误，state 不推进。"""
    src, consumer = env["src"], env["consumer"]
    old_agents = consumer / "AGENTS.md"
    old_agents.write_text("CONSUMER OLD\n")

    monkeypatch.setattr("template_sync.apply._run_tests", lambda c: False)
    rc = cmd_apply(
        Namespace(allow_dirty_source=True, decision=["AGENTS.md:update"], skip_tests=False, decisions={"AGENTS.md": "update"})
    )
    captured = capsys.readouterr()
    assert rc == 1
    # 已写入改动完整回滚
    assert old_agents.read_text() == "CONSUMER OLD\n"
    assert not (consumer / ".repo_template/scripts/task.py").exists()
    # 备份保留（未被清理）
    assert (consumer / ".scratch/repo_sync_rollback").exists()
    # changed 清单输出 + 可操作错误
    assert "本次写入清单" in captured.out
    assert ".repo_template/scripts/task.py" in captured.out
    assert "已完整回滚" in captured.err
    assert read_state()["last_synced_commit"] is None


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
        sync_file(src / ".md_kx.toml", target, set())
    assert (target / "user_note.txt").is_file()
    assert not (target / ".md_kx.toml").exists()


def test_sync_dir_rejects_file_target(env):
    """目标同名为文件：明确报错停止，不静默覆盖。"""
    src, consumer = env["src"], env["consumer"]
    target = consumer / ".repo_template" / "docs"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("i am a file\n", encoding="utf-8")

    with pytest.raises(ctx.SyncError, match="类型不符"):
        sync_dir(src / ".repo_template/docs", target, set())
    assert target.read_text() == "i am a file\n"


# ---------------------------------------------------------------------------
# 边界：同一性拒绝 / init
# ---------------------------------------------------------------------------

def test_assert_not_self(env, monkeypatch):
    src = env["src"]
    # 无关路径 → 不拒绝
    other = env["consumer"].parent / "unrelated"
    assert_not_self(other)
    # CONSUMER 指向 src 自身 → 拒绝
    monkeypatch.setattr(ctx, "CONSUMER", src)
    with pytest.raises(ctx.SyncError):
        assert_not_self(src)


def test_init_writes_template_source(env, monkeypatch):
    consumer, state_file = env["consumer"], env["state_file"]
    monkeypatch.setattr(ctx, "CONSUMER", consumer)
    monkeypatch.setattr(ctx, "STATE_PATH", state_file)
    cmd_init(Namespace(source=str(env["src"])))
    state = read_state()
    assert state["template_source"]["kind"] == "path"
    assert state["template_source"]["value"] == str(env["src"])


def test_init_refuses_legacy_state_and_requires_explicit_migration(env, monkeypatch):
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
        cmd_init(Namespace(source=str(env["src"])))
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

    from template_sync.state import cmd_migrate

    assert cmd_migrate(Namespace(kind="legacy-state")) == 0
    assert not legacy.exists()
    data = json.loads(env["state_file"].read_text(encoding="utf-8"))
    assert data["last_synced_commit"] == "abc123"
    assert data["template_source"]["value"] == str(env["src"])
    assert any(p["text"] == "旧规则" for p in data["user_prompts"])


def test_resolve_src_rejects_invalid(env):
    state = read_state()
    state["template_source"]["value"] = "/nonexistent/not_a_template"
    with pytest.raises(ctx.SyncError):
        resolve_src(state)


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

    orig_state = read_state()
    with pytest.raises(ctx.SyncError, match="不是有效模板源"):
        source_identity(str(not_a_template))

    # 验证原有 state 中的 template_source 保持不变
    assert read_state()["template_source"] == orig_state["template_source"]


def test_mcp_merge_tolerates_non_dict_structure(env, capsys):
    """验证遇到合法 JSON 但非对象的错误 MCP 结构时不崩溃并告警跳过（Issue #26）。"""
    src, consumer = env["src"], env["consumer"]
    (src / ".mcp.json").write_text(json.dumps({"mcpServers": {"tpl": {"command": "x"}}}))
    dmcp = consumer / ".mcp.json"

    # 1. 消费端为数组
    dmcp.write_text("[]\n", encoding="utf-8")
    changed = set()
    merge_mcp(src, changed)
    err = capsys.readouterr().err
    assert "非 JSON 对象" in err
    assert dmcp not in changed

    # 2. 消费端 mcpServers 为数组
    dmcp.write_text(json.dumps({"mcpServers": [1, 2, 3]}), encoding="utf-8")
    changed = set()
    merge_mcp(src, changed)
    err = capsys.readouterr().err
    assert "mcpServers 非对象" in err




# ---------------------------------------------------------------------------
# install-hooks：core.hooksPath 幂等设置
# 旧测试 test_install_hooks_overwrites_stale_path 已删：一律覆盖会摘掉
# husky/lefthook；现语义见 refuses_foreign_path / force_overwrites。
# ---------------------------------------------------------------------------

def _ensure_hook(consumer: Path, *, executable: bool = True) -> Path:
    hook = consumer / ".repo_template/hooks/pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    hook.chmod(0o755 if executable else 0o644)
    return hook


def test_install_hooks_sets_and_idempotent(tmp_path, monkeypatch, capsys):
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _git(consumer, "init", "-b", "main")
    _ensure_hook(consumer)
    monkeypatch.setattr(ctx, "CONSUMER", consumer)

    assert cmd_install_hooks(Namespace()) == 0
    out = capsys.readouterr().out
    assert "已设为" in out
    assert _git(consumer, "config", "--get", "core.hooksPath").stdout.strip() \
        == ".repo_template/hooks"

    # 幂等：已指向目标 → no-op
    assert cmd_install_hooks(Namespace()) == 0
    assert "无需改动" in capsys.readouterr().out


def test_install_hooks_uses_consumer_lock_for_both_paths(tmp_path, monkeypatch):
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _git(consumer, "init", "-b", "main")
    _ensure_hook(consumer)
    monkeypatch.setattr(ctx, "CONSUMER", consumer)
    calls = []

    def fake_lock(root, name):
        calls.append((root, name))
        return contextlib.nullcontext()

    monkeypatch.setattr("template_sync.common.git_common_lock", fake_lock)
    assert cmd_install_hooks(Namespace()) == 0
    assert [root for root, _ in calls] == [consumer]


def test_install_hooks_refuses_foreign_path(tmp_path, monkeypatch, capsys):
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _git(consumer, "init", "-b", "main")
    _git(consumer, "config", "core.hooksPath", ".git/hooks")
    _ensure_hook(consumer)
    monkeypatch.setattr(ctx, "CONSUMER", consumer)

    assert cmd_install_hooks(Namespace()) == 1
    err = capsys.readouterr().err
    assert "--force" in err
    assert _git(consumer, "config", "--get", "core.hooksPath").stdout.strip() \
        == ".git/hooks"


def test_install_hooks_force_overwrites(tmp_path, monkeypatch, capsys):
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _git(consumer, "init", "-b", "main")
    _git(consumer, "config", "core.hooksPath", ".git/hooks")
    _ensure_hook(consumer)
    monkeypatch.setattr(ctx, "CONSUMER", consumer)

    assert cmd_install_hooks(Namespace(force=True)) == 0
    assert _git(consumer, "config", "--get", "core.hooksPath").stdout.strip() \
        == ".repo_template/hooks"


def test_install_hooks_nested_product_uses_toplevel_relative(tmp_path, monkeypatch, capsys):
    """工厂仓：git 顶层在上、产物在 repo/ 时，hooksPath 须相对顶层。"""
    factory = tmp_path / "factory"
    product = factory / "repo"
    product.mkdir(parents=True)
    _git(factory, "init", "-b", "main")
    _ensure_hook(product)
    monkeypatch.setattr(ctx, "CONSUMER", product)

    assert cmd_install_hooks(Namespace()) == 0
    assert _git(factory, "config", "--get", "core.hooksPath").stdout.strip() \
        == "repo/.repo_template/hooks"
    assert "repo/.repo_template/hooks" in capsys.readouterr().out


def test_install_hooks_rejects_missing_or_nonexec(tmp_path, monkeypatch, capsys):
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _git(consumer, "init", "-b", "main")
    monkeypatch.setattr(ctx, "CONSUMER", consumer)

    assert cmd_install_hooks(Namespace()) == 1
    assert "缺 hook 脚本" in capsys.readouterr().err

    _ensure_hook(consumer, executable=False)
    assert cmd_install_hooks(Namespace()) == 1
    assert "不可执行" in capsys.readouterr().err
    assert _git(consumer, "config", "--get", "core.hooksPath", check=False).stdout.strip() \
        == ""


# ---------------------------------------------------------------------------
# F16：apply 中途失败回滚（目录删除 / skill 覆盖须恢复内容）
# ---------------------------------------------------------------------------

def test_repair_symlinks_removes_retired_managed_merge_guard(env):
    consumer = env['consumer']
    guard = consumer / '.claude/hooks/merge_guard.py'
    guard.parent.mkdir(parents=True)
    guard.symlink_to('../../.repo_template/hooks/merge_guard.py')
    changed: set[Path] = set()
    reports = repair_symlinks(changed)
    assert not guard.exists() and not guard.is_symlink()
    assert guard in changed
    assert any('已退役并移除' in line for line in reports)


def test_repair_symlinks_preserves_manual_merge_guard_file(env):
    consumer = env['consumer']
    guard = consumer / '.claude/hooks/merge_guard.py'
    guard.parent.mkdir(parents=True)
    guard.write_text('manual\n')
    changed: set[Path] = set()
    repair_symlinks(changed)
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
    repair_symlinks(changed)
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
    changed: set[Path] = set()
    repair_symlinks(changed)
    data = json.loads(settings.read_text())
    assert data['hooks']['PreToolUse'][0]['hooks'] == [
        {'type': 'command', 'command': 'python3 scripts/custom_merge_guard.py'},
    ]
    assert settings in changed


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
    changed: set[Path] = set()
    repair_symlinks(changed)
    data = json.loads(settings.read_text())
    assert data['hooks']['PreToolUse'][0]['hooks'] == [
        {'type': 'command', 'command': 'python3 scripts/custom_merge_guard.py'},
        {'type': 'command', 'command': 'echo keep'},
    ]
    assert 'PostToolUse' in data['hooks']
    assert not guard.is_symlink()
    assert settings in changed and guard in changed


def test_repair_symlinks_refuses_malformed_settings_before_unlink(env):
    consumer = env['consumer']
    settings = consumer / '.claude/settings.json'
    settings.parent.mkdir(parents=True)
    settings.write_text('{bad')
    guard = consumer / '.claude/hooks/merge_guard.py'
    guard.parent.mkdir(parents=True, exist_ok=True)
    guard.symlink_to('../../.repo_template/hooks/merge_guard.py')
    with pytest.raises(ctx.SyncError, match='不能安全移除'):
        repair_symlinks(set())
    assert guard.is_symlink()


def _install_current_workflow_templates(consumer: Path) -> tuple[str, str]:
    source = SCRIPTS_DIR.parent / 'docs/task_template'
    target = consumer / '.repo_template/docs/task_template'
    target.mkdir(parents=True, exist_ok=True)
    task = (source / 'task.md').read_text()
    note = (source / 'progress.md').read_text()
    (target / 'task.md').write_text(task)
    (target / 'progress.md').write_text(note)
    return task, note


def _bind_repo_task_context(monkeypatch: pytest.MonkeyPatch, consumer: Path) -> None:
    from repo_task import context as task_ctx

    monkeypatch.setattr(task_ctx, "REPO_ROOT", consumer)
    monkeypatch.setattr(task_ctx, "TASKS_DIR", consumer / "docs/tasks")
    monkeypatch.setattr(task_ctx, "ARCHIVE_TASKS_DIR", consumer / "docs/archive/tasks")


def _old_task_documents(spec: str, task: str) -> tuple[str, str]:
    old_spec = spec.replace(
        '只写可观察、可独立验证的行为；每条使用稳定且不复用的 `AC-NNN`。需真实部署或人工环境验证时在编号前加 `[deploy]`。创建时均为未勾选（`- [ ]`）；实施验证通过后由实施 Agent 就地打勾（`- [x]`）；由 Review Agent 独立复核打勾真实性。技术选型不作为行为 AC。',
        '旧版规范文字，只写可观察行为和 AC-NNN。',
    )
    old_task = task.replace('status: backlog', 'status: blocked', 1)
    old_task = old_task.replace('branch: ""\n', 'branch: ""\nschedule_status: scheduled\n', 1)
    old_task = old_task.replace('review_limit: 5\n', '').replace('verify_limit: 5\n', '')
    old_task = old_task.replace(
        '执行期记录关键步骤、决策、验证、阻塞和用户批准的新轮次上限。',
        '执行期边做边写：旧版固定说明。',
    ).replace(
        '创建期不预测实施步骤。只记有追溯价值的内容；无事项时写“无”。',
        '创建期不预测实施步骤——旧版固定说明。',
    )
    return old_spec, old_task


def test_guide_blocks_ignore_fenced_headings_and_blocks():
    text = (
        "### 范围\n\n"
        "```markdown\n### 验收标准\n"
        f"{_GUIDE_OPEN}\n伪规范\n{_GUIDE_CLOSE}\n"
        "```\n\n"
        f"{_GUIDE_OPEN}\n真规范\n{_GUIDE_CLOSE}\n"
    )
    blocks = _guide_blocks_by_heading(text)
    assert set(blocks) == {"范围"}
    assert len(blocks["范围"]) == 1
    assert "真规范" in blocks["范围"][0]
    assert "伪规范" not in blocks["范围"][0]


def test_replace_guide_blocks_ignores_fenced_heading():
    current = f"### 验收标准\n\n{_GUIDE_OPEN}\n现行规范\n{_GUIDE_CLOSE}\n"
    old = "### 范围\n\n```markdown\n### 验收标准\n旧代码\n```\n\n保留内容\n"
    assert _replace_guide_blocks(old, current) == old


def test_rename_legacy_title_tracks_mixed_and_longer_fences():
    mixed = [
        "# Task spec", "```markdown", "~~~",
        "# Task 过程总账", "~~~", "```", "# Task 过程总账",
    ]
    _rename_legacy_title(mixed, 0)
    assert mixed[3] == "# Task 过程总账"
    assert mixed[6] == "# Task 进展"

    longer = [
        "````markdown", "```", "# Task 过程总账", "````", "# Task 过程总账",
    ]
    _rename_legacy_title(longer, 0)
    assert longer[2] == "# Task 过程总账"
    assert longer[4] == "# Task 进展"


def test_legacy_full_spec_schema_migrates_as_heading_groups(env):
    current_spec, _ = _install_current_workflow_templates(env['consumer'])
    legacy = (Path(__file__).parent / 'fixtures/spec_pre_workflow_simplification.md').read_text()
    migrated = _replace_guide_blocks(legacy, current_spec)
    current_blocks = _guide_blocks_by_heading(current_spec)
    migrated_blocks = _guide_blocks_by_heading(migrated)
    assert migrated_blocks == current_blocks
    assert _replace_guide_blocks(migrated, current_spec) == migrated
    assert '已判定不写测试的分支与原因' not in migrated
    assert 'mock 边界、fixture 来源、断言目标' not in migrated


def test_missing_guide_block_is_restored_and_reported(env):
    consumer = env['consumer']
    current_spec, current_task = _install_current_workflow_templates(consumer)
    _install_current_workflow_templates(env['src'])
    missing = current_spec.replace(
        _guide_blocks_by_heading(current_spec)['验收标准'][0] + '\n\n', '', 1
    )
    task_dir = consumer / 'docs/tasks/t001_missing'
    task_dir.mkdir(parents=True)
    (task_dir / 'task.md').write_text(missing)
    (task_dir / 'progress.md').write_text(current_task)
    migrations, worktrees, errors = workflow_migration_status(env['src'])
    assert 'docs/tasks/t001_missing/task.md' in migrations
    assert worktrees == [] and errors == []
    force_migrate_workflow_tasks(set())
    assert (task_dir / 'task.md').read_text() == current_spec


def test_migration_status_reports_malformed_spec_without_raising(env):
    consumer = env['consumer']
    current_spec, current_task = _install_current_workflow_templates(consumer)
    _install_current_workflow_templates(env['src'])
    malformed = current_spec.replace(_GUIDE_CLOSE, '', 1)
    task_dir = consumer / 'docs/tasks/t001_malformed'
    task_dir.mkdir(parents=True)
    (task_dir / 'task.md').write_text(malformed)
    (task_dir / 'progress.md').write_text(current_task)
    migrations, worktrees, errors = workflow_migration_status(env['src'])
    assert migrations == [] and worktrees == []
    assert len(errors) == 1 and 't001_malformed/task.md' in errors[0]
    assert cmd_status(Namespace()) == 0
    assert cmd_plan(Namespace()) == 0
    with pytest.raises(ctx.SyncError, match='无法安全迁移'):
        force_migrate_workflow_tasks(set())


def test_workflow_migrations_are_idempotent_for_current_schema(env):
    current_spec, current_task = _install_current_workflow_templates(env['consumer'])
    assert _replace_guide_blocks(current_spec, current_spec) == current_spec
    assert _migrate_task_text(current_task, current_task) == current_task


@pytest.mark.parametrize('parent', ['docs/tasks', 'docs/archive/tasks'])
@pytest.mark.parametrize('layout', ['spec_task', 'task_note'])
def test_progress_migration_preserves_task_history(env, parent, layout):
    consumer = env['consumer']
    spec, progress = _install_current_workflow_templates(consumer)
    _install_current_workflow_templates(env['src'])
    task_dir = consumer / parent / 't001_history'
    task_dir.mkdir(parents=True)
    old = progress.replace('# Task 进展', '# Task 过程总账').replace(
        'note: ""', 'note: "保留审计说明"',
    ) + '\n```markdown\n# Task 过程总账\n```\n\n保留实施证据。\n'
    spec_name, progress_name = ('spec.md', 'task.md') if layout == 'spec_task' else ('task.md', 'note.md')
    (task_dir / spec_name).write_text(spec)
    (task_dir / progress_name).write_text(old)
    (task_dir / 'review_general.md').write_text('历史 review，不补 TICK 标签。\n')
    (task_dir / 'handoff.json').write_text('{"history": true}\n')
    migrations, worktrees, errors = workflow_migration_status(env['src'])
    assert str((task_dir / progress_name).relative_to(consumer)) in migrations
    assert worktrees == [] and errors == []

    changed = set()
    force_migrate_workflow_tasks(changed)

    assert (task_dir / 'task.md').read_text() == spec
    assert (task_dir / 'progress.md').read_text() == old.replace('# Task 过程总账', '# Task 进展', 1)
    assert not (task_dir / 'note.md').exists()
    assert not (task_dir / 'spec.md').exists()
    assert (task_dir / 'review_general.md').read_text() == '历史 review，不补 TICK 标签。\n'
    assert (task_dir / 'handoff.json').read_text() == '{"history": true}\n'
    assert task_dir / 'progress.md' in changed
    assert task_dir / progress_name in changed
    assert force_migrate_workflow_tasks(set()) == []


@pytest.mark.parametrize('parent', ['docs/tasks', 'docs/archive/tasks'])
def test_progress_migration_rejects_duplicate_names_before_any_writes(env, parent):
    consumer = env['consumer']
    spec, progress = _install_current_workflow_templates(consumer)
    _install_current_workflow_templates(env['src'])
    for slug in ('a_ready', 'z_conflict'):
        directory = consumer / parent / f't001_{slug}'
        directory.mkdir(parents=True)
        (directory / 'task.md').write_text(spec)
        (directory / 'note.md').write_text(progress.replace('# Task 进展', '# Task 过程总账'))
    (directory / 'progress.md').write_text(progress)
    before = {path: path.read_bytes() for path in (consumer / parent).rglob('*') if path.is_file()}

    _, _, errors = workflow_migration_status(env['src'])
    assert any('note.md' in error and 'progress.md' in error for error in errors)
    changed = set()
    with pytest.raises(ctx.SyncError, match='note.md.*progress.md'):
        force_migrate_workflow_tasks(changed)
    assert changed == set()
    assert {path: path.read_bytes() for path in (consumer / parent).rglob('*') if path.is_file()} == before


def test_apply_failure_restores_progress_rename_and_history(env, monkeypatch):
    consumer = env['consumer']
    _bind_repo_task_context(monkeypatch, consumer)
    spec, progress = _install_current_workflow_templates(env['src'])
    task_dir = consumer / 'docs/archive/tasks/t001_history'
    task_dir.mkdir(parents=True)
    expected_progress = (
        progress.replace('tid: t000', 'tid: t001')
        .replace('slug: example_slug', 'slug: history')
        .replace('status: backlog', 'status: done')
    )
    old = expected_progress.replace('# Task 进展', '# Task 过程总账')
    (task_dir / 'task.md').write_text(spec)
    (task_dir / 'note.md').write_text(old)
    state_before = env['state_file'].read_bytes()
    migrated = []

    def fail_after_migration(changed):
        migrated.append((task_dir / 'progress.md').read_text())
        assert not (task_dir / 'note.md').exists()
        raise ctx.SyncError('模拟迁移后同步失败')

    monkeypatch.setattr('template_sync.apply.migrate_conventions', fail_after_migration)
    assert cmd_apply(Namespace(allow_dirty_source=True, skip_tests=True, decisions={})) == 1
    assert migrated == [expected_progress]
    assert (task_dir / 'note.md').read_text() == old
    assert (task_dir / 'task.md').read_text() == spec
    assert not (task_dir / 'progress.md').exists()
    assert not (consumer / 'docs/tasks_index.json').exists()
    assert not (consumer / 'docs/archive/tasks_index.json').exists()
    assert env['state_file'].read_bytes() == state_before


def test_force_migrate_workflow_updates_old_task_documents(env):
    consumer = env['consumer']
    current_spec, current_task = _install_current_workflow_templates(consumer)
    _install_current_workflow_templates(env['src'])
    old_spec, old_task = _old_task_documents(current_spec, current_task)

    # 1. 活跃任务：构造真实旧版文件布局 (spec.md 为需求规格, task.md 为过程总账)
    task_dir = consumer / 'docs/tasks/t001_old'
    task_dir.mkdir(parents=True)
    active_old_task = (
        old_task.replace('tid: t000', 'tid: t001')
        .replace('slug: example_slug', 'slug: old')
    )
    (task_dir / 'spec.md').write_text(old_spec, encoding="utf-8")
    (task_dir / 'task.md').write_text(active_old_task, encoding="utf-8")

    # 2. 归档任务：构造真实旧版文件布局 (spec.md + task.md)
    archive_dir = consumer / 'docs/archive/tasks/t002_archived_old'
    archive_dir.mkdir(parents=True)
    archived_old_task = (
        old_task.replace('tid: t000', 'tid: t002')
        .replace('slug: example_slug', 'slug: archived_old')
        .replace('status: blocked', 'status: done')
    )
    (archive_dir / 'spec.md').write_text(old_spec, encoding="utf-8")
    (archive_dir / 'task.md').write_text(archived_old_task, encoding="utf-8")

    # 迁移前：store.py 扫描会因为缺少 progress.md 拒绝扫描
    from repo_task import store as task_store, context as task_ctx
    with pytest.raises(task_ctx.TaskDataError, match="缺 progress.md"):
        task_store._scan_tasks_in_directories(consumer / 'docs/tasks', consumer / 'docs/archive/tasks')

    # 迁移状态应检出活跃和归档下的旧文件
    migrations, worktrees, errors = workflow_migration_status(env['src'])
    assert 'docs/tasks/t001_old/spec.md' in migrations
    assert 'docs/tasks/t001_old/task.md' in migrations
    assert 'docs/archive/tasks/t002_archived_old/spec.md' in migrations
    assert 'docs/archive/tasks/t002_archived_old/task.md' in migrations
    assert worktrees == [] and errors == []

    changed: set[Path] = set()
    reports = force_migrate_workflow_tasks(changed)

    # 验证活跃任务已完成布局转换
    assert not (task_dir / 'spec.md').exists()
    assert (task_dir / 'task.md').is_file()
    assert (task_dir / 'progress.md').is_file()

    migrated_spec = (task_dir / 'task.md').read_text(encoding="utf-8")
    migrated_task = (task_dir / 'progress.md').read_text(encoding="utf-8")
    assert '旧版规范文字' not in migrated_spec
    for blocks in _guide_blocks_by_heading(current_spec).values():
        for block in blocks:
            assert block in migrated_spec
    assert 'status: "active"' in migrated_task
    assert 'schedule_status' not in migrated_task
    assert 'review_limit: "5"' in migrated_task
    assert 'verify_limit: "5"' in migrated_task
    for line in _implementation_guidance(current_task):
        assert line in migrated_task
    assert '旧版固定说明' not in migrated_task
    assert task_dir / 'spec.md' in changed
    assert task_dir / 'task.md' in changed
    assert task_dir / 'progress.md' in changed

    # 验证归档任务已完成布局转换
    assert not (archive_dir / 'spec.md').exists()
    assert (archive_dir / 'task.md').is_file()
    assert (archive_dir / 'progress.md').is_file()
    migrated_archive_task = (archive_dir / 'progress.md').read_text(encoding="utf-8")
    assert 'status: done' in migrated_archive_task or 'status: "done"' in migrated_archive_task
    assert archive_dir / 'spec.md' in changed
    assert archive_dir / 'task.md' in changed
    assert archive_dir / 'progress.md' in changed

    # 幂等性：迁移后再查 status 为空，再次执行不写盘
    assert _replace_guide_blocks(migrated_spec, current_spec) == migrated_spec
    assert _migrate_task_text(migrated_task, current_task) == migrated_task
    assert force_migrate_workflow_tasks(set()) == []
    migrations, worktrees, errors = workflow_migration_status(env['src'])
    assert migrations == [] and worktrees == [] and errors == []

    # 迁移后：store.py 扫描正常解析活跃与归档任务
    tasks = task_store._scan_tasks_in_directories(consumer / 'docs/tasks', consumer / 'docs/archive/tasks')
    assert [t['tid'] for t in tasks] == ['t001', 't002']
    assert tasks[0]['status'] == 'active' and tasks[0]['dir'] == 'docs/tasks/t001_old'
    assert tasks[1]['status'] == 'done' and tasks[1]['dir'] == 'docs/archive/tasks/t002_archived_old'


def test_apply_workflow_migration_leaves_no_derived_index(env, capsys):
    """A14：workflow 迁移后不再生成派生索引；task 文件是唯一权威。"""
    consumer = env["consumer"]
    spec, progress = _install_current_workflow_templates(env["src"])

    task_dir = consumer / "docs/tasks/t001_old"
    task_dir.mkdir(parents=True)
    (task_dir / "task.md").write_text(spec, encoding="utf-8")
    old_note = (
        progress.replace("tid: t000", "tid: t001")
        .replace("slug: example_slug", "slug: old")
        .replace("status: backlog", "status: blocked")
        .replace("# Task 进展", "# Task 过程总账")
    )
    (task_dir / "note.md").write_text(old_note, encoding="utf-8")

    assert cmd_apply(Namespace(
        allow_dirty_source=True, skip_tests=True, decisions={},
    )) == 0
    capsys.readouterr()
    assert (task_dir / "progress.md").is_file()
    assert not (task_dir / "note.md").exists()
    assert not (consumer / "docs/tasks_index.json").exists()
    assert not (consumer / "docs/archive/tasks_index.json").exists()
    from repo_task import store as task_store

    tasks = task_store._scan_tasks_in_directories(
        consumer / "docs/tasks", consumer / "docs/archive/tasks"
    )
    assert [(task["tid"], task["status"]) for task in tasks] == [("t001", "active")]


def test_task_migration_only_reads_schema_fields_from_frontmatter(env):
    _, current_task = _install_current_workflow_templates(env['consumer'])
    old_task = current_task.replace('review_limit: 5\n', '').replace('verify_limit: 5\n', '')
    old_task = old_task.replace(
        '无\n\n## Review 处置',
        'status: blocked\nreview_limit: 正文不是字段\nverify_limit: 正文不是字段\n\n## Review 处置',
    )
    migrated = _migrate_task_text(old_task, current_task)
    assert 'status: blocked' in migrated
    assert 'review_limit: 正文不是字段' in migrated
    assert 'verify_limit: 正文不是字段' in migrated
    frontmatter = migrated.split('---', 2)[1]
    assert 'review_limit: "5"' in frontmatter
    assert 'verify_limit: "5"' in frontmatter


def test_force_migrate_workflow_rejects_registered_task_worktree(env):
    consumer = env['consumer']
    current_spec, current_task = _install_current_workflow_templates(consumer)
    task_dir = consumer / 'docs/tasks/t001_old'
    task_dir.mkdir(parents=True)
    (task_dir / 'task.md').write_text(current_spec)
    (task_dir / 'progress.md').write_text(current_task)
    _git(consumer, 'init', '-b', 'main')
    _git(consumer, 'config', 'user.email', 'test@example.com')
    _git(consumer, 'config', 'user.name', 'test')
    _git(consumer, 'add', '-A')
    _git(consumer, 'commit', '-m', 'init')
    worktree = consumer.parent / 'consumer_t001'
    _git(consumer, 'worktree', 'add', '-b', 't001_old', str(worktree))

    with pytest.raises(ctx.SyncError, match='必须先完成或 rewind'):
        force_migrate_workflow_tasks(set())


def _commit_consumer_baseline(consumer):
    _git(consumer, 'config', 'user.email', 'test@example.com')
    _git(consumer, 'config', 'user.name', 'test')
    _git(consumer, 'add', '-A')
    _git(consumer, 'commit', '-m', 'baseline')


def _task_branch(consumer, tid, slug, status, *, directory='docs/tasks'):
    """建 task 分支：progress.md 状态为 status，目录位于 directory；回到 main。"""
    branch = f'{tid}_{slug}'
    _git(consumer, 'checkout', '-b', branch)
    task_dir = consumer / directory / branch
    task_dir.mkdir(parents=True)
    (task_dir / 'progress.md').write_text(
        f'---\ntid: "{tid}"\nslug: "{slug}"\nstatus: "{status}"\n---\n正文\n', encoding='utf-8',
    )
    _git(consumer, 'add', '-A')
    _git(consumer, 'commit', '-m', f'{branch} {status}')
    _git(consumer, 'checkout', 'main')
    return branch


@pytest.mark.parametrize('directory', ['docs/tasks', 'docs/archive/tasks'])
def test_workflow_migration_blocks_unintegrated_done_branch(env, directory):
    consumer = env['consumer']
    _install_current_workflow_templates(consumer)
    _commit_consumer_baseline(consumer)
    branch = _task_branch(consumer, 't001', 'old', 'done', directory=directory)

    _, _, errors = workflow_migration_status(env['src'])
    assert any(branch in error and 'integrate' in error for error in errors)
    with pytest.raises(ctx.SyncError, match=f'{branch}.*integrate'):
        force_migrate_workflow_tasks(set())

    # 合入默认分支后放行
    _git(consumer, 'merge', '--no-ff', '-m', f'merge({branch})', branch)
    _, _, errors = workflow_migration_status(env['src'])
    assert errors == []
    force_migrate_workflow_tasks(set())


@pytest.mark.parametrize('status', ['active', 'backlog'])
def test_workflow_migration_ignores_unmerged_non_done_branch(env, status):
    consumer = env['consumer']
    _install_current_workflow_templates(consumer)
    _commit_consumer_baseline(consumer)
    _task_branch(consumer, 't001', 'old', status)

    _, _, errors = workflow_migration_status(env['src'])
    assert errors == []
    force_migrate_workflow_tasks(set())


def test_workflow_migration_lists_every_unintegrated_done_branch(env):
    consumer = env['consumer']
    _install_current_workflow_templates(consumer)
    _commit_consumer_baseline(consumer)
    first = _task_branch(consumer, 't001', 'old', 'done')
    second = _task_branch(consumer, 't002', 'newer', 'done')

    with pytest.raises(ctx.SyncError) as raised:
        force_migrate_workflow_tasks(set())
    assert first in str(raised.value) and second in str(raised.value)


def test_migrate_conventions_updates_atomic_write_preserving_custom_content(env, monkeypatch):
    consumer = env['consumer']
    conv_file = consumer / "docs/blueprint/conventions.md"
    conv_file.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ctx, "CONVENTIONS_PATH", conv_file)

    original_text = (
        "# 约定\n\n"
        "## 项目自定义约定\n"
        "- 保持现有 React 风格。\n\n"
        "### 原子写\n"
        "- 写权威/派生状态数据走 tmp 文件 + fsync + os.replace 原子写（.repo_template/scripts/task.py 的 _atomic_write_text），防中断半写。"
        "t063 曾实现。脚本层跨模块复用时 from task import _atomic_write_text（task.py 有 __main__ guard，import 安全）。\n"
    )
    conv_file.write_text(original_text, encoding="utf-8")

    assert conventions_migration_status() == [ctx._rel(conv_file)]

    changed = set()
    reports = migrate_conventions(changed)
    assert len(reports) == 1
    assert conv_file in changed

    migrated_text = conv_file.read_text(encoding="utf-8")
    assert "保持现有 React 风格" in migrated_text
    assert "t063 曾实现" in migrated_text
    assert "from repo_task.documents import atomic_write_text" in migrated_text
    assert "_atomic_write_text" not in migrated_text
    assert "from task import" not in migrated_text

    # 幂等：再次执行无迁移
    assert conventions_migration_status() == []
    assert migrate_conventions(set()) == []


def _write_specs_index(consumer, text):
    index = consumer / "docs/specs_index.md"
    index.write_text(text, encoding="utf-8")
    return index


def test_migrate_specs_index_drops_empty_supersedes_column(env, capsys):
    consumer = env["consumer"]
    (consumer / "docs/specs").mkdir()
    (consumer / "docs/specs/feature.md").write_text("# feature\n", encoding="utf-8")
    index = _write_specs_index(consumer, (
        "# 当前生效 spec 清单\n\n"
        "在表即生效。每个 task 收尾时写入或更新对应行（须已过黑盒）；废弃时整行删除，spec 移入 `docs/archive/specs/`。历史清单由 `docs/archive/specs/` 目录承载。替代旧需求时在 `supersedes` 列填旧 slug；无则填 `-`。\n\n"
        "|slug|task 清单|最后更新时间|supersedes|\n"
        "|---|---|---|---|\n"
        "|feature|t001|2026-10-24|-|\n"
    ))
    assert specs_index_migration_status() == [ctx._rel(index)]

    assert cmd_apply(Namespace(allow_dirty_source=True, skip_tests=True, decisions={})) == 0
    text = index.read_text(encoding="utf-8")
    assert "在表即生效" in text
    assert "历史清单由" in text
    assert "替代旧需求时" not in text
    assert "supersedes" not in text
    assert "|slug|task 清单|最后更新时间|" in text
    assert "|feature|t001|2026-10-24|" in text
    assert specs_index_report() == []
    assert ctx._rel(index) in capsys.readouterr().out


def test_migrate_specs_index_removes_legacy_note_on_three_column_table(env):
    consumer = env["consumer"]
    (consumer / "docs/specs").mkdir()
    (consumer / "docs/specs/feature.md").write_text("# feature\n", encoding="utf-8")
    index = _write_specs_index(consumer, (
        "# 当前生效 spec 清单\n\n"
        "在表即生效。每个 task 收尾时写入或更新对应行（须已过黑盒）；废弃时整行删除，spec 移入 `docs/archive/specs/`。历史清单由 `docs/archive/specs/` 目录承载。替代旧需求时在 `supersedes` 列填旧 slug；无则填 `-`。\n\n"
        "|slug|task 清单|最后更新时间|\n"
        "|---|---|---|\n"
        "|feature|t001|2026-10-24|\n"
    ))
    assert specs_index_migration_status() == [ctx._rel(index)]
    changed = set()
    assert len(migrate_specs_index(changed)) == 1
    text = index.read_text(encoding="utf-8")
    assert "替代旧需求时" not in text
    assert "在表即生效" in text
    assert "|slug|task 清单|最后更新时间|" in text
    assert "|feature|t001|2026-10-24|" in text
    assert specs_index_report() == []


def test_migrate_specs_index_keeps_column_with_real_supersedes_data(env):
    consumer = env["consumer"]
    before = (
        "|slug|task 清单|最后更新时间|supersedes|\n"
        "|---|---|---|---|\n"
        "|feature|t001|2026-10-24|old_slug|\n"
    )
    index = _write_specs_index(consumer, before)
    changed = set()
    assert migrate_specs_index(changed) == []
    assert changed == set()
    assert index.read_text(encoding="utf-8") == before
    assert specs_index_migration_status() == []


# 原 test_apply_reports_remaining_specs_index_violations 固化了已废弃的
# 「specs_index 违规仅打印但 apply 仍返回 0」契约；按 A02 整体替换为阻断并完整回滚测试。


@pytest.mark.parametrize(
    "bad_table",
    [
        # 1. 有真实 supersedes 数据，不可猜测丢列
        "|slug|task 清单|最后更新时间|supersedes|\n|---|---|---|---|\n|feature|t001|2026-10-24|old_slug|\n",
        # 2. 额外自定义列
        "|slug|验证方式|task 清单|最后固化时间|\n|---|---|---|---|\n|feature|API|t001|2026-10-24|\n",
        # 3. 坏 slug（kebab-case）
        "|slug|task 清单|最后更新时间|\n|---|---|---|\n|bad-slug|t001|2026-10-24|\n",
    ],
)
def test_apply_rejects_remaining_specs_index_violations_and_rolls_back(env, capsys, bad_table):
    consumer = env["consumer"]
    (consumer / "docs/specs").mkdir(exist_ok=True)
    (consumer / "docs/specs/feature.md").write_text("# feature\n", encoding="utf-8")
    index = _write_specs_index(consumer, bad_table)
    state_before = env["state_file"].read_bytes()

    assert cmd_apply(Namespace(allow_dirty_source=True, skip_tests=True, decisions={})) == 1
    captured = capsys.readouterr()
    assert "specs_index 契约违规:" in captured.out
    assert "已完整回滚" in captured.err
    assert index.read_text(encoding="utf-8") == bad_table
    assert not (consumer / ".repo_template/scripts/task.py").exists()
    assert not (consumer / ".repo_template/sync_manifest.json").exists()
    assert env["state_file"].read_bytes() == state_before

    # 修正数据后才能完成同步
    _write_specs_index(
        consumer,
        "|slug|task 清单|最后更新时间|\n|---|---|---|\n|feature|t001|2026-10-24|\n",
    )
    assert cmd_apply(Namespace(allow_dirty_source=True, skip_tests=True, decisions={})) == 0
    assert (consumer / ".repo_template/scripts/task.py").exists()


def test_status_and_plan_show_specs_index_contract(env, capsys):
    assert cmd_status(Namespace()) == 0
    assert "specs_index 契约" in capsys.readouterr().out
    assert cmd_plan(Namespace()) == 0
    assert "specs_index 契约" in capsys.readouterr().out


def test_workflow_migration_blocks_when_git_worktree_fails(env, monkeypatch):
    """验证无法查询 git worktree 时拒绝放行 schema 迁移并 fail-closed 报错（Issue #22）。"""
    _install_current_workflow_templates(env["consumer"])
    real_git = sync_git
    def fake_git(repo, *args, **kwargs):
        if "worktree" in args:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="simulated lock error")
        return real_git(repo, *args, **kwargs)

    monkeypatch.setattr("template_sync.workflow_migration._git", fake_git)

    # 1. workflow_migration_status 报告阻塞错误
    _, _, errors = workflow_migration_status(env["src"])
    assert any("无法查询 git worktree" in err for err in errors)

    # 2. force_migrate_workflow_tasks 抛出 SyncError 阻断写盘
    with pytest.raises(ctx.SyncError, match="无法查询 git worktree"):
        force_migrate_workflow_tasks(set())


def test_run_tests_timeout_handling(env, monkeypatch, capsys):
    """验证契约测试超时被捕获、打印明确超时诊断且返回 False 触发回滚（Issue #29）。"""
    def mock_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["pytest"], timeout=120)

    monkeypatch.setattr("template_sync.gates.subprocess", SimpleNamespace(**{**vars(subprocess), "run": mock_run}))
    ret = _run_tests(env["consumer"])
    assert ret is False
    err = capsys.readouterr().err
    assert "契约测试超时" in err



# ---------------------------------------------------------------------------
# 目标软链、registry、legacy、离线源与结构错误（Issue #72）
# ---------------------------------------------------------------------------

def test_sync_file_rejects_symlink_and_broken_symlink_target(env, tmp_path):
    consumer = env["consumer"]
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-safe\n", encoding="utf-8")
    source = env["src"] / "asset.txt"
    source.write_text("template\n", encoding="utf-8")

    link = consumer / "asset.txt"
    link.symlink_to(outside)
    with pytest.raises(ctx.SyncError, match="软链"):
        sync_file(source, link, set())
    assert outside.read_text(encoding="utf-8") == "outside-safe\n"

    broken_target = tmp_path / "missing-dir" / "created.txt"
    broken = consumer / "broken.txt"
    broken.symlink_to(broken_target)
    broken_source = env["src"] / "broken.txt"
    broken_source.write_text("template\n", encoding="utf-8")
    with pytest.raises(ctx.SyncError, match="软链"):
        sync_file(broken_source, broken, set())
    assert not broken_target.exists()


def _install_consumer_skill(consumer: Path, name: str) -> None:
    skill = consumer / ".repo_template/skills" / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: none\n---\nrun\n", encoding="utf-8"
    )


def test_link_skills_backs_up_and_rolls_back_real_directory(env, monkeypatch):
    consumer = env["consumer"]
    _install_consumer_skill(consumer, "task-run")
    (consumer / ".repo_template/skills/registry.json").write_text(
        json.dumps({"skills": {"task-run": {"role": "public"}}}), encoding="utf-8"
    )
    real = consumer / ".claude/skills/task-run"
    real.mkdir(parents=True)
    (real / "sentinel.txt").write_text("keep\n", encoding="utf-8")

    def fail_symlink(*args, **kwargs):
        raise OSError("simulated symlink failure")

    monkeypatch.setattr("template_sync.skills_links.os", SimpleNamespace(**{**vars(os), "symlink": fail_symlink}))
    with pytest.raises(ctx.SyncError, match="link-skills 失败"):
        cmd_link_skills(Namespace())
    monkeypatch.setattr("template_sync.skills_links.os", os)

    assert real.is_dir() and not real.is_symlink()
    assert (real / "sentinel.txt").read_text(encoding="utf-8") == "keep\n"


def test_registry_missing_skill_fails_closed_in_status_and_apply(env):
    consumer = env["consumer"]
    _install_consumer_skill(consumer, "task-run")
    _install_consumer_skill(consumer, "unregistered")
    (consumer / ".repo_template/skills/registry.json").write_text(
        json.dumps({"skills": {"task-run": {"role": "public"}}}), encoding="utf-8"
    )
    with pytest.raises(ctx.SyncError, match="未登记 skill"):
        opencode_commands_status()
    with pytest.raises(ctx.SyncError, match="未登记 skill"):
        sync_opencode_commands(set())


def test_url_status_plan_use_cache_without_pull(env, monkeypatch):
    consumer = env["consumer"]
    cache = consumer / ".scratch/repo_template_sync_src"
    (cache / ".git").mkdir(parents=True)
    (cache / ".repo_template/scripts").mkdir(parents=True)
    (cache / ".repo_template/scripts/task.py").write_text("print('task')\n", encoding="utf-8")
    state = {"template_source": {"kind": "url", "value": "https://example.invalid/repo.git"}}
    calls = []

    class Result:
        returncode = 0
        stdout = "https://example.invalid/repo.git\n"
        stderr = ""

    def fake_git(repo, *args, **kwargs):
        calls.append(args)
        if "pull" in args:
            raise AssertionError("refresh=False 不应 pull")
        return Result()

    monkeypatch.setattr("template_sync.source._git", fake_git)
    assert resolve_src(state, refresh=False) == cache.resolve()
    assert not any("pull" in args for args in calls)


def test_manifest_missing_commit_and_policy_non_object_raise_sync_error(env):
    env["state_file"].unlink()
    manifest = env["consumer"] / ".repo_template/sync_manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(ctx.SyncError, match="resolved_commit"):
        read_state()

    env["state_file"].write_text(json.dumps({
        "template_source": {"kind": "path", "value": str(env["src"])},
        "last_synced_commit": None,
        "user_prompts": [],
    }), encoding="utf-8")
    policy = env["consumer"] / ".repo_template/sync_policy.json"
    policy.write_text("[]", encoding="utf-8")
    with pytest.raises(ctx.SyncError, match="JSON 对象"):
        read_state()


def test_git_timeout_is_reported_as_sync_error(env):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=30)

    old = subprocess.run
    subprocess.run = timeout
    try:
        with pytest.raises(ctx.SyncError, match="超时"):
            sync_git(env["consumer"], "status")
    finally:
        subprocess.run = old


def test_mcp_sanitizes_args_url_and_headers(env):
    servers = {
        "demo": {
            "command": "demo",
            "args": ["--api-key=SECRET-A", "--token", "SECRET-B", "--verbose", "x"],
            "url": "https://user:SECRET-C@example.invalid/path?token=SECRET-D&x=1",
            "headers": {"Authorization": "Bearer SECRET-E", "X-Trace": "keep"},
            "env": {"API_TOKEN": "SECRET-F"},
        }
    }
    clean = _sanitize_mcp_servers(servers)
    text = json.dumps(clean)
    for secret in ("SECRET-A", "SECRET-B", "SECRET-C", "SECRET-D", "SECRET-E", "SECRET-F"):
        assert secret not in text
    assert "--verbose" in clean["demo"]["args"] and "x" in clean["demo"]["args"]
    assert clean["demo"]["headers"]["X-Trace"] == "keep"
    assert "x=1" in clean["demo"]["url"]


def test_mcp_invalid_url_port_raises_sync_error(env):
    bad_url = "https://user:SECRET-C@example.invalid:notaport/mcp"
    with pytest.raises(ctx.SyncError) as direct:
        _sanitize_mcp_servers({"demo": {"url": bad_url}})
    message = str(direct.value)
    assert "demo" in message
    assert "端口" in message
    assert "SECRET-C" not in message
    assert "example.invalid" not in message

    (env["src"] / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"demo": {"url": bad_url}}}), encoding="utf-8"
    )
    with pytest.raises(ctx.SyncError) as merged:
        merge_mcp(env["src"], set())
    assert ".mcp.json" in str(merged.value)
    assert "SECRET-C" not in str(merged.value)


def test_dirty_tree_rejects_secret_files(env):
    (env["src"] / ".repo_template/dummy.pem").write_text("secret\n", encoding="utf-8")
    with pytest.raises(ctx.SyncError, match="不可分发"):
        _assert_distributable_tree(env["src"])
    _git(env["src"], "add", "-A")
    _git(env["src"], "commit", "-m", "add secret")
    with pytest.raises(ValueError, match="不可分发"):
        export_template(env["src"], env["consumer"] / ".scratch/export-target")


def test_prettierignore_header_marks_conditional_entries(env):
    """旧的单行豁免头升级为说明条件性条目的两行头（Issue #83）。"""
    ignore = env["consumer"] / ".prettierignore"
    ignore.write_text(_PRETTIERIGNORE_OLD_HEADER + "\n", encoding="utf-8")
    merge_prettierignore(set())
    text = ignore.read_text(encoding="utf-8")
    assert _PRETTIERIGNORE_OLD_HEADER not in text
    assert "按需生成" in text
    assert ".repo_template/sync_manifest.json" in text
