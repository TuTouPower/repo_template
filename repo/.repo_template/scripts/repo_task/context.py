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
    """由当前工作区解析主工作区根；仅在 .git 不存在时退回 code_root。

    .git 存在即代表这是 Git 工作区（含 linked worktree 的 gitfile）。此时 git 调用
    失败、超时或输出为空都意味着无法确认主工作区身份，必须显式报错，禁止把
    工作区本地目录静默当作主工作区根。
    """
    if not (code_root / ".git").exists():
        return code_root
    try:
        result = subprocess.run(
            ["git", "-C", str(code_root), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise TaskDataError(
            f"无法解析 {code_root} 的 git 公共目录（{error}）；"
            "拒绝把工作区本地目录当作主工作区根"
        ) from error
    if result.returncode != 0:
        raise TaskDataError(
            f"无法解析 {code_root} 的 git 公共目录："
            f"git rev-parse --git-common-dir 退出码 {result.returncode}"
            f"（{result.stderr.strip()}）；拒绝把工作区本地目录当作主工作区根"
        )
    if not result.stdout.strip():
        raise TaskDataError(
            f"无法解析 {code_root} 的 git 公共目录：git rev-parse --git-common-dir 输出为空；"
            "拒绝把工作区本地目录当作主工作区根"
        )
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
    def from_code_location(cls, code_file: Path | str) -> "RepoContext":
        """入口脚本/模块所在工作区：由 .repo_template 祖先定位；未命中即显式报错。"""
        resolved = Path(code_file).resolve()
        for parent in (resolved, *resolved.parents):
            if parent.name == ".repo_template":
                return cls.for_root(parent.parent)
        raise TaskDataError(
            f"无法从 {code_file} 定位 .repo_template 祖先；请显式指定仓库根（--root）"
        )

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
        return self.primary_root / "docs" / "runtime" / "tasks_audit.log"

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


VALID_STATUSES = ("backlog", "active", "done", "dropped")
TERMINAL_STATUSES = ("done", "dropped")
# 仅活跃状态可 rewind 及其顺序（防 forward）
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


class FenceState:
    """Markdown fenced code 状态机：仓库内唯一的 fence 规则实现。

    开栏用 ``FENCE_RE``（行首 0-3 空格 + 反引号/波浪号围栏），闭栏用
    ``FENCE_CLOSE_RE``（裸围栏、同字符、宽度 >= 开栏）。围栏内带 info string
    的围栏行是内容，不提前闭合代码块。``feed`` 返回该行是否属于 fenced code
    （含开栏与闭栏行本身）；各消费方据此决定是否纳入结构解析或正文抽取。
    """

    __slots__ = ("marker",)

    def __init__(self) -> None:
        self.marker: tuple[str, int] | None = None

    def feed(self, line: str) -> bool:
        if self.marker is not None:
            close = FENCE_CLOSE_RE.match(line)
            if (
                close
                and close.group(1)[0] == self.marker[0]
                and len(close.group(1)) >= self.marker[1]
            ):
                self.marker = None
            return True
        opening = FENCE_RE.match(line)
        if opening:
            self.marker = (opening.group(1)[0], len(opening.group(1)))
            return True
        return False
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
    "review_level", "review_limit", "verify_limit", "diff_anchor", "spec_source_sha", "depends_on", "conflicts_with",
    "note",
)


class TaskDataError(Exception):
    """task 数据不一致。"""


class TaskNotFoundError(TaskDataError):
    """指定 ref 或目录中不存在目标 task。"""
