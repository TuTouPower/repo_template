"""repo_task.privacy 契约测试：路径 token 边界、编码与 index/tree 扫描（A07/A08）。

本文件随产物分发，含大量家目录路径样例；一律用运行期拼接构造，避免模板自带
测试夹具自身被隐私门禁命中（P0-1）。契约测试不能依赖工厂测试模块，受控 Git
环境统一复用产物树内 git_env_support（单一真相源，DS L286）。
"""

import sys
from pathlib import Path

import pytest

from git_env_support import git_run as _git
from git_env_support import init_repo

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task import git_ops  # noqa: E402
from repo_task.context import TaskDataError  # noqa: E402
from repo_task.privacy import (  # noqa: E402
    MAX_SCAN_BLOB_BYTES,
    PrivacyScanError,
    decode_candidate_bytes,
    find_developer_path_leaks,
    is_binary_candidate,
    is_privacy_candidate,
    scan_candidate_bytes,
    scan_git_index_privacy,
    scan_git_tree_privacy,
    scan_text_privacy_leaks,
)

pytestmark = pytest.mark.contract


# --- 运行期拼接 helper：源码内绝不出现连续开发者家目录路径 ---
def _joined(*parts: str) -> str:
    return "/".join(parts)


def _win(*parts: str) -> str:
    return "C:\\" + "\\".join(parts)


def _win_double(*parts: str) -> str:
    return "C:\\\\" + "\\\\".join(parts)


def _win_forward(*parts: str) -> str:
    return "C:" + _joined("", *parts)


def _file_uri(*parts: str) -> str:
    return "file://" + _joined("", *parts)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = init_repo(tmp_path / "repo")
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
        _win_forward("Users", "carol"),
        _win_forward("Users", "王五"),
        _win("Users", "dave"),
        _win("Users", "赵六"),
        _win_double("Users", "zack"),
        _win_forward("Users", "erin", "proj"),
        _file_uri("Users", "alice"),
        _file_uri("Users", "张三", "repo"),
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
        _win_forward("Users", "Public"),
        _win_forward("Users", "Public", "shared"),
        _win("Users", "Public"),
        _win("Users", "Public", "shared"),
        _win_double("Users", "Public"),
        _win_double("Users", "Public", "shared"),
        _win("Users", "Default", "x"),
        _win_forward("Users", "Default User"),
        "relative/path" + _joined("", "Users", "alice"),
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
    token = _win_forward("Users", "王五")
    assert find_developer_path_leaks(token) == [token]
    assert find_developer_path_leaks(_win("Users", "赵六")) == [_win("Users", "赵六")]
    assert find_developer_path_leaks(_win_double("Users", "alice")) == [_win_double("Users", "alice")]
    escaped = '{"p": "' + _win_double("Users", "carol", "proj") + '"}'
    assert find_developer_path_leaks(escaped) == [_win_double("Users", "carol")]
    assert find_developer_path_leaks(_file_uri("Users", "alice", "proj")) == [_file_uri("Users", "alice")]
    assert find_developer_path_leaks(_file_uri("Users", "张三")) == [_file_uri("Users", "张三")]


def test_windows_public_profiles_are_exempt_across_separator_styles():
    assert find_developer_path_leaks(_win_forward("Users", "Public")) == []
    assert find_developer_path_leaks(_win("Users", "Public")) == []
    assert find_developer_path_leaks(_win_double("Users", "Public")) == []
    assert find_developer_path_leaks(_win_forward("Users", "Public", "shared")) == []
    assert find_developer_path_leaks(_win("Users", "Public", "shared")) == []
    assert find_developer_path_leaks(_win_double("Users", "Public", "shared")) == []
    assert find_developer_path_leaks(_win_forward("Users", "Default User")) == []
    assert find_developer_path_leaks(_win("Users", "Default User", "data")) == []


def test_root_rule_does_not_start_mid_path():
    nested = _joined("", "home", "root", "private")
    assert _joined("", "root", "private") not in find_developer_path_leaks(nested)


# --- P0-2：token 边界五类变体 ---
@pytest.mark.parametrize(
    "text",
    [
        "见" + _joined("", "Users", "alice", "proj"),                 # 汉字紧邻（ASCII 边界）
        _joined("", "users", "alice", "proj"),                        # Users 段大小写不敏感
        _win_forward("users", "alice"),                               # Windows 盘符 Users 大小写不敏感
        "C:" + "//" + "//".join(("Users", "alice")),                  # 1+ 连续分隔符（C://Users//）
        "//" + _joined("Users", "alice", "proj"),                     # 前置 //
        _win("users", "alice"),                                       # Windows 反斜杠大小写不敏感
    ],
)
def test_token_boundary_variants_are_detected(text):
    assert find_developer_path_leaks(text)


# --- DS L227：用户名不再贪婪吞掉后续词 ---
@pytest.mark.parametrize(
    "text",
    [
        _win("Users", "Public") + " 下的日志",
        _win_forward("Users", "Public") + " 下的日志",
        _win("Users", "Default User") + " 下的日志",
    ],
)
def test_windows_username_does_not_swallow_following_words(text):
    assert find_developer_path_leaks(text) == []


def test_posix_username_stops_at_whitespace():
    token = _joined("", "Users", "alice")
    assert find_developer_path_leaks(token + " 的日志") == [token]


def test_all_users_multitoken_profile_is_exempt():
    assert find_developer_path_leaks(_win("Users", "All Users", "shared")) == []
    assert find_developer_path_leaks(_win_forward("Users", "All Users")) == []


def test_system_profile_prefix_does_not_exempt_real_username():
    assert find_developer_path_leaks(_win("Users", "Defaultish", "proj"))
    assert find_developer_path_leaks(_win("Users", "Publicity", "proj"))


# --- DS L228：BOM / UTF-16 / 未知编码 fail-closed ---
@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
def test_utf16_without_bom_is_detected(encoding):
    token = _joined("", "Users", "karson")
    data = (token + "/proj\n").encode(encoding)
    assert scan_candidate_bytes(data, label="a.md") == [f"a.md:1: {token}"]


def test_utf16_bom_is_detected():
    token = _joined("", "Users", "karson")
    data = (token + "/proj\n").encode("utf-16")
    assert scan_candidate_bytes(data, label="a.md") == [f"a.md:1: {token}"]


def test_utf8_bom_is_detected():
    token = _joined("", "home", "bob")
    data = b"\xef\xbb\xbf" + (token + "\n").encode()
    assert scan_candidate_bytes(data, label="a.md") == [f"a.md:1: {token}"]


def test_unknown_encoding_fails_closed():
    with pytest.raises(PrivacyScanError, match="编码无法判定"):
        decode_candidate_bytes(b"\x81\xfe\x00\xff\x81\x00\xfe", label="a.md")


def test_index_scan_detects_utf16_blob(repo):
    token = _joined("", "Users", "karson")
    (repo / "u16.md").write_bytes((token + "/proj\n").encode("utf-16-le"))
    _git(repo, "add", "-A")
    assert scan_git_index_privacy(repo)


# --- DS L213：单一文本入口可对 commit message 等任意文本调用 ---
def test_text_entry_scans_arbitrary_commit_message():
    token = _joined("", "Users", "alice")
    message = "feat: 完成\n\n修改路径 " + token + "/proj\n"
    assert scan_text_privacy_leaks(message, label="commit-message") == [
        f"commit-message:3: {token}"
    ]


# --- DS L211：未暂存软链用 lstat，不跟随目标 ---
def test_extra_worktree_symlink_does_not_follow_target(repo):
    token = _joined("", "Users", "alice")
    (repo / "target.md").write_text("leak " + token + "\n", encoding="utf-8")
    (repo / "link.md").symlink_to("target.md")
    # 旧实现 is_file()/read_bytes() 会跟随软链读到目标泄漏；新实现只扫链接文本。
    assert scan_git_index_privacy(repo, extra_worktree_paths=["link.md"]) == []


def test_extra_worktree_symlink_text_leak_is_detected(repo):
    token = _joined("", "Users", "alice")
    (repo / "link.md").symlink_to(token + "/outside.md")
    leaks = scan_git_index_privacy(repo, extra_worktree_paths=["link.md"])
    assert any(entry.startswith("link.md:1:") for entry in leaks)


# --- P0-1：模板自带 tests/ 目录是扫描候选，绝不豁免 ---
def test_template_tests_dir_is_not_exempt():
    assert is_privacy_candidate(".repo_template/tests/test_privacy.py")
    assert is_privacy_candidate(".repo_template/tests/any.py")
    assert not is_privacy_candidate("docs/archive/old.md")
    assert not is_privacy_candidate(".scratch/tmp.md")


# --- index / tree 扫描 ---
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


# --- P1-4：读取前按后缀/大小预过滤，复用 git_ops.batch_cat_file ---
def test_binary_suffix_is_excluded_from_candidates():
    assert is_binary_candidate("a.png")
    assert is_binary_candidate("a.PNG")
    assert not is_binary_candidate("a.md")


def test_binary_suffix_is_filtered_before_git_read(repo, monkeypatch):
    token = _joined("", "Users", "alice")
    (repo / "image.png").write_bytes((token + "\n").encode())
    _git(repo, "add", "-A")
    png_sha = _git(repo, "ls-files", "-s", "--", "image.png").stdout.split()[1]

    seen: list[str] = []
    real_sizes = git_ops.batch_object_sizes
    real_cat = git_ops.batch_cat_file

    def spy_sizes(oids, **kw):
        seen.extend(oids)
        return real_sizes(oids, **kw)

    def spy_cat(oids, **kw):
        seen.extend(oids)
        return real_cat(oids, **kw)

    monkeypatch.setattr(git_ops, "batch_object_sizes", spy_sizes)
    monkeypatch.setattr(git_ops, "batch_cat_file", spy_cat)
    assert scan_git_index_privacy(repo) == []
    assert png_sha not in seen


def test_oversized_blob_is_scanned_per_object_instead_of_failing_whole_scan(
    repo, monkeypatch
):
    """超大对象不再让整体扫描失败：逐个顺序读取扫描（P1-1 根因修正）。

    旧语义（任一对象超限则整体 fail-closed）已删除：正常二进制/office/大文本
    会阻断 finish/commit/integrate 发布链路，属过严阻断。现行为：超限对象逐个
    读入扫描，干净通过、泄漏检出；只有超过硬上限的对象才显式失败。
    """
    token = _joined("", "Users", "alice")
    (repo / "big.md").write_text("clean\n", encoding="utf-8")
    (repo / "big_leak.md").write_text("leak " + token + "/proj\n", encoding="utf-8")
    _git(repo, "add", "-A")
    real = git_ops.batch_object_sizes

    def inflated(oids, **kw):
        return {oid: MAX_SCAN_BLOB_BYTES + 1 for oid in real(oids, **kw)}

    monkeypatch.setattr(git_ops, "batch_object_sizes", inflated)
    leaks = scan_git_index_privacy(repo)
    assert any(entry.startswith("big_leak.md:1:") and token in entry for entry in leaks)
    assert not any(entry.startswith("big.md") for entry in leaks)


def test_blob_beyond_hard_cap_fails_closed(repo, monkeypatch):
    from repo_task.privacy import MAX_SCAN_BLOB_HARD_CAP

    (repo / "huge.md").write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "-A")
    real = git_ops.batch_object_sizes

    def inflated(oids, **kw):
        return {oid: MAX_SCAN_BLOB_HARD_CAP + 1 for oid in real(oids, **kw)}

    monkeypatch.setattr(git_ops, "batch_object_sizes", inflated)
    with pytest.raises(PrivacyScanError, match="无法安全扫描"):
        scan_git_index_privacy(repo)


def test_index_scan_reuses_git_ops_batch_reader(repo, monkeypatch):
    for index in range(12):
        (repo / f"f{index}.md").write_text(f"clean {index}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    calls: list[int] = []
    real = git_ops.batch_cat_file

    def spy(oids, **kw):
        calls.append(len(oids))
        return real(oids, **kw)

    monkeypatch.setattr(git_ops, "batch_cat_file", spy)
    assert scan_git_index_privacy(repo) == []
    assert calls and sum(calls) >= 13  # tracked.md + 12 个新文件


# 旧测试 patch 了已删除的 privacy.read_blobs（重复 cat-file 解析实现）。
# 读取入口已统一为 git_ops.batch_cat_file 严格解析器，改为对该入口注入失败，
# 仍验证「候选内容不可读时 fail-closed」这一语义。
def test_scan_fails_closed_when_candidate_content_unreadable(repo, monkeypatch):
    def boom(*_args, **_kwargs):
        raise TaskDataError("simulated read failure")

    monkeypatch.setattr(git_ops, "batch_cat_file", boom)
    (repo / "a.md").write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "-A")
    with pytest.raises(PrivacyScanError, match="simulated read failure"):
        scan_git_index_privacy(repo)
