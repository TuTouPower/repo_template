"""repo_task.privacy 契约测试：路径 token 边界与 index/tree 扫描（A07/A08）。"""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task.privacy import (  # noqa: E402
    PrivacyScanError,
    find_developer_path_leaks,
    scan_git_index_privacy,
    scan_git_tree_privacy,
)

pytestmark = pytest.mark.contract


def _joined(*parts: str) -> str:
    # 用运行时拼接，避免本测试源文件自身出现连续开发者路径。
    return "/".join(parts)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=check,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")
    (root / "tracked.md").write_text("clean\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    return root


@pytest.mark.parametrize(
    "text",
    [
        _joined("", "Users", "alice"),
        _joined("", "Users", "alice", "proj"),
        _joined("", "Users", "张三"),
        _joined("", "home", "bob"),
        _joined("", "home", "bob", "work"),
        _joined("", "home", "李四"),
        _joined("", "root"),
        _joined("", "root", "srv", "app"),
        "C:" + _joined("", "Users", "carol"),
        "C:" + _joined("", "Users", "王五"),
        "C:\\" + "\\".join(("Users", "dave")),
        "C:\\" + "\\".join(("Users", "赵六")),
        "C:\\\\" + "\\\\".join(("Users", "zack")),
        "C:" + _joined("", "Users", "erin", "proj"),
        "file:///Users/alice",
        "file:///Users/张三/repo",
    ],
)
def test_home_tokens_are_detected(text):
    assert find_developer_path_leaks(text)


@pytest.mark.parametrize(
    "text",
    [
        # 相似前缀与嵌套路径：home 目录下的 root 用户是合法家目录 token，但不得被 root 规则按子串命中
        _joined("", "home", "root", "private"),
        "/rooster/data",
        "/Users2/alice",
        _joined("", "homepage", "bob"),
        "C:/Users/Public",
        "C:/Users/Public/shared",
        "C:\\" + "\\".join(("Users", "Public")),
        "C:\\" + "\\".join(("Users", "Public", "shared")),
        "C:\\\\" + "\\\\".join(("Users", "Public")),
        "C:\\\\" + "\\\\".join(("Users", "Public", "shared")),
        "C:\\" + "\\".join(("Users", "Default", "x")),
        "C:/Users/Default User",
        "relative/path/Users/alice",
    ],
)
def test_non_home_tokens_are_not_reported(text):
    leaks = find_developer_path_leaks(text)
    if _joined("", "home", "root") in text:
        assert leaks == [_joined("", "home", "root")]
    else:
        assert leaks == []


def test_unicode_and_json_escaped_and_uri_leaks():
    assert find_developer_path_leaks(_joined("", "Users", "张三")) == [_joined("", "Users", "张三")]
    assert find_developer_path_leaks(_joined("", "home", "李四")) == [_joined("", "home", "李四")]
    assert find_developer_path_leaks("C:" + _joined("", "Users", "王五")) == ["C:" + _joined("", "Users", "王五")]
    assert find_developer_path_leaks(r"C:\Users\赵六") == [r"C:\Users\赵六"]
    assert find_developer_path_leaks(r"C:\\Users\\alice") == [r"C:\\Users\\alice"]
    assert find_developer_path_leaks('{"p": "C:\\\\Users\\\\carol\\\\proj"}') == [r"C:\\Users\\carol"]
    assert find_developer_path_leaks("file:///Users/alice/proj") == ["file:///Users/alice"]
    assert find_developer_path_leaks("file:///Users/张三") == ["file:///Users/张三"]


def test_windows_public_profiles_are_exempt_across_separator_styles():
    assert find_developer_path_leaks("C:/Users/Public") == []
    assert find_developer_path_leaks(r"C:\Users\Public") == []
    assert find_developer_path_leaks(r"C:\\Users\\Public") == []
    assert find_developer_path_leaks("C:/Users/Public/shared") == []
    assert find_developer_path_leaks(r"C:\Users\Public\shared") == []
    assert find_developer_path_leaks(r"C:\\Users\\Public\\shared") == []
    assert find_developer_path_leaks("C:/Users/Default User") == []
    assert find_developer_path_leaks(r"C:\Users\Default User\data") == []


def test_root_rule_does_not_start_mid_path():
    nested = _joined("", "home", "root", "private")
    assert _joined("", "root", "private") not in find_developer_path_leaks(nested)


def test_index_scan_covers_non_markdown_and_index_content(repo):
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text(
        "P = '" + _joined("", "Users", "alice") + "'\n", encoding="utf-8"
    )
    (repo / "notes").write_text("see " + _joined("", "home", "bob") + "\n", encoding="utf-8")
    (repo / "config.json").write_text(
        '{"p": "' + _joined("", "Users", "carol") + '"}\n', encoding="utf-8"
    )
    _git(repo, "add", "-A")

    leaks = scan_git_index_privacy(repo)
    assert any(entry.startswith("src/app.py:1:") for entry in leaks)
    assert any(entry.startswith("notes:1:") for entry in leaks)
    assert any(entry.startswith("config.json:1:") for entry in leaks)


def test_index_scan_blocks_even_when_worktree_is_already_clean(repo):
    """A07：index 有泄漏、工作区已修正时仍须阻断。"""
    path = repo / "leak.md"
    path.write_text("x " + _joined("", "Users", "alice") + "\n", encoding="utf-8")
    _git(repo, "add", "leak.md")
    path.write_text("clean now\n", encoding="utf-8")

    assert scan_git_index_privacy(repo)


def test_index_scan_excludes_archive_and_scratch(repo):
    archived = repo / "docs" / "archive"
    archived.mkdir(parents=True)
    (archived / "old.md").write_text(_joined("", "Users", "alice"), encoding="utf-8")
    scratch = repo / ".scratch"
    scratch.mkdir()
    (scratch / "tmp.md").write_text(_joined("", "home", "bob"), encoding="utf-8")
    _git(repo, "add", "-A")

    assert scan_git_index_privacy(repo) == []


def test_tree_scan_reads_committed_snapshot(repo):
    (repo / "doc.md").write_text(_joined("", "Users", "alice") + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "leak")

    assert scan_git_tree_privacy(repo, "HEAD")
    assert scan_git_tree_privacy(repo, "HEAD~1") == []


def test_scan_fails_closed_when_candidate_content_unreadable(repo, monkeypatch):
    import repo_task.privacy as privacy

    def boom(*_args, **_kwargs):
        raise privacy.PrivacyScanError("simulated read failure")

    monkeypatch.setattr(privacy, "read_blobs", boom)
    (repo / "a.md").write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "-A")
    with pytest.raises(PrivacyScanError, match="simulated read failure"):
        scan_git_index_privacy(repo)
