"""同步预检：三入口先给完整迁移指引，当前布局再按同一 spec 契约阻断。"""

from __future__ import annotations

import argparse
import stat
from pathlib import Path

import pytest

from repo_task.file_transaction import SYNC_TRANSACTION_DIR, state_transaction
from template_sync.apply import cmd_apply
from template_sync.status_plan import cmd_plan, cmd_status
from test_repo_sync import env, _commit_consumer_baseline


VALID = "---\nslug: feature\nstatus: active\ntasks: [baseline]\n---\n\n# Feature\n"


def _write_spec(root: Path, text: str) -> None:
    path = root / "docs/specs/feature.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _assets(root: Path) -> dict:
    snapshot = {}
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if ".git" in rel.parts:
            continue
        mode = path.lstat().st_mode
        data = str(path.readlink()) if path.is_symlink() else path.read_bytes() if path.is_file() else None
        snapshot[rel.as_posix()] = (stat.S_IFMT(mode), stat.S_IMODE(mode), data)
    return snapshot


def _blocked_outputs(env, capsys) -> tuple[list[str], str]:
    root, sync = env["consumer"], env["sync"]
    _commit_consumer_baseline(root)
    before = _assets(root)
    state_before = env["state_file"].read_bytes()
    previews = []
    for command in (cmd_status, cmd_plan):
        assert command(argparse.Namespace(sync=sync, fetch=False)) == 0
        previews.append(capsys.readouterr().out)
        assert _assets(root) == before
    assert cmd_apply(argparse.Namespace(sync=sync, skip_tests=True, decisions={})) == 1
    error = capsys.readouterr().err.removeprefix("错误: ").strip()
    assert error
    assert _assets(root) == before
    assert env["state_file"].read_bytes() == state_before
    assert state_transaction(root, SYNC_TRANSACTION_DIR).read() is None
    for output in previews:
        assert "BLOCKED" in output
        assert error in output
    return previews, error


@pytest.mark.parametrize("text", ["# Feature\n", "---\nslug: feature\n---\n# Feature\n"])
def test_legacy_specs_preview_and_apply_without_separate_migration(env, capsys, text):
    _write_spec(env["consumer"], text)
    _commit_consumer_baseline(env["consumer"])
    before = _assets(env["consumer"])
    for command in (cmd_status, cmd_plan):
        assert command(argparse.Namespace(sync=env["sync"], fetch=False)) == 0
        output = capsys.readouterr().out
        assert "specs-layout" in output and "apply 内置迁移" in output
        assert "BLOCKED" not in output
        assert _assets(env["consumer"]) == before
    assert cmd_apply(argparse.Namespace(sync=env["sync"], skip_tests=True, decisions={})) == 0
    assert "slug: feature" in (env['consumer'] / 'docs/specs/feature.md').read_text()


@pytest.mark.parametrize("text, diagnostic", [
    (VALID.replace("active", "invalid"), "status 只允许"),
    (VALID.replace("[baseline]", "[t001, t001]"), "重复 token"),
    (VALID.replace("tasks: [baseline]", "tasks: [baseline]\nextra: forbidden"), "多余字段"),
    ("---\nslug: feature\nstatus: active\ntasks: [baseline]\n", "未闭合"),
    (VALID.replace("slug: feature", "slug: feature\nslug: feature"), "重复键"),
])
def test_current_layout_invalid_specs_have_same_contract_blocker(env, capsys, text, diagnostic):
    _write_spec(env["consumer"], text)
    previews, error = _blocked_outputs(env, capsys)
    for output in [*previews, error]:
        assert "docs/specs front matter 契约违规" in output
        assert diagnostic in output
        assert "specs-layout 旧布局" not in output


def test_migration_backlog_and_invalid_current_spec_are_both_reported(env, capsys):
    _write_spec(env["consumer"], VALID.replace("active", "invalid"))
    (env["consumer"] / "docs/pending.md").write_text("# Pending\n", encoding="utf-8")
    previews, error = _blocked_outputs(env, capsys)
    for output in [*previews, error]:
        assert "pending-ledger" in output
        assert "契约违规" in output


def test_current_legal_spec_allows_previews_and_apply(env, capsys):
    _write_spec(env["consumer"], VALID)
    for command in (cmd_status, cmd_plan):
        assert command(argparse.Namespace(sync=env["sync"], fetch=False)) == 0
        output = capsys.readouterr().out
        assert "同步预检通过" in output
        assert "front matter 契约违规" not in output
    _commit_consumer_baseline(env["consumer"])
    assert cmd_apply(argparse.Namespace(sync=env["sync"], skip_tests=True, decisions={})) == 0
    assert (env["consumer"] / "docs/specs/feature.md").read_text(encoding="utf-8") == VALID


def test_legacy_only_state_points_to_source(env, capsys):
    legacy = env["sync"].legacy_state_path()
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{}", encoding="utf-8")
    env["state_file"].unlink()
    for command in (cmd_status, cmd_plan):
        assert command(argparse.Namespace(sync=env["sync"], fetch=False)) == 0
        output = capsys.readouterr().out
        assert "--source" in output
        assert "init --source" not in output
