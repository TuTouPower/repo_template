"""P0-1 完整产物树隐私门禁回归 + DS L223 单一 fence 规则一致性。

工厂既有种子仓只复制 ``scripts/`` + ``docs/task_template``，不含随硬同步分发
的 ``.repo_template/tests/``、``hooks/``、``docs/`` 等文件，无法暴露「模板自带
夹具命中隐私门禁 → 消费仓 finish 必然失败」。本文件用完整产物树 helper 验证
门禁在真实分发布局下不误报、仍是候选、仍能拦截真泄漏，并跑真实 finish 链路。
"""

import argparse
import contextlib
import io
import json

import pytest

from git_repo_support import (
    build_full_product_tree_repo,
    copy_seed_repo,
    git_run,
    install_fake_md_kx,
)
from render_review_prompts import extract_section
from repo_task import context as ctx
from repo_task import control, lifecycle, privacy
from repo_task.documents import _visible_markdown_lines
from repo_task.review import visible_markdown_lines as review_visible_lines
from task_flow_support import finish_cli, prepare_finish

OWNER = "owner-privacy-full-tree"


def _joined(*parts: str) -> str:
    return "/".join(parts)


@pytest.fixture(scope="module")
def full_tree_repo(tmp_path_factory):
    return build_full_product_tree_repo(tmp_path_factory.mktemp("full_tree") / "repo")


def test_full_product_tree_index_has_no_privacy_leaks(full_tree_repo):
    """完整产物树被 Git 跟踪后，index 全量扫描不得误报（P0-1 根因回归）。"""
    assert privacy.scan_git_index_privacy(full_tree_repo) == []


def test_full_product_tree_tree_scan_has_no_privacy_leaks(full_tree_repo):
    assert privacy.scan_git_tree_privacy(full_tree_repo, "HEAD") == []


def test_template_test_file_is_still_a_scan_candidate(full_tree_repo):
    """不得通过豁免 .repo_template/tests/ 绕过门禁。"""
    assert privacy.is_privacy_candidate(".repo_template/tests/test_privacy.py")
    tracked = git_run(full_tree_repo, "ls-files", "-z").stdout.split("\0")
    assert ".repo_template/tests/test_privacy.py" in tracked


def test_finish_privacy_precheck_accepts_full_product_tree(full_tree_repo):
    """finish 前置的隐私检查（_check_privacy_leaks）在完整产物树上不阻断。"""
    repo = ctx.RepoContext.for_root(full_tree_repo)
    task_dir = full_tree_repo / "docs" / "tasks" / "t001_alpha"
    lifecycle._check_privacy_leaks(task_dir, repo=repo)


def test_full_product_tree_still_blocks_real_leak(full_tree_repo, tmp_path):
    """门禁未被削弱：往完整产物树仓注入真泄漏仍被拦截。"""
    repo = copy_seed_repo(full_tree_repo, tmp_path / "with_leak")
    token = _joined("", "Users", "alice")
    (repo / "leak.md").write_text("路径 " + token + "/proj\n", encoding="utf-8")
    git_run(repo, "add", "-A")
    leaks = privacy.scan_git_index_privacy(repo)
    assert any(entry.startswith("leak.md:1:") for entry in leaks)


def _begin(repo, tid="t001", owner=OWNER):
    context = ctx.RepoContext.for_root(repo)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        control.cmd_run_begin(argparse.Namespace(
            tid=tid, owner_id=owner, base=None, repo=context
        ))
    return json.loads(buf.getvalue())


def test_real_finish_passes_on_full_product_tree(full_tree_repo, tmp_path, monkeypatch):
    """完整产物树 + finish：隐私门禁不误阻断，实现 commit 正常产生。"""
    repo = copy_seed_repo(full_tree_repo, tmp_path / "finish_repo")
    install_fake_md_kx(tmp_path, monkeypatch)
    _begin(repo)
    worktree = repo.parent / f"{repo.name}_t001"
    message_file = prepare_finish(worktree, "t001", "alpha")
    head_before = git_run(worktree, "rev-parse", "HEAD").stdout.strip()

    result = finish_cli(worktree, "t001", OWNER, message_file)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "隐私" not in result.stderr
    assert git_run(worktree, "rev-parse", "HEAD").stdout.strip() != head_before


FENCE_TEXT = (
    "````markdown\n"
    "## 契约区\n"
    "伪内容\n"
    "````md\n"           # 带 info string 的同宽围栏行：是内容，不是闭栏
    "### 伪标题\n"
    "````\n"
    "## 契约区\n"
    "真实内容\n"
)


def test_fence_rules_are_unified_across_consumers():
    """DS L223：context/documents/review/render 共用单一 fence 规则。"""
    lines = FENCE_TEXT.splitlines()
    state = ctx.FenceState()
    assert all(state.feed(line) for line in lines[:6])  # 带 info 的围栏行仍在块内
    assert not state.feed(lines[6])
    assert "伪内容" not in _visible_markdown_lines(FENCE_TEXT)
    assert "### 伪标题" not in _visible_markdown_lines(FENCE_TEXT)
    assert "### 伪标题" not in review_visible_lines(FENCE_TEXT)
    assert extract_section(FENCE_TEXT, "## 契约区") == "真实内容"


# --- DS L216：未命中 .repo_template 祖先时显式报错，不再 parents[3] 猜根 ---
def test_from_code_location_resolves_toolkit_ancestor(full_tree_repo):
    resolved = ctx.RepoContext.from_code_location(
        full_tree_repo / ".repo_template" / "scripts" / "task.py"
    )
    assert resolved.root == full_tree_repo.resolve()


def test_from_code_location_rejects_unresolvable_root(tmp_path):
    with pytest.raises(ctx.TaskDataError, match="定位"):
        ctx.RepoContext.from_code_location(tmp_path / "unrelated" / "module.py")
