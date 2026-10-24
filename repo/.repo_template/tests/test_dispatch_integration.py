"""Real-git tests for exact handoff, cleanup, single integration, and chain transactions."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
TASK_TEMPLATE_DIR = SCRIPTS_DIR.parent / "docs" / "task_template"
sys.path.insert(0, str(SCRIPTS_DIR))

from review_support import ensure_review_evidence, configure_project_verification

from repo_task import context as ctx
from repo_task import monitoring
from repo_task.documents import parse_front_matter, write_front_matter


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check,
    )


def _valid_spec():
    text = (TASK_TEMPLATE_DIR / "task.md").read_text(encoding="utf-8")
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
def git_repo(tmp_path, monkeypatch):
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
    _, body = parse_front_matter(TASK_TEMPLATE_DIR / "note.md")
    for tid, slug in (("t001", "alpha"), ("t002", "beta"), ("t003", "gamma")):
        task_dir = tasks / f"{tid}_{slug}"
        shutil.copytree(TASK_TEMPLATE_DIR, task_dir)
        write_front_matter(task_dir / "note.md", {
            "tid": tid, "slug": slug, "title": slug, "status": "backlog",
            "branch": "", "worktree": "", "review_level": "full",
            "diff_anchor": "", "note": "",
        }, body)
        (task_dir / "task.md").write_text(_valid_spec(), encoding="utf-8")
    monkeypatch.setattr(ctx, "TASKS_DIR", tasks)
    monkeypatch.setattr(ctx, "ARCHIVE_TASKS_DIR", archive)
    monkeypatch.setattr(ctx, "TEMPLATE_DIR", template)
    monkeypatch.setattr(ctx, "ACTIVE_PATH", repo / "docs" / "tasks_index.json")
    monkeypatch.setattr(ctx, "ARCHIVE_PATH", repo / "docs" / "archive" / "tasks_index.json")
    monkeypatch.setattr(ctx, "AUDIT_PATH", repo / "docs" / "archive" / "tasks_audit.log")
    monkeypatch.setattr(ctx, "REPO_ROOT", repo)
    monkeypatch.setattr(ctx, "RUNTIME_DIR", repo / "docs" / "runtime")
    monkeypatch.setattr(ctx, "LEDGER_PATH", repo / "docs" / "runtime" / "dispatch_ledger.jsonl")
    (repo / ".gitignore").write_text(
        "__pycache__/\n*.py[cod]\ndocs/runtime/\n", encoding="utf-8"
    )
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    configure_project_verification(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _task_cli(repo, *args):
    return subprocess.run(
        [sys.executable, str(repo / ".repo_template" / "scripts" / "task.py"), *args],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _read_ledger(repo):
    path = repo / "docs" / "runtime" / "dispatch_ledger.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _worktree(repo, tid):
    return repo.parent / f"{repo.name}_{tid}"


def _reserve(repo, tid, executor="inline", model=None):
    args = ["attempt", "reserve", tid, "--executor", executor]
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


def _handoff(tid, branch, identity, base_sha, **overrides):
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
    payload.update(overrides)
    return payload


def _prepare_done(
    repo, tid, slug, *, base=None, handoff_overrides=None, mutate=None,
    terminal=True,
):
    start_args = ["start", tid]
    if base:
        start_args += ["--base", base]
    started = _task_cli(repo, *start_args)
    assert started.returncode == 0, started.stderr
    identity = _reserve(repo, tid)
    worktree = _worktree(repo, tid)
    if mutate:
        mutate(worktree)
    finished = _task_cli(worktree, "finish", tid)
    assert finished.returncode == 0, finished.stderr
    branch = f"{tid}_{slug}"
    base_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    archive = worktree / "docs" / "archive" / "tasks" / branch
    ensure_review_evidence(worktree, archive, base_sha)
    payload = _handoff(tid, branch, identity, base_sha)
    payload.update(handoff_overrides or {})
    (archive / "handoff.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", f"feat({tid}): complete {slug}")
    head = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    if terminal:
        result = _task_cli(
            repo, "attempt", "terminal", tid, *_identity_args(identity),
            "--status", "completed",
        )
        assert result.returncode == 0, result.stderr
        report = _task_cli(
            repo, "attempt", "report", tid, *_identity_args(identity),
            "--status", "done", "--sha", head,
        )
        assert report.returncode == 0, report.stderr
    return identity, branch, head


def _cleanup(repo, tid, identity):
    result = _task_cli(repo, "cleanup-worktree", tid, *_identity_args(identity))
    assert result.returncode == 0, result.stderr
    return result


def _handoff_path(repo, tid, slug):
    return _worktree(repo, tid) / "docs" / "archive" / "tasks" / f"{tid}_{slug}" / "handoff.json"


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


def test_verify_ready_with_valid_handoff(git_repo):
    identity, branch, _ = _prepare_done(git_repo, "t001", "alpha")

    verdict, detail = monitoring.verify_integrate_ready(
        "t001", identity["attempt"], identity["execution_id"]
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

    verdict, detail = monitoring.verify_integrate_ready(
        "t001", identity["attempt"], identity["execution_id"]
    )

    assert verdict == "incomplete"
    assert "非终态" in detail


def test_verify_incomplete_without_branch(git_repo):
    # verify_integrate_ready 只查分支/handoff/spec，不查 ledger；
    # 直接传假 identity 验证「无本地分支 → incomplete」，无需真实 reserve（RT-006 门禁要求 start 后 reserve）
    verdict, detail = monitoring.verify_integrate_ready("t001", 1, "fake-execution")

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
        ("ac-evidence-empty", "缺 AC-001"),
        ("ac-evidence-unknown", "未知 AC-002"),
        ("ac-evidence-bad-value", "非空字符串数组"),
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
        elif defect == "ac-evidence-empty":
            payload["ac_evidence"] = {}
        elif defect == "ac-evidence-unknown":
            payload["ac_evidence"] = {"AC-002": ["x"]}
        elif defect == "ac-evidence-bad-value":
            payload["ac_evidence"] = {"AC-001": []}
        content = json.dumps(payload, ensure_ascii=False)
    _rewrite_handoff(git_repo, "t001", "alpha", content)

    verdict, detail = monitoring.verify_integrate_ready(
        "t001", identity["attempt"], identity["execution_id"]
    )

    assert verdict == "contract"
    assert expected_detail in detail


def test_handoff_rejects_wrong_field_type(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    path = _handoff_path(git_repo, "t001", "alpha")
    payload = json.loads(path.read_text("utf-8"))
    payload["tests"] = ["pytest"]
    _rewrite_handoff(git_repo, "t001", "alpha", json.dumps(payload, ensure_ascii=False))

    verdict, detail = monitoring.verify_integrate_ready(
        "t001", identity["attempt"], identity["execution_id"]
    )

    assert verdict == "contract"
    assert "tests" in detail


def test_handoff_rejects_base_sha_other_than_tip_first_parent(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    path = _handoff_path(git_repo, "t001", "alpha")
    payload = json.loads(path.read_text("utf-8"))
    payload["base_sha"] = "HEAD"
    _rewrite_handoff(git_repo, "t001", "alpha", json.dumps(payload, ensure_ascii=False))

    verdict, detail = monitoring.verify_integrate_ready(
        "t001", identity["attempt"], identity["execution_id"]
    )

    assert verdict == "contract"
    assert "base_sha" in detail and "first parent" in detail


def test_handoff_rejects_diff_anchor_not_matching_execution_parent(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    worktree = _worktree(git_repo, "t001")
    task_path = worktree / "docs/archive/tasks/t001_alpha/note.md"
    fm, body = parse_front_matter(task_path)
    fm["diff_anchor"] = "0" * 40
    write_front_matter(task_path, fm, body)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "--amend", "--no-edit")
    verdict, detail = monitoring.verify_integrate_ready(
        "t001", identity["attempt"], identity["execution_id"]
    )

    assert verdict == "contract"
    assert "diff_anchor" in detail
    assert "一个 task 必须恰有一个执行 commit" in detail


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
    # Recovery also persists derived indexes; merge_sha remains the actual merge commit.
    assert (git_repo / "docs/tasks_index.json").is_file()
    assert (git_repo / "docs/archive/tasks_index.json").is_file()
    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() != preintegrate_head


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

    verdict, detail = monitoring.verify_integrate_ready(
        "t001", identity["attempt"], identity["execution_id"]
    )

    assert verdict == "contract"
    assert "多个分支" in detail


def test_verify_handoff_attempt_must_match_parallel_attempt(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")

    mismatch_attempt, detail = monitoring.verify_integrate_ready(
        "t001", identity["attempt"] + 1, identity["execution_id"]
    )
    assert mismatch_attempt == "contract"
    assert "attempt=" in detail and "不符" in detail

    mismatch_execution, detail = monitoring.verify_integrate_ready(
        "t001", identity["attempt"], "wrong"
    )
    assert mismatch_execution == "contract"
    assert "execution_id" in detail and "不符" in detail

    ready, detail = monitoring.verify_integrate_ready(
        "t001", identity["attempt"], identity["execution_id"]
    )
    assert ready == "ready", detail


def test_parallel_cleanup_and_integrate_require_terminal_attempt(git_repo):
    identity, branch, _ = _prepare_done(
        git_repo, "t001", "alpha", terminal=False
    )

    missing_identity = _task_cli(git_repo, "cleanup-worktree", "t001")
    assert missing_identity.returncode != 0
    assert "--attempt" in missing_identity.stderr
    assert "--execution-id" in missing_identity.stderr

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

    cleaned = _cleanup(git_repo, "t001", identity)
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

    no_identity = _task_cli(git_repo, "integrate", "t001")
    assert no_identity.returncode != 0
    assert "--attempt" in no_identity.stderr and "--execution-id" in no_identity.stderr


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
    with pytest.raises(TaskDataError, match="git status"):
        git_ops.porcelain_entries()
    with pytest.raises(TaskDataError, match="git status"):
        git_ops.tracked_dirty_entries()


def test_review_scope_fingerprint_includes_untracked(git_repo, monkeypatch):
    """RT-002：未跟踪文件（未 git add -N）加入后 scope 指纹必须变，防静默绕过 review 门禁。"""
    import check_review_status as crs

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




def test_cmd_integrate_uses_chain_lock(git_repo):
    """RT-004：cmd_integrate 必须被 _chain_locked 装饰，single 与 chain 互斥主干写。"""
    from repo_task import integration

    wrapped = getattr(integration.cmd_integrate, "__wrapped__", None)
    assert wrapped is not None, "cmd_integrate 未被 _chain_locked 包装（single 不拿主干写锁）"
    assert wrapped.__name__ == "cmd_integrate"


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
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
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


def test_ledger_read_filtered_by_tid_and_fast_allocate(tmp_path, monkeypatch):
    """验证大型账本下按 tid 快速预过滤读取与单次打开追加优化（Issue #57）。"""
    from repo_task import context as ctx
    from repo_task.ledger import ledger_allocate_attempt, ledger_append, ledger_read

    runtime = tmp_path / "docs" / "runtime"
    runtime.mkdir(parents=True)
    ledger_path = runtime / "dispatch_ledger.jsonl"
    monkeypatch.setattr(ctx, "LEDGER_PATH", ledger_path)
    monkeypatch.setattr(ctx, "REPO_ROOT", tmp_path)

    # 1. 模拟写入 500 条其他任务记录
    lines = [json.dumps({"tid": f"t{i:04d}", "attempt": 1, "event": "start"}) for i in range(1, 501)]
    # 加入 t001 的 attempt 1 记录
    lines.append(json.dumps({"tid": "t001", "attempt": 1, "event": "start"}))
    ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 2. 仅读 t001 的记录
    t001_events = ledger_read(tid="t001")
    assert len(t001_events) == 1
    assert t001_events[0]["tid"] == "t001"

    # 3. 为 t001 分配下一个 attempt，应为 2
    allocated = ledger_allocate_attempt("t001", lambda att, ev: {"event": "attempt_reserved", "state": "running"})
    assert allocated["attempt"] == 2
    assert allocated["tid"] == "t001"

    # 4. 再次按 tid 读，包含两条
    t001_events2 = ledger_read(tid="t001")
    assert len(t001_events2) == 2


def test_chain_locked_uses_shared_file_lock(tmp_path, monkeypatch):
    """验证 integration._chain_locked 统一使用 locks.file_lock（Issue #56）。"""
    from repo_task import integration, locks

    lock_calls = []

    real_file_lock = locks.file_lock
    def spy_file_lock(path):
        lock_calls.append(path)
        return real_file_lock(path)

    monkeypatch.setattr(integration, "file_lock", spy_file_lock)
    monkeypatch.setattr(integration, "_integration_lock_path", lambda: tmp_path / "integrate.lock")

    @integration._chain_locked
    def my_action(val):
        return val * 2

    assert my_action(21) == 42
    assert len(lock_calls) == 1
    assert lock_calls[0] == tmp_path / "integrate.lock"


