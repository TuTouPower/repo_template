"""D1：原位保留与集成验证。

finish 在原位（docs/tasks/）写 done、生成 handoff.json 并提交；integrate /
integrate-chain 在 `git merge --no-ff` 之后保持成员目录在 docs/tasks/ 原位，杜绝归档搬移与派生 index。
"""

import json

import pytest

from test_dispatch_integration import (
    _cleanup, _git, _identity_args, _prepare_done, _read_ledger, _task_cli, _worktree, git_repo,
)
from repo_task.context import RepoContext
from test_finish_atomic import OWNER, _begin
from review_support import valid_spec
from task_flow_support import finish_cli, prepare_finish
from repo_task.documents import parse_front_matter, write_front_matter
from repo_task.monitoring import review_scope_fingerprint

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
# integrate / integrate-chain：所有成员目录保留在 docs/tasks/ 原位，无物理归档
# --------------------------------------------------------------------------


def test_integrate_merge_commit_keeps_task_in_place_without_archive(git_repo):
    identity, branch, _ = _prepare_done(git_repo, "t001", "alpha")
    before = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    tip_note = _git(git_repo, "show", f"{branch}:{ACTIVE}/progress.md").stdout

    merge = _integrated(git_repo, "t001", identity)

    assert _git(git_repo, "rev-parse", f"{merge}^1").stdout.strip() == before
    assert len(_git(git_repo, "rev-parse", f"{merge}^@").stdout.split()) == 2
    changes = _merge_changes(git_repo, merge)
    assert changes[f"{ACTIVE}/progress.md"] == "M"
    assert changes[f"{ACTIVE}/handoff.json"] == "A"
    assert not [path for path in changes if path.startswith("docs/archive/")]
    assert (git_repo / ACTIVE).is_dir()
    assert (git_repo / ACTIVE / "progress.md").is_file()
    assert (git_repo / ACTIVE / "handoff.json").is_file()
    assert (git_repo / ACTIVE / "progress.md").read_text("utf-8") == tip_note
    assert not (git_repo / "docs/archive/tasks/t001_alpha").exists()
    assert _git(git_repo, "status", "--porcelain").stdout == ""
    assert len([e for e in _read_ledger(git_repo) if e["event"] == "integrated"]) == 1


def test_integrate_chain_keeps_all_members_in_place_without_archive(git_repo):
    first, first_branch, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", first)
    second, _, _ = _prepare_done(git_repo, "t002", "beta", base=first_branch)
    _cleanup(git_repo, "t002", second)
    merges_before = int(_git(git_repo, "rev-list", "--merges", "--count", "HEAD").stdout)

    assert _task_cli(git_repo, "integrate-chain", "t002").returncode == 0
    # 准备阶段：所有成员留在 docs/tasks/
    staged = _git(git_repo, "diff", "--cached", "--no-renames", "--name-only").stdout.split()
    assert "docs/tasks/t001_alpha/handoff.json" in staged
    assert "docs/tasks/t002_beta/handoff.json" in staged
    assert not [p for p in staged if p.startswith("docs/archive/")]
    finished = _task_cli(git_repo, "integrate-chain", "t002", "--continue")
    assert finished.returncode == 0, finished.stderr

    assert int(_git(git_repo, "rev-list", "--merges", "--count", "HEAD").stdout) == merges_before + 1
    for name in ("t001_alpha", "t002_beta"):
        assert (git_repo / "docs/tasks" / name / "handoff.json").is_file()
        assert (git_repo / "docs/tasks" / name / "progress.md").is_file()
        assert not (git_repo / "docs/archive/tasks" / name).exists()


def test_integrate_continue_rejects_tampered_core_asset_before_commit(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", identity)
    assert _task_cli(git_repo, "integrate", "t001", *_identity_args(identity)).returncode == 0
    handoff = git_repo / ACTIVE / "handoff.json"
    handoff.write_text(json.dumps({"tampered": True}), encoding="utf-8")
    _git(git_repo, "add", ACTIVE + "/handoff.json")

    finished = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity), "--continue")
    assert finished.returncode != 0
    assert "偏离 Git 自动合并结果" in finished.stderr or "核心资产与已验证实现 tip 不一致" in finished.stderr


def test_manual_merge_then_integrate_keeps_in_place_without_extra_commit(git_repo):
    identity, branch, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", identity)
    assert _git(git_repo, "merge", "--no-ff", "-m", "merge(t001): t001_alpha", branch).returncode == 0
    merge = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    assert (git_repo / ACTIVE).is_dir()

    result = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity))

    assert result.returncode == 0, result.stderr
    assert (git_repo / ACTIVE).is_dir()
    assert (git_repo / ACTIVE / "handoff.json").is_file()
    # 没有产生多余归档 commit，HEAD 保持为 merge commit
    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == merge
    assert _git(git_repo, "status", "--porcelain").stdout == ""
    integrated = [e for e in _read_ledger(git_repo) if e["event"] == "integrated"]
    assert integrated[0]["merge_sha"] == merge


def test_conflicted_task_doc_resolution_diverging_from_tip_is_rejected(git_repo):
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

    # 门禁严格拦截：原始冲突路径也不得在 merge 期间修改成员核心资产
    assert finished.returncode != 0
    assert "核心资产与已验证实现 tip 不一致" in finished.stderr


# --------------------------------------------------------------------------
# 校验边界：除集成必需的核心资产与合并变更外，其余非预期改动严格拒绝
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


def test_altered_content_inside_member_dir_is_rejected(git_repo):
    identity = _prepared_merge(git_repo)
    target = git_repo / ACTIVE / "task.md"
    target.write_text(target.read_text("utf-8") + "\n额外内容\n", encoding="utf-8")
    _git(git_repo, "add", ACTIVE + "/task.md")

    _assert_rejected(git_repo, identity, ACTIVE + "/task.md")


def test_extra_file_inside_member_dir_is_rejected(git_repo):
    identity = _prepared_merge(git_repo)
    (git_repo / ACTIVE / "extra.md").write_text("injected\n", encoding="utf-8")
    _git(git_repo, "add", ACTIVE + "/extra.md")

    _assert_rejected(git_repo, identity, ACTIVE + "/extra.md")


def test_tampering_non_member_core_asset_during_merge_is_rejected(git_repo):
    identity = _prepared_merge(git_repo)
    other = git_repo / "docs/tasks/t002_beta"
    (other / "progress.md").write_text("tampered\n", encoding="utf-8")
    _git(git_repo, "add", "docs/tasks/t002_beta/progress.md")

    _assert_rejected(git_repo, identity, "docs/tasks/t002_beta/progress.md")


# --------------------------------------------------------------------------
# D3：依赖实现 SHA 查找按 Git 对象与 commit 血缘定位
# --------------------------------------------------------------------------


def _legacy_archived_history(repo):
    """旧布局历史：实现 commit 在旧路径添加 handoff.json，主干迁移后在规范路径，通过对象 OID 定位。"""
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "-q", "-b", "t001_alpha")
    old_path = "docs/archive/tasks/t001_alpha"
    (repo / old_path).mkdir(parents=True, exist_ok=True)
    handoff_content = json.dumps({
        "tid": "t001", "attempt": 1, "execution_id": "legacy", "status": "done",
        "branch": "t001_alpha", "base_sha": base, "spec_source_sha": base,
        "tests": "t", "blackbox": "b", "review": "r", "pending": [], "findings": [],
        "review_scope": "legacy_scope_1234",
    }, indent=2) + "\n"
    (repo / old_path / "handoff.json").write_text(handoff_content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat(t001): legacy implementation commit")
    implementation = _git(repo, "rev-parse", "HEAD").stdout.strip()

    _git(repo, "checkout", "-q", "main")
    # 主干合并并迁移到规范固定路径 docs/tasks/t001_alpha/
    _git(repo, "merge", "--no-ff", "-q", "-m", "merge(t001): t001_alpha", "t001_alpha")
    (repo / ACTIVE / "handoff.json").write_text(handoff_content, encoding="utf-8")
    note = repo / ACTIVE / "progress.md"
    fm, body = parse_front_matter(note)
    fm["status"] = "done"
    write_front_matter(note, fm, body)
    if (repo / old_path).exists():
        _git(repo, "rm", "-rf", "docs/archive")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "chore(t001): migration to canonical docs/tasks/ path")
    _git(repo, "branch", "-D", "t001_alpha")
    return base, implementation


def test_dependency_implementation_found_for_handoff_added_under_active_dir(git_repo):
    from repo_task import integration

    identity, branch, implementation = _prepare_done(git_repo, "t001", "alpha")
    _integrated(git_repo, "t001", identity)
    assert not _git(git_repo, "branch", "--list", branch).stdout.strip()
    added = _git(git_repo, "show", "--name-status", "--format=", implementation).stdout
    assert f"A\t{ACTIVE}/handoff.json" in added

    assert integration._dependency_implementations(
        "t001", repo=RepoContext.for_root(git_repo)
    ) == [implementation]


def test_dependency_implementation_found_for_legacy_history_added_under_archive(git_repo):
    from repo_task import integration

    _, implementation = _legacy_archived_history(git_repo)

    assert integration._dependency_implementations(
        "t001", repo=RepoContext.for_root(git_repo)
    ) == [implementation]


def test_dependency_implementation_rejects_base_sha_not_matching_first_parent(git_repo):
    from repo_task import integration
    from repo_task.context import TaskDataError

    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    _integrated(git_repo, "t001", identity)
    handoff = git_repo / ACTIVE / "handoff.json"
    payload = json.loads(handoff.read_text("utf-8"))
    payload["base_sha"] = "0" * 40
    handoff.write_text(json.dumps(payload), encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-q", "-m", "tamper handoff base_sha")

    with pytest.raises(TaskDataError, match="缺依赖实现的可验证 SHA"):
        integration._dependency_implementations("t001", repo=RepoContext.for_root(git_repo))


@pytest.mark.parametrize("mutator", [
    lambda p: p.pop("execution_id"),
    lambda p: p.update({"execution_id": ""}),
    lambda p: p.update({"execution_id": "   "}),
    lambda p: p.pop("attempt"),
    lambda p: p.update({"attempt": 0}),
    lambda p: p.update({"attempt": -1}),
    lambda p: p.update({"attempt": True}),
    lambda p: p.update({"attempt": "1"}),
    lambda p: p.pop("spec_source_sha"),
])
def test_dependency_implementation_rejects_invalid_handoff_fields(git_repo, mutator):
    from repo_task import integration
    from repo_task.context import TaskDataError

    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    _integrated(git_repo, "t001", identity)
    handoff = git_repo / ACTIVE / "handoff.json"
    payload = json.loads(handoff.read_text("utf-8"))
    mutator(payload)
    handoff.write_text(json.dumps(payload), encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-q", "-m", "tamper handoff fields")

    with pytest.raises(TaskDataError, match="缺依赖实现的可验证 SHA"):
        integration._dependency_implementations("t001", repo=RepoContext.for_root(git_repo))


# --------------------------------------------------------------------------
# M03：链连续性回归与单 task base 已在主干
# --------------------------------------------------------------------------


def _insert_gap_commit_before_implementation(worktree, first_tip):
    """在 t002 实现 commit 与前驱 tip 之间插入一个无 task handoff 的空 commit。"""
    implementation = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    _git(worktree, "reset", "--hard", first_tip)
    _git(worktree, "commit", "--allow-empty", "-m", "gap commit without task handoff")
    gap = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    _git(worktree, "cherry-pick", implementation)
    task_dir = worktree / "docs/tasks/t002_beta"
    fm, body = parse_front_matter(task_dir / "progress.md")
    fm["diff_anchor"] = gap
    write_front_matter(task_dir / "progress.md", fm, body)
    payload = json.loads((task_dir / "handoff.json").read_text("utf-8"))
    payload["base_sha"] = gap
    payload["review_scope"] = review_scope_fingerprint(
        gap, "docs/tasks/t002_beta", repo_root=worktree
    )
    (task_dir / "handoff.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "--amend", "--no-edit")
    return gap


def test_chain_rejects_non_task_commit_between_members(git_repo):
    first, first_branch, first_tip = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", first)
    second, _, _ = _prepare_done(git_repo, "t002", "beta", base=first_branch)
    worktree = _worktree(git_repo, "t002")
    _insert_gap_commit_before_implementation(worktree, first_tip)
    _cleanup(git_repo, "t002", second)

    result = _task_cli(git_repo, "integrate-chain", "t002")

    assert result.returncode != 0
    assert "链不连续" in result.stderr
    assert not [e for e in _read_ledger(git_repo) if e["event"] == "integrated"]


def test_chain_rejects_predecessor_advanced_after_successor_fork(git_repo):
    first, first_branch, first_head = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", first)
    second, _, _ = _prepare_done(git_repo, "t002", "beta", base=first_branch)
    _cleanup(git_repo, "t002", second)
    _git(git_repo, "checkout", "-q", first_branch)
    _git(git_repo, "commit", "--allow-empty", "-m", "predecessor advanced after fork")
    _git(git_repo, "checkout", "-q", "main")
    assert _git(git_repo, "rev-parse", first_branch).stdout.strip() != first_head

    result = _task_cli(git_repo, "integrate-chain", "t002")

    assert result.returncode != 0
    assert "不是 HEAD 的祖先" in result.stderr
    assert "integrate-chain" in result.stderr
    assert not [e for e in _read_ledger(git_repo) if e["event"] == "integrated"]


def test_single_integrate_rejects_base_not_merged(git_repo):
    first, first_branch, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", first)
    second, _, _ = _prepare_done(git_repo, "t002", "beta", base=first_branch)
    _cleanup(git_repo, "t002", second)

    result = _task_cli(git_repo, "integrate", "t002", *_identity_args(second))

    assert result.returncode != 0
    assert "不是 HEAD 的祖先" in result.stderr
    assert "先 integrate 前驱" in result.stderr
    assert _git(git_repo, "rev-parse", "--verify", "MERGE_HEAD", check=False).returncode != 0


# --------------------------------------------------------------------------
# A06 / A09：三方解冲突保护定义与正文；materialize 拒绝非普通文件模式
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tamper_kind",
    ["title", "review_level", "note", "body", "unknown_field"],
)
def test_auto_conflict_resolution_preserves_conflict_when_main_edits_definition_or_body(
    git_repo, tamper_kind
):
    """A06：主干修改了 title、review_level、note、未知字段或正文时，拒绝自动覆盖，保留原生冲突。"""
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", identity)
    note_path = git_repo / ACTIVE / "progress.md"
    fm, body = parse_front_matter(note_path)
    if tamper_kind == "title":
        fm["title"] = "main changed title"
        write_front_matter(note_path, fm, body)
    elif tamper_kind == "review_level":
        fm["review_level"] = "single"
        write_front_matter(note_path, fm, body)
    elif tamper_kind == "note":
        fm["note"] = "main independent note"
        write_front_matter(note_path, fm, body)
    elif tamper_kind == "body":
        write_front_matter(note_path, fm, body.replace("\n无\n", "\n主干独立编辑正文\n", 1))
    elif tamper_kind == "unknown_field":
        raw = note_path.read_text(encoding="utf-8")
        note_path.write_text(raw.replace("---\ntid:", "---\ncustom_ext: '1'\ntid:", 1), encoding="utf-8")
    _git(git_repo, "add", str(note_path.relative_to(git_repo)))
    _git(git_repo, "commit", "-m", f"main edit {tamper_kind}")

    prepared = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity))

    assert prepared.returncode != 0
    assert "progress.md" in prepared.stderr
    _git(git_repo, "merge", "--abort")


def test_auto_conflict_resolution_accepts_authorized_review_upgrade_and_limits(git_repo):
    """A06：分支经授权执行 review-upgrade 与 limits 调大时，三方比较认可合法演进。"""
    from repo_task.integration import _is_pure_backlog_to_done_member_conflict

    first, first_branch, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", first)
    added = _task_cli(
        git_repo, "add", "--title", "delta", "--slug", "delta", "--review-level", "single"
    )
    assert added.returncode == 0, added.stderr
    t004_dir = git_repo / "docs/tasks/t004_delta"
    (t004_dir / "task.md").write_text(valid_spec(), encoding="utf-8")
    t004_note = t004_dir / "progress.md"
    t004_note.write_text(
        t004_note.read_text(encoding="utf-8") + "\n- specs_index: not_applicable: 测试。\n",
        encoding="utf-8",
    )
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "add t004 on main")

    begun = _task_cli(
        git_repo, "run-begin", "t004", "--owner-id", OWNER, "--base", first_branch
    )
    assert begun.returncode == 0, begun.stderr
    wt4 = _worktree(git_repo, "t004")
    assert _task_cli(wt4, "review-upgrade", "t004", "--reason", "安全面扩大").returncode == 0
    assert _task_cli(wt4, "limits", "t004", "--review", "8", "--reason", "用户批准").returncode == 0
    msg4 = prepare_finish(wt4, "t004", "delta")
    assert finish_cli(wt4, "t004", OWNER, msg4).returncode == 0
    assert _task_cli(
        git_repo, "run-close", "t004", "--owner-id", OWNER, "--result", "done"
    ).returncode == 0

    assert _is_pure_backlog_to_done_member_conflict(
        "t004_delta", ["t001_alpha", "t004_delta"], "docs/tasks/t004_delta/progress.md",
        repo=RepoContext.for_root(git_repo),
    )


def test_materialize_task_dir_rejects_symlink_and_preserves_executable_mode(git_repo):
    """A09：materialize 保留 100644/100755，遇到 120000 symlink 在零写入前拒绝且不残留 worktree/分支。"""
    first, first_branch, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", first)
    added = _task_cli(git_repo, "add", "--title", "delta", "--slug", "delta")
    assert added.returncode == 0, added.stderr
    t004_dir = git_repo / "docs/tasks/t004_delta"
    (t004_dir / "task.md").write_text(valid_spec(), encoding="utf-8")
    script = t004_dir / "helper.sh"
    script.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    script.chmod(0o755)
    link = t004_dir / "bad_link"
    link.symlink_to("task.md")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "add t004 with symlink")

    failed = _task_cli(git_repo, "start", "t004", "--base", first_branch)
    assert failed.returncode != 0
    assert "mode=120000" in failed.stderr
    assert not _worktree(git_repo, "t004").exists()
    assert not _git(git_repo, "branch", "--list", "t004_delta").stdout.strip()

    # 移除 symlink 后，普通文件与 100755 可执行文件正常物化
    link.unlink()
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "remove symlink from t004")
    ok = _task_cli(git_repo, "start", "t004", "--base", first_branch)
    assert ok.returncode == 0, ok.stderr
    wt4_script = _worktree(git_repo, "t004") / "docs/tasks/t004_delta/helper.sh"
    assert wt4_script.is_file() and not wt4_script.is_symlink()
    assert wt4_script.stat().st_mode & 0o111

