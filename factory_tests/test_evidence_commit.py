"""D2：执行分支 = 1 个实现 commit + 0..n 个仅含 review 过程文件的证据 commit。

done 之后 review 失效时，补审证据作为证据 commit 追加，再按原 identity cleanup；
不 amend、不 rewind、不 reserve。证据 commit 触及其它任何路径（交付文件、handoff.json、
其它 task 目录、task 目录内的非过程文件）一律拒绝。
"""
import json

from test_dispatch_integration import (
    _cleanup, _git, _handoff, _identity_args, _prepare_done, _read_ledger, _reserve,
    _task_cli, _worktree, git_repo,
)
from review_support import ensure_review_evidence, pass_report
from task_flow_support import mark_done
from repo_task import monitoring
from repo_task.documents import parse_front_matter
from repo_task.monitoring import EVIDENCE_COMMIT_FILES, REVIEW_PROCESS_FILES, review_scope_fingerprint

REL = "docs/tasks/t001_alpha"


def _recovery(repo, tid="t001"):
    before = _git(repo, "status", "--porcelain").stdout
    result = _task_cli(repo, "recovery", tid)
    assert result.returncode == 0, result.stderr
    assert _git(repo, "status", "--porcelain").stdout == before
    return json.loads(result.stdout)


def _make_stale(repo):
    """report 之后交付内容被改写，使最终提交的 review 失效。

    证据 commit 规程针对这种现场；这里用 amend 仅作为构造现场的篡改手段。
    """
    identity, branch, _ = _prepare_done(repo, "t001", "alpha")
    w = _worktree(repo, "t001")
    (w / "README.md").write_text("unreviewed finalization change\n")
    _git(w, "add", "-A")
    _git(w, "commit", "--amend", "--no-edit")
    return identity, branch, w


def _write_pass_reports(worktree, *, tick="TICK-VALID", round_no=2, task_rel=REL):
    fm, _ = parse_front_matter(worktree / task_rel / "note.md")
    scope = review_scope_fingerprint(fm["diff_anchor"], task_rel, repo_root=worktree)
    assert scope
    spec_text = (worktree / task_rel / "task.md").read_text(encoding="utf-8")
    for name in ("review_code.md", "review_test.md"):
        (worktree / task_rel / name).write_text(
            pass_report(scope, spec_text, round_no=round_no, tick=tick), encoding="utf-8",
        )
    return scope


def _commit_evidence(worktree, *, tick="TICK-VALID", message="chore(t001): 补审证据"):
    """重新 review 当前 tip，并把新报告作为证据 commit 追加。"""
    scope = _write_pass_reports(worktree, tick=tick)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", message)
    return scope


def _verify(identity):
    return monitoring.verify_integrate_ready("t001", identity["attempt"], identity["execution_id"])


# --------------------------------------------------------------------------
# 证据 commit 的允许集合
# --------------------------------------------------------------------------


def test_evidence_files_derive_from_review_process_files_without_handoff():
    assert set(EVIDENCE_COMMIT_FILES) == set(REVIEW_PROCESS_FILES) - {"handoff.json"}
    assert {"note.md", "review_code.md", "review_test.md", "review_general.md"} <= set(EVIDENCE_COMMIT_FILES)
    assert "task.md" not in EVIDENCE_COMMIT_FILES


# --------------------------------------------------------------------------
# stale -> 证据 commit -> cleanup
# --------------------------------------------------------------------------


def test_stale_review_recovery_points_to_evidence_commit_and_cleanup_still_rejects(git_repo):
    identity, _, w = _make_stale(git_repo)
    state = _recovery(git_repo)
    assert state["phase"] == "evidence_repair"
    assert "证据 commit" in state["action"]
    assert "amend" not in state["action"]
    result = _task_cli(git_repo, "cleanup-worktree", "t001", *_identity_args(identity))
    assert result.returncode != 0
    assert "review" in result.stderr.lower()
    assert w.is_dir()


def test_review_only_evidence_commit_restores_cleanup(git_repo):
    identity, branch, w = _make_stale(git_repo)
    implementation = _git(w, "rev-parse", "HEAD").stdout.strip()
    _commit_evidence(w)
    assert _git(w, "rev-parse", "HEAD^1").stdout.strip() == implementation
    assert _git(w, "status", "--porcelain").stdout == ""
    verdict, detail = _verify(identity)
    assert verdict == "ready", detail
    assert "1 个证据 commit" in detail
    result = _task_cli(git_repo, "cleanup-worktree", "t001", *_identity_args(identity))
    assert result.returncode == 0, result.stderr
    assert not w.exists() or not (w / ".git").exists()
    assert _git(git_repo, "rev-list", "--count", f"main..{branch}").stdout.strip() == "2"


def test_several_evidence_commits_are_accepted(git_repo):
    identity, _, w = _make_stale(git_repo)
    _write_pass_reports(w, tick="TICK-VOID")
    _git(w, "add", "-A")
    _git(w, "commit", "-m", "chore(t001): 补审证据 round 2")
    assert _verify(identity)[0] == "contract"
    _commit_evidence(w, message="chore(t001): 补审证据 round 3")
    verdict, detail = _verify(identity)
    assert verdict == "ready", detail
    assert "2 个证据 commit" in detail


def test_empty_evidence_commit_is_harmless(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    w = _worktree(git_repo, "t001")
    _git(w, "commit", "--allow-empty", "-m", "chore(t001): empty marker")
    assert _verify(identity)[0] == "ready"


def test_integrate_carries_evidence_commits_into_main_with_archive(git_repo):
    identity, branch, w = _make_stale(git_repo)
    _commit_evidence(w)
    tip = _git(w, "rev-parse", "HEAD").stdout.strip()
    _cleanup(git_repo, "t001", identity)
    assert _task_cli(git_repo, "integrate", "t001", *_identity_args(identity)).returncode == 0
    finished = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity), "--continue")
    assert finished.returncode == 0, finished.stderr
    assert _git(git_repo, "merge-base", "--is-ancestor", tip, "main", check=False).returncode == 0
    assert (git_repo / "docs/archive/tasks/t001_alpha/review_code.md").is_file()
    assert not (git_repo / REL).exists()
    assert len([e for e in _read_ledger(git_repo) if e["event"] == "integrated"]) == 1


def test_chain_with_evidence_commit_on_predecessor_stays_continuous(git_repo):
    first, first_branch, _ = _prepare_done(git_repo, "t001", "alpha")
    w1 = _worktree(git_repo, "t001")
    (w1 / "README.md").write_text("late change\n")
    _git(w1, "add", "-A")
    _git(w1, "commit", "--amend", "--no-edit")
    _commit_evidence(w1)
    _cleanup(git_repo, "t001", first)
    second, _, _ = _prepare_done(git_repo, "t002", "beta", base=first_branch)
    _cleanup(git_repo, "t002", second)
    assert _task_cli(git_repo, "integrate-chain", "t002").returncode == 0
    finished = _task_cli(git_repo, "integrate-chain", "t002", "--continue")
    assert finished.returncode == 0, finished.stderr
    assert not (git_repo / "docs/tasks/t001_alpha").exists()
    assert (git_repo / "docs/archive/tasks/t001_alpha/handoff.json").is_file()
    assert (git_repo / "docs/archive/tasks/t002_beta/handoff.json").is_file()


# --------------------------------------------------------------------------
# 违规：证据 commit 触及其它路径
# --------------------------------------------------------------------------


def _assert_rejected(repo, identity, w, needle):
    verdict, detail = _verify(identity)
    assert verdict == "contract"
    assert "证据 commit" in detail
    assert needle in detail
    result = _task_cli(repo, "cleanup-worktree", "t001", *_identity_args(identity))
    assert result.returncode != 0
    assert "证据 commit" in result.stderr
    assert w.is_dir()


def _extra_commit(w, writer):
    writer(w)
    _git(w, "add", "-A")
    _git(w, "commit", "-m", "chore(t001): 越界改动")


def test_evidence_commit_touching_deliverable_is_rejected(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    w = _worktree(git_repo, "t001")
    _extra_commit(w, lambda root: (root / "README.md").write_text("second commit change\n"))
    _assert_rejected(git_repo, identity, w, "README.md")
    assert _recovery(git_repo)["phase"] != "evidence_repair"


def test_evidence_commit_mixing_review_file_and_deliverable_is_rejected(git_repo):
    identity, _, w = _make_stale(git_repo)

    def mixed(root):
        _write_pass_reports(root)
        (root / "src_extra.py").write_text("x = 1\n")

    _extra_commit(w, mixed)
    _assert_rejected(git_repo, identity, w, "src_extra.py")


def test_evidence_commit_touching_handoff_is_rejected(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    w = _worktree(git_repo, "t001")

    def rewrite(root):
        path = root / REL / "handoff.json"
        payload = json.loads(path.read_text("utf-8"))
        payload["review"] = "rewritten"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    _extra_commit(w, rewrite)
    _assert_rejected(git_repo, identity, w, "handoff.json")


def test_evidence_commit_touching_task_spec_is_rejected(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    w = _worktree(git_repo, "t001")
    spec = w / REL / "task.md"
    _extra_commit(w, lambda root: spec.write_text(spec.read_text("utf-8") + "\n事后补写的需求\n", encoding="utf-8"))
    _assert_rejected(git_repo, identity, w, f"{REL}/task.md")


def test_evidence_commit_touching_another_task_dir_is_rejected(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    w = _worktree(git_repo, "t001")
    _extra_commit(
        w, lambda root: (root / "docs/tasks/t002_beta/review_code.md").write_text("verdict: PASS\n"),
    )
    _assert_rejected(git_repo, identity, w, "docs/tasks/t002_beta/review_code.md")


def test_evidence_commit_with_nested_review_file_is_rejected(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    w = _worktree(git_repo, "t001")

    def nested(root):
        (root / REL / "sub").mkdir()
        (root / REL / "sub/review_code.md").write_text("verdict: PASS\n")

    _extra_commit(w, nested)
    _assert_rejected(git_repo, identity, w, f"{REL}/sub/review_code.md")


def test_merge_commit_in_execution_branch_is_rejected(git_repo):
    identity, branch, _ = _prepare_done(git_repo, "t001", "alpha")
    w = _worktree(git_repo, "t001")
    _git(git_repo, "checkout", "-q", "-b", "side", "main")
    _git(git_repo, "commit", "--allow-empty", "-m", "side")
    _git(git_repo, "checkout", "-q", "main")
    assert _git(w, "merge", "--no-ff", "-m", "merge side", "side", check=False).returncode == 0
    verdict, detail = _verify(identity)
    assert verdict == "contract"
    assert "证据 commit" in detail


def test_tick_void_in_evidence_commit_blocks_cleanup(git_repo):
    """J3：已提交的 PASS 报告若含 TICK-VOID，integrate 门禁同样判不过。"""
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    w = _worktree(git_repo, "t001")
    _commit_evidence(w, tick="TICK-VOID")
    result = _task_cli(git_repo, "cleanup-worktree", "t001", *_identity_args(identity))
    assert result.returncode != 0
    assert "overall=FAIL" in result.stderr
    assert w.is_dir()


def test_review_ac_rows_must_match_committed_task_md_at_cleanup(git_repo):
    """J3：integrate 门禁须读取已提交的 task.md，报告 AC 行与其编号不一致时不放行。"""
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    w = _worktree(git_repo, "t001")
    fm, _ = parse_front_matter(w / REL / "note.md")
    scope = review_scope_fingerprint(fm["diff_anchor"], REL, repo_root=w)
    spec_text = (w / REL / "task.md").read_text(encoding="utf-8")
    assert "AC-001" in spec_text
    foreign_spec = spec_text.replace("AC-001", "AC-999")
    for name in ("review_code.md", "review_test.md"):
        (w / REL / name).write_text(pass_report(scope, foreign_spec, round_no=2), encoding="utf-8")
    _git(w, "add", "-A")
    _git(w, "commit", "-m", "chore(t001): 补审证据")
    result = _task_cli(git_repo, "cleanup-worktree", "t001", *_identity_args(identity))
    assert result.returncode != 0
    assert "overall=INCOMPLETE" in result.stderr
    assert "AC-999" in result.stderr
    assert w.is_dir()


# --------------------------------------------------------------------------
# report / ledger import / recovery
# --------------------------------------------------------------------------


def test_report_done_rejects_stale_and_succeeds_after_evidence_commit(git_repo):
    assert _task_cli(git_repo, "start", "t001").returncode == 0
    identity = _reserve(git_repo, "t001")
    w = _worktree(git_repo, "t001")
    spec = w / REL / "task.md"
    spec.write_text(spec.read_text().replace("- [ ] AC-001", "- [x] AC-001"), encoding="utf-8")
    mark_done(w, "t001")
    base_sha = _git(w, "rev-parse", "HEAD").stdout.strip()
    # 先写 review 证据，再改交付内容：提交后的 tip 已失效（review-at-09:33, commit-at-09:35）
    ensure_review_evidence(w, w / REL, base_sha)
    (w / "README.md").write_text("change after review\n")
    (w / REL / "handoff.json").write_text(
        json.dumps(_handoff("t001", "t001_alpha", identity, base_sha), ensure_ascii=False),
        encoding="utf-8",
    )
    _git(w, "add", "-A")
    _git(w, "commit", "-m", "feat(t001): complete alpha")
    assert _task_cli(
        git_repo, "attempt", "terminal", "t001", *_identity_args(identity), "--status", "completed",
    ).returncode == 0
    blocked = _task_cli(
        git_repo, "attempt", "report", "t001", *_identity_args(identity),
        "--status", "done", "--sha", _git(w, "rev-parse", "HEAD").stdout.strip(),
    )
    assert blocked.returncode != 0
    assert "review" in blocked.stderr.lower()
    assert "amend" not in blocked.stderr
    assert _recovery(git_repo)["phase"] == "evidence_repair"
    _commit_evidence(w)
    tip = _git(w, "rev-parse", "HEAD").stdout.strip()
    ok = _task_cli(
        git_repo, "attempt", "report", "t001", *_identity_args(identity), "--status", "done", "--sha", tip,
    )
    assert ok.returncode == 0, ok.stderr
    assert _recovery(git_repo)["phase"] == "reported_uncleaned"


def test_recovery_accepts_worktree_head_with_evidence_commits(git_repo):
    identity, _, w = _make_stale(git_repo)
    _commit_evidence(w)
    state = _recovery(git_repo)
    assert state["phase"] == "reported_uncleaned"
    assert state["execution_id"] == identity["execution_id"]


def test_recovery_stops_when_worktree_head_has_out_of_scope_commit(git_repo):
    _prepare_done(git_repo, "t001", "alpha")
    w = _worktree(git_repo, "t001")
    _extra_commit(w, lambda root: (root / "README.md").write_text("second commit change\n"))
    state = _recovery(git_repo)
    assert state["phase"] == "needs_attention"
    assert "证据 commit" in state["action"]


def test_recovery_stops_when_successor_branch_contains_tip(git_repo):
    identity, branch, w = _make_stale(git_repo)
    tip = _git(w, "rev-parse", "HEAD").stdout.strip()
    _git(git_repo, "branch", "t002_follower", tip)
    state = _recovery(git_repo)
    assert state["phase"] == "evidence_repair"
    assert "后继分支" in state["action"]
    assert "不追加证据 commit" in state["action"]


def test_ledger_import_accepts_evidence_commits(git_repo):
    identity, _, w = _make_stale(git_repo)
    _commit_evidence(w)
    (git_repo / "docs/runtime/dispatch_ledger.jsonl").unlink()
    imported = _task_cli(git_repo, "ledger", "import", "t001", "--confirm-stopped")
    assert imported.returncode == 0, imported.stderr
    assert json.loads(imported.stdout)["execution_id"] == identity["execution_id"]


def test_ledger_import_rejects_out_of_scope_commit(git_repo):
    _prepare_done(git_repo, "t001", "alpha")
    w = _worktree(git_repo, "t001")
    _extra_commit(w, lambda root: (root / "README.md").write_text("second commit change\n"))
    ledger = git_repo / "docs/runtime/dispatch_ledger.jsonl"
    ledger.unlink()
    result = _task_cli(git_repo, "ledger", "import", "t001", "--confirm-stopped")
    assert result.returncode != 0
    assert "证据 commit" in result.stderr
    assert not ledger.exists()


def test_reserve_rewind_messages_point_to_evidence_commit_recovery(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    stale_reserve = _task_cli(git_repo, "attempt", "reserve", "t001")
    assert stale_reserve.returncode != 0
    _cleanup(git_repo, "t001", identity)
    assert _task_cli(git_repo, "integrate", "t001", *_identity_args(identity)).returncode == 0
    assert _task_cli(git_repo, "integrate", "t001", *_identity_args(identity), "--continue").returncode == 0
    reserve = _task_cli(git_repo, "attempt", "reserve", "t001")
    assert reserve.returncode != 0
    assert "recovery" in reserve.stderr
    assert "证据 commit" in reserve.stderr
    assert "amend" not in reserve.stderr
    rewind = _task_cli(git_repo, "rewind", "t001", "--reason", "try")
    assert rewind.returncode != 0
    assert "recovery" in rewind.stderr
    assert "amend" not in rewind.stderr
