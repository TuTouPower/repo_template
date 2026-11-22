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


def test_detached_duplicate_worktree_not_ambiguous(render_repo):
    """detached 副本含同一 progress.md，不得被误认为第二个任务 worktree。"""
    _start(render_repo, "t001")
    duplicate = render_repo.parent / "detached_duplicate"
    _git(render_repo, "worktree", "add", "--detach", str(duplicate), "HEAD")

    result = _render_cli(render_repo, "t001")
    assert result.returncode == 0, result.stderr


def test_task_worktree_detached_is_not_recognized(render_repo):
    """真 worktree 切到 detached：不再按任务 worktree 归属，渲染拒绝。"""
    _start(render_repo, "t001")
    worktree = _worktree_path(render_repo, "t001")
    _git(worktree, "checkout", "--detach", "HEAD")

    result = _render_cli(render_repo, "t001")
    assert result.returncode != 0
    assert "Git 登记该路径分支" in result.stderr


def test_main_copy_corruption_does_not_block_worktree_render(render_repo):
    """主仓副本损坏时，健康任务 worktree 的权威内容仍可渲染。"""
    _start(render_repo, "t001")
    (render_repo / "docs/tasks/t001_alpha/progress.md").write_text(
        "损坏的主仓副本\n", encoding="utf-8"
    )

    result = _render_cli(render_repo, "t001")
    assert result.returncode == 0, result.stderr


def test_main_branch_without_task_does_not_block_render(render_repo):
    """主仓切到不含该 task 的分支时，登记 worktree 仍可被解析。"""
    _start(render_repo, "t001")
    _git(render_repo, "checkout", "-b", "old-branch-without-task")
    _git(render_repo, "rm", "-r", "docs/tasks/t001_alpha")
    _git(render_repo, "commit", "-m", "remove task from main branch")

    result = _render_cli(render_repo, "t001")
    assert result.returncode == 0, result.stderr


def test_invalid_utf8_template_fails_closed(render_repo):
    """锁定模板含非法 UTF-8：严格解码失败并报出 commit 与模板路径。"""
    (render_repo / TEMPLATE_REL).write_bytes(b"\xff\xfe invalid utf8\n")
    _git(render_repo, "add", TEMPLATE_REL)
    _git(render_repo, "commit", "-m", "corrupt review template")
    corrupt_sha = _git(render_repo, "rev-parse", "HEAD").stdout.strip()

    _start(render_repo, "t001")
    result = _render_cli(render_repo, "t001")
    assert result.returncode != 0
    assert "UTF-8 解码失败" in result.stderr
    assert TEMPLATE_REL in result.stderr
    assert corrupt_sha[:12] in result.stderr


def test_main_worktree_on_task_branch_is_rejected(render_repo):
    """run-close 后在 main 检出任务分支：主工作区不得被当作任务 worktree。"""
    _start(render_repo, "t001")
    worktree = _worktree_path(render_repo, "t001")
    _git(render_repo, "worktree", "remove", "--force", str(worktree))
    _git(render_repo, "checkout", "t001_alpha")

    result = _render_cli(render_repo, "t001")
    assert result.returncode != 0
    assert "该分支路径" in result.stderr


def test_registered_worktree_missing_progress_reports_path(render_repo):
    """登记 worktree 缺 progress.md：报出登记路径与缺失文件，不误报无登记。"""
    _start(render_repo, "t001")
    worktree = _worktree_path(render_repo, "t001")
    (worktree / "docs/tasks/t001_alpha/progress.md").unlink()

    result = _render_cli(render_repo, "t001")
    assert result.returncode != 0
    assert "缺 docs/tasks/t001_alpha/progress.md" in result.stderr
    assert str(worktree) in result.stderr
