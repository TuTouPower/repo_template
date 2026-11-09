"""md_format.py 包装脚本测试。用 tmp_path 下真实 git 仓库验证选文件、黑名单、缺二进制、格式漂移。"""

import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from git_repo_support import get_controlled_git_env
import md_format as mdf


from repo_task.context import RepoContext

def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
        env=get_controlled_git_env(),
    )


def _fake_md_kx(tmp_path):
    """造一个假的 md_kx 可执行：把表分隔行归一（幂等），模拟 check/format 行为。"""
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
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "a.md").write_text("|x|y|\n|------|------|\n|1|2|\n", encoding="utf-8")
    (r / "docs" / "archive").mkdir(parents=True)
    (r / "docs" / "archive" / "keep.md").write_text("archived\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-m", "init")
    return r


def test_collect_all_excludes_blacklist(repo):
    rels = mdf.collect_all(repo=RepoContext.for_root(repo))
    assert "a.md" in rels
    assert not any("docs/archive/" in rel for rel in rels)


def test_collect_changed_includes_untracked(repo):
    (repo / "b.md").write_text("new\n", encoding="utf-8")
    rels = mdf.collect_changed(repo=RepoContext.for_root(repo))
    assert "b.md" in rels


def test_format_rejects_blacklist(repo):
    with pytest.raises(mdf.MdFormatError, match="黑名单"):
        mdf.format_paths(["docs/archive/keep.md"], repo=RepoContext.for_root(repo))


@pytest.mark.parametrize(
    "bypass",
    [
        "./docs/archive/keep.md",
        "docs/./archive/keep.md",
        "docs/archive/../archive/keep.md",
        "docs/pending/../archive/keep.md",
    ],
)
def test_format_rejects_blacklist_prefix_bypass(repo, bypass):
    """先 resolve 再判定黑名单：./、../ 与绝对路径都不得绕过（DS L255）。"""
    with pytest.raises(mdf.MdFormatError, match="黑名单"):
        mdf.format_paths([bypass], repo=RepoContext.for_root(repo))


def test_format_rejects_absolute_blacklist_path(repo):
    absolute = (repo / "docs/archive/keep.md").as_posix()
    with pytest.raises(mdf.MdFormatError, match="黑名单"):
        mdf.format_paths([absolute], repo=RepoContext.for_root(repo))


def test_main_rejects_blacklist_prefix_bypass(repo, capsys):
    with pytest.raises(SystemExit) as exc:
        mdf.main(["./docs/archive/keep.md"], repo=RepoContext.for_root(repo))
    assert exc.value.code == 1
    assert "黑名单" in capsys.readouterr().err


def test_main_rejects_explicit_blacklist_path(repo, capsys):
    """CLI 显式点名黑名单路径必须报错，不得静默 exit 0（Issue #76）。"""
    with pytest.raises(SystemExit) as exc:
        mdf.main(["docs/archive/keep.md"], repo=RepoContext.for_root(repo))
    assert exc.value.code == 1
    assert "黑名单" in capsys.readouterr().err


def test_format_missing_binary(repo, monkeypatch):
    monkeypatch.setattr(mdf, "find_md_kx", lambda: None)
    with pytest.raises(mdf.MdFormatError, match="找不到 md_kx"):
        mdf.format_paths(["a.md"], repo=RepoContext.for_root(repo))


def test_format_changes_file(repo, tmp_path, monkeypatch):
    exe = _fake_md_kx(tmp_path)
    monkeypatch.setattr(mdf, "find_md_kx", lambda: str(exe))
    changed = mdf.format_paths(["a.md"], repo=RepoContext.for_root(repo))
    assert changed == ["a.md"]
    assert "|---|---|" in (repo / "a.md").read_text(encoding="utf-8")


def test_format_failure_rolls_back_already_formatted(repo, tmp_path, monkeypatch):
    """中途失败：已格式化文件回滚，不留「已格式化未暂存」状态。"""
    bin_dir = tmp_path / "bin_fail"
    bin_dir.mkdir()
    exe = bin_dir / "md_kx"
    exe.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        "path = Path(sys.argv[-1])\n"
        "if path.name == 'b.md':\n"
        "    sys.exit(2)\n"
        "text = path.read_text(encoding='utf-8')\n"
        "path.write_text(text.replace('|------|------|', '|---|---|'), encoding='utf-8')\n"
    )
    exe.chmod(0o755)
    monkeypatch.setattr(mdf, "find_md_kx", lambda: str(exe))
    (repo / "b.md").write_text("|x|y|\n|------|------|\n|1|2|\n", encoding="utf-8")
    before = (repo / "a.md").read_bytes()

    with pytest.raises(mdf.MdFormatError, match="回滚"):
        mdf.format_paths(["a.md", "b.md"], repo=RepoContext.for_root(repo))
    assert (repo / "a.md").read_bytes() == before


def test_check_detects_drift(repo, tmp_path, monkeypatch):
    exe = _fake_md_kx(tmp_path)
    monkeypatch.setattr(mdf, "find_md_kx", lambda: str(exe))
    drift = mdf.format_paths(["a.md"], check=True, repo=RepoContext.for_root(repo))
    assert drift == ["a.md"]  # 未格式化，漂移
    # 格式化后无漂移
    mdf.format_paths(["a.md"], repo=RepoContext.for_root(repo))
    drift = mdf.format_paths(["a.md"], check=True, repo=RepoContext.for_root(repo))
    assert drift == []


def test_main_check_all_exits_nonzero_on_drift(repo, tmp_path, monkeypatch, capsys):
    exe = _fake_md_kx(tmp_path)
    monkeypatch.setattr(mdf, "find_md_kx", lambda: str(exe))
    with pytest.raises(SystemExit) as exc:
        mdf.main(["--check"], repo=RepoContext.for_root(repo))
    assert exc.value.code == 1
    assert "格式漂移" in capsys.readouterr().err


def test_format_rejects_paths_outside_repo(repo):
    """验证 format_paths 明确拒绝仓库外的路径（Issue #17）。"""
    with pytest.raises(mdf.MdFormatError, match="越过仓库边界"):
        mdf.format_paths(["../outside.md"], repo=RepoContext.for_root(repo))


def test_format_timeout_rolls_back_already_formatted(repo, tmp_path, monkeypatch):
    """验证批量格式化中途发生超时时完整回滚已格式化的文件快照（Issue #19）。"""
    exe = _fake_md_kx(tmp_path)
    monkeypatch.setattr(mdf, "find_md_kx", lambda: str(exe))
    (repo / "b.md").write_text("|x|y|\n|------|------|\n|1|2|\n", encoding="utf-8")
    before_a = (repo / "a.md").read_bytes()

    real_run = subprocess.run
    def flaky_run(cmd, *args, **kwargs):
        if str(repo / "b.md") in cmd:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(mdf, "subprocess", SimpleNamespace(**{**vars(subprocess), "run": flaky_run}))

    with pytest.raises(mdf.MdFormatError, match="已回滚"):
        mdf.format_paths(["a.md", "b.md"], repo=RepoContext.for_root(repo))
    assert (repo / "a.md").read_bytes() == before_a


def test_format_reports_all_restore_failures(repo, tmp_path, monkeypatch):
    """部分恢复失败时 MdFormatError 须列出未恢复路径，不得声称已回滚（DS L256）。"""
    bin_dir = tmp_path / "bin_fail_restore"
    bin_dir.mkdir()
    exe = bin_dir / "md_kx"
    exe.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        "path = Path(sys.argv[-1])\n"
        "if path.name == 'b.md':\n"
        "    sys.exit(2)\n"
        "text = path.read_text(encoding='utf-8')\n"
        "path.write_text(text.replace('|------|------|', '|---|---|'), encoding='utf-8')\n"
    )
    exe.chmod(0o755)
    monkeypatch.setattr(mdf, "find_md_kx", lambda: str(exe))
    (repo / "b.md").write_text("|x|y|\n|------|------|\n|1|2|\n", encoding="utf-8")

    real_write_bytes = Path.write_bytes

    def flaky_write_bytes(self, data):
        if self.name == "a.md":
            raise OSError("simulated restore failure")
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky_write_bytes)

    with pytest.raises(mdf.MdFormatError) as exc:
        mdf.format_paths(["a.md", "b.md"], repo=RepoContext.for_root(repo))
    message = str(exc.value)
    assert "未恢复" in message
    assert "a.md" in message
    assert "simulated restore failure" in message
    assert "已回滚本次已格式化" not in message

