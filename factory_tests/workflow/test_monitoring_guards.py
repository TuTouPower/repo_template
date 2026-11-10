"""DS L220：核心资产所有权守卫回归（历史存量非法模式、非常规目录名）。

目录识别须与 scan_tasks 对齐（不再用 TASK_BRANCH_RE 过滤目录名），且仅在非法
模式被本次变更新引入时拒绝，避免历史存量 symlink/gitlink 阻断无关 task。
"""
import pytest

from repo_task.context import TaskDataError
from repo_task.monitoring import validate_task_core_asset_changes

OWNER = {"t001_foo"}


def test_historical_illegal_mode_untouched_does_not_block():
    """历史存量 symlink/gitlink 未被本次改动触碰，不应阻断无关 task。"""
    path = "docs/tasks/t002_bar/legacy_link"
    entry = {path: ("120000", "oid_link")}
    validate_task_core_asset_changes(entry, entry, OWNER)


def test_newly_introduced_illegal_mode_is_rejected():
    path = "docs/tasks/t002_bar/new_link"
    with pytest.raises(TaskDataError, match="非法模式"):
        validate_task_core_asset_changes({}, {path: ("120000", "oid_link")}, OWNER)


def test_changed_historical_illegal_mode_is_rejected():
    """改动一个存量非法模式（symlink -> gitlink）仍属新引入非法模式，拒绝。"""
    path = "docs/tasks/t002_bar/legacy_link"
    with pytest.raises(TaskDataError, match="非法模式"):
        validate_task_core_asset_changes(
            {path: ("120000", "oid_1")}, {path: ("160000", "oid_2")}, OWNER
        )


def test_repairing_historical_illegal_mode_is_allowed():
    """把存量非法模式改回普通文件不算引入非法模式，放行。"""
    path = "docs/tasks/t002_bar/legacy_link"
    validate_task_core_asset_changes(
        {path: ("120000", "oid_1")}, {path: ("100644", "oid_2")}, OWNER
    )


def test_unconventional_task_dir_name_is_still_guarded():
    """目录识别与 scan_tasks 对齐：不匹配 TASK_BRANCH_RE 的目录名照样受核心资产保护。"""
    path = "docs/tasks/t002_WeirdName/progress.md"
    with pytest.raises(TaskDataError, match="禁止修改其他 task 核心资产"):
        validate_task_core_asset_changes(
            {path: ("100644", "oid_1")}, {path: ("100644", "oid_2")}, OWNER
        )


def test_unconventional_task_dir_new_illegal_mode_is_rejected():
    path = "docs/tasks/t002-WeirdName/link"
    with pytest.raises(TaskDataError, match="非法模式"):
        validate_task_core_asset_changes({}, {path: ("160000", "oid_commit")}, OWNER)
