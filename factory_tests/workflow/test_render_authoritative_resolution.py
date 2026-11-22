"""Issue #98：render 只读消费方必须经状态机解析权威 task 位置。

回归点：主仓 cwd 调用 render 时不再读滞后的 backlog 副本，而是解析到 active
task 的登记 worktree；非 active 状态明确拒绝。
"""
import shutil
import subprocess
import sys

import pytest

from factory_paths import FACTORY_ROOT
from test_task_start_flow import _start, _worktree_path, git_repo

FULL_TOOLKIT = FACTORY_ROOT / "repo/.repo_template"


@pytest.fixture
def render_repo(git_repo):
    """种子仓只含最小工具链；补入完整 render 脚本与 prompt 模板。"""
    dst = git_repo / ".repo_template"
    shutil.rmtree(dst)
    shutil.copytree(
        FULL_TOOLKIT, dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )
    return git_repo


def _render_cli(repo, tid, *extra):
    return subprocess.run(
        [
            sys.executable,
            str(repo / ".repo_template/scripts/render_review_prompts.py"),
            "--tid", tid, *extra,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def test_render_cli_resolves_active_task_from_main_cwd(render_repo):
    """主仓 cwd + --tid：解析到 worktree 的权威 progress.md，输出落在 worktree。"""
    _start(render_repo, "t001")
    worktree = _worktree_path(render_repo, "t001")

    result = _render_cli(render_repo, "t001", "--out-dir", ".scratch/review_prompts")
    assert result.returncode == 0, result.stderr

    out_dir = worktree / ".scratch/review_prompts"
    assert (out_dir / "code_review_prompt.md").is_file()
    assert (out_dir / "test_review_prompt.md").is_file()
    assert not (render_repo / ".scratch/review_prompts/code_review_prompt.md").exists()


def test_render_cli_rejects_stale_backlog_copy(render_repo):
    """backlog 主仓副本不是执行期权威：明确拒绝且提示滞后快照。"""
    result = _render_cli(render_repo, "t001")
    assert result.returncode != 0
    assert "权威状态为 'backlog'" in result.stderr
    assert "滞后快照" in result.stderr
