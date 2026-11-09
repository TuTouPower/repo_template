"""review_20261110 F06：A06 merge-base 端解析失败 fail-closed。

merge-base 命令失败、或 merge-base 处 progress.md 存在但读取/解析失败时，
不得把 base_fm 置 None 后跳过检查；只有 merge-base 处文件不存在才保持现行规则。
"""

from test_dispatch_integration import _git, git_repo  # noqa: F401

from repo_task.context import RepoContext
from repo_task.documents import parse_front_matter_text, write_front_matter
from repo_task.integration import _is_pure_backlog_to_done_member_conflict

TASK_NAME = "t001_alpha"
PROGRESS_PATH = f"docs/tasks/{TASK_NAME}/progress.md"
BRANCH = "f06_branch"


def _commit(repo, message):
    _git(repo, "add", PROGRESS_PATH)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _write_done_progress(repo, backlog_text):
    fm, body = parse_front_matter_text(backlog_text, source=PROGRESS_PATH)
    fm["status"] = "done"
    write_front_matter(repo / PROGRESS_PATH, fm, body)


def test_merge_base_unparseable_progress_is_not_auto_resolved(git_repo):
    """merge-base 处 progress.md 不可解析时，冲突不被自动消解。"""
    backlog_text = _git(git_repo, "show", f"HEAD:{PROGRESS_PATH}").stdout

    (git_repo / PROGRESS_PATH).write_text("这不是合法的 front matter\n", encoding="utf-8")
    base_sha = _commit(git_repo, "base: unparseable progress")
    _git(git_repo, "branch", BRANCH, base_sha)

    (git_repo / PROGRESS_PATH).write_text(backlog_text, encoding="utf-8")
    _commit(git_repo, "main: restore backlog progress")

    _git(git_repo, "checkout", BRANCH)
    _write_done_progress(git_repo, backlog_text)
    _commit(git_repo, "branch: done")
    _git(git_repo, "checkout", "main")

    assert _git(git_repo, "merge-base", "HEAD", BRANCH).stdout.strip() == base_sha
    assert not _is_pure_backlog_to_done_member_conflict(
        BRANCH, [TASK_NAME], PROGRESS_PATH, repo=RepoContext.for_root(git_repo),
    )


def test_merge_base_missing_progress_keeps_main_created_file_behavior(git_repo):
    """分叉后主干新建 progress.md 的场景，行为不变（仍可自动消解）。"""
    backlog_text = _git(git_repo, "show", f"HEAD:{PROGRESS_PATH}").stdout

    _git(git_repo, "rm", PROGRESS_PATH)
    _git(git_repo, "commit", "-m", "base: remove progress")
    base_sha = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _git(git_repo, "branch", BRANCH, base_sha)

    (git_repo / PROGRESS_PATH).write_text(backlog_text, encoding="utf-8")
    _commit(git_repo, "main: create backlog progress")

    _git(git_repo, "checkout", BRANCH)
    _write_done_progress(git_repo, backlog_text)
    _commit(git_repo, "branch: create done progress")
    _git(git_repo, "checkout", "main")

    assert _git(git_repo, "merge-base", "HEAD", BRANCH).stdout.strip() == base_sha
    assert _is_pure_backlog_to_done_member_conflict(
        BRANCH, [TASK_NAME], PROGRESS_PATH, repo=RepoContext.for_root(git_repo),
    )
