"""R05 回归：retired-hooks 改写 settings.json 必须原子写，失败残留须可见且阻断收尾。

旧写法用固定 ``settings.json.tmp`` + ``Path.replace``：replace 抛错时临时文件
残留，事务虽标记 rolled_back 并输出「已完整回滚」，但下一轮 ``_preflight`` 的
干净工作区检查（含未跟踪文件）会被该残留阻断。改用
``repo_task.documents.atomic_write_text`` 后，清理失败会传播残留路径，事务保留
restore_failed，恢复命令须在残留消失后才能收尾。
"""

import argparse
import json
from pathlib import Path

import pytest
import repo_task.documents as documents
from git_repo_support import install_fake_md_kx
from repo_task.file_transaction import SYNC_TRANSACTION_DIR, open_transactions, state_transaction
from template_sync import context as ctx
from template_sync import transaction as txn_module
from template_sync.apply import cmd_apply
from template_sync.hooks import _RETIRED_MERGE_GUARD_COMMAND, migrate_retired_hooks
from template_sync.transaction import cmd_transaction
from test_repo_sync import env, _git, _commit_consumer_baseline


_RETIRED_SETTINGS = {
    "hooks": {
        "PreToolUse": [
            {
                "hooks": [
                    {"type": "command", "command": _RETIRED_MERGE_GUARD_COMMAND},
                    {"type": "command", "command": "python3 scripts/custom_guard.py"},
                ]
            }
        ]
    },
    "permissions": {"allow": []},
}


def _prepare(env, tmp_path, monkeypatch):
    install_fake_md_kx(tmp_path, monkeypatch)
    _commit_consumer_baseline(env["consumer"])
    return argparse.Namespace(sync=env["sync"], skip_tests=True, decisions={})


def _scenario(env) -> tuple[Path, bytes]:
    """构造 retired-hooks 迁移现场并提交为干净基线，返回 settings 路径与原字节。"""
    consumer = env["consumer"]
    retired = consumer / ".repo_template/hooks/merge_guard.py"
    retired.parent.mkdir(parents=True, exist_ok=True)
    retired.write_text("# guard\n", encoding="utf-8")
    guard = consumer / ".claude/hooks/merge_guard.py"
    guard.parent.mkdir(parents=True, exist_ok=True)
    guard.symlink_to("../../.repo_template/hooks/merge_guard.py")
    settings = consumer / ".claude/settings.json"
    settings.write_text(
        json.dumps(_RETIRED_SETTINGS, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "retired hooks baseline")
    return settings, settings.read_bytes()


def _temp_leftovers(claude: Path) -> list[str]:
    return sorted(
        path.name for path in claude.rglob("*")
        if path.is_file() and path.name.endswith(".tmp")
    )


def _assert_no_temp(claude: Path) -> None:
    assert _temp_leftovers(claude) == []
    assert not (claude / "settings.json.tmp").exists()
    assert not list(claude.glob(".settings.json.*.tmp"))


def _double_failure_apply(env, tmp_path, monkeypatch):
    """rename 与临时文件 unlink 双故障现场，返回 settings 原字节与残留临时文件。"""
    args = _prepare(env, tmp_path, monkeypatch)
    settings, original = _scenario(env)
    claude = settings.parent
    real_replace = documents.os.replace
    real_unlink = Path.unlink

    def failing_replace(src, dst, *a, **kw):
        if Path(dst).name == "settings.json":
            raise OSError("injected rename failure")
        return real_replace(src, dst, *a, **kw)

    def failing_unlink(self, *a, **kw):
        if self.name.startswith(".settings.json.") and self.name.endswith(".tmp"):
            raise OSError("injected cleanup failure")
        return real_unlink(self, *a, **kw)

    with monkeypatch.context() as injected:
        injected.setattr(documents.os, "replace", failing_replace)
        injected.setattr(Path, "unlink", failing_unlink)
        assert cmd_apply(args) == 1
    leftovers = sorted(claude.glob(".settings.json.*.tmp"))
    assert leftovers
    return settings, original, leftovers[0]


def test_rename_failure_cleans_temp_and_rolls_back(env, tmp_path, monkeypatch, capsys):
    args = _prepare(env, tmp_path, monkeypatch)
    settings, original = _scenario(env)
    claude = settings.parent
    _assert_no_temp(claude)

    real_replace = documents.os.replace

    def failing_replace(src, dst, *a, **kw):
        if Path(dst).name == "settings.json":
            raise OSError("injected rename failure")
        return real_replace(src, dst, *a, **kw)

    with monkeypatch.context() as injected:
        injected.setattr(documents.os, "replace", failing_replace)
        assert cmd_apply(args) == 1

    output = capsys.readouterr()
    assert "已完整回滚" in output.err
    assert state_transaction(
        env["consumer"], SYNC_TRANSACTION_DIR
    ).read()["status"] == "rolled_back"
    assert settings.read_bytes() == original
    _assert_no_temp(claude)
    assert _git(
        env["consumer"], "status", "--porcelain", "--untracked-files=all"
    ).stdout == ""


def test_success_preserves_indent_and_leaves_no_temp(env):
    settings, original = _scenario(env)
    claude = settings.parent

    reports = migrate_retired_hooks(sync=env["sync"])

    assert any("已移除退役 merge guard PreToolUse" in line for line in reports)
    _assert_no_temp(claude)
    rewritten = settings.read_text(encoding="utf-8")
    assert rewritten != original.decode("utf-8")
    assert json.loads(rewritten)["hooks"]["PreToolUse"][0]["hooks"] == [
        {"type": "command", "command": "python3 scripts/custom_guard.py"}
    ]
    assert '\n    "hooks"' in rewritten


def test_cleanup_failure_reports_incomplete_restore(env, tmp_path, monkeypatch, capsys):
    """R05：临时文件清理失败时不得宣称完整回滚，事务须保留恢复未完成状态与残留路径。"""
    settings, original, leftover = _double_failure_apply(env, tmp_path, monkeypatch)

    err = capsys.readouterr().err
    assert "已完整回滚" not in err
    assert "残留路径" in err
    assert ".settings.json." in err
    tx = state_transaction(env["consumer"], SYNC_TRANSACTION_DIR).read()
    assert tx["status"] == "restore_failed"
    assert any(".settings.json." in path for path in tx["restore_leftovers"])
    assert settings.read_bytes() == original
    assert leftover.exists()
    assert "?? .claude/" in _git(
        env["consumer"], "status", "--porcelain", "--untracked-files=all"
    ).stdout


def test_restore_command_refuses_while_leftover_exists(env, tmp_path, monkeypatch):
    """R05：残留未清除时 rollback --confirm-restore 不得关闭事务。"""
    settings, original, leftover = _double_failure_apply(env, tmp_path, monkeypatch)
    action = argparse.Namespace(sync=env["sync"], action="rollback", confirm_restore=True)
    with pytest.raises(ctx.SyncError, match="残留文件未清除"):
        cmd_transaction(action)
    tx = state_transaction(env["consumer"], SYNC_TRANSACTION_DIR).read()
    assert tx["status"] == "restore_failed"
    assert str(leftover) in tx["restore_leftovers"]
    assert leftover.exists()
    assert open_transactions(env["consumer"])


def test_restore_command_completes_after_leftover_removed(env, tmp_path, monkeypatch):
    """R05：人工清除残留后恢复命令可收尾，下一轮同步不再被阻断。"""
    settings, original, leftover = _double_failure_apply(env, tmp_path, monkeypatch)
    leftover.unlink()
    action = argparse.Namespace(sync=env["sync"], action="rollback", confirm_restore=True)
    assert cmd_transaction(action) == 0
    tx = state_transaction(env["consumer"], SYNC_TRANSACTION_DIR).read()
    assert tx["status"] == "rolled_back"
    assert "restore_leftovers" not in tx
    assert open_transactions(env["consumer"]) == []


def test_settings_migration_conflict_blocks_before_commit(env, tmp_path, monkeypatch, capsys):
    """R01：迁移写入 settings 也进入实际写入集，未豁免时提交前阻断。"""
    args = _prepare(env, tmp_path, monkeypatch)
    settings, original = _scenario(env)
    assert cmd_apply(args) == 1
    err = capsys.readouterr().err
    assert ".claude/settings.json" in err and ".prettierignore" in err
    assert "已完整回滚" in err
    assert state_transaction(
        env["consumer"], SYNC_TRANSACTION_DIR
    ).read()["status"] == "rolled_back"
    assert settings.read_bytes() == original


def test_settings_migration_supported_flow_completes(env, tmp_path, monkeypatch):
    """R01：消费仓豁免 settings 后，迁移→commit→accept 全链可完成。"""
    args = _prepare(env, tmp_path, monkeypatch)
    settings, original = _scenario(env)
    consumer = env["consumer"]
    (consumer / ".prettierignore").write_text(
        "/.claude/settings.json\n" + "\n".join(ctx.PRETTIERIGNORE_TEMPLATE_RULES) + "\n",
        encoding="utf-8",
    )
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "exempt settings")

    assert cmd_apply(args) == 0
    assert settings.read_bytes() != original
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "sync")
    monkeypatch.setattr(txn_module, "_run_tests", lambda root: True)
    action = argparse.Namespace(sync=env["sync"], action="accept", confirm_restore=False)
    assert cmd_transaction(action) == 0
    assert state_transaction(consumer, SYNC_TRANSACTION_DIR).read()["status"] == "accepted"
