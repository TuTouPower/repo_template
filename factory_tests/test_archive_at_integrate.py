"""D1：归档从 finish 移到 integrate。

finish 只在原位（docs/tasks/）写 done、生成 handoff.json 并提交；integrate /
integrate-chain 在 `git merge --no-ff --no-commit` 之后把成员目录 git mv 进归档，
与派生 index 一起进入同一个 merge commit。
"""

import json

import pytest

from test_dispatch_integration import (
    _cleanup, _git, _identity_args, _prepare_done, _read_ledger, _task_cli, _worktree, git_repo,
)
from test_finish_atomic import OWNER, _begin
from task_flow_support import finish_cli, prepare_finish
from repo_task.documents import parse_front_matter, write_front_matter

ACTIVE = "docs/tasks/t001_alpha"
ARCHIVE = "docs/archive/tasks/t001_alpha"


def _merge_changes(repo, rev="HEAD"):
    """merge commit 相对 first parent 的 {路径: 状态}（不做 rename 检测）。"""
    out = _git(repo, "diff", "--no-renames", "--name-status", f"{rev}^1", rev).stdout
    return {line.split("\t", 1)[1]: line.split("\t", 1)[0] for line in out.splitlines()}


def _integrated(repo, tid, identity):
    """cleanup 后走完 integrate + --continue，返回 merge commit。"""
    _cleanup(repo, tid, identity)
    prepared = _task_cli(repo, "integrate", tid, *_identity_args(identity))
    assert prepared.returncode == 0, prepared.stderr
    finished = _task_cli(repo, "integrate", tid, *_identity_args(identity), "--continue")
    assert finished.returncode == 0, finished.stderr
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


# --------------------------------------------------------------------------
# finish：原位 done，不再归档
# --------------------------------------------------------------------------


def test_finish_keeps_task_dir_in_place_with_status_done(git_repo):
    _begin(git_repo)
    worktree = _worktree(git_repo, "t001")
    message = prepare_finish(worktree, "t001", "alpha")

    result = finish_cli(worktree, "t001", OWNER, message)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (worktree / ACTIVE).is_dir()
    assert not (worktree / ARCHIVE).exists()
    fm, _ = parse_front_matter(worktree / ACTIVE / "progress.md")
    assert fm["status"] == "done"
    assert fm["branch"] == "t001_alpha"
    assert fm["worktree"] == ""
    assert "worktree 未移除" not in fm["note"]
    committed = _git(worktree, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert f"{ACTIVE}/handoff.json" in committed
    assert not [path for path in committed if path.startswith("docs/archive/")]
    assert "docs/tasks_index.json" not in committed
    assert _git(worktree, "status", "--porcelain").stdout == ""


def test_finish_is_rejected_outside_the_task_worktree(git_repo):
    _begin(git_repo)
    worktree = _worktree(git_repo, "t001")
    message = prepare_finish(worktree, "t001", "alpha")

    primary = _task_cli(
        git_repo, "finish", "t001", "--owner-id", OWNER, "--message-file", str(message)
    )

    assert primary.returncode != 0
    assert "status=backlog" in primary.stderr


def test_done_in_active_dir_is_not_executable_and_listed_as_done(git_repo):
    _begin(git_repo)
    worktree = _worktree(git_repo, "t001")
    message = prepare_finish(worktree, "t001", "alpha")
    assert finish_cli(worktree, "t001", OWNER, message).returncode == 0

    # worktree 内 list 读到原位 done
    listed = _task_cli(worktree, "list", "--status", "done")
    assert listed.returncode == 0 and "t001" in listed.stdout
    # 主仓有效状态：来自 worktree 的 done
    effective = _task_cli(git_repo, "effective-status")
    assert effective.returncode == 0, effective.stderr
    row = next(line for line in effective.stdout.splitlines() if line.startswith("| t001"))
    assert "done" in row and "worktree" in row
    # done 不是可执行成员：不进 plan，也不能重复 start
    plan = _task_cli(git_repo, "plan")
    assert plan.returncode == 0, plan.stderr
    assert "t001" not in plan.stdout
    restart = _task_cli(git_repo, "start", "t001")
    assert restart.returncode != 0


# --------------------------------------------------------------------------
# integrate / integrate-chain：归档 rename 与派生 index 进入同一个 merge commit
# --------------------------------------------------------------------------


def test_integrate_merge_commit_holds_archive_rename_and_indexes(git_repo):
    identity, branch, _ = _prepare_done(git_repo, "t001", "alpha")
    before = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    tip_note = _git(git_repo, "show", f"{branch}:{ACTIVE}/progress.md").stdout

    merge = _integrated(git_repo, "t001", identity)

    # 归档与派生 index 同在一个 merge commit，其 first parent 是合并前的主干
    assert _git(git_repo, "rev-parse", f"{merge}^1").stdout.strip() == before
    assert len(_git(git_repo, "rev-parse", f"{merge}^@").stdout.split()) == 2
    changes = _merge_changes(git_repo, merge)
    assert changes[f"{ACTIVE}/progress.md"] == "D"
    assert changes[f"{ARCHIVE}/progress.md"] == "A"
    assert changes[f"{ARCHIVE}/handoff.json"] == "A"
    assert changes["docs/tasks_index.json"] != "" and changes["docs/archive/tasks_index.json"] != ""
    assert not (git_repo / ACTIVE).exists()
    archive_index = json.loads((git_repo / "docs/archive/tasks_index.json").read_text("utf-8"))
    assert [item["tid"] for item in archive_index["tasks"]] == ["t001"]
    active_index = json.loads((git_repo / "docs/tasks_index.json").read_text("utf-8"))
    assert "t001" not in [item["tid"] for item in active_index["tasks"]]
    # 归档是纯移动：内容与分支 tip 一致
    assert (git_repo / ARCHIVE / "progress.md").read_text("utf-8") == tip_note
    assert _git(git_repo, "status", "--porcelain").stdout == ""
    assert len([e for e in _read_ledger(git_repo) if e["event"] == "integrated"]) == 1


def test_integrate_chain_archives_every_member_in_one_merge_commit(git_repo):
    first, first_branch, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", first)
    second, _, _ = _prepare_done(git_repo, "t002", "beta", base=first_branch)
    _cleanup(git_repo, "t002", second)
    merges_before = int(_git(git_repo, "rev-list", "--merges", "--count", "HEAD").stdout)

    assert _task_cli(git_repo, "integrate-chain", "t002").returncode == 0
    # 准备阶段已暂存归档移动，尚无 merge commit
    staged = _git(git_repo, "diff", "--cached", "--no-renames", "--name-only").stdout.split()
    assert f"{ARCHIVE}/handoff.json" in staged
    assert "docs/archive/tasks/t002_beta/handoff.json" in staged
    finished = _task_cli(git_repo, "integrate-chain", "t002", "--continue")
    assert finished.returncode == 0, finished.stderr

    assert int(_git(git_repo, "rev-list", "--merges", "--count", "HEAD").stdout) == merges_before + 1
    changes = _merge_changes(git_repo)
    for name in ("t001_alpha", "t002_beta"):
        assert changes[f"docs/tasks/{name}/progress.md"] == "D"
        assert changes[f"docs/archive/tasks/{name}/progress.md"] == "A"
    assert not (git_repo / "docs/tasks/t001_alpha").exists()
    assert not (git_repo / "docs/tasks/t002_beta").exists()
    assert (git_repo / "docs/tasks/t003_gamma/progress.md").is_file()  # 非成员 task 不动
    archive_index = json.loads((git_repo / "docs/archive/tasks_index.json").read_text("utf-8"))
    assert [item["tid"] for item in archive_index["tasks"]] == ["t001", "t002"]


def test_continue_restages_archive_move_that_was_undone_before_commit(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", identity)
    assert _task_cli(git_repo, "integrate", "t001", *_identity_args(identity)).returncode == 0
    _git(git_repo, "mv", ARCHIVE, ACTIVE)

    finished = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity), "--continue")

    assert finished.returncode == 0, finished.stderr
    assert (git_repo / ARCHIVE / "handoff.json").is_file()
    assert not (git_repo / ACTIVE).exists()
    assert _merge_changes(git_repo)[f"{ARCHIVE}/handoff.json"] == "A"


def test_manual_merge_then_integrate_archives_in_maintenance_commit(git_repo):
    identity, branch, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", identity)
    assert _git(git_repo, "merge", "--no-ff", "-m", "merge(t001): t001_alpha", branch).returncode == 0
    merge = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    assert (git_repo / ACTIVE).is_dir()

    result = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity))

    assert result.returncode == 0, result.stderr
    assert not (git_repo / ACTIVE).exists()
    assert (git_repo / ARCHIVE / "handoff.json").is_file()
    assert _git(git_repo, "rev-parse", "HEAD^1").stdout.strip() == merge
    assert _git(git_repo, "status", "--porcelain").stdout == ""
    integrated = [e for e in _read_ledger(git_repo) if e["event"] == "integrated"]
    assert integrated[0]["merge_sha"] == merge


def test_conflicted_task_doc_resolution_is_carried_into_archive(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", identity)
    # 主干在 task 运行期间改写分支打勾的同一行，制造 task.md 的 modify/modify 冲突
    spec = git_repo / ACTIVE / "task.md"
    spec.write_text(
        spec.read_text("utf-8").replace("- [ ] AC-001", "- [ ] AC-001 主干改写"), encoding="utf-8",
    )
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "main edit task.md")
    branch_text = _git(git_repo, "show", f"t001_alpha:{ACTIVE}/task.md").stdout

    prepared = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity))

    assert prepared.returncode != 0
    assert "task.md" in prepared.stderr
    assert _git(git_repo, "rev-parse", "--verify", "MERGE_HEAD", check=False).returncode == 0
    resolved = branch_text.replace("- [x] AC-001", "- [x] AC-001 人工合并")
    spec.write_text(resolved, encoding="utf-8")
    _git(git_repo, "add", ACTIVE + "/task.md")

    finished = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity), "--continue")

    assert finished.returncode == 0, finished.stderr
    assert (git_repo / ARCHIVE / "task.md").read_text("utf-8") == resolved
    assert not (git_repo / ACTIVE).exists()
    assert (git_repo / ARCHIVE / "handoff.json").is_file()


# --------------------------------------------------------------------------
# 校验边界：只豁免「派生 index + 成员目录的纯移动」，其余仍严格拒绝
# --------------------------------------------------------------------------


def _prepared_merge(repo):
    identity, _, _ = _prepare_done(repo, "t001", "alpha")
    _cleanup(repo, "t001", identity)
    assert _task_cli(repo, "integrate", "t001", *_identity_args(identity)).returncode == 0
    return identity


def _continue(repo, identity):
    return _task_cli(repo, "integrate", "t001", *_identity_args(identity), "--continue")


def _assert_rejected(repo, identity, needle):
    result = _continue(repo, identity)
    assert result.returncode != 0
    assert needle in result.stderr
    assert not [e for e in _read_ledger(repo) if e["event"] == "integrated"]
    assert _git(repo, "rev-parse", "--verify", "MERGE_HEAD", check=False).returncode == 0
    _git(repo, "merge", "--abort")


def test_extra_staged_non_member_path_is_rejected(git_repo):
    identity = _prepared_merge(git_repo)
    (git_repo / "evil.txt").write_text("not covered by review\n", encoding="utf-8")
    _git(git_repo, "add", "evil.txt")

    _assert_rejected(git_repo, identity, "evil.txt")


def test_staged_edit_of_task_side_file_is_rejected(git_repo):
    identity = _prepared_merge(git_repo)
    (git_repo / "README.md").write_text("tampered during merge\n", encoding="utf-8")
    _git(git_repo, "add", "README.md")

    _assert_rejected(git_repo, identity, "README.md")


def test_moving_a_non_member_task_dir_is_rejected(git_repo):
    identity = _prepared_merge(git_repo)
    _git(git_repo, "mv", "docs/tasks/t002_beta", "docs/archive/tasks/t002_beta")

    _assert_rejected(git_repo, identity, "t002_beta")


def test_altered_content_inside_archived_member_dir_is_rejected(git_repo):
    identity = _prepared_merge(git_repo)
    target = git_repo / ARCHIVE / "task.md"
    target.write_text(target.read_text("utf-8") + "\n额外内容\n", encoding="utf-8")
    _git(git_repo, "add", ARCHIVE + "/task.md")

    _assert_rejected(git_repo, identity, ARCHIVE + "/task.md")


def test_extra_file_inside_archived_member_dir_is_rejected(git_repo):
    identity = _prepared_merge(git_repo)
    (git_repo / ARCHIVE / "extra.md").write_text("injected\n", encoding="utf-8")
    _git(git_repo, "add", ARCHIVE + "/extra.md")

    _assert_rejected(git_repo, identity, ARCHIVE + "/extra.md")


def test_archive_target_already_occupied_is_rejected(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", identity)
    occupied = git_repo / ARCHIVE
    occupied.mkdir(parents=True)
    (occupied / "progress.md").write_text("occupied\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "occupy archive target")

    prepared = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity))

    assert prepared.returncode != 0
    assert "同时存在" in prepared.stderr or "归档" in prepared.stderr
    assert not [e for e in _read_ledger(git_repo) if e["event"] == "integrated"]


# --------------------------------------------------------------------------
# D3：依赖实现 SHA 查找同时覆盖活跃目录（新历史）与归档目录（旧历史）
# --------------------------------------------------------------------------


def _legacy_archived_history(repo):
    """旧布局历史：实现 commit 直接在归档目录添加 handoff.json，合并后删除分支。"""
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "-q", "-b", "t001_alpha")
    _git(repo, "mv", ACTIVE, ARCHIVE)
    note = repo / ARCHIVE / "progress.md"
    fm, body = parse_front_matter(note)
    fm["status"] = "done"
    write_front_matter(note, fm, body)
    (repo / ARCHIVE / "handoff.json").write_text(json.dumps({
        "tid": "t001", "attempt": 1, "execution_id": "legacy", "status": "done",
        "branch": "t001_alpha", "base_sha": base, "tests": "t", "blackbox": "b",
        "review": "r", "pending": [], "findings": [],
    }), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat(t001): legacy archived implementation")
    implementation = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--no-ff", "-q", "-m", "merge(t001): t001_alpha", "t001_alpha")
    _git(repo, "branch", "-D", "t001_alpha")
    return base, implementation


def test_dependency_implementation_found_for_handoff_added_under_active_dir(git_repo):
    from repo_task import integration

    identity, branch, implementation = _prepare_done(git_repo, "t001", "alpha")
    _integrated(git_repo, "t001", identity)
    assert not _git(git_repo, "branch", "--list", branch).stdout.strip()
    # 合并后 handoff.json 位于归档目录，但添加它的实现 commit 把它放在 docs/tasks/
    added = _git(git_repo, "show", "--name-status", "--format=", implementation).stdout
    assert f"A\t{ACTIVE}/handoff.json" in added

    assert integration._dependency_implementations("t001") == [implementation]


def test_dependency_implementation_found_for_legacy_history_added_under_archive(git_repo):
    from repo_task import integration

    _, implementation = _legacy_archived_history(git_repo)

    assert integration._dependency_implementations("t001") == [implementation]


def test_dependency_implementation_rejects_base_sha_not_matching_first_parent(git_repo):
    from repo_task import integration
    from repo_task.context import TaskDataError

    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    _integrated(git_repo, "t001", identity)
    handoff = git_repo / ARCHIVE / "handoff.json"
    payload = json.loads(handoff.read_text("utf-8"))
    payload["base_sha"] = "0" * 40
    handoff.write_text(json.dumps(payload), encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-q", "-m", "tamper handoff base_sha")

    with pytest.raises(TaskDataError, match="缺依赖实现的可验证 SHA"):
        integration._dependency_implementations("t001")
