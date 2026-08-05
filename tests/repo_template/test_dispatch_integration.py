"""调度控制面真实 git 集成：refs 验证、observe 指纹去重、自动记账和 MERGE_HEAD 校验。"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "repo_template"
TASK_TEMPLATE_DIR = SCRIPTS_DIR.parent.parent / "docs" / "tasks" / "task_template"
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task import context as ctx
from repo_task import integration, monitoring
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
        "- {AC 编号}：{不可测原因与替代验证方式}": "- 全部 AC 可自动测试",
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
    """真实 git 主仓 + 一个 backlog task（t001_alpha）；账本路径重定向进仓。"""
    repo = tmp_path / "repo"
    tasks = repo / "docs" / "tasks"
    archive = repo / "docs" / "archive" / "tasks"
    template = tasks / "task_template"
    (tasks).mkdir(parents=True)
    archive.mkdir(parents=True)
    (repo / "scripts" / "repo_template").mkdir(parents=True)
    shutil.copy2(SCRIPTS_DIR / "task.py", repo / "scripts" / "repo_template" / "task.py")
    shutil.copytree(
        SCRIPTS_DIR / "repo_task",
        repo / "scripts" / "repo_template" / "repo_task",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(TASK_TEMPLATE_DIR, template)
    task_dir = tasks / "t001_alpha"
    shutil.copytree(TASK_TEMPLATE_DIR, task_dir)
    _, body = parse_front_matter(TASK_TEMPLATE_DIR / "task.md")
    write_front_matter(
        task_dir / "task.md",
        {
            "tid": "t001", "slug": "alpha", "title": "alpha", "status": "backlog",
            "branch": "", "worktree": "", "review_level": "full",
            "diff_anchor": "", "note": "",
        },
        body,
    )
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
    (repo / ".gitignore").write_text("__pycache__/\n*.py[cod]\ndocs/runtime/\n", encoding="utf-8")
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _task_cli(repo, *args):
    return subprocess.run(
        [sys.executable, str(repo / "scripts" / "repo_template" / "task.py"), *args],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _worktree_path(repo, tid="t001"):
    return repo.parent / f"{repo.name}_{tid}"


def _start(repo, tid="t001"):
    integration.cmd_start(argparse.Namespace(tid=tid, base=None))


def _finish_commit_cleanup(repo, tid="t001", slug="alpha"):
    worktree = _worktree_path(repo, tid)
    finished = _task_cli(worktree, "finish", tid)
    assert finished.returncode == 0, finished.stderr
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", f"feat({tid}): complete {slug}")
    cleaned = _task_cli(repo, "cleanup-worktree", tid)
    assert cleaned.returncode == 0, cleaned.stderr
    return f"{tid}_{slug}"


def _make_done_branch(repo, handoff=None, tip_status="done", tid="t001", slug="alpha"):
    """手工造分支 tip 现场：终态时 task 目录归档，非终态时留在 docs/tasks。"""
    branch = f"{tid}_{slug}"
    _git(repo, "checkout", "-b", branch)
    if tip_status in ("done", "dropped"):
        dst = repo / "docs" / "archive" / "tasks" / branch
        shutil.move(str(repo / "docs" / "tasks" / branch), str(dst))
    else:
        dst = repo / "docs" / "tasks" / branch
    task_md = dst / "task.md"
    fm, body = parse_front_matter(task_md)
    fm["status"] = tip_status
    fm["branch"] = branch
    write_front_matter(task_md, fm, body)
    if handoff is not None:
        (dst / "handoff.json").write_text(handoff, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"feat({tid}): done")
    _git(repo, "checkout", "main")
    return branch


def _read_ledger(repo):
    path = repo / "docs" / "runtime" / "dispatch_ledger.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


# --------------------------------------------------------------------------
# verify_integrate_ready：真 refs 各分支
# --------------------------------------------------------------------------


def test_verify_ready_with_valid_handoff(git_repo):
    handoff = json.dumps({"tid": "t001", "status": "done", "branch": "t001_alpha"})
    _make_done_branch(git_repo, handoff=handoff)

    verdict, detail = monitoring.verify_integrate_ready("t001")

    assert verdict == "ready", detail


def test_verify_incomplete_when_tip_not_terminal(git_repo):
    _make_done_branch(git_repo, tip_status="active")

    verdict, detail = monitoring.verify_integrate_ready("t001")

    assert verdict == "incomplete"
    assert "非终态" in detail


def test_verify_incomplete_without_branch(git_repo):
    verdict, detail = monitoring.verify_integrate_ready("t001")

    assert verdict == "incomplete"
    assert "无本地 task 分支" in detail


@pytest.mark.parametrize(
    "handoff, expected_detail",
    [
        (None, "缺"),
        ("not json", "无法解析"),
        ("[]", "非 JSON 对象"),
        (json.dumps({"tid": "t001", "branch": "t001_alpha"}), "缺失或非终态"),
        (json.dumps({"tid": "t001", "status": "active", "branch": "t001_alpha"}), "缺失或非终态"),
        (json.dumps({"tid": "t999", "status": "done", "branch": "t001_alpha"}), "tid="),
        (json.dumps({"tid": "t001", "status": "done", "branch": "t001_other"}), "branch="),
    ],
)
def test_verify_contract_on_handoff_defects(git_repo, handoff, expected_detail):
    _make_done_branch(git_repo, handoff=handoff)

    verdict, detail = monitoring.verify_integrate_ready("t001")

    assert verdict == "contract"
    assert expected_detail in detail


# --------------------------------------------------------------------------
# observe：attempt/worktree 校验与变化去重
# --------------------------------------------------------------------------


def _dispatch(repo, attempt="1", worker_id="worker-1"):
    result = _task_cli(
        repo, "ledger", "record", "--event", "dispatch", "--tid", "t001",
        "--attempt", attempt, "--model", "opus", "--worker-id", worker_id,
    )
    assert result.returncode == 0, result.stderr


def test_observe_appends_first_and_changed_only(git_repo):
    _start(git_repo)
    _dispatch(git_repo)

    first = _task_cli(git_repo, "observe", "t001", "--attempt", "1", "--json")
    second = _task_cli(git_repo, "observe", "t001", "--attempt", "1", "--json")
    assert first.returncode == second.returncode == 0
    assert json.loads(first.stdout)["changed"] is True
    assert json.loads(second.stdout)["changed"] is False
    assert json.loads(first.stdout)["worker_id"] == "worker-1"
    assert len([event for event in _read_ledger(git_repo) if event["event"] == "observation"]) == 1

    (_worktree_path(git_repo) / "new.bin").write_bytes(b"\x00\xffchanged")
    changed = _task_cli(git_repo, "observe", "t001", "--attempt", "1", "--json")

    assert changed.returncode == 0, changed.stderr
    assert json.loads(changed.stdout)["changed"] is True
    assert len([event for event in _read_ledger(git_repo) if event["event"] == "observation"]) == 2


def test_observe_rejects_unknown_attempt_mismatched_owner_and_missing_worktree(git_repo):
    _start(git_repo)
    _dispatch(git_repo)

    unknown = _task_cli(git_repo, "observe", "t001", "--attempt", "2")
    assert unknown.returncode != 0
    assert "未 dispatch" in unknown.stderr

    worktree = _worktree_path(git_repo)
    _git(worktree, "checkout", "-b", "t999_wrong")
    mismatch = _task_cli(git_repo, "observe", "t001", "--attempt", "1")
    assert mismatch.returncode != 0
    assert "归属不符" in mismatch.stderr

    _git(worktree, "checkout", "t001_alpha")
    _git(git_repo, "worktree", "remove", "--force", str(worktree))
    missing = _task_cli(git_repo, "observe", "t001", "--attempt", "1")
    assert missing.returncode != 0
    assert "worktree 不存在或未登记" in missing.stderr


# --------------------------------------------------------------------------
# cmd_start / cmd_integrate 自动落账（真实仓库布局，守卫通过）
# --------------------------------------------------------------------------


def test_start_appends_ledger_event(git_repo):
    _start(git_repo)

    events = _read_ledger(git_repo)
    start = next(e for e in events if e["event"] == "start")
    assert start["tid"] == "t001"
    assert start["branch"] == "t001_alpha"
    assert start["worktree"] == f"../{git_repo.name}_t001"
    assert "ts" in start


def test_integrate_appends_integrated_with_merge_sha(git_repo):
    _start(git_repo)
    _finish_commit_cleanup(git_repo)

    result = _task_cli(git_repo, "integrate", "t001")

    assert result.returncode == 0, result.stderr
    events = _read_ledger(git_repo)
    integrated = [e for e in events if e["event"] == "integrated"]
    assert len(integrated) == 1
    assert integrated[0]["tid"] == "t001"
    merge_commit = _git(git_repo, "log", "--format=%H", "-2").stdout.splitlines()[1]
    assert integrated[0]["merge_sha"] == merge_commit


def test_integrate_skip_merge_also_appends_integrated(git_repo):
    """已合入跳过 merge 的路径也补记 integrated，防「分支已删但永久在飞」。"""
    _start(git_repo)
    _finish_commit_cleanup(git_repo)
    first = _task_cli(git_repo, "integrate", "t001", "--keep-branch")
    assert first.returncode == 0, first.stderr

    second = _task_cli(git_repo, "integrate", "t001")

    assert second.returncode == 0, second.stderr
    assert "跳过 merge" in second.stdout
    integrated = [e for e in _read_ledger(git_repo) if e["event"] == "integrated"]
    assert len(integrated) == 2
    assert all(e["tid"] == "t001" and e["merge_sha"] for e in integrated)


def test_integrate_continue_rejects_foreign_merge_head(git_repo):
    """进行中的 merge 来自其他分支时，--continue 拒绝提交并记账到错误 task。"""
    _start(git_repo)
    _finish_commit_cleanup(git_repo)  # t001_alpha 分支就绪，但不 integrate
    # 制造另一个分支的冲突 merge 并解决（MERGE_HEAD 仍在）
    shared = git_repo / "shared.txt"
    shared.write_text("base\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "chore: add shared")
    _git(git_repo, "checkout", "-b", "other")
    shared.write_text("from other\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "chore: other edit")
    _git(git_repo, "checkout", "main")
    shared.write_text("from main\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "chore: main edit")
    assert _git(git_repo, "merge", "other", check=False).returncode != 0
    shared.write_text("resolved\n", encoding="utf-8")
    _git(git_repo, "add", "shared.txt")

    result = _task_cli(git_repo, "integrate", "t001", "--continue")

    assert result.returncode != 0
    assert "不符" in result.stderr
    assert "MERGE_HEAD" in result.stderr
    assert _read_ledger(git_repo) == [] or all(
        e["event"] != "integrated" for e in _read_ledger(git_repo)
    )
    _git(git_repo, "merge", "--abort")


def test_verify_contract_when_multiple_branches(git_repo):
    """同 tid 多个本地分支：integrate 会拒绝，verify 判 contract 而非静默选一。"""
    handoff = json.dumps({"tid": "t001", "status": "done", "branch": "t001_alpha"})
    _make_done_branch(git_repo, handoff=handoff)
    _git(git_repo, "branch", "t001_beta")

    verdict, detail = monitoring.verify_integrate_ready("t001")

    assert verdict == "contract"
    assert "多个分支" in detail


def test_verify_handoff_attempt_must_match_parallel_attempt(git_repo):
    handoff = json.dumps({
        "tid": "t001", "status": "done", "branch": "t001_alpha", "attempt": 2,
    })
    _make_done_branch(git_repo, handoff=handoff)

    mismatch, detail = monitoring.verify_integrate_ready("t001", attempt=1)
    assert mismatch == "contract"
    assert "attempt=2" in detail and "当前 attempt=1" in detail

    ready, detail = monitoring.verify_integrate_ready("t001", attempt=2)
    assert ready == "ready", detail


def test_parallel_cleanup_and_integrate_require_terminal_attempt(git_repo):
    _start(git_repo)
    _dispatch(git_repo)
    worktree = _worktree_path(git_repo)
    finished = _task_cli(worktree, "finish", "t001")
    assert finished.returncode == 0, finished.stderr
    (worktree / "docs" / "archive" / "tasks" / "t001_alpha" / "handoff.json").write_text(
        json.dumps({
            "tid": "t001", "attempt": 1, "status": "done", "branch": "t001_alpha",
        }),
        encoding="utf-8",
    )
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "feat(t001): complete alpha")

    missing_attempt = _task_cli(git_repo, "cleanup-worktree", "t001")
    assert missing_attempt.returncode != 0
    assert "必须显式给 --attempt" in missing_attempt.stderr

    missing_terminal = _task_cli(git_repo, "cleanup-worktree", "t001", "--attempt", "1")
    assert missing_terminal.returncode != 0
    assert "尚无 worker_terminal" in missing_terminal.stderr

    terminal = _task_cli(
        git_repo, "ledger", "record", "--event", "worker_terminal", "--tid", "t001",
        "--attempt", "1", "--worker-id", "worker-1", "--status", "completed",
    )
    assert terminal.returncode == 0, terminal.stderr
    cleaned = _task_cli(git_repo, "cleanup-worktree", "t001", "--attempt", "1")
    assert cleaned.returncode == 0, cleaned.stderr

    integrated = _task_cli(git_repo, "integrate", "t001", "--attempt", "1")
    assert integrated.returncode == 0, integrated.stderr
    records = [event for event in _read_ledger(git_repo) if event["event"] == "integrated"]
    assert records and records[-1]["attempt"] == 1
