"""契约测试受控 Git 环境：产物树内单一真相源（DS L286）。

产物仓不含 factory_tests，契约测试不得 import 其 git_repo_support；
本模块在产物树内提供等价隔离，消除各测试文件重复自包含的 git 替身。
一切 git 调用都经本模块，宿主配置永不透入：

- 丢弃全部透传 ``GIT_*``（``GIT_DIR`` / ``GIT_WORK_TREE`` / ``GIT_INDEX_FILE`` 等会把
  git 重定向到错误仓库；``GIT_CONFIG_COUNT`` / ``GIT_CONFIG_KEY_*`` 可注入任意配置）；
- ``GIT_CONFIG_NOSYSTEM=1`` + ``GIT_CONFIG_GLOBAL=/dev/null`` 屏蔽宿主系统/全局配置
 （宿主 ``commit.gpgsign`` 会让测试提交挂起等签名，宿主 ``core.hooksPath`` 会执行外部钩子）；
- ``init_repo`` 永远用空 template 目录初始化，宿主 git 模板钩子进不了测试仓；
- 新仓显式关闭 ``commit.gpgsign``，固定测试身份，``GIT_TERMINAL_PROMPT=0`` 禁止交互。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def get_controlled_git_env(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """返回屏蔽宿主配置、无外部变量污染、身份确定的 git 子进程环境。"""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_TERMINAL_PROMPT": "0",
    })
    if overrides:
        env.update(overrides)
    return env


def git_run(
    repo: Path,
    *args: str,
    check: bool = True,
    env: dict[str, str] | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    """在受控环境中执行 ``git -C <repo>``，默认 check=True、fail-closed。"""
    run_env = get_controlled_git_env(env)
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
        timeout=timeout,
        env=run_env,
    )


def init_repo(repo: Path, *, branch: str = "main") -> Path:
    """初始化与宿主模板/配置隔离的测试仓库：空 template + 本地身份 + 关 gpgsign。"""
    repo = Path(repo)
    repo.mkdir(parents=True, exist_ok=True)
    template = tempfile.mkdtemp(prefix="empty-git-template-")
    try:
        git_run(repo, "init", "-b", branch, f"--template={template}")
    finally:
        shutil.rmtree(template, ignore_errors=True)
    git_run(repo, "config", "user.email", "test@example.com")
    git_run(repo, "config", "user.name", "test")
    git_run(repo, "config", "commit.gpgsign", "false")
    git_run(repo, "config", "gc.auto", "0")
    return repo
