"""task.py goal / goal-check 的 real-git 测试。"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest


SCRIPTS_DIR = FACTORY_ROOT / "repo/.repo_template/scripts"
TASK_TEMPLATE_DIR = SCRIPTS_DIR.parent / "docs" / "task_template"
sys.path.insert(0, str(SCRIPTS_DIR))

from review_support import ensure_review_evidence, configure_project_verification, valid_spec as _valid_spec
from task_flow_support import mark_done

from repo_task import attempts
from repo_task import context as ctx
from repo_task import control
from repo_task import integration
from repo_task.documents import parse_front_matter, write_front_matter
from repo_task.monitoring import review_scope_fingerprint


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check, timeout=120,
    )


from git_repo_support import fast_copy_repo
from git_repo_support import state_file


@pytest.fixture
def git_repo(tmp_path, workflow_two_tasks_seed):
    repo = tmp_path / "repo"
    return fast_copy_repo(workflow_two_tasks_seed, repo)


def _cli(repo, *args, stdin=""):
    return subprocess.run(
        [sys.executable, str(repo / ".repo_template" / "scripts" / "task.py"), *args],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
        input=stdin, timeout=120,
    )


def _snapshot(repo):
    path = state_file(repo, "goal_queue.json")
    return json.loads(path.read_text("utf-8"))


def _relate(repo, tid, **values):
    path = next((repo / "docs/tasks").glob(f"{tid}_*/progress.md"))
    frontmatter, body = parse_front_matter(path)
    frontmatter.update(values)
    write_front_matter(path, frontmatter, body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"graph: {tid}")


def _worktree(repo, tid):
    return repo.parent / f"{repo.name}_{tid}"


def _reserve(repo, tid):
    result = _cli(repo, "attempt", "reserve", tid)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _identity_args(identity):
    return ["--attempt", str(identity["attempt"]), "--execution-id", identity["execution_id"]]


def _prepare_done(repo, tid, slug):
    """走完整执行流至 terminal completed + report done（不 cleanup）。"""
    context = ctx.RepoContext.for_root(repo)
    integration.cmd_start(argparse.Namespace(tid=tid, base=None, repo=context))
    identity = attempts.reserve_attempt(tid, repo=context)
    worktree = _worktree(repo, tid)
    task_file = worktree / "docs" / "tasks" / f"{tid}_{slug}" / "task.md"
    if task_file.is_file():
        content = task_file.read_text(encoding="utf-8")
        task_file.write_text(content.replace("- [ ] AC-001", "- [x] AC-001"), encoding="utf-8")
    mark_done(worktree, tid)
    branch = f"{tid}_{slug}"
    base_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    task_dir = worktree / "docs" / "tasks" / branch
    ensure_review_evidence(worktree, task_dir, base_sha)
    fm, _ = parse_front_matter(task_dir / "progress.md")
    spec_source_sha = fm.get("spec_source_sha") or base_sha
    payload = {
        "tid": tid,
        "attempt": identity["attempt"],
        "execution_id": identity["execution_id"],
        "status": "done",
        "branch": branch,
        "base_sha": base_sha,
        "spec_source_sha": spec_source_sha,
        "tests": "pytest -q",
        "blackbox": "pass",
        "review": "pass",
        "review_scope": review_scope_fingerprint(
            base_sha, f"docs/tasks/{branch}", repo_root=worktree
        ),
        "pending": [],
        "findings": [],
    }
    (task_dir / "handoff.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", f"feat({tid}): complete {slug}")
    head = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    attempts.terminal_attempt(
        tid, identity["attempt"], identity["execution_id"], "completed", repo=context
    )
    control.require_report_done_ready(
        tid, identity["attempt"], identity["execution_id"], repo=context
    )
    attempts.report_attempt(
        tid, identity["attempt"], identity["execution_id"], "done", sha=head, repo=context
    )
    return identity


def _cleanup(repo, tid, identity):
    context = ctx.RepoContext.for_root(repo)
    return integration._cleanup_worktree(
        tid, identity["attempt"], identity["execution_id"], repo=context
    )


def test_goal_freezes_default_queue_and_prints_paste_line(git_repo):
    result = _cli(git_repo, "goal")
    assert result.returncode == 0, result.stderr
    assert _snapshot(git_repo)["queue"] == ["t001", "t002"]
    assert "/goal 按 task-run skill 链式串行执行冻结队列 [t001, t002]" in result.stdout
    assert "goal-check" in result.stdout


def test_goal_explicit_tids_preserve_order_and_validate(git_repo):
    result = _cli(git_repo, "goal", "t002", "t001")
    assert result.returncode == 0, result.stderr
    assert _snapshot(git_repo)["queue"] == ["t002", "t001"]

    assert _cli(git_repo, "goal", "t001", "t001").returncode != 0
    assert _cli(git_repo, "goal", "t009").returncode != 0
    assert _cli(git_repo, "goal", "bogus").returncode != 0


def test_goal_explicit_tids_reject_invalid_topology(git_repo):
    _relate(git_repo, "t002", depends_on="t001")

    result = _cli(git_repo, "goal", "t002", "t001")
    assert result.returncode == 1
    assert "invalid_queue" in result.stderr

    result = _cli(git_repo, "goal", "t001", "t002")
    assert result.returncode == 0, result.stderr
    assert _snapshot(git_repo)["queue"] == ["t001", "t002"]


def test_goal_snapshot_freezes_queue_edges(git_repo):
    _relate(git_repo, "t002", depends_on="t001")

    assert _cli(git_repo, "goal", "t001", "t002").returncode == 0
    assert _snapshot(git_repo)["edges"] == [
        {"type": "dep", "from": "t001", "to": "t002"},
    ]


def test_goal_check_rejects_legacy_snapshot_without_edges(git_repo):
    assert _cli(git_repo, "goal").returncode == 0
    path = state_file(git_repo, "goal_queue.json")
    legacy = _snapshot(git_repo)
    legacy.pop("edges")
    path.write_text(json.dumps(legacy), encoding="utf-8")

    result = _cli(git_repo, "goal-check")
    assert result.returncode == 1
    assert "edges" in result.stderr
    assert "--reset" in result.stderr

    shown = _cli(git_repo, "goal")
    assert shown.returncode == 1
    assert "--reset" in shown.stderr

    reset = _cli(git_repo, "goal", "--reset")
    assert reset.returncode == 0, reset.stderr
    assert "edges" in _snapshot(git_repo)


def test_goal_check_missing_snapshot_errors(git_repo):
    result = _cli(git_repo, "goal-check")
    assert result.returncode == 1
    assert "task.py goal" in result.stderr or "task.py goal" in result.stdout


def test_goal_check_incomplete_then_complete(git_repo):
    assert _cli(git_repo, "goal").returncode == 0
    result = _cli(git_repo, "goal-check")
    assert result.returncode == 2
    assert "GOAL_QUEUE_INCOMPLETE: 0/2 closed" in result.stdout

    identity = _prepare_done(git_repo, "t001", "alpha")
    result = _cli(git_repo, "goal-check")
    assert result.returncode == 2
    assert "t001 cleanup_pending" in result.stdout

    _cleanup(git_repo, "t001", identity)
    result = _cli(git_repo, "goal-check")
    assert result.returncode == 2
    assert "t001 closed" in result.stdout
    assert "GOAL_QUEUE_INCOMPLETE: 1/2 closed" in result.stdout

    identity2 = _prepare_done(git_repo, "t002", "beta")
    _cleanup(git_repo, "t002", identity2)
    result = _cli(git_repo, "goal-check")
    assert result.returncode == 0
    assert "GOAL_QUEUE_COMPLETE" in result.stdout


def _stop_t001(git_repo, status, *report_args):
    assert _cli(git_repo, "goal").returncode == 0
    assert _cli(git_repo, "start", "t001").returncode == 0
    identity = _reserve(git_repo, "t001")
    terminal_status = "completed" if status == "blocked" else "failed"
    terminal = _cli(
        git_repo, "attempt", "terminal", "t001", *_identity_args(identity),
        "--status", terminal_status,
    )
    assert terminal.returncode == 0, terminal.stderr
    report = _cli(
        git_repo, "attempt", "report", "t001", *_identity_args(identity),
        "--status", status, *report_args,
    )
    assert report.returncode == 0, report.stderr


def test_goal_check_blocked_with_independent_member_pending_stays_incomplete(git_repo):
    _stop_t001(git_repo, "blocked", "--reason", "blackbox 满轮")
    result = _cli(git_repo, "goal-check")
    assert result.returncode == 2
    assert "stopped_at=t001 state=blocked skipped=-" in result.stdout
    assert "GOAL_QUEUE_INCOMPLETE: 0/2 closed" in result.stdout
    assert "GOAL_QUEUE_STOPPED" not in result.stdout


def test_goal_check_stopped_on_blocked_when_no_member_can_continue(git_repo):
    _stop_t001(git_repo, "blocked", "--reason", "blackbox 满轮")
    identity2 = _prepare_done(git_repo, "t002", "beta")
    _cleanup(git_repo, "t002", identity2)
    result = _cli(git_repo, "goal-check")
    assert result.returncode == 3
    assert "GOAL_QUEUE_STOPPED: t001=blocked" in result.stdout


def test_goal_check_failed_follows_same_stop_rules(git_repo):
    _stop_t001(git_repo, "failed", "--class", "infra", "--reason", "环境不可用")
    pending = _cli(git_repo, "goal-check")
    assert pending.returncode == 2
    assert "stopped_at=t001 state=failed skipped=-" in pending.stdout
    identity2 = _prepare_done(git_repo, "t002", "beta")
    _cleanup(git_repo, "t002", identity2)
    result = _cli(git_repo, "goal-check")
    assert result.returncode == 3
    assert "GOAL_QUEUE_STOPPED: t001=failed" in result.stdout


def test_goal_check_running_counts_incomplete(git_repo):
    assert _cli(git_repo, "goal").returncode == 0
    assert _cli(git_repo, "start", "t001").returncode == 0
    _reserve(git_repo, "t001")
    result = _cli(git_repo, "goal-check")
    assert result.returncode == 2
    assert "t001 running" in result.stdout


def test_goal_check_dropped_member_stops_instead_of_false_complete(git_repo):
    assert _cli(git_repo, "goal").returncode == 0
    dropped = _cli(git_repo, "drop", "t001", "--reason", "不再需要")
    assert dropped.returncode == 0, dropped.stderr
    identity2 = _prepare_done(git_repo, "t002", "beta")
    _cleanup(git_repo, "t002", identity2)
    result = _cli(git_repo, "goal-check")
    assert result.returncode == 3
    assert "GOAL_QUEUE_STOPPED: t001=dropped" in result.stdout
    assert "GOAL_QUEUE_COMPLETE" not in result.stdout


def test_goal_empty_queue_view_keeps_snapshot_reset_clears(git_repo):
    assert _cli(git_repo, "goal").returncode == 0
    snapshot = state_file(git_repo, "goal_queue.json")
    assert snapshot.is_file()
    before = _snapshot(git_repo)
    for tid in ("t001", "t002"):
        dropped = _cli(git_repo, "drop", tid, "--reason", "不再需要")
        assert dropped.returncode == 0, dropped.stderr
    viewed = _cli(git_repo, "goal")
    assert viewed.returncode == 0, viewed.stderr
    assert "当前冻结队列：t001, t002" in viewed.stdout
    # L226：提示统一为原位 done/dropped/integrated 语义，断言同步改为「已闭环」，
    # 仍校验具体提示与两个 stale tid，强度不减。
    assert "队列含已闭环或不存在成员（t001, t002）" in viewed.stdout
    assert "/goal 行不可直接执行" in viewed.stdout
    assert snapshot.is_file()
    assert _snapshot(git_repo) == before
    # --reset 直接覆盖免确认：队列为空时清除快照
    result = _cli(git_repo, "goal", "--reset")
    assert result.returncode == 0, result.stderr
    assert "队列为空" in result.stdout
    assert not snapshot.exists()
    # 旧快照已被清除：goal-check 报缺快照而不是粘过期队列
    assert _cli(git_repo, "goal-check").returncode == 1


def test_goal_no_args_views_existing_snapshot_without_rewrite(git_repo):
    first = _cli(git_repo, "goal")
    assert first.returncode == 0, first.stderr
    before = _snapshot(git_repo)
    result = _cli(git_repo, "goal")
    assert result.returncode == 0, result.stderr
    assert "当前冻结队列：t001, t002" in result.stdout
    assert f"冻结时间：{before['created_at']}" in result.stdout
    assert "只读展示" in result.stdout
    assert _snapshot(git_repo) == before


def test_goal_accidental_no_args_keeps_custom_order(git_repo):
    first = _cli(git_repo, "goal", "t002", "t001")
    assert first.returncode == 0, first.stderr
    before = _snapshot(git_repo)
    assert before["queue"] == ["t002", "t001"]
    assert _cli(git_repo, "start", "t001").returncode == 0
    _reserve(git_repo, "t001")
    result = _cli(git_repo, "goal")
    assert result.returncode == 0, result.stderr
    assert "当前冻结队列：t002, t001" in result.stdout
    assert _snapshot(git_repo) == before


def test_goal_reset_skips_confirm_explicit_tids_require_confirm(git_repo):
    assert _cli(git_repo, "goal", "t002", "t001").returncode == 0

    # --reset 直接覆盖免确认
    reset = _cli(git_repo, "goal", "--reset")
    assert reset.returncode == 0, reset.stderr
    assert _snapshot(git_repo)["queue"] == ["t001", "t002"]
    assert "队列已重新冻结：t001, t002" in reset.stdout

    # 显式 tid 顺序不一致仍须确认
    refused = _cli(git_repo, "goal", "t002", "t001")
    assert refused.returncode != 0
    assert "确认覆盖请加 --yes" in refused.stderr
    assert "已冻结" in refused.stderr
    assert _snapshot(git_repo)["queue"] == ["t001", "t002"]

    custom = _cli(git_repo, "goal", "t002", "t001", "--yes")
    assert custom.returncode == 0, custom.stderr
    assert _snapshot(git_repo)["queue"] == ["t002", "t001"]

    same = _cli(git_repo, "goal", "t002", "t001")
    assert same.returncode == 0, same.stderr
    assert _snapshot(git_repo)["queue"] == ["t002", "t001"]


def test_goal_reset_rejects_explicit_tids(git_repo):
    result = _cli(git_repo, "goal", "--reset", "t001")
    assert result.returncode != 0
    assert "互斥" in result.stderr or "互斥" in result.stdout
    assert not (state_file(git_repo, "goal_queue.json")).exists()


def test_goal_no_args_does_not_reset_corrupt_snapshot(git_repo):
    assert _cli(git_repo, "goal").returncode == 0
    path = state_file(git_repo, "goal_queue.json")
    path.write_text("{not json", encoding="utf-8")
    result = _cli(git_repo, "goal")
    assert result.returncode != 0
    assert "损坏" in result.stderr or "损坏" in result.stdout
    assert path.read_text(encoding="utf-8") == "{not json"
    rebuilt = _cli(git_repo, "goal", "--reset", "--yes")
    assert rebuilt.returncode == 0, rebuilt.stderr
    assert _snapshot(git_repo)["queue"] == ["t001", "t002"]


def test_goal_no_args_reports_binary_snapshot_as_corrupt(git_repo):
    assert _cli(git_repo, "goal").returncode == 0
    path = state_file(git_repo, "goal_queue.json")
    payload = b"\xff\xfe{not utf8"
    path.write_bytes(payload)
    result = _cli(git_repo, "goal")
    assert result.returncode != 0
    text = result.stderr + result.stdout
    assert "损坏" in text
    assert "Traceback" not in text
    assert path.read_bytes() == payload
