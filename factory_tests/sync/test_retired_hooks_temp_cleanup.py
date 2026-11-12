"""R05 回归：retired-hooks 改写 settings.json 必须原子写，rename 失败不留临时文件。

旧写法用固定 ``settings.json.tmp`` + ``Path.replace``：replace 抛错时临时文件
残留，事务虽标记 rolled_back 并输出「已完整回滚」，但下一轮 ``_preflight`` 的
干净工作区检查（含未跟踪文件）会被该残留阻断，形成「报告已回滚却无法重跑」死锁。
改用 ``repo_task.documents.atomic_write_text`` 后，异常分支会清理同目录唯一临时文件。
"""

import argparse
import json
from pathlib import Path

import repo_task.documents as documents
from git_repo_support import install_fake_md_kx
from repo_task.file_transaction import SYNC_TRANSACTION_DIR, state_transaction
from template_sync.apply import cmd_apply
from template_sync.hooks import _RETIRED_MERGE_GUARD_COMMAND, migrate_retired_hooks
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
