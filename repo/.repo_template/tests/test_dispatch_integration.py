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

from repo_task import context as ctx
from repo_task import monitoring
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
    _, body = parse_front_matter(TASK_TEMPLATE_DIR / "task.md")
    for tid, slug in (("t001", "alpha"), ("t002", "beta"), ("t003", "gamma")):
        task_dir = tasks / f"{tid}_{slug}"
        shutil.copytree(TASK_TEMPLATE_DIR, task_dir)
        write_front_matter(task_dir / "task.md", {
            "tid": tid, "slug": slug, "title": slug, "status": "backlog",
            "branch": "", "worktree": "", "review_level": "full",
            "diff_anchor": "", "note": "",
        }, body)
        (task_dir / "spec.md").write_text(_valid_spec(), encoding="utf-8")
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
    identity, _, _ = _prepare_done(
        git_repo, "t001", "alpha", handoff_overrides={"tests": ["pytest"]}
    )

    verdict, detail = monitoring.verify_integrate_ready(
        "t001", identity["attempt"], identity["execution_id"]
    )

    assert verdict == "contract"
    assert "tests" in detail


def test_handoff_rejects_base_sha_other_than_tip_first_parent(git_repo):
    identity, _, _ = _prepare_done(
        git_repo, "t001", "alpha", handoff_overrides={"base_sha": "HEAD"}
    )

    verdict, detail = monitoring.verify_integrate_ready(
        "t001", identity["attempt"], identity["execution_id"]
    )

    assert verdict == "contract"
    assert "base_sha" in detail and "first parent" in detail


def test_handoff_rejects_diff_anchor_not_matching_execution_parent(git_repo):
    def corrupt_diff_anchor(worktree):
        task_path = worktree / "docs/tasks/t001_alpha/task.md"
        fm, body = parse_front_matter(task_path)
        fm["diff_anchor"] = "0" * 40
        write_front_matter(task_path, fm, body)

    identity, _, _ = _prepare_done(
        git_repo, "t001", "alpha", mutate=corrupt_diff_anchor
    )
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


def test_integrate_appends_integrated_with_merge_sha(git_repo):
    identity, branch, branch_head = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", identity)

    result = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity))

    assert result.returncode == 0, result.stderr
    assert _git(
        git_repo, "merge-base", "--is-ancestor", branch_head, "main", check=False
    ).returncode == 0
    assert _git(git_repo, "branch", "--list", branch).stdout.strip() == ""
    integrated = [event for event in _read_ledger(git_repo) if event["event"] == "integrated"]
    assert len(integrated) == 1
    event = integrated[0]
    assert event["tid"] == "t001"
    assert event["attempt"] == identity["attempt"]
    assert event["execution_id"] == identity["execution_id"]
    assert _git(git_repo, "rev-parse", f"{event['merge_sha']}^2", check=False).returncode == 0
    assert _git(
        git_repo, "merge-base", "--is-ancestor", event["merge_sha"], "main", check=False
    ).returncode == 0


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


def test_integrate_skip_merge_rejects_foreign_head(git_repo):
    """崩溃窗口：分支已合入但 HEAD 被无关 commit 推进，skip-merge 拒绝以 HEAD 冒充 merge_sha（RT-003）。"""
    identity, branch, branch_head = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", identity)
    _git(git_repo, "merge", "--no-ff", "-m", "test: premerge task", branch)
    _git(git_repo, "commit", "--allow-empty", "-m", "test: unrelated advance after premerge")

    result = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity))

    assert result.returncode != 0
    assert "找不到对应 merge(t001)" in result.stderr
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

    integrated = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity))
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


def test_integrate_chain_aggregate_gate_then_one_merge_and_exact_events(git_repo):
    first, first_branch, first_head = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", first)
    second, second_branch, second_head = _prepare_done(
        git_repo, "t002", "beta", base=first_branch
    )
    _cleanup(git_repo, "t002", second)

    before_merges = int(_git(git_repo, "rev-list", "--merges", "--count", "HEAD").stdout)
    result = _task_cli(git_repo, "integrate-chain", "t002")
    assert result.returncode == 0, result.stderr
    after_merges = int(_git(git_repo, "rev-list", "--merges", "--count", "HEAD").stdout)
    assert after_merges - before_merges == 1
    for head in (first_head, second_head):
        assert _git(git_repo, "merge-base", "--is-ancestor", head, "main", check=False).returncode == 0
    git_dir = Path(_git(git_repo, "rev-parse", "--absolute-git-dir").stdout.strip())
    transaction = git_dir / "repo-task" / "integrate-chain.json"
    payload = json.loads(transaction.read_text(encoding="utf-8"))
    assert payload["phase"] == "awaiting_verification"
    assert payload["merge_sha"]
    assert _git(git_repo, "branch", "--list", first_branch).stdout.strip() == first_branch
    assert _git(git_repo, "branch", "--list", second_branch).stdout.strip() == second_branch
    events = [item for item in _read_ledger(git_repo) if item["event"] == "integrated"]
    assert [(item["tid"], item["attempt"], item["execution_id"]) for item in events] == [
        ("t001", first["attempt"], first["execution_id"]),
        ("t002", second["attempt"], second["execution_id"]),
    ]

    finalized = _task_cli(git_repo, "integrate-chain", "t002", "--continue")
    assert finalized.returncode == 0, finalized.stderr
    assert not transaction.exists()
    assert _git(git_repo, "branch", "--list", first_branch).stdout.strip() == ""
    assert _git(git_repo, "branch", "--list", second_branch).stdout.strip() == ""


def test_integrate_chain_recovers_post_merge_finalize_without_duplicate_events(git_repo):
    first, first_branch, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", first)
    second, second_branch, _ = _prepare_done(
        git_repo, "t002", "beta", base=first_branch
    )
    _cleanup(git_repo, "t002", second)
    started = _task_cli(git_repo, "integrate-chain", "t002")
    assert started.returncode == 0, started.stderr

    git_dir = Path(_git(git_repo, "rev-parse", "--absolute-git-dir").stdout.strip())
    transaction = git_dir / "repo-task" / "integrate-chain.json"
    payload = json.loads(transaction.read_text(encoding="utf-8"))
    assert payload["phase"] == "awaiting_verification"
    original_events = [
        item for item in _read_ledger(git_repo) if item["event"] == "integrated"
    ]
    payload["phase"] = "merged"
    payload["index_sha"] = None
    transaction.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    recovered = _task_cli(git_repo, "integrate-chain", "t002", "--continue")
    assert recovered.returncode == 0, recovered.stderr
    recovered_payload = json.loads(transaction.read_text(encoding="utf-8"))
    assert recovered_payload["phase"] == "awaiting_verification"
    recovered_events = [
        item for item in _read_ledger(git_repo) if item["event"] == "integrated"
    ]
    assert recovered_events == original_events
    assert _git(git_repo, "branch", "--list", first_branch).stdout.strip() == first_branch
    assert _git(git_repo, "branch", "--list", second_branch).stdout.strip() == second_branch

    finalized = _task_cli(git_repo, "integrate-chain", "t002", "--continue")
    assert finalized.returncode == 0, finalized.stderr
    assert not transaction.exists()


def test_integrate_chain_preflight_failure_has_zero_merge_and_zero_integrated(git_repo):
    first, first_branch, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", first)
    second, _, _ = _prepare_done(
        git_repo, "t002", "beta", base=first_branch,
        handoff_overrides={"execution_id": "wrong"},
    )
    worktree = _worktree(git_repo, "t002")
    _git(git_repo, "worktree", "remove", str(worktree))
    before = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    result = _task_cli(git_repo, "integrate-chain", "t002")
    assert result.returncode != 0
    assert "execution_id" in result.stderr
    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == before
    assert not [item for item in _read_ledger(git_repo) if item["event"] == "integrated"]
    assert _git(git_repo, "branch", "--list", first_branch).stdout.strip() == first_branch
    assert second["execution_id"] != "wrong"


def test_integrate_chain_conflict_continue_uses_exact_transaction(git_repo):
    shared = git_repo / "shared.txt"
    shared.write_text("base\n", encoding="utf-8")
    _git(git_repo, "add", "shared.txt")
    _git(git_repo, "commit", "-m", "add shared")

    def task_edit(worktree):
        (worktree / "shared.txt").write_text("from task\n", encoding="utf-8")

    identity, _, _ = _prepare_done(
        git_repo, "t001", "alpha", mutate=task_edit
    )
    _cleanup(git_repo, "t001", identity)
    shared.write_text("from main\n", encoding="utf-8")
    _git(git_repo, "add", "shared.txt")
    _git(git_repo, "commit", "-m", "edit shared on main")

    conflict = _task_cli(git_repo, "integrate-chain", "t001")
    assert conflict.returncode != 0
    assert "冲突" in conflict.stderr
    git_dir = Path(_git(git_repo, "rev-parse", "--absolute-git-dir").stdout.strip())
    transaction = git_dir / "repo-task" / "integrate-chain.json"
    assert transaction.is_file()

    foreign = _task_cli(git_repo, "integrate-chain", "t002", "--continue")
    assert foreign.returncode != 0
    assert "tail_tid" in foreign.stderr

    shared.write_text("resolved\n", encoding="utf-8")
    _git(git_repo, "add", "shared.txt")
    resumed = _task_cli(git_repo, "integrate-chain", "t001", "--continue")
    assert resumed.returncode == 0, resumed.stderr
    payload = json.loads(transaction.read_text(encoding="utf-8"))
    assert payload["phase"] == "awaiting_verification"
    assert payload["merge_sha"]
    assert shared.read_text(encoding="utf-8") == "resolved\n"

    finalized = _task_cli(git_repo, "integrate-chain", "t001", "--continue")
    assert finalized.returncode == 0, finalized.stderr
    assert not transaction.exists()


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


def test_require_index_commit_rejects_foreign_commit(git_repo):
    """RT-005：紧邻 merge 的 commit 若非工具链 index commit，恢复必须拒绝认领。"""
    from repo_task import integration

    _git(git_repo, "commit", "--allow-empty", "-m", "chore: not index commit")
    with pytest.raises(ctx.TaskDataError, match="非工具链 index 维护 commit"):
        integration._require_index_commit("HEAD")


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
