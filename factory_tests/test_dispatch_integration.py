"""Real-git tests for exact handoff, cleanup, single integration, and chain transactions."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "repo/.repo_template/scripts"
TASK_TEMPLATE_DIR = SCRIPTS_DIR.parent / "docs" / "task_template"
sys.path.insert(0, str(SCRIPTS_DIR))

from review_support import ensure_review_evidence, configure_project_verification, valid_spec as _valid_spec
from task_flow_support import mark_done

from repo_task import attempts
from repo_task import context as ctx
from repo_task import control
from repo_task import integration
from repo_task import monitoring
from repo_task.documents import parse_front_matter, write_front_matter


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check, timeout=120,
    )


from git_repo_support import fast_copy_repo


@pytest.fixture
def git_repo(tmp_path, workflow_three_tasks_seed, monkeypatch):
    repo = tmp_path / "repo"
    return fast_copy_repo(workflow_three_tasks_seed, repo, tmp_path, monkeypatch)


def _task_cli(repo, *args):
    return subprocess.run(
        [sys.executable, str(repo / ".repo_template" / "scripts" / "task.py"), *args],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120,
    )


def _read_ledger(repo):
    path = repo / "docs" / "runtime" / "dispatch_ledger.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _worktree(repo, tid):
    return repo.parent / f"{repo.name}_{tid}"


def _reserve(repo, tid, model=None):
    args = ["attempt", "reserve", tid]
    if model:
        args += ["--model", model]
    result = _task_cli(repo, *args)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _identity_args(identity):
    return [
        "--attempt", str(identity["attempt"]),
        "--execution-id", identity["execution_id"],
    ]


def _handoff(tid, branch, identity, base_sha, *, review_scope="", spec_source_sha="", **overrides):
    payload = {
        "tid": tid,
        "attempt": identity["attempt"],
        "execution_id": identity["execution_id"],
        "status": "done",
        "branch": branch,
        "base_sha": base_sha,
        "spec_source_sha": spec_source_sha or base_sha,
        "tests": "pytest -q",
        "blackbox": "pass",
        "review": "pass",
        "review_scope": review_scope,
        "pending": [],
        "findings": [],
    }
    payload.update(overrides)
    return payload


def _prepare_done(
    repo, tid, slug, *, base=None, handoff_overrides=None, mutate=None,
    terminal=True,
):
    context = ctx.RepoContext.for_root(repo)
    integration.cmd_start(argparse.Namespace(tid=tid, base=base, repo=context))
    identity = attempts.reserve_attempt(tid, repo=context)
    worktree = _worktree(repo, tid)
    task_file = worktree / "docs" / "tasks" / f"{tid}_{slug}" / "task.md"
    if task_file.is_file():
        content = task_file.read_text(encoding="utf-8")
        task_file.write_text(content.replace("- [ ] AC-001", "- [x] AC-001"), encoding="utf-8")
    if mutate:
        mutate(worktree)
    mark_done(worktree, tid)
    branch = f"{tid}_{slug}"
    base_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    task_dir = worktree / "docs" / "tasks" / branch
    ensure_review_evidence(worktree, task_dir, base_sha)
    review_scope = monitoring.review_scope_fingerprint(
        base_sha, task_dir.relative_to(worktree).as_posix(), repo_root=worktree,
    )
    fm, _ = parse_front_matter(task_dir / "progress.md")
    spec_source_sha = fm.get("spec_source_sha") or base_sha
    payload = _handoff(tid, branch, identity, base_sha, review_scope=review_scope, spec_source_sha=spec_source_sha)
    payload.update(handoff_overrides or {})
    (task_dir / "handoff.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", f"feat({tid}): complete {slug}")
    head = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    if terminal:
        attempts.terminal_attempt(
            tid, identity["attempt"], identity["execution_id"], "completed", repo=context
        )
        control.require_report_done_ready(
            tid, identity["attempt"], identity["execution_id"], repo=context
        )
        attempts.report_attempt(
            tid, identity["attempt"], identity["execution_id"], "done", sha=head, repo=context
        )
    return identity, branch, head


def _cleanup_cli(repo, tid, identity):
    result = _task_cli(repo, "cleanup-worktree", tid, *_identity_args(identity))
    assert result.returncode == 0, result.stderr
    return result


def _cleanup(repo, tid, identity):
    context = ctx.RepoContext.for_root(repo)
    return integration._cleanup_worktree(
        tid, identity["attempt"], identity["execution_id"], repo=context
    )


def _handoff_path(repo, tid, slug):
    return _worktree(repo, tid) / "docs" / "tasks" / f"{tid}_{slug}" / "handoff.json"


def _rewrite_handoff(repo, tid, slug, content):
    worktree = _worktree(repo, tid)
    path = _handoff_path(repo, tid, slug)
    if content is None:
        path.unlink()
    else:
        path.write_text(content, encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", f"test({tid}): alter handoff")


# --------------------------------------------------------------------------
# verify_integrate_ready：真实 refs、exact handoff 与 first-parent provenance
# --------------------------------------------------------------------------


def _verify(repo, tid, attempt, execution_id):
    return monitoring.verify_integrate_ready(
        tid, attempt, execution_id, repo=ctx.RepoContext.for_root(repo)
    )


def test_verify_ready_with_valid_handoff(git_repo):
    identity, branch, _ = _prepare_done(git_repo, "t001", "alpha")

    verdict, kind, detail = _verify(
        git_repo, "t001", identity["attempt"], identity["execution_id"]
    )

    assert verdict == "ready", detail
    parent = _git(git_repo, "rev-parse", f"{branch}^1").stdout.strip()
    payload = json.loads(_handoff_path(git_repo, "t001", "alpha").read_text("utf-8"))
    assert payload["base_sha"] == parent
    assert payload["attempt"] == identity["attempt"]
    assert payload["execution_id"] == identity["execution_id"]


def test_verify_incomplete_when_tip_not_terminal(git_repo):
    started = _task_cli(git_repo, "start", "t001")
    assert started.returncode == 0, started.stderr
    identity = _reserve(git_repo, "t001")

    verdict, kind, detail = _verify(
        git_repo, "t001", identity["attempt"], identity["execution_id"]
    )

    assert verdict == "incomplete"
    assert "非终态" in detail


def test_verify_incomplete_without_branch(git_repo):
    # verify_integrate_ready 只查分支/handoff/spec，不查 ledger；
    # 直接传假 identity 验证「无本地分支 → incomplete」，无需真实 reserve（RT-006 门禁要求 start 后 reserve）
    verdict, kind, detail = _verify(git_repo, "t001", 1, "fake-execution")

    assert verdict == "incomplete"
    assert "无本地 task 分支" in detail


@pytest.mark.parametrize(
    ("defect", "expected_detail"),
    [
        ("missing", "缺"),
        ("not-json", "无法解析"),
        ("not-object", "非 JSON 对象"),
        ("missing-status", "缺必填字段 status"),
        ("missing-attempt", "缺必填字段 attempt"),
        ("bool-attempt", "attempt"),
        ("zero-attempt", "attempt"),
        ("negative-attempt", "attempt"),
        ("active-status", "status='active'"),
        ("wrong-tid", "tid='t999'"),
        ("wrong-branch", "branch='t001_other'"),
        ("wrong-execution", "execution_id='wrong'"),
    ],
)
def test_verify_contract_on_handoff_defects(git_repo, defect, expected_detail):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    path = _handoff_path(git_repo, "t001", "alpha")
    payload = json.loads(path.read_text("utf-8"))
    if defect == "missing":
        content = None
    elif defect == "not-json":
        content = "not json"
    elif defect == "not-object":
        content = "[]"
    else:
        if defect == "missing-status":
            payload.pop("status")
        elif defect == "missing-attempt":
            payload.pop("attempt")
        elif defect == "bool-attempt":
            payload["attempt"] = True
        elif defect == "zero-attempt":
            payload["attempt"] = 0
        elif defect == "negative-attempt":
            payload["attempt"] = -1
        elif defect == "active-status":
            payload["status"] = "active"
        elif defect == "wrong-tid":
            payload["tid"] = "t999"
        elif defect == "wrong-branch":
            payload["branch"] = "t001_other"
        elif defect == "wrong-execution":
            payload["execution_id"] = "wrong"
        content = json.dumps(payload, ensure_ascii=False)
    _rewrite_handoff(git_repo, "t001", "alpha", content)

    verdict, kind, detail = _verify(
        git_repo, "t001", identity["attempt"], identity["execution_id"]
    )

    assert verdict == "contract"
    assert expected_detail in detail


def test_verify_contract_on_uncompleted_ac(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    worktree = _worktree(git_repo, "t001")
    spec_path = worktree / "docs" / "tasks" / "t001_alpha" / "task.md"
    content = spec_path.read_text(encoding="utf-8")
    spec_path.write_text(content.replace("- [x] AC-001", "- [ ] AC-001"), encoding="utf-8")
    _git(worktree, "add", str(spec_path))
    _git(worktree, "commit", "--amend", "--no-edit")

    verdict, kind, detail = _verify(
        git_repo, "t001", identity["attempt"], identity["execution_id"]
    )

    assert verdict == "contract"
    assert "存在未完成 AC（未打勾）：AC-001" in detail


def test_handoff_rejects_wrong_field_type(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    path = _handoff_path(git_repo, "t001", "alpha")
    payload = json.loads(path.read_text("utf-8"))
    payload["tests"] = ["pytest"]
    _rewrite_handoff(git_repo, "t001", "alpha", json.dumps(payload, ensure_ascii=False))

    verdict, kind, detail = _verify(
        git_repo, "t001", identity["attempt"], identity["execution_id"]
    )

    assert verdict == "contract"
    assert "tests" in detail


def test_handoff_rejects_base_sha_other_than_tip_first_parent(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    path = _handoff_path(git_repo, "t001", "alpha")
    payload = json.loads(path.read_text("utf-8"))
    payload["base_sha"] = "HEAD"
    _rewrite_handoff(git_repo, "t001", "alpha", json.dumps(payload, ensure_ascii=False))

    verdict, kind, detail = _verify(
        git_repo, "t001", identity["attempt"], identity["execution_id"]
    )

    assert verdict == "contract"
    assert "base_sha" in detail
    assert "执行分支必须恰好一个实现 commit" in detail


def test_handoff_rejects_diff_anchor_not_matching_implementation_parent(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    worktree = _worktree(git_repo, "t001")
    task_path = worktree / "docs/tasks/t001_alpha/progress.md"
    fm, body = parse_front_matter(task_path)
    fm["diff_anchor"] = "0" * 40
    write_front_matter(task_path, fm, body)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "--amend", "--no-edit")
    verdict, kind, detail = _verify(
        git_repo, "t001", identity["attempt"], identity["execution_id"]
    )

    assert verdict == "contract"
    assert "diff_anchor" in detail
    assert "执行分支必须恰好一个实现 commit" in detail


# --------------------------------------------------------------------------
# observe：reserve/bind exact identity、worktree ownership 与指纹去重
# --------------------------------------------------------------------------


def test_start_appends_ledger_event(git_repo):
    result = _task_cli(git_repo, "start", "t001")
    assert result.returncode == 0, result.stderr

    events = _read_ledger(git_repo)
    assert [event["event"] for event in events] == ["start"]
    start = events[0]
    assert start["tid"] == "t001"
    assert start["branch"] == "t001_alpha"
    assert start["worktree"] == f"../{git_repo.name}_t001"
    assert "attempt" not in start
    assert "execution_id" not in start
    assert "ts" in start




def test_integrate_skip_merge_also_appends_integrated(git_repo):
    identity, branch, branch_head = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", identity)
    # RT-003 后 skip-merge 须解析真实 merge({tid}) commit（不再无条件取当前 HEAD）；
    # 预 merge 用工具链规范 message，使新实现可识别该 merge commit。断言语义不变。
    manual = _git(git_repo, "merge", "--no-ff", "-m", "merge(t001): t001_alpha", branch)
    assert manual.returncode == 0, manual.stderr
    preintegrate_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    result = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity))

    assert result.returncode == 0, result.stderr
    assert "跳过 merge" in result.stdout
    assert _git(
        git_repo, "merge-base", "--is-ancestor", branch_head, "main", check=False
    ).returncode == 0
    integrated = [event for event in _read_ledger(git_repo) if event["event"] == "integrated"]
    assert len(integrated) == 1
    assert integrated[0]["merge_sha"] == preintegrate_head
    assert integrated[0]["attempt"] == identity["attempt"]
    assert integrated[0]["execution_id"] == identity["execution_id"]
    # merge_sha remains the actual merge commit; no derived index is generated.
    assert not (git_repo / "docs/tasks_index.json").exists()
    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == preintegrate_head


def test_integrate_skip_merge_rejects_foreign_head(git_repo):
    """崩溃窗口：分支已合入但 HEAD 被无关 commit 推进，skip-merge 拒绝以 HEAD 冒充 merge_sha（RT-003）。"""
    identity, branch, branch_head = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", identity)
    _git(git_repo, "merge", "--no-ff", "-m", "test: premerge task", branch)
    _git(git_repo, "commit", "--allow-empty", "-m", "test: unrelated advance after premerge")

    result = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity))

    assert result.returncode != 0
    assert "找不到对应的 merge(t001):" in result.stderr
    assert not [event for event in _read_ledger(git_repo) if event["event"] == "integrated"]


def test_integrate_continue_rejects_foreign_merge_head(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", identity)

    shared = git_repo / "shared.txt"
    shared.write_text("base\n", encoding="utf-8")
    _git(git_repo, "add", "shared.txt")
    _git(git_repo, "commit", "-m", "test: add shared")
    _git(git_repo, "checkout", "-b", "other")
    shared.write_text("from other\n", encoding="utf-8")
    _git(git_repo, "add", "shared.txt")
    _git(git_repo, "commit", "-m", "test: other edit")
    _git(git_repo, "checkout", "main")
    shared.write_text("from main\n", encoding="utf-8")
    _git(git_repo, "add", "shared.txt")
    _git(git_repo, "commit", "-m", "test: main edit")
    assert _git(git_repo, "merge", "other", check=False).returncode != 0
    shared.write_text("resolved\n", encoding="utf-8")
    _git(git_repo, "add", "shared.txt")

    result = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity), "--continue"
    )

    assert result.returncode != 0
    assert "MERGE_HEAD" in result.stderr
    assert "不符" in result.stderr
    assert not [event for event in _read_ledger(git_repo) if event["event"] == "integrated"]
    _git(git_repo, "merge", "--abort")


def test_verify_contract_when_multiple_branches(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    _git(git_repo, "branch", "t001_beta", "t001_alpha")

    verdict, kind, detail = _verify(
        git_repo, "t001", identity["attempt"], identity["execution_id"]
    )

    assert verdict == "contract"
    assert "多个分支" in detail


def test_verify_handoff_attempt_must_match_parallel_attempt(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")

    mismatch_attempt, kind, detail = _verify(
        git_repo, "t001", identity["attempt"] + 1, identity["execution_id"]
    )
    assert mismatch_attempt == "contract"
    assert kind == "handoff"
    assert "attempt=" in detail and "不符" in detail

    mismatch_execution, kind, detail = _verify(
        git_repo, "t001", identity["attempt"], "wrong"
    )
    assert mismatch_execution == "contract"
    assert kind == "handoff"
    assert "execution_id" in detail and "不符" in detail

    ready, kind, detail = _verify(
        git_repo, "t001", identity["attempt"], identity["execution_id"]
    )
    assert ready == "ready", detail
    assert kind == "ready"


def test_parallel_cleanup_and_integrate_require_terminal_attempt(git_repo):
    identity, branch, _ = _prepare_done(
        git_repo, "t001", "alpha", terminal=False
    )

    cleanup_running = _task_cli(
        git_repo, "cleanup-worktree", "t001", *_identity_args(identity)
    )
    assert cleanup_running.returncode != 0
    assert "须先 terminal" in cleanup_running.stderr

    integrate_running = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity)
    )
    assert integrate_running.returncode != 0
    assert "须先 terminal" in integrate_running.stderr

    wrong_identity = _task_cli(
        git_repo, "cleanup-worktree", "t001", "--attempt", "1",
        "--execution-id", "wrong",
    )
    assert wrong_identity.returncode != 0
    assert "不匹配 identity" in wrong_identity.stderr

    terminal = _task_cli(
        git_repo, "attempt", "terminal", "t001", *_identity_args(identity),
        "--status", "completed",
    )
    assert terminal.returncode == 0, terminal.stderr
    terminal_event = json.loads(terminal.stdout)
    assert terminal_event["event"] == "attempt_terminal"
    assert terminal_event["execution_id"] == identity["execution_id"]

    report = _task_cli(
        git_repo, "attempt", "report", "t001", *_identity_args(identity),
        "--status", "done", "--sha", _git(git_repo, "rev-parse", "HEAD").stdout.strip(),
    )
    assert report.returncode == 0, report.stderr

    worktree = _worktree(git_repo, "t001")
    (worktree / "dirty.txt").write_text("user data\n", encoding="utf-8")
    dirty = _task_cli(git_repo, "cleanup-worktree", "t001", *_identity_args(identity))
    assert dirty.returncode != 0
    assert "未提交改动" in dirty.stderr
    assert (worktree / "dirty.txt").read_text("utf-8") == "user data\n"
    (worktree / "dirty.txt").unlink()

    cleaned = _cleanup_cli(git_repo, "t001", identity)
    assert "worktree 已移除" in cleaned.stdout
    assert not worktree.exists()
    assert _git(git_repo, "branch", "--list", branch).stdout.strip() == branch

    prepared = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity))
    assert prepared.returncode == 0, prepared.stderr
    assert not [event for event in _read_ledger(git_repo) if event["event"] == "integrated"]
    integrated = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity), "--continue"
    )
    assert integrated.returncode == 0, integrated.stderr
    records = [event for event in _read_ledger(git_repo) if event["event"] == "integrated"]
    assert records[-1]["attempt"] == identity["attempt"]
    assert records[-1]["execution_id"] == identity["execution_id"]


# test_escalated_completed_allows_manual_integrate 已删除：
# escalate 命令已退役（CLI 仅 reserve/terminal/report，见 repo_task/cli.py），
# 原测试调用不存在的 `task.py attempt escalate` 且未断言其 returncode，属假绿；
# terminal=completed 后 cleanup/integrate 放行的场景已由 _prepare_done 系列测试覆盖。


# --------------------------------------------------------------------------
# 新增计划覆盖：chain aggregate/transaction 与 legacy CLI 显式失败
# --------------------------------------------------------------------------






def test_integrate_chain_preflight_failure_has_zero_merge_and_zero_integrated(git_repo):
    first, first_branch, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", first)
    second, _, _ = _prepare_done(
        git_repo, "t002", "beta", base=first_branch,
    )
    worktree = _worktree(git_repo, "t002")
    path = _handoff_path(git_repo, "t002", "beta")
    payload = json.loads(path.read_text("utf-8"))
    payload["execution_id"] = "wrong"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "--amend", "--no-edit")
    _git(git_repo, "worktree", "remove", str(worktree))
    before = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    result = _task_cli(git_repo, "integrate-chain", "t002")
    assert result.returncode != 0
    assert "execution_id" in result.stderr
    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == before
    assert not [item for item in _read_ledger(git_repo) if item["event"] == "integrated"]
    assert _git(git_repo, "branch", "--list", first_branch).stdout.strip() == first_branch
    assert second["execution_id"] != "wrong"




def test_legacy_cli_paths_fail_explicitly(git_repo):
    old_record = _task_cli(
        git_repo, "ledger", "record", "--event", "dispatch", "--tid", "t001"
    )
    assert old_record.returncode != 0
    assert "invalid choice" in old_record.stderr

    old_chain = _task_cli(
        git_repo, "integrate", "t001", "--attempt", "1",
        "--execution-id", "legacy", "--chain",
    )
    assert old_chain.returncode != 0
    assert "--chain" in old_chain.stderr


# --------------------------------------------------------------------------
# review 要求的回归：F03 fail-closed / RT-002 untracked 指纹 / RT-005 index 归属
# / RT-004 锁装饰 / RT-009 并发取号
# --------------------------------------------------------------------------


def test_porcelain_entries_fails_closed_on_git_error(git_repo, monkeypatch):
    """F03：git status 失败必须抛 TaskDataError，不得返回空集合被误判「干净」。"""
    from repo_task import git_ops
    from repo_task.context import TaskDataError

    class Boom:
        returncode = 1
        stdout = ""
        stderr = "fatal: test error"

    monkeypatch.setattr(git_ops, "_git", lambda *a, **k: Boom())
    repo_ctx = ctx.RepoContext.for_root(git_repo)
    with pytest.raises(TaskDataError, match="git status"):
        git_ops.porcelain_entries(repo=repo_ctx)
    with pytest.raises(TaskDataError, match="git status"):
        git_ops.tracked_dirty_entries(repo=repo_ctx)


def test_review_scope_fingerprint_includes_untracked(git_repo, monkeypatch):
    """RT-002：未跟踪文件（未 git add -N）加入后 scope 指纹必须变，防静默绕过 review 门禁。"""
    from repo_task import review_status as crs

    monkeypatch.setattr(crs, "REPO_ROOT", git_repo)
    anchor = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    task_dir = git_repo / "docs" / "tasks" / "t001_alpha"
    before = crs.current_scope_fingerprint(task_dir, anchor)
    assert before is not None
    (git_repo / "src").mkdir(parents=True, exist_ok=True)
    (git_repo / "src" / "new_impl.py").write_text("x = 1\n", encoding="utf-8")
    after = crs.current_scope_fingerprint(task_dir, anchor)
    assert after is not None
    assert after != before




def test_cmd_integrate_uses_chain_lock(git_repo, monkeypatch):
    """RT-004：调用 cmd_integrate 时确实获取 chain 锁（可观察行为）。"""
    from argparse import Namespace
    from repo_task import integration, locks

    calls = []
    real_file_lock = locks.file_lock

    def spy_file_lock(path):
        calls.append(path)
        return real_file_lock(path)

    monkeypatch.setattr(integration, "file_lock", spy_file_lock)
    monkeypatch.setattr(
        integration, "_integration_lock_path",
        lambda repo: git_repo / ".git" / "integrate.lock",
    )
    args = Namespace(
        tid="t999", attempt=1, execution_id="x",
        keep_branch=False, continue_merge=False,
        repo=ctx.RepoContext.for_root(git_repo),
    )
    with pytest.raises(SystemExit):
        integration.cmd_integrate(args)
    assert calls and calls[0].name == "integrate.lock"


def _concurrent_add(repo, slug):
    """模块级（可 pickle）：并发跑 task add，供 ProcessPoolExecutor。"""
    result = _task_cli(repo, "add", "--title", f"task {slug}", "--slug", slug)
    return result.returncode, result.stdout


def test_concurrent_add_allocates_unique_tids(git_repo):
    """RT-009：并发 task add 取号互斥，不撞号。"""
    import re
    from concurrent.futures import ProcessPoolExecutor
    from functools import partial

    runner = partial(_concurrent_add, git_repo)
    with ProcessPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(runner, ["conc_a", "conc_b"]))
    assert all(rc == 0 for rc, _ in results), results
    tids = []
    for _, stdout in results:
        match = re.search(r"added (t\d+) ", stdout)
        assert match, stdout
        tids.append(match.group(1))
    assert len(set(tids)) == 2, f"并发 add 撞号：{tids}"


def test_track_worktree_reverse_lookup_and_dedup(tmp_path):
    """验证 track_worktree 逆序读取 ledger 与尾部查重（Issue #61）。"""
    import importlib.util
    import json
    scripts_dir = Path(__file__).resolve().parents[1] / "repo/.repo_template/scripts"
    spec = importlib.util.spec_from_file_location("track_worktree", str(scripts_dir / "track_worktree.py"))
    tw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tw)

    project = tmp_path / "proj"
    runtime = project / "docs" / "runtime"
    runtime.mkdir(parents=True)
    ledger = runtime / "dispatch_ledger.jsonl"

    # 1. 填充 300 条历史无关事件
    lines = [json.dumps({"tid": f"t{i:04d}", "event": "start", "worktree": f"wt_{i}"}) for i in range(1, 300)]
    # 加入目标 t999 的 start 事件
    lines.append(json.dumps({"tid": "t999", "event": "start", "worktree": "worktrees/t999_wt"}))
    # 加入 300 条后续无关事件
    lines.extend(json.dumps({"tid": f"t{i:04d}", "event": "report"}) for i in range(300, 600))
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 2. 逆序解析应当正确找到 t999 的 worktree
    resolved = tw.resolve_worktree(str(project), "t999")
    assert resolved == str((project / "worktrees/t999_wt").resolve())

    # 3. 追加 t999 的 terminal 事件（report），解析立即闭合为 ""
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(json.dumps({"tid": "t999", "event": "report", "status": "done"}) + "\n")
    assert tw.resolve_worktree(str(project), "t999") == ""

    # 4. 尾部查重
    statusline = project / ".scratch" / "statusline_workdir.jsonl"
    record = {"tid": "t999", "agent": "test", "worktree": "some/path"}
    tw.append_record(str(statusline), record)
    assert statusline.read_text(encoding="utf-8").count("\n") == 1
    # 相同记录再次追加应跳过
    tw.append_record(str(statusline), record)
    assert statusline.read_text(encoding="utf-8").count("\n") == 1
    # 不同记录可以追加
    tw.append_record(str(statusline), {**record, "tid": "t1000"})
    assert statusline.read_text(encoding="utf-8").count("\n") == 2


def test_track_worktree_resolves_from_start_through_execution_claim(tmp_path):
    """start → reserve → claim 后映射仍有效；终态事件才关闭窗口（Issue #74）。"""
    import importlib.util
    import json
    scripts_dir = Path(__file__).resolve().parents[1] / "repo/.repo_template/scripts"
    spec = importlib.util.spec_from_file_location("track_worktree", str(scripts_dir / "track_worktree.py"))
    tw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tw)

    project = tmp_path / "proj"
    runtime = project / "docs" / "runtime"
    runtime.mkdir(parents=True)
    ledger = runtime / "dispatch_ledger.jsonl"
    ledger.write_text(
        "\n".join([
            json.dumps({"tid": "t001", "event": "start", "worktree": "worktrees/t001_alpha"}),
            json.dumps({"tid": "t001", "event": "attempt_reserved", "attempt": 1, "state": "running"}),
            json.dumps({"tid": "t001", "event": "execution_claimed", "attempt": 1, "owner_id": "o"}),
        ]) + "\n",
        encoding="utf-8",
    )
    expected = str((project / "worktrees/t001_alpha").resolve())
    assert tw.resolve_worktree(str(project), "t001") == expected

    with ledger.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"tid": "t001", "event": "attempt_terminal", "attempt": 1}) + "\n")
    assert tw.resolve_worktree(str(project), "t001") == ""


def test_track_worktree_binds_session_explicitly_without_command_sniffing(tmp_path):
    """A16：会话绑定由显式身份建立，不解析终端命令文本。"""
    import importlib.util
    import json
    scripts_dir = Path(__file__).resolve().parents[1] / "repo/.repo_template/scripts"
    spec = importlib.util.spec_from_file_location("track_worktree", str(scripts_dir / "track_worktree.py"))
    tw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tw)

    project = tmp_path / "project"
    runtime = project / "docs/runtime"
    runtime.mkdir(parents=True)
    ledger = runtime / "dispatch_ledger.jsonl"
    ledger.write_text(
        "".join(json.dumps(event) + "\n" for event in [
            {"tid": "t001", "event": "start", "worktree": "../project_t001"},
            {"tid": "t001", "event": "attempt_reserved", "attempt": 1, "state": "running"},
            {"tid": "t001", "event": "execution_claimed", "attempt": 1, "owner_id": "owner-a"},
        ]),
        encoding="utf-8",
    )

    assert tw.bind(str(project), "owner-a", "t001", 1, "exec-1") is True
    assert tw.current_worktree(str(project), "owner-a") == str(tmp_path / "project_t001")
    record = json.loads(
        (project / ".scratch/statusline_workdir.jsonl").read_text(encoding="utf-8")
    )
    assert record["session"] == "owner-a"
    assert record["attempt"] == 1

    with ledger.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"tid": "t001", "event": "attempt_terminal"}) + "\n")
    assert tw.current_worktree(str(project), "owner-a") == ""
    assert tw.bind(str(project), "owner-b", "t001") is False
    assert tw.current_worktree(str(project), "owner-b") == ""


def test_run_begin_binds_and_run_close_done_unbinds_statusline(git_repo):
    """A16：run-begin 显式写绑定；run-close done 解除绑定，渲染端解析为空。"""
    from test_run_commands import OWNER
    from task_flow_support import finish_cli, prepare_finish

    begun = _task_cli(git_repo, "run-begin", "t001", "--owner-id", OWNER)
    assert begun.returncode == 0, begun.stderr
    mapping = git_repo / ".scratch/statusline_workdir.jsonl"
    record = json.loads(mapping.read_text(encoding="utf-8").splitlines()[-1])
    assert record["session"] == OWNER and record["tid"] == "t001"
    assert record["worktree"] == str(_worktree(git_repo, "t001").resolve())

    worktree = _worktree(git_repo, "t001")
    message = prepare_finish(worktree, "t001", "alpha")
    assert finish_cli(worktree, "t001", OWNER, message).returncode == 0
    closed = _task_cli(git_repo, "run-close", "t001", "--owner-id", OWNER, "--result", "done")
    assert closed.returncode == 0, closed.stderr
    last = json.loads(mapping.read_text(encoding="utf-8").splitlines()[-1])
    assert last["session"] == OWNER and last["worktree"] == ""


def test_ledger_read_filtered_by_tid_and_fast_allocate(tmp_path):
    """验证大型账本下按 tid 快速预过滤读取与单次打开追加优化（Issue #57）。"""
    from repo_task import context as ctx
    from repo_task.ledger import ledger_allocate_attempt, ledger_append, ledger_read

    runtime = tmp_path / "docs" / "runtime"
    runtime.mkdir(parents=True)
    ledger_path = runtime / "dispatch_ledger.jsonl"
    repo = ctx.RepoContext.for_root(tmp_path)

    # 1. 模拟写入 500 条其他任务记录
    lines = [json.dumps({"tid": f"t{i:04d}", "attempt": 1, "event": "start"}) for i in range(1, 501)]
    # 加入 t001 的 attempt 1 记录
    lines.append(json.dumps({"tid": "t001", "attempt": 1, "event": "start"}))
    ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 2. 仅读 t001 的记录
    t001_events = ledger_read(tid="t001", repo=repo)
    assert len(t001_events) == 1
    assert t001_events[0]["tid"] == "t001"

    # 3. 为 t001 分配下一个 attempt，应为 2
    allocated = ledger_allocate_attempt(
        "t001", lambda att, ev: {"event": "attempt_reserved", "state": "running"}, repo=repo
    )
    assert allocated["attempt"] == 2
    assert allocated["tid"] == "t001"

    # 4. 再次按 tid 读，包含两条
    t001_events2 = ledger_read(tid="t001", repo=repo)
    assert len(t001_events2) == 2


def test_ledger_unreadable_fails_closed_instead_of_reusing_attempt(tmp_path, monkeypatch):
    """账本存在但不可读时必须拒绝，不能按空账本重新分配 attempt（Issue #70）。"""
    from repo_task import context as ctx
    from repo_task.ledger import ledger_allocate_attempt, ledger_read

    repo_root = tmp_path / "repo"
    runtime = repo_root / "docs" / "runtime"
    runtime.mkdir(parents=True)
    ledger_path = runtime / "dispatch_ledger.jsonl"
    ledger_path.write_text(
        json.dumps({"tid": "t001", "attempt": 1, "event": "start"}) + "\n",
        encoding="utf-8",
    )
    repo = ctx.RepoContext.for_root(repo_root)

    real_open = Path.open

    def denied(self, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if self == ledger_path and "r" in mode and "+" not in mode:
            raise PermissionError(13, "Permission denied", str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(ctx.TaskDataError, match="无法读取"):
        ledger_read(repo=repo)
    with pytest.raises(ctx.TaskDataError, match="无法读取"):
        ledger_allocate_attempt(
            "t001", lambda att, ev: {"event": "attempt_reserved", "state": "running"}, repo=repo
        )
    with real_open(ledger_path, "r", encoding="utf-8") as stream:
        assert stream.read().count("\n") == 1


def test_chain_locked_uses_shared_file_lock(tmp_path, monkeypatch):
    """验证 integration._chain_locked 统一使用 locks.file_lock（Issue #56）。"""
    from repo_task import integration, locks

    lock_calls = []

    real_file_lock = locks.file_lock
    def spy_file_lock(path):
        lock_calls.append(path)
        return real_file_lock(path)

    monkeypatch.setattr(integration, "file_lock", spy_file_lock)
    monkeypatch.setattr(integration, "_integration_lock_path", lambda repo=None: tmp_path / "integrate.lock")

    @integration._chain_locked
    def my_action(val, repo=None):
        return val * 2

    assert my_action(21, repo=ctx.RepoContext.for_root(tmp_path)) == 42
    assert len(lock_calls) == 1
    assert lock_calls[0] == tmp_path / "integrate.lock"
