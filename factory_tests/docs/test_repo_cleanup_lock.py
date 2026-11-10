"""repo-cleanup 控制面锁工厂回归：带 scratch 时非阻塞，不带时照常运行。"""

import os
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

SCRIPTS = FACTORY_ROOT / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS))

from repo_task.locks import LockBusyError, file_lock  # noqa: E402

CLEANUP = SCRIPTS / "repo_cleanup.py"


def _env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def test_scratch_cleanup_fails_fast_when_control_lock_held(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-q", "-b", "main"], cwd=repo, check=True, env=_env()
    )
    (repo / ".scratch/exp").mkdir(parents=True)
    scratch_file = repo / ".scratch/exp/tmp.txt"
    scratch_file.write_text("draft\n", encoding="utf-8")
    (repo / "scripts").mkdir()
    log = repo / "scripts/debug.log"
    log.write_text("log\n", encoding="utf-8")

    with file_lock(repo / ".git/repo_template_control.lock"):
        blocked = subprocess.run(
            [sys.executable, str(CLEANUP), "apply"],
            cwd=repo, capture_output=True, text=True, encoding="utf-8", env=_env(),
        )
        assert blocked.returncode != 0
        assert "控制面忙" in blocked.stderr
        assert scratch_file.is_file()
        assert log.is_file()

        allowed = subprocess.run(
            [sys.executable, str(CLEANUP), "apply", "logs"],
            cwd=repo, capture_output=True, text=True, encoding="utf-8", env=_env(),
        )
        assert allowed.returncode == 0, allowed.stderr
        assert not log.exists()
        assert scratch_file.is_file()


def test_lock_busy_error_remains_an_oserror():
    """旧调用方的 except OSError 仍能接住非阻塞锁失败。"""
    assert issubclass(LockBusyError, OSError)
