"""repo_sync.py 机械化同步器的 real-git 测试。

SRC（模板）与 CONSUMER（消费项目）都在 tmp_path 下构造真实目录，monkeypatch
模块级路径常量（CONSUMER / STATE_PATH / SKILLS_AGENTS / SKILLS_CLAUDE）重绑定，
覆盖 state 字段级原子更新、user_prompts 管理、硬同步覆盖与多余删除、skill 覆盖
与 sync_state.json 保护、软链、.gitignore / MCP 机械合并、apply 全流程与改动清单。
"""

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "repo_template"
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

    (src / "scripts/repo_template").mkdir(parents=True)
    (src / "scripts/repo_template/task.py").write_text("print('task')\n")
    (src / "tests/repo_template").mkdir(parents=True)
    (src / "tests/repo_template/test_x.py").write_text("def test_x():\n    pass\n")
    (src / "docs/tasks/task_template").mkdir(parents=True)
    (src / "docs/tasks/task_template/spec.md").write_text("# spec\n")
    (src / "docs/reviews/prompts").mkdir(parents=True)
    (src / "docs/reviews/prompts/general_prompt.txt").write_text("general\n")
    (src / "docs/spikes").mkdir(parents=True)
    (src / "docs/spikes/report_template.md").write_text("# report\n")
    (src / "docs/blueprint").mkdir(parents=True)
    (src / "docs/blueprint/architecture_repo_template.md").write_text("# arch\n")
    (src / ".claude/hooks").mkdir(parents=True)
    (src / ".claude/hooks/merge_guard.py").write_text("print('guard')\n")
    (src / ".agents/skills/task-run").mkdir(parents=True)
    (src / ".agents/skills/task-run/SKILL.md").write_text(
        "---\nname: task-run\ndescription: none\ndisable-model-invocation: true\n---\nrun\n"
    )
    (src / "AGENTS.md").write_text("SRC AGENTS\n")
    (src / ".gitignore").write_text("node_modules/\n*.log\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-m", "init")
    head = _git(src, "rev-parse", "HEAD").stdout.strip()

    consumer = tmp_path / "consumer"
    consumer.mkdir()
    state_file = consumer / ".agents/skills/repo-template-sync/sync_state.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(json.dumps({
        "template_source": {"kind": "path", "value": str(src)},
        "last_synced_commit": None,
        "last_synced_at": None,
        "user_prompts": [],
    }, indent=2))

    monkeypatch.setattr(rs, "CONSUMER", consumer)
    monkeypatch.setattr(rs, "STATE_PATH", state_file)
    monkeypatch.setattr(rs, "SKILLS_AGENTS", consumer / ".agents/skills")
    monkeypatch.setattr(rs, "SKILLS_CLAUDE", consumer / ".claude/skills")
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
    task = consumer / "scripts/repo_template/task.py"
    task.parent.mkdir(parents=True)
    task.write_text("print('OLD')\n")
    extra = consumer / "tests/repo_template/extra_old.py"
    extra.parent.mkdir(parents=True)
    extra.write_text("x = 1\n")

    changed: set[Path] = set()
    rs.sync_dir(src / "scripts/repo_template", consumer / "scripts/repo_template", changed)
    rs.sync_dir(src / "tests/repo_template", consumer / "tests/repo_template", changed)

    assert task.read_text() == "print('task')\n"
    assert (consumer / "tests/repo_template/test_x.py").exists()
    assert not extra.exists()
    assert task in changed
    assert extra in changed


def test_sync_file_delete_when_src_missing(env):
    src, consumer = env["src"], env["consumer"]
    dst = consumer / "docs/spikes/report_template.md"
    dst.parent.mkdir(parents=True)
    dst.write_text("old\n")
    changed: set[Path] = set()
    rs.sync_file(src / "docs/spikes/report_template.md", dst, changed)
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
    dst = consumer / ".agents/skills/task-run"
    dst.mkdir(parents=True)
    (dst / "SKILL.md").write_text("OLD VERSION\n")
    protected = dst / "sync_state.json"
    protected.write_text('{"keep": true}\n')

    changed: set[Path] = set()
    rs.sync_skill(src / ".agents/skills/task-run", dst, changed)
    assert (dst / "SKILL.md").read_text().startswith("---\nname: task-run")
    assert protected.read_text() == '{"keep": true}\n'
    assert protected not in changed


def test_repair_symlinks(env):
    src, consumer = env["src"], env["consumer"]
    dst = consumer / ".agents/skills/task-run"
    dst.mkdir(parents=True)
    changed: set[Path] = set()
    reports = rs.repair_symlinks(changed)
    link = consumer / ".claude/skills/task-run"
    assert link.is_symlink()
    assert link.resolve() == (consumer / ".agents/skills/task-run").resolve()
    assert not reports
    # 再次运行不重复改
    changed = set()
    rs.repair_symlinks(changed)
    assert not changed
    # 非本机制链接报告不碰：.agents/skills 下目录对应的 .claude/skills 位置被普通文件占用
    managed = consumer / ".agents/skills/foreign-skill"
    managed.mkdir(parents=True)
    foreign = consumer / ".claude/skills/foreign-skill"
    foreign.write_text("not a symlink\n")
    changed = set()
    reports = rs.repair_symlinks(changed)
    assert any("foreign-skill" in r for r in reports)
    assert foreign.exists()
    assert not changed


# ---------------------------------------------------------------------------
# .gitignore / MCP 机械合并
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
# apply 全流程 + 改动清单
# ---------------------------------------------------------------------------

def test_apply_flow_writes_and_advances_state(env, capsys):
    src, consumer, head = env["src"], env["consumer"], env["head"]
    old_agents = consumer / "AGENTS.md"
    old_agents.write_text("CONSUMER OLD\n")

    rs.cmd_apply(Namespace(decision=["AGENTS.md:update"], skip_tests=True, decisions={"AGENTS.md": "update"}))
    out = capsys.readouterr().out

    # 硬同步
    assert (consumer / "scripts/repo_template/task.py").exists()
    assert (consumer / "docs/spikes/report_template.md").read_text() == "# report\n"
    assert (consumer / ".claude/hooks/merge_guard.py").exists()
    # skill + 软链
    assert (consumer / ".agents/skills/task-run/SKILL.md").read_text().startswith("---")
    assert (consumer / ".claude/skills/task-run").is_symlink()
    # 裁定单元 update
    assert (consumer / "AGENTS.md").read_text() == "SRC AGENTS\n"
    # state 推进
    state = rs.read_state()
    assert state["last_synced_commit"] == head
    assert state["last_synced_at"]
    # 改动清单输出（点名 add 依据）
    assert "scripts/repo_template/task.py" in out
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
    (src / "scripts/repo_template/dirty_extra.py").write_text("x\n")  # SRC 未提交改动
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
    rs.cmd_init(Namespace(source=str(env["src"])))
    state = rs.read_state()
    assert state["template_source"]["kind"] == "path"
    assert state["template_source"]["value"] == str(env["src"])


def test_resolve_src_rejects_invalid(env):
    state = rs.read_state()
    state["template_source"]["value"] = "/nonexistent/not_a_template"
    with pytest.raises(rs.SyncError):
        rs.resolve_src(state)
