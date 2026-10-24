"""repo_sync.py 机械化同步器的 real-git 测试。

SRC（模板）与 CONSUMER（消费项目）都在 tmp_path 下构造真实目录，monkeypatch
模块级路径常量（CONSUMER / STATE_PATH / SKILLS_AGENTS / SKILLS_CLAUDE）重绑定，
覆盖 state 字段级原子更新、user_prompts 管理、硬同步覆盖与多余删除、skill 覆盖
与 sync_state.json 保护、软链、.gitignore / .prettierignore / MCP 机械合并、apply 全流程与改动清单。
"""

import json
import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import repo_sync as rs
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
    (toolkit / "docs/task_template/note.md").write_text("---\ntid: t000\n---\n## 实施笔记\n无\n")
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

    monkeypatch.setattr(rs, "CONVENTIONS_PATH", consumer / "docs/blueprint/conventions.md")
    monkeypatch.setattr(rs, "SOURCE_OVERRIDE", None)
    monkeypatch.setattr(rs, "CHECKOUT_OVERRIDE", None)
    monkeypatch.setattr(rs, "CONSUMER", consumer)
    monkeypatch.setattr(rs, "STATE_PATH", state_file)
    monkeypatch.setattr(rs, "LEGACY_STATE_PATH", consumer / ".agents/skills/repo-template-sync/sync_state.json")
    monkeypatch.setattr(rs, "SKILLS_SRC", consumer / ".repo_template/skills")
    monkeypatch.setattr(rs, "SKILLS_AGENTS", consumer / ".agents/skills")
    monkeypatch.setattr(rs, "SKILLS_CLAUDE", consumer / ".claude/skills")
    monkeypatch.setattr(rs, "COMMANDS_OPENCODE", consumer / ".opencode/commands")
    return {"src": src, "consumer": consumer, "head": head, "state_file": state_file}


def _add_prompt(text: str, tags: str = "") -> None:
    rs.cmd_prompt(Namespace(action="add", text=text, tags=tags, id=None))


# ---------------------------------------------------------------------------
# state 字段级原子更新
# ---------------------------------------------------------------------------

def test_sync_state_atomic_update_preserves_unknown_keys(env):
    state_file = env["state_file"]
    data = json.loads(state_file.read_text())
    data["future_field"] = {"x": 1}
    data["nested_unknown"] = ["a", "b"]
    state_file.write_text(json.dumps(data))

    rs.write_state({**rs.read_state(), "last_synced_at": "2026-01-01T00:00:00+08:00"})
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
    assert len(rs.read_state()["user_prompts"]) == 1
    # 同 tag 新条 supersede 旧条
    _add_prompt("新版 .env 指令", ".env")
    state = rs.read_state()
    assert len(state["user_prompts"]) == 2
    assert state["user_prompts"][0]["revoked"] is True
    assert state["user_prompts"][1]["revoked"] is False
    # 不同 tag 不 supersede
    _add_prompt("MCP 保留", "mcp")
    state = rs.read_state()
    assert state["user_prompts"][1]["revoked"] is False
    assert len(state["user_prompts"]) == 3
    # revoke
    rs.cmd_prompt(Namespace(action="revoke", text=None, tags=None, id=2))
    assert rs.read_state()["user_prompts"][2]["revoked"] is True
    assert len(rs.active_prompts(rs.read_state())) == 1


def test_prompt_substrings_only_active(env):
    _add_prompt(".env 不要 ignore", ".gitignore,.env")
    _add_prompt("MCP 保留", "mcp")
    assert set(rs.prompt_substrings(rs.read_state())) == {".gitignore", ".env", "mcp"}
    rs.cmd_prompt(Namespace(action="revoke", text=None, tags=None, id=1))
    assert set(rs.prompt_substrings(rs.read_state())) == {".gitignore", ".env"}


def test_corrupt_sync_state_raises_sync_error_with_recovery_guidance(env):
    """验证 sync_state.json 损坏或格式错误时抛出 SyncError 并附带恢复指引（Issue #49）。"""
    consumer = env["consumer"]
    state_file = consumer / ".repo_template/sync_state.json"

    # 1. 截断/非法 JSON
    state_file.write_text("{corrupt: json truncated...", encoding="utf-8")
    with pytest.raises(rs.SyncError, match="损坏"):
        rs.read_state()

    # 2. 非 JSON 对象（如 JSON 数组）
    state_file.write_text("[1, 2, 3]\n", encoding="utf-8")
    with pytest.raises(rs.SyncError, match="格式错误.*预期为 JSON 对象"):
        rs.read_state()


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
    rs.sync_dir(src / ".repo_template", consumer / ".repo_template", changed)

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
    rs.sync_file(src / ".repo_template/docs/spike_report_template.md", dst, changed)
    assert dst.read_text() == "# report\n"
    # src 缺失 → 删 dst
    orphan = consumer / ".claude/hooks/merge_guard.py"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("x\n")
    changed = set()
    rs.sync_file(src / ".claude/hooks/nonexistent_guard.py", orphan, changed)
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
    with pytest.raises(rs.SyncError, match="目录软链.*拒绝"):
        rs.sync_dir(src_dir, dst_link, changed)

    # 哨兵目录内容完好
    assert (sentinel / "outside.txt").read_text() == "precious\n"
    assert not (sentinel / "file.txt").exists()



# ---------------------------------------------------------------------------
# skill 覆盖 + sync_state.json 保护 + 软链
# ---------------------------------------------------------------------------

def test_skill_override_preserves_sync_state(env):
    src, consumer = env["src"], env["consumer"]
    dst = consumer / ".repo_template/skills/task-run"
    dst.mkdir(parents=True)
    (dst / "SKILL.md").write_text("OLD VERSION\n")
    protected = dst / "sync_state.json"
    protected.write_text('{"keep": true}\n')

    changed: set[Path] = set()
    rs.sync_skill(src / ".repo_template/skills/task-run", dst, changed)
    assert (dst / "SKILL.md").read_text().startswith("---\nname: task-run")
    assert protected.read_text() == '{"keep": true}\n'
    assert protected not in changed


def test_repair_symlinks(env):
    src, consumer = env["src"], env["consumer"]
    dst = consumer / ".repo_template/skills/task-run"
    dst.mkdir(parents=True)
    (dst / "SKILL.md").write_text("run\n")
    changed: set[Path] = set()
    reports = rs.repair_symlinks(changed)
    link = consumer / ".claude/skills/task-run"
    assert link.is_symlink()
    assert link.resolve() == dst.resolve()
    agents_link = consumer / ".agents/skills/task-run"
    assert agents_link.is_symlink()
    assert agents_link.resolve() == dst.resolve()
    assert not reports
    changed = set()
    rs.repair_symlinks(changed)
    assert not changed
    foreign_src = consumer / ".repo_template/skills/foreign-skill"
    foreign_src.mkdir(parents=True)
    foreign = consumer / ".claude/skills/foreign-skill"
    foreign.write_text("not a symlink\n")
    changed = set()
    reports = rs.repair_symlinks(changed)
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
    reports = rs.repair_symlinks(changed)

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
    rs.repair_symlinks(changed)
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

    # 校验 symlink_status 报告
    status = rs.symlink_status()
    assert {"name": "active-skill", "state": "ok"} in status
    assert {"name": "old-skill", "state": "cleanup"} in status

    # 执行 repair_symlinks
    changed = set()
    reports = rs.repair_symlinks(changed)

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


def test_skill_status_reports_stale_real_dir(env):
    src, consumer = env["src"], env["consumer"]
    dst = consumer / ".repo_template/skills/task-run"
    dst.mkdir(parents=True)
    (dst / "SKILL.md").write_text("run\n")
    stale = consumer / ".agents/skills/task-run"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("stale\n")

    items = rs.skill_status(src)
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
    reports = rs.sync_opencode_commands(changed)
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
    assert rs.sync_opencode_commands(changed) == []
    assert not changed

    # 带标记残留删、手写保留、手写同名跳过并报告
    (consumer / ".opencode/commands/old-x.md").write_text(
        "---\ndescription: x\n---\n> Auto-generated by repo_sync.py from skill 'old-x'.\n"
    )
    (consumer / ".opencode/commands/mine.md").write_text("---\ndescription: mine\n---\ncustom\n")
    (consumer / ".opencode/commands/task-run.md").write_text("---\ndescription: mine-takeover\n---\ncustom\n")
    changed = set()
    reports = rs.sync_opencode_commands(changed)
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
    items = rs.opencode_commands_status()
    sample_item = next(it for it in items if it["name"] == "task-sample")
    assert sample_item["state"] == "create"

    # 2. 生成命令薄层后状态应为 same
    changed = set()
    rs.sync_opencode_commands(changed)
    items = rs.opencode_commands_status()
    sample_item = next(it for it in items if it["name"] == "task-sample")
    assert sample_item["state"] == "same"

    # 3. 仅修改 skill description，不执行 link/sync
    (skill_dir / "SKILL.md").write_text("---\nname: task-sample\ndescription: Updated new description\n---\nbody\n")

    # 4. opencode_commands_status 必须检出 update 漂移
    items = rs.opencode_commands_status()
    sample_item = next(it for it in items if it["name"] == "task-sample")
    assert sample_item["state"] == "update"
    assert "漂移" in sample_item["desc"]

    # 5. cmd_status 能打印提示
    rs.cmd_status(Namespace())
    out = capsys.readouterr().out
    assert "opencode 命令薄层" in out
    assert "update" in out and "/task-sample" in out

    # 6. sync 之后恢复一致
    changed = set()
    rs.sync_opencode_commands(changed)
    items = rs.opencode_commands_status()
    sample_item = next(it for it in items if it["name"] == "task-sample")
    assert sample_item["state"] == "same"



def test_shipped_skills_have_real_descriptions():
    """模板分发的每个 skill 必须有真实 description（命令文案与按描述路由依赖）。"""
    skills_dir = Path(__file__).resolve().parents[1] / "skills"
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
    info = rs.merge_gitignore(src, rs.prompt_substrings(rs.read_state()), changed)
    text = gi.read_text()
    assert "node_modules/" in text              # 消费独有保留
    assert ".env" not in text.splitlines()      # prompt 禁止 ignore 行被删
    assert "*.log" in text                      # 模板独有追加
    assert info["added"] == ["*.log"]
    assert info["removed"] == [".env"]
    assert gi in changed


def test_prettierignore_merge_creates_and_dedupes(env):
    consumer = env["consumer"]
    pi = consumer / ".prettierignore"

    changed: set[Path] = set()
    info = rs.merge_prettierignore(changed)
    text = pi.read_text()
    for rule in rs.PRETTIERIGNORE_TEMPLATE_RULES:
        assert rule in text.splitlines()
    assert pi in changed

    # 二次合并：去重，无改动
    changed = set()
    info = rs.merge_prettierignore(changed)
    assert info["added"] == []
    assert changed == set()
    assert text == pi.read_text()


def test_prettierignore_preserves_consumer_rules(env):
    consumer = env["consumer"]
    pi = consumer / ".prettierignore"
    pi.write_text("pnpm-lock.yaml\n*.md\n")

    changed: set[Path] = set()
    rs.merge_prettierignore(changed)
    lines = pi.read_text().splitlines()
    assert "pnpm-lock.yaml" in lines             # 消费独有保留
    assert "*.md" in lines
    for rule in rs.PRETTIERIGNORE_TEMPLATE_RULES:
        assert rule in lines
    assert pi in changed


def test_prettierignore_covers_generated_artifacts(env):
    # Issue #3 回访：同步状态、派生索引、任务产物缺一即红门禁
    rules = set(rs.PRETTIERIGNORE_TEMPLATE_RULES)
    assert ".repo_template/sync_state.json" in rules
    assert "docs/tasks_index.json" in rules
    assert "docs/archive/tasks_index.json" in rules
    assert "docs/**/handoff.json" in rules


def test_detect_json_indent(env):
    assert rs._detect_json_indent('{\n  "a": 1\n}\n') == 2
    assert rs._detect_json_indent('{\n    "a": 1\n}\n') == 4
    assert rs._detect_json_indent('{\n\t"a": 1\n}\n') == "\t"
    assert rs._detect_json_indent('{"a": 1}') == 2
    assert rs._detect_json_indent('') == 2


def test_mcp_merge_preserves_consumer_indent(env):
    src, consumer = env["src"], env["consumer"]
    (src / ".mcp.json").write_text(json.dumps({"mcpServers": {"tpl-server": {"command": "x"}}}))
    dmcp = consumer / ".mcp.json"
    dmcp.write_text('{\n    "mcpServers": {\n        "consumer-server": {"command": "y"}\n    }\n}\n')

    changed: set[Path] = set()
    rs.merge_mcp(src, changed)
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
                {'type': 'command', 'command': rs._RETIRED_MERGE_GUARD_COMMAND},
                {'type': 'command', 'command': 'echo keep'},
            ],
        }]},
    }, ensure_ascii=False, indent=4) + "\n")
    changed: set[Path] = set()
    rs.repair_symlinks(changed)
    raw = settings.read_text()
    assert json.loads(raw)["hooks"]["PreToolUse"][0]["hooks"] == [
        {"type": "command", "command": "echo keep"},
    ]
    assert '\n    "hooks"' in raw  # 退役项移除，但 4 空格体例保留
    assert settings in changed


def test_retired_template_warnings(env):
    consumer = env["consumer"]
    assert rs.retired_template_warnings() == []
    stale = consumer / ".github/workflows/repo-template-ci.yml"
    stale.parent.mkdir(parents=True)
    stale.write_text("name: repo-template\n")
    warnings = rs.retired_template_warnings()
    assert len(warnings) == 1
    assert ".github/workflows/repo-template-ci.yml" in warnings[0]


def test_mcp_merge_keywise(env):
    src, consumer = env["src"], env["consumer"]
    (src / ".mcp.json").write_text(json.dumps({"mcpServers": {"tpl-server": {"command": "x"}}}))
    dmcp = consumer / ".mcp.json"
    dmcp.write_text(json.dumps({"mcpServers": {"consumer-server": {"command": "y"}}}))

    changed: set[Path] = set()
    rs.merge_mcp(src, changed)
    ddata = json.loads(dmcp.read_text())
    assert "tpl-server" in ddata["mcpServers"]
    assert "consumer-server" in ddata["mcpServers"]
    # 已有键不覆盖（禁冲密钥）
    (src / ".mcp.json").write_text(json.dumps({"mcpServers": {"tpl-server": {"command": "OVERWRITE"}}}))
    changed = set()
    rs.merge_mcp(src, changed)
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
    reports = rs.merge_mcp(src, changed)

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
    assert rs._apply_agents_sectioned(src, changed) is True
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
    assert rs._apply_agents_sectioned(src, set()) is False


# ---------------------------------------------------------------------------
# apply 全流程 + 改动清单
# ---------------------------------------------------------------------------

def test_apply_flow_writes_and_advances_state(env, capsys):
    src, consumer, head = env["src"], env["consumer"], env["head"]
    old_agents = consumer / "AGENTS.md"
    old_agents.write_text("CONSUMER OLD\n")

    rs.cmd_apply(Namespace(allow_dirty_source=True, decision=["AGENTS.md:update"], skip_tests=True, decisions={"AGENTS.md": "update"}))
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
    state = rs.read_state()
    assert state["last_synced_commit"] == head
    assert state["last_synced_at"]
    # 改动清单输出（点名 add 依据）
    assert ".repo_template/scripts/task.py" in out
    assert "AGENTS.md" in out


def test_apply_skips_unresolved_shared_unit(env):
    src, consumer = env["src"], env["consumer"]
    old_agents = consumer / "AGENTS.md"
    old_agents.write_text("CONSUMER OLD\n")

    rs.cmd_apply(Namespace(allow_dirty_source=True, decision=[], skip_tests=True, decisions={}))
    # 无决策 → AGENTS.md 不动（ask_user 语义）
    assert (consumer / "AGENTS.md").read_text() == "CONSUMER OLD\n"


def test_apply_src_dirty_does_not_advance_commit(env):
    src, consumer = env["src"], env["consumer"]
    (src / ".repo_template/scripts/dirty_extra.py").write_text("x\n")  # SRC 未提交改动
    _add_prompt("跳过测试", "")
    rs.cmd_apply(Namespace(allow_dirty_source=True, decision=[], skip_tests=True, decisions={}))
    state = rs.read_state()
    assert state["last_synced_commit"] is None
    assert state["last_synced_at"]


def test_apply_test_failure_rolls_back_keeps_backup_and_prints_changed(env, monkeypatch, capsys):
    """契约测试失败：完整回滚、保留备份、输出 changed 清单与可操作错误，state 不推进。"""
    src, consumer = env["src"], env["consumer"]
    old_agents = consumer / "AGENTS.md"
    old_agents.write_text("CONSUMER OLD\n")

    monkeypatch.setattr(rs, "_run_tests", lambda c: False)
    rc = rs.cmd_apply(
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
    assert rs.read_state()["last_synced_commit"] is None


def test_sync_file_rejects_directory_target(env):
    """目标同名目录：拒绝写入目录内部，保持目录内容原样。"""
    src, consumer = env["src"], env["consumer"]
    target = consumer / ".md_kx.toml"
    target.mkdir(parents=True)
    (target / "user_note.txt").write_text("keep\n", encoding="utf-8")

    with pytest.raises(rs.SyncError, match="是目录"):
        rs.sync_file(src / ".md_kx.toml", target, set())
    assert (target / "user_note.txt").is_file()
    assert not (target / ".md_kx.toml").exists()


def test_sync_dir_rejects_file_target(env):
    """目标同名为文件：明确报错停止，不静默覆盖。"""
    src, consumer = env["src"], env["consumer"]
    target = consumer / ".repo_template" / "docs"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("i am a file\n", encoding="utf-8")

    with pytest.raises(rs.SyncError, match="类型不符"):
        rs.sync_dir(src / ".repo_template/docs", target, set())
    assert target.read_text() == "i am a file\n"


# ---------------------------------------------------------------------------
# 边界：同一性拒绝 / init
# ---------------------------------------------------------------------------

def test_assert_not_self(env, monkeypatch):
    src = env["src"]
    # 无关路径 → 不拒绝
    other = env["consumer"].parent / "unrelated"
    rs.assert_not_self(other)
    # CONSUMER 指向 src 自身 → 拒绝
    monkeypatch.setattr(rs, "CONSUMER", src)
    with pytest.raises(rs.SyncError):
        rs.assert_not_self(src)


def test_init_writes_template_source(env, monkeypatch):
    consumer, state_file = env["consumer"], env["state_file"]
    monkeypatch.setattr(rs, "CONSUMER", consumer)
    monkeypatch.setattr(rs, "STATE_PATH", state_file)
    monkeypatch.setattr(rs, "LEGACY_STATE_PATH", consumer / ".agents/skills/repo-template-sync/sync_state.json")
    rs.cmd_init(Namespace(source=str(env["src"])))
    state = rs.read_state()
    assert state["template_source"]["kind"] == "path"
    assert state["template_source"]["value"] == str(env["src"])


def test_init_migrates_legacy_state_and_prompts(env, monkeypatch):
    consumer, state_file = env["consumer"], env["state_file"]
    state_file.unlink()
    legacy = consumer / ".agents/skills/repo-template-sync/sync_state.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({
        "template_source": {"kind": "path", "value": "/old/path"},
        "last_synced_commit": "deadbeef",
        "last_synced_at": "2026-01-01T00:00:00+08:00",
        "user_prompts": [{"text": "保留我", "tags": ["x"], "revoked": False}],
    }))
    monkeypatch.setattr(rs, "STATE_PATH", state_file)
    monkeypatch.setattr(rs, "LEGACY_STATE_PATH", legacy)
    rs.cmd_init(Namespace(source=str(env["src"])))
    state = rs.read_state()
    # prompt 与审计字段随迁移保留；template_source 以本次 --source 为准
    assert state["user_prompts"][0]["text"] == "保留我"
    assert state["last_synced_commit"] == "deadbeef"
    assert state["template_source"]["value"] == str(env["src"])
    assert not legacy.exists()


def test_resolve_src_rejects_invalid(env):
    state = rs.read_state()
    state["template_source"]["value"] = "/nonexistent/not_a_template"
    with pytest.raises(rs.SyncError):
        rs.resolve_src(state)


def test_source_identity_rejects_option_like_string():
    """验证 source 参数为 option-like 格式时被明确拒绝（Issue #40）。"""
    with pytest.raises(rs.SyncError, match="option 格式参数"):
        rs.source_identity("--upload-pack")
    with pytest.raises(rs.SyncError, match="option 格式参数"):
        rs.source_identity("-o")
    with pytest.raises(rs.SyncError, match="不能为空"):
        rs.source_identity("")


def test_init_rejects_non_template_local_directory(env, tmp_path):
    """验证 init 传入非模板的普通目录时拒绝并保留原有有效 state（Issue #23）。"""
    not_a_template = tmp_path / "empty_dir"
    not_a_template.mkdir()

    orig_state = rs.read_state()
    with pytest.raises(rs.SyncError, match="不是有效模板源"):
        rs.source_identity(str(not_a_template))

    # 验证原有 state 中的 template_source 保持不变
    assert rs.read_state()["template_source"] == orig_state["template_source"]


def test_mcp_merge_tolerates_non_dict_structure(env, capsys):
    """验证遇到合法 JSON 但非对象的错误 MCP 结构时不崩溃并告警跳过（Issue #26）。"""
    src, consumer = env["src"], env["consumer"]
    (src / ".mcp.json").write_text(json.dumps({"mcpServers": {"tpl": {"command": "x"}}}))
    dmcp = consumer / ".mcp.json"

    # 1. 消费端为数组
    dmcp.write_text("[]\n", encoding="utf-8")
    changed = set()
    rs.merge_mcp(src, changed)
    err = capsys.readouterr().err
    assert "非 JSON 对象" in err
    assert dmcp not in changed

    # 2. 消费端 mcpServers 为数组
    dmcp.write_text(json.dumps({"mcpServers": [1, 2, 3]}), encoding="utf-8")
    changed = set()
    rs.merge_mcp(src, changed)
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
    monkeypatch.setattr(rs, "CONSUMER", consumer)

    assert rs.cmd_install_hooks(Namespace()) == 0
    out = capsys.readouterr().out
    assert "已设为" in out
    assert _git(consumer, "config", "--get", "core.hooksPath").stdout.strip() \
        == ".repo_template/hooks"

    # 幂等：已指向目标 → no-op
    assert rs.cmd_install_hooks(Namespace()) == 0
    assert "无需改动" in capsys.readouterr().out


def test_install_hooks_refuses_foreign_path(tmp_path, monkeypatch, capsys):
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _git(consumer, "init", "-b", "main")
    _git(consumer, "config", "core.hooksPath", ".git/hooks")
    _ensure_hook(consumer)
    monkeypatch.setattr(rs, "CONSUMER", consumer)

    assert rs.cmd_install_hooks(Namespace()) == 1
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
    monkeypatch.setattr(rs, "CONSUMER", consumer)

    assert rs.cmd_install_hooks(Namespace(force=True)) == 0
    assert _git(consumer, "config", "--get", "core.hooksPath").stdout.strip() \
        == ".repo_template/hooks"


def test_install_hooks_nested_product_uses_toplevel_relative(tmp_path, monkeypatch, capsys):
    """工厂仓：git 顶层在上、产物在 repo/ 时，hooksPath 须相对顶层。"""
    factory = tmp_path / "factory"
    product = factory / "repo"
    product.mkdir(parents=True)
    _git(factory, "init", "-b", "main")
    _ensure_hook(product)
    monkeypatch.setattr(rs, "CONSUMER", product)

    assert rs.cmd_install_hooks(Namespace()) == 0
    assert _git(factory, "config", "--get", "core.hooksPath").stdout.strip() \
        == "repo/.repo_template/hooks"
    assert "repo/.repo_template/hooks" in capsys.readouterr().out


def test_install_hooks_rejects_missing_or_nonexec(tmp_path, monkeypatch, capsys):
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _git(consumer, "init", "-b", "main")
    monkeypatch.setattr(rs, "CONSUMER", consumer)

    assert rs.cmd_install_hooks(Namespace()) == 1
    assert "缺 hook 脚本" in capsys.readouterr().err

    _ensure_hook(consumer, executable=False)
    assert rs.cmd_install_hooks(Namespace()) == 1
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
    reports = rs.repair_symlinks(changed)
    assert not guard.exists() and not guard.is_symlink()
    assert guard in changed
    assert any('已退役并移除' in line for line in reports)


def test_repair_symlinks_preserves_manual_merge_guard_file(env):
    consumer = env['consumer']
    guard = consumer / '.claude/hooks/merge_guard.py'
    guard.parent.mkdir(parents=True)
    guard.write_text('manual\n')
    changed: set[Path] = set()
    rs.repair_symlinks(changed)
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
                {'type': 'command', 'command': rs._RETIRED_MERGE_GUARD_COMMAND},
                {'type': 'command', 'command': 'python3 scripts/custom_merge_guard.py'},
            ],
        }]},
    }))
    guard = consumer / '.claude/hooks/merge_guard.py'
    guard.parent.mkdir(parents=True, exist_ok=True)
    guard.write_text('manual\n')
    changed: set[Path] = set()
    rs.repair_symlinks(changed)
    data = json.loads(settings.read_text())
    commands = [entry['command'] for entry in data['hooks']['PreToolUse'][0]['hooks']]
    assert commands == [rs._RETIRED_MERGE_GUARD_COMMAND, 'python3 scripts/custom_merge_guard.py']
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
                {'type': 'command', 'command': rs._RETIRED_MERGE_GUARD_COMMAND},
                {'type': 'command', 'command': 'python3 scripts/custom_merge_guard.py'},
            ],
        }]},
    }))
    changed: set[Path] = set()
    rs.repair_symlinks(changed)
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
    rs.repair_symlinks(changed)
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
    with pytest.raises(rs.SyncError, match='不能安全移除'):
        rs.repair_symlinks(set())
    assert guard.is_symlink()


def _install_current_workflow_templates(consumer: Path) -> tuple[str, str]:
    source = SCRIPTS_DIR.parent / 'docs/task_template'
    target = consumer / '.repo_template/docs/task_template'
    target.mkdir(parents=True, exist_ok=True)
    task = (source / 'task.md').read_text()
    note = (source / 'note.md').read_text()
    (target / 'task.md').write_text(task)
    (target / 'note.md').write_text(note)
    return task, note


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


def test_legacy_full_spec_schema_migrates_as_heading_groups(env):
    current_spec, _ = _install_current_workflow_templates(env['consumer'])
    legacy = (Path(__file__).parent / 'fixtures/spec_pre_workflow_simplification.md').read_text()
    migrated = rs._replace_guide_blocks(legacy, current_spec)
    current_blocks = rs._guide_blocks_by_heading(current_spec)
    migrated_blocks = rs._guide_blocks_by_heading(migrated)
    assert migrated_blocks == current_blocks
    assert rs._replace_guide_blocks(migrated, current_spec) == migrated
    assert '已判定不写测试的分支与原因' not in migrated
    assert 'mock 边界、fixture 来源、断言目标' not in migrated


def test_missing_guide_block_is_restored_and_reported(env):
    consumer = env['consumer']
    current_spec, current_task = _install_current_workflow_templates(consumer)
    _install_current_workflow_templates(env['src'])
    missing = current_spec.replace(
        rs._guide_blocks_by_heading(current_spec)['验收标准'][0] + '\n\n', '', 1
    )
    task_dir = consumer / 'docs/tasks/t001_missing'
    task_dir.mkdir(parents=True)
    (task_dir / 'task.md').write_text(missing)
    (task_dir / 'note.md').write_text(current_task)
    migrations, worktrees, errors = rs.workflow_migration_status(env['src'])
    assert 'docs/tasks/t001_missing/task.md' in migrations
    assert worktrees == [] and errors == []
    rs.force_migrate_workflow_tasks(set())
    assert (task_dir / 'task.md').read_text() == current_spec


def test_migration_status_reports_malformed_spec_without_raising(env):
    consumer = env['consumer']
    current_spec, current_task = _install_current_workflow_templates(consumer)
    _install_current_workflow_templates(env['src'])
    malformed = current_spec.replace(rs._GUIDE_CLOSE, '', 1)
    task_dir = consumer / 'docs/tasks/t001_malformed'
    task_dir.mkdir(parents=True)
    (task_dir / 'task.md').write_text(malformed)
    (task_dir / 'note.md').write_text(current_task)
    migrations, worktrees, errors = rs.workflow_migration_status(env['src'])
    assert migrations == [] and worktrees == []
    assert len(errors) == 1 and 't001_malformed/task.md' in errors[0]
    assert rs.cmd_status(Namespace()) == 0
    assert rs.cmd_plan(Namespace()) == 0
    with pytest.raises(rs.SyncError, match='无法安全迁移'):
        rs.force_migrate_workflow_tasks(set())


def test_workflow_migrations_are_idempotent_for_current_schema(env):
    current_spec, current_task = _install_current_workflow_templates(env['consumer'])
    assert rs._replace_guide_blocks(current_spec, current_spec) == current_spec
    assert rs._migrate_task_text(current_task, current_task) == current_task


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

    # 迁移前：store.py 扫描会因为缺少 note.md 拒绝扫描
    from repo_task import store as task_store, context as task_ctx
    with pytest.raises(task_ctx.TaskDataError, match="缺 note.md"):
        task_store._scan_tasks_in_directories(consumer / 'docs/tasks', consumer / 'docs/archive/tasks')

    # 迁移状态应检出活跃和归档下的旧文件
    migrations, worktrees, errors = rs.workflow_migration_status(env['src'])
    assert 'docs/tasks/t001_old/spec.md' in migrations
    assert 'docs/tasks/t001_old/task.md' in migrations
    assert 'docs/archive/tasks/t002_archived_old/spec.md' in migrations
    assert 'docs/archive/tasks/t002_archived_old/task.md' in migrations
    assert worktrees == [] and errors == []

    changed: set[Path] = set()
    reports = rs.force_migrate_workflow_tasks(changed)

    # 验证活跃任务已完成布局转换
    assert not (task_dir / 'spec.md').exists()
    assert (task_dir / 'task.md').is_file()
    assert (task_dir / 'note.md').is_file()

    migrated_spec = (task_dir / 'task.md').read_text(encoding="utf-8")
    migrated_task = (task_dir / 'note.md').read_text(encoding="utf-8")
    assert '旧版规范文字' not in migrated_spec
    for blocks in rs._guide_blocks_by_heading(current_spec).values():
        for block in blocks:
            assert block in migrated_spec
    assert 'status: "active"' in migrated_task
    assert 'schedule_status' not in migrated_task
    assert 'review_limit: "5"' in migrated_task
    assert 'verify_limit: "5"' in migrated_task
    for line in rs._implementation_guidance(current_task):
        assert line in migrated_task
    assert '旧版固定说明' not in migrated_task
    assert task_dir / 'spec.md' in changed
    assert task_dir / 'task.md' in changed
    assert task_dir / 'note.md' in changed

    # 验证归档任务已完成布局转换
    assert not (archive_dir / 'spec.md').exists()
    assert (archive_dir / 'task.md').is_file()
    assert (archive_dir / 'note.md').is_file()
    migrated_archive_task = (archive_dir / 'note.md').read_text(encoding="utf-8")
    assert 'status: done' in migrated_archive_task or 'status: "done"' in migrated_archive_task
    assert archive_dir / 'spec.md' in changed
    assert archive_dir / 'task.md' in changed
    assert archive_dir / 'note.md' in changed

    # 幂等性：迁移后再查 status 为空，再次执行不写盘
    assert rs._replace_guide_blocks(migrated_spec, current_spec) == migrated_spec
    assert rs._migrate_task_text(migrated_task, current_task) == migrated_task
    assert rs.force_migrate_workflow_tasks(set()) == []
    migrations, worktrees, errors = rs.workflow_migration_status(env['src'])
    assert migrations == [] and worktrees == [] and errors == []

    # 迁移后：store.py 扫描正常解析活跃与归档任务
    tasks = task_store._scan_tasks_in_directories(consumer / 'docs/tasks', consumer / 'docs/archive/tasks')
    assert [t['tid'] for t in tasks] == ['t001', 't002']
    assert tasks[0]['status'] == 'active' and tasks[0]['dir'] == 'docs/tasks/t001_old'
    assert tasks[1]['status'] == 'done' and tasks[1]['dir'] == 'docs/archive/tasks/t002_archived_old'


def test_task_migration_only_reads_schema_fields_from_frontmatter(env):
    _, current_task = _install_current_workflow_templates(env['consumer'])
    old_task = current_task.replace('review_limit: 5\n', '').replace('verify_limit: 5\n', '')
    old_task = old_task.replace(
        '无\n\n## Review 处置',
        'status: blocked\nreview_limit: 正文不是字段\nverify_limit: 正文不是字段\n\n## Review 处置',
    )
    migrated = rs._migrate_task_text(old_task, current_task)
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
    (task_dir / 'note.md').write_text(current_task)
    _git(consumer, 'init', '-b', 'main')
    _git(consumer, 'config', 'user.email', 'test@example.com')
    _git(consumer, 'config', 'user.name', 'test')
    _git(consumer, 'add', '-A')
    _git(consumer, 'commit', '-m', 'init')
    worktree = consumer.parent / 'consumer_t001'
    _git(consumer, 'worktree', 'add', '-b', 't001_old', str(worktree))

    with pytest.raises(rs.SyncError, match='必须先完成或 rewind'):
        rs.force_migrate_workflow_tasks(set())


def test_migrate_conventions_updates_atomic_write_preserving_custom_content(env, monkeypatch):
    consumer = env['consumer']
    conv_file = consumer / "docs/blueprint/conventions.md"
    conv_file.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(rs, "CONVENTIONS_PATH", conv_file)

    original_text = (
        "# 约定\n\n"
        "## 项目自定义约定\n"
        "- 保持现有 React 风格。\n\n"
        "### 原子写\n"
        "- 写权威/派生状态数据走 tmp 文件 + fsync + os.replace 原子写（.repo_template/scripts/task.py 的 _atomic_write_text），防中断半写。"
        "t063 曾实现。脚本层跨模块复用时 from task import _atomic_write_text（task.py 有 __main__ guard，import 安全）。\n"
    )
    conv_file.write_text(original_text, encoding="utf-8")

    assert rs.conventions_migration_status() == [rs._rel(conv_file)]

    changed = set()
    reports = rs.migrate_conventions(changed)
    assert len(reports) == 1
    assert conv_file in changed

    migrated_text = conv_file.read_text(encoding="utf-8")
    assert "保持现有 React 风格" in migrated_text
    assert "t063 曾实现" in migrated_text
    assert "from repo_task.documents import atomic_write_text" in migrated_text
    assert "_atomic_write_text" not in migrated_text
    assert "from task import" not in migrated_text

    # 幂等：再次执行无迁移
    assert rs.conventions_migration_status() == []
    assert rs.migrate_conventions(set()) == []


def test_workflow_migration_blocks_when_git_worktree_fails(env, monkeypatch):
    """验证无法查询 git worktree 时拒绝放行 schema 迁移并 fail-closed 报错（Issue #22）。"""
    _install_current_workflow_templates(env["consumer"])
    real_git = rs._git
    def fake_git(repo, *args, **kwargs):
        if "worktree" in args:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="simulated lock error")
        return real_git(repo, *args, **kwargs)

    monkeypatch.setattr(rs, "_git", fake_git)

    # 1. workflow_migration_status 报告阻塞错误
    _, _, errors = rs.workflow_migration_status(env["src"])
    assert any("无法查询 git worktree" in err for err in errors)

    # 2. force_migrate_workflow_tasks 抛出 SyncError 阻断写盘
    with pytest.raises(rs.SyncError, match="无法查询 git worktree"):
        rs.force_migrate_workflow_tasks(set())


def test_run_tests_timeout_handling(env, monkeypatch, capsys):
    """验证契约测试超时被捕获、打印明确超时诊断且返回 False 触发回滚（Issue #29）。"""
    def mock_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["pytest"], timeout=120)

    monkeypatch.setattr(subprocess, "run", mock_run)
    ret = rs._run_tests(env["consumer"])
    assert ret is False
    err = capsys.readouterr().err
    assert "契约测试超时" in err

