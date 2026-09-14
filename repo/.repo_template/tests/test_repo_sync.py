"""repo_sync.py 机械化同步器的 real-git 测试。

SRC（模板）与 CONSUMER（消费项目）都在 tmp_path 下构造真实目录，monkeypatch
模块级路径常量（CONSUMER / STATE_PATH / SKILLS_AGENTS / SKILLS_CLAUDE）重绑定，
覆盖 state 字段级原子更新、user_prompts 管理、硬同步覆盖与多余删除、skill 覆盖
与 sync_state.json 保护、软链、.gitignore / .prettierignore / MCP 机械合并、apply 全流程与改动清单。
"""

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import repo_sync as rs


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
    (toolkit / "docs/task_template/spec.md").write_text("# spec\n")
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
    state_file = consumer / ".repo_template/sync_state.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(json.dumps({
        "template_source": {"kind": "path", "value": str(src)},
        "last_synced_commit": None,
        "last_synced_at": None,
        "user_prompts": [],
    }, indent=2))

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


def test_prep_oneway_sync_tooling(env):
    src, consumer = env["src"], env["consumer"]
    # 模板侧 core skill 与工具链更新
    core_src = src / ".repo_template/skills/repo-template-sync-core"
    core_src.mkdir(parents=True)
    (core_src / "SKILL.md").write_text(
        "---\nname: repo-template-sync-core\ndescription: none\ndisable-model-invocation: true\n---\nnew core\n"
    )
    (src / ".repo_template/scripts/repo_sync.py").write_text("new tool\n")

    dst_core = consumer / ".repo_template/skills/repo-template-sync-core"
    dst_core.mkdir(parents=True)
    (dst_core / "SKILL.md").write_text("---\nname: old\ndescription: old\n---\nold core\n")
    (consumer / ".repo_template/scripts").mkdir(parents=True)
    (consumer / ".repo_template/scripts/consumer_extra.py").write_text("extra\n")

    rs.cmd_prep(Namespace())

    assert (dst_core / "SKILL.md").read_text().endswith("new core\n")
    assert (consumer / ".repo_template/scripts/repo_sync.py").read_text() == "new tool\n"
    assert (consumer / ".repo_template/scripts/consumer_extra.py").exists()
    link = consumer / ".claude/skills/repo-template-sync-core"
    assert link.is_symlink()
    assert link.resolve() == dst_core.resolve()
    # 不写 state（不推进 last_synced_commit）
    assert rs.read_state()["last_synced_commit"] is None


def test_prep_idempotent_no_changes(env):
    src, consumer = env["src"], env["consumer"]
    core_src = src / ".repo_template/skills/repo-template-sync-core"
    core_src.mkdir(parents=True)
    (core_src / "SKILL.md").write_text(
        "---\nname: repo-template-sync-core\ndescription: none\ndisable-model-invocation: true\n---\nsame\n"
    )
    rs.cmd_prep(Namespace())
    rs.cmd_prep(Namespace())
    assert (consumer / ".repo_template/skills/repo-template-sync-core/SKILL.md").read_text().endswith("same\n")



# ---------------------------------------------------------------------------
# .gitignore / .prettierignore / MCP 机械合并
# ---------------------------------------------------------------------------

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

    rs.cmd_apply(Namespace(decision=["AGENTS.md:update"], skip_tests=True, decisions={"AGENTS.md": "update"}))
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

    rs.cmd_apply(Namespace(decision=[], skip_tests=True, decisions={}))
    # 无决策 → AGENTS.md 不动（ask_user 语义）
    assert (consumer / "AGENTS.md").read_text() == "CONSUMER OLD\n"


def test_apply_src_dirty_does_not_advance_commit(env):
    src, consumer = env["src"], env["consumer"]
    (src / ".repo_template/scripts/dirty_extra.py").write_text("x\n")  # SRC 未提交改动
    _add_prompt("跳过测试", "")
    rs.cmd_apply(Namespace(decision=[], skip_tests=True, decisions={}))
    state = rs.read_state()
    assert state["last_synced_commit"] is None
    assert state["last_synced_at"]


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

def test_dir_delete_rollback_restores_content(env):
    """F16：目录删除入回滚栈，回滚后内容完整恢复（非空壳 mkdir）。"""
    import shutil

    consumer = env["consumer"]
    target = consumer / "docs" / "ext" / "sub"
    target.mkdir(parents=True)
    (target / "keep.txt").write_text("keep", encoding="utf-8")
    rs._ROLLBACK.clear()
    rs._stage_rollback(consumer / "docs" / "ext")
    shutil.rmtree(consumer / "docs" / "ext")
    assert not (consumer / "docs" / "ext").exists()
    rs._rollback_changes()
    assert (consumer / "docs" / "ext" / "sub" / "keep.txt").read_text(encoding="utf-8") == "keep"
    rs._ROLLBACK.clear()
    rs._cleanup_rollback()


def test_skill_overwrite_rollback_restores_old_file(env):
    """F16：sync_skill 覆盖 skill 文件须入回滚栈，回滚后旧内容恢复。"""
    consumer = env["consumer"]
    src = env["src"]
    dst_skill = consumer / ".repo_template" / "skills" / "task-run"
    src_skill = src / ".repo_template" / "skills" / "task-run"
    dst_skill.mkdir(parents=True)
    (dst_skill / "SKILL.md").write_text("OLD CONTENT\n", encoding="utf-8")
    assert (src_skill / "SKILL.md").read_text(encoding="utf-8") != "OLD CONTENT\n"

    changed: set[Path] = set()
    rs._ROLLBACK.clear()
    rs.sync_skill(src_skill, dst_skill, changed)
    assert (dst_skill / "SKILL.md").read_text(encoding="utf-8").startswith("---")
    rs._rollback_changes()
    assert (dst_skill / "SKILL.md").read_text(encoding="utf-8") == "OLD CONTENT\n"
    rs._ROLLBACK.clear()
    rs._cleanup_rollback()


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
    spec = (source / 'spec.md').read_text()
    task = (source / 'task.md').read_text()
    (target / 'spec.md').write_text(spec)
    (target / 'task.md').write_text(task)
    return spec, task


def _old_task_documents(spec: str, task: str) -> tuple[str, str]:
    old_spec = spec.replace(
        '只写可观察、可独立验证的行为；每条使用稳定且不复用的 `AC-NNN`。需真实部署或人工环境验证时在编号前加 `[deploy]`。技术选型不作为行为 AC。',
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
    (task_dir / 'spec.md').write_text(missing)
    (task_dir / 'task.md').write_text(current_task)
    migrations, worktrees, errors = rs.workflow_migration_status(env['src'])
    assert 'docs/tasks/t001_missing/spec.md' in migrations
    assert worktrees == [] and errors == []
    rs.force_migrate_workflow_tasks(set())
    assert (task_dir / 'spec.md').read_text() == current_spec


def test_migration_status_reports_malformed_spec_without_raising(env):
    consumer = env['consumer']
    current_spec, current_task = _install_current_workflow_templates(consumer)
    _install_current_workflow_templates(env['src'])
    malformed = current_spec.replace(rs._GUIDE_CLOSE, '', 1)
    task_dir = consumer / 'docs/tasks/t001_malformed'
    task_dir.mkdir(parents=True)
    (task_dir / 'spec.md').write_text(malformed)
    (task_dir / 'task.md').write_text(current_task)
    migrations, worktrees, errors = rs.workflow_migration_status(env['src'])
    assert migrations == [] and worktrees == []
    assert len(errors) == 1 and 't001_malformed/spec.md' in errors[0]
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
    task_dir = consumer / 'docs/tasks/t001_old'
    task_dir.mkdir(parents=True)
    (task_dir / 'spec.md').write_text(old_spec)
    (task_dir / 'task.md').write_text(old_task)

    changed: set[Path] = set()
    reports = rs.force_migrate_workflow_tasks(changed)

    migrated_spec = (task_dir / 'spec.md').read_text()
    migrated_task = (task_dir / 'task.md').read_text()
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
    assert task_dir / 'spec.md' in changed and task_dir / 'task.md' in changed
    assert len(reports) == 2
    assert rs._replace_guide_blocks(migrated_spec, current_spec) == migrated_spec
    assert rs._migrate_task_text(migrated_task, current_task) == migrated_task
    assert rs.force_migrate_workflow_tasks(set()) == []
    migrations, worktrees, errors = rs.workflow_migration_status(env['src'])
    assert migrations == [] and worktrees == [] and errors == []


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
    (task_dir / 'spec.md').write_text(current_spec)
    (task_dir / 'task.md').write_text(current_task)
    _git(consumer, 'init', '-b', 'main')
    _git(consumer, 'config', 'user.email', 'test@example.com')
    _git(consumer, 'config', 'user.name', 'test')
    _git(consumer, 'add', '-A')
    _git(consumer, 'commit', '-m', 'init')
    worktree = consumer.parent / 'consumer_t001'
    _git(consumer, 'worktree', 'add', '-b', 't001_old', str(worktree))

    with pytest.raises(rs.SyncError, match='必须先完成或 rewind'):
        rs.force_migrate_workflow_tasks(set())
