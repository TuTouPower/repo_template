"""D01：执行分支恰好一个实现 commit；review 失效通过 amend 补审进同一 commit 修复。"""

import json

import pytest

from test_dispatch_integration import (
    _cleanup, _git, _handoff, _identity_args, _prepare_done, _read_ledger,
    _reserve, _task_cli, _worktree, git_repo,
)
from review_support import ensure_review_evidence, pass_report
from task_flow_support import finish_cli, mark_done, prepare_finish
from repo_task import monitoring
from repo_task.documents import parse_front_matter, write_front_matter

REL = "docs/tasks/t001_alpha"
OWNER = "owner-repair"


def _recovery(repo, tid="t001"):
    before = _git(repo, "status", "--porcelain").stdout
    result = _task_cli(repo, "recovery", tid)
    assert result.returncode == 0, result.stderr
    assert _git(repo, "status", "--porcelain").stdout == before
    return json.loads(result.stdout)


def _verify(identity):
    return monitoring.verify_integrate_ready(
        "t001", identity["attempt"], identity["execution_id"]
    )


def _scope(worktree, task_rel=REL):
    fm, _ = parse_front_matter(worktree / task_rel / "progress.md")
    scope = monitoring.review_scope_fingerprint(fm["diff_anchor"], task_rel, repo_root=worktree)
    assert scope
    return scope


def _write_reports(worktree, *, tick="TICK-VALID", scope=None, spec_text=None, round_no=2):
    task_dir = worktree / REL
    if scope is None:
        scope = _scope(worktree)
    if spec_text is None:
        spec_text = (task_dir / "task.md").read_text(encoding="utf-8")
    for name in ("review_code.md", "review_test.md"):
        (task_dir / name).write_text(
            pass_report(scope, spec_text, round_no=round_no, tick=tick), encoding="utf-8",
        )
    return scope


def _amend(worktree):
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "--amend", "--no-edit")


def _make_stale(repo, *, terminal=False):
    """done 已提交；review 过程文件被改成 stale 现场。"""
    identity, branch, _ = _prepare_done(repo, "t001", "alpha", terminal=terminal)
    worktree = _worktree(repo, "t001")
    _write_reports(worktree, scope="stale-scope")
    _amend(worktree)
    return identity, branch, worktree, _git(worktree, "rev-parse", "HEAD").stdout.strip()


# --------------------------------------------------------------------------
# stale -> review_repair -> amend
# --------------------------------------------------------------------------


def test_stale_review_recovery_points_to_review_repair_and_cleanup_rejects(git_repo):
    identity, _, worktree, _ = _make_stale(git_repo)

    state = _recovery(git_repo)

    assert state["phase"] == "review_repair"
    assert "禁止 rewind、reserve" in state["action"]
    assert "amend" in state["action"]
    assert "run-close" in state["action"]
    assert "证据 commit" not in state["action"]
    assert "后继分支" not in state["action"]
    result = _task_cli(git_repo, "cleanup-worktree", "t001", *_identity_args(identity))
    assert result.returncode != 0
    assert worktree.is_dir()


def test_amend_review_restores_cleanup(git_repo):
    identity, branch, worktree, _ = _make_stale(git_repo, terminal=True)
    anchor = _git(worktree, "rev-parse", "HEAD^1").stdout.strip()

    _write_reports(worktree)
    _amend(worktree)

    assert _git(worktree, "rev-list", "--count", f"{anchor}..HEAD").stdout.strip() == "1"
    verdict, kind, detail = _verify(identity)
    assert verdict == "ready", detail
    assert kind == "ready"
    _cleanup(git_repo, "t001", identity)
    assert _git(git_repo, "rev-list", "--count", f"main..{branch}").stdout.strip() == "1"


def test_amend_then_integrate_archives_single_commit(git_repo):
    identity, _, worktree, _ = _make_stale(git_repo, terminal=True)
    _write_reports(worktree)
    _amend(worktree)
    tip = _git(worktree, "rev-parse", "HEAD").stdout.strip()

    _cleanup(git_repo, "t001", identity)
    assert _task_cli(git_repo, "integrate", "t001", *_identity_args(identity)).returncode == 0
    finished = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity), "--continue"
    )

    assert finished.returncode == 0, finished.stderr
    assert _git(git_repo, "merge-base", "--is-ancestor", tip, "main", check=False).returncode == 0
    assert (git_repo / "docs/archive/tasks/t001_alpha/review_code.md").is_file()
    assert not (git_repo / REL).exists()
    assert len([e for e in _read_ledger(git_repo) if e["event"] == "integrated"]) == 1


def test_merge_commit_in_execution_branch_is_rejected(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    worktree = _worktree(git_repo, "t001")
    _git(git_repo, "checkout", "-q", "-b", "side", "main")
    _git(git_repo, "commit", "--allow-empty", "-m", "side")
    _git(git_repo, "checkout", "-q", "main")
    assert _git(worktree, "merge", "--no-ff", "-m", "merge side", "side", check=False).returncode == 0

    verdict, kind, detail = _verify(identity)

    assert verdict == "contract"
    assert "恰好一个实现 commit" in detail
    result = _task_cli(git_repo, "cleanup-worktree", "t001", *_identity_args(identity))
    assert result.returncode != 0
    assert "恰好一个实现 commit" in result.stderr
    assert worktree.is_dir()


def test_amend_with_tick_void_is_blocked(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    worktree = _worktree(git_repo, "t001")
    _write_reports(worktree, tick="TICK-VOID")
    _amend(worktree)

    result = _task_cli(git_repo, "cleanup-worktree", "t001", *_identity_args(identity))

    assert result.returncode != 0
    assert "overall=FAIL" in result.stderr
    assert worktree.is_dir()


def test_amend_with_foreign_ac_rows_is_blocked(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    worktree = _worktree(git_repo, "t001")
    task_dir = worktree / REL
    spec_text = (task_dir / "task.md").read_text(encoding="utf-8")
    assert "AC-001" in spec_text
    _write_reports(worktree, spec_text=spec_text.replace("AC-001", "AC-999"))
    _amend(worktree)

    result = _task_cli(git_repo, "cleanup-worktree", "t001", *_identity_args(identity))

    assert result.returncode != 0
    assert "overall=INCOMPLETE" in result.stderr
    assert "AC-999" in result.stderr
    assert worktree.is_dir()


def test_recovery_stops_when_successor_branch_contains_tip(git_repo):
    _, _, worktree, tip = _make_stale(git_repo)
    _git(git_repo, "branch", "t002_follower", tip)

    state = _recovery(git_repo)

    assert state["phase"] == "review_repair"
    assert "后继分支" in state["action"]
    assert "不 amend" in state["action"]
    assert "证据 commit" not in state["action"]


# --------------------------------------------------------------------------
# 第二个 commit 无论内容一律拒绝（cleanup / integrate / ledger import / recovery）
# --------------------------------------------------------------------------


def _second_commit_review_only(worktree):
    (worktree / REL / "review_code.md").write_text("verdict: PASS\n", encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "chore(t001): extra review commit")


def _second_commit_delivery(worktree):
    (worktree / "README.md").write_text("second commit change\n", encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "chore(t001): extra delivery commit")


@pytest.mark.parametrize("writer", [_second_commit_review_only, _second_commit_delivery])
def test_second_commit_is_rejected_everywhere(git_repo, writer):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    worktree = _worktree(git_repo, "t001")
    writer(worktree)

    verdict, kind, detail = _verify(identity)
    assert verdict == "contract"
    assert kind == "provenance"
    assert "恰好一个实现 commit" in detail

    cleanup = _task_cli(git_repo, "cleanup-worktree", "t001", *_identity_args(identity))
    assert cleanup.returncode != 0 and "恰好一个实现 commit" in cleanup.stderr
    integrate = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity))
    assert integrate.returncode != 0 and "恰好一个实现 commit" in integrate.stderr
    state = _recovery(git_repo)
    assert state["phase"] == "needs_attention"
    assert "恰好一个实现 commit" in state["action"]
    assert worktree.is_dir()

    ledger = git_repo / "docs/runtime/dispatch_ledger.jsonl"
    ledger.unlink()
    imported = _task_cli(git_repo, "ledger", "import", "t001", "--confirm-stopped")
    assert imported.returncode != 0
    assert "恰好一个实现 commit" in imported.stderr
    assert not ledger.exists()


# --------------------------------------------------------------------------
# review_scope / review_level
# --------------------------------------------------------------------------


def test_amend_changing_delivery_is_rejected_by_review_scope(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    worktree = _worktree(git_repo, "t001")
    (worktree / "README.md").write_text("changed after review\n", encoding="utf-8")
    _amend(worktree)

    verdict, kind, detail = _verify(identity)

    assert verdict == "contract"
    assert kind == "review_scope_mismatch"
    assert "review_scope" in detail
    assert "finish-unwind" in detail
    result = _task_cli(git_repo, "cleanup-worktree", "t001", *_identity_args(identity))
    assert result.returncode != 0
    assert "review_scope" in result.stderr
    assert worktree.is_dir()


def test_handoff_missing_review_scope_is_contract(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    worktree = _worktree(git_repo, "t001")
    path = worktree / REL / "handoff.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("review_scope")
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    _amend(worktree)

    verdict, kind, detail = _verify(identity)

    assert verdict == "contract"
    assert kind == "handoff"
    assert "review_scope" in detail
    result = _task_cli(git_repo, "cleanup-worktree", "t001", *_identity_args(identity))
    assert result.returncode != 0
    assert "review_scope" in result.stderr


def test_review_level_downgrade_in_implementation_commit_is_rejected(git_repo):
    assert _task_cli(git_repo, "start", "t001").returncode == 0
    identity = _reserve(git_repo, "t001")
    worktree = _worktree(git_repo, "t001")
    task_dir = worktree / REL
    spec = task_dir / "task.md"
    spec.write_text(spec.read_text(encoding="utf-8").replace("- [ ] AC-001", "- [x] AC-001"),
                    encoding="utf-8")
    mark_done(worktree, "t001")
    base = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    fm, body = parse_front_matter(task_dir / "progress.md")
    fm["review_level"] = "single"
    write_front_matter(task_dir / "progress.md", fm, body)
    ensure_review_evidence(worktree, task_dir, base)
    payload = _handoff(
        "t001", "t001_alpha", identity, base,
        review_scope=monitoring.review_scope_fingerprint(base, REL, repo_root=worktree),
    )
    (task_dir / "handoff.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "feat(t001): complete alpha")
    terminal = _task_cli(
        git_repo, "attempt", "terminal", "t001", *_identity_args(identity),
        "--status", "completed",
    )
    assert terminal.returncode == 0, terminal.stderr

    result = _task_cli(
        git_repo, "attempt", "report", "t001", *_identity_args(identity),
        "--status", "done", "--sha", _git(worktree, "rev-parse", "HEAD").stdout.strip(),
    )

    assert result.returncode != 0
    assert "review_level 被降级" in result.stderr


def test_review_level_downgrade_by_amend_is_rejected(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    worktree = _worktree(git_repo, "t001")
    path = worktree / REL / "progress.md"
    fm, body = parse_front_matter(path)
    fm["review_level"] = "single"
    write_front_matter(path, fm, body)
    _amend(worktree)

    verdict, kind, detail = _verify(identity)

    assert verdict == "contract"
    assert kind == "provenance"
    assert "review_level 被降级" in detail
    result = _task_cli(git_repo, "cleanup-worktree", "t001", *_identity_args(identity))
    assert result.returncode != 0
    assert "review_level 被降级" in result.stderr


def test_single_to_full_upgrade_still_passes(git_repo):
    path = git_repo / REL / "progress.md"
    fm, body = parse_front_matter(path)
    fm["review_level"] = "single"
    write_front_matter(path, fm, body)
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "chore(t001): start at single review")

    assert _task_cli(git_repo, "start", "t001").returncode == 0
    identity = _reserve(git_repo, "t001")
    worktree = _worktree(git_repo, "t001")
    task_dir = worktree / REL
    spec = task_dir / "task.md"
    spec.write_text(spec.read_text(encoding="utf-8").replace("- [ ] AC-001", "- [x] AC-001"),
                    encoding="utf-8")
    upgraded = _task_cli(worktree, "review-upgrade", "t001", "--reason", "test upgrade")
    assert upgraded.returncode == 0, upgraded.stderr
    mark_done(worktree, "t001")
    base = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    ensure_review_evidence(worktree, task_dir, base)
    payload = _handoff(
        "t001", "t001_alpha", identity, base,
        review_scope=monitoring.review_scope_fingerprint(base, REL, repo_root=worktree),
    )
    (task_dir / "handoff.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "feat(t001): complete alpha")
    head = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    terminal = _task_cli(
        git_repo, "attempt", "terminal", "t001", *_identity_args(identity),
        "--status", "completed",
    )
    assert terminal.returncode == 0, terminal.stderr
    report = _task_cli(
        git_repo, "attempt", "report", "t001", *_identity_args(identity),
        "--status", "done", "--sha", head,
    )
    assert report.returncode == 0, report.stderr
    _cleanup(git_repo, "t001", identity)


# --------------------------------------------------------------------------
# report 已 done 不 amend；run-close 失败 -> review_repair -> 一次收尾
# --------------------------------------------------------------------------


def test_recovery_stops_when_report_done_and_gate_fails(git_repo):
    _prepare_done(git_repo, "t001", "alpha")
    worktree = _worktree(git_repo, "t001")
    _write_reports(worktree, tick="TICK-VOID")
    _amend(worktree)

    state = _recovery(git_repo)

    assert state["phase"] != "review_repair"
    assert "report 已写入 done" in state["action"]
    assert "不 amend" in state["action"]


def test_run_close_gate_failure_recovers_with_single_amend(git_repo):
    identity, _, worktree, _ = _make_stale(git_repo)
    claimed = _task_cli(
        git_repo, "attempt", "claim", "t001", *_identity_args(identity),
        "--owner-id", OWNER,
    )
    assert claimed.returncode == 0, claimed.stderr

    blocked = _task_cli(
        git_repo, "run-close", "t001", "--owner-id", OWNER, "--result", "done",
    )

    assert blocked.returncode != 0
    assert "review_repair" in blocked.stderr
    state = _recovery(git_repo)
    assert state["phase"] == "review_repair"
    assert "run-close t001 --owner-id ... --result done" in state["action"]

    _write_reports(worktree)
    _amend(worktree)
    closed = _task_cli(
        git_repo, "run-close", "t001", "--owner-id", OWNER, "--result", "done",
    )

    assert closed.returncode == 0, closed.stderr
    assert not worktree.exists()
    assert _recovery(git_repo)["phase"] == "closed"


def test_ledger_import_accepts_single_commit(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    (git_repo / "docs/runtime/dispatch_ledger.jsonl").unlink()

    imported = _task_cli(git_repo, "ledger", "import", "t001", "--confirm-stopped")

    assert imported.returncode == 0, imported.stderr
    assert json.loads(imported.stdout)["execution_id"] == identity["execution_id"]


def test_reserve_and_rewind_messages_point_to_review_repair(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    stale_reserve = _task_cli(git_repo, "attempt", "reserve", "t001")
    assert stale_reserve.returncode != 0
    assert "recovery" in stale_reserve.stderr and "amend" in stale_reserve.stderr
    assert "证据 commit" not in stale_reserve.stderr

    _cleanup(git_repo, "t001", identity)
    assert _task_cli(git_repo, "integrate", "t001", *_identity_args(identity)).returncode == 0
    assert _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity), "--continue"
    ).returncode == 0
    reserve = _task_cli(git_repo, "attempt", "reserve", "t001")
    assert reserve.returncode != 0
    assert "recovery" in reserve.stderr and "amend" in reserve.stderr
    assert "证据 commit" not in reserve.stderr
    rewind = _task_cli(git_repo, "rewind", "t001", "--reason", "try")
    assert rewind.returncode != 0
    assert "recovery" in rewind.stderr and "amend" in rewind.stderr
    assert "证据 commit" not in rewind.stderr


# --------------------------------------------------------------------------
# review_scope 不一致 -> review_mismatch -> finish-unwind 撤销重做
# --------------------------------------------------------------------------


def test_review_scope_mismatch_recovers_via_finish_unwind(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha", terminal=False)
    worktree = _worktree(git_repo, "t001")
    anchor = _git(worktree, "rev-parse", "HEAD^1").stdout.strip()
    (worktree / "README.md").write_text("changed after review\n", encoding="utf-8")
    _amend(worktree)
    assert _git(worktree, "rev-parse", "HEAD^1").stdout.strip() == anchor

    state = _recovery(git_repo)
    assert state["phase"] == "review_mismatch"
    assert "finish-unwind" in state["action"]
    assert "禁止 rewind、reserve" in state["action"]

    claimed = _task_cli(
        git_repo, "attempt", "claim", "t001", *_identity_args(identity), "--owner-id", OWNER,
    )
    assert claimed.returncode == 0, claimed.stderr
    closed = _task_cli(git_repo, "run-close", "t001", "--owner-id", OWNER, "--result", "done")
    assert closed.returncode != 0
    assert "review_scope" in closed.stderr

    rewind = _task_cli(worktree, "rewind", "t001", "--reason", "try")
    assert rewind.returncode != 0

    unwound = _task_cli(
        git_repo, "finish-unwind", "t001", "--reason", "test unwind", *_identity_args(identity),
    )
    assert unwound.returncode == 0, unwound.stderr
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == anchor
    fm, _ = parse_front_matter(worktree / REL / "progress.md")
    assert fm["status"] == "active"
    assert fm["worktree"], "unwind 后 worktree 归属必须恢复，否则后续 finish 认不出自身 worktree"

    message = prepare_finish(worktree, "t001", "alpha")
    finished = finish_cli(worktree, "t001", OWNER, message)
    assert finished.returncode == 0, finished.stdout + finished.stderr
    assert "finish=OK" in finished.stdout
    again = _task_cli(git_repo, "run-close", "t001", "--owner-id", OWNER, "--result", "done")
    assert again.returncode == 0, again.stderr
    assert _recovery(git_repo)["phase"] == "closed"


def test_finish_unwind_refuses_with_successor(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha", terminal=False)
    worktree = _worktree(git_repo, "t001")
    (worktree / "README.md").write_text("changed after review\n", encoding="utf-8")
    _amend(worktree)
    tip = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    _git(git_repo, "branch", "t002_follower", tip)

    state = _recovery(git_repo)
    assert state["phase"] != "review_mismatch"
    assert "后继" in state["action"]
    refused = _task_cli(
        git_repo, "finish-unwind", "t001", "--reason", "x", *_identity_args(identity),
    )
    assert refused.returncode != 0
    assert "后继" in refused.stderr
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == tip


def test_finish_unwind_refuses_when_report_done(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    worktree = _worktree(git_repo, "t001")
    (worktree / "README.md").write_text("changed after review\n", encoding="utf-8")
    _amend(worktree)

    state = _recovery(git_repo)
    assert "report 已写入 done" in state["action"]
    refused = _task_cli(
        git_repo, "finish-unwind", "t001", "--reason", "x", *_identity_args(identity),
    )
    assert refused.returncode != 0
    assert "report 已写入 done" in refused.stderr


def test_finish_unwind_refuses_review_gate_failure(git_repo):
    """报告过期（review_repair 现场）不得用 finish-unwind 撤销。"""
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha", terminal=False)
    worktree = _worktree(git_repo, "t001")
    _write_reports(worktree, tick="TICK-VOID")
    _amend(worktree)
    tip = _git(worktree, "rev-parse", "HEAD").stdout.strip()

    state = _recovery(git_repo)
    assert state["phase"] == "review_repair"
    refused = _task_cli(
        git_repo, "finish-unwind", "t001", "--reason", "x", *_identity_args(identity),
    )
    assert refused.returncode != 0
    assert "不是内容不一致" in refused.stderr
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == tip


def test_finish_unwind_refuses_terminal_attempt_without_report(git_repo):
    """terminal 已关闭又无 report 时不撤销，recovery 直接停止。"""
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha", terminal=False)
    worktree = _worktree(git_repo, "t001")
    terminal = _task_cli(
        git_repo, "attempt", "terminal", "t001", *_identity_args(identity),
        "--status", "completed",
    )
    assert terminal.returncode == 0, terminal.stderr
    (worktree / "README.md").write_text("changed after review\n", encoding="utf-8")
    _amend(worktree)
    tip = _git(worktree, "rev-parse", "HEAD").stdout.strip()

    state = _recovery(git_repo)
    assert state["phase"] != "review_mismatch"
    assert "人工核查" in state["action"]
    refused = _task_cli(
        git_repo, "finish-unwind", "t001", "--reason", "x", *_identity_args(identity),
    )
    assert refused.returncode != 0
    assert "只处理 running 现场" in refused.stderr
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == tip
