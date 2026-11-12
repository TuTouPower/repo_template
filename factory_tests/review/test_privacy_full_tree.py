"""完整产物树布局下的 fence 规则一致性与 RepoContext 定位回归。

工厂既有种子仓只复制 ``scripts/`` + ``docs/task_template``，不含随硬同步分发
的 ``.repo_template/tests/``、``hooks/``、``docs/`` 等文件；本文件用完整产物树
helper 验证 DS L223 单一 fence 规则与 from_code_location 的定位行为。
"""

import pytest

from git_repo_support import build_full_product_tree_repo
from render_review_prompts import extract_section
from repo_task import context as ctx
from repo_task.documents import _visible_markdown_lines
from repo_task.review import visible_markdown_lines as review_visible_lines


@pytest.fixture(scope="module")
def full_tree_repo(tmp_path_factory):
    return build_full_product_tree_repo(tmp_path_factory.mktemp("full_tree") / "repo")


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
