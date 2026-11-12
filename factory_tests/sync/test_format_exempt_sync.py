"""模板自有资产 format 豁免：可观测的 lint-staged 替身与提交稳定性回归。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from git_repo_support import install_fake_md_kx
from repo_task.file_transaction import SYNC_TRANSACTION_DIR, state_transaction
from template_sync import context as ctx
from template_sync import transaction as txn_module
from template_sync.apply import cmd_apply
from template_sync.shared_files import _git_ignored
from test_repo_sync import _git, env

_HOOK = '''#!/bin/sh
python3 "$(dirname "$0")/lint_staged.py"
'''

_LINT_STAGED = '''\
import json
import subprocess
import tempfile
from pathlib import Path

root = Path(subprocess.run(
    ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
).stdout.strip())
pi = root / ".prettierignore"
rules = pi.read_text(encoding="utf-8") if pi.exists() else ""
staged = subprocess.run(
    ["git", "diff", "--cached", "--name-only"], capture_output=True, text=True, check=True
).stdout.splitlines()
state = {"scanned": [], "rewritten": []}
with tempfile.TemporaryDirectory(prefix="fake_lint_staged_") as directory:
    engine = Path(directory)
    subprocess.run(["git", "init", "-q"], cwd=engine, check=True)
    (engine / ".gitignore").write_text(rules, encoding="utf-8")
    for rel in staged:
        if not rel.endswith(".json"):
            continue
        state["scanned"].append(rel)
        ignored = subprocess.run(
            ["git", "-C", str(engine), "-c", "core.excludesFile=/dev/null",
             "check-ignore", "--no-index", "-q", "--", rel],
            capture_output=True, text=True,
        )
        if ignored.returncode == 0:
            continue
        path = root / rel
        text = path.read_text(encoding="utf-8")
        if text.strip() == '{"version": 1}':
            path.write_text('{ "version": 1 }\\n', encoding="utf-8")
        elif '"args": [\\n' in text:
            path.write_text(
                json.dumps(json.loads(text), ensure_ascii=False, separators=(",", ": ")) + "\\n",
                encoding="utf-8",
            )
        else:
            continue
        subprocess.run(["git", "add", rel], check=True)
        state["rewritten"].append(rel)
scratch = root / ".scratch"
scratch.mkdir(exist_ok=True)
(scratch / "fake_hook_state.json").write_text(json.dumps(state), encoding="utf-8")
'''

_PROBE = ".repo_template/format_probe.json"
_COMPACT = b'{"version": 1}\n'
_EXPANDED = b'{ "version": 1 }\n'


def _prepare(env, tmp_path, monkeypatch):
    install_fake_md_kx(tmp_path, monkeypatch)
    return argparse.Namespace(sync=env["sync"], skip_tests=True, decisions={})


def _add_format_probe(env):
    """让被测 JSON 在本轮同步中真实新增，而不是沿用已提交基线字节。"""
    probe = env["src"] / _PROBE
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_bytes(_COMPACT)
    _git(env["src"], "add", "-A")
    _git(env["src"], "commit", "-m", "add format probe")


def _hook_script(tmp_path):
    return tmp_path / "fake_hooks" / "lint_staged.py"


def _install_fake_lint_staged(env, tmp_path):
    hooks = tmp_path / "fake_hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-commit").write_text(_HOOK)
    (hooks / "pre-commit").chmod(0o755)
    (hooks / "lint_staged.py").write_text(_LINT_STAGED)
    _git(env["consumer"], "config", "core.hooksPath", str(hooks))


def _hook_state(consumer):
    return json.loads((consumer / ".scratch/fake_hook_state.json").read_text(encoding="utf-8"))


def _run_hook_directly(consumer, tmp_path):
    subprocess.run(
        [sys.executable, str(_hook_script(tmp_path))],
        cwd=consumer, capture_output=True, text=True, check=True,
    )


def test_probe_is_staged_and_exempt_hook_is_noop(env, tmp_path, monkeypatch):
    args = _prepare(env, tmp_path, monkeypatch)
    _add_format_probe(env)
    _install_fake_lint_staged(env, tmp_path)
    consumer = env["consumer"]
    assert cmd_apply(args) == 0
    assert (consumer / _PROBE).read_bytes() == _COMPACT
    assert "/.repo_template/" in (consumer / ".prettierignore").read_text().splitlines()

    _git(consumer, "add", "-A")
    assert _PROBE in _git(consumer, "diff", "--cached", "--name-only").stdout.splitlines()
    _git(consumer, "commit", "-m", "sync")
    state = _hook_state(consumer)
    assert _PROBE in state["scanned"]
    assert state["rewritten"] == []
    assert _git(consumer, "show", f"HEAD:{_PROBE}").stdout == _COMPACT.decode()

    monkeypatch.setattr(txn_module, "_run_tests", lambda root: True)
    action = argparse.Namespace(sync=env["sync"], action="accept", confirm_restore=False)
    assert txn_module.cmd_transaction(action) == 0
    assert state_transaction(consumer, SYNC_TRANSACTION_DIR).read()["status"] == "accepted"


def test_negation_rules_block_apply_before_commit(env, tmp_path, monkeypatch, capsys):
    args = _prepare(env, tmp_path, monkeypatch)
    _add_format_probe(env)
    consumer = env["consumer"]
    rules = "\n".join(ctx.PRETTIERIGNORE_TEMPLATE_RULES) + "\n"
    (consumer / ".prettierignore").write_text(
        rules + "!.repo_template/\n!.repo_template/format_probe.json\n", encoding="utf-8"
    )
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "consumer negation rules")

    assert cmd_apply(args) == 1
    assert "未被 format 豁免覆盖" in capsys.readouterr().err
    assert state_transaction(consumer, SYNC_TRANSACTION_DIR).read()["status"] == "rolled_back"
    assert not (consumer / _PROBE).exists()
    assert "!.repo_template/" in (consumer / ".prettierignore").read_text().splitlines()


def test_fake_formatter_rewrites_when_exemption_is_off(env, tmp_path, monkeypatch):
    """正反对照：替身不是空壳，关闭豁免后确实改写 staged JSON。"""
    args = _prepare(env, tmp_path, monkeypatch)
    _add_format_probe(env)
    _install_fake_lint_staged(env, tmp_path)
    consumer = env["consumer"]
    assert cmd_apply(args) == 0
    (consumer / ".prettierignore").write_text("# no exemption\n", encoding="utf-8")
    _git(consumer, "add", "-A")
    _run_hook_directly(consumer, tmp_path)
    state = _hook_state(consumer)
    assert _PROBE in state["scanned"]
    assert _PROBE in state["rewritten"]
    assert (consumer / _PROBE).read_bytes() == _EXPANDED


def test_directory_outside_toolkit_remains_formatter_managed(env, tmp_path, monkeypatch):
    """R01 边界：目录外同步 JSON 不受模板豁免，仍由消费仓 formatter 管理。"""
    _prepare(env, tmp_path, monkeypatch)
    _install_fake_lint_staged(env, tmp_path)
    consumer = env["consumer"]
    (consumer / ".mcp.json").write_bytes(_COMPACT)
    _git(consumer, "add", "-A")
    _run_hook_directly(consumer, tmp_path)
    assert ".mcp.json" in _hook_state(consumer)["rewritten"]
    assert (consumer / ".mcp.json").read_bytes() == _EXPANDED


_MCP_SOURCE = {"mcpServers": {"probe": {"command": "npx", "args": ["--stdio"]}}}
_MCP_CONSUMER = {"mcpServers": {}}


def _mcp_scenario(env, *, exempt=False):
    src, consumer = env["src"], env["consumer"]
    (src / ".mcp.json").write_text(
        json.dumps(_MCP_SOURCE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _git(src, "add", "-A")
    _git(src, "commit", "-m", "add MCP probe server")
    mcp = consumer / ".mcp.json"
    mcp.write_text(
        json.dumps(_MCP_CONSUMER, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if exempt:
        (consumer / ".prettierignore").write_text(
            "/.mcp.json\n" + "\n".join(ctx.PRETTIERIGNORE_TEMPLATE_RULES) + "\n",
            encoding="utf-8",
        )
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "consumer MCP baseline")
    return mcp


def test_directory_outside_toolkit_conflict_blocks_before_commit(env, tmp_path, monkeypatch, capsys):
    """R01：真实 MCP 合并在提交前显式阻断，不返回 0 埋下提交后死锁。"""
    args = _prepare(env, tmp_path, monkeypatch)
    mcp = _mcp_scenario(env)
    before = mcp.read_bytes()
    assert cmd_apply(args) == 1
    err = capsys.readouterr().err
    assert ".mcp.json" in err and ".prettierignore" in err
    assert "已完整回滚" in err
    assert state_transaction(env["consumer"], SYNC_TRANSACTION_DIR).read()["status"] == "rolled_back"
    assert mcp.read_bytes() == before
    assert _git(env["consumer"], "status", "--porcelain", "--untracked-files=all").stdout == ""


def test_directory_outside_toolkit_supported_flow_completes(env, tmp_path, monkeypatch):
    """R01：消费仓把目录外路径纳入自己的 .prettierignore 后，同步可完成且可复验。"""
    args = _prepare(env, tmp_path, monkeypatch)
    mcp = _mcp_scenario(env, exempt=True)
    _install_fake_lint_staged(env, tmp_path)
    consumer = env["consumer"]
    assert cmd_apply(args) == 0
    rules_text = (consumer / ".prettierignore").read_text(encoding="utf-8")
    assert "/.mcp.json" in rules_text.splitlines()
    assert _git_ignored(rules_text, [".mcp.json"]) == {".mcp.json"}
    assert '"probe"' in mcp.read_text(encoding="utf-8")

    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "sync")
    state = _hook_state(consumer)
    assert ".mcp.json" in state["scanned"]
    assert ".mcp.json" not in state["rewritten"]
    assert '"probe"' in _git(consumer, "show", "HEAD:.mcp.json").stdout

    monkeypatch.setattr(txn_module, "_run_tests", lambda root: True)
    action = argparse.Namespace(sync=env["sync"], action="accept", confirm_restore=False)
    assert txn_module.cmd_transaction(action) == 0
    assert state_transaction(consumer, SYNC_TRANSACTION_DIR).read()["status"] == "accepted"


def test_old_unanchored_rule_is_migrated_during_apply(env, tmp_path, monkeypatch):
    """R08：旧模板规则基线经完整 apply 后回收，嵌套同名目录恢复格式检查。"""
    args = _prepare(env, tmp_path, monkeypatch)
    consumer = env["consumer"]
    nested = consumer / "src/.repo_template/app.json"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text('{"x": 1}\n', encoding="utf-8")
    (consumer / ".prettierignore").write_text(
        ".repo_template/\ndocs/**/handoff.json\n.opencode/package.json\n.opencode/package-lock.json\n",
        encoding="utf-8",
    )
    assert _git_ignored(
        (consumer / ".prettierignore").read_text(encoding="utf-8"),
        ["src/.repo_template/app.json"],
    ) == {"src/.repo_template/app.json"}
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "old unanchored rules baseline")

    assert cmd_apply(args) == 0
    lines = (consumer / ".prettierignore").read_text().splitlines()
    assert ".repo_template/" not in lines
    assert "/.repo_template/" in lines
    assert _git_ignored(
        (consumer / ".prettierignore").read_text(encoding="utf-8"),
        ["src/.repo_template/app.json"],
    ) == set()


def test_consumer_fixtures_ignore_does_not_block_sync(env, tmp_path, monkeypatch):
    """N01：消费仓合法忽略 tests/fixtures/ 不应被误判成模板规则范围扩张。"""
    args = _prepare(env, tmp_path, monkeypatch)
    consumer = env["consumer"]
    fixtures = consumer / "tests/fixtures/payload.json"
    fixtures.parent.mkdir(parents=True, exist_ok=True)
    fixtures.write_text('{"payload": 1}\n', encoding="utf-8")
    (consumer / ".prettierignore").write_text(
        "tests/fixtures/\n" + "\n".join(ctx.PRETTIERIGNORE_TEMPLATE_RULES) + "\n",
        encoding="utf-8",
    )
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "consumer ignores fixtures")
    assert cmd_apply(args) == 0
    lines = (consumer / ".prettierignore").read_text().splitlines()
    assert "tests/fixtures/" in lines
    assert "/.repo_template/" in lines
