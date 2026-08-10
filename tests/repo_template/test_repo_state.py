"""repo_state.py 完整工作树 vs baseline 取数的 real-git 测试。"""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "repo_template" / "repo_state.py"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "note.md").write_text("doc base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _baseline(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def test_deliverable_distinguishes_change_kinds(repo):
    baseline = _baseline(repo)
    # 未变更 tracked 文件
    result = _run(repo, "deliverable", baseline, "tracked.txt")
    assert result.returncode == 0 and "exists unchanged" in result.stdout
    # 已修改未 commit
    (repo / "tracked.txt").write_text("base\nchanged\n", encoding="utf-8")
    result = _run(repo, "deliverable", baseline, "tracked.txt")
    assert result.returncode == 0 and "changed vs baseline" in result.stdout
    # 未跟踪新文件
    (repo / "new.txt").write_text("fresh\n", encoding="utf-8")
    result = _run(repo, "deliverable", baseline, "new.txt")
    assert result.returncode == 0 and "untracked new file" in result.stdout
    # 不存在
    result = _run(repo, "deliverable", baseline, "nope.txt")
    assert result.returncode == 1 and result.stdout.strip() == "missing"
    # 相对 baseline 被删除
    _git(repo, "rm", "-qf", "tracked.txt")
    result = _run(repo, "deliverable", baseline, "tracked.txt")
    assert result.returncode == 1 and result.stdout.strip() == "missing"


def test_deliverable_baseline_unavailable_degrades_to_existence(repo):
    (repo / "new.txt").write_text("fresh\n", encoding="utf-8")
    result = _run(repo, "deliverable", "no-git", "new.txt")
    assert result.returncode == 0 and "baseline unavailable" in result.stdout
    assert "WARNING" in result.stderr
    result = _run(repo, "deliverable", "no-git", "nope.txt")
    assert result.returncode == 1


def test_changed_files_covers_tracked_untracked_deleted_and_exclude(repo):
    baseline = _baseline(repo)
    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    _git(repo, "rm", "-qf", "docs/note.md")
    (repo / "new.txt").write_text("fresh\n", encoding="utf-8")
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "new_doc.md").write_text("doc\n", encoding="utf-8")

    result = _run(repo, "changed-files", baseline)
    paths = set(result.stdout.splitlines())
    assert paths == {"tracked.txt", "docs/note.md", "new.txt", "docs/new_doc.md"}

    result = _run(repo, "changed-files", baseline, "--exclude", "docs/*")
    paths = set(result.stdout.splitlines())
    assert paths == {"tracked.txt", "new.txt"}


def test_added_lines_includes_untracked_bodies_and_honours_exclude(repo):
    baseline = _baseline(repo)
    (repo / "tracked.txt").write_text("base\nprint('debug')\n", encoding="utf-8")
    (repo / "fresh.py").write_text("TODO: fix\nprint('x')\n", encoding="utf-8")
    (repo / "docs" / "new_doc.md").write_text("TODO in docs\n", encoding="utf-8")
    (repo / "ignored").mkdir()
    (repo / "ignored" / "log.txt").write_text("TODO ignored\n", encoding="utf-8")

    result = _run(repo, "added-lines", baseline)
    lines = result.stdout.splitlines()
    assert "print('debug')" in lines
    assert "TODO: fix" in lines  # 未跟踪文件全文算新增
    assert "TODO in docs" in lines
    assert "TODO ignored" not in lines  # gitignore 的不进
    assert "base" not in lines  # 上下文行不进

    result = _run(repo, "added-lines", baseline, "--exclude", "docs/*")
    assert "TODO in docs" not in result.stdout.splitlines()


def test_added_lines_baseline_unavailable_only_untracked(repo):
    (repo / "fresh.py").write_text("new line\n", encoding="utf-8")
    result = _run(repo, "added-lines", "deadbeef" * 5)
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["new line"]
    assert "WARNING" in result.stderr


def test_added_lines_exclude_robust_to_diff_prefix_config(repo):
    # 用户 git config 改变 diff 前缀（b/ → w/ 或无前缀）时 --exclude 不得失效
    _git(repo, "config", "diff.mnemonicPrefix", "true")
    baseline = _baseline(repo)
    (repo / "tracked.txt").write_text("base\nprint('debug')\n", encoding="utf-8")
    (repo / "docs" / "new_doc.md").write_text("TODO in docs\n", encoding="utf-8")
    result = _run(repo, "added-lines", baseline, "--exclude", "docs/*")
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "print('debug')" in lines
    assert "TODO in docs" not in lines


def test_non_git_repo_errors(tmp_path):
    result = _run(tmp_path, "added-lines", "HEAD")
    assert result.returncode != 0
    assert "非 git 仓库" in result.stderr
