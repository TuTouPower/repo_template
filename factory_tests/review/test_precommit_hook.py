"""pre-commit hook 测试。subprocess 真实跑 hook 脚本（git 调用场景），
fake md_kx 经 PATH 注入，验证 staged .md 只读检查，漂移拒绝且 index 与工作区均不变。"""

import os
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS_DIR = FACTORY_ROOT / "repo/.repo_template/scripts"
HOOK_PATH = SCRIPTS_DIR.parent / "hooks" / "pre-commit"


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check,
    )


def _fake_md_kx(tmp_path):
    """把表分隔行归一（幂等）：模拟 md_kx 的 check/format 行为。"""
    script = (
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        "path = Path(sys.argv[-1])\n"
        "text = path.read_text(encoding='utf-8')\n"
        "out = text.replace('|------|------|', '|---|---|')\n"
        "if '--check' in sys.argv:\n"
        "    sys.exit(0 if out == text else 1)\n"
        "path.write_text(out, encoding='utf-8')\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    exe = bin_dir / "md_kx"
    exe.write_text(script, encoding="utf-8")
    exe.chmod(0o755)
    return exe


def _fake_md_kx_failing_on(tmp_path, fail_name):
    """格式除 fail_name 外的文件；对 fail_name 退出 1（模拟中途失败）。"""
    script = (
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        "path = Path(sys.argv[-1])\n"
        f"if path.name == '{fail_name}':\n"
        "    sys.exit(2)\n"
        "text = path.read_text(encoding='utf-8')\n"
        "if '--check' in sys.argv:\n"
        "    sys.exit(0 if '|------|------|' not in text else 1)\n"
        "path.write_text(text.replace('|------|------|', '|---|---|'), encoding='utf-8')\n"
    )
    bin_dir = tmp_path / "bin_fail"
    bin_dir.mkdir(exist_ok=True)
    exe = bin_dir / "md_kx"
    exe.write_text(script, encoding="utf-8")
    exe.chmod(0o755)
    return bin_dir


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "docs" / "archive").mkdir(parents=True)
    (r / "docs" / "archive" / "keep.md").write_text("archived\n", encoding="utf-8")
    (r / "a.md").write_text("|x|y|\n|---|---|\n|1|2|\n", encoding="utf-8")
    (r / "b.py").write_text("x = 1\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-m", "init")
    return r


def _run_hook(repo, tmp_path, *, with_fake_kx=True, bin_dir=None):
    env = dict(os.environ)
    if with_fake_kx:
        fake_bin = bin_dir or _fake_md_kx(tmp_path).parent  # PATH 放目录，不是可执行文件本身
        env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
    else:
        env["PATH"] = "/usr/bin:/bin"  # 系统工具可用，但不含 md_kx
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)], cwd=str(repo), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30,
    )


def test_rename_with_edit_is_format_checked(repo, tmp_path):
    """P2-4：改名并修改的 .md 被 Git 判为 R 时，格式门禁不得漏掉。"""
    body = "|x|y|\n|------|------|\n" + "".join(f"|{i}|{i}|\n" for i in range(1, 9))
    (repo / "a.md").write_text(body, encoding="utf-8")
    _git(repo, "add", "a.md")
    _git(repo, "commit", "-m", "drifted baseline")
    _git(repo, "mv", "a.md", "b.md")
    (repo / "b.md").write_text(body.replace("|1|1|", "|1|9|"), encoding="utf-8")
    _git(repo, "add", "-A")
    status = _git(repo, "diff", "--cached", "--name-status").stdout
    assert status.startswith("R"), status

    result = _run_hook(repo, tmp_path)
    assert result.returncode == 1
    assert "格式漂移" in result.stderr


def test_partial_commit_checks_git_index_file(repo, tmp_path):
    """P2-1：hook 须按 GIT_INDEX_FILE（git commit --only 临时 index）校验编号。"""
    pending = repo / "docs/pending"
    pending.mkdir(parents=True)
    old = pending / "p001_a.md"
    old.write_text(
        '---\npid: "p001"\nslug: "a"\nstatus: "todo"\n---\n# p001 a\n', encoding="utf-8"
    )
    _git(repo, "add", "docs/pending/p001_a.md")
    _git(repo, "commit", "-m", "add pending")

    new = pending / "p001_b.md"
    new.write_text(
        '---\npid: "p001"\nslug: "b"\nstatus: "todo"\n---\n# p001 b\n', encoding="utf-8"
    )
    # 主 index：只保留新路径（无同号）；工作区同时保留旧文件，避免 dirty 干扰。
    _git(repo, "add", "docs/pending/p001_b.md")
    _git(repo, "rm", "--cached", "docs/pending/p001_a.md")

    # 临时 index：HEAD + 只提交的新路径；旧路径仍来自 HEAD → 实际提交树同号。
    tmp_index = tmp_path / "partial.index"
    env_index = dict(os.environ)
    env_index["GIT_INDEX_FILE"] = str(tmp_index)
    subprocess.run(
        ["git", "-C", str(repo), "read-tree", "HEAD"], env=env_index,
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "add", "docs/pending/p001_b.md"], env=env_index,
        check=True, capture_output=True,
    )

    env = dict(os.environ)
    env["GIT_INDEX_FILE"] = str(tmp_index)
    env["PATH"] = str(_fake_md_kx(tmp_path).parent) + os.pathsep + env["PATH"]
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)], cwd=str(repo), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode == 1
    assert "编号校验失败" in result.stderr


def _stage_drifted_md(repo):
    """把 a.md 改成格式漂移并暂存。"""
    (repo / "a.md").write_text("|x|y|\n|------|------|\n|1|2|\n", encoding="utf-8")
    _git(repo, "add", "a.md")


def test_staged_md_drift_rejected_without_mutation(repo, tmp_path):
    _stage_drifted_md(repo)
    before = (repo / "a.md").read_bytes()
    index = _git(repo, "write-tree").stdout
    result = _run_hook(repo, tmp_path)
    assert result.returncode == 1
    assert "格式漂移" in result.stderr
    assert (repo / "a.md").read_bytes() == before
    assert _git(repo, "write-tree").stdout == index


def test_reviewed_formatted_md_commits_unchanged(repo, tmp_path):
    _stage_drifted_md(repo)
    executable = _fake_md_kx(tmp_path)
    subprocess.run([str(executable), str(repo / 'a.md')], check=True)
    _git(repo, 'add', 'a.md')
    index = _git(repo, 'write-tree').stdout
    before = (repo / 'a.md').read_bytes()
    assert _run_hook(repo, tmp_path).returncode == 0
    assert (repo / 'a.md').read_bytes() == before
    assert _git(repo, 'write-tree').stdout == index


def test_no_staged_md_passes_unchanged(repo, tmp_path):
    (repo / "b.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "add", "b.py")
    result = _run_hook(repo, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "|---|---|" not in _git(repo, "diff", "--cached").stdout


def test_no_staged_md_passes_without_md_kx(repo, tmp_path):
    (repo / "b.py").write_text("x = 3\n", encoding="utf-8")
    _git(repo, "add", "b.py")
    result = _run_hook(repo, tmp_path, with_fake_kx=False)
    assert result.returncode == 0, result.stderr


def test_pure_rename_does_not_mutate_index_or_file(repo, tmp_path):
    _git(repo, 'mv', 'a.md', 'renamed.md')
    index = _git(repo, 'write-tree').stdout
    before = (repo / 'renamed.md').read_bytes()
    assert _run_hook(repo, tmp_path).returncode == 0
    assert (repo / 'renamed.md').read_bytes() == before
    assert _git(repo, 'write-tree').stdout == index


def test_missing_md_kx_blocks(repo, tmp_path):
    _stage_drifted_md(repo)
    result = _run_hook(repo, tmp_path, with_fake_kx=False)
    assert result.returncode == 1
    assert "md_kx" in result.stderr


def test_dirty_worktree_rejected(repo, tmp_path):
    _stage_drifted_md(repo)
    (repo / "a.md").write_text(
        "|x|y|\n|------|------|\n|1|2|\n\nextra unstaged\n", encoding="utf-8",
    )
    result = _run_hook(repo, tmp_path)
    assert result.returncode == 1
    assert "不一致" in result.stderr
    cached = _git(repo, "show", ":a.md").stdout
    assert "extra unstaged" not in cached
    assert (repo / "a.md").read_text(encoding="utf-8").endswith("extra unstaged\n")


def test_blacklisted_md_skipped(repo, tmp_path):
    (repo / "docs" / "archive" / "keep.md").write_text("|a|b|\n|------|------|\n", encoding="utf-8")
    _git(repo, "add", "docs/archive/keep.md")
    result = _run_hook(repo, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "|------|------|" in (repo / "docs/archive/keep.md").read_text(encoding="utf-8")


def test_non_ascii_staged_md_checked_without_mutation(repo, tmp_path):
    path = repo / "中文文档.md"
    path.write_text("|x|y|\n|------|------|\n|1|2|\n", encoding="utf-8")
    _git(repo, "add", "中文文档.md")
    before = path.read_bytes()
    index = _git(repo, 'write-tree').stdout
    result = _run_hook(repo, tmp_path)
    assert result.returncode == 1
    assert "格式漂移" in result.stderr
    assert path.read_bytes() == before
    assert _git(repo, 'write-tree').stdout == index


def test_format_failure_rolls_back_without_misleading_dirty_state(repo, tmp_path):
    """格式化中途失败：阻断并回滚，下次运行不再误报「工作区与暂存不一致」。"""
    _stage_drifted_md(repo)  # a.md 漂移并暂存
    z = repo / "z.md"
    z.write_text("|x|y|\n|------|------|\n|1|2|\n", encoding="utf-8")
    _git(repo, "add", "z.md")
    bin_dir = _fake_md_kx_failing_on(tmp_path, "z.md")

    result = _run_hook(repo, tmp_path, bin_dir=bin_dir)
    assert result.returncode == 1
    assert "md_kx" in result.stderr
    assert "|------|------|" in (repo / "a.md").read_text(encoding="utf-8")  # 已回滚

    result = _run_hook(repo, tmp_path, bin_dir=bin_dir)
    assert result.returncode == 1
    assert "不一致" not in result.stderr


def test_review_retention_blocks_only_new_review_over_limit(repo, tmp_path):
    reviews = repo / "docs/reviews"
    for index in range(1, 6):
        directory = reviews / f"review_2026010{index}_120000"
        directory.mkdir(parents=True)
        (directory / "review_general.md").write_text("PASS\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "reviews")
    (repo / "b.py").write_text("x = 4\n", encoding="utf-8")
    _git(repo, "add", "b.py")
    assert _run_hook(repo, tmp_path).returncode == 0

    new = reviews / "review_20260106_120000"
    new.mkdir()
    (new / "review_general.md").write_text("PASS\n", encoding="utf-8")
    _git(repo, "add", "-A")
    result = _run_hook(repo, tmp_path)
    assert result.returncode == 1
    assert "超过 5 个" in result.stderr
    assert "git rm -r -q" in result.stderr
