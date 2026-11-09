"""F05：finish 提交事务的快照双向校验与回滚续行。

覆盖：快照副本回读校验中止 begin、回滚前逐副本校验并拒绝篡改、rolling_back
阶段标记与 judge 可续滚、回滚后 task 哈希集合/index tree 复验、提交超时两侧
判定（已提交不回滚 / 未提交精确回滚）、回滚中途及再次中断后幂等重做。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_dispatch_integration import _git, _task_cli, git_repo  # noqa: F401
from test_finish_atomic import OWNER, _ready, _snapshot
from test_finish_txn_contract import (
    TASK_REL,
    _begin,
    controlled_env,  # noqa: F401
    txn_repo,  # noqa: F401
)
from repo_task import commit_txn, context as ctx, lifecycle


class _RollbackInterrupted(Exception):
    """模拟回滚进程被强杀：不落在 rollback 的 OSError 处理分支。"""


def _run_finish_in_process(worktree: Path, message: Path, owner: str = OWNER) -> str:
    args = argparse.Namespace(
        tid="t001", owner_id=owner, message_file=str(message),
        repo=ctx.RepoContext.for_root(worktree),
    )
    with pytest.raises(SystemExit) as exc:
        lifecycle.cmd_finish(args)
    return str(exc.value.code)


def _seed_leftover_finish_txn(worktree: Path, message: Path):
    """构造真实 worktree 上的「finish 已写文件并暂存、尚未提交」事务现场。"""
    task_dir = worktree / TASK_REL
    before_hashes = commit_txn._snapshot_hashes(task_dir)
    before_tree = commit_txn._write_tree_with_index(worktree, None)
    anchor = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    txn = commit_txn.begin(
        worktree, tid="t001", attempt=1, execution_id="x", branch="t001_alpha",
        diff_anchor=anchor, review_scope="x", task_rel=TASK_REL,
        message_file=str(message),
    )
    commit_txn.write_file(
        worktree, txn, ctx.PROGRESS_FILE,
        (task_dir / ctx.PROGRESS_FILE).read_text(encoding="utf-8") + "\nfinish 事务写入\n",
    )
    commit_txn.write_file(worktree, txn, "handoff.json", "{}\n")
    commit_txn.stage(worktree, txn)
    return txn, before_hashes, before_tree


def _interrupt_copy(monkeypatch, fail_at: int):
    real = shutil.copy2
    calls = {"n": 0}

    def wrapper(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == fail_at:
            raise _RollbackInterrupted("injected rollback interruption")
        return real(*args, **kwargs)

    monkeypatch.setattr(commit_txn.shutil, "copy2", wrapper)
    return real


def _assert_restored(worktree: Path, before_hashes: dict[str, str], before_tree: str) -> None:
    assert commit_txn._snapshot_hashes(worktree / TASK_REL) == before_hashes
    assert commit_txn._write_tree_with_index(worktree, None) == before_tree


# --- 快照副本落盘回读校验 -------------------------------------------------


def test_begin_aborts_when_snapshot_copy_digest_mismatch(
    txn_repo, controlled_env, monkeypatch,
):
    files_dir = commit_txn.txn_dir(txn_repo) / commit_txn.FILES_DIR
    real = shutil.copy2

    def corrupting_copy(src, dst, *args, **kwargs):
        result = real(src, dst, *args, **kwargs)
        target = Path(dst)
        if files_dir in target.parents and target.is_file() and not target.is_symlink():
            target.write_bytes(target.read_bytes() + b"corrupt")
        return result

    monkeypatch.setattr(commit_txn.shutil, "copy2", corrupting_copy)
    with pytest.raises(ctx.TaskDataError, match="快照副本"):
        _begin(txn_repo)

    assert (txn_repo / TASK_REL / "progress.md").read_text(encoding="utf-8") == (
        "---\ntid: t001\n---\nbody\n"
    )
    loaded = commit_txn.load(txn_repo)
    assert loaded is not None and loaded["files"] == {}
    assert commit_txn.judge(txn_repo, loaded)[0] == "unknown"


# --- 回滚前逐副本校验 -----------------------------------------------------


def test_rollback_refuses_tampered_snapshot_and_keeps_journal(txn_repo, controlled_env):
    txn = _begin(txn_repo)
    task_dir = txn_repo / TASK_REL
    commit_txn.write_file(
        txn_repo, txn, ctx.PROGRESS_FILE, "---\ntid: t001\n---\nchanged\n"
    )
    snapshot = commit_txn.txn_dir(txn_repo) / commit_txn.FILES_DIR / "progress.md"
    snapshot.write_bytes(snapshot.read_bytes() + b"tampered")

    with pytest.raises(ctx.TaskDataError, match="快照副本"):
        commit_txn.rollback(txn_repo, txn)

    assert commit_txn.load(txn_repo) is not None
    assert (task_dir / "progress.md").read_text(encoding="utf-8") == (
        "---\ntid: t001\n---\nchanged\n"
    )


def test_finish_refuses_rollback_when_snapshot_tampered(git_repo):
    _, worktree, message = _ready(git_repo)
    _seed_leftover_finish_txn(worktree, message)
    snapshot = commit_txn.txn_dir(worktree) / commit_txn.FILES_DIR / "progress.md"
    snapshot.write_bytes(snapshot.read_bytes() + b"tampered")

    result = _task_cli(
        worktree, "finish", "t001", "--owner-id", OWNER,
        "--message-file", str(message),
    )

    assert result.returncode != 0
    assert "快照副本" in result.stderr
    assert commit_txn.load(worktree) is not None
    assert (worktree / TASK_REL / "progress.md").read_text(encoding="utf-8").endswith(
        "finish 事务写入\n"
    )


# --- rolling_back 标记与续滚 ------------------------------------------------


def test_judge_recognizes_rolling_back_as_resumable(txn_repo, controlled_env):
    txn = _begin(txn_repo)
    commit_txn.mark_phase(txn_repo, txn, "rolling_back")
    (txn_repo / TASK_REL / "progress.md").write_text("partial rollback\n", encoding="utf-8")

    verdict, detail = commit_txn.judge(txn_repo, txn)

    assert verdict == "rollback_safe"
    assert "回滚" in detail


@pytest.mark.parametrize("fail_at", [2, 4])
def test_rollback_interruption_resumes_from_snapshot(git_repo, monkeypatch, fail_at):
    _, worktree, message = _ready(git_repo)
    txn, before_hashes, before_tree = _seed_leftover_finish_txn(worktree, message)

    real = _interrupt_copy(monkeypatch, fail_at)
    with pytest.raises(_RollbackInterrupted):
        commit_txn.rollback(worktree, txn)
    loaded = commit_txn.load(worktree)
    assert loaded is not None and loaded["phase"] == "rolling_back"
    if fail_at == 2:
        # index 已替换后中断：暂存区先回到事务前，task 目录仍部分未还原。
        assert commit_txn._write_tree_with_index(worktree, None) == before_tree

    state = json.loads(_task_cli(git_repo, "recovery", "t001").stdout)
    assert state["phase"] == "finish_txn_rollback_safe"
    assert "重跑 finish" in state["action"]

    monkeypatch.setattr(commit_txn.shutil, "copy2", real)
    rerun = _task_cli(
        worktree, "finish", "t001", "--owner-id", "wrong-owner",
        "--message-file", str(message),
    )
    assert rerun.returncode != 0
    assert "已幂等回滚上次中断的提交事务" in rerun.stderr
    assert commit_txn.load(worktree) is None
    _assert_restored(worktree, before_hashes, before_tree)


def test_rollback_survives_repeated_interruptions(git_repo, monkeypatch):
    _, worktree, message = _ready(git_repo)
    txn, before_hashes, before_tree = _seed_leftover_finish_txn(worktree, message)

    real = _interrupt_copy(monkeypatch, 2)
    with pytest.raises(_RollbackInterrupted):
        commit_txn.rollback(worktree, txn)
    assert commit_txn.load(worktree)["phase"] == "rolling_back"

    _interrupt_copy(monkeypatch, 3)
    with pytest.raises(_RollbackInterrupted):
        commit_txn.rollback(worktree, txn)
    assert commit_txn.load(worktree)["phase"] == "rolling_back"

    monkeypatch.setattr(commit_txn.shutil, "copy2", real)
    assert commit_txn.rollback(worktree, txn) == []
    assert commit_txn.load(worktree) is None
    _assert_restored(worktree, before_hashes, before_tree)


def test_rollback_keeps_journal_when_index_tree_mismatch(git_repo, monkeypatch):
    _, worktree, message = _ready(git_repo)
    txn, before_hashes, before_tree = _seed_leftover_finish_txn(worktree, message)
    real_replace = commit_txn.os.replace

    def skip_index_replace(src, dst):
        if Path(dst).name == "index":
            return None
        return real_replace(src, dst)

    monkeypatch.setattr(commit_txn.os, "replace", skip_index_replace)
    problems = commit_txn.rollback(worktree, txn)
    assert any("暂存区 tree 与事务前 index 快照不一致" in item for item in problems)
    assert commit_txn.load(worktree)["phase"] == "rolling_back"

    monkeypatch.setattr(commit_txn.os, "replace", real_replace)
    assert commit_txn.rollback(worktree, txn) == []
    _assert_restored(worktree, before_hashes, before_tree)


def test_rollback_keeps_journal_when_restored_hashes_mismatch(git_repo, monkeypatch):
    _, worktree, message = _ready(git_repo)
    txn, before_hashes, before_tree = _seed_leftover_finish_txn(worktree, message)
    task_dir = worktree / TASK_REL
    real = shutil.copy2

    def corrupting_copy(src, dst, *args, **kwargs):
        result = real(src, dst, *args, **kwargs)
        target = Path(dst)
        if task_dir in target.parents and target.is_file() and not target.is_symlink():
            target.write_bytes(target.read_bytes() + b"x")
        return result

    monkeypatch.setattr(commit_txn.shutil, "copy2", corrupting_copy)
    problems = commit_txn.rollback(worktree, txn)
    assert any("task 目录哈希集合与事务前不一致" in item for item in problems)
    assert commit_txn.load(worktree)["phase"] == "rolling_back"

    monkeypatch.setattr(commit_txn.shutil, "copy2", real)
    assert commit_txn.rollback(worktree, txn) == []
    _assert_restored(worktree, before_hashes, before_tree)


# --- 提交超时两侧判定 -----------------------------------------------------


def test_timeout_after_commit_judges_committed_and_does_not_rollback(git_repo, monkeypatch):
    _, worktree, message = _ready(git_repo)
    anchor = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    real = commit_txn.run_git_commit

    def timeout_after_commit(worktree, message_file, timeout=600):
        real(worktree, message_file, timeout)
        return subprocess.CompletedProcess(
            args=["git", "commit"], returncode=1, stdout="", stderr="git commit 超时"
        )

    monkeypatch.setattr(commit_txn, "run_git_commit", timeout_after_commit)

    detail = _run_finish_in_process(worktree, message)

    assert "提交已产生" in detail and "不回滚" in detail
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() != anchor
    assert _git(worktree, "rev-list", "--count", f"{anchor}..HEAD").stdout.strip() == "1"
    assert commit_txn.load(worktree) is None
    committed_progress = _git(
        worktree, "show", "HEAD:docs/tasks/t001_alpha/progress.md"
    ).stdout
    assert 'status: "done"' in committed_progress


def test_timeout_before_commit_judges_rollback_safe(git_repo, monkeypatch):
    _, worktree, message = _ready(git_repo)
    anchor = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    before = _snapshot(git_repo, worktree)

    def timeout_before_commit(worktree, message_file, timeout=600):
        return subprocess.CompletedProcess(
            args=["git", "commit"], returncode=1, stdout="", stderr="git commit 超时"
        )

    monkeypatch.setattr(commit_txn, "run_git_commit", timeout_before_commit)

    detail = _run_finish_in_process(worktree, message)

    assert "已全部回滚" in detail
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == anchor
    assert commit_txn.load(worktree) is None
    assert _snapshot(git_repo, worktree) == before
