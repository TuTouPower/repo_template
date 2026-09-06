"""review_scope_fingerprint（repo_task.monitoring）集成测试。

防回归：render_review_prompts 的产物（code/test/general_review_prompt.md）经 --out-dir
落 task 目录后是 untracked 文件，若未进指纹排除列表会参与重算，check_review_status 随之
误判 review_scope=stale。真实 git 仓库，不 stub 指纹函数，直接触达 monitoring 哈希逻辑。
"""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task.monitoring import (
    REVIEW_PROCESS_FILES,
    REVIEW_PROMPT_OUTPUT_FILES,
    review_scope_fingerprint,
)

TASK_REL = "docs/tasks/t001_foo"


def _git(repo: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )


@pytest.fixture()
def git_repo(tmp_path):
    """真实 git 仓库：README + task 目录（task.md/spec.md 已提交到 main）。"""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")
    task_dir = root / TASK_REL
    task_dir.mkdir(parents=True)
    (root / "README.md").write_text("repo\n", encoding="utf-8")
    (task_dir / "task.md").write_text("---\ntid: t001\n---\n", encoding="utf-8")
    (task_dir / "spec.md").write_text("## 契约区\nAC1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    anchor = _git(root, "rev-parse", "HEAD").stdout.strip()
    return root, anchor


def _fingerprint(repo: Path, anchor: str) -> str:
    fp = review_scope_fingerprint(anchor, TASK_REL, repo_root=repo)
    assert fp, "指纹不应为空（git 失败会静默返回空串，测试应暴露）"
    return fp


@pytest.mark.parametrize("filename", REVIEW_PROCESS_FILES)
def test_untracked_review_process_file_keeps_fingerprint(git_repo, filename):
    """task 目录内 untracked review 流程文件（含 *_review_prompt.md 渲染产物）不进指纹。"""
    repo, anchor = git_repo
    before = _fingerprint(repo, anchor)
    (repo / TASK_REL / filename).write_text("untracked 流程内容\n", encoding="utf-8")
    assert _fingerprint(repo, anchor) == before


def test_untracked_foreign_file_changes_fingerprint(git_repo):
    """对照：非流程 untracked 文件仍改指纹——排除不是一刀切放掉 task 目录。"""
    repo, anchor = git_repo
    before = _fingerprint(repo, anchor)
    (repo / TASK_REL / "stray_note.md").write_text("x\n", encoding="utf-8")
    assert _fingerprint(repo, anchor) != before


def test_tracked_change_still_changes_fingerprint(git_repo):
    """对照：review 后 tracked 代码/spec 改动仍改指纹——stale 判定不因排除而失效。"""
    repo, anchor = git_repo
    before = _fingerprint(repo, anchor)
    (repo / "README.md").write_text("repo v2\n", encoding="utf-8")
    assert _fingerprint(repo, anchor) != before


def test_prompt_output_filenames_covered_by_review_process_files():
    """渲染产物输出名全部在指纹排除清单内（防排除列表漏新增产物）。"""
    covered = {name for names in REVIEW_PROMPT_OUTPUT_FILES.values() for name in names}
    assert covered <= set(REVIEW_PROCESS_FILES)


def test_scope_is_stable_across_staging_archive_and_commit(git_repo):
    """The same delivered bytes have one scope, independent of Git lifecycle phase."""
    import shutil
    repo, anchor = git_repo
    (repo / 'new.py').write_text('result = 1\n')
    (repo / TASK_REL / 'spec.md').write_text('## 契约区\nverified AC1\n')
    reviewed = _fingerprint(repo, anchor)
    _git(repo, 'add', '-N', 'new.py')
    assert _fingerprint(repo, anchor) == reviewed
    _git(repo, 'add', '-A')
    assert _fingerprint(repo, anchor) == reviewed
    archive = 'docs/archive/tasks/t001_foo'
    (repo / archive).parent.mkdir(parents=True)
    shutil.move(str(repo / TASK_REL), str(repo / archive))
    assert review_scope_fingerprint(anchor, archive, repo_root=repo) == reviewed
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-m', 'complete task')
    assert review_scope_fingerprint(anchor, archive, repo_root=repo, ref='HEAD') == reviewed


def test_committed_scope_tracks_archived_spec_and_not_primary_dirt(git_repo):
    import shutil
    repo, anchor = git_repo
    archive = 'docs/archive/tasks/t001_foo'
    (repo / archive).parent.mkdir(parents=True)
    shutil.move(str(repo / TASK_REL), str(repo / archive))
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-m', 'archive')
    before = review_scope_fingerprint(anchor, archive, repo_root=repo, ref='HEAD')
    (repo / 'README.md').write_text('unrelated primary worktree dirt\n')
    assert review_scope_fingerprint(anchor, archive, repo_root=repo, ref='HEAD') == before
    (repo / archive / 'spec.md').write_text('changed AC\n')
    _git(repo, 'add', archive)
    _git(repo, 'commit', '-m', 'change archived spec')
    assert review_scope_fingerprint(anchor, archive, repo_root=repo, ref='HEAD') != before


def test_scope_tracks_mode_and_symlink_target(git_repo):
    repo, anchor = git_repo
    before = _fingerprint(repo, anchor)
    (repo / 'README.md').chmod(0o755)
    mode_scope = _fingerprint(repo, anchor)
    assert mode_scope != before
    link = repo / 'link'
    link.symlink_to('README.md')
    link_scope = _fingerprint(repo, anchor)
    assert link_scope != mode_scope
    link.unlink()
    link.symlink_to('missing-target')
    assert _fingerprint(repo, anchor) != link_scope
    _git(repo, 'add', '-A')
    reviewed = _fingerprint(repo, anchor)
    _git(repo, 'commit', '-m', 'mode and link')
    assert review_scope_fingerprint(anchor, TASK_REL, repo_root=repo, ref='HEAD') == reviewed
