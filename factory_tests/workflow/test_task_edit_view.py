"""task.py 调度边、冲突与 view 流程用例。

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
