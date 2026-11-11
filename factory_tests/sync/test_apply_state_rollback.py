"""P1-2 回归：apply 成功后 CLI rollback 恢复 common-dir state 与工作区工具文件。"""

from __future__ import annotations

import json
import sys

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS_DIR = FACTORY_ROOT / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task.file_transaction import SYNC_TRANSACTION_DIR, state_transaction
from template_sync.cli import main as repo_sync_main

from test_repo_sync import env, _commit_consumer_baseline


def test_apply_then_cli_rollback_restores_common_dir_state_and_tool_files(env):
    src = env["src"]
    consumer = env["consumer"]
    state_path = env["sync"].state_path
    old_toolkit = consumer / ".repo_template/scripts/task.py"
    old_toolkit.parent.mkdir(parents=True, exist_ok=True)
    old_toolkit.write_text("OLD TASK TOOL\n", encoding="utf-8")

    def cli(*argv: str) -> int:
        with pytest.raises(SystemExit) as raised:
            repo_sync_main(["--consumer", str(consumer), *argv])
        return raised.value.code

    assert cli("init", "--source", str(src)) == 0
    state_before = state_path.read_bytes()
    assert json.loads(state_before)["last_synced_commit"] is None

    _commit_consumer_baseline(consumer)
    assert cli("apply", "--skip-tests") == 0
    assert json.loads(state_path.read_bytes())["last_synced_commit"] == env["head"]
    assert not (old_toolkit.is_file() and old_toolkit.read_text(encoding="utf-8") == "OLD TASK TOOL\n")

    data = state_transaction(consumer, SYNC_TRANSACTION_DIR).read()
    assert "sync_state.json" in data["state_paths"]

    assert cli("transaction", "rollback", "--confirm-restore") == 0

    assert state_path.read_bytes() == state_before
    assert json.loads(state_path.read_bytes())["last_synced_commit"] is None
    assert old_toolkit.read_text(encoding="utf-8") == "OLD TASK TOOL\n"
