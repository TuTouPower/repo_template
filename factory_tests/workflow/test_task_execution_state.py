"""task.py attempt、effective status 与 merge 历史 流程用例。

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
