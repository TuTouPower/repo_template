"""Issue #98 后续审阅：单 task 定位、恢复流程、调用位置与规则版本无关。"""
import shutil
import subprocess
import sys

import pytest

from factory_paths import FACTORY_ROOT
from test_task_start_flow import _start, _worktree_path, git_repo

FULL_TOOLKIT = FACTORY_ROOT / "repo/.repo_template"
TEMPLATE_REL = ".repo_template/docs/review_prompts/code_prompt.txt"


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


@pytest.fixture
def render_repo(git_repo):
    """种子仓只含最小工具链；补入完整工具链并提交，使 start 锁定的来源含模板。"""
    dst = git_repo / ".repo_template"
    shutil.rmtree(dst)
    shutil.copytree(
        FULL_TOOLKIT, dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "toolkit: full render toolchain")
    return git_repo


def _render_cli(repo, tid, *extra, cwd=None):
    return subprocess.run(
        [
            sys.executable,
            str(repo / ".repo_template/scripts/render_review_prompts.py"),
            "--tid", tid, *extra,
        ],
        cwd=cwd or repo,
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
    assert "无登记 worktree" in result.stderr or "backlog" in result.stderr


def test_render_cli_allows_done_task_for_review_repair(render_repo):
    """review_repair：done + 登记 worktree 仍可重新渲染补审。"""
    from repo_task.documents import parse_front_matter, write_front_matter

    _start(render_repo, "t001")
    worktree = _worktree_path(render_repo, "t001")
    progress = worktree / "docs/tasks/t001_alpha/progress.md"
    fm, body = parse_front_matter(progress)
    fm["status"] = "done"
    write_front_matter(progress, fm, body)

    result = _render_cli(render_repo, "t001")
    assert result.returncode == 0, result.stderr
    assert "===== code_review_prompt =====" in result.stdout


def test_unrelated_corrupt_task_does_not_block_render(render_repo):
    """仅校验目标 task：邻 task progress.md 损坏不影响渲染。"""
    _start(render_repo, "t001")
    (render_repo / "docs/tasks/t002_beta/progress.md").write_text(
        "损坏的 front matter\n", encoding="utf-8"
    )
    result = _render_cli(render_repo, "t001")
    assert result.returncode == 0, result.stderr


def test_render_from_non_default_main_branch(render_repo):
    """主仓临时切到非主干分支：单 task 定位不要求主仓处于主干。"""
    _start(render_repo, "t001")
    _git(render_repo, "checkout", "-b", "temporary-main-branch")

    result = _render_cli(render_repo, "t001")
    assert result.returncode == 0, result.stderr


def test_templates_pinned_to_locked_source(render_repo):
    """规则锁定在 spec_source_sha：主仓模板更新后跨调用位置渲染结果一致。"""
    _start(render_repo, "t001")
    worktree = _worktree_path(render_repo, "t001")

    (render_repo / TEMPLATE_REL).write_text(
        "[code]\nLATEST RULE\n{contract_section}\n", encoding="utf-8"
    )
    _git(render_repo, "add", TEMPLATE_REL)
    _git(render_repo, "commit", "-m", "toolkit: update review rules")

    from_main = _render_cli(render_repo, "t001")
    from_worktree = _render_cli(render_repo, "t001", cwd=worktree)
    assert from_main.returncode == 0, from_main.stderr
    assert from_worktree.returncode == 0, from_worktree.stderr
    assert from_main.stdout == from_worktree.stdout
    assert "LATEST RULE" not in from_main.stdout
