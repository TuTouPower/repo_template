"""Repository paths, shared constants, and data errors."""

import os
import re
from datetime import timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SCRIPT_DIR.parent.parent

ACTIVE_PATH = REPO_ROOT / "docs/tasks_index.json"
ARCHIVE_PATH = REPO_ROOT / "docs/archive/tasks_index.json"
AUDIT_PATH = REPO_ROOT / "docs/archive/tasks_audit.log"
TASKS_DIR = REPO_ROOT / "docs" / "tasks"
ARCHIVE_TASKS_DIR = REPO_ROOT / "docs" / "archive" / "tasks"
TEMPLATE_DIR = TASKS_DIR / "task_template"
RUNTIME_DIR = REPO_ROOT / "docs" / "runtime"
LEDGER_PATH = RUNTIME_DIR / "dispatch_ledger.jsonl"

VALID_STATUSES = ("backlog", "active", "blocked", "done", "dropped")
ARCHIVED_STATUSES = ("done", "dropped")
SCHEDULE_STATUSES = ("scheduled", "pending_clarification")
# 仅活跃目录内可 rewind 的状态及其顺序（防 forward）
STATUS_ORDER = ("backlog", "active", "blocked")
DEFAULT_REWIND = {"active": "backlog", "blocked": "active"}  # 撤一步映射
BLOCK_REASONS = ("blackbox", "review", "infra")
REVIEW_LEVELS = ("full", "single")
DEFAULT_REVIEW_LEVEL = "full"
LEDGER_RECORDABLE_EVENTS = ("note",)
LEDGER_REPORT_STATUSES = ("done", "blocked", "failed")
LEDGER_TERMINAL_STATUSES = ("completed", "failed", "stopped")
LEDGER_FAIL_CLASSES = ("infra", "task", "contract")
ATTEMPT_EXECUTORS = ("inline",)
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
TASK_BRANCH_RE = re.compile(r"^(t[0-9]+)_[a-z][a-z0-9_]*$")
TID_RE = re.compile(r"^t([0-9]+)$")
HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*$")
H3_RE = re.compile(r"^ {0,3}###[ \t]+(.+?)[ \t]*$")
LIST_ITEM_RE = re.compile(r"^ {0,3}-[ \t]+(.+)$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
UNVERIFIED_RE = re.compile(
    r"(?<![A-Z0-9_-])UNVERIFIED(?:-(BLOCKING|SPIKE))?(?![A-Z0-9_-])"
)
TEMPLATE_PLACEHOLDER_RE = re.compile(r"\{[^{}\n]*[一-鿿][^{}\n]*\}")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
UNKNOWN_CONTRACT_HEADING = "未知契约清单"
SPEC_REQUIRED_HEADINGS = (
    (1, "Task spec"),
    (2, "背景"),
    (2, "契约区"),
    (3, "范围"),
    (3, "非范围"),
    (3, "验收标准"),
    (3, "可测试性声明"),
    (2, "上下文区"),
    (3, "有意不测"),
    (3, "测试策略"),
    (3, "未知契约清单"),
    (3, "风险与回退"),
    (3, "依赖与约束"),
    (3, "Finalization 时更新的 blueprint"),
)
SPEC_GUIDE_OPEN = "<!-- 规范（门禁必留，不得删除） -->"
SPEC_GUIDE_CLOSE = "<!-- /规范 -->"
TASK_REQUIRED_HEADINGS = (
    (1, "Task 过程总账"),
    (2, "实施笔记"),
    (2, "Review 处置"),
    (2, "收尾报告"),
)
IMPLEMENTATION_NOTE_GUIDANCE = (
    "执行期边做边写：实际步骤、踩坑、中途决策、偏离 spec、关键验证、blocked 原因与用户放行的新轮次上限。",
    "创建期不预测实施步骤——那时尚未读代码，预测必然失准。只记有追溯价值的内容，不写命令流水账。无事项时写：无",
)
TZ_CN = timezone(timedelta(hours=8))

FRONT_MATTER_KEYS = (
    "tid", "slug", "title", "status", "branch", "worktree",
    "review_level", "diff_anchor", "depends_on", "conflicts_with",
    "schedule_status", "note",
)


class TaskDataError(Exception):
    """task 数据不一致。"""


def _rel(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()



def worktree_rel_path(tid: str) -> str:
    return f"../{REPO_ROOT.name}_{tid}"

def effective_worktree(fm: dict) -> str:
    """worktree 相对路径：front matter 优先；主仓副本不含该字段（start 后不再回写主仓），
    此时按命名约定推导。路径不存在或未登记时由 remove_worktree 安全放过。"""
    return fm.get("worktree") or worktree_rel_path(fm["tid"])
