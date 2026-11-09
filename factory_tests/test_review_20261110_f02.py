"""2026-11-10 审阅 F02/F04 回归：宽字符三入口一致与中段家目录识别。

F02：无 BOM、CJK 主导的 UTF-16 不再受 NUL 占比门槛限制；门禁、显式审计、
finish 候选共用同一解码判定。F04：只识别 macOS 数据卷与 UNC 两种中段形式，
拒绝 //server/... 与盘符相对写法，https://host/Users/<name> 结果不变。
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from git_repo_support import get_controlled_git_env, git_run
from repo_task import lifecycle, privacy
from repo_task.context import RepoContext
from repo_task.privacy import PrivacyScanError

TASK_REL = "docs/tasks/t001_alpha"
CJK = "中文说明" * 40


def _home(*parts: str) -> str:
    return "/".join(("", "Users", *parts))


def _wide_cjk(token: str, *, leak: bool) -> str:
    middle = f"路径 {token}/proj" if leak else "无泄漏"
    return CJK + "\n" + middle + "\n" + ("更多中文" * 40)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    """最小 Git 仓；受控身份，不依赖宿主 Git 配置。"""
    env = get_controlled_git_env()
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        if key.startswith("GIT_"):
            monkeypatch.setenv(key, value)
    root = tmp_path / "repo"
    root.mkdir()
    git_run(root, "init", "-b", "main")
    git_run(root, "config", "user.email", "test@example.com")
    git_run(root, "config", "user.name", "test")
    (root / "init.txt").write_text("init\n", encoding="utf-8")
    git_run(root, "add", "-A")
    git_run(root, "commit", "-m", "init")
    return root


def _gate(root: Path) -> list[str]:
    return privacy.scan_privacy_changes(root)


def _audit(root: Path) -> list[str]:
    return privacy.scan_git_index_privacy(root)


def _finish(root: Path) -> list[str]:
    return lifecycle._scan_finish_candidates(
        TASK_REL, "clean message\n", repo=RepoContext.for_root(root)
    )


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
def test_bomless_utf16_cjk_leak_seen_by_all_three_entries(repo, encoding):
    token = _home("karson")
    data = _wide_cjk(token, leak=True).encode(encoding)
    assert data.count(0) * 4 < len(data)
    (repo / "wide.md").write_bytes(data)
    git_run(repo, "add", "wide.md")

    expected = f"wide.md:2: {token}"
    assert privacy.scan_candidate_bytes(data, label="wide.md") == [expected]
    assert _gate(repo) == [expected]
    assert expected in _audit(repo)
    assert expected in _finish(repo)


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
def test_bomless_utf16_cjk_clean_file_not_blocked_in_finish(repo, encoding):
    token = _home("karson")
    data = _wide_cjk(token, leak=False).encode(encoding)
    (repo / "clean.md").write_bytes(data)
    git_run(repo, "add", "clean.md")

    assert privacy.scan_candidate_bytes(data, label="clean.md") == []
    assert _gate(repo) == []
    assert _finish(repo) == []


@pytest.mark.parametrize(
    "data",
    [
        b"\x00\x00\xfe\xff" + (_home("karson") + "/proj\n").encode("utf-32-be"),
        (_home("karson") + "/proj\n").encode("utf-32-be"),
    ],
    ids=["utf32-be-bom", "utf32-be-no-bom"],
)
def test_utf32_be_leak_seen_by_gate_and_audit(repo, data):
    token = _home("karson")
    (repo / "u32.md").write_bytes(data)
    git_run(repo, "add", "u32.md")
    expected = f"u32.md:1: {token}"
    assert privacy.scan_candidate_bytes(data, label="u32.md") == [expected]
    assert _gate(repo) == [expected]
    assert expected in _audit(repo)


def test_png_and_zip_stay_binary_across_entries(repo):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128 + b"clean"
    (repo / "shot.png").write_bytes(png)
    with zipfile.ZipFile(repo / "book.zip", "w") as archive:
        archive.writestr("content.xml", "<a>hello</a>")
    git_run(repo, "add", "shot.png", "book.zip")

    assert privacy.scan_candidate_bytes(png, label="shot.png") == []
    assert (repo / "book.zip").read_bytes()
    assert _gate(repo) == []
    assert _audit(repo) == []
    assert _finish(repo) == []


def test_unknown_non_nul_encoding_still_fails_closed(repo):
    (repo / "bad.txt").write_bytes(b"\xff\xff")
    git_run(repo, "add", "bad.txt")
    with pytest.raises(PrivacyScanError, match="编码无法判定"):
        _gate(repo)


def test_mid_path_home_forms_are_seen_by_gate(repo):
    mac = "/".join(("", "System", "Volumes", "Data", "Users", "alice"))
    unc = "\\\\" + "server" + "\\" + "Users" + "\\" + "bob"
    (repo / "paths.md").write_text("mac " + mac + "\nunc " + unc + "\n", encoding="utf-8")
    git_run(repo, "add", "paths.md")

    leaks = _gate(repo)
    assert any(mac in item for item in leaks)
    assert any(unc in item for item in leaks)
