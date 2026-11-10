"""2026-11-10 审阅 F14 回归：token 尾点、比较器重命名与 pre-commit 文案分离。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

from git_repo_support import get_controlled_git_env, git_run
from repo_task import privacy

HOOK = FACTORY_ROOT / "repo/.repo_template/hooks/pre-commit"


def _home(*parts: str) -> str:
    return "/".join(("", *parts))


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


def test_reported_token_trims_trailing_sentence_dot():
    token = _home("Users", "alice")
    assert privacy.find_developer_path_leaks(token + ".") == [token]
    assert privacy.find_developer_path_leaks("见 " + token + ". 结束") == [token]


def test_renamed_file_content_is_scanned_as_new_path(consumer):
    token = _home("Users", "renameuser")
    (consumer / "old.md").write_text("clean\n", encoding="utf-8")
    git_run(consumer, "add", "old.md")
    git_run(consumer, "commit", "-m", "clean old path")
    (consumer / "old.md").write_text(token + "/proj\n", encoding="utf-8")
    git_run(consumer, "add", "old.md")
    git_run(consumer, "commit", "-m", "leak in old path")
    git_run(consumer, "mv", "old.md", "new.md")

    # rename-aware：纯移动不把旧内容当新增；历史泄漏不阻断本次移动。
    assert privacy.scan_privacy_changes(consumer) == []

    (consumer / "new.md").write_text("clean\n" + token + "/added\n", encoding="utf-8")
    git_run(consumer, "add", "new.md")
    assert privacy.scan_privacy_changes(consumer) == [f"new.md:2: {token}"]


def test_precommit_reports_id_failure_and_privacy_failure_distinctly(consumer):
    pending = consumer / "docs" / "pending"
    pending.mkdir(parents=True)
    (pending / "p001_first.md").write_text("first\n", encoding="utf-8")
    (pending / "p001_second.md").write_text("second\n", encoding="utf-8")
    git_run(consumer, "add", "-A")

    id_result = _run_hook(consumer)
    assert id_result.returncode == 1
    assert "编号校验失败" in id_result.stderr
    assert "隐私扫描失败" not in id_result.stderr


def test_precommit_reports_privacy_scan_failure_separately(consumer):
    (consumer / "bad.txt").write_bytes(b"\xff\xff")
    git_run(consumer, "add", "bad.txt")

    privacy_result = _run_hook(consumer)
    assert privacy_result.returncode == 1
    assert "隐私扫描失败" in privacy_result.stderr
    assert "编号校验失败" not in privacy_result.stderr
