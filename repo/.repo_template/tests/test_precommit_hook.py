"""pre-commit hook 测试。subprocess 真实跑 hook 脚本（git 调用场景），
fake md_kx 经 PATH 注入，验证 staged .md 被格式化并重新暂存。"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
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
    bin_dir.mkdir()
    exe = bin_dir / "md_kx"
    exe.write_text(script, encoding="utf-8")
    exe.chmod(0o755)
    return exe


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


def _run_hook(repo, tmp_path, *, with_fake_kx=True):
    env = dict(os.environ)
    if with_fake_kx:
        fake_bin = _fake_md_kx(tmp_path).parent  # PATH 放目录，不是可执行文件本身
        env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
    else:
        env["PATH"] = "/usr/bin:/bin"  # 系统工具可用，但不含 md_kx
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)], cwd=str(repo), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30,
    )


def _stage_drifted_md(repo):
    """把 a.md 改成格式漂移并暂存。"""
    (repo / "a.md").write_text("|x|y|\n|------|------|\n|1|2|\n", encoding="utf-8")
    _git(repo, "add", "a.md")


def test_staged_md_formatted_and_readded(repo, tmp_path):
    _stage_drifted_md(repo)
    result = _run_hook(repo, tmp_path)
    assert result.returncode == 0, result.stderr
    # 文件已格式化
    assert "|---|---|" in (repo / "a.md").read_text(encoding="utf-8")
    # 重新暂存：cached 内容为格式化后
    cached = _git(repo, "diff", "--cached", "--", "a.md").stdout
    assert "|------|------|" not in cached


def test_no_staged_md_passes_unchanged(repo, tmp_path):
    (repo / "b.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "add", "b.py")
    result = _run_hook(repo, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "|---|---|" not in _git(repo, "diff", "--cached").stdout


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
