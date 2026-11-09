"""P2 worktree/statusline/视图修复回归（工厂集成测试，不进产物）。

逐项覆盖复审报告「worktree / statusline / 视图」节 P2 项（1-4、6-8、10-13；
5/9 在 repo/.repo_template/tests/test_view_server.py 契约测试覆盖）。
"""
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task import context as ctx
from repo_task import statusline, worktrees
from repo_task.context import RepoContext


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )


@pytest.fixture()
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@e.c")
    _git(repo, "config", "user.name", "t")
    (repo / "base.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _repo_ctx(repo: Path) -> RepoContext:
    return RepoContext.for_root(repo)


def _write_ledger(project: Path, events: list[dict]) -> Path:
    runtime = project / "docs" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    ledger = runtime / "dispatch_ledger.jsonl"
    ledger.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
    )
    return ledger


def _open_project(tmp_path: Path, name: str, tids: list[str]) -> Path:
    """建 project 目录 + 各 tid 的 start 事件 + worktree 空目录。"""
    project = tmp_path / name
    events = [
        {"tid": tid, "event": "start", "worktree": f"../{name}_{tid}"}
        for tid in tids
    ]
    _write_ledger(project, events)
    for tid in tids:
        (tmp_path / f"{name}_{tid}").mkdir()
    return project


# ---- P2-1 discard/remove 归属伪造 ----

def test_discard_refuses_foreign_tid(git_repo):
    """tid 给定时 rel 与派生路径不符直接拒绝，worktree 原位保留。"""
    repo_ctx = _repo_ctx(git_repo)
    _git(git_repo, "worktree", "add", "-b", "t002_x", "../repo_t002")
    try:
        ok, message = worktrees.discard_worktree("../repo_t002", repo=repo_ctx, tid="t001")
        assert ok is False
        assert "非归属" in message
        assert "../repo_t002" in message
        assert (git_repo.parent / "repo_t002").exists()
    finally:
        _git(git_repo, "worktree", "remove", "--force", "../repo_t002")


def test_discard_accepts_own_tid(git_repo):
    """归属一致时照常强制移除（破坏性语义不变）。"""
    repo_ctx = _repo_ctx(git_repo)
    _git(git_repo, "worktree", "add", "-b", "t002_x", "../repo_t002")
    ok, _ = worktrees.discard_worktree("../repo_t002", repo=repo_ctx, tid="t002")
    assert ok is True
    assert not (git_repo.parent / "repo_t002").exists()


def test_remove_refuses_forged_expected_branch(git_repo):
    """expected_branch 与 rel 同源伪造时，tid 校验仍拒绝跨 task 清理。"""
    repo_ctx = _repo_ctx(git_repo)
    _git(git_repo, "worktree", "add", "-b", "t002_x", "../repo_t002")
    try:
        ok, message = worktrees.remove_worktree(
            "../repo_t002", expected_branch="t002_x", repo=repo_ctx, tid="t001",
        )
        assert ok is False
        assert "拒绝" in message
        assert (git_repo.parent / "repo_t002").exists()
    finally:
        _git(git_repo, "worktree", "remove", "--force", "../repo_t002")


def test_remove_without_tid_still_catches_path_branch_mismatch(git_repo):
    """tid 缺省时路径归属与登记分支 tid 不一致也拒绝（纵深，不断旧行为）。"""
    repo_ctx = _repo_ctx(git_repo)
    _git(git_repo, "worktree", "add", "-b", "t001_x", "../repo_t002")
    try:
        ok, message = worktrees.remove_worktree("../repo_t002", repo=repo_ctx)
        assert ok is False
        assert "不符" in message
    finally:
        _git(git_repo, "worktree", "remove", "--force", "../repo_t002")


# ---- P2-2 check-ignore 引号 ----

def test_find_unignored_env_symlinks_detects_unicode_dir_rule(git_repo, tmp_path):
    """非 ASCII 目录规则（环境/）命中软链时不再因 quotePath 引号漏报。"""
    (git_repo / ".gitignore").write_text("环境/\n", encoding="utf-8")
    _git(git_repo, "add", ".gitignore")
    _git(git_repo, "commit", "-m", "ignore unicode env dir")
    ext = tmp_path / "external"
    ext.mkdir()
    (git_repo / "环境").symlink_to(ext)
    diagnostics = worktrees.find_unignored_env_symlinks(git_repo, ["环境"])
    assert "环境" in diagnostics
    assert "环境/" in diagnostics["环境"]


# ---- P2-3 exists 误判 dangling 软链 ----

def test_create_worktree_refuses_dangling_symlink(git_repo):
    """目标路径是 dangling 软链时报已存在，不再随后 add 失败。"""
    repo_ctx = _repo_ctx(git_repo)
    rel = repo_ctx.worktree_rel("t001")
    dangling = git_repo / rel
    assert not os.path.lexists(dangling)
    os.symlink(git_repo / "no_such_target", dangling)
    assert dangling.is_symlink() and not dangling.exists()
    with pytest.raises(ctx.TaskDataError, match="已存在"):
        worktrees.create_worktree("t001", "t001_x", "HEAD", repo=repo_ctx)


def test_prune_unregistered_reports_dangling_symlink(git_repo):
    """未登记但残留 dangling 软链时拒绝静默成功。"""
    repo_ctx = _repo_ctx(git_repo)
    rel = "worktrees/t009_orphan"
    dangling = (git_repo / rel)
    dangling.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(git_repo / "no_such_target", dangling)
    ok, message = worktrees.remove_worktree(rel, repo=repo_ctx)
    assert ok is False
    assert "仍存在" in message


# ---- P2-4 current_worktree 归属校验 ----

def _claimed_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (tmp_path / "project_t001").mkdir()
    _write_ledger(project, [
        {"tid": "t001", "event": "start", "worktree": "../project_t001"},
        {"tid": "t001", "event": "attempt_reserved", "attempt": 1,
         "execution_id": "exec-1"},
        {"tid": "t001", "event": "execution_claimed", "attempt": 1,
         "execution_id": "exec-1", "owner_id": "owner-a"},
    ])
    return project


def test_current_worktree_rejects_identity_mismatch(tmp_path, caplog):
    """绑定 attempt/execution 与账本当前 identity 不一致时返回空。"""
    project = _claimed_project(tmp_path)
    assert statusline.bind(str(project), "owner-b", "t001", 99, "exec-X", owner_id="owner-b") is True
    with caplog.at_level(logging.WARNING):
        assert statusline.current_worktree(str(project), "owner-b") == ""
    assert "归属不符" in caplog.text


def test_current_worktree_rejects_owner_mismatch(tmp_path, caplog):
    """identity 一致但账本 owner 与会话不一致时返回空。"""
    project = _claimed_project(tmp_path)
    assert statusline.bind(str(project), "owner-b", "t001", 1, "exec-1", owner_id="owner-b") is True
    with caplog.at_level(logging.WARNING):
        assert statusline.current_worktree(str(project), "owner-b") == ""
    assert "owner 不符" in caplog.text


def test_current_worktree_accepts_matching_identity(tmp_path):
    """identity 与 owner 一致时照常返回 worktree。"""
    project = _claimed_project(tmp_path)
    assert statusline.bind(str(project), "owner-a", "t001", 1, "exec-1", owner_id="owner-a") is True
    assert statusline.current_worktree(str(project), "owner-a") == str(
        (tmp_path / "project_t001").resolve()
    )


# ---- P2-6 ledger worktree 值信任 ----

def test_resolve_rejects_forged_absolute_worktree(tmp_path, caplog):
    """伪造 worktree:"/" 不再返回 /。"""
    project = tmp_path / "project"
    _write_ledger(project, [
        {"tid": "t001", "event": "start", "worktree": "/"},
    ])
    with caplog.at_level(logging.WARNING):
        assert statusline.resolve_worktree(str(project), "t001") == ""
    assert "不受信" in caplog.text


def test_resolve_rejects_cross_tid_worktree(tmp_path, caplog):
    """伪造他 tid 派生路径不再被信任。"""
    project = tmp_path / "project"
    _write_ledger(project, [
        {"tid": "t001", "event": "start", "worktree": "../project_t002"},
    ])
    with caplog.at_level(logging.WARNING):
        assert statusline.resolve_worktree(str(project), "t001") == ""
    assert "不受信" in caplog.text


# ---- P2-7 append_record 半行 ----

def test_append_record_repairs_half_line(tmp_path):
    """中断残留半行先补换行，两行均可独立解析。"""
    path = tmp_path / ".scratch" / "statusline_workdir.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"session": "s", "tid": "t001"}')
    assert statusline.append_record(str(path), {"session": "s", "tid": "t002"}) is True
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["tid"] for line in lines] == ["t001", "t002"]


# ---- P2-8 append 失败静默成功 ----

def test_append_record_returns_false_on_failure(tmp_path, caplog):
    """.scratch 被文件占用时追加失败返回 False（不再吞掉）。"""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".scratch").write_text("occupied\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert statusline.append_record(
            str(project / ".scratch" / "statusline_workdir.jsonl"), {"a": 1},
        ) is False
    assert "无法创建" in caplog.text


def test_bind_propagates_persist_failure(tmp_path, caplog):
    """持久化失败时 bind 返回 False，CLI 侧可得非零退出码。"""
    project = _open_project(tmp_path, "project", ["t001"])
    (project / ".scratch").write_text("occupied\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert statusline.bind(str(project), "sess-1", "t001", owner_id="sess-1") is False
    assert "持久化失败" in caplog.text


# ---- P2-10 回扫无界 ----

def test_tail_scan_is_bounded(tmp_path, caplog):
    """超大账本回扫命中行数上限即停，不再全文件回放到头。"""
    project = tmp_path / "project"
    runtime = project / "docs" / "runtime"
    runtime.mkdir(parents=True)
    ledger = runtime / "dispatch_ledger.jsonl"
    filler = "".join(
        json.dumps({"tid": "t999", "event": "heartbeat", "i": i}) + "\n"
        for i in range(statusline._MAX_SCAN_LINES + 5000)
    )
    ledger.write_text(
        json.dumps({"tid": "t001", "event": "start",
                    "worktree": "../project_t001"}) + "\n" + filler,
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        assert statusline.resolve_worktree(str(project), "t001") == ""
    assert "上限" in caplog.text


# ---- P2-11 bind 参数不足 ----

def test_bind_cli_accepts_attempt_and_execution_id(tmp_path, monkeypatch):
    """track_worktree bind 支持 --attempt/--execution-id 并落盘。"""
    project = _open_project(tmp_path, "project", ["t001"])
    monkeypatch.setattr(sys, "argv", [
        "track_worktree.py", "bind", "--session", "owner-a", "--owner-id", "owner-a", "--tid", "t001",
        "--project", str(project), "--attempt", "2", "--execution-id", "exec-2",
    ])
    assert statusline.main() == 0
    line = (project / statusline.STATUSLINE_FILE).read_text(encoding="utf-8").strip()
    record = json.loads(line)
    assert record["attempt"] == 2
    assert record["execution_id"] == "exec-2"


# ---- P2-12 unbind 配对 ----

def test_unbind_pairs_by_session_and_tid(tmp_path):
    """同 session 跨 tid unbind 不清掉另一 tid 的打开绑定。"""
    project = _open_project(tmp_path, "project", ["t001", "t002"])
    assert statusline.bind(str(project), "s", "t001", 1, "e1", owner_id="s") is True
    assert statusline.bind(str(project), "s", "t002", 1, "e2", owner_id="s") is True
    assert statusline.current_worktree(str(project), "s") == str(
        (tmp_path / "project_t002").resolve()
    )
    assert statusline.unbind(str(project), "s", "t002") is True
    assert statusline.current_worktree(str(project), "s") == str(
        (tmp_path / "project_t001").resolve()
    )
    assert statusline.unbind(str(project), "s", "t001") is True
    assert statusline.current_worktree(str(project), "s") == ""


def test_stale_unbind_does_not_clear_newer_binding(tmp_path):
    """同 tid 先 unbind 后重绑：旧 unbind 不得清除新绑定。"""
    project = _open_project(tmp_path, "project", ["t001"])
    assert statusline.bind(str(project), "s", "t001", 1, "e1", owner_id="s") is True
    assert statusline.unbind(str(project), "s", "t001") is True
    assert statusline.current_worktree(str(project), "s") == ""
    assert statusline.bind(str(project), "s", "t001", 2, "e2", owner_id="s") is True
    assert statusline.current_worktree(str(project), "s") == str(
        (tmp_path / "project_t001").resolve()
    )


# ---- P2-13 exclude glob 注入 ----

def test_escape_exclude_segment_escapes_glob():
    """glob 元字符转义，锚定规则只表达字面路径。"""
    assert worktrees._escape_exclude_segment("data[2024]") == r"/data\[2024\]"
    assert worktrees._escape_exclude_segment("a?b*c") == r"/a\?b\*c"
    assert worktrees._escape_exclude_segment("plain.env") == "/plain.env"


def test_ensure_links_ignored_writes_escaped_rule(git_repo, monkeypatch):
    """含 glob 字符的软链路径落盘为转义规则，不按 glob 展开。"""
    worktree = git_repo / "wt"
    worktree.mkdir()
    calls = {"n": 0}

    def fake_untracked(root, paths):
        calls["n"] += 1
        return ["a[1].env"] if calls["n"] == 1 else []

    monkeypatch.setattr(worktrees, "_untracked_in", fake_untracked)
    worktrees._ensure_links_ignored(worktree, ["a[1].env"], repo=_repo_ctx(git_repo))
    from repo_task.locks import git_common_dir
    exclude = git_common_dir(git_repo) / "info" / "exclude"
    text = exclude.read_text(encoding="utf-8")
    assert r"/a\[1\].env" in text
    assert "\n/a[1].env\n" not in text
