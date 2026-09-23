"""Done + stale review evidence repair: amend same execution commit.

Covers the reported_uncleaned deadlock: cleanup/integrate share the review
gate, and no new attempt/rewind/second-commit may fix stale evidence. The
only legal path is re-reviewing the tip and amending review process files
into the same execution commit, then exact cleanup with the original
identity. report=done is gated on the same verify, staying in
committed_unreported until evidence is fixed.
"""
import json

from test_dispatch_integration import (
    _git, _handoff, _handoff_path, _identity_args, _prepare_done,
    _reserve, _task_cli, _worktree, git_repo,
)
from repo_task.documents import parse_front_matter
from repo_task.monitoring import review_scope_fingerprint

REL = "docs/archive/tasks/t001_alpha"


def _recovery(repo, tid="t001"):
    before = _git(repo, "status", "--porcelain").stdout
    result = _task_cli(repo, "recovery", tid)
    assert result.returncode == 0, result.stderr
    assert _git(repo, "status", "--porcelain").stdout == before
    return json.loads(result.stdout)


def _make_stale(repo):
    """Valid done fixture, then amend a deliverable so review goes stale."""
    identity, branch, _ = _prepare_done(repo, "t001", "alpha")
    w = _worktree(repo, "t001")
    (w / "README.md").write_text("unreviewed finalization change\n")
    _git(w, "add", "-A")
    _git(w, "commit", "--amend", "--no-edit")
    return identity, branch, w


def _amend_review_only(worktree):
    """Refresh review evidence for the current tip without touching deliverables."""
    task_md = worktree / REL / "task.md"
    fm, _ = parse_front_matter(task_md)
    anchor = fm["diff_anchor"]
    scope = review_scope_fingerprint(anchor, REL, repo_root=worktree)
    assert scope
    for name in ("review_code.md", "review_test.md"):
        (worktree / REL / name).write_text(
            f"## Round 2\nreviewed_scope: {scope}\nverdict: PASS\n", encoding="utf-8"
        )
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "--amend", "--no-edit")
    return scope


def test_stale_review_recovery_points_to_amend_and_cleanup_still_rejects(git_repo):
    identity, _, w = _make_stale(git_repo)
    state = _recovery(git_repo)
    assert state["phase"] == "evidence_repair"
    assert "amend" in state["action"]
    assert "同一个执行 commit" in state["action"]
    result = _task_cli(git_repo, "cleanup-worktree", "t001", *_identity_args(identity))
    assert result.returncode != 0
    assert "review" in result.stderr.lower()
    assert w.is_dir()


def test_review_only_amend_restores_cleanup(git_repo):
    identity, _, w = _make_stale(git_repo)
    _amend_review_only(w)
    result = _task_cli(git_repo, "cleanup-worktree", "t001", *_identity_args(identity))
    assert result.returncode == 0, result.stderr
    assert not w.exists() or not (w / ".git").exists()


def test_second_commit_still_rejected(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    w = _worktree(git_repo, "t001")
    (w / "README.md").write_text("second commit change\n")
    _git(w, "add", "-A")
    _git(w, "commit", "-m", "second commit")
    result = _task_cli(git_repo, "cleanup-worktree", "t001", *_identity_args(identity))
    assert result.returncode != 0
    assert "恰有一个执行 commit" in result.stderr
    state = _recovery(git_repo)
    assert state["phase"] != "evidence_repair"


def test_report_done_rejects_stale_and_succeeds_after_amend(git_repo):
    assert _task_cli(git_repo, "start", "t001").returncode == 0
    identity = _reserve(git_repo, "t001")
    w = _worktree(git_repo, "t001")
    assert _task_cli(w, "finish", "t001").returncode == 0
    branch = "t001_alpha"
    base_sha = _git(w, "rev-parse", "HEAD").stdout.strip()
    archive = w / REL
    # Write review evidence first, then change a deliverable so the
    # committed tip is stale (mirrors review-at-09:33, commit-at-09:35).
    scope = review_scope_fingerprint(base_sha, REL, repo_root=w)
    for name in ("review_code.md", "review_test.md"):
        (archive / name).write_text(
            f"## Round 1\nreviewed_scope: {scope}\nverdict: PASS\n", encoding="utf-8"
        )
    (w / "README.md").write_text("change after review\n")
    (archive / "handoff.json").write_text(
        json.dumps(_handoff("t001", branch, identity, base_sha), ensure_ascii=False),
        encoding="utf-8",
    )
    _git(w, "add", "-A")
    _git(w, "commit", "-m", "feat(t001): complete alpha")
    head = _git(w, "rev-parse", "HEAD").stdout.strip()
    assert _task_cli(
        git_repo, "attempt", "terminal", "t001", *_identity_args(identity),
        "--status", "completed",
    ).returncode == 0
    blocked = _task_cli(
        git_repo, "attempt", "report", "t001", *_identity_args(identity),
        "--status", "done", "--sha", head,
    )
    assert blocked.returncode != 0
    assert "review" in blocked.stderr.lower()
    state = _recovery(git_repo)
    assert state["phase"] == "evidence_repair"
    _amend_review_only(w)
    tip = _git(w, "rev-parse", "HEAD").stdout.strip()
    ok = _task_cli(
        git_repo, "attempt", "report", "t001", *_identity_args(identity),
        "--status", "done", "--sha", tip,
    )
    assert ok.returncode == 0, ok.stderr
    assert _recovery(git_repo)["phase"] == "reported_uncleaned"


def test_reserve_rewind_messages_point_to_recovery(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    # Stale-state reserve already points to recovery (no rewind suggestion).
    stale_reserve = _task_cli(git_repo, "attempt", "reserve", "t001", "--executor", "inline")
    assert stale_reserve.returncode != 0
    # Integrate to main so effective status is archived done, then check
    # the archived reserve/rewind messages.
    assert _task_cli(
        git_repo, "cleanup-worktree", "t001", *_identity_args(identity)
    ).returncode == 0
    assert _task_cli(git_repo, "integrate", "t001", *_identity_args(identity)).returncode == 0
    assert _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity), "--continue"
    ).returncode == 0
    reserve = _task_cli(git_repo, "attempt", "reserve", "t001", "--executor", "inline")
    assert reserve.returncode != 0
    assert "recovery" in reserve.stderr
    assert "amend" in reserve.stderr
    assert "需先 rewind" not in reserve.stderr
    assert "先 rewind 或显式恢复" not in reserve.stderr
    rewind = _task_cli(git_repo, "rewind", "t001", "--reason", "try")
    assert rewind.returncode != 0
    assert "recovery" in rewind.stderr
    assert "放弃用 drop" not in rewind.stderr
    assert "彻底删除用 purge" not in rewind.stderr
    assert _identity_args(identity)
