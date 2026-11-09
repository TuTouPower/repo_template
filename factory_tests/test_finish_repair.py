"""P2 commit_txn/lifecycle/recovery/control 修补回归 + P1-3 口径统一与死循环消除。

覆盖：judge 血缘判定（偏离判 unknown）、prepared/writing 暂存区比对与回滚拒绝、
journal 占位优先与非常规 journal、finalize 实际消失校验、finish-txn-clear 共用
txn 锁与实现 commit 对应性核对、message-file 解析后包含检查、recovery FM/后继
判定 fail-closed、privacy kind 专用映射、历史泄漏下 finish→run-close→ready 全链路。
"""

import argparse
import contextlib
import io
import json
from contextlib import contextmanager

import pytest

from git_repo_support import git_run
from task_flow_support import finish_cli, prepare_finish
from test_dispatch_integration import _git, _task_cli, _worktree, git_repo
from test_finish_atomic import _committed_journal, _snapshot
from test_finish_txn_contract import TID, TASK_REL, _begin, controlled_env, txn_repo
from repo_task import commit_txn, context as ctx, control, lifecycle, monitoring, recovery
from repo_task import locks

OWNER = "owner-repair"


def _home(user: str, *tail: str) -> str:
    return "/".join(("", "Users", user, *tail))


def _begin_flow(repo, tid="t001", owner=OWNER):
    context = ctx.RepoContext.for_root(repo)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        control.cmd_run_begin(argparse.Namespace(
            tid=tid, owner_id=owner, base=None, repo=context
        ))
    return json.loads(buf.getvalue())


# --- P2 commit_txn：血缘判定 ----------------------------------------------------


def test_judge_unknown_when_head_diverged_to_unrelated_commit(txn_repo, controlled_env):
    txn = commit_txn.begin(
        txn_repo, tid=TID, attempt=1, execution_id="e" * 32, branch="t001_alpha",
        diff_anchor="anchor", review_scope="scope", task_rel=TASK_REL,
        message_file="/tmp/message.txt",
    )
    git_run(txn_repo, "checkout", "--orphan", "detour")
    git_run(txn_repo, "commit", "--allow-empty", "-m", "diverged")
    verdict, detail = commit_txn.judge(txn_repo, txn)
    assert verdict == "unknown"
    assert "不能认领外部提交" in detail


def test_judge_rejects_external_descendant_before_commit_phase(txn_repo, controlled_env):
    txn = commit_txn.begin(
        txn_repo, tid=TID, attempt=1, execution_id="e" * 32, branch="t001_alpha",
        diff_anchor="anchor", review_scope="scope", task_rel=TASK_REL,
        message_file="/tmp/message.txt",
    )
    git_run(txn_repo, "commit", "--allow-empty", "-m", "advance")
    assert commit_txn.judge(txn_repo, txn)[0] == "unknown"


# --- P2 commit_txn：prepared/writing 暂存区比对 ----------------------------------


def test_judge_unknown_when_index_externally_modified_in_prepared(txn_repo, controlled_env):
    txn = _begin(txn_repo)
    (txn_repo / "outside.txt").write_text("external\n", encoding="utf-8")
    git_run(txn_repo, "add", "outside.txt")
    verdict, detail = commit_txn.judge(txn_repo, txn)
    assert verdict == "unknown"
    assert "暂存区" in detail


def test_rollback_refuses_when_index_externally_modified(txn_repo, controlled_env):
    txn = _begin(txn_repo)
    (txn_repo / "outside.txt").write_text("external\n", encoding="utf-8")
    git_run(txn_repo, "add", "outside.txt")
    with pytest.raises(ctx.TaskDataError, match="暂存区与提交事务开始时的快照不一致"):
        commit_txn.rollback(txn_repo, txn)
    # 拒绝回滚后现场与 journal 保留。
    assert (txn_repo / "outside.txt").is_file()
    assert commit_txn.load(txn_repo) is not None


# --- P2 commit_txn：journal 占位与非常规 journal ----------------------------------


def test_begin_leaves_journal_when_snapshot_crashes(txn_repo, controlled_env, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("snapshot disk failure")

    monkeypatch.setattr(commit_txn, "_snapshot_task_dir", boom)
    with pytest.raises(OSError, match="snapshot disk failure"):
        _begin(txn_repo)
    # 占位 journal 已存在：load 能读到并判 unknown，而不是被误认为无事务。
    loaded = commit_txn.load(txn_repo)
    assert loaded is not None and loaded["phase"] == "prepared"
    assert commit_txn.judge(txn_repo, loaded)[0] == "unknown"


@pytest.mark.parametrize("corrupt", ["symlink", "directory"])
def test_load_rejects_nonregular_journal(txn_repo, controlled_env, tmp_path, corrupt):
    directory = commit_txn.txn_dir(txn_repo)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / commit_txn.JOURNAL
    if corrupt == "symlink":
        real = tmp_path / "journal.json"
        real.write_text(json.dumps({
            "version": commit_txn.JOURNAL_VERSION, "tid": TID, "attempt": 1, "execution_id": "e" * 32,
            "branch": "t001_alpha", "diff_anchor": "a", "review_scope": "s",
            "task_rel": TASK_REL, "message_file": "/tmp/m", "original_head": "h",
            "index_existed": False, "files": {}, "phase": "prepared",
            "expected_tree": "", "expected_files": {}, "pending_files": {}, "pending_tree": "",
        }), encoding="utf-8")
        target.symlink_to(real)
    else:
        target.mkdir()
    with pytest.raises(ctx.TaskDataError, match="非普通文件"):
        commit_txn.load(txn_repo)


# --- P2 commit_txn：finalize 实际消失校验 -------------------------------------------


def test_finalize_removes_txn_dir(txn_repo, controlled_env):
    _begin(txn_repo)
    assert commit_txn.load(txn_repo) is not None
    commit_txn.finalize(txn_repo)
    assert not commit_txn.txn_dir(txn_repo).exists()
    assert commit_txn.load(txn_repo) is None


def test_finalize_raises_when_dir_persists(txn_repo, controlled_env, monkeypatch):
    _begin(txn_repo)
    monkeypatch.setattr(commit_txn.shutil, "rmtree", lambda *args, **kwargs: None)
    with pytest.raises(ctx.TaskDataError, match="无法清理"):
        commit_txn.finalize(txn_repo)


# --- P2：finish-txn-clear 共用 txn 锁 + 对应性核对 -------------------------------------


def test_finish_txn_clear_holds_txn_lock(git_repo, monkeypatch):
    from test_finish_atomic import _ready

    identity, worktree, message = _ready(git_repo)
    anchor = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    assert finish_cli(worktree, "t001", "owner-fin", message).returncode == 0
    _committed_journal(worktree, anchor, message, execution_id=identity["execution_id"])

    paths: list[str] = []
    real = locks.file_lock

    @contextmanager
    def spy(lock_path, **kwargs):
        paths.append(str(lock_path))
        with real(lock_path, **kwargs):
            yield

    monkeypatch.setattr(locks, "file_lock", spy)
    context = ctx.RepoContext.for_root(git_repo)
    recovery.cmd_finish_txn_clear(argparse.Namespace(tid="t001", repo=context))
    assert str(commit_txn.txn_lock_path(worktree)) in paths


def test_finish_txn_clear_refuses_mismatched_identity(git_repo):
    from test_finish_atomic import _ready

    _, worktree, message = _ready(git_repo)
    anchor = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    assert finish_cli(worktree, "t001", "owner-fin", message).returncode == 0
    # 默认伪造 journal 身份与实现 commit 执行回执不符：拒绝清理。
    _committed_journal(worktree, anchor, message, execution_id="forged-execution")
    result = _task_cli(git_repo, "finish-txn-clear", "t001")
    assert result.returncode != 0
    assert "身份不符" in result.stderr
    assert commit_txn.load(worktree) is not None


# --- P2 lifecycle：cmd_finish 统一捕获 + message 解析后包含 -------------------------------


def test_finish_rejects_message_file_resolving_into_task_dir(git_repo):
    from test_finish_atomic import _ready

    _, worktree, _ = _ready(git_repo)
    task_dir = worktree / "docs/tasks/t001_alpha"
    linkdir = worktree.parent / "linkdir_repair"
    if linkdir.is_symlink() or linkdir.exists():
        linkdir.unlink()
    linkdir.symlink_to(task_dir, target_is_directory=True)
    before = _snapshot(git_repo, worktree)

    result = finish_cli(worktree, "t001", "owner-fin", linkdir / "msg.txt")

    assert result.returncode != 0
    assert "位于当前 task 目录内" in result.stderr
    assert _snapshot(git_repo, worktree) == before


def test_verify_post_commit_scope_fingerprint_failure_is_diagnostic(git_repo, monkeypatch):
    from test_finish_atomic import _ready

    _, worktree, _ = _ready(git_repo)
    monkeypatch.setattr(
        monitoring, "review_scope_fingerprint",
        lambda *args, **kwargs: (_ for _ in ()).throw(ctx.TaskDataError("disk failure")),
    )
    repo = ctx.RepoContext.for_root(worktree)
    with pytest.raises(SystemExit) as exc:
        lifecycle._verify_post_commit_scope("scope", "anchor", TASK_REL, TID, repo=repo)
    assert "指纹重算失败" in str(exc.value)


def test_require_report_done_ready_privacy_kind_has_guidance():
    from unittest import mock

    with mock.patch.object(
        control, "verify_integrate_ready",
        return_value=("contract", "privacy", "分支 tip 引入泄漏"),
    ):
        with pytest.raises(SystemExit) as exc:
            control.require_report_done_ready("t001", 1, "e" * 32, repo=object())
    assert "定位引入提交" in str(exc.value)


# --- P2 recovery：FM/后继判定 fail-closed ---------------------------------------------


def test_recovery_reports_corrupt_for_unparseable_progress(git_repo):
    _begin_flow(git_repo)
    worktree = _worktree(git_repo, "t001")
    (worktree / "docs/tasks/t001_alpha/progress.md").write_text(
        "no front matter here\n", encoding="utf-8"
    )
    result = _task_cli(git_repo, "recovery", "t001")
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["phase"] == "corrupt"


def _amend_to_scope_mismatch(worktree):
    task_dir = worktree / "docs/tasks/t001_alpha"
    (task_dir / "task.md").write_text(
        (task_dir / "task.md").read_text(encoding="utf-8") + "\n<!-- amend -->\n",
        encoding="utf-8",
    )
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "--amend", "--no-edit")


def _finish_without_close(git_repo):
    from test_finish_atomic import _ready

    _, worktree, message = _ready(git_repo)
    result = finish_cli(worktree, "t001", "owner-fin", message)
    assert result.returncode == 0, result.stdout + result.stderr
    return worktree


def test_recovery_detects_review_mismatch_after_amend(git_repo):
    worktree = _finish_without_close(git_repo)
    _amend_to_scope_mismatch(worktree)
    state = json.loads(_task_cli(git_repo, "recovery", "t001").stdout)
    assert state["phase"] == "review_mismatch"


def test_recovery_successor_failure_maps_to_needs_attention(git_repo, monkeypatch):
    worktree = _finish_without_close(git_repo)
    _amend_to_scope_mismatch(worktree)
    monkeypatch.setattr(
        recovery, "_tip_has_successor",
        lambda *args, **kwargs: (_ for _ in ()).throw(ctx.TaskDataError("branch boom")),
    )
    state = recovery.recovery_state("t001", repo=ctx.RepoContext.for_root(git_repo))
    assert state["phase"] == "needs_attention"
    assert "后继分支判定失败" in state["action"]


def test_recovery_maps_privacy_kind_to_dedicated_phase(git_repo, monkeypatch):
    worktree = _finish_without_close(git_repo)
    _amend_to_scope_mismatch(worktree)
    monkeypatch.setattr(lifecycle, "scan_tip_privacy_leaks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        recovery, "verify_integrate_ready",
        lambda *args, **kwargs: ("contract", "privacy", "分支 tip 引入泄漏X"),
    )
    state = recovery.recovery_state("t001", repo=ctx.RepoContext.for_root(git_repo))
    assert state["phase"] == "committed_privacy_leak"
    assert "定位引入提交" in state["action"]


# --- P1-3：历史泄漏不阻断 finish→run-close→ready ----------------------------------------


def test_historical_leak_does_not_block_finish_close_ready(git_repo):
    """死循环回归：base 历史含家目录路径、tip 无新增泄漏时，全链路放行。"""
    leak = _home("histuser", "legacy")
    (git_repo / "legacy.txt").write_text("path " + leak + "\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "seed historical leak")

    identity = _begin_flow(git_repo)
    worktree = _worktree(git_repo, "t001")
    message = prepare_finish(worktree, "t001", "alpha")

    finished = finish_cli(worktree, "t001", OWNER, message)
    assert finished.returncode == 0, finished.stdout + finished.stderr
    closed = _task_cli(git_repo, "run-close", "t001", "--owner-id", OWNER, "--result", "done")
    assert closed.returncode == 0, closed.stderr

    repo = ctx.RepoContext.for_root(git_repo)
    verdict, kind, _ = monitoring.verify_integrate_ready(
        "t001", identity["attempt"], identity["execution_id"], repo=repo
    )
    assert (verdict, kind) == ("ready", "ready")
