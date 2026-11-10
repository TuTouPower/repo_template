"""工具链横切守卫契约测试：DS L221 / L261 / L286 回归。

- git_env_support：宿主 GIT_* 透传、注入式全局配置（gpgsign/hooksPath）必须被隔离；
- render_review_prompts.resolve_out_dir：仓库外拒绝，docs/tasks/ 下拒绝并引导 .scratch；

"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import git_env_support
import render_review_prompts as rrp
from git_env_support import get_controlled_git_env, git_run, init_repo

pytestmark = pytest.mark.contract


# --- DS L286：受控 git 环境隔离语义 ---

def test_controlled_env_drops_host_git_passthrough(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/tmp/evil.git")
    monkeypatch.setenv("GIT_WORK_TREE", "/tmp/evil")
    monkeypatch.setenv("GIT_INDEX_FILE", "/tmp/evil-index")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "/tmp/evil-hooks")
    env = get_controlled_git_env()
    assert "GIT_DIR" not in env
    assert "GIT_WORK_TREE" not in env
    assert "GIT_INDEX_FILE" not in env
    assert "GIT_CONFIG_COUNT" not in env
    assert not any(
        key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_")
        for key in env
    )
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_git_run_ignores_host_git_dir_redirect(tmp_path, monkeypatch):
    """宿主 GIT_DIR 指向不存在的仓库时，helper 调用仍在目标仓执行。"""
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "nonexistent.git"))
    repo = init_repo(tmp_path / "repo")
    (repo / "a.md").write_text("x\n", encoding="utf-8")
    git_run(repo, "add", "-A")
    git_run(repo, "commit", "-m", "init")
    assert git_run(repo, "rev-parse", "--show-toplevel").stdout.strip() == str(repo)


def test_git_run_ignores_injected_gpgsign_config(tmp_path, monkeypatch):
    """经 GIT_CONFIG_* 注入的 commit.gpgsign=true 必须被屏蔽，否则提交因无密钥失败。"""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "commit.gpgsign")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    repo = init_repo(tmp_path / "repo")
    (repo / "a.md").write_text("x\n", encoding="utf-8")
    git_run(repo, "add", "-A")
    git_run(repo, "commit", "-m", "init")  # 隔离失效时此处因 gpg 签名失败
    assert git_run(repo, "log", "--format=%s").stdout.strip() == "init"


def test_init_repo_leaves_no_host_template_hooks(tmp_path):
    """空 template 初始化：宿主模板钩子进不了测试仓（hooks 目录缺席或仅剩 sample）。"""
    repo = init_repo(tmp_path / "repo")
    hooks = repo / ".git" / "hooks"
    leftovers = list(hooks.iterdir()) if hooks.is_dir() else []
    assert all(p.name.endswith(".sample") for p in leftovers)
    assert git_run(repo, "config", "--local", "--get", "commit.gpgsign").stdout.strip() == "false"


# --- DS L221：--out-dir 不得落在 docs/tasks/ 下 ---

@pytest.fixture
def fake_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(rrp, "REPO_ROOT", root)
    return root


def test_out_dir_inside_task_tree_rejected(fake_root):
    with pytest.raises(SystemExit, match="不得落在 docs/tasks"):
        rrp.resolve_out_dir("docs/tasks/t001_x")


def test_out_dir_tasks_root_rejected(fake_root):
    with pytest.raises(SystemExit, match="不得落在 docs/tasks"):
        rrp.resolve_out_dir("docs/tasks")


def test_out_dir_absolute_task_path_rejected(fake_root):
    with pytest.raises(SystemExit, match="不得落在 docs/tasks"):
        rrp.resolve_out_dir(str(fake_root / "docs" / "tasks" / "t001_x"))


def test_out_dir_outside_repo_rejected(fake_root, tmp_path):
    with pytest.raises(SystemExit, match="必须在仓库内"):
        rrp.resolve_out_dir(str(tmp_path / "elsewhere"))


def test_out_dir_scratch_and_stdout_default_allowed(fake_root):
    assert rrp.resolve_out_dir(".scratch/review_prompts") == (
        fake_root / ".scratch" / "review_prompts"
    ).resolve()
    assert rrp.resolve_out_dir("docs/archive/reviews") == (
        fake_root / "docs" / "archive" / "reviews"
    ).resolve()


def test_git_env_support_module_is_single_source():
    """契约测试不再各自携带 git 替身：helper 必须可被统一 import。"""
    assert callable(git_env_support.git_run)
    assert callable(git_env_support.init_repo)
    assert callable(git_env_support.get_controlled_git_env)
