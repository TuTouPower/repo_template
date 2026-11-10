"""C2：RepoContext 缓存 common/state 路径解析，重复访问不再启动 git。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from git_repo_support import git_run
from repo_task import context as ctx


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)
    root = tmp_path / "repo"
    root.mkdir()
    git_run(root, "init", "-b", "main")
    git_run(root, "config", "user.email", "test@example.com")
    git_run(root, "config", "user.name", "test")
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    git_run(root, "add", "-A")
    git_run(root, "commit", "-m", "init")
    return root


def test_repeated_path_access_resolves_common_dir_once(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    real_run = ctx.subprocess.run

    def counting_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and "--git-common-dir" in cmd:
            calls.append(list(cmd))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(ctx.subprocess, "run", counting_run)

    context = ctx.RepoContext.for_root(repo)
    for _ in range(5):
        context.common_dir
        context.state_dir
        context.ledger_path
        context.audit_path
        context.goal_path
        context.statusline_path
        context.sync_state_path

    assert len(calls) == 1
    assert context.state_dir == repo.resolve() / ".git/repo_template"
