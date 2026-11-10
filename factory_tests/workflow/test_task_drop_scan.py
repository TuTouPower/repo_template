"""task.py drop / edit / scan 流程用例。

从 test_task_start_flow 拆分；fixture 与 helper 仍由 test_task_start_flow 提供。"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT
from types import SimpleNamespace

import pytest


SCRIPTS_DIR = FACTORY_ROOT / "repo/.repo_template/scripts"
TASK_TEMPLATE_DIR = SCRIPTS_DIR.parent / "docs" / "task_template"
sys.path.insert(0, str(SCRIPTS_DIR))

from review_support import ensure_review_evidence, configure_project_verification, valid_spec
from task_flow_support import mark_done, finish_cli, prepare_finish

from repo_task import context as ctx
from repo_task import attempts, control, integration, lifecycle, store
from repo_task.monitoring import review_scope_fingerprint
from repo_task.documents import parse_front_matter, write_front_matter


from test_task_start_flow import (
    _check_ac, _finish_commit_cleanup, _git, _handoff, _identity_args, _ledger,
    _reserve, _rewind, _set_spec, _start, _task_cli, _terminal, _valid_spec,
    _valid_task_body, _worktree_path, _write_handoff, _write_worktree_include,
    git_repo,
)


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
