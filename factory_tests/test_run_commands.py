"""C1/C2：run-begin 一次完成 start → reserve → claim；run-close 按结果收尾且重入安全。"""

import argparse
import json

import pytest

from test_dispatch_integration import (
    git_repo, _git, _task_cli, _prepare_done, _cleanup, _worktree, _read_ledger,
)
from review_support import valid_spec
from task_flow_support import finish_cli, prepare_finish
from repo_task import attempts
from repo_task import control
from repo_task import context as ctx
from repo_task.documents import parse_front_matter, write_front_matter

OWNER = "owner-run"


def _begin(repo, tid="t001", owner=OWNER, *extra):
    result = _task_cli(repo, "run-begin", tid, "--owner-id", owner, *extra)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _close(repo, result, tid="t001", owner=OWNER, *extra):
    return _task_cli(repo, "run-close", tid, "--owner-id", owner, "--result", result, *extra)


def _recovery(repo, tid="t001"):
    result = _task_cli(repo, "recovery", tid)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _events(repo, kind=None):
    events = _read_ledger(repo)
    return [e for e in events if kind is None or e["event"] == kind]


def _namespace(**overrides):
    values = {"tid": "t001", "owner_id": OWNER, "base": None}
    values.update(overrides)
    return argparse.Namespace(**values)


# --------------------------------------------------------------------------
# run-begin
# --------------------------------------------------------------------------


def test_run_begin_starts_reserves_and_claims_in_one_call(git_repo):
    identity = _begin(git_repo)

    assert set(identity) == {"tid", "attempt", "execution_id", "branch", "worktree", "owner_id"}
    assert identity["tid"] == "t001"
    assert identity["attempt"] == 1
    assert identity["branch"] == "t001_alpha"
    assert identity["owner_id"] == OWNER
    assert identity["worktree"] == str(_worktree(git_repo, "t001").resolve())
    assert _worktree(git_repo, "t001").is_dir()
    assert [e["event"] for e in _events(git_repo)] == [
        "start", "attempt_reserved", "execution_claimed",
    ]
    reserved = _events(git_repo, "attempt_reserved")[0]
    assert reserved["executor"] == "inline"
    assert reserved["execution_id"] == identity["execution_id"]
    state = _recovery(git_repo)
    assert state["phase"] == "executing"
    assert state["owner_id"] == OWNER
    # claim 已完成：携同一 owner 的严格 preflight 在 worktree 内可通过
    preflight = _task_cli(
        _worktree(git_repo, "t001"), "preflight", "t001",
        "--require-verified", "--owner-id", OWNER,
    )
    assert preflight.returncode == 0, preflight.stdout + preflight.stderr


def test_run_begin_keeps_stdout_machine_readable(git_repo):
    result = _task_cli(git_repo, "run-begin", "t001", "--owner-id", OWNER)
    assert result.returncode == 0, result.stderr
    json.loads(result.stdout)  # start 的进度文本不得污染 stdout
    assert "diff_anchor" in result.stderr


def test_run_begin_accepts_base_for_chain(git_repo):
    first, branch, head = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", first)
    second = _begin(git_repo, "t002", "owner-two", "--base", branch)
    assert second["attempt"] == 1 and second["execution_id"] != first["execution_id"]
    fm, _ = parse_front_matter(_worktree(git_repo, "t002") / "docs/tasks/t002_beta/progress.md")
    assert fm["diff_anchor"] == head


def test_run_begin_rejects_active_task_with_recovery_hint_and_no_writes(git_repo):
    _begin(git_repo)
    before = _read_ledger(git_repo)

    again = _task_cli(git_repo, "run-begin", "t001", "--owner-id", "owner-other")

    assert again.returncode != 0
    assert "task.py recovery t001" in again.stderr
    assert _read_ledger(git_repo) == before


def test_run_begin_rejects_existing_attempt_worktree_and_branch(git_repo):
    ledger = git_repo / "docs/runtime/dispatch_ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({
        "event": "attempt_reserved", "tid": "t001", "attempt": 1,
        "execution_id": "old", "executor": "inline", "state": "running",
    }) + "\n", encoding="utf-8")
    existing_attempt = _task_cli(git_repo, "run-begin", "t001", "--owner-id", OWNER)
    assert existing_attempt.returncode != 0
    assert "attempt" in existing_attempt.stderr and "task.py recovery t001" in existing_attempt.stderr

    _worktree(git_repo, "t002").mkdir()
    existing_worktree = _task_cli(git_repo, "run-begin", "t002", "--owner-id", OWNER)
    assert existing_worktree.returncode != 0
    assert "worktree" in existing_worktree.stderr and "task.py recovery t002" in existing_worktree.stderr

    _git(git_repo, "branch", "t003_gamma")
    existing_branch = _task_cli(git_repo, "run-begin", "t003", "--owner-id", OWNER)
    assert existing_branch.returncode != 0
    assert "分支" in existing_branch.stderr and "task.py recovery t003" in existing_branch.stderr
    assert not _worktree(git_repo, "t003").exists()


def test_run_begin_start_failure_leaves_recovery_pending(git_repo):
    path = git_repo / "docs/tasks/t001_alpha/progress.md"
    fm, body = parse_front_matter(path)
    fm["depends_on"] = "t002"
    write_front_matter(path, fm, body)
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "schedule")

    result = _task_cli(git_repo, "run-begin", "t001", "--owner-id", OWNER)

    assert result.returncode != 0
    assert "步骤 start" in result.stderr and "依赖未满足" in result.stderr
    assert "task.py recovery t001" in result.stderr
    assert _events(git_repo, "attempt_reserved") == []
    assert _recovery(git_repo)["phase"] == "pending"


def test_run_begin_reserve_failure_is_recovered_as_started_without_attempt(git_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise ctx.TaskDataError("boom-reserve")

    monkeypatch.setattr(control, "reserve_attempt", boom)
    with pytest.raises(SystemExit) as stop:
        control.cmd_run_begin(_namespace(repo=ctx.RepoContext.for_root(git_repo)))

    message = str(stop.value)
    assert "步骤 reserve" in message and "boom-reserve" in message
    assert "task.py recovery t001" in message
    state = _recovery(git_repo)
    assert state["phase"] == "started_without_attempt"
    assert "只 reserve" in state["action"]
    assert _events(git_repo, "attempt_reserved") == []


def test_run_begin_claim_failure_is_recovered_as_executing_without_owner(git_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise ctx.TaskDataError("boom-claim")

    monkeypatch.setattr(control, "claim_execution", boom)
    with pytest.raises(SystemExit) as stop:
        control.cmd_run_begin(_namespace(repo=ctx.RepoContext.for_root(git_repo)))

    message = str(stop.value)
    assert "步骤 claim" in message and "boom-claim" in message
    assert "task.py recovery t001" in message
    state = _recovery(git_repo)
    assert state["phase"] == "executing"
    assert state["owner_id"] == ""
    assert "尚未 claim" in state["action"]
    assert [e["event"] for e in _events(git_repo)] == ["start", "attempt_reserved"]
    # recovery 给出的恢复路径：按原 identity 补 claim，不重复 start/reserve
    claim = _task_cli(git_repo, "attempt", "claim", "t001", "--owner-id", OWNER)
    assert claim.returncode == 0, claim.stderr
    assert _recovery(git_repo)["owner_id"] == OWNER


# --------------------------------------------------------------------------
# run-close
# --------------------------------------------------------------------------


def _claimed_done(repo, owner=OWNER):
    """已 finish 并提交、已 claim、尚未 terminal 的 task。"""
    identity, branch, head = _prepare_done(repo, "t001", "alpha", terminal=False)
    attempts.claim_execution(
        "t001", identity["attempt"], identity["execution_id"], owner, repo=ctx.RepoContext.for_root(repo)
    )
    return identity, branch, head


def test_run_close_done_writes_terminal_report_and_cleans_worktree(git_repo):
    identity, branch, head = _claimed_done(git_repo)

    result = _close(git_repo, "done")

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert (summary["tid"], summary["attempt"], summary["execution_id"], summary["result"]) == (
        "t001", identity["attempt"], identity["execution_id"], "done",
    )
    terminal = _events(git_repo, "attempt_terminal")
    report = _events(git_repo, "report")
    assert [e["status"] for e in terminal] == ["completed"]
    assert [e["status"] for e in report] == ["done"]
    assert report[0]["sha"] == head == _git(git_repo, "rev-parse", branch).stdout.strip()
    assert not _worktree(git_repo, "t001").exists()
    assert _git(git_repo, "branch", "--list", branch).stdout.strip() == branch
    assert _recovery(git_repo)["phase"] == "closed"


def test_run_close_done_is_reentrant_without_duplicate_events(git_repo):
    _claimed_done(git_repo)
    assert _close(git_repo, "done").returncode == 0
    before = _read_ledger(git_repo)

    again = _close(git_repo, "done")

    assert again.returncode == 0, again.stderr
    assert _read_ledger(git_repo) == before


def test_run_close_done_resumes_after_cleanup_failure(git_repo):
    _claimed_done(git_repo)
    worktree = _worktree(git_repo, "t001")
    (worktree / "dirty.txt").write_text("x", encoding="utf-8")

    failed = _close(git_repo, "done")

    assert failed.returncode != 0
    assert "未提交改动" in failed.stderr and "重入" in failed.stderr
    assert len(_events(git_repo, "attempt_terminal")) == 1
    assert len(_events(git_repo, "report")) == 1
    assert worktree.is_dir()
    assert _recovery(git_repo)["phase"] == "reported_uncleaned"

    (worktree / "dirty.txt").unlink()
    resumed = _close(git_repo, "done")

    assert resumed.returncode == 0, resumed.stderr
    assert len(_events(git_repo, "attempt_terminal")) == 1
    assert len(_events(git_repo, "report")) == 1
    assert not worktree.exists()


def test_run_close_done_resumes_after_terminal_only(git_repo):
    identity, _, _ = _claimed_done(git_repo)
    terminal = _task_cli(git_repo, "attempt", "terminal", "t001", "--status", "completed")
    assert terminal.returncode == 0, terminal.stderr
    assert _recovery(git_repo)["phase"] == "committed_unreported"

    result = _close(git_repo, "done")

    assert result.returncode == 0, result.stderr
    assert len(_events(git_repo, "attempt_terminal")) == 1
    assert [e["status"] for e in _events(git_repo, "report")] == ["done"]
    assert not _worktree(git_repo, "t001").exists()


def test_run_close_done_requires_committed_finish_and_writes_nothing_otherwise(git_repo):
    _begin(git_repo)
    before = _read_ledger(git_repo)

    result = _close(git_repo, "done")

    assert result.returncode != 0
    assert "report=done 拒绝" in result.stderr
    assert _read_ledger(git_repo) == before
    assert _worktree(git_repo, "t001").is_dir()


def test_run_close_rejects_foreign_or_missing_owner_without_writes(git_repo):
    _claimed_done(git_repo)
    before = _read_ledger(git_repo)

    foreign = _close(git_repo, "done", "t001", "owner-other")

    assert foreign.returncode != 0
    assert "owner_id" in foreign.stderr
    assert _read_ledger(git_repo) == before
    assert _worktree(git_repo, "t001").is_dir()


def test_run_close_rejects_unclaimed_attempt(git_repo):
    assert _task_cli(git_repo, "start", "t001").returncode == 0
    reserved = _task_cli(git_repo, "attempt", "reserve", "t001")
    assert reserved.returncode == 0, reserved.stderr
    before = _read_ledger(git_repo)

    result = _close(git_repo, "blocked")

    assert result.returncode != 0
    assert "owner_id" in result.stderr
    assert _read_ledger(git_repo) == before


def test_run_close_blocked_stops_attempt_and_keeps_worktree(git_repo):
    identity = _begin(git_repo)

    result = _close(git_repo, "blocked", "t001", OWNER, "--reason", "缺少外部凭据")

    assert result.returncode == 0, result.stderr
    assert [e["status"] for e in _events(git_repo, "attempt_terminal")] == ["stopped"]
    report = _events(git_repo, "report")
    assert [(e["status"], e.get("reason")) for e in report] == [("blocked", "缺少外部凭据")]
    assert "sha" not in report[0]
    assert _worktree(git_repo, "t001").is_dir()
    fm, _ = parse_front_matter(_worktree(git_repo, "t001") / "docs/tasks/t001_alpha/progress.md")
    assert fm["status"] == "active"
    assert _recovery(git_repo)["phase"] == "retry_ready"
    # 重入：不重复写事件
    before = _read_ledger(git_repo)
    assert _close(git_repo, "blocked").returncode == 0
    assert _read_ledger(git_repo) == before
    # 与已写结果冲突的 --result 被拒绝
    conflict = _close(git_repo, "failed")
    assert conflict.returncode != 0
    assert "冲突" in conflict.stderr
    assert _read_ledger(git_repo) == before
    assert identity["attempt"] == 1


def test_run_close_failed_writes_failed_terminal_and_report(git_repo):
    _begin(git_repo)

    result = _close(git_repo, "failed", "t001", OWNER, "--class", "infra", "--reason", "磁盘满")

    assert result.returncode == 0, result.stderr
    assert [e["status"] for e in _events(git_repo, "attempt_terminal")] == ["failed"]
    report = _events(git_repo, "report")
    assert [(e["status"], e.get("class"), e.get("reason")) for e in report] == [
        ("failed", "infra", "磁盘满"),
    ]
    assert _worktree(git_repo, "t001").is_dir()
    before = _read_ledger(git_repo)
    assert _close(git_repo, "failed").returncode == 0
    assert _read_ledger(git_repo) == before


def test_run_close_resumes_blocked_after_terminal_only(git_repo):
    _begin(git_repo)
    assert _task_cli(git_repo, "attempt", "terminal", "t001", "--status", "stopped").returncode == 0

    result = _close(git_repo, "blocked")

    assert result.returncode == 0, result.stderr
    assert len(_events(git_repo, "attempt_terminal")) == 1
    assert [e["status"] for e in _events(git_repo, "report")] == ["blocked"]


def test_run_close_done_after_integrate_is_idempotent(git_repo):
    _claimed_done(git_repo)
    assert _close(git_repo, "done").returncode == 0
    for extra in ((), ("--continue",)):
        merged = _task_cli(git_repo, "integrate", "t001", *extra)
        assert merged.returncode == 0, merged.stderr
    before = _read_ledger(git_repo)

    again = _close(git_repo, "done")

    assert again.returncode == 0, again.stderr
    assert _read_ledger(git_repo) == before


def test_run_begin_supports_task_added_and_scheduled_on_main_after_unmerged_chain_fork(git_repo):
    """Issue #88：存在未合并已 done 链时，在 main 新建 backlog task（含 single 级）并编辑后继依赖后，
    可用 run-begin --base <链尾>（及自动选 base）起步、finish、run-close 并整链 integrate-chain 闭环。
    """
    first, first_branch, _ = _prepare_done(
        git_repo, "t001", "alpha",
        mutate=lambda w: (w / "chain_1.txt").write_text("from t001\n", encoding="utf-8"),
    )
    _cleanup(git_repo, "t001", first)
    second, second_branch, second_tip = _prepare_done(
        git_repo, "t002", "beta", base=first_branch,
        mutate=lambda w: (w / "chain_2.txt").write_text("from t002\n", encoding="utf-8"),
    )
    _cleanup(git_repo, "t002", second)

    # 在 main 创建 t004（single 级，依赖链尾 t002），并给队列下一 task t003 追加 depends_on=t004
    added = _task_cli(
        git_repo, "add", "--title", "delta", "--slug", "delta", "--review-level", "single"
    )
    assert added.returncode == 0, added.stderr
    t004_dir = git_repo / "docs/tasks/t004_delta"
    (t004_dir / "task.md").write_text(valid_spec(), encoding="utf-8")
    t004_note = t004_dir / "progress.md"
    t004_note.write_text(
        t004_note.read_text(encoding="utf-8")
        + "\n- specs_index: not_applicable: 隔离链尾起步测试。\n",
        encoding="utf-8",
    )
    assert _task_cli(git_repo, "edit", "t004", "--depends-on", "t002").returncode == 0
    assert _task_cli(git_repo, "edit", "t003", "--depends-append", "t004").returncode == 0

    # 未在 main 提交前，run-begin 拒绝启动未入库 task
    uncommitted = _task_cli(
        git_repo, "run-begin", "t004", "--owner-id", OWNER, "--base", second_branch
    )
    assert uncommitted.returncode != 0
    assert "尚未提交" in uncommitted.stderr

    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "chore(task): add t004 and update t003 dependency")

    # 1. t004 显式以未合并链尾 t002_beta 为 --base 起步
    identity_4 = _begin(git_repo, "t004", OWNER, "--base", second_branch)
    wt4 = _worktree(git_repo, "t004")
    assert (wt4 / "chain_1.txt").is_file() and (wt4 / "chain_2.txt").is_file()
    fm4, _ = parse_front_matter(wt4 / "docs/tasks/t004_delta/progress.md")
    assert fm4["status"] == "active"
    assert fm4["review_level"] == "single"
    assert fm4["depends_on"] == "t002"
    assert fm4["diff_anchor"] == second_tip
    # 链尾分支 tip 未被污染
    assert _git(git_repo, "rev-parse", second_branch).stdout.strip() == second_tip

    msg4 = prepare_finish(
        wt4, "t004", "delta",
        mutate=lambda w: (w / "chain_4.txt").write_text("from t004\n", encoding="utf-8"),
    )
    assert finish_cli(wt4, "t004", OWNER, msg4).returncode == 0
    closed4 = _close(git_repo, "done", "t004", OWNER)
    assert closed4.returncode == 0, closed4.stderr

    # 2. t003 在 main 上追加了 depends_on=t004，不传 --base 自动落到 t004_delta 并同步新图定义
    identity_3 = _begin(git_repo, "t003", OWNER)
    wt3 = _worktree(git_repo, "t003")
    assert (wt3 / "chain_4.txt").is_file()
    fm3, _ = parse_front_matter(wt3 / "docs/tasks/t003_gamma/progress.md")
    assert fm3["depends_on"] == "t004"
    msg3 = prepare_finish(
        wt3, "t003", "gamma",
        mutate=lambda w: (w / "chain_3.txt").write_text("from t003\n", encoding="utf-8"),
    )
    assert finish_cli(wt3, "t003", OWNER, msg3).returncode == 0
    closed3 = _close(git_repo, "done", "t003", OWNER)
    assert closed3.returncode == 0, closed3.stderr

    assert identity_4["tid"] == "t004" and identity_3["tid"] == "t003"
    ps_out = _task_cli(git_repo, "ps")
    assert ps_out.returncode == 0, ps_out.stderr
    assert "contract" not in ps_out.stdout

    # 3. integrate-chain 整链合并（主干存在 t004/t003 的 backlog 初始目录，自动收敛为分支 done 终态并归档）
    prep = _task_cli(git_repo, "integrate-chain", "t003")
    assert prep.returncode == 0, prep.stderr
    cont = _task_cli(git_repo, "integrate-chain", "t003", "--continue")
    assert cont.returncode == 0, cont.stderr

    for name in ("t001_alpha", "t002_beta", "t004_delta", "t003_gamma"):
        assert not (git_repo / "docs/tasks" / name).exists()
        assert (git_repo / "docs/archive/tasks" / name / "handoff.json").is_file()
    for fname in ("chain_1.txt", "chain_2.txt", "chain_4.txt", "chain_3.txt"):
        assert (git_repo / fname).is_file()
