"""C3：finish 原子化 —— 前置检查零写入、机器生成 handoff.json、提交失败整体回滚。"""

import json
import os
import re

import pytest

from test_dispatch_integration import git_repo, _git, _task_cli, _worktree, _read_ledger
from task_flow_support import mark_done, fill_summary, finish_cli, prepare_finish
from repo_task import monitoring
from repo_task import context as ctx

OWNER = "owner-fin"


def _begin(repo, tid="t001", owner=OWNER):
    result = _task_cli(repo, "run-begin", tid, "--owner-id", owner)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _ready(repo, **kwargs):
    identity = _begin(repo)
    worktree = _worktree(repo, "t001")
    message = prepare_finish(worktree, "t001", "alpha", **kwargs)
    return identity, worktree, message


def _snapshot(repo, worktree):
    """finish 前后的全部可观察状态；零写入即前后相等。"""
    files = {
        path.relative_to(worktree).as_posix(): path.read_bytes()
        for path in sorted(worktree.rglob("*"))
        if path.is_file() and path.name != ".git" and "__pycache__" not in path.parts
    }
    git = {
        name: _git(worktree, *args).stdout
        for name, args in {
            "status": ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
            "cached": ("diff", "--cached", "--binary"),
            "staged_files": ("ls-files", "-s"),
            "head": ("rev-parse", "HEAD"),
        }.items()
    }
    ledger = (repo / "docs/runtime/dispatch_ledger.jsonl").read_bytes()
    return files, git, ledger


def _archive(worktree):
    return worktree / "docs/archive/tasks/t001_alpha"


def _task_dir(worktree):
    return worktree / "docs/tasks/t001_alpha"


def _committed_handoff(worktree):
    return json.loads(_git(worktree, "show", "HEAD:docs/tasks/t001_alpha/handoff.json").stdout)


# --------------------------------------------------------------------------
# 接口：两个参数必填，不保留无提交的旧模式
# --------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["--owner-id", "--message-file"])
def test_finish_requires_owner_id_and_message_file(git_repo, missing):
    _, worktree, message = _ready(git_repo)
    args = {"--owner-id": OWNER, "--message-file": str(message)}
    del args[missing]
    command = ["finish", "t001"] + [item for pair in args.items() for item in pair]
    before = _snapshot(git_repo, worktree)

    result = _task_cli(worktree, *command)

    assert result.returncode != 0
    assert missing in result.stderr
    assert _snapshot(git_repo, worktree) == before


def test_finish_without_any_option_is_rejected_with_zero_writes(git_repo):
    _, worktree, _ = _ready(git_repo)
    before = _snapshot(git_repo, worktree)
    result = _task_cli(worktree, "finish", "t001")
    assert result.returncode != 0
    assert _snapshot(git_repo, worktree) == before


# --------------------------------------------------------------------------
# 正常路径：一个执行 commit + 机器生成 handoff
# --------------------------------------------------------------------------


def test_finish_commits_once_with_machine_generated_handoff_in_place(git_repo):
    identity, worktree, _ = _ready(git_repo)
    anchor = _git(worktree, "rev-parse", "HEAD").stdout.strip()

    def deliver(root):
        (root / "src").mkdir()
        (root / "src/new.txt").write_text("delivered\n", encoding="utf-8")
        for rel in (
            "docs/pending/todo/p001_new_item.md",
            "docs/pending/parked/p003_parked_item.md",
            "docs/archive/pending/p002_closed_item.md",
            "docs/findings/d001_verified_fact.md",
        ):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# item\n", encoding="utf-8")

    message = prepare_finish(
        worktree, "t001", "alpha", mutate=deliver, message="feat(t001): 完成 alpha\n\n正文第二段。\n",
    )

    result = finish_cli(worktree, "t001", OWNER, message)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(worktree, "rev-list", "--count", f"{anchor}..HEAD").stdout.strip() == "1"
    assert _git(worktree, "rev-parse", "HEAD^1").stdout.strip() == anchor
    assert _git(worktree, "log", "-1", "--format=%B").stdout.strip() == (
        "feat(t001): 完成 alpha\n\n正文第二段。"
    )
    assert _git(worktree, "status", "--porcelain").stdout == ""
    assert _task_dir(worktree).is_dir() and not _archive(worktree).exists()
    handoff = _committed_handoff(worktree)
    assert handoff == {
        "tid": "t001",
        "attempt": identity["attempt"],
        "execution_id": identity["execution_id"],
        "status": "done",
        "branch": "t001_alpha",
        "base_sha": anchor,
        "tests": "pytest -q 通过",
        "blackbox": "无黑盒",
        "review": "PASS",
        "pending": ["p001", "p002", "p003"],
        "findings": ["d001"],
    }
    # 与既有验证契约同源：可被 verify_integrate_ready 接受
    verdict, detail = monitoring.verify_integrate_ready(
        "t001", identity["attempt"], identity["execution_id"]
    )
    assert verdict == "ready", detail


def test_finish_handoff_lists_only_entries_added_after_base(git_repo):
    for rel in ("docs/pending/todo/p005_old.md", "docs/findings/d009_old.md"):
        path = git_repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# old\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "seed existing ledger entries")
    identity = _begin(git_repo)
    worktree = _worktree(git_repo, "t001")

    def touch(root):
        # base 已存在的条目只被修改或归档迁移：不算本 task 新增
        (root / "docs/pending/todo/p005_old.md").write_text("# edited\n", encoding="utf-8")
        (root / "docs/findings/d009_old.md").write_text("# edited\n", encoding="utf-8")
        (root / "docs/pending/todo/p006_new.md").write_text("# new\n", encoding="utf-8")

    message = prepare_finish(worktree, "t001", "alpha", mutate=touch)
    result = finish_cli(worktree, "t001", OWNER, message)

    assert result.returncode == 0, result.stdout + result.stderr
    handoff = _committed_handoff(worktree)
    assert handoff["pending"] == ["p006"]
    assert handoff["findings"] == []
    assert handoff["execution_id"] == identity["execution_id"]


def test_full_main_path_begin_finish_close_integrate_uses_only_tid_and_owner(git_repo):
    identity, worktree, message = _ready(git_repo)

    finished = finish_cli(worktree, "t001", OWNER, message)
    assert finished.returncode == 0, finished.stdout + finished.stderr
    closed = _task_cli(git_repo, "run-close", "t001", "--owner-id", OWNER, "--result", "done")
    assert closed.returncode == 0, closed.stderr
    assert not worktree.exists()
    assert _task_cli(git_repo, "integrate", "t001").returncode == 0
    merged = _task_cli(git_repo, "integrate", "t001", "--continue")
    assert merged.returncode == 0, merged.stderr

    integrated = [e for e in _read_ledger(git_repo) if e["event"] == "integrated"]
    assert [(e["attempt"], e["execution_id"]) for e in integrated] == [
        (identity["attempt"], identity["execution_id"])
    ]
    assert not _git(git_repo, "branch", "--list", "t001_alpha").stdout.strip()
    assert (git_repo / "docs/archive/tasks/t001_alpha/handoff.json").is_file()


# --------------------------------------------------------------------------
# handoff 的 tests / blackbox / review 来自 note.md
# --------------------------------------------------------------------------


@pytest.mark.parametrize("label", ["测试", "黑盒", "review"])
@pytest.mark.parametrize("value", ["待执行时填写", ""])
def test_finish_rejects_placeholder_or_empty_verification_summary(git_repo, label, value):
    _, worktree, message = _ready(git_repo)
    note = worktree / "docs/tasks/t001_alpha/note.md"
    text = note.read_text(encoding="utf-8")
    text = re.sub(rf"(?m)^- {label}：.*$", f"- {label}：{value}".rstrip(), text)
    note.write_text(text, encoding="utf-8")
    before = _snapshot(git_repo, worktree)

    result = finish_cli(worktree, "t001", OWNER, message)

    assert result.returncode != 0
    assert f"`- {label}：`" in result.stderr
    assert "模板占位" in result.stderr
    assert _snapshot(git_repo, worktree) == before


def test_finish_rejects_missing_summary_line(git_repo):
    _, worktree, message = _ready(git_repo)
    note = worktree / "docs/tasks/t001_alpha/note.md"
    note.write_text(re.sub(r"(?m)^- 黑盒：.*\n", "", note.read_text(encoding="utf-8")), encoding="utf-8")
    before = _snapshot(git_repo, worktree)

    result = finish_cli(worktree, "t001", OWNER, message)

    assert result.returncode != 0
    assert "缺 `- 黑盒：` 行" in result.stderr
    assert _snapshot(git_repo, worktree) == before


# --------------------------------------------------------------------------
# 前置检查失败：零写入
# --------------------------------------------------------------------------


def _touch_untracked(worktree):
    (worktree / "stray.txt").write_text("stray\n", encoding="utf-8")


def _modify_unstaged(worktree):
    (worktree / ".gitignore").write_text("__pycache__/\n*.py[cod]\ndocs/runtime/\n# edit\n", encoding="utf-8")


def _intent_to_add(worktree):
    (worktree / "intent.txt").write_text("i\n", encoding="utf-8")
    _git(worktree, "add", "-N", "--", "intent.txt")


def _staged_then_modified(worktree):
    (worktree / "both.txt").write_text("one\n", encoding="utf-8")
    _git(worktree, "add", "both.txt")
    (worktree / "both.txt").write_text("two\n", encoding="utf-8")


@pytest.mark.parametrize(("stray", "path"), [
    (_touch_untracked, "stray.txt"),
    (_modify_unstaged, ".gitignore"),
    (_intent_to_add, "intent.txt"),
    (_staged_then_modified, "both.txt"),
])
def test_finish_rejects_unstaged_or_untracked_files_with_zero_writes(git_repo, stray, path):
    _, worktree, message = _ready(git_repo)
    stray(worktree)
    before = _snapshot(git_repo, worktree)

    result = finish_cli(worktree, "t001", OWNER, message)

    assert result.returncode != 0
    assert "未暂存或未跟踪" in result.stderr and path in result.stderr
    assert _snapshot(git_repo, worktree) == before
    assert (worktree / "docs/tasks/t001_alpha/note.md").is_file()


def test_finish_ignores_ignored_files_and_task_directory_files(git_repo):
    def extra(root):
        (root / "cache.pyc").write_text("ignored", encoding="utf-8")
        (root / "docs/tasks/t001_alpha/extra_notes.txt").write_text("task dir file", encoding="utf-8")

    _begin(git_repo)
    worktree = _worktree(git_repo, "t001")
    message = prepare_finish(worktree, "t001", "alpha", mutate=extra)

    result = finish_cli(worktree, "t001", OWNER, message)

    assert result.returncode == 0, result.stdout + result.stderr
    committed = _git(worktree, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert "docs/tasks/t001_alpha/extra_notes.txt" in committed
    assert "cache.pyc" not in committed


def _fail_review(worktree):
    (worktree / "docs/tasks/t001_alpha/review_code.md").write_text(
        "## Round 1\nverdict: FAIL\n", encoding="utf-8"
    )


def _stale_review(worktree):
    (worktree / "late.txt").write_text("changed after review\n", encoding="utf-8")
    _git(worktree, "add", "late.txt")


def _missing_review(worktree):
    (worktree / "docs/tasks/t001_alpha/review_test.md").unlink()


def _uncheck_ac(worktree):
    spec = worktree / "docs/tasks/t001_alpha/task.md"
    spec.write_text(spec.read_text(encoding="utf-8").replace("- [x] AC-001", "- [ ] AC-001"), encoding="utf-8")


def _extra_commit(worktree):
    _git(worktree, "commit", "--allow-empty", "-m", "intermediate")


@pytest.mark.parametrize(("break_it", "needle"), [
    (_fail_review, "overall=FAIL"),
    (_stale_review, "review_scope=stale"),
    (_missing_review, "overall=INCOMPLETE"),
    (_uncheck_ac, "存在未完成 AC（未打勾）：AC-001"),
    (_extra_commit, "不是 diff_anchor"),
])
def test_finish_gate_failures_write_nothing(git_repo, break_it, needle):
    _, worktree, message = _ready(git_repo)
    break_it(worktree)
    before = _snapshot(git_repo, worktree)

    result = finish_cli(worktree, "t001", OWNER, message)

    assert result.returncode != 0
    assert needle in result.stderr
    assert _snapshot(git_repo, worktree) == before


def test_finish_rejects_foreign_owner_and_unclaimed_attempt(git_repo):
    _, worktree, message = _ready(git_repo)
    before = _snapshot(git_repo, worktree)
    foreign = finish_cli(worktree, "t001", "owner-other", message)
    assert foreign.returncode != 0
    assert "执行权未领取或 owner_id 不匹配" in foreign.stdout
    assert _snapshot(git_repo, worktree) == before

    # 另一 task：start + reserve 但未 claim
    assert _task_cli(git_repo, "start", "t002").returncode == 0
    assert _task_cli(git_repo, "attempt", "reserve", "t002").returncode == 0
    other = _worktree(git_repo, "t002")
    other_message = prepare_finish(other, "t002", "beta")
    unclaimed = finish_cli(other, "t002", OWNER, other_message)
    assert unclaimed.returncode != 0
    assert "执行权未领取或 owner_id 不匹配" in unclaimed.stdout
    assert not (other / "docs/archive/tasks/t002_beta").exists()


@pytest.mark.parametrize("content", [None, "", "   \n"])
def test_finish_rejects_missing_or_empty_message_file(git_repo, content):
    _, worktree, message = _ready(git_repo)
    target = worktree.parent / "other_message.txt"
    if content is not None:
        target.write_text(content, encoding="utf-8")
    before = _snapshot(git_repo, worktree)

    result = finish_cli(worktree, "t001", OWNER, target)

    assert result.returncode != 0
    assert "--message-file" in result.stderr
    assert _snapshot(git_repo, worktree) == before


def test_finish_rejects_message_file_inside_repository(git_repo):
    _, worktree, _ = _ready(git_repo)
    message = worktree / "message.txt"
    message.write_text("feat: x\n", encoding="utf-8")
    before = _snapshot(git_repo, worktree)

    result = finish_cli(worktree, "t001", OWNER, message)

    assert result.returncode != 0
    assert "message.txt" in result.stderr
    assert _snapshot(git_repo, worktree) == before


# --------------------------------------------------------------------------
# 提交失败：整体回滚
# --------------------------------------------------------------------------


def _install_hook(repo, body):
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    os.chmod(hook, 0o755)
    return hook


def test_finish_commit_failure_rolls_back_every_write(git_repo):
    _, worktree, message = _ready(
        git_repo, mutate=lambda root: (root / "delivered.txt").write_text("d\n", encoding="utf-8"),
    )
    # 交付文件之外，task 目录里有一部分内容已被调用方暂存
    _git(worktree, "add", "docs/tasks/t001_alpha/task.md")
    hook = _install_hook(git_repo, 'echo "hook-says-no" >&2\nexit 1')
    before = _snapshot(git_repo, worktree)

    result = finish_cli(worktree, "t001", OWNER, message)

    assert result.returncode != 0
    assert "hook-says-no" in result.stderr
    assert "已全部回滚" in result.stderr
    assert _snapshot(git_repo, worktree) == before
    assert (worktree / "docs/tasks/t001_alpha/note.md").is_file()
    fm_text = (worktree / "docs/tasks/t001_alpha/note.md").read_text(encoding="utf-8")
    assert 'status: "active"' in fm_text and 'worktree: "../' in fm_text
    # 回滚后现场可直接重试
    hook.unlink()
    retry = finish_cli(worktree, "t001", OWNER, message)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert _git(worktree, "status", "--porcelain").stdout == ""


def test_finish_commit_msg_hook_rejection_also_rolls_back(git_repo):
    _, worktree, message = _ready(git_repo)
    hook = git_repo / ".git" / "hooks" / "commit-msg"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text('#!/bin/sh\necho "bad-message" >&2\nexit 1\n', encoding="utf-8")
    os.chmod(hook, 0o755)
    before = _snapshot(git_repo, worktree)

    result = finish_cli(worktree, "t001", OWNER, message)

    assert result.returncode != 0
    assert "bad-message" in result.stderr
    assert _snapshot(git_repo, worktree) == before


def test_finish_reports_incomplete_rollback_with_recovery_hint(git_repo):
    _, worktree, message = _ready(git_repo)
    # hook 失败的同时把 note.md 换成目录，使 front matter 无法还原
    _install_hook(
        git_repo,
        'rm docs/tasks/t001_alpha/note.md && mkdir docs/tasks/t001_alpha/note.md\n'
        'echo "hook-says-no" >&2\nexit 1',
    )

    result = finish_cli(worktree, "t001", OWNER, message)

    assert result.returncode != 0
    assert "回滚未完成" in result.stderr
    assert "note.md front matter 还原失败" in result.stderr
    assert "task.py recovery t001" in result.stderr


# --------------------------------------------------------------------------
# 中断：由 recovery 现有阶段检测兜底
# --------------------------------------------------------------------------


def test_recovery_detects_finish_interrupted_before_handoff(git_repo):
    _, worktree, _ = _ready(git_repo)
    mark_done(worktree, "t001")

    result = _task_cli(git_repo, "recovery", "t001")

    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["phase"] == "finish_interrupted"
    assert "不重复 finish" in state["action"]


def test_recovery_detects_finish_interrupted_before_commit(git_repo):
    identity, worktree, _ = _ready(git_repo)
    mark_done(worktree, "t001")
    handoff = {
        "tid": "t001", "attempt": identity["attempt"], "execution_id": identity["execution_id"],
        "status": "done", "branch": "t001_alpha",
        "base_sha": _git(worktree, "rev-parse", "HEAD").stdout.strip(),
        "tests": "t", "blackbox": "b", "review": "r", "pending": [], "findings": [],
    }
    (_task_dir(worktree) / "handoff.json").write_text(json.dumps(handoff), encoding="utf-8")

    state = json.loads(_task_cli(git_repo, "recovery", "t001").stdout)

    assert state["phase"] == "finished_uncommitted"
    assert "不重复 finish" in state["action"]
