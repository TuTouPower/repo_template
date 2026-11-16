"""2026-11-10 审阅 F14 回归：pre-commit 编号校验 fail-closed。"""

import os
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

from git_repo_support import get_controlled_git_env, git_run

HOOK = FACTORY_ROOT / "repo/.repo_template/hooks/pre-commit"


@pytest.fixture
def consumer(tmp_path: Path, monkeypatch) -> Path:
    env = get_controlled_git_env()
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        if key.startswith("GIT_"):
            monkeypatch.setenv(key, value)
    root = tmp_path / "consumer"
    root.mkdir()
    git_run(root, "init", "-b", "main")
    git_run(root, "config", "user.email", "test@example.com")
    git_run(root, "config", "user.name", "test")
    (root / ".gitignore").write_text(".scratch/\n", encoding="utf-8")
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    git_run(root, "add", "-A")
    git_run(root, "commit", "-m", "init")
    return root


def _run_hook(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK)], cwd=str(root), env=get_controlled_git_env(),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )


def test_precommit_reports_id_failure_fail_closed(consumer):
    pending = consumer / "docs" / "pending"
    pending.mkdir(parents=True)
    (pending / "p001_first.md").write_text("first\n", encoding="utf-8")
    (pending / "p001_second.md").write_text("second\n", encoding="utf-8")
    git_run(consumer, "add", "-A")

    id_result = _run_hook(consumer)
    assert id_result.returncode == 1
    assert "编号校验失败" in id_result.stderr
