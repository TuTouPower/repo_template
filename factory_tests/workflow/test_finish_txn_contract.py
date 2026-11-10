"""finish 提交事务与隐私候选的契约级单测（W2 主题）。

覆盖：journal 结构/路径校验（DS L208）、O_EXCL 创建（DS L209）、全阶段哈希比对
（P1-1）、软链 lstat（DS L211）、忽略文件排除（DS L212）、commit message 扫描
（DS L213）、消除进程级缓存（DS L214）、死分支/死代码（DS L215/L217）、
_tip_has_successor fail-closed（DS L224）、bind 诊断降级（P1-12/DS L267/L268）。

Git 操作统一走 git_repo_support 的受控 env（DS L286），不裸用宿主配置。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from git_repo_support import get_controlled_git_env, git_run
from repo_task import commit_txn, context as ctx, control, lifecycle, recovery, statusline, store

TID = "t001"
TASK_REL = "docs/tasks/t001_alpha"


def _home(user: str, *tail: str) -> str:
    """运行期拼接家目录路径，避免测试源码自身成为隐私门禁命中样本。"""
    return "/".join(("", "Users", user, *tail))


def _valid_journal() -> dict:
    return {
        "version": commit_txn.JOURNAL_VERSION,
        "tid": TID,
        "attempt": 1,
        "execution_id": "e" * 32,
        "branch": "t001_alpha",
        "diff_anchor": "anchor",
        "review_scope": "scope",
        "task_rel": TASK_REL,
        "message_file": "/tmp/message.txt",
        "original_head": "head",
        "original_index_tree": "",
        "index_existed": False,
        "files": {},
        "phase": "prepared",
        "expected_tree": "",
        "expected_files": {},
        "pending_files": {},
        "pending_tree": "",
    }


def _write_journal(root: Path, data: dict) -> None:
    path = commit_txn.txn_dir(root) / commit_txn.JOURNAL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _begin(root: Path) -> dict:
    return commit_txn.begin(
        root, tid=TID, attempt=1, execution_id="e" * 32, branch="t001_alpha",
        diff_anchor="anchor", review_scope="scope", task_rel=TASK_REL,
        message_file="/tmp/message.txt",
    )


@pytest.fixture
def controlled_env(monkeypatch):
    """让生产代码里的 git 调用也继承受控身份/配置（DS L286）。"""
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
    return env


@pytest.fixture
def txn_repo(tmp_path):
    """只含一个 task 目录的最小 Git 仓库；初始化全程受控 env。"""
    root = tmp_path / "repo"
    root.mkdir()
    git_run(root, "init", "-b", "main")
    git_run(root, "config", "user.email", "test@example.com")
    git_run(root, "config", "user.name", "test")
    task_dir = root / TASK_REL
    task_dir.mkdir(parents=True)
    (task_dir / "progress.md").write_text(
        "---\ntid: t001\n---\nbody\n", encoding="utf-8"
    )
    git_run(root, "add", "-A")
    git_run(root, "commit", "-m", "init")
    return root


# --- DS L208：journal 结构/路径校验 ---------------------------------------


@pytest.mark.parametrize("missing", ["task_rel", "files", "phase", "version"])
def test_journal_load_rejects_missing_keys(txn_repo, controlled_env, missing):
    data = _valid_journal()
    del data[missing]
    _write_journal(txn_repo, data)
    with pytest.raises(ctx.TaskDataError, match="缺必需键|版本非法"):
        commit_txn.load(txn_repo)


@pytest.mark.parametrize("bad", ["../../..", "docs/elsewhere/t001_x", "/abs/t001_x"])
def test_journal_load_rejects_bad_task_rel(txn_repo, controlled_env, bad):
    data = _valid_journal()
    data["task_rel"] = bad
    _write_journal(txn_repo, data)
    with pytest.raises(ctx.TaskDataError, match="task_rel 非法"):
        commit_txn.load(txn_repo)


def test_journal_load_rejects_traversal_files(txn_repo, controlled_env):
    data = _valid_journal()
    data["files"] = {"../escape.txt": "deadbeef"}
    _write_journal(txn_repo, data)
    with pytest.raises(ctx.TaskDataError, match="路径或结构非法"):
        commit_txn.load(txn_repo)


# --- DS L209：O_EXCL 创建 + 锁路径在事务目录之外 ---------------------------


def test_begin_o_excl_rejects_second_journal(txn_repo, controlled_env):
    _begin(txn_repo)
    assert (commit_txn.txn_dir(txn_repo) / commit_txn.JOURNAL).is_file()
    with pytest.raises(ctx.TaskDataError, match="已存在未完成的提交事务"):
        _begin(txn_repo)


def test_txn_lock_path_is_outside_txn_dir(txn_repo, controlled_env):
    lock = commit_txn.txn_lock_path(txn_repo)
    assert lock.parent == commit_txn.txn_dir(txn_repo).parent
    assert commit_txn.txn_dir(txn_repo) not in lock.parents


# --- P1-1：prepared/writing 阶段现场被外部修改一律 unknown ----------------


def test_judge_unknown_when_prepared_site_modified(txn_repo, controlled_env):
    txn = _begin(txn_repo)
    (txn_repo / TASK_REL / "progress.md").write_text("人工修复\n", encoding="utf-8")
    verdict, detail = commit_txn.judge(txn_repo, txn)
    assert verdict == "unknown"
    assert "task 目录内容与提交事务期望不一致" in detail


# --- DS L211/L212/L213：候选枚举与文本扫描 --------------------------------


def test_symlink_candidate_scans_readlink_text_only(txn_repo, controlled_env):
    (txn_repo / TASK_REL / "leak_link").symlink_to(_home("leakuser", "secret"))
    repo = ctx.RepoContext.for_root(txn_repo)
    leaks = lifecycle._scan_finish_candidates(TASK_REL, "clean message\n", repo=repo)
    assert any(_home("leakuser") in item for item in leaks)


def test_gitignored_candidate_is_not_scanned(txn_repo, controlled_env):
    (txn_repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (txn_repo / TASK_REL / "draft.log").write_text(
        f"{_home('leakuser', 'draft')}\n", encoding="utf-8"
    )
    repo = ctx.RepoContext.for_root(txn_repo)
    assert lifecycle._scan_finish_candidates(TASK_REL, "clean message\n", repo=repo) == []


def test_commit_message_is_scanned(txn_repo, controlled_env):
    repo = ctx.RepoContext.for_root(txn_repo)
    leaks = lifecycle._scan_finish_candidates(
        TASK_REL, f"see {_home('leakuser', 'proj')}\n", repo=repo
    )
    assert any("commit message" in item and _home("leakuser") in item for item in leaks)


# --- DS L214/L215/L217：消除全局缓存与死代码 ------------------------------


def test_snapshot_cache_is_bounded_and_keyed_by_template():
    snapshot = store.CommitTaskSnapshot("s", (), {}, {})
    cache = store.CommitSnapshotCache(max_entries=2)
    cache.set_commit("repo|tmplA", "s1", snapshot)
    cache.set_commit("repo|tmplA", "s2", snapshot)
    cache.set_commit("repo|tmplA", "s3", snapshot)
    assert cache.get_commit("repo|tmplA", "s1") is None
    other = store.CommitSnapshotCache()
    other.set_commit("repo|tmplA", "s1", snapshot)
    assert other.get_commit("repo|tmplB", "s1") is None


def test_find_task_requires_tasks_argument():
    with pytest.raises(TypeError):
        store.find_task(TID)


def test_dead_tree_helper_removed():
    assert not hasattr(store, "_path_exists_in_tree")


# --- DS L224：_tip_has_successor fail-closed ------------------------------


def test_tip_has_successor_fails_closed(monkeypatch):
    def fail(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=["git"], returncode=1, stdout="", stderr="boom"
        )

    monkeypatch.setattr(recovery, "_git", fail)
    with pytest.raises(ctx.TaskDataError, match="无法列出本地分支"):
        recovery._tip_has_successor("abc", "t001_alpha", repo=object())


# --- P1-12 / DS L267 / L268：不吞 bind 返回值，统一诊断降级 ----------------


def _fake_repo():
    return ctx.RepoContext.for_root(Path("/tmp/fake_project"))


def test_bind_statusline_reports_false_result(capfd, monkeypatch):
    monkeypatch.setattr(statusline, "bind", lambda *a, **k: False)
    control._bind_statusline(_fake_repo(), "session-1", TID, 2, "e" * 32, "session-1")
    err = capfd.readouterr().err
    assert "状态行绑定" in err
    assert "/tmp/fake_project" in err and "session-1" in err and TID in err
    assert "最新 start 事件" in err


def test_bind_statusline_degrades_exception(capfd, monkeypatch):
    def boom(*a, **k):
        raise AttributeError("'int' object has no attribute 'get'")

    monkeypatch.setattr(statusline, "bind", boom)
    control._bind_statusline(_fake_repo(), "session-1", TID, 1, "e" * 32, "session-1")
    err = capfd.readouterr().err
    assert "状态行绑定" in err and "has no attribute" in err


# --- P1-3：tip commit 隐私核对边界 ----------------------------------------


def test_verify_tip_privacy_blocks_leaking_commit(txn_repo, controlled_env):
    (txn_repo / "leak.txt").write_text(f"path {_home('leakuser', 'proj')}\n", encoding="utf-8")
    git_run(txn_repo, "add", "-A")
    git_run(txn_repo, "commit", "-m", "leak")
    repo = ctx.RepoContext.for_root(txn_repo)
    with pytest.raises(SystemExit) as exc:
        lifecycle.verify_tip_privacy("HEAD", repo=repo)
    assert "分支 tip" in str(exc.value) and _home("leakuser") in str(exc.value)
