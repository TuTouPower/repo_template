"""P1-2/P2 integrate 回归：重叠 clean merge 认领、merge 隐私门禁、落盘复验、A06 引用与 fail-closed。

真实 Git 仓库，触达 _validate_claimed_merge_commit / _commit_native_merge 生产链路。
"""
import shlex

import pytest

from test_dispatch_integration import (
    git_repo, _git, _task_cli, _prepare_done, _cleanup, _identity_args,
    _read_ledger, _verify,
)
from repo_task import context as ctx
from repo_task import integration
from repo_task.context import RepoContext, TaskDataError

ACTIVE = "docs/tasks/t001_alpha"
SHARED = "shared_overlap.txt"
DATA = "data.txt"


def _prepare_overlapping_clean_merge(git_repo):
    """构造重叠 clean merge：base 有 SHARED，主干与分支各改不同行，手动 merge 后返回身份信息。"""
    shared = git_repo / SHARED
    shared.write_text("line1\nline2\nline3\n", encoding="utf-8")
    _git(git_repo, "add", SHARED)
    _git(git_repo, "commit", "-m", "test: add shared overlap file")

    def mutate(worktree):
        target = worktree / SHARED
        target.write_text("line1 branch\nline2\nline3\n", encoding="utf-8")

    identity, branch, _ = _prepare_done(git_repo, "t001", "alpha", mutate=mutate)
    _cleanup(git_repo, "t001", identity)
    shared.write_text("line1\nline2\nline3 main\n", encoding="utf-8")
    _git(git_repo, "add", SHARED)
    _git(git_repo, "commit", "-m", "test: main edits shared tail")
    assert _git(
        git_repo, "merge", "--no-ff", "-m", "merge(t001): t001_alpha", branch
    ).returncode == 0
    merge = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    return identity, branch, merge


def test_overlapping_clean_merge_already_merged_accepted(git_repo):
    """P1-2：重叠文件干净合并后 already_merged 幂等清理须放行，不再误报 tip 不一致。"""
    identity, branch, merge = _prepare_overlapping_clean_merge(git_repo)

    result = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity))

    assert result.returncode == 0, result.stderr
    assert "跳过 merge" in result.stdout
    integrated = [e for e in _read_ledger(git_repo) if e["event"] == "integrated"]
    assert len(integrated) == 1
    assert integrated[0]["merge_sha"] == merge
    merged_text = _git(git_repo, "show", f"{merge}:{SHARED}").stdout
    assert "line1 branch" in merged_text and "line3 main" in merged_text


def test_overlapping_clean_merge_continue_reentry_accepted(git_repo):
    """P1-2：重叠干净合并 --continue 落盘后重入须复验通过（恢复可重入）。"""
    shared = git_repo / SHARED
    shared.write_text("line1\nline2\nline3\n", encoding="utf-8")
    _git(git_repo, "add", SHARED)
    _git(git_repo, "commit", "-m", "test: add shared overlap file")

    def mutate(worktree):
        (worktree / SHARED).write_text("line1 branch\nline2\nline3\n", encoding="utf-8")

    identity, branch, _ = _prepare_done(git_repo, "t001", "alpha", mutate=mutate)
    _cleanup(git_repo, "t001", identity)
    shared.write_text("line1\nline2\nline3 main\n", encoding="utf-8")
    _git(git_repo, "add", SHARED)
    _git(git_repo, "commit", "-m", "test: main edits shared tail")

    assert _task_cli(git_repo, "integrate", "t001", *_identity_args(identity)).returncode == 0
    first = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity), "--continue", "--keep-branch"
    )
    assert first.returncode == 0, first.stderr
    # 模拟崩溃后重入：merge 已落盘但调用方重放 --continue，须复验认领而非误拒。
    second = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity), "--continue", "--keep-branch"
    )
    assert second.returncode == 0, second.stderr
    assert len([e for e in _read_ledger(git_repo) if e["event"] == "integrated"]) == 1


def test_merge_changeset_privacy_leak_rejected_before_commit(git_repo):
    """P2：冲突解决内容带家目录泄漏，--continue 提交前须由变更集隐私门禁拒绝。"""
    shared = git_repo / SHARED
    shared.write_text("line1\nline2\n", encoding="utf-8")
    _git(git_repo, "add", SHARED)
    _git(git_repo, "commit", "-m", "test: add shared")

    def mutate(worktree):
        (worktree / SHARED).write_text("line1 branch\nline2\n", encoding="utf-8")

    identity, branch, _ = _prepare_done(git_repo, "t001", "alpha", mutate=mutate)
    _cleanup(git_repo, "t001", identity)
    shared.write_text("line1 main\nline2\n", encoding="utf-8")
    _git(git_repo, "add", SHARED)
    _git(git_repo, "commit", "-m", "test: main conflicting edit")

    prepared = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity))
    assert prepared.returncode != 0
    assert "原生冲突" in prepared.stderr

    shared.write_text("leak /Users/alice/secret\n", encoding="utf-8")
    _git(git_repo, "add", SHARED)
    finished = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity), "--continue")
    assert finished.returncode != 0
    assert "泄漏" in finished.stderr
    assert not [e for e in _read_ledger(git_repo) if e["event"] == "integrated"]
    _git(git_repo, "merge", "--abort", check=False)


def test_commit_hook_tree_rewrite_rejected_after_commit(git_repo):
    """P2：pre-commit hook 改写已校验内容，落盘 tree 复验须拒绝认领。"""
    identity, branch, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", identity)
    assert _task_cli(git_repo, "integrate", "t001", *_identity_args(identity)).returncode == 0

    hook = git_repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(
        "#!/bin/sh\necho hook-rewrite >> hook_extra.txt\ngit add hook_extra.txt\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    try:
        finished = _task_cli(
            git_repo, "integrate", "t001", *_identity_args(identity), "--continue"
        )
    finally:
        hook.unlink(missing_ok=True)
    assert finished.returncode != 0
    assert "落盘 tree 与已校验 staged tree 不一致" in finished.stderr
    assert not [e for e in _read_ledger(git_repo) if e["event"] == "integrated"]
    _git(git_repo, "merge", "--abort", check=False)


def test_main_rewrite_command_quotes_special_chars(git_repo):
    """P2 A06：修复命令须 shlex.quote 路径与 ref，含空格路径可直接执行。"""
    context = RepoContext.for_root(git_repo)
    branch = "t001_alpha"
    quoted_path = f"{ACTIVE}/a b.md"
    command = integration._main_rewrite_command(quoted_path, branch, repo=context)
    assert command == f"git rm -f -- {shlex.quote(quoted_path)}"
    existing = integration._main_rewrite_command(f"{ACTIVE}/task.md", "main", repo=context)
    assert existing == f"git checkout {shlex.quote('main')} -- {shlex.quote(f'{ACTIVE}/task.md')}"


def test_member_paths_changed_between_fail_closed(git_repo):
    """P2 A06：成员变更比较失败须抛 TaskDataError，绝不返回空集合静默放行。"""
    context = RepoContext.for_root(git_repo)
    with pytest.raises(TaskDataError, match="无法比较"):
        integration._member_paths_changed_between(
            "0" * 40, "HEAD", ["t001_alpha"], repo=context
        )
    with pytest.raises(TaskDataError, match="无法解析 merge-base"):
        integration._main_touched_member_paths(
            "refs/heads/nonexistent-branch-xyz", ["t001_alpha"], repo=context
        )


def _prepare_a06_member_overlap_merge(git_repo):
    """真实 git：base 含成员 data.txt，feature 改 line1、main 改 line5 干净合并，
    按 A06 取分支 tip。返回 (branch, branch_tip, data_path, auto_tree)。"""
    data_path = f"{ACTIVE}/{DATA}"
    data = git_repo / data_path
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text("line1\nline2\nline3\nline4\nline5\n", encoding="utf-8")
    _git(git_repo, "add", data_path)
    _git(git_repo, "commit", "-m", "test: base adds member data.txt")

    def mutate(worktree):
        (worktree / data_path).write_text(
            "line1 branch\nline2\nline3\nline4\nline5\n", encoding="utf-8"
        )

    identity, branch, tip = _prepare_done(git_repo, "t001", "alpha", mutate=mutate)
    _cleanup(git_repo, "t001", identity)

    data.write_text("line1\nline2\nline3\nline4\nline5 main\n", encoding="utf-8")
    _git(git_repo, "add", data_path)
    _git(git_repo, "commit", "-m", "test: main edits member data.txt tail")

    merged = _git(git_repo, "merge", "--no-commit", "--no-ff", branch, check=False)
    assert merged.returncode == 0, merged.stderr
    context = RepoContext.for_root(git_repo)
    auto_tree, conflicts = integration._expected_auto_merge(tip, repo=context)
    assert not conflicts
    auto_text = _git(git_repo, "show", f"{auto_tree}:{data_path}").stdout
    assert "line1 branch" in auto_text and "line5 main" in auto_text

    _git(git_repo, "checkout", branch, "--", data_path)
    return branch, tip, data_path, auto_tree


def test_a06_member_overlap_merge_claim_matches_precommit(git_repo):
    """P1-6：提交前放行的 A06 重叠 clean merge，认领同一 merge commit 须一致通过。"""
    branch, tip, data_path, auto_tree = _prepare_a06_member_overlap_merge(git_repo)
    context = RepoContext.for_root(git_repo)
    main_touched = set(
        integration._main_touched_member_paths(tip, [branch], repo=context)
    )
    assert data_path in main_touched

    staged_tree = integration._validate_merge_staged_scope(
        tip, [branch], member_tips={branch: tip}, repo=context
    )
    assert staged_tree

    _git(git_repo, "commit", "-m", "merge(t001): t001_alpha")
    merge = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    merge_tree = _git(git_repo, "rev-parse", f"{merge}^{{tree}}").stdout.strip()
    assert _git(git_repo, "show", f"{merge}:{data_path}").stdout == _git(
        git_repo, "show", f"{tip}:{data_path}"
    ).stdout

    integration._validate_claimed_merge_commit(
        merge, tip, names=[branch], member_tips={branch: tip}, repo=context
    )

    divergence = {
        path for path in _git(
            git_repo, "diff", "--no-renames", "--name-only", "-z", auto_tree, merge_tree
        ).stdout.split("\0") if path
    }
    assert data_path in divergence
    integration._validate_automated_merge_result(divergence, set(), main_touched)
    with pytest.raises(TaskDataError, match="自动合并结果"):
        integration._validate_automated_merge_result(divergence, set(), set())
