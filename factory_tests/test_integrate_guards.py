"""集成前门禁回归：P1-3 隐私闭环、DS L218 merge 血缘认领、DS L219 主干独立改写。

真实 Git 仓库，触达 verify_integrate_ready / cmd_integrate / integrate-chain 生产链路。
"""
import pytest

from test_dispatch_integration import (
    git_repo, _git, _task_cli, _prepare_done, _cleanup, _identity_args, _verify,
    _read_ledger,
)
from repo_task import attempts, integration
from repo_task.context import RepoContext, TaskDataError

ACTIVE = "docs/tasks/t001_alpha"


def _leaking_branch(repo):
    def leak(worktree):
        (worktree / "leak.txt").write_text(
            "file:///Users/alice/secret\n", encoding="utf-8"
        )
    return _prepare_done(repo, "t001", "alpha", mutate=leak, terminal=False)


def test_verify_integrate_ready_reports_privacy_kind_on_tip_leak(git_repo):
    """P1-3：分支 tip 提交快照泄漏 → 统一 failure kind=privacy，不静默放行。"""
    identity, _, _ = _leaking_branch(git_repo)
    verdict, kind, detail = _verify(
        git_repo, "t001", identity["attempt"], identity["execution_id"]
    )
    assert verdict == "contract"
    assert kind == "privacy"
    assert "泄漏" in detail and "file:///Users/alice" in detail


def test_integrate_rejects_leaking_tip_before_merge(git_repo):
    """P1-3：合入前至少校验 tip tree，泄漏提交不得进入主干。"""
    identity, _, head = _leaking_branch(git_repo)
    context = RepoContext.for_root(git_repo)
    attempts.terminal_attempt(
        "t001", identity["attempt"], identity["execution_id"], "completed", repo=context
    )
    attempts.report_attempt(
        "t001", identity["attempt"], identity["execution_id"], "done",
        sha=head, repo=context,
    )
    result = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity))
    assert result.returncode != 0
    assert "泄漏提交不得合入主干" in result.stderr
    assert not [e for e in _read_ledger(git_repo) if e["event"] == "integrated"]
    assert _git(git_repo, "rev-parse", "--verify", "MERGE_HEAD", check=False).returncode != 0


def test_claimed_merge_commit_rejects_forged_core_asset_tampering(git_repo):
    """DS L218：手工/伪造 merge 夹带核心资产篡改，认领时按变更范围一致性拒绝。"""
    identity, branch, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", identity)
    _git(git_repo, "merge", "--no-ff", "--no-commit", "-m", "merge(t001): t001_alpha", branch)
    other = git_repo / "docs/tasks/t002_beta/progress.md"
    other.write_text(other.read_text("utf-8") + "\ntampered\n", encoding="utf-8")
    _git(git_repo, "add", "docs/tasks/t002_beta/progress.md")
    _git(git_repo, "commit", "-m", "merge(t001): t001_alpha")

    result = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity))
    assert result.returncode != 0
    assert "变更范围与 task 分支实现不一致" in result.stderr
    assert "docs/tasks/t002_beta/progress.md" in result.stderr
    assert not [e for e in _read_ledger(git_repo) if e["event"] == "integrated"]


def test_claimed_merge_lineage_rejects_first_parent_holding_branch_tip(git_repo):
    """DS L218：first parent 不能已含 task 分支实现，防止伪造任意主干基座。"""
    identity, branch, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", identity)
    tip = _git(git_repo, "rev-parse", branch).stdout.strip()
    assert _git(
        git_repo, "merge", "--no-ff", "-m", "merge(t001): t001_alpha", branch
    ).returncode == 0
    merge = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    tree = _git(git_repo, "rev-parse", f"{merge}^{{tree}}").stdout.strip()
    # first parent 用「位于 branch tip 之上」的提交，含 task 分支实现；git 会去重相同 parent，
    # 故须另造一个 child commit 而非直接把 tip 同时作为两个 parent。
    extra = _git(
        git_repo, "commit-tree", tree, "-p", tip, "-m", "extra on branch"
    ).stdout.strip()
    forged = _git(
        git_repo, "commit-tree", tree, "-p", extra, "-p", tip,
        "-m", "merge(t001): t001_alpha",
    ).stdout.strip()
    _git(git_repo, "update-ref", "refs/heads/main", forged)
    with pytest.raises(TaskDataError, match="first parent 已含 task 分支实现"):
        integration._validate_merge_lineage(
            forged, tip, repo=RepoContext.for_root(git_repo)
        )


def test_main_independent_rewrite_reports_command_and_continue_succeeds(git_repo):
    """DS L219：Git 干净合并的主干独立改写须区分报告并给出可执行命令，修复后 --continue 成功。"""
    identity, branch, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", identity)
    spec = git_repo / ACTIVE / "task.md"
    spec.write_text(spec.read_text("utf-8") + "\n主干独立追加说明\n", encoding="utf-8")
    _git(git_repo, "add", ACTIVE + "/task.md")
    _git(git_repo, "commit", "-m", "main edits spec tail")
    tip_spec = _git(git_repo, "show", f"{branch}:{ACTIVE}/task.md").stdout
    command = f"git checkout {branch} -- {ACTIVE}/task.md"

    prepared = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity))
    assert prepared.returncode != 0
    assert "独立改写" in prepared.stderr and command in prepared.stderr
    assert "原生冲突" not in prepared.stderr
    assert _git(git_repo, "diff", "--name-only", "--diff-filter=U", check=False).stdout.strip() == ""

    unfixed = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity), "--continue")
    assert unfixed.returncode != 0
    assert "未取分支实现内容" in unfixed.stderr and command in unfixed.stderr

    _git(git_repo, "checkout", branch, "--", ACTIVE + "/task.md")
    finished = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity), "--continue")
    assert finished.returncode == 0, finished.stderr
    merge = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    assert _git(git_repo, "show", f"{merge}:{ACTIVE}/task.md").stdout == tip_spec


def test_main_independent_new_member_file_reports_git_rm(git_repo):
    """DS L219：主干新增、分支不存在的成员路径，修复命令为 git rm 且可走通。"""
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", identity)
    extra = git_repo / ACTIVE / "main_only.md"
    extra.write_text("main independent note\n", encoding="utf-8")
    _git(git_repo, "add", ACTIVE + "/main_only.md")
    _git(git_repo, "commit", "-m", "main adds member file")

    prepared = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity))
    assert prepared.returncode != 0
    assert "独立改写" in prepared.stderr
    assert f"git rm -f -- {ACTIVE}/main_only.md" in prepared.stderr
    _git(git_repo, "rm", "-f", "--", ACTIVE + "/main_only.md")
    finished = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity), "--continue")
    assert finished.returncode == 0, finished.stderr
    assert not (git_repo / ACTIVE / "main_only.md").exists()


def test_native_merge_conflict_is_reported_as_conflict(git_repo):
    """DS L219：真原生行级冲突仍按「解决冲突并 git add」报告，不误判为主干独立改写。"""
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", identity)
    spec = git_repo / ACTIVE / "task.md"
    spec.write_text(
        spec.read_text("utf-8").replace("- [ ] AC-001", "- [ ] AC-001 主干改写"),
        encoding="utf-8",
    )
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "main edit task.md")

    prepared = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity))
    assert prepared.returncode != 0
    assert "原生冲突" in prepared.stderr
    assert "解决冲突并 git add" in prepared.stderr
    assert ACTIVE + "/task.md" in prepared.stderr
    _git(git_repo, "merge", "--abort", check=False)
