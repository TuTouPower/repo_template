"""task.py 扇出 start、integrate 合并、worktree 门禁与失败补偿（真实 git 仓库）。"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "repo/.repo_template/scripts"
TASK_TEMPLATE_DIR = SCRIPTS_DIR.parent / "docs" / "task_template"
sys.path.insert(0, str(SCRIPTS_DIR))

from review_support import ensure_review_evidence, configure_project_verification, valid_spec
from task_flow_support import mark_done, finish_cli, prepare_finish

from repo_task import context as ctx
from repo_task import attempts, control, integration, lifecycle, store
from repo_task.monitoring import review_scope_fingerprint
from repo_task.documents import parse_front_matter, write_front_matter


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", check=check, timeout=120
    )


def _valid_spec(unknown_contract_item="外部行为：已核实"):
    return valid_spec(unknown_contract_item)


def _valid_task_body():
    _, body = parse_front_matter(TASK_TEMPLATE_DIR / "progress.md")
    return body


from git_repo_support import fast_copy_repo


@pytest.fixture
def git_repo(tmp_path, workflow_three_tasks_seed, monkeypatch):
    """真实 git 主仓 + 三个 backlog task（高速种子独立副本）。"""
    repo = tmp_path / "repo"
    return fast_copy_repo(workflow_three_tasks_seed, repo, tmp_path, monkeypatch)


def _start(repo, tid="t001", base=None):
    integration.cmd_start(argparse.Namespace(tid=tid, base=base, repo=ctx.RepoContext.for_root(repo)))


def _task_cli(repo, *args):
    return subprocess.run(
        [sys.executable, str(repo / ".repo_template" / "scripts" / "task.py"), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8", errors="replace",
        timeout=120,
    )


def _worktree_path(repo, tid="t001"):
    return repo.parent / f"{repo.name}_{tid}"


def _reserve(repo, tid, model=None):
    args = ["attempt", "reserve", tid]
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


def _handoff(tid, branch, identity, base_sha, *, status="done", review_scope="", spec_source_sha=None):
    if spec_source_sha is None:
        spec_source_sha = base_sha
    return {
        "tid": tid,
        "attempt": identity["attempt"],
        "execution_id": identity["execution_id"],
        "status": status,
        "branch": branch,
        "base_sha": base_sha,
        "spec_source_sha": spec_source_sha,
        "tests": "pytest -q",
        "blackbox": "pass",
        "review": "pass",
        "review_scope": review_scope,
        "pending": [],
        "findings": [],
    }


def _check_ac(worktree, tid="t001", slug="alpha"):
    spec = worktree / f"docs/tasks/{tid}_{slug}/task.md"
    if spec.is_file():
        spec.write_text(spec.read_text(encoding="utf-8").replace("- [ ] AC-001", "- [x] AC-001"), encoding="utf-8")


def _terminal(repo, tid, identity, status="completed"):
    result = _task_cli(
        repo, "attempt", "terminal", tid, *_identity_args(identity),
        "--status", status,
    )
    assert result.returncode == 0, result.stderr
    if status == "completed":
        # report 门禁：cleanup/integrate 前须 report=done
        report = _task_cli(
            repo, "attempt", "report", tid, *_identity_args(identity),
            "--status", "done",
            "--sha", _git(repo, "rev-parse", "HEAD").stdout.strip(),
        )
        assert report.returncode == 0, report.stderr
    return result


def _write_handoff(worktree, tid, slug, identity, base_sha, *, status="done", spec_source_sha=None):
    branch = f"{tid}_{slug}"
    task_dir = worktree / "docs" / "tasks" / branch
    ensure_review_evidence(worktree, task_dir, base_sha)
    scope = review_scope_fingerprint(base_sha, f"docs/tasks/{branch}", repo_root=worktree)
    if not spec_source_sha:
        fm, _ = parse_front_matter(task_dir / "progress.md")
        spec_source_sha = fm.get("spec_source_sha") or base_sha
    (task_dir / "handoff.json").write_text(
        json.dumps(
            _handoff(tid, branch, identity, base_sha, status=status, review_scope=scope, spec_source_sha=spec_source_sha),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _set_spec(repo, tid, slug, unknown_contract_item):
    spec = repo / f"docs/tasks/{tid}_{slug}/task.md"
    spec.write_text(_valid_spec(unknown_contract_item), encoding="utf-8")
    _git(repo, "add", str(spec.relative_to(repo)))
    if _git(repo, "diff", "--cached", "--quiet", check=False).returncode != 0:
        _git(repo, "commit", "-m", f"add {tid} spec")


def _finish_commit_cleanup(repo, tid, slug):
    context = ctx.RepoContext.for_root(repo)
    identity = attempts.reserve_attempt(tid, repo=context)
    worktree = _worktree_path(repo, tid)
    _check_ac(worktree, tid, slug)
    mark_done(worktree, tid)
    base_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    _write_handoff(worktree, tid, slug, identity, base_sha)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", f"feat({tid}): complete {slug}")
    branch = f"{tid}_{slug}"
    branch_head = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    attempts.terminal_attempt(tid, identity["attempt"], identity["execution_id"], "completed", repo=context)
    attempts.report_attempt(
        tid, identity["attempt"], identity["execution_id"], "done",
        sha=_git(repo, "rev-parse", "HEAD").stdout.strip(), repo=context,
    )
    integration._cleanup_worktree(tid, identity["attempt"], identity["execution_id"], repo=context)
    assert not worktree.exists()
    assert _git(repo, "branch", "--list", branch).stdout.strip() == branch
    return identity, branch, branch_head


def test_add_copies_validated_template(git_repo):
    result = _task_cli(
        git_repo,
        "add",
        "--title",
        "delta",
        "--slug",
        "delta",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    task_dir = git_repo / "docs/tasks/t004_delta"
    assert task_dir.is_dir()
    _, body = parse_front_matter(task_dir / "progress.md")
    assert "## 实施笔记\n\n" in body
    assert "\n无\n\n## Review 处置" in body


def test_add_rejects_invalid_template_before_copy(git_repo):
    template_task = git_repo / ".repo_template/docs/task_template/progress.md"
    template_task.write_text(
        template_task.read_text(encoding="utf-8").replace(
            "\n无\n\n## Review 处置",
            "\n## Review 处置",
            1,
        ),
        encoding="utf-8",
    )

    result = _task_cli(
        git_repo,
        "add",
        "--title",
        "delta",
        "--slug",
        "delta",
    )

    assert result.returncode != 0
    assert "模板结构校验失败" in result.stderr
    assert not (git_repo / "docs/tasks/t004_delta").exists()


def test_add_rejects_template_with_stale_front_matter_field_before_copy(git_repo):
    """A13：模板含当前契约外字段（如已删除的 schedule_status）时在建目录前失败，不静默清除。"""
    template_task = git_repo / ".repo_template/docs/task_template/progress.md"
    template_task.write_text(
        template_task.read_text(encoding="utf-8").replace(
            'conflicts_with: ""\n', 'conflicts_with: ""\nschedule_status: scheduled\n', 1
        ),
        encoding="utf-8",
    )

    result = _task_cli(git_repo, "add", "--title", "delta", "--slug", "delta")

    assert result.returncode != 0
    assert "当前契约外字段" in result.stderr
    assert "schedule_status" in result.stderr
    assert not (git_repo / "docs/tasks/t004_delta").exists()


def test_start_keeps_main_unchanged_and_activates_only_worktree(git_repo):
    initial_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    _start(git_repo)

    assert _git(git_repo, "branch", "--show-current").stdout.strip() == "main"
    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == initial_head
    assert _git(git_repo, "status", "--porcelain").stdout.strip() == ""
    worktree = _worktree_path(git_repo)
    assert worktree.is_dir()
    assert _git(worktree, "branch", "--show-current").stdout.strip() == "t001_alpha"
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == initial_head
    assert _git(worktree, "status", "--porcelain").stdout.split() == [
        "M",
        "docs/tasks/t001_alpha/progress.md",
    ]

    main_fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/progress.md")
    worktree_fm, _ = parse_front_matter(worktree / "docs/tasks/t001_alpha/progress.md")
    assert main_fm["status"] == "backlog"
    assert main_fm["branch"] == ""
    assert worktree_fm["status"] == "active"
    assert worktree_fm["branch"] == "t001_alpha"
    assert worktree_fm["worktree"] == f"../{git_repo.name}_t001"
    assert worktree_fm["diff_anchor"] == initial_head
    assert not (git_repo / "docs/tasks_index.json").exists()
    assert not (git_repo / "docs/archive/tasks_index.json").exists()


def test_start_rejects_non_primary_branch(git_repo):
    _git(git_repo, "switch", "-c", "feature")

    with pytest.raises(SystemExit, match="主干"):
        _start(git_repo)

    assert not _worktree_path(git_repo).exists()


def test_start_ignores_dirty_primary_worktree(git_repo):
    (git_repo / "unrelated.txt").write_text("dirty", encoding="utf-8")

    _start(git_repo)

    worktree = _worktree_path(git_repo)
    assert worktree.is_dir()
    assert not (worktree / "unrelated.txt").exists()
    assert (git_repo / "unrelated.txt").read_text(encoding="utf-8") == "dirty"
    worktree_fm, _ = parse_front_matter(worktree / "docs/tasks/t001_alpha/progress.md")
    assert worktree_fm["status"] == "active"


def test_start_rejects_existing_task_branch(git_repo):
    _git(git_repo, "branch", "t001_alpha")

    with pytest.raises(SystemExit, match="分支.*已存在"):
        _start(git_repo)

    assert not _worktree_path(git_repo).exists()


def test_start_rejects_existing_worktree_path(git_repo):
    _worktree_path(git_repo).mkdir()

    with pytest.raises(SystemExit, match="已存在"):
        _start(git_repo)


def test_start_rejects_task_worktree(git_repo):
    _start(git_repo)
    result = _task_cli(_worktree_path(git_repo), "start", "t002")

    assert result.returncode != 0
    assert "主工作区" in result.stderr


def test_list_from_task_worktree_reads_own_snapshot(git_repo):
    """A14：list 只读权威文件；在 task worktree 中读取该 worktree 自己的快照。"""
    _start(git_repo)
    result = _task_cli(_worktree_path(git_repo), "list")

    assert result.returncode == 0, result.stderr
    assert "t001" in result.stdout
    assert not (_worktree_path(git_repo) / "docs/tasks_index.json").exists()


def test_finish_rejects_uncompleted_ac(git_repo):
    """finish 拦截未打勾的 AC，且零写入。"""
    begun = _task_cli(git_repo, "run-begin", "t001", "--owner-id", "owner-a")
    assert begun.returncode == 0, begun.stderr
    worktree = _worktree_path(git_repo)
    message = prepare_finish(worktree, "t001", "alpha", tick_ac=False)
    before = _git(worktree, "status", "--porcelain").stdout

    finished = finish_cli(worktree, "t001", "owner-a", message)

    assert finished.returncode != 0
    assert "存在未完成 AC（未打勾）：AC-001" in finished.stderr
    assert _git(worktree, "status", "--porcelain").stdout == before
    assert not (worktree / "docs/archive/tasks/t001_alpha").exists()


def test_cleanup_worktree_requires_clean_commit_and_is_idempotent(git_repo):
    _start(git_repo)
    identity = _reserve(git_repo, "t001")
    assert _task_cli(
        git_repo, "attempt", "terminal", "t001", *_identity_args(identity),
        "--status", "completed",
    ).returncode == 0
    # 未 finish 的 active 分支：report=done 先被同一门禁拒绝
    not_done_report = _task_cli(
        git_repo, "attempt", "report", "t001", *_identity_args(identity),
        "--status", "done",
        "--sha", _git(git_repo, "rev-parse", "HEAD").stdout.strip(),
    )
    assert not_done_report.returncode != 0
    assert "非终态" in not_done_report.stderr
    not_done = _task_cli(
        git_repo, "cleanup-worktree", "t001", *_identity_args(identity)
    )
    assert not_done.returncode != 0

    # 已 finish 且提交（分支 ref=done），但 worktree 新增脏改动：拒绝
    worktree = _worktree_path(git_repo)
    _check_ac(worktree, "t001", "alpha")
    mark_done(worktree, "t001")
    base_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    _write_handoff(worktree, "t001", "alpha", identity, base_sha)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "feat(t001): complete alpha")
    head = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    assert _task_cli(
        git_repo, "attempt", "report", "t001", *_identity_args(identity),
        "--status", "done", "--sha", head,
    ).returncode == 0
    (worktree / "dirty.txt").write_text("x", encoding="utf-8")
    dirty = _task_cli(
        git_repo, "cleanup-worktree", "t001", *_identity_args(identity)
    )
    assert dirty.returncode != 0
    assert "未提交改动" in dirty.stderr

    # 清掉脏改动后 cleanup 成功且幂等
    (worktree / "dirty.txt").unlink()
    branch = "t001_alpha"
    cleaned = _task_cli(
        git_repo, "cleanup-worktree", "t001", *_identity_args(identity)
    )
    assert cleaned.returncode == 0, cleaned.stderr
    assert not worktree.exists()
    repeated = _task_cli(
        git_repo, "cleanup-worktree", "t001", *_identity_args(identity)
    )
    assert repeated.returncode == 0
    assert "幂等" in repeated.stdout
    assert _git(git_repo, "branch", "--list", branch).stdout.strip() == branch


def test_cleanup_worktree_rejects_wrong_registered_branch(git_repo):
    _start(git_repo)
    identity = _reserve(git_repo, "t001")
    worktree = _worktree_path(git_repo)
    _check_ac(worktree, "t001", "alpha")
    mark_done(worktree, "t001")
    base_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    _write_handoff(worktree, "t001", "alpha", identity, base_sha)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "feat(t001): complete alpha")
    _terminal(git_repo, "t001", identity)
    _git(worktree, "switch", "-c", "t999_other")

    result = _task_cli(
        git_repo, "cleanup-worktree", "t001", *_identity_args(identity)
    )

    assert result.returncode != 0
    assert "登记分支" in result.stderr
    assert "预期 't001_alpha'" in result.stderr


def test_cleanup_worktree_rejects_unregistered_directory(git_repo):
    _start(git_repo)
    identity, _, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    unknown = _worktree_path(git_repo)
    unknown.mkdir()
    marker = unknown / "keep.txt"
    marker.write_text("user data\n", encoding="utf-8")

    result = _task_cli(
        git_repo, "cleanup-worktree", "t001", *_identity_args(identity)
    )

    assert result.returncode != 0
    assert "未登记为 git worktree" in result.stderr
    assert marker.read_text(encoding="utf-8") == "user data\n"


def test_start_compensates_when_worktree_creation_fails(git_repo, monkeypatch):
    initial_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    def fail_create(*args, **kwargs):
        raise ctx.TaskDataError("模拟 worktree 创建失败")

    monkeypatch.setattr(integration, "create_worktree", fail_create)

    with pytest.raises(SystemExit, match="主仓.*未修改"):
        _start(git_repo)

    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == initial_head
    assert _git(git_repo, "status", "--porcelain").stdout.strip() == ""
    assert not _worktree_path(git_repo).exists()
    assert _git(git_repo, "branch", "--list", "t001_alpha").stdout.strip() == ""


def test_start_compensates_when_local_config_link_fails(git_repo, monkeypatch):
    initial_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    def fail_link(*args, **kwargs):
        raise OSError("模拟本地配置软链失败")

    monkeypatch.setattr(integration, "link_local_files", fail_link)

    with pytest.raises(SystemExit, match="主仓.*未修改"):
        _start(git_repo)

    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == initial_head
    assert _git(git_repo, "status", "--porcelain").stdout.strip() == ""
    assert not _worktree_path(git_repo).exists()
    assert _git(git_repo, "branch", "--list", "t001_alpha").stdout.strip() == ""


def test_rewind_discards_uncommitted_activation_and_removes_empty_branch(git_repo):
    _start(git_repo)
    worktree = _worktree_path(git_repo)

    lifecycle.cmd_rewind(
        argparse.Namespace(
            tid="t001", to="backlog", reason="撤回", yes=True,
            repo=ctx.RepoContext.for_root(git_repo),
        )
    )

    assert not worktree.exists()
    assert _git(git_repo, "branch", "--list", "t001_alpha").stdout.strip() == ""
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/progress.md")
    assert fm["status"] == "backlog"
    assert fm["worktree"] == ""
    assert "rewound: effective=active -> backlog" in fm["note"]
    assert "main 记录为 backlog" in fm["note"]






def test_start_always_forks_from_current_main_head(git_repo):
    main_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _start(git_repo, "t001")
    _start(git_repo, "t002")

    for tid, slug in (("t001", "alpha"), ("t002", "beta")):
        worktree = _worktree_path(git_repo, tid)
        fm, _ = parse_front_matter(worktree / f"docs/tasks/{tid}_{slug}/progress.md")
        assert fm["status"] == "active"
        assert fm["diff_anchor"] == main_head
        assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == main_head
    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == main_head


def test_start_picks_up_main_advanced_between_starts(git_repo):
    _start(git_repo, "t001")
    (git_repo / "parallel.txt").write_text("main advanced", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "chore: parallel work on main")
    advanced = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    _start(git_repo, "t002")

    worktree = _worktree_path(git_repo, "t002")
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == advanced
    assert (worktree / "parallel.txt").is_file()


def test_start_rejects_when_primary_head_differs_from_main(git_repo):
    _git(git_repo, "checkout", "-b", "feature")

    with pytest.raises(SystemExit, match="主干"):
        _start(git_repo, "t002")


def test_list_and_show_read_completed_state_from_branch(git_repo):
    _start(git_repo, "t001")
    _, branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")

    main_show = _task_cli(git_repo, "show", "t001")
    ref_show = _task_cli(git_repo, "show", "t001", "--ref", branch)
    ref_list = _task_cli(git_repo, "list", "--ref", branch, "--status", "done")

    assert "status" in main_show.stdout and "backlog" in main_show.stdout
    assert "progress_md" in main_show.stdout and "task_md" in main_show.stdout
    assert "task.md" in main_show.stdout and "progress.md" in main_show.stdout
    assert ref_show.returncode == 0 and "done" in ref_show.stdout
    assert "source_ref" in ref_show.stdout
    assert "task.md" in ref_show.stdout
    assert ref_list.returncode == 0 and "t001" in ref_list.stdout


# --------------------------------------------------------------------------
# integrate：完成即合并
# --------------------------------------------------------------------------




def test_prepare_merge_checks_merge_tree_capability_before_git_merge(monkeypatch):
    calls = []

    def unsupported(_branch, **_kwargs):
        raise ctx.TaskDataError('内容门禁需要 Git >= 2.38')

    def record_git(args, **_kwargs):
        calls.append(args)
        raise AssertionError('git merge must not run after capability failure')

    monkeypatch.setattr(integration, '_expected_auto_merge', unsupported)
    monkeypatch.setattr(integration, '_git', record_git)
    with pytest.raises(SystemExit, match='merge 尚未开始.*Git >= 2.38'):
        integration._prepare_native_merge(
            't001_alpha', 'merge(t001): t001_alpha', ['t001_alpha'],
            repo=ctx.RepoContext.for_root(Path.cwd()),
        )
    assert calls == []


def test_integrate_keeps_branch_when_requested(git_repo):
    _start(git_repo, "t001")
    identity, branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")

    rejected = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity), "--keep-branch"
    )
    assert rejected.returncode != 0
    assert "只在 --continue" in rejected.stderr
    prepared = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity))
    assert prepared.returncode == 0, prepared.stderr
    result = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity),
        "--continue", "--keep-branch",
    )
    assert result.returncode == 0, result.stderr
    assert _git(git_repo, "branch", "--list", branch).stdout.strip() == branch




def test_integrate_rejects_unfinished_task(git_repo):
    _start(git_repo, "t001")
    identity = _reserve(git_repo, "t001")
    assert _task_cli(
        git_repo, "attempt", "terminal", "t001", *_identity_args(identity),
        "--status", "completed",
    ).returncode == 0
    # 未 finish 的 active 分支：report=done 先被同一门禁拒绝，integrate 仍拒绝
    not_done_report = _task_cli(
        git_repo, "attempt", "report", "t001", *_identity_args(identity),
        "--status", "done",
        "--sha", _git(git_repo, "rev-parse", "HEAD").stdout.strip(),
    )
    assert not_done_report.returncode != 0
    assert "非终态" in not_done_report.stderr

    result = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity)
    )

    assert result.returncode != 0


def test_integrate_rejects_registered_worktree(git_repo):
    _start(git_repo, "t001")
    identity = _reserve(git_repo, "t001")
    worktree = _worktree_path(git_repo)
    _check_ac(worktree, "t001", "alpha")
    mark_done(worktree, "t001")
    base_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    _write_handoff(worktree, "t001", "alpha", identity, base_sha)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "feat(t001): complete alpha")
    _terminal(git_repo, "t001", identity)

    result = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity)
    )

    assert result.returncode != 0
    assert "cleanup-worktree" in result.stderr


def test_integrate_rejects_tracked_dirty_primary_worktree(git_repo):
    tracked = git_repo / "tracked.txt"
    tracked.write_text("base", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "chore: add tracked file")
    _start(git_repo, "t001")
    identity, _, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    tracked.write_text("modified", encoding="utf-8")

    result = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity)
    )

    assert result.returncode != 0
    assert "已跟踪文件未提交" in result.stderr


def test_integrate_ignores_untracked_files(git_repo):
    _start(git_repo, "t001")
    identity, _, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    (git_repo / "scratch_note.txt").write_text("untracked", encoding="utf-8")

    result = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity)
    )

    assert result.returncode == 0, result.stderr
    assert (git_repo / "scratch_note.txt").is_file()


def test_integrate_detects_merge_state_from_any_cwd(git_repo, tmp_path):
    """_merge_in_progress 用绝对 git-dir；从仓库外调用不得误判为无 merge。"""
    shared = git_repo / "shared.txt"
    shared.write_text("base\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "chore: add shared file")
    _start(git_repo, "t001")
    worktree = _worktree_path(git_repo)
    (worktree / "shared.txt").write_text("from task\n", encoding="utf-8")
    identity, _, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    shared.write_text("from main\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "chore: edit shared on main")
    assert _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity)
    ).returncode != 0

    outside = tmp_path / "outside"
    outside.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(git_repo / ".repo_template" / "scripts" / "task.py"),
            "integrate",
            "t001",
            *_identity_args(identity),
        ],
        cwd=outside,
        capture_output=True,
        text=True,
        encoding="utf-8", errors="replace",
    )

    assert result.returncode != 0
    assert "进行中的 merge" in result.stderr


def test_integrate_reports_conflict_and_continues_after_resolution(git_repo):
    shared = git_repo / "shared.txt"
    shared.write_text("base\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "chore: add shared file")

    _start(git_repo, "t001")
    worktree = _worktree_path(git_repo)
    (worktree / "shared.txt").write_text("from task\n", encoding="utf-8")
    identity, branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")

    shared.write_text("from main\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "chore: edit shared on main")

    conflicted = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity)
    )
    assert conflicted.returncode != 0
    assert "shared.txt" in conflicted.stderr
    assert "--continue" in conflicted.stderr

    premature = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity), "--continue"
    )
    assert premature.returncode != 0
    assert "未解决冲突" in premature.stderr

    shared.write_text("resolved\n", encoding="utf-8")
    _git(git_repo, "add", "shared.txt")
    resumed = _task_cli(
        git_repo, "integrate", "t001", *_identity_args(identity), "--continue"
    )

    assert resumed.returncode == 0, resumed.stderr
    assert shared.read_text(encoding="utf-8") == "resolved\n"
    assert _git(git_repo, "branch", "--list", branch).stdout.strip() == ""




# --------------------------------------------------------------------------
# integrate-chain：串行链式，只合链尾
# --------------------------------------------------------------------------


def test_chain_start_from_previous_completed_branch(git_repo):
    """串行：t002 从 t001 已完成分支创建，继承其成果。"""
    _start(git_repo, "t001")
    _, first_branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")

    _start(git_repo, "t002", base=first_branch)

    second = _worktree_path(git_repo, "t002")
    assert second.is_dir()
    fm, _ = parse_front_matter(second / "docs/tasks/t002_beta/progress.md")
    assert fm["status"] == "active"
    # t002 分支从 t001 分支创建，包含其 commit
    assert _git(
        second, "merge-base", "--is-ancestor", first_branch, "HEAD", check=False
    ).returncode == 0




def test_chain_integrate_rejects_mid_chain_undone(git_repo):
    """链上有未完成的 task 时拒绝合并。"""
    _start(git_repo, "t001")
    _, first_branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    _start(git_repo, "t002", base=first_branch)  # t002 active 未 finish
    _reserve(git_repo, "t002")

    result = _task_cli(git_repo, "integrate-chain", "t002")

    assert result.returncode != 0
    assert "须为 done/dropped" in result.stderr


def test_chain_integrate_rejects_mid_chain_registered_worktree(git_repo):
    """链上有 task 仍挂 worktree 时拒绝合并。"""
    _start(git_repo, "t001")
    _, first_branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    _start(git_repo, "t002", base=first_branch)
    identity = _reserve(git_repo, "t002")
    # t002 finish + handoff + commit + terminal，但不 cleanup-worktree
    worktree = _worktree_path(git_repo, "t002")
    _check_ac(worktree, "t002", "beta")
    mark_done(worktree, "t002")
    base_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    _write_handoff(worktree, "t002", "beta", identity, base_sha)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "feat(t002): beta")
    _terminal(git_repo, "t002", identity)

    result = _task_cli(git_repo, "integrate-chain", "t002")

    assert result.returncode != 0
    assert "仍登记 worktree" in result.stderr


def test_integrate_requires_primary_worktree(git_repo):
    _start(git_repo, "t001")
    identity = _reserve(git_repo, "t001")
    worktree = _worktree_path(git_repo)

    result = _task_cli(
        worktree, "integrate", "t001", *_identity_args(identity)
    )

    assert result.returncode != 0
    assert "主工作区" in result.stderr


def test_preflight_reads_active_state_only_inside_task_worktree(git_repo):
    _start(git_repo)
    worktree = _worktree_path(git_repo)

    primary = _task_cli(git_repo, "preflight", "t001")
    task_worktree = _task_cli(worktree, "preflight", "t001")

    assert primary.returncode != 0
    assert "status=backlog" in primary.stdout
    assert "status=backlog" not in task_worktree.stdout


def test_preflight_can_check_backlog_from_main_and_chain_ref(git_repo):
    _set_spec(git_repo, "t001", "alpha", "外部行为：已核实")
    _set_spec(git_repo, "t002", "beta", "外部行为：已核实")

    main_backlog = _task_cli(git_repo, "preflight", "t001", "--allow-backlog")
    assert main_backlog.returncode == 0, main_backlog.stdout + main_backlog.stderr
    assert "preflight=PASS" in main_backlog.stdout

    _start(git_repo)
    _, branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    chain_backlog = _task_cli(
        git_repo,
        "preflight",
        "t002",
        "--allow-backlog",
        "--ref",
        branch,
    )

    assert chain_backlog.returncode == 0, chain_backlog.stdout + chain_backlog.stderr
    assert f"source_ref: {branch}" in chain_backlog.stdout
    assert "未检查 task worktree 与当前脏改动" in chain_backlog.stdout
    assert "preflight=PASS" in chain_backlog.stdout


def test_preflight_active_worktree_allows_implementation_files_without_warning(git_repo):
    _set_spec(git_repo, "t001", "alpha", "外部行为：已核实")
    _start(git_repo)
    worktree = _worktree_path(git_repo)

    (worktree / "src").mkdir(exist_ok=True)
    (worktree / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (worktree / "tests").mkdir(exist_ok=True)
    (worktree / "tests" / "test_app.py").write_text("assert True\n", encoding="utf-8")
    (worktree / "docs" / "blueprint").mkdir(parents=True, exist_ok=True)
    (worktree / "docs" / "blueprint" / "testing.md").write_text(
        "## verification_policy\nproject_configured\n## verification_notes\n测试配置已明确\n## doctor_cmd\n```bash\npytest\n```\n## test_cmd\n```bash\npytest\n```\n## blackbox_verify\n```bash\npytest\n```\n",
        encoding="utf-8",
    )

    result = _task_cli(worktree, "preflight", "t001")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "preflight=PASS" in result.stdout
    assert "与本 task 无关" not in result.stdout
    assert "未提交改动" not in result.stdout
    assert "混入其他 task 目录改动" not in result.stdout


def test_preflight_active_worktree_warns_on_cross_task_directory_change(git_repo):
    _set_spec(git_repo, "t001", "alpha", "外部行为：已核实")
    _start(git_repo)
    worktree = _worktree_path(git_repo)

    other_spec = worktree / "docs" / "tasks" / "t002_beta" / "task.md"
    other_spec.write_text("modified in t001 worktree\n", encoding="utf-8")

    result = _task_cli(worktree, "preflight", "t001")
    assert "工作区混入其他 task 目录改动" in result.stdout
    assert "docs/tasks/t002_beta/task.md" in result.stdout


def test_preflight_backlog_warns_on_uncommitted_files(git_repo):
    _set_spec(git_repo, "t001", "alpha", "外部行为：已核实")
    (git_repo / "dirty_code.py").write_text("x = 1\n", encoding="utf-8")

    result = _task_cli(git_repo, "preflight", "t001", "--allow-backlog")
    assert "工作区有 1 项未提交改动：dirty_code.py" in result.stdout


def test_preflight_ref_diagnostics_when_active_in_worktree(git_repo):
    _set_spec(git_repo, "t001", "alpha", "外部行为：已核实")
    _start(git_repo)

    result = _task_cli(git_repo, "preflight", "t001", "--ref", "t001_alpha")
    assert result.returncode != 0
    assert "status=backlog" in result.stdout
    assert "快照滞后" in result.stdout
    assert "--ref 仅读取 Git 提交快照" in result.stdout


def test_start_rejects_missing_scaffold_before_mutation(git_repo):
    spec = git_repo / "docs/tasks/t001_alpha/task.md"
    # 按原始行扫描删除第一个 `<!-- 规范 -->` 块（含空行），
    # 不硬编码块格式，模板空行变化不影响本测试。
    lines = spec.read_text(encoding="utf-8").splitlines()
    out, in_block, skipped = [], False, False
    for line in lines:
        if not skipped and line.strip() == "<!-- 规范（门禁必留，不得删除） -->":
            in_block, skipped = True, True
            continue
        if in_block:
            if line.strip() == "<!-- /规范 -->":
                in_block = False
            continue
        out.append(line)
    spec.write_text("\n".join(out), encoding="utf-8")
    _git(git_repo, "add", str(spec.relative_to(git_repo)))
    _git(git_repo, "commit", "-m", "break task scaffold")
    initial_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    with pytest.raises(SystemExit, match="规范块"):
        _start(git_repo)

    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == initial_head
    assert _git(git_repo, "status", "--porcelain").stdout.strip() == ""
    assert not _worktree_path(git_repo).exists()


def test_backlog_preflight_rejects_empty_implementation_notes(git_repo):
    task_md = git_repo / "docs/tasks/t001_alpha/progress.md"
    text = task_md.read_text(encoding="utf-8").replace(
        "\n无\n\n## Review 处置",
        "\n## Review 处置",
        1,
    )
    task_md.write_text(text, encoding="utf-8")

    result = _task_cli(git_repo, "preflight", "t001", "--allow-backlog")

    assert result.returncode != 0
    assert "实施笔记为空" in result.stdout


def test_start_rejects_blocking_unknown_contract_before_mutation(git_repo):
    _set_spec(git_repo, "t001", "alpha", "用户账号：UNVERIFIED-BLOCKING，需用户核实")
    initial_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    with pytest.raises(SystemExit, match="UNVERIFIED-BLOCKING"):
        _start(git_repo)

    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == initial_head
    assert _git(git_repo, "status", "--porcelain").stdout.strip() == ""
    assert not _worktree_path(git_repo).exists()


def test_start_rejects_ambiguous_unverified_marker(git_repo):
    _set_spec(git_repo, "t001", "alpha", "外部行为：UNVERIFIED，待决定")

    with pytest.raises(SystemExit, match="裸 UNVERIFIED"):
        _start(git_repo)

    assert not _worktree_path(git_repo).exists()


def test_spike_requires_strict_preflight_before_implementation(git_repo):
    _set_spec(git_repo, "t001", "alpha", "平台行为：UNVERIFIED-SPIKE，执行期实验")
    _start(git_repo)
    worktree = _worktree_path(git_repo)

    default = _task_cli(worktree, "preflight", "t001")
    assert default.returncode == 0, default.stderr
    assert "WARN" in default.stdout
    assert "只能先完成实验并回填结论" in default.stdout

    strict = _task_cli(worktree, "preflight", "t001", "--require-verified")
    assert strict.returncode != 0
    assert "UNVERIFIED-SPIKE" in strict.stdout

    spec = worktree / "docs/tasks/t001_alpha/task.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "平台行为：UNVERIFIED-SPIKE，执行期实验",
            "平台行为：已通过本地兼容实验核实",
        ),
        encoding="utf-8",
    )
    verified = _task_cli(worktree, "preflight", "t001", "--require-verified")
    assert verified.returncode == 0, verified.stderr
    assert "preflight=PASS" in verified.stdout


def test_preflight_rejects_blocking_marker_added_after_start(git_repo):
    _set_spec(git_repo, "t001", "alpha", "外部行为：已核实")
    _start(git_repo)
    worktree = _worktree_path(git_repo)
    spec = worktree / "docs/tasks/t001_alpha/task.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "外部行为：已核实",
            "用户账号：UNVERIFIED-BLOCKING，需用户核实",
        ),
        encoding="utf-8",
    )

    result = _task_cli(worktree, "preflight", "t001")

    assert result.returncode != 0
    assert "UNVERIFIED-BLOCKING" in result.stdout


# --------------------------------------------------------------------------
# 审阅修复回归测试
# --------------------------------------------------------------------------

def _rewind(repo, tid="t001", to="backlog", yes=True):
    lifecycle.cmd_rewind(argparse.Namespace(
        tid=tid, to=to, reason="撤回", yes=yes, repo=ctx.RepoContext.for_root(repo),
    ))


def test_rewind_keeps_branch_with_own_commits_and_guides(git_repo, capsys):
    """T1：rewind 后分支有 task commit 时保留分支并输出恢复指引。"""
    _start(git_repo)
    worktree = _worktree_path(git_repo)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "checkpoint")

    _rewind(git_repo)

    # 分支保留（own_commits=True，不删除）
    assert _git(git_repo, "branch", "--list", "t001_alpha").stdout.strip() == "t001_alpha"
    assert not worktree.exists()
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/progress.md")
    assert fm["status"] == "backlog"
    assert fm["branch"] == ""


def test_rewind_without_yes_warns_branch_kept_and_recovery(git_repo, monkeypatch, capsys):
    """rewind 交互确认时输出分支保留与恢复指引。"""
    _start(git_repo)
    worktree = _worktree_path(git_repo)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "checkpoint")
    monkeypatch.setattr("builtins.input", lambda: "y")

    _rewind(git_repo, yes=False)

    err = capsys.readouterr().err
    assert "将保留" in err
    assert "git branch -D t001_alpha" in err
    assert "git worktree add" in err


def test_rewind_backlog_not_covered_by_stale_branch(git_repo):
    """A31：rewind 保留分支后，view 读到 main 的 backlog，而非旧分支 active。"""
    _start(git_repo)
    worktree = _worktree_path(git_repo)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "checkpoint")
    _rewind(git_repo, "t001")
    # 分支保留（own commit），main 已显式回 backlog
    assert _git(git_repo, "branch", "--list", "t001_alpha").stdout.strip() == "t001_alpha"
    assert not worktree.exists()

    result = _task_cli(git_repo, "view")

    assert result.returncode == 0, result.stderr
    assert "[运行中] active 0" in result.stdout
    assert "t001  alpha" in result.stdout


def test_rewind_rejects_foreign_registered_branch(git_repo):
    """rewind 拒绝强制删除登记为其他分支的 worktree。"""
    _start(git_repo)
    worktree = _worktree_path(git_repo)
    # 把 worktree 切到另一个分支（模拟路径被其他分支占用）
    _git(worktree, "switch", "-c", "t999_other")

    with pytest.raises(SystemExit, match="不符"):
        _rewind(git_repo)

    assert worktree.exists()


def test_cleanup_worktree_rejects_active_even_when_clean(git_repo):
    """T2：active task 有 checkpoint commit 使 worktree clean 时，cleanup 仍拒绝。"""
    _start(git_repo)
    identity = _reserve(git_repo, "t001")
    worktree = _worktree_path(git_repo)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "checkpoint")
    assert _git(worktree, "status", "--porcelain").stdout.strip() == ""
    assert _task_cli(
        git_repo, "attempt", "terminal", "t001", *_identity_args(identity),
        "--status", "completed",
    ).returncode == 0
    # 未 finish 的 active 分支：report=done 先被同一门禁拒绝，cleanup 仍拒绝
    not_done_report = _task_cli(
        git_repo, "attempt", "report", "t001", *_identity_args(identity),
        "--status", "done",
        "--sha", _git(git_repo, "rev-parse", "HEAD").stdout.strip(),
    )
    assert not_done_report.returncode != 0
    assert "非终态" in not_done_report.stderr

    result = _task_cli(
        git_repo, "cleanup-worktree", "t001", *_identity_args(identity)
    )

    assert result.returncode != 0
    assert worktree.exists()


def test_cleanup_worktree_rejects_prefix_collision_branch(git_repo):
    """cleanup 拒绝 t001_scratch 这类仅共享前缀的非 task 分支。"""
    _start(git_repo)
    identity = _reserve(git_repo, "t001")
    worktree = _worktree_path(git_repo)
    _check_ac(worktree, "t001", "alpha")
    mark_done(worktree, "t001")
    base_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    _write_handoff(worktree, "t001", "alpha", identity, base_sha)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "feat(t001): complete alpha")
    _terminal(git_repo, "t001", identity)
    _git(worktree, "switch", "-c", "t001_scratch")

    result = _task_cli(
        git_repo, "cleanup-worktree", "t001", *_identity_args(identity)
    )

    assert result.returncode != 0
    assert "存在多个本地 task 分支" in result.stderr
    assert "t001_scratch" in result.stderr
    assert worktree.exists()


def test_preflight_warns_when_backlog_covered_by_registered_worktree(git_repo):
    """T3：main 显 backlog 但 worktree 已 active 时，preflight 给出滞后警告。"""
    _start(git_repo)

    result = _task_cli(git_repo, "preflight", "t001", "--allow-backlog")

    assert "滞后" in result.stdout or "worktree" in result.stdout
    assert "不能据此重复 start" in result.stdout


def test_drop_from_main_rejects_stale_backlog_with_active_worktree(git_repo):
    """drop 在主仓探测到 worktree active，拒绝归档过期 backlog。"""
    _start(git_repo)

    result = _task_cli(git_repo, "drop", "t001", "--reason", "x")

    assert result.returncode != 0
    assert "滞后" in result.stderr
    # main 副本未被归档
    assert (git_repo / "docs/tasks/t001_alpha/progress.md").exists()
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/progress.md")
    assert fm["status"] == "backlog"


def test_drop_from_main_rejects_stale_backlog_with_unmerged_done_branch(git_repo):
    """drop 探测到未合并 done 分支，拒绝重复标 dropped。"""
    _start(git_repo)
    _finish_commit_cleanup(git_repo, "t001", "alpha")

    result = _task_cli(git_repo, "drop", "t001", "--reason", "x")

    assert result.returncode != 0
    assert "滞后" in result.stderr or "status=done" in result.stderr


def test_drop_allows_genuine_fresh_backlog(git_repo):
    """未 start 的真 backlog 仍可正常 drop；原位写 dropped，不生成派生索引。"""
    result = _task_cli(git_repo, "drop", "t001", "--reason", "不需要了")

    assert result.returncode == 0, result.stderr
    fm, _ = parse_front_matter(
        git_repo / "docs/tasks/t001_alpha/progress.md"
    )
    assert fm["status"] == "dropped"
    assert "不需要了" in fm["note"]
    assert not (git_repo / "docs/tasks_index.json").exists()


def test_drop_keeps_task_in_place_without_archive(git_repo):
    """drop 原位写 status: dropped，目录保留在 docs/tasks/{tid}_{slug}，杜绝归档搬移。"""
    result = _task_cli(git_repo, "drop", "t001", "--reason", "测试原位 drop")
    assert result.returncode == 0, result.stderr
    fm, _ = parse_front_matter(
        git_repo / "docs/tasks/t001_alpha/progress.md"
    )
    assert fm["status"] == "dropped"
    assert (git_repo / "docs/tasks/t001_alpha").is_dir()
    assert not (git_repo / "docs/archive/tasks/t001_alpha").exists()


def test_missing_testing_sections_parser_matrix():
    """testing.md 章节解析矩阵：fence、缩进代码、小节、占位回显、空章节。"""
    from repo_task.lifecycle import _missing_testing_sections

    fence_cmd = "```bash\n# 注释在 fence 内不算标题\npytest -q\n```\n"
    matrix = {
        "模板默认": (
            "# 测试\n\n`{doctor_cmd}` 说明。\n\n## doctor_cmd\n\n"
            + fence_cmd + "\n## test_cmd\n\n```bash\npytest -q\n```\n\n## blackbox_verify\n\n无\n",
            [],
        ),
        "缩进代码块首行井号": (
            "## doctor_cmd\n\n    # 环境检查\n    pytest --collect-only\n\n"
            "## test_cmd\n\n    pytest -q\n\n## blackbox_verify\n\n无\n",
            [],
        ),
        "章节内小节": (
            "## doctor_cmd\n\n### 前置\npytest --collect-only\n\n"
            "## test_cmd\n\npytest -q\n\n## blackbox_verify\n\n无\n",
            [],
        ),
        "单行引用其他占位符": (
            "## doctor_cmd\n\npytest\n\n## test_cmd\n\npytest -q\n\n"
            "## blackbox_verify\n\n同 {test_cmd}\n",
            [],
        ),
        "缺全部章节": ("# 测试\n\n说明，无章节。\n",
                     ["{doctor_cmd}", "{test_cmd}", "{blackbox_verify}"]),
        "空章节": ("## doctor_cmd\n\n## test_cmd\n\n```bash\npytest -q\n```\n\n"
                 "## blackbox_verify\n\n无\n",
                 ["{doctor_cmd}"]),
        "占位回显待填": ("## doctor_cmd\n\n`{doctor_cmd}` 待填\n\n## test_cmd\n\npytest\n",
                     ["{doctor_cmd}", "{blackbox_verify}"]),
        "大写反引号标题": ("## `DOCTOR_CMD`\n\npytest\n",
                       ["{test_cmd}", "{blackbox_verify}"]),
        "四反引号内三反引号与伪标题": (
            "## doctor_cmd\n\n````bash\necho ```\n## fake\n````\n\n"
            "## test_cmd\n\n```bash\npytest -q\n```\n\n## blackbox_verify\n\n无\n",
            [],
        ),
    }
    for name, (text, expected) in matrix.items():
        assert _missing_testing_sections(text) == expected, name


def test_parse_policy_respects_fence_length_and_extracts_bash_blocks():
    """testing.md 章节解析与 documents 口径一致：闭栏须同字符且不短于开栏（Issue #78）。"""
    from repo_task.validation import parse_policy

    text = """# 测试

## verification_policy

project_configured

## verification_notes

全部门禁可执行。

## doctor_cmd

````bash
echo ```
echo doctor
````

## test_cmd

```bash
pytest -q
```

## blackbox_verify

无
"""
    commands = parse_policy(text)
    assert "echo ```" in commands["doctor_cmd"]
    assert commands["test_cmd"] == "pytest -q"
    assert commands["blackbox_verify"] == ""


def test_preflight_ignores_placeholder_explanations_when_sections_are_filled(git_repo):
    testing = git_repo / "docs/blueprint/testing.md"
    testing.parent.mkdir(parents=True, exist_ok=True)
    testing.write_text(
        """# 测试

`{doctor_cmd}` / `{test_cmd}` / `{blackbox_verify}` 的说明。

## verification_policy

project_configured

## verification_notes

该测试显式选择已填命令。

## doctor_cmd

```bash
python3 --version
```

## test_cmd

```bash
pytest -q
```

## blackbox_verify

```bash
python3 --version
```
""",
        encoding="utf-8",
    )
    result = _task_cli(git_repo, "preflight", "t001", "--allow-backlog")
    assert result.returncode == 0, result.stderr
    assert "testing.md 仍有未填占位符" not in result.stdout


def test_edit_rejects_stale_backlog_with_active_worktree(git_repo):
    """edit 拒绝在 main 过期 backlog 上操作。"""
    _start(git_repo)

    result = _task_cli(git_repo, "edit", "t001", "--review-level", "single")

    assert result.returncode != 0
    assert "滞后" in result.stderr


def test_edit_allows_fresh_backlog(git_repo):
    """真 backlog 可正常 edit。"""
    result = _task_cli(git_repo, "edit", "t001", "--review-level", "single")

    assert result.returncode == 0, result.stderr
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/progress.md")
    assert fm["review_level"] == "single"


def test_scan_tasks_at_ref_ignores_nested_task_md(git_repo):
    """scan_tasks_at_ref 忽略 task 目录内嵌套的 progress.md，不误判为独立 task。"""
    _start(git_repo)
    _finish_commit_cleanup(git_repo, "t001", "alpha")
    # 在 t002 目录放一个嵌套 progress.md（模拟附件），提交到 t002 分支
    _start(git_repo, "t002")
    worktree = _worktree_path(git_repo, "t002")
    nested = worktree / "docs/tasks/t002_beta/attachments"
    nested.mkdir(parents=True)
    write_front_matter(
        nested / "progress.md",
        {"tid": "t999", "slug": "fake", "status": "backlog"},
        "nested attachment\n",
    )
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "add nested attachment")

    # --ref 读 t002 分支：嵌套 progress.md 不应被当成独立 task（否则 t999 目录名校验报错）
    tasks = store.scan_tasks_at_ref("t002_beta", repo=ctx.RepoContext.for_root(git_repo))
    tids = [t["tid"] for t in tasks]
    assert "t999" not in tids
    assert "t002" in tids


def test_scan_tasks_at_ref_reports_missing_root_task_md(git_repo):
    """scan_tasks_at_ref 对缺根 progress.md 的目录报数据损坏而非静默忽略。"""
    _start(git_repo)
    _finish_commit_cleanup(git_repo, "t001", "alpha")
    # 在 t002 分支建一个无 progress.md 的目录
    _start(git_repo, "t002")
    worktree = _worktree_path(git_repo, "t002")
    broken = worktree / "docs/tasks/t005_broken"
    broken.mkdir(parents=True)
    (broken / "task.md").write_text("# task\n", encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "add broken dir")

    with pytest.raises(ctx.TaskDataError, match="缺 progress.md"):
        store.scan_tasks_at_ref("t002_beta", repo=ctx.RepoContext.for_root(git_repo))


def test_scan_tasks_at_ref_detects_duplicate_tid_across_active_and_archive(git_repo):
    """验证 scan_tasks_at_ref 在同一 ref 中出现同名 active/archive 时显式报错（Issue #47）。"""
    _git(git_repo, "checkout", "-b", "branch_with_dup")
    t001_a = git_repo / "docs/tasks/t001_dup"
    t001_b = git_repo / "docs/tasks/t001_other"
    t001_a.mkdir(parents=True, exist_ok=True)
    t001_b.mkdir(parents=True, exist_ok=True)
    write_front_matter(
        t001_a / "progress.md",
        {"tid": "t001", "slug": "dup", "status": "backlog"},
        "active t001\n",
    )
    write_front_matter(
        t001_b / "progress.md",
        {"tid": "t001", "slug": "other", "status": "done"},
        "archive t001\n",
    )
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "commit with dup t001")

    with pytest.raises(ctx.TaskDataError, match="重复 tid"):
        store.scan_tasks_at_ref("branch_with_dup", repo=ctx.RepoContext.for_root(git_repo))


def test_link_local_files_excludes_foreign_and_worktree_envs(git_repo):
    """验证 link_local_files 排除已存在 worktree、artifacts、scratch 等目录内的 .env（Issue #46）。"""
    from repo_task import worktrees

    # 1. 主仓根目录合法 .env
    root_env = git_repo / ".env"
    root_env.write_text("ROOT=1\n")

    # 2. 其它目录中的无关 .env
    (git_repo / ".scratch").mkdir(parents=True, exist_ok=True)
    (git_repo / ".scratch/.env").write_text("SCRATCH=1\n")
    (git_repo / "artifacts").mkdir(parents=True, exist_ok=True)
    (git_repo / "artifacts/.env").write_text("ARTIFACTS=1\n")
    (git_repo / "docs").mkdir(parents=True, exist_ok=True)
    (git_repo / "docs/.env").write_text("DOCS=1\n")

    # 3. 其它已登记 worktree 内部的 .env
    other_wt = git_repo / "worktrees/t001_alpha"
    other_wt.mkdir(parents=True, exist_ok=True)
    (other_wt / ".env").write_text("OTHER_WT=1\n")

    new_wt = git_repo / "worktrees/t002_beta"
    new_wt.mkdir(parents=True, exist_ok=True)

    linked = worktrees.link_local_files(new_wt, repo=ctx.RepoContext.for_root(git_repo))

    # 只有根目录的 .env 被链入
    assert linked == [".env"]
    assert (new_wt / ".env").is_symlink()
    assert not (new_wt / ".scratch/.env").exists()
    assert not (new_wt / "artifacts/.env").exists()
    assert not (new_wt / "worktrees/t001_alpha/.env").exists()


def test_link_local_files_skips_private_local_dir(git_repo):
    """仓库根 local/ 是用户私有目录：其中备份的 .env 不得被默认 .env 扫描链入 worktree。"""
    from repo_task import worktrees

    (git_repo / ".env").write_text("ROOT=1\n")
    (git_repo / "local/backup").mkdir(parents=True)
    (git_repo / "local/backup/.env").write_text("BACKUP=1\n")
    new_wt = git_repo / "worktrees/t002_beta"
    new_wt.mkdir(parents=True, exist_ok=True)

    linked = worktrees.link_local_files(new_wt, repo=ctx.RepoContext.for_root(git_repo))

    assert linked == [".env"]
    assert not (new_wt / "local").exists()


def _write_worktree_include(repo):
    (repo / ".gitignore").write_text(
        "__pycache__/\n*.py[cod]\ndocs/runtime/\n.env\n.env.*\n/data/\nconfig/local.*\n",
        encoding="utf-8",
    )
    (repo / ".worktreeinclude").write_text(
        "# 本地配置与容器数据\n.env.*\n/data/\nconfig/local.*\nnotes.txt\ndocs/runtime/\n",
        encoding="utf-8",
    )
    (repo / "config").mkdir(exist_ok=True)
    (repo / "config/app.yaml").write_text("tracked: true\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "worktree include")
    (repo / ".env.local").write_text("LOCAL=1\n", encoding="utf-8")
    (repo / "config/local.yaml").write_text("db: local\n", encoding="utf-8")
    (repo / "data/db").mkdir(parents=True)
    (repo / "data/db/state.db").write_text("rows\n", encoding="utf-8")
    (repo / "notes.txt").write_text("用户在途未跟踪文件\n", encoding="utf-8")
    (repo / "docs/runtime").mkdir(parents=True, exist_ok=True)
    (repo / "docs/runtime/probe.json").write_text("{}\n", encoding="utf-8")


def test_start_links_worktree_include_ignored_paths(git_repo):
    """.worktreeinclude 命中的 gitignore 文件与整目录软链进 worktree；
    未被忽略的未跟踪文件、控制面目录与已入库文件不链接。"""
    _write_worktree_include(git_repo)

    _start(git_repo)

    wt = _worktree_path(git_repo)
    for rel in (".env.local", "config/local.yaml", "data"):
        assert (wt / rel).is_symlink(), rel
    assert (wt / "data/db/state.db").read_text(encoding="utf-8") == "rows\n"
    assert not (wt / "notes.txt").exists()
    assert not (wt / "docs/runtime/probe.json").exists()
    assert not (wt / "config/app.yaml").is_symlink()
    # 目录软链不匹配 `/data/` 这类仅目录规则；须仍被忽略，不能被 git add 误暂存。
    status = _git(wt, "status", "--porcelain").stdout.splitlines()
    assert status == [" M docs/tasks/t001_alpha/progress.md"]
    # 托管 exclude 只补软链路径：主仓未被忽略的在途文件仍可见。
    assert _git(git_repo, "status", "--porcelain").stdout.splitlines() == ["?? notes.txt"]
    common = Path(_git(git_repo, "rev-parse", "--git-common-dir").stdout.strip())
    exclude = (common if common.is_absolute() else git_repo / common) / "info/exclude"
    assert "/data" in exclude.read_text(encoding="utf-8").splitlines()


def test_worktree_removal_keeps_linked_primary_data(git_repo):
    """清理 worktree 只删软链本身，主仓被链接的目录与文件完好。"""
    from repo_task import worktrees

    _write_worktree_include(git_repo)
    _start(git_repo)
    wt = _worktree_path(git_repo)
    repo_ctx = ctx.RepoContext.for_root(git_repo)
    rel = repo_ctx.worktree_rel("t001")
    _git(wt, "commit", "-am", "active")

    removed, message = worktrees.remove_worktree(rel, expected_branch="t001_alpha", repo=repo_ctx)

    assert removed, message
    assert not wt.exists()
    assert (git_repo / "data/db/state.db").read_text(encoding="utf-8") == "rows\n"
    assert (git_repo / ".env.local").read_text(encoding="utf-8") == "LOCAL=1\n"


def test_forced_worktree_discard_does_not_follow_unmanaged_dir_link(git_repo):
    """即便遗留非托管目录软链，强制丢弃 worktree 也不穿透删除主仓目标。"""
    from repo_task import worktrees

    _write_worktree_include(git_repo)
    _start(git_repo)
    wt = _worktree_path(git_repo)
    (wt / "extra_data").symlink_to(git_repo / "data")

    repo_ctx = ctx.RepoContext.for_root(git_repo)
    removed, message = worktrees.discard_worktree(repo_ctx.worktree_rel("t001"), repo=repo_ctx)

    assert removed, message
    assert not wt.exists()
    assert (git_repo / "data/db/state.db").read_text(encoding="utf-8") == "rows\n"


def test_rewind_audit_failure_persists_trace_and_reports_real_state(git_repo, monkeypatch):
    """审计失败时按实际状态提示，并把结果写入 note 以便追溯（Issue #76）。"""
    _start(git_repo, "t001")

    def fail_audit(*args, **kwargs):
        raise ctx.TaskDataError("audit unavailable")

    monkeypatch.setattr(lifecycle, "append_audit", fail_audit)
    with pytest.raises(SystemExit, match="审计写入失败"):
        lifecycle.cmd_rewind(
            argparse.Namespace(
                tid="t001", to=None, reason="audit failure", yes=True,
                repo=ctx.RepoContext.for_root(git_repo),
            )
        )
    note = git_repo / "docs/tasks/t001_alpha/progress.md"
    fm, body = parse_front_matter(note)
    assert fm["status"] == "backlog"
    assert "rewind audit failed" in fm["note"]
    assert not (git_repo / "worktrees/t001_alpha").exists()


def test_cli_edit_symlink_note_fails_with_unified_error(git_repo):
    """软链 progress.md 的写入拒绝应经 CLI 统一出口，而不是裸 traceback（Issue #76）。"""
    note = git_repo / "docs/tasks/t001_alpha/progress.md"
    target = git_repo / "docs/tasks/t001_alpha/real_progress.md"
    target.write_text(note.read_text(encoding="utf-8"), encoding="utf-8")
    note.unlink()
    note.symlink_to(target)

    result = _task_cli(git_repo, "edit", "t001", "--note", "changed")
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "does not support writing to symlink" in result.stderr
    assert "数据不一致" in result.stderr


def test_rewind_write_failure_appends_failed_audit_marker(git_repo, monkeypatch):
    """验证 rewind 状态写入失败时记录 rewind_failed 审计标记，权威状态与审计不分叉（Issue #36）。"""
    from repo_task import lifecycle
    import argparse

    _start(git_repo, "t001")
    audit_file = git_repo / "docs/runtime/tasks_audit.log"

    def fail_write(*args, **kwargs):
        raise OSError("injected disk failure")

    monkeypatch.setattr(lifecycle, "write_front_matter", fail_write)

    # 尝试 rewind，预期失败退出
    with pytest.raises(SystemExit, match="rewind 状态写入失败"):
        lifecycle.cmd_rewind(argparse.Namespace(
            tid="t001", to=None, reason="test failure", yes=True,
            repo=ctx.RepoContext.for_root(git_repo),
        ))

    audit_text = audit_file.read_text(encoding="utf-8")
    assert "rewind_failed" in audit_text
    assert "injected disk failure" in audit_text




# --------------------------------------------------------------------------
# task 调度图与 view
# --------------------------------------------------------------------------

def test_add_initializes_empty_schedule_edges_without_status(git_repo):
    result = _task_cli(git_repo, "add", "--title", "delta", "--slug", "delta")

    assert result.returncode == 0, result.stderr
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t004_delta/progress.md")
    assert fm["depends_on"] == ""
    assert fm["conflicts_with"] == ""
    assert "schedule_status" not in fm


def test_edit_updates_dependencies_and_one_sided_conflict_hint(git_repo):
    dependency = _task_cli(
        git_repo,
        "edit",
        "t002",
        "--depends-on",
        "t003,t001,t003",
    )
    # t002 已依赖 t001/t003，t001↔t002 冲突边会被 L1 冗余门禁拒绝；
    # 单向 conflict hint 用无依赖路径的 t003 验证
    conflict = _task_cli(
        git_repo,
        "edit",
        "t001",
        "--conflicts-with",
        "t003",
    )

    assert dependency.returncode == 0, dependency.stderr
    assert conflict.returncode == 0, conflict.stderr
    first, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/progress.md")
    second, _ = parse_front_matter(git_repo / "docs/tasks/t002_beta/progress.md")
    third, _ = parse_front_matter(git_repo / "docs/tasks/t003_gamma/progress.md")
    assert second["depends_on"] == "t001,t003"
    assert "schedule_status" not in second
    assert first["conflicts_with"] == "t003"
    assert third.get("conflicts_with", "") == ""

    removed = _task_cli(git_repo, "edit", "t001", "--conflicts-remove", "t003")
    assert removed.returncode == 0, removed.stderr
    first, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/progress.md")
    third, _ = parse_front_matter(git_repo / "docs/tasks/t003_gamma/progress.md")
    assert first["conflicts_with"] == ""
    assert third.get("conflicts_with", "") == ""


def test_conflicts_remove_clears_peer_only_declaration(git_repo):
    declared = _task_cli(git_repo, 'edit', 't003', '--conflicts-with', 't001')
    assert declared.returncode == 0, declared.stderr
    removed = _task_cli(git_repo, 'edit', 't001', '--conflicts-remove', 't003')
    assert removed.returncode == 0, removed.stderr
    first, _ = parse_front_matter(git_repo / 'docs/tasks/t001_alpha/progress.md')
    third, _ = parse_front_matter(git_repo / 'docs/tasks/t003_gamma/progress.md')
    assert first['conflicts_with'] == ''
    assert third['conflicts_with'] == ''
    view = _task_cli(git_repo, 'view')
    assert 't001 ↔ t003' not in view.stdout


def test_view_warns_when_dependency_ready_tasks_conflict(git_repo):
    result = _task_cli(git_repo, 'edit', 't001', '--conflicts-with', 't003')
    assert result.returncode == 0, result.stderr
    view = _task_cli(git_repo, 'view')
    assert view.returncode == 0, view.stderr
    assert '下一批可跑（冲突项勿并行，分链以 plan 为准）' in view.stdout
    assert '可跑但互相冲突' in view.stdout
    assert 't001 ↔ t003  — 不要并行启动' in view.stdout


def test_edit_allows_conflict_hint_to_active_task_without_peer_write(git_repo):
    _start(git_repo, "t001")
    result = _task_cli(git_repo, "edit", "t002", "--conflicts-with", "t001")
    assert result.returncode == 0, result.stderr
    second, _ = parse_front_matter(git_repo / "docs/tasks/t002_beta/progress.md")
    assert second["conflicts_with"] == "t001"


def test_edit_skips_reverse_edge_for_done_target_in_main(git_repo):
    """done target 已合 main、归档不可写：owner 单边增删 conflicts，
    不再因 peer.status=done 而卡死。"""
    _start(git_repo, "t001")
    identity, _, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    for extra in ((), ("--continue",)):
        merged = _task_cli(git_repo, "integrate", "t001", *_identity_args(identity), *extra)
        assert merged.returncode == 0, merged.stderr
    # t001 已归档 done；t002 单边声明冲突应成功（不写 t001 反向边）
    declared = _task_cli(git_repo, "edit", "t002", "--conflicts-with", "t001")
    assert declared.returncode == 0, declared.stderr
    t002_fm, _ = parse_front_matter(git_repo / "docs/tasks/t002_beta/progress.md")
    assert t002_fm["conflicts_with"] == "t001"
    t001_fm, _ = parse_front_matter(
        git_repo / "docs/tasks/t001_alpha/progress.md"
    )
    assert t001_fm.get("conflicts_with", "") == ""

    removed = _task_cli(git_repo, "edit", "t002", "--conflicts-remove", "t001")
    assert removed.returncode == 0, removed.stderr
    t002_fm, _ = parse_front_matter(git_repo / "docs/tasks/t002_beta/progress.md")
    assert t002_fm["conflicts_with"] == ""




def test_drop_rejects_referenced_task(git_repo):
    result = _task_cli(git_repo, "edit", "t002", "--depends-on", "t001")
    assert result.returncode == 0, result.stderr

    dropped = _task_cli(git_repo, "drop", "t001", "--reason", "obsolete")

    assert dropped.returncode != 0
    assert "t002.depends_on" in dropped.stderr
    assert (git_repo / "docs/tasks/t001_alpha/progress.md").exists()


def test_view_dag_conflicts_and_groups(git_repo):
    """view 输出全景：下一批、被依赖阻塞、被冲突阻塞分组展示。"""
    commands = (
        ("t002", "--depends-on", "t001"),
        ("t003", "--conflicts-with", "t002"),
    )
    for command in commands:
        result = _task_cli(git_repo, "edit", *command)
        assert result.returncode == 0, result.stderr

    first = _task_cli(git_repo, "view")

    assert first.returncode == 0, first.stderr
    # t001、t003 无依赖且无 active 冲突，进下一批
    assert "▸ 下一批可跑" in first.stdout
    assert "t001" in first.stdout and "t003" in first.stdout
    # t002 依赖 t001，进被依赖阻塞组
    assert "▸ 被依赖阻塞" in first.stdout
    assert "t001 → t002" in first.stdout


def test_view_shows_active_conflict_block(git_repo):
    """active task 占资源阻塞冲突方；done（不论是否合 main）即释放，冲突方解阻塞。"""
    conflict = _task_cli(git_repo, "edit", "t001", "--conflicts-with", "t002")
    assert conflict.returncode == 0, conflict.stderr
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "schedule tasks")

    _start(git_repo, "t001")
    active = _task_cli(git_repo, "view")
    assert active.returncode == 0, active.stderr
    assert "▸ 被冲突阻塞" in active.stdout
    assert "t002 ↔ t001" in active.stdout
    assert "t002 ↔ t001  — t002: beta" in active.stdout

    _finish_commit_cleanup(git_repo, "t001", "alpha")
    # t001 done（完成口径）即释放资源；即使未合 main，t002 也解冲突进可跑。
    unmerged = _task_cli(git_repo, "view")
    assert unmerged.returncode == 0, unmerged.stderr
    assert "▸ 下一批可跑" in unmerged.stdout
    assert "t002" in unmerged.stdout
    assert "未入 main" in unmerged.stdout


def test_view_handles_diamond_dependencies(git_repo):
    """菱形依赖：t003 依赖 t001+t002，二者未完成时 t003 进被依赖阻塞组。"""
    commands = (("t003", "--depends-on", "t001,t002"),)
    for command in commands:
        result = _task_cli(git_repo, "edit", *command)
        assert result.returncode == 0, result.stderr

    result = _task_cli(git_repo, "view")

    assert result.returncode == 0, result.stderr
    assert "▸ 下一批可跑" in result.stdout
    assert "t001" in result.stdout and "t002" in result.stdout
    assert "▸ 被依赖阻塞" in result.stdout
    assert "t001 → t003" in result.stdout
    assert "t002 → t003" in result.stdout


def test_view_resolves_done_task_in_place_to_unblock_dependents(git_repo):
    """主干原位 done task 正常解开下游依赖。"""
    dependency = _task_cli(
        git_repo,
        "edit",
        "t002",
        "--depends-on",
        "t001",
    )
    assert dependency.returncode == 0, dependency.stderr
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "schedule dependency")

    _start(git_repo, "t001")
    _, branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    _git(git_repo, "merge", "--ff-only", branch)

    result = _task_cli(git_repo, "view")

    assert result.returncode == 0, result.stderr
    # t001 done 后 t002 解依赖，进下一批
    assert "▸ 下一批可跑" in result.stdout
    assert "t002" in result.stdout


def test_view_rejects_dependency_cycle(git_repo):
    """view 对依赖环报错（edit 已在写入前拦截新环，此处构造历史脏数据）。"""
    first_path = git_repo / "docs/tasks/t001_alpha/progress.md"
    first, first_body = parse_front_matter(first_path)
    first["depends_on"] = "t002"
    write_front_matter(first_path, first, first_body)
    second_path = git_repo / "docs/tasks/t002_beta/progress.md"
    second, second_body = parse_front_matter(second_path)
    second["depends_on"] = "t001"
    write_front_matter(second_path, second, second_body)

    result = _task_cli(git_repo, "view")

    assert result.returncode != 0
    assert "view=FAIL：invalid_graph: depends_on cycle" in result.stderr


def test_edit_rejects_dependency_cycle_before_write(git_repo):
    """A30：edit 在写盘前检测依赖环，拒绝持久化无效图。"""
    first = _task_cli(git_repo, "edit", "t001", "--depends-on", "t002")
    assert first.returncode == 0, first.stderr

    second = _task_cli(git_repo, "edit", "t002", "--depends-on", "t001")

    assert second.returncode != 0
    assert "依赖环" in second.stderr
    # progress.md 未被污染，index 保持上一次重建结果
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t002_beta/progress.md")
    assert fm.get("depends_on", "") == ""
    first_fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/progress.md")
    assert first_fm["depends_on"] == "t002"


def test_edit_rejects_multi_node_cycle(git_repo):
    """A30：多节点间接环同样在写盘前拒绝。"""
    for command in (
        ("t001", "--depends-on", "t002"),
        ("t002", "--depends-on", "t003"),
    ):
        result = _task_cli(git_repo, "edit", *command)
        assert result.returncode == 0, result.stderr

    blocked = _task_cli(git_repo, "edit", "t003", "--depends-on", "t001")

    assert blocked.returncode != 0
    assert "依赖环" in blocked.stderr
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t003_gamma/progress.md")
    assert fm.get("depends_on", "") == ""


def test_edit_cycle_resolved_by_dependency_removal(git_repo):
    """A30：移除依赖解除环后，edit 允许继续修改并持久化。"""
    first = _task_cli(git_repo, "edit", "t001", "--depends-on", "t002")
    assert first.returncode == 0, first.stderr
    # 手动注入 t002 -> t001 环，模拟绕过 edit 的历史脏数据
    second_path = git_repo / "docs/tasks/t002_beta/progress.md"
    second, second_body = parse_front_matter(second_path)
    second["depends_on"] = "t001"
    write_front_matter(second_path, second, second_body)
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "inject cycle")

    removed = _task_cli(git_repo, "edit", "t001", "--depends-remove", "t002")

    assert removed.returncode == 0, removed.stderr
    first_fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/progress.md")
    assert first_fm["depends_on"] == ""




def test_view_reads_historical_conflict_as_undirected(git_repo):
    """单向冲突声明（历史脏数据）按无向处理：双方互相阻塞，都进冲突组。"""
    first_path = git_repo / "docs/tasks/t001_alpha/progress.md"
    first, first_body = parse_front_matter(first_path)
    first["conflicts_with"] = "t002"
    write_front_matter(first_path, first, first_body)

    second_path = git_repo / "docs/tasks/t002_beta/progress.md"
    second, second_body = parse_front_matter(second_path)
    second["conflicts_with"] = ""
    write_front_matter(second_path, second, second_body)

    result = _task_cli(git_repo, "view")

    assert result.returncode == 0, result.stderr
    # 单向声明按无向并发提示读取；无 active 占用时双方都依赖就绪。
    assert "▸ 下一批可跑" in result.stdout
    assert "t001" in result.stdout and "t002" in result.stdout
    assert "▸ 被冲突阻塞" not in result.stdout




def test_view_rejects_dangling_and_dropped_references(git_repo):
    """view 图校验：依赖/冲突引用不存在或 dropped task 均报错。"""
    first_path = git_repo / "docs/tasks/t001_alpha/progress.md"
    first, first_body = parse_front_matter(first_path)
    first["depends_on"] = "t999"
    write_front_matter(first_path, first, first_body)

    dangling = _task_cli(git_repo, "view")

    assert dangling.returncode != 0
    assert "invalid_graph: t001.depends_on 引用不存在 task t999" in dangling.stderr

    first["depends_on"] = ""
    first["conflicts_with"] = "t999"
    write_front_matter(first_path, first, first_body)
    dangling_conflict = _task_cli(git_repo, "view")

    assert dangling_conflict.returncode != 0
    assert "invalid_graph: t001.conflicts_with 引用不存在 task t999" in dangling_conflict.stderr

    first["conflicts_with"] = ""
    write_front_matter(first_path, first, first_body)
    dropped = _task_cli(git_repo, "drop", "t003", "--reason", "obsolete")
    assert dropped.returncode == 0, dropped.stderr
    first["depends_on"] = "t003"
    write_front_matter(first_path, first, first_body)

    stale = _task_cli(git_repo, "view")

    assert stale.returncode != 0
    assert "invalid_graph: t001.depends_on 引用 dropped task t003" in stale.stderr

    first["depends_on"] = ""
    first["conflicts_with"] = "t003"
    write_front_matter(first_path, first, first_body)
    stale_conflict = _task_cli(git_repo, "view")

    assert stale_conflict.returncode != 0
    assert "invalid_graph: t001.conflicts_with 引用 dropped task t003" in stale_conflict.stderr


def test_drop_ignores_done_task_historical_edges(git_repo):
    """已完成 done task 的历史调度边不应锁死活跃 task 的 drop。"""
    done_task = git_repo / "docs/tasks/t099_done"
    done_task.mkdir(parents=True)
    template_task = git_repo / ".repo_template/docs/task_template/progress.md"
    _, body = parse_front_matter(template_task)
    write_front_matter(
        done_task / "progress.md",
        {
            "tid": "t099",
            "slug": "done",
            "title": "done",
            "status": "done",
            "branch": "",
            "worktree": "",
            "review_level": "full",
            "diff_anchor": "",
            "depends_on": "t003",
            "conflicts_with": "",
            "note": "",
        },
        body,
    )

    result = _task_cli(git_repo, "drop", "t003", "--reason", "obsolete")

    assert result.returncode == 0, result.stderr
    assert (git_repo / "docs/tasks/t003_gamma").is_dir()
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t003_gamma/progress.md")
    assert fm["status"] == "dropped"


def test_edit_title_not_blocked_by_stale_dependency_edges(git_repo):
    """--title 等无关编辑不应被历史脏数据 depends_on/conflicts_with 卡住。"""
    first_path = git_repo / "docs/tasks/t001_alpha/progress.md"
    first, first_body = parse_front_matter(first_path)
    first["depends_on"] = "t999"
    first["conflicts_with"] = "t999"
    write_front_matter(first_path, first, first_body)

    result = _task_cli(git_repo, "edit", "t001", "--title", "renamed")

    assert result.returncode == 0, result.stderr
    updated, _ = parse_front_matter(first_path)
    assert updated["title"] == "renamed"


def test_edit_supports_append_remove_and_clear_for_schedule_edges(git_repo):
    commands = (
        ("t001", "--depends-append", "t003"),
        ("t001", "--depends-append", "t002"),
        ("t001", "--depends-remove", "t003"),
        ("t001", "--depends-on", ""),
        ("t001", "--conflicts-append", "t003"),
        ("t001", "--conflicts-append", "t002"),
        ("t001", "--conflicts-remove", "t003"),
        ("t001", "--conflicts-with", ""),
    )
    for command in commands:
        result = _task_cli(git_repo, "edit", *command)
        assert result.returncode == 0, result.stderr

    first, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/progress.md")
    second, _ = parse_front_matter(git_repo / "docs/tasks/t002_beta/progress.md")
    third, _ = parse_front_matter(git_repo / "docs/tasks/t003_gamma/progress.md")
    assert first["depends_on"] == ""
    assert first["conflicts_with"] == ""
    assert second.get("conflicts_with", "") == ""
    assert third.get("conflicts_with", "") == ""


def _ledger(repo):
    path = repo / "docs" / "runtime" / "dispatch_ledger.jsonl"
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text("utf-8").splitlines()
        if line.strip()
    ]


def test_start_only_writes_topology_event_and_does_not_create_attempt(git_repo):
    initial = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    result = _task_cli(git_repo, "start", "t001")
    assert result.returncode == 0, result.stderr
    worktree = git_repo.parent / "repo_t001"
    fm, _ = parse_front_matter(worktree / "docs/tasks/t001_alpha/progress.md")
    assert fm["status"] == "active"
    assert fm["diff_anchor"] == initial
    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == initial
    events = _ledger(git_repo)
    assert [event["event"] for event in events] == ["start"]
    assert "attempt" not in events[0]
    assert "execution_id" not in events[0]


def test_attempt_reserve_requires_start(git_repo):
    """RT-006：reserve 须在 start 之后（active + worktree 已登记）。

    原 test_attempt_reserve_and_start_are_separate_events 固化 reserve-before-start；
    该顺序会在 start 失败时留下无 worktree 的孤儿 running identity，审查判定为 bug，
    改写为门禁语义：reserve 在 start 前被拒，start 后成功。
    """
    reserved = _task_cli(
        git_repo, "attempt", "reserve", "t001", "--model", "opus"
    )
    assert reserved.returncode != 0
    assert "reserve 须在 start 之后" in reserved.stderr
    started = _task_cli(git_repo, "start", "t001")
    assert started.returncode == 0, started.stderr
    reserved = _task_cli(
        git_repo, "attempt", "reserve", "t001", "--model", "opus"
    )
    assert reserved.returncode == 0, reserved.stderr


def test_chain_start_can_still_use_completed_branch_as_topology_base(git_repo):
    first = _task_cli(git_repo, "start", "t001")
    assert first.returncode == 0, first.stderr
    worktree = git_repo.parent / "repo_t001"
    _check_ac(worktree, "t001", "alpha")
    mark_done(worktree, "t001")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "feat(t001): done")
    _git(git_repo, "worktree", "remove", str(worktree))

    second = _task_cli(git_repo, "start", "t002", "--base", "t001_alpha")
    assert second.returncode == 0, second.stderr
    second_worktree = git_repo.parent / "repo_t002"
    assert _git(
        second_worktree, "merge-base", "--is-ancestor", "t001_alpha", "HEAD", check=False
    ).returncode == 0


def test_chain_start_base_names_uncommitted_finish_as_cause(git_repo):
    """finish 未提交时，--base 拒绝信息须指明「完成状态尚未提交」而非「须先完成」。"""
    first = _task_cli(git_repo, "start", "t001")
    assert first.returncode == 0, first.stderr
    worktree = git_repo.parent / "repo_t001"
    _check_ac(worktree, "t001", "alpha")
    mark_done(worktree, "t001")

    second = _task_cli(git_repo, "start", "t002", "--base", "t001_alpha")
    assert second.returncode != 0
    assert "status='backlog'" in second.stderr
    assert "完成状态尚未提交" in second.stderr
    assert second.stderr.index("有效状态为 done") < second.stderr.index("task commit 再重试")
    # 对照：前置真正未完成（worktree 强制移除丢弃未提交 finish）时保持原报错，不带提示
    _git(git_repo, "worktree", "remove", "--force", str(worktree))
    plain = _task_cli(git_repo, "start", "t002", "--base", "t001_alpha")
    assert plain.returncode != 0
    assert "须先完成或 drop" in plain.stderr
    assert "完成状态尚未提交" not in plain.stderr


# --------------------------------------------------------------------------
# effective-status：有效状态与读取来源（worktree / branch / main）
# --------------------------------------------------------------------------


def test_effective_sources_all_backlog_main(git_repo):
    sources = store.discover_effective_sources(repo=ctx.RepoContext.for_root(git_repo))
    assert set(sources) == {"t001", "t002", "t003"}
    for tid in ("t001", "t002", "t003"):
        assert sources[tid]["status"] == "backlog"
        assert sources[tid]["source"] == "main"
        assert sources[tid]["read_at"] is None


def test_effective_sources_active_worktree(git_repo):
    _start(git_repo, "t001")
    sources = store.discover_effective_sources(repo=ctx.RepoContext.for_root(git_repo))
    assert sources["t001"]["status"] == "active"
    assert sources["t001"]["source"] == "worktree"
    assert sources["t001"]["read_at"] == str(_worktree_path(git_repo, "t001"))
    # 未 start 的 task 仍从主干读
    assert sources["t002"]["source"] == "main"


def test_effective_sources_completed_branch_after_cleanup(git_repo):
    _start(git_repo, "t001")
    _, branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    sources = store.discover_effective_sources(repo=ctx.RepoContext.for_root(git_repo))
    assert sources["t001"]["status"] == "done"
    assert sources["t001"]["source"] == "branch"
    assert sources["t001"]["read_at"] == branch


def test_effective_status_cli_output(git_repo, capsys):
    from repo_task.control import cmd_effective_status
    import argparse
    cmd_effective_status(argparse.Namespace(status=None, repo=ctx.RepoContext.for_root(git_repo)))
    out = capsys.readouterr().out
    assert "t001" in out and "backlog" in out and "main" in out

    _start(git_repo, "t001")
    cmd_effective_status(argparse.Namespace(status=None, repo=ctx.RepoContext.for_root(git_repo)))
    out = capsys.readouterr().out
    assert "worktree" in out
    assert str(_worktree_path(git_repo, "t001")) in out


def test_effective_discovery_exposes_corrupt_branch_and_blocks_scheduling(git_repo):
    """A05：未合并分支的 progress.md 损坏时，不得静默回落为 main 的 backlog；
    状态观察入口展示 corrupt 诊断，调度与写操作直接阻断。
    """
    _git(git_repo, "checkout", "-b", "t001_alpha")
    note = git_repo / "docs/tasks/t001_alpha/progress.md"
    note.write_text("---\ntid: t001\ntid: t001\n---\nbody\n", encoding="utf-8")
    _git(git_repo, "add", str(note.relative_to(git_repo)))
    _git(git_repo, "commit", "-m", "corrupt front matter on branch")
    _git(git_repo, "checkout", "main")

    # 1. 观察入口：展示 corrupt 与来源 branch，不回落为 main backlog
    sources = store.discover_effective_sources(repo=ctx.RepoContext.for_root(git_repo))
    assert sources["t001"]["status"] == "corrupt"
    assert sources["t001"]["source"] == "branch"
    assert sources["t001"]["read_at"] == "t001_alpha"
    assert "front matter 重复键" in sources["t001"]["note"]

    ps_res = _task_cli(git_repo, "ps")
    assert ps_res.returncode == 0, ps_res.stderr
    assert "t001" in ps_res.stdout and "corrupt" in ps_res.stdout

    # 2. 调度与写操作入口：阻断，不按旧 backlog 放行
    with pytest.raises(ctx.TaskDataError, match="数据损坏"):
        store.discover_effective_tasks(repo=ctx.RepoContext.for_root(git_repo))
    assert _task_cli(git_repo, "view").returncode != 0
    assert _task_cli(git_repo, "plan").returncode != 0
    assert _task_cli(git_repo, "start", "t002").returncode != 0


def test_backlog_merge_source_close_failure_restores_graph_and_all_source_directories(git_repo, monkeypatch):
    from repo_task.graph import cmd_merge_backlog
    from repo_task.ledger import ledger_read
    from repo_task import lifecycle
    repo_ctx = ctx.RepoContext.for_root(git_repo)
    ledger_read(repo=repo_ctx)  # Include the observational control lock in the initial baseline.
    before = {p.relative_to(git_repo): p.read_bytes() for p in (git_repo / 'docs').rglob('*') if p.is_file()}

    original_close = lifecycle._close_task

    def fail_close(args, status, note, **kwargs):
        if args.tid == 't002':
            raise OSError('simulated second source drop failure')
        return original_close(args, status, note, **kwargs)

    monkeypatch.setattr("repo_task.lifecycle._close_task", fail_close)
    with pytest.raises(ctx.TaskDataError, match='已恢复图与来源元数据'):
        cmd_merge_backlog(argparse.Namespace(tid='t003', sources='t001,t002', repo=repo_ctx))
    after = {p.relative_to(git_repo): p.read_bytes() for p in (git_repo / 'docs').rglob('*') if p.is_file()}
    assert after == before


def test_find_merge_commit_handles_deep_merge_history(git_repo):
    """验证超过 50 个无关 merge 后较早的 task merge 依然能通过祖先路径正确识别（Issue #62）。"""
    from repo_task.integration import _find_merge_commit

    # 1. 创建 task 分支并提交一个 commit
    _git(git_repo, "checkout", "-b", "t001_branch")
    dummy = git_repo / "dummy.txt"
    dummy.write_text("dummy\n")
    _git(git_repo, "add", "dummy.txt")
    _git(git_repo, "commit", "-m", "feat(task): work on t001")
    second_parent = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    # 2. 合并入 main 并带前缀
    _git(git_repo, "checkout", "main")
    _git(git_repo, "merge", "--no-ff", "t001_branch", "-m", "task: integrate t001_alpha")
    merge_sha = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    # 3. 产生 55 个无关的 merge commit
    for i in range(55):
        branch = f"unrelated_{i}"
        _git(git_repo, "checkout", "-b", branch)
        f = git_repo / f"file_{i}.txt"
        f.write_text(f"{i}\n")
        _git(git_repo, "add", f"file_{i}.txt")
        _git(git_repo, "commit", "-m", f"commit {i}")
        _git(git_repo, "checkout", "main")
        _git(git_repo, "merge", "--no-ff", branch, "-m", f"merge {i}")

    # 4. _find_merge_commit 应当跨越这 55 个无关 merge 找到原 merge commit
    from repo_task.context import RepoContext
    found = _find_merge_commit(
        second_parent, "task: integrate t001", repo=RepoContext.for_root(git_repo)
    )
    assert found == merge_sha


@pytest.mark.parametrize('partial', ['branch', 'worktree'])
def test_start_timeout_reports_unconfirmed_resources_without_deleting_them(git_repo, monkeypatch, partial):
    from repo_task import worktrees
    original_git = worktrees._git

    def interrupted_add(args, **kwargs):
        if args[:2] != ['worktree', 'add']:
            return original_git(args, **kwargs)
        if partial == 'branch':
            original_git(['branch', args[3], args[-1]], **kwargs)
        else:
            assert original_git(args, **kwargs).returncode == 0
        raise ctx.TaskDataError('模拟 worktree add 超时')

    monkeypatch.setattr(worktrees, '_git', interrupted_add)
    head = _git(git_repo, 'rev-parse', 'HEAD').stdout.strip()
    with pytest.raises(SystemExit, match='创建结果未确认'):
        integration.cmd_start(argparse.Namespace(tid='t001', base=None, repo=ctx.RepoContext.for_root(git_repo)))
    assert _git(git_repo, 'rev-parse', 'HEAD').stdout.strip() == head
    assert _git(git_repo, 'rev-parse', '--verify', 'refs/heads/t001_alpha').stdout.strip() == head
    assert _worktree_path(git_repo, 't001').exists() == (partial == 'worktree')
    assert parse_front_matter(git_repo / 'docs/tasks/t001_alpha/progress.md')[0]['status'] == 'backlog'


def test_start_failure_does_not_remove_preexisting_worktree(git_repo):
    """验证并发/重复 start 失败时绝不移除已存在会话的合法 worktree 和分支（Issue #32）。"""
    # 会话 A 成功启动
    r1 = _task_cli(git_repo, "start", "t001")
    assert r1.returncode == 0
    wt1 = _worktree_path(git_repo, "t001")
    assert wt1.is_dir()

    # 会话 B 尝试再次启动同一 task，预期因 worktree/分支已存在而失败
    r2 = _task_cli(git_repo, "start", "t001")
    assert r2.returncode != 0

    # 会话 A 的 worktree 和分支必须完好保留
    assert wt1.is_dir()
    branch_ref = _git(git_repo, "rev-parse", "--verify", "refs/heads/t001_alpha")
    assert branch_ref.returncode == 0


def test_issue_90_uncommitted_spike_rejected_until_committed(git_repo):
    """Issue #90 核心场景：A 完成后，B 在主仓工作区修改未提交被拒绝；提交后从 A tip 启动成功。"""
    # 1. 建立 t002 对 t001 的依赖
    edit_res = _task_cli(git_repo, "edit", "t002", "--depends-on", "t001")
    assert edit_res.returncode == 0, edit_res.stderr
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "schedule: t002 depends on t001")

    # 2. 启动并完成 t001 (A)，保留分支 t001_alpha（未合并到 main）
    _start(git_repo, "t001")
    identity1, branch1, tip1 = _finish_commit_cleanup(git_repo, "t001", "alpha")

    # 3. 在主仓工作区修改 t002 的 task.md，模拟 spike 回填消除 UNVERIFIED-BLOCKING，但不提交
    b_spec = git_repo / "docs/tasks/t002_beta/task.md"
    content = b_spec.read_text(encoding="utf-8")
    b_spec.write_text(content + "\n<!-- spike 回填未提交 -->\n", encoding="utf-8")

    # 4. 显式与自动 base 启动均被脏检查拦截，报错且不创建 worktree/分支
    r_explicit = _task_cli(git_repo, "start", "t002", "--base", branch1)
    assert r_explicit.returncode != 0
    assert "start=FAIL：docs/tasks/t002_beta 在主干有未提交改动；请先提交后再 start" in r_explicit.stderr
    assert not _worktree_path(git_repo, "t002").exists()

    r_auto = _task_cli(git_repo, "start", "t002")
    assert r_auto.returncode != 0
    assert "start=FAIL：docs/tasks/t002_beta 在主干有未提交改动；请先提交后再 start" in r_auto.stderr

    # 5. 将主干规格提交到主干后，启动成功
    _git(git_repo, "add", "docs/tasks/t002_beta/task.md")
    _git(git_repo, "commit", "-m", "spec(t002): commit verified spike contract")
    spec_commit = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    r_start = _task_cli(git_repo, "start", "t002", "--base", branch1)
    assert r_start.returncode == 0, r_start.stderr

    wt_b = _worktree_path(git_repo, "t002")
    assert wt_b.is_dir()
    fm, _ = parse_front_matter(wt_b / "docs/tasks/t002_beta/progress.md")
    assert fm["status"] == "active"
    assert fm["diff_anchor"] == tip1
    assert fm["spec_source_sha"] == spec_commit
    # 确认 worktree 内物化的正是主干上锁定的规格
    assert "spike 回填未提交" in (wt_b / "docs/tasks/t002_beta/task.md").read_text(encoding="utf-8")


def test_issue_90_source_and_base_sha_equal_path_dirty_guard_and_materialize(git_repo):
    """source SHA == base SHA 路径同样执行脏检查与完整物化。"""
    # 在主仓 t001_alpha 制造未提交改动（unstaged）
    (git_repo / "docs/tasks/t001_alpha/progress.md").write_text(
        (git_repo / "docs/tasks/t001_alpha/progress.md").read_text(encoding="utf-8") + " ",
        encoding="utf-8",
    )
    r_fail = _task_cli(git_repo, "start", "t001")
    assert r_fail.returncode != 0
    assert "start=FAIL：docs/tasks/t001_alpha 在主干有未提交改动；请先提交后再 start" in r_fail.stderr

    # 还原并提交一个主干修改，然后启动
    _git(git_repo, "checkout", "--", "docs/tasks/t001_alpha/progress.md")
    initial_sha = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    r_ok = _task_cli(git_repo, "start", "t001")
    assert r_ok.returncode == 0, r_ok.stderr
    wt = _worktree_path(git_repo, "t001")
    fm, _ = parse_front_matter(wt / "docs/tasks/t001_alpha/progress.md")
    assert fm["diff_anchor"] == initial_sha
    assert fm["spec_source_sha"] == initial_sha


def test_issue_90_uncommitted_new_task_rejected(git_repo):
    """新建 task 未提交报明确错误：尚未提交，请先提交创建后再 start。"""
    add_res = _task_cli(git_repo, "add", "--title", "delta task", "--slug", "delta")
    assert add_res.returncode == 0, add_res.stderr
    r = _task_cli(git_repo, "start", "t004")
    assert r.returncode != 0
    assert "start=FAIL：t004 在主干尚未提交；请先提交创建与调度变更后再 start" in r.stderr


def test_issue_90_dirty_checks_rename_and_unrelated_changes(git_repo):
    """脏检查覆盖 rename 两端、非忽略 untracked 附件，并放行无关主仓脏改动。"""
    # 1. 主仓无关改动（README.md）不影响 t001 启动
    (git_repo / "README.md").write_text("unrelated doc\n", encoding="utf-8")
    r_unrelated = _task_cli(git_repo, "start", "t001")
    assert r_unrelated.returncode == 0, r_unrelated.stderr
    # 回退 t001 以便继续测试
    _task_cli(git_repo, "rewind", "t001", "--reason", "test cleanup")

    # 2. 在 task 目录内增加非忽略未跟踪文件，被拦截
    (git_repo / "docs/tasks/t001_alpha/extra.txt").write_text("attachment\n", encoding="utf-8")
    r_untracked = _task_cli(git_repo, "start", "t001")
    assert r_untracked.returncode != 0
    assert "start=FAIL：docs/tasks/t001_alpha 在主干有未提交改动；请先提交后再 start" in r_untracked.stderr
    (git_repo / "docs/tasks/t001_alpha/extra.txt").unlink()

    # 3. 将文件 git mv 移出 task 目录（测试 rename 来源端检查）
    tracked_file = git_repo / "docs/tasks/t001_alpha/task.md"
    outside = git_repo / "task_moved.md"
    _git(git_repo, "mv", str(tracked_file.relative_to(git_repo)), str(outside.relative_to(git_repo)))
    r_rename_src = _task_cli(git_repo, "start", "t001")
    assert r_rename_src.returncode != 0
    assert "start=FAIL：docs/tasks/t001_alpha 在主干有未提交改动；请先提交后再 start" in r_rename_src.stderr

    # 还原
    _git(git_repo, "reset", "--hard", "HEAD")


def test_issue_90_main_progression_does_not_drift_active_task_review_baseline(git_repo, monkeypatch):
    """主干后续变化不会导致正在执行的 task 的 review 契约基线和初始 review_level 漂移。"""
    # 1. 启动 t001，锁定初始主干快照
    _start(git_repo, "t001")
    wt = _worktree_path(git_repo, "t001")
    fm, _ = parse_front_matter(wt / "docs/tasks/t001_alpha/progress.md")
    locked_source_sha = fm["spec_source_sha"]

    # 2. 主干在此之后被其他任务或人工修改：修改 t001 的 task.md 契约与 review_level 并提交主干
    main_spec = git_repo / "docs/tasks/t001_alpha/task.md"
    main_spec.write_text(main_spec.read_text(encoding="utf-8") + "\n- [ ] AC-999: 主干新增契约\n", encoding="utf-8")
    _git(git_repo, "add", "docs/tasks/t001_alpha/task.md")
    _git(git_repo, "commit", "-m", "drift main spec on purpose")
    new_main_sha = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    assert new_main_sha != locked_source_sha

    # 3. 正在执行的 worktree 内渲染 review prompt：
    # 契约基线依然针对 locked_source_sha，若 worktree 未篡改契约，则无 drift 警告
    import render_review_prompts as rrp
    monkeypatch.setattr(rrp, "REPO_ROOT", wt)
    prompts = rrp.render_review_prompts(wt / "docs/tasks/t001_alpha")
    assert "契约区 drift 警告" not in prompts["code_review_prompt.md"]

    # 4. 若 worktree 内真的篡改了契约，drift 警告以 locked_source_sha 为基线检出
    wt_spec = wt / "docs/tasks/t001_alpha/task.md"
    wt_spec.write_text(wt_spec.read_text(encoding="utf-8").replace("AC-001：", "AC-001：被篡改契约 "), encoding="utf-8")
    prompts_drifted = rrp.render_review_prompts(wt / "docs/tasks/t001_alpha")
    assert "契约区 drift 警告" in prompts_drifted["code_review_prompt.md"]
    assert "被篡改契约" in prompts_drifted["code_review_prompt.md"]
    # 来源显示的是真实的锁定 SHA
    assert f"（{locked_source_sha[:12]}）" in prompts_drifted["code_review_prompt.md"]


def test_issue_90_verify_integrate_ready_checks_spec_source_sha_and_level_downgrade(git_repo):
    """verify_integrate_ready 严格校验 spec_source_sha 的存在、一致性与只升不降。"""
    from repo_task import monitoring
    repo_ctx = ctx.RepoContext.for_root(git_repo)

    _start(git_repo, "t001")
    identity = _reserve(git_repo, "t001")
    wt = _worktree_path(git_repo, "t001")
    _check_ac(wt, "t001", "alpha")
    mark_done(wt, "t001")
    base_sha = _git(wt, "rev-parse", "HEAD").stdout.strip()
    task_dir = wt / "docs/tasks/t001_alpha"
    ensure_review_evidence(wt, task_dir, base_sha)
    scope = review_scope_fingerprint(base_sha, "docs/tasks/t001_alpha", repo_root=wt)
    fm, _ = parse_front_matter(task_dir / "progress.md")
    spec_source_sha = fm["spec_source_sha"]

    # 1. 来源缺失
    bad_payload = _handoff("t001", "t001_alpha", identity, base_sha, review_scope=scope)
    del bad_payload["spec_source_sha"]
    (task_dir / "handoff.json").write_text(json.dumps(bad_payload), encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-m", "commit with missing spec_source_sha")
    verdict, kind, detail = monitoring.verify_integrate_ready("t001", identity["attempt"], identity["execution_id"], repo=repo_ctx)
    assert verdict == "contract"
    assert "缺必填字段 spec_source_sha" in detail

    # 2. 来源无法解析 commit
    _git(wt, "reset", "--soft", "HEAD~1")
    bad_payload["spec_source_sha"] = "0" * 40
    (task_dir / "handoff.json").write_text(json.dumps(bad_payload), encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-m", "commit with unknown spec_source_sha")
    verdict, kind, detail = monitoring.verify_integrate_ready("t001", identity["attempt"], identity["execution_id"], repo=repo_ctx)
    assert verdict == "contract"
    assert "spec_source_sha" in detail

    # 3. 尝试非法降级 review_level (full -> single)
    _git(wt, "reset", "--soft", "HEAD~1")
    wt_progress = task_dir / "progress.md"
    wt_fm, wt_body = parse_front_matter(wt_progress)
    wt_fm["review_level"] = "single"  # 初始在主干是 full，非法降为 single
    write_front_matter(wt_progress, wt_fm, wt_body)
    good_payload = _handoff("t001", "t001_alpha", identity, base_sha, review_scope=scope, spec_source_sha=spec_source_sha)
    (task_dir / "handoff.json").write_text(json.dumps(good_payload), encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-m", "commit with downgraded review_level")
    verdict, kind, detail = monitoring.verify_integrate_ready("t001", identity["attempt"], identity["execution_id"], repo=repo_ctx)
    assert verdict == "contract"
    assert "review_level 被降级" in detail


