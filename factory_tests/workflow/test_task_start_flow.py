"""task.py 流程测试的共享 helper 与 ``git_repo`` fixture 模块。

用例已按被测主题拆分到 ``test_task_*.py``；本模块只保留其它测试
import 的 ``git_repo`` / ``_task_cli`` 等符号，避免调用方感知拆分。
"""
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
from git_repo_support import state_file


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
















































# --------------------------------------------------------------------------
# integrate：完成即合并
# --------------------------------------------------------------------------
























# --------------------------------------------------------------------------
# integrate-chain：串行链式，只合链尾
# --------------------------------------------------------------------------




































# --------------------------------------------------------------------------
# 审阅修复回归测试
# --------------------------------------------------------------------------

def _rewind(repo, tid="t001", to="backlog", yes=True):
    lifecycle.cmd_rewind(argparse.Namespace(
        tid=tid, to=to, reason="撤回", yes=yes, repo=ctx.RepoContext.for_root(repo),
    ))












































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
















# --------------------------------------------------------------------------
# task 调度图与 view
# --------------------------------------------------------------------------















































def _ledger(repo):
    path = state_file(repo, "ledger.jsonl")
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text("utf-8").splitlines()
        if line.strip()
    ]










# --------------------------------------------------------------------------
# effective-status：有效状态与读取来源（worktree / branch / main）
# --------------------------------------------------------------------------
































