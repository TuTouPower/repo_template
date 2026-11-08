"""核心资产所有权校验与合并门禁单元测试。

验证 monitoring.validate_task_core_asset_changes：
- 新增、删除、内容修改、模式变化、跨目录移动、子目录隐藏拦截；
- 非核心普通文件放行；
- 授权 owner 核心资产修改放行；
- lifecycle 与 integration 门禁拦截联动。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import pytest
import repo_task.context as ctx
from repo_task.context import TaskDataError
from repo_task.monitoring import (
    TASK_CORE_ASSET_NAMES,
    validate_task_core_asset_changes,
)

pytestmark = pytest.mark.contract


def test_core_asset_names_constant():
    assert ctx.PROGRESS_FILE in TASK_CORE_ASSET_NAMES
    assert "task.md" in TASK_CORE_ASSET_NAMES
    assert "handoff.json" in TASK_CORE_ASSET_NAMES


@pytest.mark.parametrize("core_file", [
    "progress.md",
    "task.md",
    "handoff.json",
    "review_code.md",
    "review_test.md",
    "review_general.md",
    "sub/progress.md",
    "nested/dir/review_arch.md",
])
def test_reject_unauthorized_modification_of_core_asset(core_file):
    path = f"docs/tasks/t002_bar/{core_file}"
    before = {path: ("100644", "oid_before_1111")}
    after = {path: ("100644", "oid_after_2222")}
    with pytest.raises(TaskDataError, match="禁止修改其他 task 核心资产.*modified"):
        validate_task_core_asset_changes(before, after, owner_names={"t001_foo"})


@pytest.mark.parametrize("core_file", [
    "progress.md",
    "task.md",
    "handoff.json",
    "review_code.md",
    "sub/handoff.json",
])
def test_reject_unauthorized_addition_of_core_asset(core_file):
    path = f"docs/tasks/t002_bar/{core_file}"
    before = {}
    after = {path: ("100644", "oid_new_3333")}
    with pytest.raises(TaskDataError, match="禁止修改其他 task 核心资产.*added"):
        validate_task_core_asset_changes(before, after, owner_names={"t001_foo"})


@pytest.mark.parametrize("core_file", [
    "progress.md",
    "task.md",
    "handoff.json",
    "review_test.md",
    "sub/task.md",
])
def test_reject_unauthorized_deletion_of_core_asset(core_file):
    path = f"docs/tasks/t002_bar/{core_file}"
    before = {path: ("100644", "oid_old_4444")}
    after = {}
    with pytest.raises(TaskDataError, match="禁止修改其他 task 核心资产.*deleted"):
        validate_task_core_asset_changes(before, after, owner_names={"t001_foo"})


def test_reject_unauthorized_mode_change_of_core_asset():
    path = "docs/tasks/t002_bar/progress.md"
    before = {path: ("100644", "oid_same")}
    after = {path: ("100755", "oid_same")}
    with pytest.raises(TaskDataError, match="禁止修改其他 task 核心资产.*mode_changed"):
        validate_task_core_asset_changes(before, after, owner_names={"t001_foo"})


def test_reject_unauthorized_content_and_mode_change_of_core_asset():
    path = "docs/tasks/t002_bar/task.md"
    before = {path: ("100644", "oid_old")}
    after = {path: ("100755", "oid_new")}
    with pytest.raises(TaskDataError, match="禁止修改其他 task 核心资产.*content_and_mode_changed"):
        validate_task_core_asset_changes(before, after, owner_names={"t001_foo"})


def test_reject_unauthorized_symlink_or_gitlink_in_task_dir():
    # 非 owner 目录下引入 symlink (120000) 哪怕是非核心文件也必须阻断
    symlink_path = "docs/tasks/t002_bar/link_to_core"
    before = {}
    after = {symlink_path: ("120000", "oid_link")}
    with pytest.raises(TaskDataError, match="非 owner task 目录含不支持的文件模式"):
        validate_task_core_asset_changes(before, after, owner_names={"t001_foo"})

    # gitlink (160000)
    gitlink_path = "docs/tasks/t002_bar/submodule"
    after_sub = {gitlink_path: ("160000", "commit_sha")}
    with pytest.raises(TaskDataError, match="非 owner task 目录含不支持的文件模式"):
        validate_task_core_asset_changes(before, after_sub, owner_names={"t001_foo"})


def test_reject_cross_task_core_asset_move():
    # 模拟将 t002 的 progress.md 移走（t002 处删除，或者改名）
    src = "docs/tasks/t002_bar/progress.md"
    dst = "docs/tasks/t001_foo/stolen_progress.md"
    before = {src: ("100644", "oid_1")}
    after = {dst: ("100644", "oid_1")}
    with pytest.raises(TaskDataError, match="禁止修改其他 task 核心资产.*deleted"):
        validate_task_core_asset_changes(before, after, owner_names={"t001_foo"})


def test_allow_owner_modification_of_core_assets():
    # owner 为 t001_foo 时，修改自己的核心资产应放行
    before = {
        "docs/tasks/t001_foo/progress.md": ("100644", "oid_p1"),
        "docs/tasks/t001_foo/task.md": ("100644", "oid_t1"),
    }
    after = {
        "docs/tasks/t001_foo/progress.md": ("100644", "oid_p2"),
        "docs/tasks/t001_foo/task.md": ("100644", "oid_t2"),
        "docs/tasks/t001_foo/handoff.json": ("100644", "oid_h1"),
        "docs/tasks/t001_foo/review_code.md": ("100644", "oid_r1"),
    }
    # 零异常
    validate_task_core_asset_changes(before, after, owner_names={"t001_foo"})


def test_allow_unauthorized_task_non_core_regular_files():
    # 允许修改非 owner 目录下的非核心普通补充文件
    path = "docs/tasks/t002_bar/notes.txt"
    before = {path: ("100644", "oid_note1")}
    after = {path: ("100644", "oid_note2")}
    # 零异常
    validate_task_core_asset_changes(before, after, owner_names={"t001_foo"})


def test_ignore_template_directory_changes():
    # task_template 不是 task 实体，不视为受保护的核心资产侵犯
    path = "docs/tasks/task_template/progress.md"
    before = {path: ("100644", "oid_tmpl1")}
    after = {path: ("100644", "oid_tmpl2")}
    validate_task_core_asset_changes(before, after, owner_names={"t001_foo"})


def test_multiple_owners_support():
    # 在整链集成时支持多个 owner
    before = {
        "docs/tasks/t001_foo/progress.md": ("100644", "oid_1"),
        "docs/tasks/t002_bar/progress.md": ("100644", "oid_2"),
    }
    after = {
        "docs/tasks/t001_foo/progress.md": ("100644", "oid_1_new"),
        "docs/tasks/t002_bar/progress.md": ("100644", "oid_2_new"),
    }
    # 两者均在 owner_names 中时放行
    validate_task_core_asset_changes(before, after, owner_names={"t001_foo", "t002_bar"})

    # 若 t003_baz 被改动则阻断
    after["docs/tasks/t003_baz/progress.md"] = ("100644", "oid_3_bad")
    with pytest.raises(TaskDataError, match="禁止修改其他 task 核心资产.*t003"):
        validate_task_core_asset_changes(before, after, owner_names={"t001_foo", "t002_bar"})
