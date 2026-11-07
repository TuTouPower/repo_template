"""Repository paths, shared constants, and data errors.

仓库身份由不可变 ``RepoContext`` 表达：入口解析目标后构造，所有文件/Git 操作显式
接收该上下文。``root`` 是主工作区（控制面身份），``worktree`` 是当前工作区
（task 文档所在）；主工作区内两者相同。

路径派生物（tasks 目录、ledger、audit、模板目录）全部由上下文计算，不再依赖
模块级可变全局。
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path


def _git_common_root(code_root: Path) -> Path:
    """由当前工作区解析主工作区根；非独立 git 工作区时退回 code_root。"""
    if not (code_root / ".git").exists():
        return code_root
    try:
        result = subprocess.run(
            ["git", "-C", str(code_root), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return code_root
    if result.returncode != 0 or not result.stdout.strip():
        return code_root
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = (code_root / common).resolve()
    return common.parent if common.name == ".git" else common


@dataclass(frozen=True)
class RepoContext:
    """不可变仓库身份。

    root：当前工作区根（task 文档所在；主仓内等于 primary_root）。
    primary_root：主工作区根（ledger、audit、worktree 登记与命名）。
    """

    root: Path
    primary_root: Path

    @classmethod
    def for_root(cls, root: Path, *, primary_root: Path | None = None) -> "RepoContext":
        current = root.expanduser().resolve()
        primary = (primary_root or _git_common_root(current)).expanduser().resolve()
        return cls(root=current, primary_root=primary)

    @classmethod
    def from_code_location(cls, code_file: Path) -> "RepoContext":
        """入口脚本/模块所在工作区：自动识别主工作区。"""
        return cls.for_root(Path(code_file).resolve().parents[3])

    @property
    def name(self) -> str:
        return self.primary_root.name

    @property
    def toolkit_root(self) -> Path:
        return self.root / ".repo_template"

    @property
    def tasks_dir(self) -> Path:
        return self.root / "docs" / "tasks"

    @property
    def archive_tasks_dir(self) -> Path:
        return self.root / "docs" / "archive" / "tasks"

    @property
    def template_dir(self) -> Path:
        return self.toolkit_root / "docs" / "task_template"

    @property
    def runtime_dir(self) -> Path:
        return self.primary_root / "docs" / "runtime"

    @property
    def ledger_path(self) -> Path:
        return self.runtime_dir / "dispatch_ledger.jsonl"

    @property
    def audit_path(self) -> Path:
        return self.primary_root / "docs" / "archive" / "tasks_audit.log"

    def rel(self, path: Path) -> str:
        for base in (self.root, self.primary_root):
            try:
                return path.relative_to(base).as_posix()
            except ValueError:
                continue
        return path.as_posix()

    def worktree_rel(self, tid: str) -> str:
        return f"../{self.name}_{tid}"

    def effective_worktree(self, fm: dict) -> str:
        """front matter 优先；主仓副本按命名约定推导。"""
        return fm.get("worktree") or self.worktree_rel(fm["tid"])


def current_context() -> "RepoContext":
    """过渡期默认身份：读取可被测试替换的模块级路径。A15 收尾时删除。"""
    return RepoContext.for_root(REPO_ROOT)


# 过渡期默认上下文：入口（task.py）会构造并显式传递；旧调用点在迁移完成前
# 仍可省略参数。A15 收尾时删除该默认值与同名模块级路径。
DEFAULT_CONTEXT = RepoContext.from_code_location(Path(__file__))
SCRIPT_DIR = DEFAULT_CONTEXT.toolkit_root / "scripts"
TOOLKIT_ROOT = DEFAULT_CONTEXT.toolkit_root
REPO_ROOT = DEFAULT_CONTEXT.root
AUDIT_PATH = DEFAULT_CONTEXT.audit_path
TASKS_DIR = DEFAULT_CONTEXT.tasks_dir
ARCHIVE_TASKS_DIR = DEFAULT_CONTEXT.archive_tasks_dir
TEMPLATE_DIR = DEFAULT_CONTEXT.template_dir
RUNTIME_DIR = DEFAULT_CONTEXT.runtime_dir
LEDGER_PATH = DEFAULT_CONTEXT.ledger_path

VALID_STATUSES = ("backlog", "active", "done", "dropped")
ARCHIVED_STATUSES = ("done", "dropped")
# 仅活跃目录内可 rewind 的状态及其顺序（防 forward）
STATUS_ORDER = ("backlog", "active")
DEFAULT_REWIND = {"active": "backlog"}  # 撤一步映射
REVIEW_LEVELS = ("full", "single")
DEFAULT_REVIEW_LEVEL = "full"
LEDGER_REPORT_STATUSES = ("done", "blocked", "failed")
LEDGER_TERMINAL_STATUSES = ("completed", "failed", "stopped")
LEDGER_FAIL_CLASSES = ("infra", "task", "contract")
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
PROGRESS_FILE = "progress.md"
PROGRESS_TITLE = "Task 进展"
PROGRESS_REQUIRED_HEADINGS = (
    (1, PROGRESS_TITLE),
    (2, "实施笔记"),
    (2, "Review 处置"),
    (2, "收尾报告"),
)
IMPLEMENTATION_NOTE_GUIDANCE = (
    "执行期记录关键步骤、决策、验证、阻塞和用户批准的新轮次上限。",
    "创建期不预测实施步骤。只记有追溯价值的内容；无事项时写“无”。",
)
TZ_CN = timezone(timedelta(hours=8))

FRONT_MATTER_KEYS = (
    "tid", "slug", "title", "status", "branch", "worktree",
    "review_level", "review_limit", "verify_limit", "diff_anchor", "depends_on", "conflicts_with",
    "note",
)


class TaskDataError(Exception):
    """task 数据不一致。"""


class TaskNotFoundError(TaskDataError):
    """指定 ref 或目录中不存在目标 task。"""


def _rel(path: Path) -> str:
    return DEFAULT_CONTEXT.rel(path)


def worktree_rel_path(tid: str) -> str:
    return DEFAULT_CONTEXT.worktree_rel(tid)


def effective_worktree(fm: dict) -> str:
    return DEFAULT_CONTEXT.effective_worktree(fm)
