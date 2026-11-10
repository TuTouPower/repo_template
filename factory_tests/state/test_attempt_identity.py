"""C4/C5：attempt reserve 无 --executor；exact identity 省略时自动解析，显式值须一致。"""

import json

import pytest

from test_dispatch_integration import (
    git_repo, _git, _task_cli, _prepare_done, _reserve, _identity_args,
    _worktree, _read_ledger,
)
from git_repo_support import state_file, worktree_state_file
from repo_task import attempts
from repo_task import context as ctx


def _ledger_count(repo):
    return len(_read_ledger(repo))


def test_reserve_executor_flag_is_removed_but_ledger_still_records_inline(git_repo):
    assert _task_cli(git_repo, "start", "t001").returncode == 0

    rejected = _task_cli(git_repo, "attempt", "reserve", "t001", "--executor", "inline")
    assert rejected.returncode != 0
    assert "--executor" in rejected.stderr
    assert not [e for e in _read_ledger(git_repo) if e["event"] == "attempt_reserved"]

    event = _reserve(git_repo, "t001")
    assert event["executor"] == "inline"
    reserved = [e for e in _read_ledger(git_repo) if e["event"] == "attempt_reserved"]
    assert [e["executor"] for e in reserved] == ["inline"]


def test_identity_flags_are_optional_in_help(git_repo):
    for command in (("attempt", "claim"), ("attempt", "terminal"), ("attempt", "report"),
                    ("cleanup-worktree",), ("integrate",)):
        result = _task_cli(git_repo, *command, "--help")
        assert result.returncode == 0, result.stderr
        usage = result.stdout.split("\n\n", 1)[0]
        assert "[--attempt ATTEMPT]" in usage and "[--execution-id EXECUTION_ID]" in usage


def test_claim_terminal_report_resolve_current_identity_from_ledger(git_repo):
    assert _task_cli(git_repo, "start", "t001").returncode == 0
    identity = _reserve(git_repo, "t001")

    claim = _task_cli(git_repo, "attempt", "claim", "t001", "--owner-id", "owner-a")
    assert claim.returncode == 0, claim.stderr
    claimed = json.loads(claim.stdout)
    assert (claimed["attempt"], claimed["execution_id"]) == (identity["attempt"], identity["execution_id"])
    assert claimed["owner_id"] == "owner-a"

    # claim 的 owner 核对规则不变：另一 owner 不带接管参数仍被拒绝
    conflict = _task_cli(git_repo, "attempt", "claim", "t001", "--owner-id", "owner-b")
    assert conflict.returncode != 0
    assert "ownership conflict" in conflict.stderr

    assert _task_cli(git_repo, "attempt", "terminal", "t001", "--status", "stopped").returncode == 0
    report = _task_cli(git_repo, "attempt", "report", "t001", "--status", "blocked")
    assert report.returncode == 0, report.stderr
    events = [e for e in _read_ledger(git_repo) if e["event"] in ("attempt_terminal", "report")]
    assert {(e["attempt"], e["execution_id"]) for e in events} == {
        (identity["attempt"], identity["execution_id"])
    }


def test_cleanup_and_integrate_resolve_current_identity_from_ledger(git_repo):
    identity, branch, head = _prepare_done(git_repo, "t001", "alpha", terminal=False)
    assert _task_cli(git_repo, "attempt", "terminal", "t001", "--status", "completed").returncode == 0
    report = _task_cli(git_repo, "attempt", "report", "t001", "--status", "done", "--sha", head)
    assert report.returncode == 0, report.stderr

    cleaned = _task_cli(git_repo, "cleanup-worktree", "t001")
    assert cleaned.returncode == 0, cleaned.stderr
    assert not _worktree(git_repo, "t001").exists()

    prepared = _task_cli(git_repo, "integrate", "t001")
    assert prepared.returncode == 0, prepared.stderr
    done = _task_cli(git_repo, "integrate", "t001", "--continue")
    assert done.returncode == 0, done.stderr
    integrated = [e for e in _read_ledger(git_repo) if e["event"] == "integrated"]
    assert [(e["attempt"], e["execution_id"]) for e in integrated] == [
        (identity["attempt"], identity["execution_id"])
    ]


@pytest.mark.parametrize(("command", "extra"), [
    (("attempt", "terminal"), ("--status", "stopped")),
    (("attempt", "report"), ("--status", "blocked")),
    (("attempt", "claim"), ("--owner-id", "owner-a")),
    (("cleanup-worktree",), ()),
    (("integrate",), ()),
])
def test_explicit_identity_must_match_resolved_identity(git_repo, command, extra):
    assert _task_cli(git_repo, "start", "t001").returncode == 0
    identity = _reserve(git_repo, "t001")
    before = _ledger_count(git_repo)

    wrong = _task_cli(
        git_repo, *command, "t001",
        "--attempt", str(identity["attempt"]), "--execution-id", "wrong", *extra,
    )

    assert wrong.returncode != 0
    assert "不一致" in wrong.stderr
    assert _ledger_count(git_repo) == before


def test_partial_explicit_identity_is_rejected(git_repo):
    assert _task_cli(git_repo, "start", "t001").returncode == 0
    identity = _reserve(git_repo, "t001")
    before = _ledger_count(git_repo)

    only_attempt = _task_cli(
        git_repo, "attempt", "terminal", "t001", "--attempt", str(identity["attempt"]),
        "--status", "stopped",
    )
    only_execution = _task_cli(
        git_repo, "attempt", "terminal", "t001", "--execution-id", identity["execution_id"],
        "--status", "stopped",
    )

    for result in (only_attempt, only_execution):
        assert result.returncode != 0
        assert "同时提供" in result.stderr
    assert _ledger_count(git_repo) == before


def test_missing_attempt_is_reported_instead_of_guessed(git_repo):
    assert _task_cli(git_repo, "start", "t001").returncode == 0
    result = _task_cli(git_repo, "attempt", "claim", "t001", "--owner-id", "owner-a")
    assert result.returncode != 0
    assert "尚无 attempt" in result.stderr


def test_old_explicit_identity_is_rejected_after_new_attempt(git_repo):
    assert _task_cli(git_repo, "start", "t001").returncode == 0
    first = _reserve(git_repo, "t001")
    for command, status in (("terminal", "stopped"), ("report", "blocked")):
        assert _task_cli(git_repo, "attempt", command, "t001", "--status", status).returncode == 0
    second = _reserve(git_repo, "t001")
    assert second["attempt"] == first["attempt"] + 1

    stale = _task_cli(
        git_repo, "attempt", "terminal", "t001", *_identity_args(first), "--status", "stopped",
    )
    assert stale.returncode != 0
    assert "不一致" in stale.stderr

    current = _task_cli(git_repo, "attempt", "terminal", "t001", "--status", "stopped")
    assert current.returncode == 0, current.stderr
    assert json.loads(current.stdout)["attempt"] == second["attempt"]


def test_worktree_resolves_identity_from_execution_context(git_repo):
    assert _task_cli(git_repo, "start", "t001").returncode == 0
    identity = _reserve(git_repo, "t001")
    worktree = _worktree(git_repo, "t001")
    repo = ctx.RepoContext.for_root(worktree)

    assert attempts.resolve_identity("t001", repo=repo) == (identity["attempt"], identity["execution_id"])
    assert attempts.resolve_identity(
        "t001", identity["attempt"], identity["execution_id"], repo=repo
    ) == (identity["attempt"], identity["execution_id"])
    with pytest.raises(ctx.TaskDataError, match="不一致"):
        attempts.resolve_identity("t001", identity["attempt"], "wrong", repo=repo)

    context_path = worktree_state_file(worktree, "repo-task-execution.json")
    receipt = json.loads(context_path.read_text(encoding="utf-8"))
    receipt["execution_id"] = "stale"
    context_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ctx.TaskDataError, match="与主仓 ledger 当前 identity"):
        attempts.resolve_identity("t001", repo=repo)

    context_path.unlink()
    with pytest.raises(ctx.TaskDataError, match="无法从"):
        attempts.resolve_identity("t001", repo=repo)


def test_omitted_identity_keeps_terminal_gate_and_missing_attempt_error(git_repo):
    # 无 attempt 时省略 identity：明确报错，不猜测
    for command in ("cleanup-worktree", "integrate"):
        result = _task_cli(git_repo, command, "t001")
        assert result.returncode != 0
        assert "尚无 attempt" in result.stderr

    # 当前 attempt 仍 running：省略 identity 解析到它，随后被 terminal 门禁拒绝
    _prepare_done(git_repo, "t001", "alpha", terminal=False)
    for command in ("cleanup-worktree", "integrate"):
        result = _task_cli(git_repo, command, "t001")
        assert result.returncode != 0
        assert "须先 terminal" in result.stderr
