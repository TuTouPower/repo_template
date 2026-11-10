"""P1-1 隐私过严阻断回归：正常二进制/office/GBK/大文件不再阻断 finish 全链路。

覆盖：staged PNG/.docx 等二进制放行、GBK 文本干净放行且泄漏检出、9MB 大文件
逐对象扫描（干净通过/泄漏检出/超硬上限失败）、变更集扫描忽略历史存量、
UTF-32 BOM 优先识别与可读性 gate。家目录样例一律运行期拼接，避免测试源码
自身成为隐私门禁命中样本。
"""

import subprocess
import zipfile
from pathlib import Path

import pytest

from git_repo_support import git_run
from repo_task import context as ctx
from repo_task import lifecycle, privacy
from repo_task.privacy import (
    MAX_SCAN_BLOB_BYTES,
    MAX_SCAN_BLOB_HARD_CAP,
    PrivacyScanError,
    decode_candidate_bytes,
    scan_commit_introduced_privacy,
)

TASK_REL = "docs/tasks/t001_alpha"


def _home(user: str, *tail: str) -> str:
    return "/".join(("", "Users", user, *tail))


@pytest.fixture
def tiny_repo(tmp_path, monkeypatch):
    """最小 Git 仓；本地身份配置齐全，不依赖宿主 git 全局配置。"""
    import os

    from git_repo_support import get_controlled_git_env

    env = get_controlled_git_env()
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)
    for key in (
        "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL", "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL",
        "GIT_TERMINAL_PROMPT",
    ):
        monkeypatch.setenv(key, env[key])
    root = tmp_path / "repo"
    root.mkdir()
    git_run(root, "init", "-b", "main")
    git_run(root, "config", "user.email", "test@example.com")
    git_run(root, "config", "user.name", "test")
    (root / "init.txt").write_text("init\n", encoding="utf-8")
    git_run(root, "add", "-A")
    git_run(root, "commit", "-m", "init")
    task_dir = root / TASK_REL
    task_dir.mkdir(parents=True)
    (task_dir / "progress.md").write_text("x\n", encoding="utf-8")
    return root


def _candidates(root: Path) -> list[str]:
    repo = ctx.RepoContext.for_root(root)
    return lifecycle._scan_finish_candidates(TASK_REL, "clean message\n", repo=repo)


def _stage(root: Path, rel: str) -> None:
    git_run(root, "add", "--", rel)


# --- P1-1：二进制后缀预过滤 -----------------------------------------------


@pytest.mark.parametrize("rel", ["shot.png", "photo.JPG", "clip.avi", "movie.mkv", "pic.heic"])
def test_staged_image_and_video_do_not_block(tiny_repo, rel):
    (tiny_repo / rel).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 128 + b"clean")
    _stage(tiny_repo, rel)
    assert _candidates(tiny_repo) == []


@pytest.mark.parametrize("rel", ["doc.docx", "book.xlsx", "deck.pptx", "old.doc", "data.tar"])
def test_staged_office_and_archive_do_not_block(tiny_repo, rel):
    with zipfile.ZipFile(tiny_repo / rel, "w") as archive:
        archive.writestr("content.xml", "<a>hello</a>")
    _stage(tiny_repo, rel)
    assert _candidates(tiny_repo) == []


def test_unknown_suffix_binary_is_skipped_by_nul_sniff(tiny_repo):
    (tiny_repo / "blob.unknownext").write_bytes(bytes(range(256)) * 4 + b"\x00\x01\x02")
    _stage(tiny_repo, "blob.unknownext")
    assert _candidates(tiny_repo) == []


def test_binary_with_embedded_home_path_is_still_flagged_by_suffix_bypass(tiny_repo):
    """未知后缀但可解码的内容照常扫描：ASCII 泄漏不因 NUL 嗅探被放过。"""
    user = _home("leakuser")
    (tiny_repo / "data.unknownext").write_bytes(("prefix " + user + "/proj\n").encode())
    _stage(tiny_repo, "data.unknownext")
    leaks = _candidates(tiny_repo)
    assert any(user in item for item in leaks)


# --- P1-1：GBK 文本 ----------------------------------------------------------


def test_staged_gbk_clean_text_passes(tiny_repo):
    (tiny_repo / "gbk.txt").write_bytes("中文纯文本，无泄漏\n".encode("gbk"))
    _stage(tiny_repo, "gbk.txt")
    assert _candidates(tiny_repo) == []


def test_staged_gbk_leak_is_detected(tiny_repo):
    user = _home("leakuser")
    (tiny_repo / "gbk.txt").write_bytes(("路径 " + user + "/proj\n").encode("gbk"))
    _stage(tiny_repo, "gbk.txt")
    leaks = _candidates(tiny_repo)
    assert any("gbk.txt" in item and user in item for item in leaks)


def test_gbk_decoded_binary_garbage_still_fails_closed():
    with pytest.raises(PrivacyScanError, match="编码无法判定"):
        decode_candidate_bytes(b"\x81\xfe\x00\xff\x81\x00\xfe", label="a.md")


# --- P1-1：超大对象逐个扫描 ----------------------------------------------------


def test_large_clean_file_passes_index_and_candidate_scan(tiny_repo):
    (tiny_repo / "big.md").write_bytes(b"clean line\n" * (MAX_SCAN_BLOB_BYTES // 12))
    git_run(tiny_repo, "add", "-A")
    assert privacy.scan_git_index_privacy(tiny_repo) == []
    assert _candidates(tiny_repo) == []


def test_large_file_leak_is_found(tiny_repo):
    user = _home("leakuser")
    (tiny_repo / "big.md").write_bytes(
        b"clean line\n" * 1000 + ("leak " + user + "/proj\n").encode()
    )
    git_run(tiny_repo, "add", "-A")
    leaks = privacy.scan_git_index_privacy(tiny_repo)
    assert any("big.md" in item and user in item for item in leaks)


def test_blob_beyond_hard_cap_fails_closed(tiny_repo, monkeypatch):
    from repo_task import git_ops

    (tiny_repo / "huge.md").write_text("clean\n", encoding="utf-8")
    git_run(tiny_repo, "add", "-A")
    real = git_ops.batch_object_sizes

    def inflated(oids, **kw):
        return {oid: MAX_SCAN_BLOB_HARD_CAP + 1 for oid in real(oids, **kw)}

    monkeypatch.setattr(git_ops, "batch_object_sizes", inflated)
    with pytest.raises(PrivacyScanError, match="无法安全扫描"):
        privacy.scan_git_index_privacy(tiny_repo)


# --- P1-3：变更集扫描忽略历史存量 ---------------------------------------------


def test_commit_scan_ignores_historical_leak(tiny_repo):
    token = _home("histuser", "old")
    (tiny_repo / "hist.txt").write_text("old " + token + "\n", encoding="utf-8")
    git_run(tiny_repo, "add", "-A")
    git_run(tiny_repo, "commit", "-m", "historical leak")
    (tiny_repo / "new.txt").write_text("clean\n", encoding="utf-8")
    git_run(tiny_repo, "add", "-A")
    git_run(tiny_repo, "commit", "-m", "clean tip")
    assert scan_commit_introduced_privacy(tiny_repo, "HEAD") == []
    # 显式审计入口仍能看到历史存量。
    assert privacy.scan_git_tree_privacy(tiny_repo, "HEAD")


def test_commit_scan_flags_introduced_leak_with_lineno(tiny_repo):
    (tiny_repo / "base.txt").write_text("base\n", encoding="utf-8")
    git_run(tiny_repo, "add", "-A")
    git_run(tiny_repo, "commit", "-m", "base")
    user = _home("leakuser")
    (tiny_repo / "base.txt").write_text("base\nplus " + user + "/new\n", encoding="utf-8")
    git_run(tiny_repo, "add", "-A")
    git_run(tiny_repo, "commit", "-m", "leak tip")
    leaks = lifecycle._scan_commit_privacy_leaks("HEAD", repo=ctx.RepoContext.for_root(tiny_repo))
    assert leaks == [f"base.txt:2: {user}"]


def test_commit_scan_on_root_commit(tiny_repo):
    user = _home("leakuser")
    (tiny_repo / "init.txt").write_text("init " + user + "/root\n", encoding="utf-8")
    git_run(tiny_repo, "add", "-A")
    git_run(tiny_repo, "commit", "--amend", "--no-edit")
    leaks = scan_commit_introduced_privacy(tiny_repo, "HEAD")
    assert any(user in item for item in leaks)


def test_commit_scan_flags_utf16_leak(tiny_repo):
    user = _home("leakuser")
    (tiny_repo / "u16.txt").write_bytes((user + "/u16\n").encode("utf-16-le"))
    git_run(tiny_repo, "add", "-A")
    git_run(tiny_repo, "commit", "-m", "u16")
    leaks = scan_commit_introduced_privacy(tiny_repo, "HEAD")
    assert any("u16.txt" in item and user in item for item in leaks)


def test_typechange_to_leaking_symlink_is_flagged(tiny_repo):
    (tiny_repo / "target_real.txt").write_text("real\n", encoding="utf-8")
    git_run(tiny_repo, "add", "-A")
    git_run(tiny_repo, "commit", "-m", "base")
    (tiny_repo / "target_real.txt").unlink()
    (tiny_repo / "target_real.txt").symlink_to(_home("leakuser", "secret"))
    git_run(tiny_repo, "add", "-A")
    leaks = _candidates(tiny_repo)
    assert any(_home("leakuser") in item for item in leaks)


def test_deleted_worktree_file_does_not_block(tiny_repo):
    (tiny_repo / "gone.txt").write_text("clean\n", encoding="utf-8")
    git_run(tiny_repo, "add", "-A")
    git_run(tiny_repo, "commit", "-m", "base")
    (tiny_repo / "gone.txt").unlink()
    assert _candidates(tiny_repo) == []


# --- P2 privacy：UTF-32 BOM 优先 + 可读性 gate ----------------------------------


def test_utf32_bom_leak_is_detected():
    user = _home("karson")
    data = (user + "/proj\n").encode("utf-32")
    leaks = privacy.scan_candidate_bytes(data, label="a.md")
    assert leaks == [f"a.md:1: {user}"]


def test_utf32le_bom_without_encoding_is_detected():
    user = _home("karson")
    data = b"\xff\xfe\x00\x00" + (user + "/proj\n").encode("utf-32-le")
    assert privacy.scan_candidate_bytes(data, label="a.md")


def test_utf32_bom_garbage_fails_closed():
    with pytest.raises(PrivacyScanError, match="非可读文本"):
        decode_candidate_bytes(b"\xff\xfe\x00\x00" + b"\x00" * 32, label="a.md")


def test_bomless_utf32_leak_is_detected():
    user = _home("karson")
    data = (user + "/proj\n").encode("utf-32-le")
    assert privacy.scan_candidate_bytes(data, label="a.md")


# --- P2 documents：缩进代码块 AC 不得采信 ----------------------------------------


def test_indented_code_ac_is_not_trusted():
    from repo_task.documents import extract_ac_ids, extract_ac_statuses

    spec = (
        "### 验收标准\n\n"
        "- [x] AC-001：真实条目\n\n"
        "    - [x] AC-002：缩进代码块示例\n\n"
        "```\n- [x] AC-003：围栏示例\n```\n"
    )
    assert extract_ac_ids(spec) == ["AC-001"]
    assert extract_ac_statuses(spec) == [("AC-001", True)]
