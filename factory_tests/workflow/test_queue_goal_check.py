from factory_paths import FACTORY_ROOT
"""E2：阻塞只停所在链；goal-check 在阻塞时 STOPPED，列出阻塞成员与同链被跳过成员（real-git）。"""

import argparse
import contextlib
import io
import pytest

from test_dispatch_integration import git_repo, _task_cli, _prepare_done, _cleanup
from test_queue_topology import _relate
from repo_task import context as ctx
from repo_task import control
from repo_task import goal
from repo_task.goal import _stops_with_skipped

OWNER = "owner-queue"


def _freeze(repo, *tids):
    context = ctx.RepoContext.for_root(repo)
    with contextlib.redirect_stdout(io.StringIO()):
        goal.cmd_goal(argparse.Namespace(tids=list(tids), reset=False, yes=True, repo=context))


def _run_close(repo, tid, result, *extra):
    reason = None
    if "--reason" in extra:
        idx = extra.index("--reason")
        if idx + 1 < len(extra):
            reason = extra[idx + 1]
    fail_class = None
    if "--class" in extra:
        idx = extra.index("--class")
        if idx + 1 < len(extra):
            fail_class = extra[idx + 1]
    context = ctx.RepoContext.for_root(repo)
    with contextlib.redirect_stdout(io.StringIO()):
        control.cmd_run_close(argparse.Namespace(
            tid=tid, owner_id=OWNER, result=result, fail_class=fail_class, reason=reason, repo=context
        ))


def _block(repo, tid, base=None):
    context = ctx.RepoContext.for_root(repo)
    with contextlib.redirect_stdout(io.StringIO()):
        control.cmd_run_begin(argparse.Namespace(tid=tid, owner_id=OWNER, base=base, repo=context))
    _run_close(repo, tid, "blocked", "--reason", "缺密钥")


def _done(repo, tid, slug, **kwargs):
    identity, _, _ = _prepare_done(repo, tid, slug, **kwargs)
    _cleanup(repo, tid, identity)


@pytest.mark.parametrize(
    "relation",
    [
        {"t002": {"depends_on": "t001"}},
        {"t002": {"conflicts_with": "t001"}},  # 后者单向声明
        {"t001": {"conflicts_with": "t002"}},  # 前者单向声明
    ],
)
def test_goal_check_blocked_chain_skips_members_and_other_chain_keeps_goal_going(git_repo, relation):
    for tid, values in relation.items():
        _relate(git_repo, tid, **values)
    _freeze(git_repo, "t001", "t002", "t003")
    _block(git_repo, "t001")

    # 无关链的 t003 仍可继续：INCOMPLETE，但保留阻塞点与被跳过成员
    result = _task_cli(git_repo, "goal-check")
    assert result.returncode == 2
    assert "stopped_at=t001 state=blocked skipped=t002" in result.stdout
    assert "t003" not in result.stdout.split("skipped=")[1].splitlines()[0]
    assert "GOAL_QUEUE_INCOMPLETE: 0/3 closed" in result.stdout
    assert "GOAL_QUEUE_STOPPED" not in result.stdout

    # 其它链全部闭环后无可继续成员：STOPPED
    _done(git_repo, "t003", "gamma")
    result = _task_cli(git_repo, "goal-check")
    assert result.returncode == 3
    assert "stopped_at=t001 state=blocked skipped=t002" in result.stdout
    assert "GOAL_QUEUE_STOPPED: t001=blocked" in result.stdout


@pytest.mark.parametrize("field", ["depends_on", "conflicts_with"])
def test_goal_check_keeps_frozen_edges_after_main_relation_removed(git_repo, field):
    _relate(git_repo, "t002", **{field: "t001"})
    _freeze(git_repo, "t001", "t002", "t003")
    _relate(git_repo, "t002", **{field: ""})
    _block(git_repo, "t001")

    result = _task_cli(git_repo, "goal-check")
    assert result.returncode == 2
    assert "stopped_at=t001 state=blocked skipped=t002" in result.stdout
    assert "GOAL_QUEUE_INCOMPLETE: 0/3 closed" in result.stdout


@pytest.mark.parametrize("field", ["depends_on", "conflicts_with"])
def test_goal_check_ignores_main_edges_added_after_freeze(git_repo, field):
    _freeze(git_repo, "t001", "t002")
    _relate(git_repo, "t002", **{field: "t001"})
    _block(git_repo, "t001")

    result = _task_cli(git_repo, "goal-check")
    assert result.returncode == 2
    assert "stopped_at=t001 state=blocked skipped=-" in result.stdout
    assert "GOAL_QUEUE_INCOMPLETE: 0/2 closed" in result.stdout


def test_goal_check_other_chain_running_keeps_goal_incomplete(git_repo):
    _relate(git_repo, "t002", depends_on="t001")
    _freeze(git_repo, "t001", "t002", "t003")
    _block(git_repo, "t001")
    started = _task_cli(git_repo, "run-begin", "t003", "--owner-id", OWNER)
    assert started.returncode == 0, started.stderr

    result = _task_cli(git_repo, "goal-check")
    assert result.returncode == 2
    assert "t003 running" in result.stdout
    assert "stopped_at=t001 state=blocked skipped=t002" in result.stdout


def test_goal_check_stopped_when_only_skipped_members_remain(git_repo):
    _relate(git_repo, "t002", depends_on="t001")
    _freeze(git_repo, "t001", "t002")
    _block(git_repo, "t001")

    result = _task_cli(git_repo, "goal-check")
    assert result.returncode == 3
    assert "t002 pending" in result.stdout
    assert "stopped_at=t001 state=blocked skipped=t002" in result.stdout
    assert "GOAL_QUEUE_STOPPED: t001=blocked" in result.stdout


def test_goal_check_stopped_even_when_other_chains_are_fully_executed(git_repo):
    _relate(git_repo, "t002", depends_on="t001")
    _freeze(git_repo, "t001", "t002", "t003")
    _block(git_repo, "t001")
    _done(git_repo, "t003", "gamma")

    result = _task_cli(git_repo, "goal-check")
    assert result.returncode == 3
    assert "t003 closed" in result.stdout
    assert "stopped_at=t001 state=blocked skipped=t002" in result.stdout
    assert "GOAL_QUEUE_STOPPED: t001=blocked" in result.stdout
    assert "GOAL_QUEUE_COMPLETE" not in result.stdout


def test_goal_check_skips_only_members_after_the_block_in_a_chain(git_repo):
    _relate(git_repo, "t002", depends_on="t001")
    _relate(git_repo, "t003", depends_on="t002")
    _freeze(git_repo, "t001", "t002", "t003")
    _done(git_repo, "t001", "alpha")
    _block(git_repo, "t002", base="t001_alpha")

    result = _task_cli(git_repo, "goal-check")
    assert result.returncode == 3
    assert "t001 closed" in result.stdout
    assert "stopped_at=t002 state=blocked skipped=t003" in result.stdout


def test_goal_check_blocked_last_member_has_no_skipped(git_repo):
    _freeze(git_repo, "t001", "t002", "t003")
    _done(git_repo, "t001", "alpha")
    _block(git_repo, "t002")
    # t003 无关且未执行：可继续
    result = _task_cli(git_repo, "goal-check")
    assert result.returncode == 2
    assert "stopped_at=t002 state=blocked skipped=-" in result.stdout

    _done(git_repo, "t003", "gamma")
    result = _task_cli(git_repo, "goal-check")
    assert result.returncode == 3
    assert "stopped_at=t002 state=blocked skipped=-" in result.stdout
    assert "GOAL_QUEUE_STOPPED: t002=blocked" in result.stdout


def test_goal_check_without_block_stays_incomplete_with_pending_chains(git_repo):
    _relate(git_repo, "t002", depends_on="t001")
    _freeze(git_repo, "t001", "t002", "t003")
    _done(git_repo, "t001", "alpha")

    result = _task_cli(git_repo, "goal-check")
    assert result.returncode == 2
    assert "GOAL_QUEUE_INCOMPLETE: 1/3 closed" in result.stdout
    assert "stopped_at=" not in result.stdout


def test_goal_check_complete_output_is_unchanged_by_chains(git_repo):
    _relate(git_repo, "t002", depends_on="t001")
    _freeze(git_repo, "t001", "t002", "t003")
    _done(git_repo, "t001", "alpha")
    _done(git_repo, "t002", "beta", base="t001_alpha")
    _done(git_repo, "t003", "gamma")

    result = _task_cli(git_repo, "goal-check")
    assert result.returncode == 0, result.stdout
    assert "execution_complete=true integrated=0/3" in result.stdout
    assert "awaiting_merge=3 tids=t001,t002,t003" in result.stdout
    assert "GOAL_QUEUE_COMPLETE" in result.stdout
    assert "stopped_at=" not in result.stdout


def test_stops_with_skipped_assigns_members_to_nearest_previous_stop():
    edges = [
        {"type": "dep", "from": "t001", "to": "t002"},
        {"type": "dep", "from": "t002", "to": "t003"},
        {"type": "dep", "from": "t003", "to": "t004"},
    ]
    states = {"t001": "blocked", "t002": "pending", "t003": "failed", "t004": "pending", "t005": "pending"}
    assert _stops_with_skipped(["t001", "t002", "t003", "t004", "t005"], edges, states) == [
        ("t001", "blocked", ["t002"]),
        ("t003", "failed", ["t004"]),
    ]
