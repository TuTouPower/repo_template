"""task.py goal / goal-check 的 real-git 测试。"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
TASK_TEMPLATE_DIR = SCRIPTS_DIR.parent / "docs" / "task_template"
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task.documents import parse_front_matter, write_front_matter


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check,
    )


def _valid_spec():
    text = (TASK_TEMPLATE_DIR / "spec.md").read_text(encoding="utf-8")
    replacements = {
        "{为什么需要此变更。}": "测试背景。",
        "{本 task 包含什么。}": "测试范围。",
        "{明确不做什么。}": "无。",
        "{可独立验证的行为结果。}": "可验证行为。",
        "- AC-001：{不可测原因与替代验证方式}": "- 全部 AC 可自动测试",
        "- {分支或场景}：{不测原因}": "- 无",
        "- {内容}": "- 按项目默认",
        "- {契约}：{分类标记}，{待验证方式}": "- 外部行为：已核实",
        "- 风险：{可能失败的地方}": "- 风险：无",
        "- 回退：{失败后如何恢复}": "- 回退：无",
        "- {前置依赖、平台、安全或兼容性约束；无则写「无」。}": "- 无",
        "- `{文件路径}`：{具体条目；无则写「无」}": "- 无",
        "- 来源：{pNNN / finding_id / 原 tid}（核实日期与结论；无外部来源写「无」）": "- 来源：无",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    tasks = repo / "docs" / "tasks"
    archive = repo / "docs" / "archive" / "tasks"
    template = tasks / "task_template"
    tasks.mkdir(parents=True)
    archive.mkdir(parents=True)
    scripts = repo / ".repo_template" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPTS_DIR / "task.py", scripts / "task.py")
    shutil.copytree(
        SCRIPTS_DIR / "repo_task", scripts / "repo_task",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(TASK_TEMPLATE_DIR, repo / ".repo_template" / "docs" / "task_template")
    shutil.copytree(TASK_TEMPLATE_DIR, template)
    _, body = parse_front_matter(TASK_TEMPLATE_DIR / "task.md")
    for tid, slug in (("t001", "alpha"), ("t002", "beta")):
        task_dir = tasks / f"{tid}_{slug}"
        shutil.copytree(TASK_TEMPLATE_DIR, task_dir)
        write_front_matter(task_dir / "task.md", {
            "tid": tid, "slug": slug, "title": slug, "status": "backlog",
            "branch": "", "worktree": "", "review_level": "full",
            "diff_anchor": "", "note": "",
        }, body)
        (task_dir / "spec.md").write_text(_valid_spec(), encoding="utf-8")
    (repo / ".gitignore").write_text(
        "__pycache__/\n*.py[cod]\ndocs/runtime/\n", encoding="utf-8"
    )
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _cli(repo, *args, stdin=""):
    return subprocess.run(
        [sys.executable, str(repo / ".repo_template" / "scripts" / "task.py"), *args],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
        input=stdin,
    )


def _snapshot(repo):
    path = repo / "docs" / "runtime" / "goal_queue.json"
    return json.loads(path.read_text("utf-8"))


def _worktree(repo, tid):
    return repo.parent / f"{repo.name}_{tid}"


def _reserve(repo, tid):
    result = _cli(repo, "attempt", "reserve", tid, "--executor", "inline")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _identity_args(identity):
    return ["--attempt", str(identity["attempt"]), "--execution-id", identity["execution_id"]]


def _prepare_done(repo, tid, slug):
    """走完整执行流至 terminal completed + report done（不 cleanup）。"""
    started = _cli(repo, "start", tid)
    assert started.returncode == 0, started.stderr
    identity = _reserve(repo, tid)
    worktree = _worktree(repo, tid)
    finished = _cli(worktree, "finish", tid)
    assert finished.returncode == 0, finished.stderr
    branch = f"{tid}_{slug}"
    base_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    archive = worktree / "docs" / "archive" / "tasks" / branch
    payload = {
        "tid": tid,
        "attempt": identity["attempt"],
        "execution_id": identity["execution_id"],
        "status": "done",
        "branch": branch,
        "base_sha": base_sha,
        "tests": "pytest -q",
        "blackbox": "pass",
        "review": "pass",
        "ac_evidence": {"AC-001": ["tests/test_x.py::test_y 通过"]},
        "pending": [],
        "findings": [],
    }
    (archive / "handoff.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", f"feat({tid}): complete {slug}")
    head = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    terminal = _cli(
        repo, "attempt", "terminal", tid, *_identity_args(identity), "--status", "completed",
    )
    assert terminal.returncode == 0, terminal.stderr
    report = _cli(
        repo, "attempt", "report", tid, *_identity_args(identity),
        "--status", "done", "--sha", head,
    )
    assert report.returncode == 0, report.stderr
    return identity


def _cleanup(repo, tid, identity):
    result = _cli(repo, "cleanup-worktree", tid, *_identity_args(identity))
    assert result.returncode == 0, result.stderr


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


def test_goal_check_stopped_on_blocked(git_repo):
    assert _cli(git_repo, "goal").returncode == 0
    assert _cli(git_repo, "start", "t001").returncode == 0
    identity = _reserve(git_repo, "t001")
    terminal = _cli(
        git_repo, "attempt", "terminal", "t001", *_identity_args(identity),
        "--status", "completed",
    )
    assert terminal.returncode == 0, terminal.stderr
    report = _cli(
        git_repo, "attempt", "report", "t001", *_identity_args(identity),
        "--status", "blocked", "--reason", "blackbox 满轮",
    )
    assert report.returncode == 0, report.stderr
    result = _cli(git_repo, "goal-check")
    assert result.returncode == 3
    assert "GOAL_QUEUE_STOPPED: t001=blocked" in result.stdout


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


def test_goal_check_stopped_on_failed(git_repo):
    assert _cli(git_repo, "goal").returncode == 0
    assert _cli(git_repo, "start", "t001").returncode == 0
    identity = _reserve(git_repo, "t001")
    terminal = _cli(
        git_repo, "attempt", "terminal", "t001", *_identity_args(identity),
        "--status", "failed",
    )
    assert terminal.returncode == 0, terminal.stderr
    report = _cli(
        git_repo, "attempt", "report", "t001", *_identity_args(identity),
        "--status", "failed", "--class", "infra", "--reason", "环境不可用",
    )
    assert report.returncode == 0, report.stderr
    result = _cli(git_repo, "goal-check")
    assert result.returncode == 3
    assert "GOAL_QUEUE_STOPPED: t001=failed" in result.stdout


def test_goal_empty_queue_view_keeps_snapshot_reset_clears(git_repo):
    assert _cli(git_repo, "goal").returncode == 0
    snapshot = git_repo / "docs" / "runtime" / "goal_queue.json"
    assert snapshot.is_file()
    before = _snapshot(git_repo)
    for tid in ("t001", "t002"):
        dropped = _cli(git_repo, "drop", tid, "--reason", "不再需要")
        assert dropped.returncode == 0, dropped.stderr
    viewed = _cli(git_repo, "goal")
    assert viewed.returncode == 0, viewed.stderr
    assert "当前冻结队列：t001, t002" in viewed.stdout
    assert "队列含已归档或不存在成员（t001, t002）" in viewed.stdout
    assert "/goal 行不可直接执行" in viewed.stdout
    assert snapshot.is_file()
    assert _snapshot(git_repo) == before
    refused = _cli(git_repo, "goal", "--reset")
    assert refused.returncode != 0
    assert snapshot.is_file()
    result = _cli(git_repo, "goal", "--reset", "--yes")
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


def test_goal_reset_and_explicit_tids_require_confirm_when_order_differs(git_repo):
    assert _cli(git_repo, "goal", "t002", "t001").returncode == 0
    before = _snapshot(git_repo)

    refused = _cli(git_repo, "goal", "--reset")
    assert refused.returncode != 0
    assert "确认覆盖请加 --yes" in refused.stderr
    assert "已冻结" in refused.stderr
    assert _snapshot(git_repo) == before

    reset = _cli(git_repo, "goal", "--reset", "--yes")
    assert reset.returncode == 0, reset.stderr
    assert _snapshot(git_repo)["queue"] == ["t001", "t002"]
    assert "队列已重新冻结：t001, t002" in reset.stdout

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
    assert not (git_repo / "docs" / "runtime" / "goal_queue.json").exists()


def test_goal_no_args_does_not_reset_corrupt_snapshot(git_repo):
    assert _cli(git_repo, "goal").returncode == 0
    path = git_repo / "docs" / "runtime" / "goal_queue.json"
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
    path = git_repo / "docs" / "runtime" / "goal_queue.json"
    payload = b"\xff\xfe{not utf8"
    path.write_bytes(payload)
    result = _cli(git_repo, "goal")
    assert result.returncode != 0
    text = result.stderr + result.stdout
    assert "损坏" in text
    assert "Traceback" not in text
    assert path.read_bytes() == payload
