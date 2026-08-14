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
    (toolkit / "hooks/merge_guard.py").write_text("print('guard')\n")
    hook = toolkit / "hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o755)
    (toolkit / "skills/task-run").mkdir(parents=True)
    (toolkit / "skills/task-run/SKILL.md").write_text(
        "---\nname: task-run\ndescription: none\ndisable-model-invocation: true\n---\nrun\n"
    )
    (src / ".github/workflows").mkdir(parents=True)
    (src / ".github/workflows/repo-template-ci.yml").write_text("name: x\n")
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
    monkeypatch.setattr(rs, "SKILLS_SRC", consumer / ".repo_template/skills")
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
    assert (consumer / ".repo_template/scripts/task.py").exists()
    assert (consumer / ".repo_template/docs/spike_report_template.md").read_text() == "# report\n"
    assert (consumer / ".claude/hooks/merge_guard.py").is_symlink()
    assert (consumer / ".repo_template/skills/task-run/SKILL.md").read_text().startswith("---")
    assert (consumer / ".claude/skills/task-run").is_symlink()
    assert (consumer / ".agents/skills/task-run").is_symlink()
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
    rs.cmd_init(Namespace(source=str(env["src"])))
    state = rs.read_state()
    assert state["template_source"]["kind"] == "path"
    assert state["template_source"]["value"] == str(env["src"])


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
