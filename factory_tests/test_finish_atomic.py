"""C3：finish 原子化 —— 前置检查零写入、机器生成 handoff.json、提交失败整体回滚。"""

import argparse
import contextlib
import io
import json
import os
import re
from pathlib import Path

import pytest

from test_dispatch_integration import git_repo, _git, _task_cli, _worktree, _read_ledger
from task_flow_support import mark_done, fill_summary, finish_cli, prepare_finish
from review_support import pass_report
from repo_task import control
from repo_task import monitoring
from repo_task import context as ctx
from repo_task.documents import parse_front_matter

OWNER = "owner-fin"


def _begin(repo, tid="t001", owner=OWNER):
    context = ctx.RepoContext.for_root(repo)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        control.cmd_run_begin(argparse.Namespace(
            tid=tid, owner_id=owner, base=None, repo=context
        ))
    return json.loads(buf.getvalue())


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
            "docs/pending/p001_new_item.md",
            "docs/pending/p003_parked_item.md",
            "docs/pending/p002_closed_item.md",
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
        "spec_source_sha": _git(git_repo, "rev-parse", "HEAD").stdout.strip(),
        "tests": "pytest -q 通过",
        "blackbox": "无黑盒",
        "review": "PASS",
        "review_scope": monitoring.review_scope_fingerprint(
            anchor, "docs/tasks/t001_alpha", repo_root=worktree, ref="HEAD"
        ),
        "pending": ["p001", "p002", "p003"],
        "findings": ["d001"],
    }
    # 与既有验证契约同源：可被 verify_integrate_ready 接受
    verdict, kind, detail = monitoring.verify_integrate_ready(
        "t001", identity["attempt"], identity["execution_id"],
        repo=ctx.RepoContext.for_root(git_repo),
    )
    assert verdict == "ready", detail


def test_finish_handoff_lists_only_entries_added_after_base(git_repo):
    for rel in ("docs/pending/p005_old.md", "docs/findings/d009_old.md"):
        path = git_repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# old\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "seed existing ledger entries")
    identity = _begin(git_repo)
    worktree = _worktree(git_repo, "t001")

    def touch(root):
        # base 已存在的条目只被修改或归档迁移：不算本 task 新增
        (root / "docs/pending/p005_old.md").write_text("# edited\n", encoding="utf-8")
        (root / "docs/findings/d009_old.md").write_text("# edited\n", encoding="utf-8")
        (root / "docs/pending/p006_new.md").write_text("# new\n", encoding="utf-8")

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
    assert (git_repo / "docs/tasks/t001_alpha/handoff.json").is_file()


def test_main_path_with_worktree_include_links_keeps_links_out_of_commit(git_repo):
    """.worktreeinclude 链入的忽略文件与目录不进实现 commit，run-close 清理后主仓目标完好。"""
    gitignore = git_repo / ".gitignore"
    gitignore.write_text(gitignore.read_text(encoding="utf-8") + "/data/\n.env.local\n", encoding="utf-8")
    (git_repo / ".worktreeinclude").write_text("/data/\n.env.local\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "worktree include")
    (git_repo / "data").mkdir()
    (git_repo / "data/state.db").write_text("rows\n", encoding="utf-8")
    (git_repo / ".env.local").write_text("LOCAL=1\n", encoding="utf-8")

    _, worktree, message = _ready(git_repo)
    assert (worktree / "data").is_symlink() and (worktree / ".env.local").is_symlink()
    finished = finish_cli(worktree, "t001", OWNER, message)
    assert finished.returncode == 0, finished.stdout + finished.stderr
    committed = _git(worktree, "show", "--name-only", "--format=", "HEAD").stdout.splitlines()
    assert "data" not in committed and ".env.local" not in committed

    closed = _task_cli(git_repo, "run-close", "t001", "--owner-id", OWNER, "--result", "done")
    assert closed.returncode == 0, closed.stderr
    assert not worktree.exists()
    assert (git_repo / "data/state.db").read_text(encoding="utf-8") == "rows\n"
    assert (git_repo / ".env.local").read_text(encoding="utf-8") == "LOCAL=1\n"


# --------------------------------------------------------------------------
# handoff 的 tests / blackbox / review 来自 progress.md
# --------------------------------------------------------------------------


@pytest.mark.parametrize("label", ["测试", "黑盒", "review"])
@pytest.mark.parametrize("value", ["待执行时填写", ""])
def test_finish_rejects_placeholder_or_empty_verification_summary(git_repo, label, value):
    _, worktree, message = _ready(git_repo)
    note = worktree / "docs/tasks/t001_alpha/progress.md"
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
    note = worktree / "docs/tasks/t001_alpha/progress.md"
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
    assert (worktree / "docs/tasks/t001_alpha/progress.md").is_file()


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
    assert (worktree / "docs/tasks/t001_alpha/progress.md").is_file()
    fm_text = (worktree / "docs/tasks/t001_alpha/progress.md").read_text(encoding="utf-8")
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


def test_finish_destructive_hook_reports_unknown_and_keeps_journal(git_repo):
    """A01：hook 破坏现场后 HEAD 未前进但现场偏离事务期望 → 结果未知，保留 journal，不自动回滚。"""
    _, worktree, message = _ready(git_repo)
    # hook 失败的同时把 progress.md 换成目录，使现场偏离事务期望且无法确定归属
    _install_hook(
        git_repo,
        'rm docs/tasks/t001_alpha/progress.md && mkdir docs/tasks/t001_alpha/progress.md\n'
        'echo "hook-says-no" >&2\nexit 1',
    )

    result = finish_cli(worktree, "t001", OWNER, message)

    assert result.returncode != 0
    assert "提交结果未知" in result.stderr
    assert "已保留 journal 与现场" in result.stderr
    assert (worktree / "docs/tasks/t001_alpha/progress.md").is_dir()
    state = json.loads(_task_cli(git_repo, "recovery", "t001").stdout)
    assert state["phase"] == "finish_txn_unknown"
    assert "不重复提交" in state["action"] or "人工核查" in state["action"]


def test_transaction_judge_reports_committed_when_head_advanced(git_repo):
    """A01：HEAD 已前进时 judge 判 committed，恢复路径不得回滚。"""
    from repo_task import commit_txn

    _, worktree, message = _ready(git_repo)
    assert finish_cli(worktree, "t001", OWNER, message).returncode == 0
    head = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    parent = _git(worktree, "rev-parse", "HEAD^1").stdout.strip()
    journal = {
        "version": commit_txn.JOURNAL_VERSION,
        "tid": "t001", "attempt": 1, "execution_id": "x", "branch": "t001_alpha",
        "diff_anchor": parent, "review_scope": "x", "task_rel": "docs/tasks/t001_alpha",
        "message_file": str(message), "original_head": parent,
        "index_existed": True, "files": {}, "phase": "committing",
        "expected_tree": "", "expected_files": {},
    }
    verdict, detail = commit_txn.judge(worktree, journal)
    assert verdict == "committed"
    assert head[:12] in detail


def test_leftover_transaction_with_git_lock_reports_unknown_without_rollback(git_repo):
    """A01：Git 锁仍在表示子进程可能未结束 → 结果未知，保留现场；recovery 如实报告。"""
    from repo_task import commit_txn

    _, worktree, message = _ready(git_repo)
    git_dir = Path(_git(worktree, "rev-parse", "--absolute-git-dir").stdout.strip())
    txn = commit_txn.begin(
        worktree, tid="t001", attempt=1, execution_id="x", branch="t001_alpha",
        diff_anchor=_git(worktree, "rev-parse", "HEAD").stdout.strip(),
        review_scope="x", task_rel="docs/tasks/t001_alpha", message_file=str(message),
    )
    (git_dir / "index.lock").write_text("", encoding="utf-8")

    verdict, detail = commit_txn.judge(worktree, txn)
    assert verdict == "unknown"
    assert "index.lock" in detail

    state = json.loads(_task_cli(git_repo, "recovery", "t001").stdout)
    assert state["phase"] == "finish_txn_unknown"
    assert "人工核查" in state["action"]


def test_finish_retry_idempotently_rolls_back_leftover_safe_transaction(git_repo):
    """A01：HEAD 未前进且现场未改的中断事务，重跑 finish 先幂等回滚再继续。"""
    _, worktree, message = _ready(git_repo)
    hook = _install_hook(git_repo, 'echo "hook-says-no" >&2\nexit 1')
    assert finish_cli(worktree, "t001", OWNER, message).returncode != 0
    # 上次失败已安全回滚并清理 journal
    assert json.loads(_task_cli(git_repo, "recovery", "t001").stdout)["phase"] != "finish_txn_unknown"
    hook.unlink()
    retry = finish_cli(worktree, "t001", OWNER, message)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert _git(worktree, "status", "--porcelain").stdout == ""


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
    base = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    handoff = {
        "tid": "t001", "attempt": identity["attempt"], "execution_id": identity["execution_id"],
        "status": "done", "branch": "t001_alpha",
        "base_sha": base,
        "spec_source_sha": _git(git_repo, "rev-parse", "HEAD").stdout.strip(),
        "tests": "t", "blackbox": "b", "review": "r", "pending": [], "findings": [],
        "review_scope": monitoring.review_scope_fingerprint(
            base, "docs/tasks/t001_alpha", repo_root=worktree
        ),
    }
    (_task_dir(worktree) / "handoff.json").write_text(json.dumps(handoff), encoding="utf-8")

    state = json.loads(_task_cli(git_repo, "recovery", "t001").stdout)

    assert state["phase"] == "finished_uncommitted"
    assert "不重复 finish" in state["action"]

# --------------------------------------------------------------------------
# M01：提交前后都绑定 review 内容；A03/A04/A05：零写入前置检查
# --------------------------------------------------------------------------


def _fake_md_kx(tmp_path):
    script = (
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        "path = Path(sys.argv[-1])\n"
        "text = path.read_text(encoding='utf-8')\n"
        "out = text.replace('|------|------|', '|---|---|')\n"
        "if '--check' in sys.argv:\n"
        "    sys.exit(0 if out == text else 1)\n"
        "path.write_text(out, encoding='utf-8')\n"
    )
    bin_dir = tmp_path / "bin_real_md"
    bin_dir.mkdir(exist_ok=True)
    exe = bin_dir / "md_kx"
    exe.write_text(script, encoding="utf-8")
    exe.chmod(0o755)
    return bin_dir


def test_finish_rejects_unformatted_markdown_when_template_hooks_enabled(
    git_repo, tmp_path, monkeypatch,
):
    bin_dir = _fake_md_kx(tmp_path)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    _git(git_repo, "config", "core.hooksPath", ".repo_template/hooks")
    _begin(git_repo)
    worktree = _worktree(git_repo, "t001")

    def deliver(root):
        (root / "notes.md").write_text(
            "|a|b|\n|------|------|\n|1|2|\n", encoding="utf-8",
        )

    message = prepare_finish(worktree, "t001", "alpha", mutate=deliver)
    before = _snapshot(git_repo, worktree)

    result = finish_cli(worktree, "t001", OWNER, message)

    assert result.returncode != 0
    assert "Markdown 格式漂移" in result.stderr
    assert "notes.md" in result.stderr
    assert "md_format.py --changed" in result.stderr
    assert _snapshot(git_repo, worktree) == before


def test_finish_rejects_missing_md_kx_when_template_hooks_enabled(git_repo, monkeypatch):
    _, worktree, message = _ready(git_repo)
    monkeypatch.setenv("PATH", os.pathsep.join(
        path for path in os.environ.get("PATH", "").split(os.pathsep)
        if path and not (Path(path) / "md_kx").exists()
    ))
    _git(git_repo, "config", "core.hooksPath", ".repo_template/hooks")
    before = _snapshot(git_repo, worktree)

    result = finish_cli(worktree, "t001", OWNER, message)

    assert result.returncode != 0
    assert "找不到 md_kx" in result.stderr
    assert _snapshot(git_repo, worktree) == before


def test_finish_reports_fail_when_hook_rewrites_delivered_markdown(git_repo):
    _, worktree, message = _ready(git_repo)
    before_head = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    _install_hook(
        git_repo,
        "printf '\\nhook rewrite\\n' >> docs/tasks/t001_alpha/task.md\n"
        "git add docs/tasks/t001_alpha/task.md",
    )

    result = finish_cli(worktree, "t001", OWNER, message)

    assert result.returncode != 0
    assert "finish=FAIL：提交内容与 review 内容不一致" in result.stderr
    assert "finish=OK" not in result.stdout
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() != before_head
    assert "hook rewrite" in _git(worktree, "show", "HEAD:docs/tasks/t001_alpha/task.md").stdout


def test_finish_done_rejects_occupied_archive_target_with_zero_writes(git_repo):
    """拦截对其他 task 核心资产的篡改，零写入退出。"""
    _, worktree, message = _ready(git_repo)
    other_task_md = worktree / "docs/tasks/t002_beta/task.md"
    other_task_md.write_text("# tampered\n", encoding="utf-8")
    _git(worktree, "add", "docs/tasks/t002_beta/task.md")
    before = _snapshot(git_repo, worktree)

    result = finish_cli(worktree, "t001", OWNER, message)

    assert result.returncode != 0
    assert "禁止修改其他 task 核心资产" in result.stderr
    assert _snapshot(git_repo, worktree) == before


def test_finish_gate_failure_reports_ac_review_detail(git_repo):
    _, worktree, message = _ready(git_repo)
    task_dir = _task_dir(worktree)
    fm, _ = parse_front_matter(task_dir / "progress.md")
    scope = monitoring.review_scope_fingerprint(
        fm["diff_anchor"], "docs/tasks/t001_alpha", repo_root=worktree,
    )
    spec_text = (task_dir / "task.md").read_text(encoding="utf-8")
    for name in ("review_code.md", "review_test.md"):
        (task_dir / name).write_text(
            pass_report(scope, spec_text, round_no=2, tick="TICK-VOID"), encoding="utf-8",
        )
    before = _snapshot(git_repo, worktree)

    result = finish_cli(worktree, "t001", OWNER, message)

    assert result.returncode != 0
    assert "overall=FAIL" in result.stderr
    assert "ac_review=" in result.stderr and "ac_review_detail=" in result.stderr
    assert "TICK-VOID" in result.stderr
    assert _snapshot(git_repo, worktree) == before


def test_finish_rejects_message_file_inside_task_directory(git_repo):
    _, worktree, _ = _ready(git_repo)
    message = _task_dir(worktree) / "message.txt"
    message.write_text("feat: x\n", encoding="utf-8")
    before = _snapshot(git_repo, worktree)

    result = finish_cli(worktree, "t001", OWNER, message)

    assert result.returncode != 0
    assert "位于当前 task 目录内" in result.stderr
    assert _snapshot(git_repo, worktree) == before


def test_finish_rejects_developer_home_path_in_review_report_and_task_docs(git_repo):
    """Issue #89：review_*.md 或 task/docs 文档含开发者家目录绝对路径时，finish 零写入阻断。"""
    _, worktree, message = _ready(git_repo)
    task_dir = _task_dir(worktree)
    leak = "/".join(("", "Users", "devuser", "project_t001"))
    report = task_dir / "review_code.md"
    clean_report = report.read_text(encoding="utf-8")
    report.write_text(f"- target：`git -C '{leak}' diff abc`\n\n" + clean_report, encoding="utf-8")
    before = _snapshot(git_repo, worktree)

    first = finish_cli(worktree, "t001", OWNER, message)

    assert first.returncode != 0
    assert "privacy_review=leak" in first.stderr
    assert "review_code.md:1:" in first.stderr
    assert "next_action=sanitize_review_paths" in first.stderr
    assert _snapshot(git_repo, worktree) == before

    # 修复 review_code.md 后，若 progress.md 仍含家目录绝对路径，同样被 _check_privacy_leaks 拦截
    report.write_text(clean_report, encoding="utf-8")
    note_path = task_dir / "progress.md"
    clean_note = note_path.read_text(encoding="utf-8")
    note_path.write_text(clean_note + f"\n临时路径：{leak}/out\n", encoding="utf-8")
    before_note = _snapshot(git_repo, worktree)

    second = finish_cli(worktree, "t001", OWNER, message)

    assert second.returncode != 0
    assert "检测到开发者家目录绝对路径泄漏" in second.stderr
    assert "docs/tasks/t001_alpha/progress.md" in second.stderr
    assert _snapshot(git_repo, worktree) == before_note

    # 清理绝对路径后，finish 正常成功
    note_path.write_text(clean_note, encoding="utf-8")
    third = finish_cli(worktree, "t001", OWNER, message)
    assert third.returncode == 0, third.stdout + third.stderr
