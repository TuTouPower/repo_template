"""repo_state.py 完整工作树 vs baseline 取数的 real-git 测试。"""

import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "repo/.repo_template/scripts" / "repo_state.py"
sys.path.insert(0, str(SCRIPT.parent))
from git_repo_support import get_controlled_git_env
import repo_state


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
        env=get_controlled_git_env(),
    )


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=get_controlled_git_env(),
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
    (repo / "docs" / "progress.md").write_text("doc base\n", encoding="utf-8")
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


# 原 test_deliverable_baseline_unavailable_degrades_to_existence 与
# test_added_lines_baseline_unavailable_only_untracked 固化了已废弃的
# 「baseline 失效仍退出 0」降级契约；按 A03 整体替换为 fail-closed 行为测试。


@pytest.mark.parametrize("bad_baseline", ["no-git", "deadbeef" * 5, "--version", "--output=/tmp/x"])
def test_invalid_baseline_fails_closed_for_all_subcommands(repo, bad_baseline):
    (repo / "new.txt").write_text("fresh\n", encoding="utf-8")
    for subcmd in (
        ("deliverable", "--", bad_baseline, "new.txt"),
        ("changed-files", "--", bad_baseline),
        ("added-lines", "--", bad_baseline),
    ):
        result = _run(repo, *subcmd)
        assert result.returncode != 0
        assert result.stdout == ""
        assert "不可解析为有效 commit" in result.stderr


def test_changed_files_covers_tracked_untracked_deleted_and_exclude(repo):
    baseline = _baseline(repo)
    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    _git(repo, "rm", "-qf", "docs/progress.md")
    (repo / "new.txt").write_text("fresh\n", encoding="utf-8")
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "new_doc.md").write_text("doc\n", encoding="utf-8")

    result = _run(repo, "changed-files", baseline)
    paths = set(result.stdout.splitlines())
    assert paths == {"tracked.txt", "docs/progress.md", "new.txt", "docs/new_doc.md"}

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


def test_non_ascii_paths_raw_and_exclude_consistent(repo):
    """非 ASCII 路径原样输出（非 C 转义引号形式），--exclude 对 tracked/untracked 均生效。"""
    (repo / "中文_tracked.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add non-ascii tracked")
    baseline = _baseline(repo)

    (repo / "中文_tracked.md").write_text("base\nTODO tracked-cn\n", encoding="utf-8")
    (repo / "docs" / "中文_untracked.md").write_text("TODO untracked-cn\n", encoding="utf-8")

    result = _run(repo, "changed-files", baseline)
    paths = set(result.stdout.splitlines())
    assert paths == {"中文_tracked.md", "docs/中文_untracked.md"}

    result = _run(repo, "changed-files", baseline, "--exclude", "docs/*")
    assert set(result.stdout.splitlines()) == {"中文_tracked.md"}

    lines = _run(repo, "added-lines", baseline).stdout.splitlines()
    assert "TODO tracked-cn" in lines
    assert "TODO untracked-cn" in lines

    lines = _run(repo, "added-lines", baseline, "--exclude", "docs/*").stdout.splitlines()
    assert "TODO tracked-cn" in lines
    assert "TODO untracked-cn" not in lines


def test_non_git_repo_errors(tmp_path):
    for subcmd in (
        ("deliverable", "HEAD", "a.txt"),
        ("changed-files", "HEAD"),
        ("added-lines", "HEAD"),
    ):
        result = _run(tmp_path, *subcmd)
        assert result.returncode != 0
        assert result.stdout == ""
        assert "非 git 仓库" in result.stderr


def test_baseline_option_like_revision_is_not_interpreted_by_git(repo):
    assert repo_state._resolve_baseline("--version") == ""
    assert repo_state._resolve_baseline("--output=/tmp/pwned") == ""


def test_added_lines_warns_and_fails_on_unreadable_untracked(repo, monkeypatch, capsys):
    """未跟踪文件读取失败必须可见且返回非零，避免清洁度门禁静默漏检（Issue #78）。"""
    baseline = _baseline(repo)
    (repo / "locked.py").write_text("TODO: hidden\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    real_read_text = Path.read_text

    def denied(self, *args, **kwargs):
        if self.name == "locked.py":
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    rc = repo_state.cmd_added_lines(Namespace(baseline=baseline, exclude=[]))
    assert rc == 1
    stderr = capsys.readouterr().err
    assert "WARNING" in stderr and "locked.py" in stderr


def test_added_lines_emits_nothing_when_untracked_file_vanishes_mid_read(
    repo, monkeypatch, capsys
):
    """未跟踪文件读取途中消失时，已收集的行不得部分写出到 stdout。"""
    baseline = _baseline(repo)
    (repo / "tracked.txt").write_text("base\nprint('tracked')\n", encoding="utf-8")
    (repo / "good.py").write_text("print('good')\n", encoding="utf-8")
    (repo / "vanishing.py").write_text("print('gone')\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    real_read_text = Path.read_text

    def vanish(self, *args, **kwargs):
        if self.name == "vanishing.py":
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", vanish)
    rc = repo_state.cmd_added_lines(Namespace(baseline=baseline, exclude=[]))
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert "vanishing.py" in captured.err


def _run_from(repo: Path, cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=get_controlled_git_env(),
    )


def test_changed_files_from_subdirectory_uses_repo_relative_paths(repo):
    """从子目录调用时，未跟踪与 tracked 路径统一为仓库根相对（DS L257）。"""
    baseline = _baseline(repo)
    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (repo / "docs" / "new_doc.md").write_text("doc\n", encoding="utf-8")

    result = _run_from(repo, repo / "docs", "changed-files", baseline)
    assert result.returncode == 0, result.stderr
    assert set(result.stdout.splitlines()) == {"tracked.txt", "docs/new_doc.md"}


def test_exclude_from_subdirectory_matches_repo_relative_paths(repo):
    """--exclude 与路径口径一致：docs/* 从子目录调用仍能排除 docs/ 下未跟踪文件。"""
    baseline = _baseline(repo)
    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (repo / "docs" / "new_doc.md").write_text("doc\n", encoding="utf-8")

    result = _run_from(repo, repo / "docs", "changed-files", baseline, "--exclude", "docs/*")
    assert result.returncode == 0, result.stderr
    assert set(result.stdout.splitlines()) == {"tracked.txt"}


def test_added_lines_exclude_from_subdirectory(repo):
    baseline = _baseline(repo)
    (repo / "docs" / "fresh.py").write_text("TODO in docs\n", encoding="utf-8")

    result = _run_from(repo, repo / "docs", "added-lines", baseline, "--exclude", "docs/*")
    assert result.returncode == 0, result.stderr
    assert "TODO in docs" not in result.stdout.splitlines()


def test_deliverable_from_subdirectory(repo):
    baseline = _baseline(repo)
    (repo / "docs" / "new_doc.md").write_text("doc\n", encoding="utf-8")

    result = _run_from(repo, repo / "docs", "deliverable", baseline, "new_doc.md")
    assert result.returncode == 0, result.stderr
    assert "untracked new file" in result.stdout
