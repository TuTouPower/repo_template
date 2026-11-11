"""模板自有资产 format 豁免：lint-staged hook 端到端回归与提交前 fail-closed。"""

from __future__ import annotations

import argparse

import pytest

from git_repo_support import install_fake_md_kx
from repo_task.file_transaction import SYNC_TRANSACTION_DIR, state_transaction
from template_sync import context as ctx
from template_sync import transaction as txn_module
from template_sync.apply import cmd_apply
from test_repo_sync import _git, env

_HOOK = '''#!/bin/sh
python3 "$(dirname "$0")/lint_staged.py"
'''

_LINT_STAGED = '''\
import fnmatch
import subprocess
from pathlib import Path

root = Path(subprocess.run(
    ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
).stdout.strip())
pi = root / ".prettierignore"
rules = (
    {line.strip() for line in pi.read_text().splitlines()
     if line.strip() and not line.lstrip().startswith("#")}
    if pi.exists() else set()
)
staged = subprocess.run(
    ["git", "diff", "--cached", "--name-only"], capture_output=True, text=True, check=True
).stdout.splitlines()
for rel in staged:
    if not rel.endswith(".json"):
        continue
    if any(
        rel.startswith(rule[:-1] + "/") if rule.endswith("/") else fnmatch.fnmatch(rel, rule)
        for rule in rules
    ):
        continue
    path = root / rel
    if path.read_text().strip() == '{"version": 1}':
        path.write_text('{ "version": 1 }\\n')
        subprocess.run(["git", "add", rel], check=True)
'''


def _prepare(env, tmp_path, monkeypatch):
    install_fake_md_kx(tmp_path, monkeypatch)
    return argparse.Namespace(sync=env["sync"], skip_tests=True, decisions={})


def _install_fake_lint_staged(env, tmp_path):
    hooks = tmp_path / "fake_hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-commit").write_text(_HOOK)
    (hooks / "pre-commit").chmod(0o755)
    (hooks / "lint_staged.py").write_text(_LINT_STAGED)
    _git(env["consumer"], "config", "core.hooksPath", str(hooks))


def test_exempt_hook_is_noop_and_accept_passes(env, tmp_path, monkeypatch):
    args = _prepare(env, tmp_path, monkeypatch)
    _install_fake_lint_staged(env, tmp_path)
    assert cmd_apply(args) == 0
    assert ".repo_template/" in (env["consumer"] / ".prettierignore").read_text().splitlines()
    _git(env["consumer"], "add", "-A")
    _git(env["consumer"], "commit", "-m", "sync")
    shown = _git(env["consumer"], "show", "HEAD:.repo_template/entity_schema.json").stdout
    assert shown == '{"version": 1}\n'
    monkeypatch.setattr(txn_module, "_run_tests", lambda root: True)
    action = argparse.Namespace(sync=env["sync"], action="accept", confirm_restore=False)
    assert txn_module.cmd_transaction(action) == 0
    assert state_transaction(env["consumer"], SYNC_TRANSACTION_DIR).read()["status"] == "accepted"


def test_enum_style_rules_fail_closed_before_commit(env, tmp_path, monkeypatch, capsys):
    args = _prepare(env, tmp_path, monkeypatch)
    monkeypatch.setattr(ctx, "PRETTIERIGNORE_TEMPLATE_RULES", (".repo_template/sync_manifest.json",))
    before = (env["consumer"] / ".repo_template/entity_schema.json").read_bytes()
    assert cmd_apply(args) == 1
    assert "未被 format 豁免覆盖" in capsys.readouterr().err
    assert state_transaction(env["consumer"], SYNC_TRANSACTION_DIR).read()["status"] == "rolled_back"
    assert (env["consumer"] / ".repo_template/entity_schema.json").read_bytes() == before
    assert not (env["consumer"] / ".prettierignore").exists()
