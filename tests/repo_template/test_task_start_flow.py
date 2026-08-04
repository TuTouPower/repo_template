"""task.py 扇出 start、integrate 合并、worktree 门禁与失败补偿（真实 git 仓库）。

omni_media 本地补丁：`_valid_spec()` 末尾的通用占位符消解，
用于消化本仓库 spec 模板比模板仓多出的占位符（如 `- 来源：{pNNN / finding_id / 原 tid}`）。
模板仓没有这些占位符；此处补丁让本仓库 fixture 与模板仓保持行为一致。
后续若模板仓同步增加这些字段，可移除此补丁。
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "repo_template"
TASK_TEMPLATE_DIR = SCRIPTS_DIR.parent.parent / "docs" / "tasks" / "task_template"
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
    # omni_media 本地补丁：通用消解剩余中文占位符（含 {pNNN / finding_id / 原 tid} 等）。
    text = re.sub(r"\{[^{}\n]*\}", "占位", text)
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
    (scripts / "repo_template").mkdir(parents=True)
    shutil.copy2(SCRIPTS_DIR / "task.py", scripts / "repo_template" / "task.py")
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
        [sys.executable, str(repo / "scripts" / "repo_template" / "task.py"), *args],
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

    _finish_commit_cleanup(git_repo, "t001", "alpha")
    _start(git_repo, "t002")
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


# --------------------------------------------------------------------------
# integrate：完成即合并
# --------------------------------------------------------------------------


def test_integrate_merges_rebuilds_index_and_deletes_branch(git_repo):
    _start(git_repo, "t001")
    branch, head = _finish_commit_cleanup(git_repo, "t001", "alpha")

    result = _task_cli(git_repo, "integrate", "t001")

    assert result.returncode == 0, result.stderr
    assert _git(
        git_repo, "merge-base", "--is-ancestor", head, "main", check=False
    ).returncode == 0
    assert _git(git_repo, "branch", "--list", branch).stdout.strip() == ""
    fm, _ = parse_front_matter(git_repo / "docs/archive/tasks/t001_alpha/task.md")
    assert fm["status"] == "done"
    assert fm["worktree"] == ""
    index = json.loads((git_repo / "docs/archive/tasks_index.json").read_text("utf-8"))
    assert [row["tid"] for row in index["tasks"]] == ["t001"]
    subjects = _git(git_repo, "log", "--format=%s", "-2").stdout.split("\n")
    assert subjects[0] == "chore(task): rebuild task indexes"
    assert subjects[1].startswith("merge(t001)")


def test_integrate_keeps_branch_when_requested(git_repo):
    _start(git_repo, "t001")
    branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")

    result = _task_cli(git_repo, "integrate", "t001", "--keep-branch")

    assert result.returncode == 0, result.stderr
    assert _git(git_repo, "branch", "--list", branch).stdout.strip() == branch


def test_parallel_tasks_integrate_independently_in_completion_order(git_repo):
    _start(git_repo, "t001")
    _start(git_repo, "t002")
    # t002 先完成先合并；t001 后完成，合并时 main 已推进 → 三方 merge
    _, second_head = _finish_commit_cleanup(git_repo, "t002", "beta")
    assert _task_cli(git_repo, "integrate", "t002").returncode == 0

    _, first_head = _finish_commit_cleanup(git_repo, "t001", "alpha")
    result = _task_cli(git_repo, "integrate", "t001")

    assert result.returncode == 0, result.stderr
    for head in (first_head, second_head):
        assert _git(
            git_repo, "merge-base", "--is-ancestor", head, "main", check=False
        ).returncode == 0
    for tid, slug in (("t001", "alpha"), ("t002", "beta")):
        fm, _ = parse_front_matter(
            git_repo / f"docs/archive/tasks/{tid}_{slug}/task.md"
        )
        assert fm["status"] == "done"


def test_integrate_rejects_unfinished_task(git_repo):
    _start(git_repo, "t001")

    result = _task_cli(git_repo, "integrate", "t001")

    assert result.returncode != 0
    assert "须为 done/dropped" in result.stderr


def test_integrate_rejects_registered_worktree(git_repo):
    _start(git_repo, "t001")
    worktree = _worktree_path(git_repo)
    assert _task_cli(worktree, "finish", "t001").returncode == 0
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "feat(t001): complete alpha")

    result = _task_cli(git_repo, "integrate", "t001")

    assert result.returncode != 0
    assert "cleanup-worktree" in result.stderr


def test_integrate_rejects_tracked_dirty_primary_worktree(git_repo):
    tracked = git_repo / "tracked.txt"
    tracked.write_text("base", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "chore: add tracked file")
    _start(git_repo, "t001")
    _finish_commit_cleanup(git_repo, "t001", "alpha")
    tracked.write_text("modified", encoding="utf-8")

    result = _task_cli(git_repo, "integrate", "t001")

    assert result.returncode != 0
    assert "已跟踪文件未提交" in result.stderr


def test_integrate_ignores_untracked_files(git_repo):
    _start(git_repo, "t001")
    _finish_commit_cleanup(git_repo, "t001", "alpha")
    (git_repo / "scratch_note.txt").write_text("untracked", encoding="utf-8")

    result = _task_cli(git_repo, "integrate", "t001")

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
    _finish_commit_cleanup(git_repo, "t001", "alpha")
    shared.write_text("from main\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "chore: edit shared on main")
    assert _task_cli(git_repo, "integrate", "t001").returncode != 0

    outside = tmp_path / "outside"
    outside.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(git_repo / "scripts" / "repo_template" / "task.py"),
            "integrate",
            "t001",
        ],
        cwd=outside,
        capture_output=True,
        text=True,
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
    branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")

    shared.write_text("from main\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "chore: edit shared on main")

    conflicted = _task_cli(git_repo, "integrate", "t001")
    assert conflicted.returncode != 0
    assert "shared.txt" in conflicted.stderr
    assert "--continue" in conflicted.stderr

    premature = _task_cli(git_repo, "integrate", "t001", "--continue")
    assert premature.returncode != 0
    assert "未解决冲突" in premature.stderr

    shared.write_text("resolved\n", encoding="utf-8")
    _git(git_repo, "add", "shared.txt")
    resumed = _task_cli(git_repo, "integrate", "t001", "--continue")

    assert resumed.returncode == 0, resumed.stderr
    assert shared.read_text(encoding="utf-8") == "resolved\n"
    assert _git(git_repo, "branch", "--list", branch).stdout.strip() == ""


def test_integrate_is_idempotent_after_merge(git_repo):
    _start(git_repo, "t001")
    _finish_commit_cleanup(git_repo, "t001", "alpha")
    assert _task_cli(git_repo, "integrate", "t001", "--keep-branch").returncode == 0

    result = _task_cli(git_repo, "integrate", "t001")

    assert result.returncode == 0, result.stderr
    assert "跳过 merge" in result.stdout
    assert _git(git_repo, "branch", "--list", "t001_alpha").stdout.strip() == ""


# --------------------------------------------------------------------------
# integrate --chain：串行链式，只合链尾
# --------------------------------------------------------------------------


def test_chain_start_from_previous_completed_branch(git_repo):
    """串行：t002 从 t001 已完成分支创建，继承其成果。"""
    _start(git_repo, "t001")
    first_branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")

    _start(git_repo, "t002", base=first_branch)

    second = _worktree_path(git_repo, "t002")
    assert second.is_dir()
    fm, _ = parse_front_matter(second / "docs/tasks/t002_beta/task.md")
    assert fm["status"] == "active"
    # t002 分支从 t001 分支创建，包含其 commit
    assert _git(
        second, "merge-base", "--is-ancestor", first_branch, "HEAD", check=False
    ).returncode == 0


def test_chain_integrate_merges_tail_and_deletes_full_chain(git_repo):
    """三 task 成链，只合链尾，祖先自动跟随，删整条链分支。"""
    main_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    branches = []
    heads = []
    previous = None
    for tid, slug in (("t001", "alpha"), ("t002", "beta"), ("t003", "gamma")):
        _start(git_repo, tid, base=previous)
        previous, head = _finish_commit_cleanup(git_repo, tid, slug)
        branches.append(previous)
        heads.append(head)

    # 链结构确认：t001 ⊂ t002 ⊂ t003，main 未前进
    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == main_head
    assert _git(
        git_repo, "merge-base", "--is-ancestor", branches[0], branches[1], check=False
    ).returncode == 0
    assert _git(
        git_repo, "merge-base", "--is-ancestor", branches[1], branches[2], check=False
    ).returncode == 0

    result = _task_cli(git_repo, "integrate", "t003", "--chain")

    assert result.returncode == 0, result.stderr
    # 只合链尾，但三个 head 全部在 main 历史里（祖先跟随）
    for head in heads:
        assert _git(
            git_repo, "merge-base", "--is-ancestor", head, "main", check=False
        ).returncode == 0
    # 三个分支全删
    for b in branches:
        assert _git(git_repo, "branch", "--list", b).stdout.strip() == ""
    # 只进一次 merge commit
    subjects = _git(git_repo, "log", "--format=%s", "-3").stdout.split("\n")
    assert subjects[0] == "chore(task): rebuild task indexes"
    assert subjects[1].startswith("merge(t003)")
    # 全部 done
    for tid, slug in (("t001", "alpha"), ("t002", "beta"), ("t003", "gamma")):
        fm, _ = parse_front_matter(
            git_repo / f"docs/archive/tasks/{tid}_{slug}/task.md"
        )
        assert fm["status"] == "done"


def test_chain_integrate_rejects_mid_chain_undone(git_repo):
    """链上有未完成的 task 时拒绝合并。"""
    _start(git_repo, "t001")
    first_branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    _start(git_repo, "t002", base=first_branch)  # t002 active 未 finish

    result = _task_cli(git_repo, "integrate", "t001", "--chain")

    assert result.returncode != 0
    assert "须全部 done/dropped" in result.stderr


def test_chain_integrate_rejects_mid_chain_registered_worktree(git_repo):
    """链上有 task 仍挂 worktree 时拒绝合并。"""
    _start(git_repo, "t001")
    first_branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    _start(git_repo, "t002", base=first_branch)
    # t002 finish + commit 但不 cleanup-worktree
    worktree = _worktree_path(git_repo, "t002")
    assert _task_cli(worktree, "finish", "t002").returncode == 0
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "feat(t002): beta")

    result = _task_cli(git_repo, "integrate", "t002", "--chain")

    assert result.returncode != 0
    assert "仍登记 worktree" in result.stderr


def test_integrate_requires_primary_worktree(git_repo):
    _start(git_repo, "t001")
    worktree = _worktree_path(git_repo)

    result = _task_cli(worktree, "integrate", "t001")

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
    # rewind 将 schedule_status 置为 pending_clarification；若被旧分支 active 覆盖则不会出现
    assert "schedule_status=pending_clarification" in result.stdout


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
    tasks = task_mod.scan_tasks_at_ref("t002_beta")
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

    with pytest.raises(task_mod.TaskDataError, match="缺 task.md"):
        task_mod.scan_tasks_at_ref("t002_beta")


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


def test_edit_updates_dependencies_and_symmetric_conflicts(git_repo):
    dependency = _task_cli(
        git_repo,
        "edit",
        "t002",
        "--depends-on",
        "t003,t001,t003",
        "--schedule-status",
        "scheduled",
    )
    conflict = _task_cli(
        git_repo,
        "edit",
        "t001",
        "--conflicts-with",
        "t003,t002",
    )

    assert dependency.returncode == 0, dependency.stderr
    assert conflict.returncode == 0, conflict.stderr
    first, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
    second, _ = parse_front_matter(git_repo / "docs/tasks/t002_beta/task.md")
    third, _ = parse_front_matter(git_repo / "docs/tasks/t003_gamma/task.md")
    assert second["depends_on"] == "t001,t003"
    assert second["schedule_status"] == "scheduled"
    assert first["conflicts_with"] == "t002,t003"
    assert second["conflicts_with"] == "t001"
    assert third["conflicts_with"] == "t001"

    removed = _task_cli(git_repo, "edit", "t001", "--conflicts-remove", "t002")
    assert removed.returncode == 0, removed.stderr
    first, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
    second, _ = parse_front_matter(git_repo / "docs/tasks/t002_beta/task.md")
    assert first["conflicts_with"] == "t003"
    assert second["conflicts_with"] == ""


def test_edit_rejects_conflict_reverse_edge_to_active_task(git_repo):
    _start(git_repo, "t001")

    result = _task_cli(git_repo, "edit", "t002", "--conflicts-with", "t001")

    assert result.returncode != 0
    assert "无法维护冲突反向边" in result.stderr


def test_edit_skips_reverse_edge_for_done_target_in_main(git_repo):
    """done target 已合 main、归档不可写：owner 单边增删 conflicts，
    不再因 peer.status=done 而卡死。"""
    _start(git_repo, "t001")
    branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    _git(git_repo, "merge", "--no-ff", branch, "-m", "merge t001")
    _task_cli(git_repo, "list", "--rebuild")
    _git(git_repo, "add", "docs/tasks_index.json", "docs/archive/tasks_index.json")
    _git(git_repo, "commit", "-m", "chore: rebuild index")
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


def test_rewind_to_backlog_marks_schedule_pending(git_repo):
    scheduled = _task_cli(
        git_repo, "edit", "t001", "--schedule-status", "scheduled"
    )
    assert scheduled.returncode == 0, scheduled.stderr
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "schedule t001")
    _start(git_repo, "t001")

    _rewind(git_repo, "t001")

    fm, _ = parse_front_matter(git_repo / "docs/tasks/t001_alpha/task.md")
    assert fm["status"] == "backlog"
    assert fm["schedule_status"] == "pending_clarification"


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
        ("t001", "--schedule-status", "scheduled"),
        ("t002", "--depends-on", "t001", "--schedule-status", "scheduled"),
        ("t003", "--conflicts-with", "t002", "--schedule-status", "scheduled"),
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
    """冲突 task 未合入 main 前一直阻塞；合入 main 后才解冲突进下一批。"""
    for tid in ("t001", "t002"):
        result = _task_cli(
            git_repo, "edit", tid, "--schedule-status", "scheduled"
        )
        assert result.returncode == 0, result.stderr
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

    branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    # t001 done 但未合入 main：t002 仍被冲突阻塞
    unmerged = _task_cli(git_repo, "view")
    assert unmerged.returncode == 0, unmerged.stderr
    assert "▸ 被冲突阻塞" in unmerged.stdout
    assert "t002 ↔ t001" in unmerged.stdout
    assert "未入 main" in unmerged.stdout

    # 合入 main 后 t001 进 main_done_set，t002 解冲突进下一批
    _git(git_repo, "merge", "--ff-only", branch)
    completed = _task_cli(git_repo, "view")
    assert completed.returncode == 0, completed.stderr
    assert "▸ 下一批可跑" in completed.stdout
    assert "t002" in completed.stdout
    assert "未入 main" not in completed.stdout


def test_view_handles_diamond_dependencies(git_repo):
    """菱形依赖：t003 依赖 t001+t002，二者未完成时 t003 进被依赖阻塞组。"""
    commands = (
        ("t001", "--schedule-status", "scheduled"),
        ("t002", "--schedule-status", "scheduled"),
        (
            "t003",
            "--depends-on",
            "t001,t002",
            "--schedule-status",
            "scheduled",
        ),
    )
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
        "--schedule-status",
        "scheduled",
    )
    assert dependency.returncode == 0, dependency.stderr
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "schedule dependency")

    _start(git_repo, "t001")
    branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
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


def test_view_reports_pending_and_unscheduled(git_repo):
    """view：scheduled 进下一批，pending_clarification 与未排程各自成组。"""
    scheduled = _task_cli(
        git_repo, "edit", "t001", "--schedule-status", "scheduled"
    )
    pending = _task_cli(
        git_repo,
        "edit",
        "t002",
        "--schedule-status",
        "pending_clarification",
    )
    assert scheduled.returncode == 0, scheduled.stderr
    assert pending.returncode == 0, pending.stderr

    result = _task_cli(git_repo, "view")

    assert result.returncode == 0, result.stderr
    assert "▸ 下一批可跑" in result.stdout
    assert "t001" in result.stdout
    assert "▸ 调度未就绪" in result.stdout
    assert "t002  schedule_status=pending_clarification" in result.stdout
    assert "▸ 未排程" in result.stdout
    assert "t003" in result.stdout


def test_view_reads_historical_conflict_as_undirected(git_repo):
    """单向冲突声明（历史脏数据）按无向处理：双方互相阻塞，都进冲突组。"""
    first_path = git_repo / "docs/tasks/t001_alpha/task.md"
    first, first_body = parse_front_matter(first_path)
    first["schedule_status"] = "scheduled"
    first["conflicts_with"] = "t002"
    write_front_matter(first_path, first, first_body)

    second_path = git_repo / "docs/tasks/t002_beta/task.md"
    second, second_body = parse_front_matter(second_path)
    second["schedule_status"] = "scheduled"
    second["conflicts_with"] = ""
    write_front_matter(second_path, second, second_body)

    result = _task_cli(git_repo, "view")

    assert result.returncode == 0, result.stderr
    # 无向冲突：t001 声明 t002，t002 反向继承；序号小者优先可跑，大者被阻塞
    assert "▸ 下一批可跑" in result.stdout
    assert "t001" in result.stdout
    assert "▸ 被冲突阻塞" in result.stdout
    assert "t002 ↔ t001" in result.stdout


def test_view_picks_lower_tid_for_symmetric_backlog_conflict(git_repo):
    """两个 backlog 互相冲突：序号小者优先可跑，大者被序号小者阻塞。"""
    for tid in ("t002", "t001"):
        result = _task_cli(
            git_repo, "edit", tid, "--schedule-status", "scheduled"
        )
        assert result.returncode == 0, result.stderr
    # t001 序号小，先声明冲突；t001 进 ready，t002 被 t001 阻塞
    conflict = _task_cli(git_repo, "edit", "t001", "--conflicts-with", "t002")
    assert conflict.returncode == 0, conflict.stderr

    result = _task_cli(git_repo, "view")
    assert result.returncode == 0, result.stderr
    assert "▸ 下一批可跑" in result.stdout
    assert "t001" in result.stdout
    assert "t002" not in result.stdout.split("▸ 被冲突阻塞")[0]
    assert "▸ 被冲突阻塞" in result.stdout
    assert "t002 ↔ t001" in result.stdout
    assert "t002 ↔ t001  — t002: beta" in result.stdout


def test_view_rejects_dangling_and_dropped_references(git_repo):
    """view 图校验：依赖/冲突引用不存在或 dropped task 均报错。"""
    first_path = git_repo / "docs/tasks/t001_alpha/task.md"
    first, first_body = parse_front_matter(first_path)
    first["schedule_status"] = "scheduled"
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
    template_task = git_repo / "docs/tasks/task_template/task.md"
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
            "schedule_status": "scheduled",
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
    assert second["conflicts_with"] == ""
    assert third["conflicts_with"] == ""
