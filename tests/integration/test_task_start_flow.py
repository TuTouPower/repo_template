"""task.py 链式 start、worktree 门禁与失败补偿（真实 git 仓库）。"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
TASK_TEMPLATE_DIR = SCRIPTS_DIR.parent / "docs" / "tasks" / "task_template"
sys.path.insert(0, str(SCRIPTS_DIR))

import task as task_mod
from task import parse_front_matter, write_front_matter


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )


def _valid_spec(unknown_contract_item="外部行为：已核实"):
    text = (TASK_TEMPLATE_DIR / "spec.md").read_text(encoding="utf-8")
    replacements = {
        "{为什么需要此变更。}": "测试背景。",
        "{本 task 包含什么。}": "测试范围。",
        "{明确不做什么。}": "无。",
        "{可独立验证的行为结果。}": "可验证行为。",
        "- {AC 编号}：{不可测原因与替代验证方式}": "- 全部 AC 可自动测试",
        "- {分支或场景}：{不测原因}": "- 无",
        "- {内容}": "- 按项目默认",
        "- {契约}：{分类标记}，{待验证方式}": f"- {unknown_contract_item}",
        "- 风险：{可能失败的地方}": "- 风险：无",
        "- 回退：{失败后如何恢复}": "- 回退：无",
        "- {前置依赖、平台、安全或兼容性约束；无则写「无」。}": "- 无",
        "- `{文件路径}`：{具体条目；无则写「无」}": "- 无",
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
    scripts = repo / "scripts"
    tasks.mkdir(parents=True)
    archive.mkdir(parents=True)
    scripts.mkdir()
    shutil.copy2(SCRIPTS_DIR / "task.py", scripts / "task.py")
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
    monkeypatch.setattr(task_mod, "TASKS_DIR", tasks)
    monkeypatch.setattr(task_mod, "ARCHIVE_TASKS_DIR", archive)
    monkeypatch.setattr(task_mod, "TEMPLATE_DIR", template)
    monkeypatch.setattr(task_mod, "ACTIVE_PATH", repo / "docs" / "tasks_index.json")
    monkeypatch.setattr(
        task_mod, "ARCHIVE_PATH", repo / "docs" / "archive" / "tasks_index.json"
    )
    monkeypatch.setattr(
        task_mod, "AUDIT_PATH", repo / "docs" / "archive" / "tasks_audit.log"
    )
    monkeypatch.setattr(task_mod, "REPO_ROOT", repo)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _start(repo, tid="t001", base=None):
    task_mod.cmd_start(argparse.Namespace(tid=tid, base=base))


def _task_cli(repo, *args):
    return subprocess.run(
        [sys.executable, str(repo / "scripts" / "task.py"), *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def _worktree_path(repo, tid="t001"):
    return repo.parent / f"{repo.name}_{tid}"


def _set_spec(repo, tid, slug, unknown_contract_item):
    spec = repo / f"docs/tasks/{tid}_{slug}/spec.md"
    spec.write_text(_valid_spec(unknown_contract_item), encoding="utf-8")
    _git(repo, "add", str(spec.relative_to(repo)))
    if _git(repo, "diff", "--cached", "--quiet", check=False).returncode != 0:
        _git(repo, "commit", "-m", f"add {tid} spec")


def _finish_commit_cleanup(repo, tid, slug):
    worktree = _worktree_path(repo, tid)
    finished = _task_cli(worktree, "finish", tid)
    assert finished.returncode == 0, finished.stderr
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", f"feat({tid}): complete {slug}")
    branch = f"{tid}_{slug}"
    branch_head = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    cleaned = _task_cli(repo, "cleanup-worktree", tid)
    assert cleaned.returncode == 0, cleaned.stderr
    assert not worktree.exists()
    assert _git(repo, "branch", "--list", branch).stdout.strip() == branch
    return branch, branch_head


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
    template_task = git_repo / "docs/tasks/task_template/task.md"
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


def test_start_rejects_dirty_primary_worktree(git_repo):
    (git_repo / "unrelated.txt").write_text("dirty", encoding="utf-8")

    with pytest.raises(SystemExit, match="未提交改动"):
        _start(git_repo)

    assert not _worktree_path(git_repo).exists()


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
    # 未 finish 的 active worktree：终态校验先于 dirty 校验拒绝
    not_done = _task_cli(git_repo, "cleanup-worktree", "t001")
    assert not_done.returncode != 0
    assert "须为 done/dropped" in not_done.stderr

    # 已 finish 且提交（分支 ref=done），但 worktree 新增脏改动：拒绝
    worktree = _worktree_path(git_repo)
    assert _task_cli(worktree, "finish", "t001").returncode == 0
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "feat(t001): complete alpha")
    (worktree / "dirty.txt").write_text("x", encoding="utf-8")
    dirty = _task_cli(git_repo, "cleanup-worktree", "t001")
    assert dirty.returncode != 0
    assert "未提交改动" in dirty.stderr

    # 清掉脏改动后 cleanup 成功且幂等
    (worktree / "dirty.txt").unlink()
    branch = "t001_alpha"
    cleaned = _task_cli(git_repo, "cleanup-worktree", "t001")
    assert cleaned.returncode == 0, cleaned.stderr
    assert not worktree.exists()
    repeated = _task_cli(git_repo, "cleanup-worktree", "t001")
    assert repeated.returncode == 0
    assert "幂等" in repeated.stdout
    assert _git(git_repo, "branch", "--list", branch).stdout.strip() == branch


def test_cleanup_worktree_rejects_wrong_registered_branch(git_repo):
    wrong = _worktree_path(git_repo)
    _git(git_repo, "worktree", "add", "-b", "t999_other", str(wrong))

    result = _task_cli(git_repo, "cleanup-worktree", "t001")

    assert result.returncode != 0
    assert "不属于 t001" in result.stderr


def test_cleanup_worktree_rejects_unregistered_directory(git_repo):
    unknown = _worktree_path(git_repo)
    unknown.mkdir()
    marker = unknown / "keep.txt"
    marker.write_text("user data\n", encoding="utf-8")

    result = _task_cli(git_repo, "cleanup-worktree", "t001")

    assert result.returncode != 0
    assert "未登记为 git worktree" in result.stderr
    assert marker.read_text(encoding="utf-8") == "user data\n"


def test_start_compensates_when_worktree_creation_fails(git_repo, monkeypatch):
    initial_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    def fail_create(*args, **kwargs):
        raise task_mod.TaskDataError("模拟 worktree 创建失败")

    monkeypatch.setattr(task_mod, "create_worktree", fail_create)

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

    monkeypatch.setattr(task_mod, "link_local_env", fail_link)

    with pytest.raises(SystemExit, match="主仓未修改"):
        _start(git_repo)

    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == initial_head
    assert _git(git_repo, "status", "--porcelain").stdout.strip() == ""
    assert not _worktree_path(git_repo).exists()
    assert _git(git_repo, "branch", "--list", "t001_alpha").stdout.strip() == ""


def test_rewind_discards_uncommitted_activation_and_removes_empty_branch(git_repo):
    _start(git_repo)
    worktree = _worktree_path(git_repo)

    task_mod.cmd_rewind(
        argparse.Namespace(tid="t001", to="backlog", reason="撤回", yes=True)
    )

    assert not worktree.exists()
    assert _git(git_repo, "branch", "--list", "t001_alpha").stdout.strip() == ""
    fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
    assert fm["status"] == "backlog"
    assert fm["worktree"] == ""
    assert "rewound: effective=active -> backlog" in fm["note"]
    assert "main 记录为 backlog" in fm["note"]


def test_block_resume_preserves_worktree_notes_and_chain_can_continue(git_repo):
    _start(git_repo)
    worktree = _worktree_path(git_repo)
    task_path = worktree / "docs/tasks/t001_alpha/task.md"
    fm, body = parse_front_matter(task_path)
    write_front_matter(task_path, fm, body + "implementation note\n")

    blocked = _task_cli(worktree, "block", "t001", "--reason", "review")
    resumed = _task_cli(worktree, "resume", "t001")

    assert blocked.returncode == 0, blocked.stderr
    assert resumed.returncode == 0, resumed.stderr
    resumed_fm, resumed_body = parse_front_matter(task_path)
    assert resumed_fm["status"] == "active"
    assert "implementation note" in resumed_body

    branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    _start(git_repo, "t002", base=branch)
    assert _worktree_path(git_repo, "t002").is_dir()


def test_blocked_task_can_be_dropped_committed_and_cleaned(git_repo):
    _start(git_repo)
    worktree = _worktree_path(git_repo)
    blocked = _task_cli(worktree, "block", "t001", "--reason", "infra")
    dropped = _task_cli(worktree, "drop", "t001", "--reason", "用户移出批次")

    assert blocked.returncode == 0, blocked.stderr
    assert dropped.returncode == 0, dropped.stderr
    archived = worktree / "docs/archive/tasks/t001_alpha/task.md"
    fm, _ = parse_front_matter(archived)
    assert fm["status"] == "dropped"
    assert fm["worktree"] == ""

    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "chore(t001): drop task")
    cleaned = _task_cli(git_repo, "cleanup-worktree", "t001")
    assert cleaned.returncode == 0, cleaned.stderr
    assert not worktree.exists()
    assert _git(git_repo, "branch", "--list", "t001_alpha").stdout.strip() == "t001_alpha"


def test_chained_start_uses_previous_completed_branch(git_repo):
    main_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _start(git_repo, "t001")
    first_branch, first_head = _finish_commit_cleanup(git_repo, "t001", "alpha")

    _start(git_repo, "t002", base=first_branch)

    second_worktree = _worktree_path(git_repo, "t002")
    second_fm, _ = parse_front_matter(second_worktree / "docs/tasks/t002_beta/task.md")
    first_fm, _ = parse_front_matter(
        second_worktree / "docs/archive/tasks/t001_alpha/task.md"
    )
    assert second_fm["status"] == "active"
    assert second_fm["diff_anchor"] == first_head
    assert first_fm["status"] == "done"
    assert _git(second_worktree, "rev-parse", "HEAD").stdout.strip() == first_head
    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == main_head


def test_chained_start_inherits_previous_spec_revision(git_repo):
    _start(git_repo, "t001")
    first_worktree = _worktree_path(git_repo, "t001")
    spec = first_worktree / "docs/tasks/t002_beta/spec.md"
    revised = spec.read_text(encoding="utf-8").replace(
        "测试背景。",
        "previous branch revision",
        1,
    )
    spec.write_text(revised, encoding="utf-8")
    first_branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")

    _start(git_repo, "t002", base=first_branch)

    inherited = _worktree_path(git_repo, "t002") / "docs/tasks/t002_beta/spec.md"
    assert inherited.read_text(encoding="utf-8") == revised


@pytest.mark.parametrize("base", ["missing", "HEAD", "origin/main"])
def test_start_rejects_non_local_branch_base(git_repo, base):
    with pytest.raises(task_mod.TaskDataError, match="本地分支"):
        _start(git_repo, "t002", base=base)


def test_start_rejects_tag_as_base(git_repo):
    _git(git_repo, "tag", "snapshot")

    with pytest.raises(task_mod.TaskDataError, match="本地分支"):
        _start(git_repo, "t002", base="snapshot")


def test_start_rejects_ordinary_feature_base(git_repo):
    _git(git_repo, "branch", "feature")

    with pytest.raises(task_mod.TaskDataError, match="task 分支"):
        _start(git_repo, "t002", base="feature")


def test_start_rejects_unfinished_task_base(git_repo):
    _start(git_repo, "t001")

    with pytest.raises(task_mod.TaskDataError, match="须先完成"):
        _start(git_repo, "t002", base="t001_alpha")


def test_start_rejects_completed_base_with_registered_worktree(git_repo):
    _start(git_repo, "t001")
    worktree = _worktree_path(git_repo)
    assert _task_cli(worktree, "finish", "t001").returncode == 0
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "feat(t001): complete alpha")

    with pytest.raises(task_mod.TaskDataError, match="仍登记 worktree"):
        _start(git_repo, "t002", base="t001_alpha")


def test_start_accepts_base_after_main_advanced(git_repo):
    """main 并行推进后，旧链尾分支仍可作为 --base 继续链（取消 main 冻结约束）。"""
    _start(git_repo, "t001")
    branch, base_head = _finish_commit_cleanup(git_repo, "t001", "alpha")

    # main 并行推进：t001_alpha 不再以当前 main 为祖先
    (git_repo / "parallel.txt").write_text("main advanced", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "chore: parallel work on main")
    assert _git(
        git_repo, "merge-base", "--is-ancestor", "main", branch, check=False
    ).returncode != 0

    # 旧链尾仍可作为 --base 继续；start 不再要求 main 是 base 的祖先
    _start(git_repo, "t002", base=branch)
    worktree = _worktree_path(git_repo, "t002")
    assert worktree.exists()
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == base_head



def test_list_and_show_read_completed_state_from_branch(git_repo):
    _start(git_repo, "t001")
    branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")

    main_show = _task_cli(git_repo, "show", "t001")
    ref_show = _task_cli(git_repo, "show", "t001", "--ref", branch)
    ref_list = _task_cli(git_repo, "list", "--ref", branch, "--status", "done")
    invalid = _task_cli(git_repo, "list", "--ref", branch, "--rebuild")

    assert "status" in main_show.stdout and "backlog" in main_show.stdout
    assert ref_show.returncode == 0 and "done" in ref_show.stdout
    assert "source_ref" in ref_show.stdout
    assert ref_list.returncode == 0 and "t001" in ref_list.stdout
    assert invalid.returncode != 0 and "不能与 --rebuild 同用" in invalid.stderr


def test_three_task_chain_merges_only_tail_and_rebuilds_index(git_repo):
    main_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    previous = None
    branches = []
    heads = []
    for tid, slug in (("t001", "alpha"), ("t002", "beta"), ("t003", "gamma")):
        _start(git_repo, tid, base=previous)
        previous, head = _finish_commit_cleanup(git_repo, tid, slug)
        branches.append(previous)
        heads.append(head)

    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == main_head
    assert _git(git_repo, "rev-list", "--count", f"main..{branches[-1]}").stdout.strip() == "3"
    assert _git(
        git_repo, "merge-base", "--is-ancestor", branches[0], branches[1], check=False
    ).returncode == 0
    assert _git(
        git_repo, "merge-base", "--is-ancestor", branches[1], branches[2], check=False
    ).returncode == 0

    _git(git_repo, "merge", "--no-ff", branches[-1], "-m", "merge task batch")
    rebuilt = _task_cli(git_repo, "list", "--rebuild")
    assert rebuilt.returncode == 0, rebuilt.stderr
    _git(git_repo, "add", "docs/tasks_index.json", "docs/archive/tasks_index.json")
    _git(git_repo, "commit", "-m", "chore: rebuild task indexes")

    assert _git(
        git_repo, "merge-base", "--is-ancestor", heads[-1], "main", check=False
    ).returncode == 0
    for tid, slug in (("t001", "alpha"), ("t002", "beta"), ("t003", "gamma")):
        fm, _ = parse_front_matter(
            git_repo / f"docs/archive/tasks/{tid}_{slug}/task.md"
        )
        assert fm["status"] == "done"
        assert fm["worktree"] == ""


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
    branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
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
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "reviewer 判 AC 时只看本区。\n",
            "",
            1,
        ),
        encoding="utf-8",
    )
    _git(git_repo, "add", str(spec.relative_to(git_repo)))
    _git(git_repo, "commit", "-m", "break task scaffold")
    initial_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    with pytest.raises(SystemExit, match="固定声明或引导语"):
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
    assert "仅可执行 Step 1" in default.stdout

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
    task_mod.cmd_rewind(argparse.Namespace(tid=tid, to=to, reason="撤回", yes=yes))


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
    worktree = _worktree_path(git_repo)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "checkpoint")
    assert _git(worktree, "status", "--porcelain").stdout.strip() == ""

    result = _task_cli(git_repo, "cleanup-worktree", "t001")

    assert result.returncode != 0
    assert "须为 done/dropped" in result.stderr
    assert worktree.exists()


def test_cleanup_worktree_rejects_prefix_collision_branch(git_repo):
    """cleanup 拒绝 t001_scratch 这类仅共享前缀的非 task 分支。"""
    wrong = _worktree_path(git_repo)
    _git(git_repo, "worktree", "add", "-b", "t001_scratch", str(wrong))

    result = _task_cli(git_repo, "cleanup-worktree", "t001")

    assert result.returncode != 0
    assert wrong.exists()


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
    """未 start 的真 backlog 仍可正常 drop。"""
    result = _task_cli(git_repo, "drop", "t001", "--reason", "不需要了")

    assert result.returncode == 0, result.stderr
    fm, _ = parse_front_matter(
        git_repo / "docs/archive/tasks/t001_alpha/task.md"
    )
    assert fm["status"] == "dropped"


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


def test_start_rejects_base_branch_slug_mismatch(git_repo):
    """--base 拒绝 slug 与 front matter 不符的伪装 task 分支。"""
    _start(git_repo, "t001")
    first_branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    # 基于 t001 分支建一个 t002_scratch，但其中 t002 是 backlog（未归档）
    _git(git_repo, "branch", "t002_wrongslug", first_branch)

    with pytest.raises(task_mod.TaskDataError, match="slug 不符"):
        _start(git_repo, "t002", base="t002_wrongslug")


def test_scan_tasks_at_ref_ignores_nested_task_md(git_repo):
    """scan_tasks_at_ref 忽略 task 目录内嵌套的 task.md，不误判为独立 task。"""
    _start(git_repo)
    branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    # 在 t002 目录放一个嵌套 task.md（模拟附件），提交到 t002 分支
    _start(git_repo, "t002", base=branch)
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
    tasks = task_mod.scan_tasks_at_ref("t002_beta")
    tids = [t["tid"] for t in tasks]
    assert "t999" not in tids
    assert "t002" in tids


def test_scan_tasks_at_ref_reports_missing_root_task_md(git_repo):
    """scan_tasks_at_ref 对缺根 task.md 的目录报数据损坏而非静默忽略。"""
    _start(git_repo)
    branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    # 在 t002 分支建一个无 task.md 的目录
    _start(git_repo, "t002", base=branch)
    worktree = _worktree_path(git_repo, "t002")
    broken = worktree / "docs/tasks/t005_broken"
    broken.mkdir(parents=True)
    (broken / "spec.md").write_text("# spec\n", encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "add broken dir")

    with pytest.raises(task_mod.TaskDataError, match="缺 task.md"):
        task_mod.scan_tasks_at_ref("t002_beta")
