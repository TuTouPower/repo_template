"""task.py issue 90 回归 流程用例。

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
