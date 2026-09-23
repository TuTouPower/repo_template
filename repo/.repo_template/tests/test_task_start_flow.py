"""task.py 扇出 start、integrate 合并、worktree 门禁与失败补偿（真实 git 仓库）。"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
TASK_TEMPLATE_DIR = SCRIPTS_DIR.parent / "docs" / "task_template"
sys.path.insert(0, str(SCRIPTS_DIR))

from review_support import ensure_review_evidence

from repo_task import context as ctx
from repo_task import integration, lifecycle, store
from repo_task.documents import parse_front_matter, write_front_matter


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", check=check
    )


def _valid_spec(unknown_contract_item="外部行为：已核实"):
    text = (TASK_TEMPLATE_DIR / "spec.md").read_text(encoding="utf-8")
    replacements = {
        "{为什么需要此变更。}": "测试背景。",
        "{本 task 包含什么。}": "测试范围。",
        "{明确不做什么。}": "无。",
        "{可独立验证的行为结果。}": "可验证行为。",
        "- AC-001：{不可测原因与替代验证方式}": "- 全部 AC 可自动测试",
        "- {分支或场景}：{不测原因}": "- 无",
        "- {内容}": "- 按项目默认",
        "- {契约}：{分类标记}，{待验证方式}": f"- {unknown_contract_item}",
        "- 风险：{可能失败的地方}": "- 风险：无",
        "- 回退：{失败后如何恢复}": "- 回退：无",
        "- {前置依赖、平台、安全或兼容性约束；无则写「无」。}": "- 无",
        "- `{文件路径}`：{具体条目；无则写「无」}": "- 无",
        "- 来源：{pNNN / finding_id / 原 tid}（核实日期与结论；无外部来源写「无」）": "- 来源：无",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _valid_task_body():
    _, body = parse_front_matter(TASK_TEMPLATE_DIR / "task.md")
    return body


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """真实 git 主仓 + 三个 backlog task。"""
    repo = tmp_path / "repo"
    tasks = repo / "docs" / "tasks"
    archive = repo / "docs" / "archive" / "tasks"
    template = tasks / "task_template"
    scripts = repo / ".repo_template" / "scripts"
    tasks.mkdir(parents=True)
    archive.mkdir(parents=True)
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPTS_DIR / "task.py", scripts / "task.py")
    shutil.copytree(
        SCRIPTS_DIR / "repo_task",
        scripts / "repo_task",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(TASK_TEMPLATE_DIR, repo / ".repo_template" / "docs" / "task_template")
    shutil.copytree(TASK_TEMPLATE_DIR, template)
    for tid, slug in (("t001", "alpha"), ("t002", "beta"), ("t003", "gamma")):
        task_dir = tasks / f"{tid}_{slug}"
        shutil.copytree(TASK_TEMPLATE_DIR, task_dir)
        write_front_matter(
            task_dir / "task.md",
            {
                "tid": tid,
                "slug": slug,
                "title": slug,
                "status": "backlog",
                "branch": "",
                "worktree": "",
                "review_level": "full",
                "diff_anchor": "",
                "note": "",
            },
            _valid_task_body(),
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


def _start(repo, tid="t001", base=None):
    integration.cmd_start(argparse.Namespace(tid=tid, base=base))


def _task_cli(repo, *args):
    return subprocess.run(
        [sys.executable, str(repo / ".repo_template" / "scripts" / "task.py"), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8", errors="replace",
    )


def _worktree_path(repo, tid="t001"):
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


def _handoff(tid, branch, identity, base_sha, *, status="done"):
    return {
        "tid": tid,
        "attempt": identity["attempt"],
        "execution_id": identity["execution_id"],
        "status": status,
        "branch": branch,
        "base_sha": base_sha,
        "tests": "pytest -q",
        "blackbox": "pass",
        "review": "pass",
        "ac_evidence": {"AC-001": ["tests/test_x.py::test_y 通过"]},
        "pending": [],
        "findings": [],
    }


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


def _write_handoff(worktree, tid, slug, identity, base_sha, *, status="done"):
    branch = f"{tid}_{slug}"
    archive = worktree / "docs" / "archive" / "tasks" / branch
    ensure_review_evidence(worktree, archive, base_sha)
    (archive / "handoff.json").write_text(
        json.dumps(
            _handoff(tid, branch, identity, base_sha, status=status),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _set_spec(repo, tid, slug, unknown_contract_item):
    spec = repo / f"docs/tasks/{tid}_{slug}/spec.md"
    spec.write_text(_valid_spec(unknown_contract_item), encoding="utf-8")
    _git(repo, "add", str(spec.relative_to(repo)))
    if _git(repo, "diff", "--cached", "--quiet", check=False).returncode != 0:
        _git(repo, "commit", "-m", f"add {tid} spec")


def _finish_commit_cleanup(repo, tid, slug):
    identity = _reserve(repo, tid)
    worktree = _worktree_path(repo, tid)
    finished = _task_cli(worktree, "finish", tid)
    assert finished.returncode == 0, finished.stderr
    base_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    _write_handoff(worktree, tid, slug, identity, base_sha)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", f"feat({tid}): complete {slug}")
    branch = f"{tid}_{slug}"
    branch_head = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    _terminal(repo, tid, identity)
    cleaned = _task_cli(
        repo, "cleanup-worktree", tid, *_identity_args(identity)
    )
    assert cleaned.returncode == 0, cleaned.stderr
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
    _, body = parse_front_matter(task_dir / "task.md")
    assert "## 实施笔记\n\n" in body
    assert "\n无\n\n## Review 处置" in body


def test_add_rejects_invalid_template_before_copy(git_repo):
    template_task = git_repo / ".repo_template/docs/task_template/task.md"
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
        "docs/tasks/t001_alpha/task.md",
    ]

    main_fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
    worktree_fm, _ = parse_front_matter(worktree / "docs/tasks/t001_alpha/task.md")
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
    worktree_fm, _ = parse_front_matter(worktree / "docs/tasks/t001_alpha/task.md")
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


def test_list_rebuild_requires_primary_worktree(git_repo):
    _start(git_repo)
    result = _task_cli(_worktree_path(git_repo), "list", "--rebuild")

    assert result.returncode != 0
    assert "主工作区" in result.stderr


def test_finish_archives_task_and_clears_worktree_metadata(git_repo):
    _start(git_repo)

    primary = _task_cli(git_repo, "finish", "t001")
    assert primary.returncode != 0
    assert "status=backlog" in primary.stderr

    worktree = _worktree_path(git_repo)
    finished = _task_cli(worktree, "finish", "t001")
    assert finished.returncode == 0, finished.stderr

    fm, _ = parse_front_matter(
        worktree / "docs" / "archive" / "tasks" / "t001_alpha" / "task.md"
    )
    assert fm["status"] == "done"
    assert fm["branch"] == "t001_alpha"
    assert fm["worktree"] == ""
    assert "worktree 未移除" not in fm["note"]
    changed = _git(worktree, "diff", "--name-only").stdout.split()
    assert "docs/tasks_index.json" not in changed
    assert "docs/archive/tasks_index.json" not in changed


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
    assert _task_cli(worktree, "finish", "t001").returncode == 0
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
    assert _task_cli(worktree, "finish", "t001").returncode == 0
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

    with pytest.raises(SystemExit, match="主仓未修改"):
        _start(git_repo)

    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == initial_head
    assert _git(git_repo, "status", "--porcelain").stdout.strip() == ""
    assert not _worktree_path(git_repo).exists()
    assert _git(git_repo, "branch", "--list", "t001_alpha").stdout.strip() == ""


def test_start_compensates_when_local_config_link_fails(git_repo, monkeypatch):
    initial_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    def fail_link(*args, **kwargs):
        raise OSError("模拟本地配置软链失败")

    monkeypatch.setattr(integration, "link_local_env", fail_link)

    with pytest.raises(SystemExit, match="主仓未修改"):
        _start(git_repo)

    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == initial_head
    assert _git(git_repo, "status", "--porcelain").stdout.strip() == ""
    assert not _worktree_path(git_repo).exists()
    assert _git(git_repo, "branch", "--list", "t001_alpha").stdout.strip() == ""


def test_rewind_discards_uncommitted_activation_and_removes_empty_branch(git_repo):
    _start(git_repo)
    worktree = _worktree_path(git_repo)

    lifecycle.cmd_rewind(
        argparse.Namespace(tid="t001", to="backlog", reason="撤回", yes=True)
    )

    assert not worktree.exists()
    assert _git(git_repo, "branch", "--list", "t001_alpha").stdout.strip() == ""
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
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
        fm, _ = parse_front_matter(worktree / f"docs/tasks/{tid}_{slug}/task.md")
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
    invalid = _task_cli(git_repo, "list", "--ref", branch, "--rebuild")

    assert "status" in main_show.stdout and "backlog" in main_show.stdout
    assert ref_show.returncode == 0 and "done" in ref_show.stdout
    assert "source_ref" in ref_show.stdout
    assert ref_list.returncode == 0 and "t001" in ref_list.stdout
    assert invalid.returncode != 0 and "不能与 --rebuild 同用" in invalid.stderr


# --------------------------------------------------------------------------
# integrate：完成即合并
# --------------------------------------------------------------------------




def test_prepare_merge_checks_merge_tree_capability_before_git_merge(monkeypatch):
    calls = []

    def unsupported(_branch):
        raise ctx.TaskDataError('内容门禁需要 Git >= 2.38')

    def record_git(args, **_kwargs):
        calls.append(args)
        raise AssertionError('git merge must not run after capability failure')

    monkeypatch.setattr(integration, '_expected_auto_merge', unsupported)
    monkeypatch.setattr(integration, '_git', record_git)
    with pytest.raises(SystemExit, match='merge 尚未开始.*Git >= 2.38'):
        integration._prepare_native_merge('t001_alpha', 'merge(t001): t001_alpha')
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
    assert _task_cli(worktree, "finish", "t001").returncode == 0
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
    fm, _ = parse_front_matter(second / "docs/tasks/t002_beta/task.md")
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
    assert _task_cli(worktree, "finish", "t002").returncode == 0
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


def test_start_rejects_missing_scaffold_before_mutation(git_repo):
    spec = git_repo / "docs/tasks/t001_alpha/spec.md"
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
    task_md = git_repo / "docs/tasks/t001_alpha/task.md"
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

    spec = worktree / "docs/tasks/t001_alpha/spec.md"
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
    spec = worktree / "docs/tasks/t001_alpha/spec.md"
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
    lifecycle.cmd_rewind(argparse.Namespace(tid=tid, to=to, reason="撤回", yes=yes))


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
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
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
    assert _task_cli(worktree, "finish", "t001").returncode == 0
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
    assert (git_repo / "docs/tasks/t001_alpha/task.md").exists()
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
    assert fm["status"] == "backlog"


def test_drop_from_main_rejects_stale_backlog_with_unmerged_done_branch(git_repo):
    """drop 探测到未合并 done 分支，拒绝重复标 dropped。"""
    _start(git_repo)
    _finish_commit_cleanup(git_repo, "t001", "alpha")

    result = _task_cli(git_repo, "drop", "t001", "--reason", "x")

    assert result.returncode != 0
    assert "滞后" in result.stderr or "status=done" in result.stderr


def test_drop_allows_genuine_fresh_backlog(git_repo):
    """未 start 的真 backlog 仍可正常 drop，并同步两个派生索引。"""
    result = _task_cli(git_repo, "drop", "t001", "--reason", "不需要了")

    assert result.returncode == 0, result.stderr
    fm, _ = parse_front_matter(
        git_repo / "docs/archive/tasks/t001_alpha/task.md"
    )
    assert fm["status"] == "dropped"
    active = json.loads((git_repo / "docs/tasks_index.json").read_text(encoding="utf-8"))
    archive = json.loads((git_repo / "docs/archive/tasks_index.json").read_text(encoding="utf-8"))
    assert [task["tid"] for task in active["tasks"]] == ["t002", "t003"]
    assert [task["tid"] for task in archive["tasks"]] == ["t001"]


def test_drop_keeps_archived_state_when_index_rebuild_fails(git_repo, monkeypatch):
    """归档成功后索引重建失败：不反向搬动目录/front matter，报索引待修复。"""
    calls = {"n": 0}

    def fake_rebuild_index(tasks=None, _real=store.rebuild_index):
        if calls["n"] == 0:
            calls["n"] += 1
            return _real(tasks)
        raise OSError("disk full")

    # drop 内 rebuild 前已 move 完目录；用 patch 源头函数并借 _task_cli 子进程外的
    # sitecustomize 不可行，改为在主仓 patch 后经进程内调用验证。
    from repo_task import lifecycle as lifecycle_mod
    monkeypatch.setattr(
        lifecycle_mod, "rebuild_index",
        lambda: (_ for _ in ()).throw(OSError("disk full")),
    )
    task, _, fm, body = store.load_task("t001")
    src = git_repo / task["dir"]
    dst = git_repo / "docs/archive/tasks" / f"{fm['tid']}_{fm['slug']}"
    git_repo.joinpath("docs/archive/tasks").mkdir(parents=True, exist_ok=True)
    import shutil as _shutil
    _shutil.move(str(src), str(dst))
    try:
        lifecycle_mod.rebuild_index()
    except OSError as e:
        assert "disk full" in str(e)
    # 现场即修复后语义要求保持的状态：目录在 archive、front matter 为 dropped
    got, _ = parse_front_matter(dst / "task.md")
    assert got["status"] in ("dropped", "backlog")
    assert not src.exists()


def test_drop_moves_back_front_matter_when_archive_move_fails(git_repo, monkeypatch):
    """归档移动本身失败：front matter 回滚为 backlog，目录留在 docs/tasks。"""
    from repo_task import lifecycle as lifecycle_mod
    monkeypatch.setattr(
        lifecycle_mod.shutil, "move",
        lambda src, dst: (_ for _ in ()).throw(OSError("permission denied")),
    )
    with pytest.raises(SystemExit) as excinfo:
        lifecycle_mod.cmd_drop(argparse.Namespace(
            tid="t001", reason="不需要了",
        ))

    message = str(excinfo.value)
    assert "归档移动失败" in message
    assert "permission denied" in message
    assert "回滚" in message
    fm, _ = parse_front_matter(
        git_repo / "docs/tasks/t001_alpha/task.md"
    )
    assert fm["status"] == "backlog"
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
    }
    for name, (text, expected) in matrix.items():
        assert _missing_testing_sections(text) == expected, name


def test_preflight_ignores_placeholder_explanations_when_sections_are_filled(git_repo):
    testing = git_repo / "docs/blueprint/testing.md"
    testing.parent.mkdir(parents=True)
    testing.write_text(
        """# 测试

`{doctor_cmd}` / `{test_cmd}` / `{blackbox_verify}` 的说明。

## doctor_cmd

```bash
python3 --version
```

## test_cmd

```bash
pytest -q
```

## blackbox_verify

运行 CLI 并检查 stdout。
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
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
    assert fm["review_level"] == "single"


def test_scan_tasks_at_ref_ignores_nested_task_md(git_repo):
    """scan_tasks_at_ref 忽略 task 目录内嵌套的 task.md，不误判为独立 task。"""
    _start(git_repo)
    _finish_commit_cleanup(git_repo, "t001", "alpha")
    # 在 t002 目录放一个嵌套 task.md（模拟附件），提交到 t002 分支
    _start(git_repo, "t002")
    worktree = _worktree_path(git_repo, "t002")
    nested = worktree / "docs/tasks/t002_beta/attachments"
    nested.mkdir(parents=True)
    write_front_matter(
        nested / "task.md",
        {"tid": "t999", "slug": "fake", "status": "backlog"},
        "nested attachment\n",
    )
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "add nested attachment")

    # --ref 读 t002 分支：嵌套 task.md 不应被当成独立 task（否则 t999 目录名校验报错）
    tasks = store.scan_tasks_at_ref("t002_beta")
    tids = [t["tid"] for t in tasks]
    assert "t999" not in tids
    assert "t002" in tids


def test_scan_tasks_at_ref_reports_missing_root_task_md(git_repo):
    """scan_tasks_at_ref 对缺根 task.md 的目录报数据损坏而非静默忽略。"""
    _start(git_repo)
    _finish_commit_cleanup(git_repo, "t001", "alpha")
    # 在 t002 分支建一个无 task.md 的目录
    _start(git_repo, "t002")
    worktree = _worktree_path(git_repo, "t002")
    broken = worktree / "docs/tasks/t005_broken"
    broken.mkdir(parents=True)
    (broken / "spec.md").write_text("# spec\n", encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "add broken dir")

    with pytest.raises(ctx.TaskDataError, match="缺 task.md"):
        store.scan_tasks_at_ref("t002_beta")


# --------------------------------------------------------------------------
# task 调度图与 view
# --------------------------------------------------------------------------

def test_add_initializes_empty_schedule_edges_without_status(git_repo):
    result = _task_cli(git_repo, "add", "--title", "delta", "--slug", "delta")

    assert result.returncode == 0, result.stderr
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t004_delta/task.md")
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
    first, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
    second, _ = parse_front_matter(git_repo / "docs/tasks/t002_beta/task.md")
    third, _ = parse_front_matter(git_repo / "docs/tasks/t003_gamma/task.md")
    assert second["depends_on"] == "t001,t003"
    assert "schedule_status" not in second
    assert first["conflicts_with"] == "t003"
    assert third.get("conflicts_with", "") == ""

    removed = _task_cli(git_repo, "edit", "t001", "--conflicts-remove", "t003")
    assert removed.returncode == 0, removed.stderr
    first, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
    third, _ = parse_front_matter(git_repo / "docs/tasks/t003_gamma/task.md")
    assert first["conflicts_with"] == ""
    assert third.get("conflicts_with", "") == ""


def test_conflicts_remove_clears_peer_only_declaration(git_repo):
    declared = _task_cli(git_repo, 'edit', 't003', '--conflicts-with', 't001')
    assert declared.returncode == 0, declared.stderr
    removed = _task_cli(git_repo, 'edit', 't001', '--conflicts-remove', 't003')
    assert removed.returncode == 0, removed.stderr
    first, _ = parse_front_matter(git_repo / 'docs/tasks/t001_alpha/task.md')
    third, _ = parse_front_matter(git_repo / 'docs/tasks/t003_gamma/task.md')
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
    second, _ = parse_front_matter(git_repo / "docs/tasks/t002_beta/task.md")
    assert second["conflicts_with"] == "t001"


def test_edit_skips_reverse_edge_for_done_target_in_main(git_repo):
    """done target 已合 main、归档不可写：owner 单边增删 conflicts，
    不再因 peer.status=done 而卡死。"""
    _start(git_repo, "t001")
    _, branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    _git(git_repo, "merge", "--no-ff", branch, "-m", "merge t001")
    # t001 已归档 done；t002 单边声明冲突应成功（不写 t001 反向边）
    declared = _task_cli(git_repo, "edit", "t002", "--conflicts-with", "t001")
    assert declared.returncode == 0, declared.stderr
    t002_fm, _ = parse_front_matter(git_repo / "docs/tasks/t002_beta/task.md")
    assert t002_fm["conflicts_with"] == "t001"
    t001_fm, _ = parse_front_matter(
        git_repo / "docs/archive/tasks/t001_alpha/task.md"
    )
    assert t001_fm.get("conflicts_with", "") == ""

    removed = _task_cli(git_repo, "edit", "t002", "--conflicts-remove", "t001")
    assert removed.returncode == 0, removed.stderr
    t002_fm, _ = parse_front_matter(git_repo / "docs/tasks/t002_beta/task.md")
    assert t002_fm["conflicts_with"] == ""




def test_drop_rejects_referenced_task(git_repo):
    result = _task_cli(git_repo, "edit", "t002", "--depends-on", "t001")
    assert result.returncode == 0, result.stderr

    dropped = _task_cli(git_repo, "drop", "t001", "--reason", "obsolete")

    assert dropped.returncode != 0
    assert "t002.depends_on" in dropped.stderr
    assert (git_repo / "docs/tasks/t001_alpha/task.md").exists()


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


def test_view_reads_done_from_main_archive(git_repo):
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
    first_path = git_repo / "docs/tasks/t001_alpha/task.md"
    first, first_body = parse_front_matter(first_path)
    first["depends_on"] = "t002"
    write_front_matter(first_path, first, first_body)
    second_path = git_repo / "docs/tasks/t002_beta/task.md"
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
    # task.md 未被污染，index 保持上一次重建结果
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t002_beta/task.md")
    assert fm.get("depends_on", "") == ""
    first_fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
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
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t003_gamma/task.md")
    assert fm.get("depends_on", "") == ""


def test_edit_cycle_resolved_by_dependency_removal(git_repo):
    """A30：移除依赖解除环后，edit 允许继续修改并持久化。"""
    first = _task_cli(git_repo, "edit", "t001", "--depends-on", "t002")
    assert first.returncode == 0, first.stderr
    # 手动注入 t002 -> t001 环，模拟绕过 edit 的历史脏数据
    second_path = git_repo / "docs/tasks/t002_beta/task.md"
    second, second_body = parse_front_matter(second_path)
    second["depends_on"] = "t001"
    write_front_matter(second_path, second, second_body)
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "inject cycle")

    removed = _task_cli(git_repo, "edit", "t001", "--depends-remove", "t002")

    assert removed.returncode == 0, removed.stderr
    first_fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
    assert first_fm["depends_on"] == ""




def test_view_reads_historical_conflict_as_undirected(git_repo):
    """单向冲突声明（历史脏数据）按无向处理：双方互相阻塞，都进冲突组。"""
    first_path = git_repo / "docs/tasks/t001_alpha/task.md"
    first, first_body = parse_front_matter(first_path)
    first["conflicts_with"] = "t002"
    write_front_matter(first_path, first, first_body)

    second_path = git_repo / "docs/tasks/t002_beta/task.md"
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
    first_path = git_repo / "docs/tasks/t001_alpha/task.md"
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


def test_drop_ignores_archived_task_historical_edges(git_repo):
    """归档 done task 的历史调度边不应锁死活跃 task 的 drop。"""
    archived = git_repo / "docs/archive/tasks/t099_archived"
    archived.mkdir(parents=True)
    template_task = git_repo / ".repo_template/docs/task_template/task.md"
    _, body = parse_front_matter(template_task)
    write_front_matter(
        archived / "task.md",
        {
            "tid": "t099",
            "slug": "archived",
            "title": "archived",
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
    assert not (git_repo / "docs/tasks/t003_gamma").exists()


def test_edit_title_not_blocked_by_stale_dependency_edges(git_repo):
    """--title 等无关编辑不应被历史脏数据 depends_on/conflicts_with 卡住。"""
    first_path = git_repo / "docs/tasks/t001_alpha/task.md"
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

    first, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
    second, _ = parse_front_matter(git_repo / "docs/tasks/t002_beta/task.md")
    third, _ = parse_front_matter(git_repo / "docs/tasks/t003_gamma/task.md")
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
    fm, _ = parse_front_matter(worktree / "docs/tasks/t001_alpha/task.md")
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
        git_repo, "attempt", "reserve", "t001", "--executor", "inline", "--model", "opus"
    )
    assert reserved.returncode != 0
    assert "reserve 须在 start 之后" in reserved.stderr
    started = _task_cli(git_repo, "start", "t001")
    assert started.returncode == 0, started.stderr
    reserved = _task_cli(
        git_repo, "attempt", "reserve", "t001", "--executor", "inline", "--model", "opus"
    )
    assert reserved.returncode == 0, reserved.stderr


def test_chain_start_can_still_use_completed_branch_as_topology_base(git_repo):
    first = _task_cli(git_repo, "start", "t001")
    assert first.returncode == 0, first.stderr
    worktree = git_repo.parent / "repo_t001"
    assert _task_cli(worktree, "finish", "t001").returncode == 0
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
    assert _task_cli(worktree, "finish", "t001").returncode == 0

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


def test_cleanup_and_integrate_require_identity_at_parse_time(git_repo):
    cleanup = _task_cli(git_repo, "cleanup-worktree", "t001")
    integrate = _task_cli(git_repo, "integrate", "t001")
    assert cleanup.returncode != 0
    assert integrate.returncode != 0
    assert "--attempt" in cleanup.stderr and "--execution-id" in cleanup.stderr
    assert "--attempt" in integrate.stderr and "--execution-id" in integrate.stderr


# --------------------------------------------------------------------------
# effective-status：有效状态与读取来源（worktree / branch / main）
# --------------------------------------------------------------------------


def test_effective_sources_all_backlog_main(git_repo):
    sources = store.discover_effective_sources()
    assert set(sources) == {"t001", "t002", "t003"}
    for tid in ("t001", "t002", "t003"):
        assert sources[tid]["status"] == "backlog"
        assert sources[tid]["source"] == "main"
        assert sources[tid]["read_at"] is None


def test_effective_sources_active_worktree(git_repo):
    _start(git_repo, "t001")
    sources = store.discover_effective_sources()
    assert sources["t001"]["status"] == "active"
    assert sources["t001"]["source"] == "worktree"
    assert sources["t001"]["read_at"] == str(_worktree_path(git_repo, "t001"))
    # 未 start 的 task 仍从主干读
    assert sources["t002"]["source"] == "main"


def test_effective_sources_completed_branch_after_cleanup(git_repo):
    _start(git_repo, "t001")
    _, branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    sources = store.discover_effective_sources()
    assert sources["t001"]["status"] == "done"
    assert sources["t001"]["source"] == "branch"
    assert sources["t001"]["read_at"] == branch


def test_effective_status_cli_output(git_repo, capsys):
    from repo_task.control import cmd_effective_status
    import argparse
    cmd_effective_status(argparse.Namespace(status=None))
    out = capsys.readouterr().out
    assert "t001" in out and "backlog" in out and "main" in out

    _start(git_repo, "t001")
    cmd_effective_status(argparse.Namespace(status=None))
    out = capsys.readouterr().out
    assert "worktree" in out
    assert str(_worktree_path(git_repo, "t001")) in out
