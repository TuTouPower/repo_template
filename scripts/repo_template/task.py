#!/usr/bin/env python3
"""task.py - task 状态唯一操作入口。

状态权威 = 每个 task 目录下 `task.md` 的 YAML front matter。两套拓扑：

- 串行（task-run）：task 一个串一个成链，每个从上一个已完成 task 分支创建（`--base`），
  全部完成后一次性把链尾合并回主干（`integrate --chain`），主干只进一次 merge commit。
- 并行（task-dispatch）：每个 task 从主干 HEAD 扇出，完成即合并回主干（`integrate`）。

**角色语义**：主仓是唯一协调点，负责 task 创建、启动 worktree、合并、派生 index 重建、
worktree 与分支清理；`start` / `integrate` / `cleanup-worktree` / `list --rebuild` 只在主仓
默认分支执行。task 实施、review、block、resume、finish 与 active/blocked 的 drop 必须在自身
worktree 进行，且不触碰主仓。`start` 不要求主仓干净；激活状态先写入新 worktree，随该 task
唯一执行 commit 入库。

合并前主仓 `list` 只反映主干，分支中的状态用 `list/show --ref` 读取。

docs/tasks_index.json 与 docs/archive/tasks_index.json 是**派生缓存**：
只由 `integrate` 在合并后重建并入库；task worktree 的执行 commit 不更新它们。
`list` 只读，用 `list --rebuild` 在主仓手动重建。

数据：
  docs/tasks/{tid}_{slug}/task.md          活跃 task 状态权威
  docs/archive/tasks/{tid}_{slug}/task.md  归档 task 状态权威
  docs/tasks_index.json                    活跃派生缓存
  docs/archive/tasks_index.json            归档派生缓存
  docs/archive/tasks_audit.log             rewind/purge 审计（append-only）

命令：
  task.py add --title TITLE --slug SLUG [--note NOTE] [--review-level LEVEL]
  task.py edit TID [--title TITLE] [--note NOTE | --note-append NOTE] [--review-level LEVEL]
                   [--depends-on TIDS | --depends-append TID | --depends-remove TID]
                   [--conflicts-with TIDS | --conflicts-append TID | --conflicts-remove TID]
                   [--schedule-status scheduled|pending_clarification]
  task.py start TID [--base TASK_BRANCH]
                                   # 并行扇出（无 --base）从主干 HEAD 建 worktree；
                                   # 串行链式从上一已完成 task 分支建
  task.py preflight TID [--allow-backlog] [--ref BRANCH] [--require-verified]
                                   # 开干/进实现前门禁；可只读检查 backlog/ref 快照
  task.py block TID --reason blackbox|review|infra
  task.py resume TID
  task.py finish TID              # done + 目录归档；执行 commit 后 cleanup-worktree
  task.py cleanup-worktree TID    # 主仓清理已提交 worktree，保留分支
  task.py integrate TID [--continue] [--keep-branch] [--chain]
                                   # 并行：合单个分支进主干 + 重建 index + 删分支
                                   # 串行 --chain：只合链尾 + 删整条链分支
  task.py drop TID --reason TEXT  # dropped + 目录归档
  task.py rewind TID [--to backlog|active] --reason TEXT
  task.py purge TID --reason TEXT
  task.py list [--status STATUS] [--ref BRANCH] [--rebuild]
  task.py show TID [--ref BRANCH]
  task.py view                         # task 全景：运行中 / 待运行分组 / 已结束
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent

ACTIVE_PATH = REPO_ROOT / "docs/tasks_index.json"
ARCHIVE_PATH = REPO_ROOT / "docs/archive/tasks_index.json"
AUDIT_PATH = REPO_ROOT / "docs/archive/tasks_audit.log"
TASKS_DIR = REPO_ROOT / "docs" / "tasks"
ARCHIVE_TASKS_DIR = REPO_ROOT / "docs" / "archive" / "tasks"
TEMPLATE_DIR = TASKS_DIR / "task_template"

VALID_STATUSES = ("backlog", "active", "blocked", "done", "dropped")
ARCHIVED_STATUSES = ("done", "dropped")
SCHEDULE_STATUSES = ("scheduled", "pending_clarification")
# 仅活跃目录内可 rewind 的状态及其顺序（防 forward）
STATUS_ORDER = ("backlog", "active", "blocked")
DEFAULT_REWIND = {"active": "backlog", "blocked": "active"}  # 撤一步映射
BLOCK_REASONS = ("blackbox", "review", "infra")
REVIEW_LEVELS = ("full", "single")
DEFAULT_REVIEW_LEVEL = "full"
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
SPEC_REQUIRED_LINES = (
    "契约区执行期原则上不再改动；确需调整须经用户确认（渲染 review prompt 时脚本会附契约区相对 diff_anchor 的 drift diff 供 reviewer 核对）。上下文区执行期可补。",
    "reviewer 判 AC 时只看本区。",
    "只写用户或调用方可观察行为，每条可独立验证。普通版本号、底层库和目录结构不作为验收标准；需要长期约束后续工作的技术选择写入 `docs/blueprint/decisions.md`。",
    "需真实部署或人工环境才能验证的条目加 `[deploy]` 前缀，标明 agent 无法自证。",
    "逐条说明哪些 AC 不可自动测试及原因；全部可测则写「全部 AC 可自动测试」。",
    "reviewer 判测试覆盖时核对本区；实施期可补。",
    "已判定不写测试的分支与原因。reviewer 不得据此出 blocking finding。无则写「无」。",
    "mock 边界、fixture 来源、断言目标。无特殊约定写「按项目默认」。",
    "尚未核实的外部 endpoint、API 形态、数据结构、第三方行为须分类标记；核实后删除标记，改为结论并注明验证方式。无则写「无」。",
    "`UNVERIFIED-BLOCKING`：只有用户或外部环境能核实；核实前 `start` 失败。",
    "`UNVERIFIED-SPIKE`：agent 可在执行期 Step 1 实验核实；未核实前不得进入实现。",
    "裸 `UNVERIFIED` 属歧义格式，门禁失败。",
)
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
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------
# git
# --------------------------------------------------------------------------

def _git(args: list, *, root: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root or REPO_ROOT), *args],
        capture_output=True, text=True,
    )


def default_branch() -> str:
    """主干分支名：origin/HEAD → init.defaultBranch → 探测 main/master → main。"""
    r = _git(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"])
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip().removeprefix("origin/")
    r = _git(["config", "--get", "init.defaultBranch"])
    if r.returncode == 0 and r.stdout.strip():
        name = r.stdout.strip()
        if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"]).returncode == 0:
            return name
    for name in ("main", "master"):
        if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"]).returncode == 0:
            return name
    return "main"


def resolve_local_branch(name: str) -> tuple[str, str]:
    """校验本地分支并返回 (分支名, 完整 HEAD SHA)。"""
    if not name or name.startswith("-"):
        raise TaskDataError(f"本地分支名非法：{name!r}")
    ref = f"refs/heads/{name}"
    if _git(["show-ref", "--verify", "--quiet", ref]).returncode != 0:
        raise TaskDataError(f"本地分支不存在：{name!r}")
    r = _git(["rev-parse", f"{ref}^{{commit}}"])
    if r.returncode != 0 or not r.stdout.strip():
        raise TaskDataError(f"无法解析本地分支 {name!r} 的 HEAD")
    return name, r.stdout.strip()


def _get_head_short() -> str:
    r = _git(["rev-parse", "--short", "HEAD"])
    return (r.stdout.strip() or "unknown") if r.returncode == 0 else "unknown"


def _get_head() -> str:
    """全量 hash：存 diff_anchor 用（短 hash 长历史下有歧义风险）；展示场景用 _get_head_short。"""
    r = _git(["rev-parse", "HEAD"])
    return (r.stdout.strip() or "unknown") if r.returncode == 0 else "unknown"


def has_unmerged_commits(branch: str) -> bool:
    """branch 上是否有未合并进主干的 commit；不可判定时保守返回 True。"""
    if not branch:
        return False
    base = default_branch()
    if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{base}"]).returncode != 0:
        print(f"WARNING: 主干分支 '{base}' 不存在，无法判断是否已合并；按「有未合并 commit」处理", file=sys.stderr)
        return True
    if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode != 0:
        return False
    r = _git(["rev-list", "--count", f"{base}..{branch}"])
    if r.returncode != 0:
        print(f"WARNING: 无法比对 {branch} 与 {base}；按「有未合并 commit」处理", file=sys.stderr)
        return True
    try:
        return int(r.stdout.strip()) > 0
    except ValueError:
        return True


def tracked_anywhere(rel_path: str) -> bool:
    """路径是否在**任意分支**被跟踪（不只当前签出的索引）。"""
    if _git(["ls-files", "--error-unmatch", rel_path]).returncode == 0:
        return True
    r = _git(["rev-list", "--all", "--max-count=1", "--", rel_path])
    return r.returncode == 0 and bool(r.stdout.strip())


def porcelain_entries(root: Path | None = None) -> list[str]:
    """`git status --porcelain -z` 的路径列表，避免引号与转义带来的解析错误。"""
    r = _git(["status", "--porcelain", "-z"], root=root)
    if r.returncode != 0:
        return []
    out, fields, i = [], r.stdout.split("\0"), 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if not entry:
            continue
        code, path = entry[:2], entry[3:]
        if code[0] in ("R", "C"):  # 重命名/复制：下一字段是来源路径
            i += 1
        out.append(path)
    return out


def tracked_dirty_entries(root: Path | None = None) -> list[str]:
    """只含已跟踪文件的未提交改动；未跟踪文件不影响 merge 结果，不计入。"""
    r = _git(["status", "--porcelain", "-z", "--untracked-files=no"], root=root)
    if r.returncode != 0:
        return []
    out, fields, i = [], r.stdout.split("\0"), 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if not entry:
            continue
        code, path = entry[:2], entry[3:]
        if code[0] in ("R", "C"):
            i += 1
        out.append(path)
    return out


def worktree_paths() -> dict[str, str]:
    """`git worktree list --porcelain` → {绝对路径: 分支名}。"""
    result, current = {}, None
    r = _git(["worktree", "list", "--porcelain"])
    if r.returncode != 0:
        return result
    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            current = line[len("worktree "):].strip()
            result[current] = ""
        elif line.startswith("branch ") and current:
            result[current] = line[len("branch "):].strip().removeprefix("refs/heads/")
    return result


def primary_worktree_path() -> Path:
    """返回 Git 登记的主工作区；worktree list 第一项始终是主工作区。"""
    paths = worktree_paths()
    if not paths:
        sys.exit("无法读取 git worktree 列表")
    return Path(next(iter(paths))).resolve()


def in_primary_worktree() -> bool:
    return REPO_ROOT.resolve() == primary_worktree_path()


def current_branch() -> str:
    r = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    return r.stdout.strip() if r.returncode == 0 else ""


def require_primary_worktree() -> None:
    if not in_primary_worktree():
        sys.exit("此命令只能在主工作区执行；请 cd 回主仓")
    base = default_branch()
    branch = current_branch()
    if branch != base:
        sys.exit(f"此命令只能在主干 {base!r} 执行（当前 {branch!r}）")


def task_worktree_path(fm: dict) -> Path:
    return (REPO_ROOT / effective_worktree(fm)).resolve()


def in_own_task_worktree(fm: dict) -> bool:
    expected = task_worktree_path(fm)
    return (
        REPO_ROOT.resolve() == expected
        and str(expected) in worktree_paths()
        and current_branch() == fm.get("branch", "")
    )


def require_own_task_worktree(fm: dict) -> None:
    if not in_own_task_worktree(fm):
        sys.exit(
            f"{fm['tid']} 必须在自身 worktree {effective_worktree(fm)} 的分支 "
            f"{fm.get('branch')!r} 执行"
        )


# --------------------------------------------------------------------------
# front matter 读写
# --------------------------------------------------------------------------

def _quote(value: str) -> str:
    """YAML 双引号标量：转义反斜杠与双引号。"""
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _unquote(value: str) -> str:
    """还原 _quote 的转义；未加引号的值原样返回。"""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("\"", "'"):
        inner = value[1:-1]
        if value[0] == "'":
            return inner
        out, i = [], 0
        while i < len(inner):
            if inner[i] == "\\" and i + 1 < len(inner):
                out.append(inner[i + 1])
                i += 2
            else:
                out.append(inner[i])
                i += 1
        return "".join(out)
    return value


def parse_front_matter_text(text: str, *, source: str) -> tuple[dict, str]:
    """从文本返回 (front matter dict, 正文)。"""
    if not text.startswith("---"):
        raise TaskDataError(f"{source}: task.md 必须以 YAML front matter (---) 开头")
    end = text.find("\n---", 3)
    if end == -1:
        raise TaskDataError(f"{source}: front matter 未闭合（缺结束的 ---）")
    body = text[end + 4:].lstrip("\n")
    fm = {}
    for line in text[3:end].splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        val = val.strip()
        # 未加引号的值剥掉行内注释（# 前须有空格），防照搬文档示例后值被污染
        if val and val[0] not in ("\"", "'"):
            val = val.split(" #", 1)[0].rstrip()
        fm[key.strip()] = _unquote(val)
    return fm, body


def parse_front_matter(path: Path) -> tuple[dict, str]:
    """返回 (front matter dict, 正文)。缺失或不合法抛 TaskDataError。

    注意：render_review_prompts.py / check_review_status.py 各有简化副本，
    改解析规则需三处同步。
    """
    return parse_front_matter_text(
        path.read_text(encoding="utf-8"),
        source=_rel(path),
    )


def dump_front_matter(fm: dict) -> str:
    """所有值一律双引号包裹并转义，避免特殊字符破坏 YAML。"""
    keys = list(FRONT_MATTER_KEYS) + [k for k in fm if k not in FRONT_MATTER_KEYS]
    lines = ["---"]
    lines += [f"{key}: {_quote(fm[key])}" for key in keys if key in fm]
    lines.append("---")
    return "\n".join(lines) + "\n"


def write_front_matter(path: Path, fm: dict, body: str) -> None:
    path.write_text(dump_front_matter(fm) + "\n" + body, encoding="utf-8", newline="\n")


def tid_sort_key(tid: str) -> int:
    match = TID_RE.fullmatch(tid)
    if not match:
        raise TaskDataError(f"tid 非法：{tid!r}")
    return int(match.group(1))


def parse_tid_list(value: str, *, field: str, allow_empty: bool = True) -> list[str]:
    """解析 front matter/严格 CLI 使用的逗号分隔规范 tid。"""
    if value == "" and allow_empty:
        return []
    items = [item.strip() for item in value.split(",")]
    if not items or any(not item for item in items):
        raise TaskDataError(f"{field} 格式非法：{value!r}（须为逗号分隔 tid）")
    invalid = [item for item in items if not TID_RE.fullmatch(item)]
    if invalid:
        raise TaskDataError(f"{field} 含非法 tid：{', '.join(invalid)}")
    return sorted(set(items), key=tid_sort_key)


def dump_tid_list(tids) -> str:
    return ",".join(sorted(set(tids), key=tid_sort_key))


def validate_tid_references(
    tids: list[str],
    *,
    field: str,
    owner_tid: str,
    tasks_by_tid: dict[str, dict],
) -> None:
    if owner_tid in tids:
        raise TaskDataError(f"{field} 不可引用自身 {owner_tid}")
    missing = [tid for tid in tids if tid not in tasks_by_tid]
    if missing:
        raise TaskDataError(f"{field} 引用不存在 task：{', '.join(missing)}")


def task_schedule_references(target_tid: str, tasks: list[dict] | None = None) -> list[str]:
    """返回非归档 task 中引用 target_tid 的字段，供 drop/purge 拒绝悬空边。

    只扫活跃目录：归档 task 的历史边无脚本清理途径（edit 拒绝归档、归档只准新增），
    若计入会永久锁死被引用 task 的 drop。
    """
    references = []
    for task in scan_tasks() if tasks is None else tasks:
        if task["tid"] == target_tid:
            continue
        if task["status"] in ARCHIVED_STATUSES:
            continue
        for field in ("depends_on", "conflicts_with"):
            tids = parse_tid_list(task.get(field, ""), field=f"{task['tid']}.{field}")
            if target_tid in tids:
                references.append(f"{task['tid']}.{field}")
    return references


def parse_unverified_contracts(spec_text: str) -> dict[str, list[str]]:
    """分类「未知契约清单」直接列表项中的未核实标记。"""
    entries = []
    collecting = False
    fence_marker = None

    for line in spec_text.splitlines():
        if fence_marker is not None:
            fence = FENCE_CLOSE_RE.match(line)
            if (
                fence
                and fence.group(1)[0] == fence_marker[0]
                and len(fence.group(1)) >= fence_marker[1]
            ):
                fence_marker = None
            continue

        fence = FENCE_RE.match(line)
        if fence:
            fence_marker = (fence.group(1)[0], len(fence.group(1)))
            continue

        heading = H3_RE.fullmatch(line)
        if heading:
            if collecting:
                break
            collecting = heading.group(1).strip() == UNKNOWN_CONTRACT_HEADING
            continue

        if collecting:
            item = LIST_ITEM_RE.match(line)
            if item:
                entries.append(item.group(1).strip())

    classified = {"blocking": [], "spike": [], "ambiguous": []}
    for entry in entries:
        kinds = {marker.group(1) for marker in UNVERIFIED_RE.finditer(entry)}
        if "BLOCKING" in kinds:
            classified["blocking"].append(entry)
        if "SPIKE" in kinds:
            classified["spike"].append(entry)
        if None in kinds:
            classified["ambiguous"].append(entry)
    return classified


def unverified_contract_gate(
    spec_text: str,
    *,
    require_verified: bool = False,
) -> tuple[list[str], list[str]]:
    """返回未知契约的 (阻塞项, 警告项)。"""
    contracts = parse_unverified_contracts(spec_text)
    problems, warnings = [], []

    if contracts["ambiguous"]:
        problems.append(
            f"未知契约清单有 {len(contracts['ambiguous'])} 项裸 UNVERIFIED；"
            "须明确改为 UNVERIFIED-BLOCKING 或 UNVERIFIED-SPIKE"
        )
    if contracts["blocking"]:
        problems.append(
            f"未知契约清单有 {len(contracts['blocking'])} 项 UNVERIFIED-BLOCKING；"
            "须由用户或外部环境核实并改写结论"
        )
    if contracts["spike"]:
        message = (
            f"未知契约清单有 {len(contracts['spike'])} 项 UNVERIFIED-SPIKE；"
            "须完成实验并替换为验证结论"
        )
        if require_verified:
            problems.append(message)
        else:
            warnings.append(f"{message}；当前仅可执行 Step 1")

    return problems, warnings


def _visible_markdown_lines(text: str) -> list[str]:
    """返回 fenced code 外的 Markdown 行。"""
    lines = []
    fence_marker = None
    for line in text.splitlines():
        if fence_marker is not None:
            fence = FENCE_CLOSE_RE.match(line)
            if (
                fence
                and fence.group(1)[0] == fence_marker[0]
                and len(fence.group(1)) >= fence_marker[1]
            ):
                fence_marker = None
            continue
        fence = FENCE_RE.match(line)
        if fence:
            fence_marker = (fence.group(1)[0], len(fence.group(1)))
            continue
        lines.append(line)
    return lines


INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def _strip_inline_code(text: str) -> str:
    """剥除 inline code 片段（一对反引号包裹的内容）。

    占位符扫描专用：真实 spec 里的 API 契约 `{ code:"未登录" }`、JSX
    `{author.name + " 头像"}` 常包在 inline code 内，剥除后不再误判为模板占位符。
    """
    return INLINE_CODE_RE.sub("", text)


def _markdown_headings(text: str) -> list[tuple[int, str]]:
    headings = []
    for line in _visible_markdown_lines(text):
        match = HEADING_RE.fullmatch(line)
        if match:
            headings.append((len(match.group(1)), match.group(2).strip()))
    return headings


def _missing_heading_sequence(
    text: str,
    required: tuple[tuple[int, str], ...],
) -> list[str]:
    """返回缺失、错层级或乱序的必需标题。"""
    headings = _markdown_headings(text)
    position = 0
    missing = []
    for expected in required:
        try:
            position = headings.index(expected, position) + 1
        except ValueError:
            missing.append(f"{'#' * expected[0]} {expected[1]}")
    return missing


def _extract_markdown_section(text: str, level: int, title: str) -> str | None:
    """提取指定标题到下一个同级或更高层级标题之间的正文。"""
    lines = text.splitlines()
    start = None
    fence_marker = None
    for index, line in enumerate(lines):
        if fence_marker is not None:
            fence = FENCE_CLOSE_RE.match(line)
            if (
                fence
                and fence.group(1)[0] == fence_marker[0]
                and len(fence.group(1)) >= fence_marker[1]
            ):
                fence_marker = None
            continue
        fence = FENCE_RE.match(line)
        if fence:
            fence_marker = (fence.group(1)[0], len(fence.group(1)))
            continue
        heading = HEADING_RE.fullmatch(line)
        if not heading:
            continue
        heading_level = len(heading.group(1))
        heading_title = heading.group(2).strip()
        if start is None:
            if heading_level == level and heading_title == title:
                start = index + 1
        elif heading_level <= level:
            return "\n".join(lines[start:index])
    if start is None:
        return None
    return "\n".join(lines[start:])


def validate_task_documents(
    spec_text: str,
    task_body: str,
    *,
    require_verified: bool = False,
    allow_template_placeholders: bool = False,
) -> tuple[list[str], list[str]]:
    """校验 task 创建骨架与未知契约门禁，返回 (阻塞项, 警告项)。"""
    problems, warnings = [], []

    missing_spec_headings = _missing_heading_sequence(spec_text, SPEC_REQUIRED_HEADINGS)
    if missing_spec_headings:
        problems.append(
            "spec.md 缺必需标题、标题层级错误或顺序错误："
            + "、".join(missing_spec_headings)
        )

    visible_spec_lines = {line.strip() for line in _visible_markdown_lines(spec_text)}
    missing_lines = [line for line in SPEC_REQUIRED_LINES if line not in visible_spec_lines]
    if missing_lines:
        problems.append(
            "spec.md 缺模板固定声明或引导语（需原样复制自模板，勿手抄）：\n  - "
            + "\n  - ".join(missing_lines)
        )

    acceptance = _extract_markdown_section(spec_text, 3, "验收标准")
    if not acceptance or not re.search(
        r"^\s*-\s*\[ \]\s*\S", acceptance, re.MULTILINE
    ):
        problems.append("spec.md 验收标准为空")

    missing_task_headings = _missing_heading_sequence(task_body, TASK_REQUIRED_HEADINGS)
    if missing_task_headings:
        problems.append(
            "task.md 缺必需标题、标题层级错误或顺序错误："
            + "、".join(missing_task_headings)
        )

    visible_task_lines = {line.strip() for line in _visible_markdown_lines(task_body)}
    missing_guidance = [
        line for line in IMPLEMENTATION_NOTE_GUIDANCE if line not in visible_task_lines
    ]
    if missing_guidance:
        problems.append("task.md 实施笔记缺模板固定说明")

    notes = _extract_markdown_section(task_body, 2, "实施笔记")
    if notes is not None:
        payload_lines = [
            line.strip()
            for line in _visible_markdown_lines(notes)
            if line.strip() and line.strip() not in IMPLEMENTATION_NOTE_GUIDANCE
        ]
        if not payload_lines:
            problems.append("task.md 实施笔记为空；无事项时写「无」")

    if not allow_template_placeholders:
        visible_text = "\n".join(_visible_markdown_lines(spec_text + "\n" + task_body))
        placeholders = TEMPLATE_PLACEHOLDER_RE.findall(_strip_inline_code(visible_text))
        if placeholders:
            problems.append(
                f"spec.md / task.md 残留 {len(placeholders)} 个模板占位符："
                + "、".join(sorted(set(placeholders))[:3])
            )

    contract_problems, contract_warnings = unverified_contract_gate(
        spec_text,
        require_verified=require_verified,
    )
    problems.extend(contract_problems)
    warnings.extend(contract_warnings)
    return problems, warnings


# --------------------------------------------------------------------------
# 扫描与派生索引
# --------------------------------------------------------------------------

def _task_record(fm: dict, *, directory: str, source: str, archived: bool) -> dict:
    tid = fm.get("tid", "")
    if not TID_RE.match(tid):
        raise TaskDataError(f"{source}: front matter tid 非法（{tid!r}）")
    expected = f"{tid}_{fm.get('slug', '')}"
    if Path(directory).name != expected:
        raise TaskDataError(f"{directory}: 目录名与 front matter 不符（应为 {expected}）")
    status = fm.get("status", "")
    if status not in VALID_STATUSES:
        raise TaskDataError(f"{source}: status 非法（{status!r}）")
    if archived != (status in ARCHIVED_STATUSES):
        raise TaskDataError(
            f"{source}: status={status} 与所在目录不符"
            f"（位于{'归档' if archived else '活跃'}目录）"
        )
    record = {k: fm.get(k, "") for k in FRONT_MATTER_KEYS}
    record["dir"] = directory
    return record


def _validate_task_records(tasks: list[dict]) -> list[dict]:
    dup = [tid for tid, n in Counter(t["tid"] for t in tasks).items() if n > 1]
    if dup:
        raise TaskDataError(f"重复 tid：{sorted(dup)}")
    tasks.sort(key=lambda t: int(TID_RE.match(t["tid"]).group(1)))
    return tasks


def _scan_tasks_in_directories(tasks_dir: Path, archive_dir: Path) -> list[dict]:
    tasks = []
    for base, archived, rel_base in (
        (tasks_dir, False, "docs/tasks"),
        (archive_dir, True, "docs/archive/tasks"),
    ):
        if not base.is_dir():
            continue
        for directory in sorted(base.iterdir()):
            if not directory.is_dir() or (not archived and directory.name == TEMPLATE_DIR.name):
                continue
            task_md = directory / "task.md"
            if not task_md.is_file():
                raise TaskDataError(f"{task_md.parent}: 缺 task.md")
            fm, _ = parse_front_matter_text(
                task_md.read_text(encoding="utf-8"), source=str(task_md)
            )
            tasks.append(_task_record(
                fm,
                directory=f"{rel_base}/{directory.name}",
                source=str(task_md),
                archived=archived,
            ))
    return _validate_task_records(tasks)


def scan_tasks() -> list[dict]:
    """扫描当前工作区 task，按 tid 升序返回状态记录。"""
    return _scan_tasks_in_directories(TASKS_DIR, ARCHIVE_TASKS_DIR)


def scan_tasks_in_worktree(root: Path) -> list[dict]:
    """扫描另一登记 worktree 的 task 状态，不修改 task.py 全局路径。"""
    return _scan_tasks_in_directories(
        root / "docs" / "tasks",
        root / "docs" / "archive" / "tasks",
    )


def git_text_at_ref(ref: str, path: str) -> str:
    r = _git(["show", f"{ref}:{path}"])
    if r.returncode != 0:
        raise TaskDataError(f"{ref}:{path} 不存在或无法读取")
    return r.stdout


def scan_tasks_at_ref(ref: str) -> list[dict]:
    """扫描指定 commit/ref 中的 task，不签出、不写工作区。

    与 scan_tasks() 一致：只枚举 docs/tasks 与 docs/archive/tasks 下一级目录，
    每个目录必须存在根 task.md；嵌套 task.md 忽略，缺根 task.md 报数据损坏。
    """
    r = _git([
        "ls-tree", "-r", "--name-only", ref, "--",
        "docs/tasks", "docs/archive/tasks",
    ])
    if r.returncode != 0:
        raise TaskDataError(f"无法读取 ref {ref!r}：{r.stderr.strip()}")
    dirs: dict[str, bool] = {}
    for path in sorted(r.stdout.splitlines()):
        if path.startswith("docs/tasks/"):
            archived = False
            rest = path[len("docs/tasks/"):]
        elif path.startswith("docs/archive/tasks/"):
            archived = True
            rest = path[len("docs/archive/tasks/"):]
        else:
            continue
        if "/" in rest:
            dirs[rest.split("/", 1)[0]] = archived
    tasks = []
    for name, archived in sorted(dirs.items()):
        if not archived and name == TEMPLATE_DIR.name:
            continue
        directory = (
            f"docs/archive/tasks/{name}" if archived else f"docs/tasks/{name}"
        )
        path = f"{directory}/task.md"
        try:
            text = git_text_at_ref(ref, path)
        except TaskDataError:
            raise TaskDataError(f"{ref}:{directory}: 缺 task.md") from None
        fm, _ = parse_front_matter_text(text, source=f"{ref}:{path}")
        tasks.append(_task_record(
            fm,
            directory=directory,
            source=f"{ref}:{path}",
            archived=archived,
        ))
    return _validate_task_records(tasks)


def rebuild_index(tasks: list[dict] | None = None) -> list[dict]:
    """把扫描结果写入两个派生缓存 JSON。"""
    tasks = scan_tasks() if tasks is None else tasks
    groups = (
        (
            ACTIVE_PATH,
            [t for t in tasks if t["status"] not in ARCHIVED_STATUSES],
            "docs/tasks/{tid}_{slug}/task.md front matter",
        ),
        (
            ARCHIVE_PATH,
            [t for t in tasks if t["status"] in ARCHIVED_STATUSES],
            "docs/archive/tasks/{tid}_{slug}/task.md front matter",
        ),
    )
    for path, rows, authority in groups:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_by": "scripts/repo_template/task.py",
            "authority": authority,
            "workspace": _rel(REPO_ROOT) or str(REPO_ROOT),
            "tasks": rows,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=4) + "\n",
            encoding="utf-8", newline="\n",
        )
    return tasks


def find_task(tid: str, tasks: list[dict] | None = None) -> dict | None:
    for t in (scan_tasks() if tasks is None else tasks):
        if t["tid"] == tid:
            return t
    return None


def _task_branch_names(tid: str) -> list[str]:
    """本地属于该 tid 的 task 分支名列表（形如 {tid}_{slug}）。"""
    r = _git(["branch", "--format=%(refname:short)", "--list", f"{tid}_*"])
    if r.returncode != 0:
        return []
    return [b.strip() for b in r.stdout.splitlines() if TASK_BRANCH_RE.fullmatch(b.strip())]


def task_effective_state(tid: str, primary_fm: dict) -> str | None:
    """main 副本为 backlog 时，探测其被 worktree/未合并分支覆盖的有效状态。

    返回覆盖证据描述；无覆盖返回 None。main 中非 backlog 状态以 main 为准，
    同样返回 None。只读，供 edit/drop 在主仓拒绝操作过期 backlog 副本。
    """
    if primary_fm.get("status") != "backlog":
        return None
    wt_rel = worktree_rel_path(tid)
    wt_path = (REPO_ROOT / wt_rel).resolve()
    registered = worktree_paths().get(str(wt_path))
    if registered:
        task_dir = REPO_ROOT / "docs" / "tasks" / f"{tid}_{primary_fm.get('slug', '')}"
        wt_task = wt_path / task_dir.relative_to(REPO_ROOT) / "task.md"
        if wt_task.is_file():
            wt_fm, _ = parse_front_matter(wt_task)
            wt_status = wt_fm.get("status", "")
            if wt_status != "backlog":
                return (
                    f"登记 worktree {wt_rel} 中 status={wt_status}"
                    f"（分支 {registered!r}）"
                )
        return f"路径 {wt_rel} 登记为分支 {registered!r} 的 worktree"
    for branch in _task_branch_names(tid):
        if not has_unmerged_commits(branch):
            continue
        try:
            _, ref_fm, _ = load_task_at_ref(tid, branch)
        except TaskDataError:
            return f"本地分支 {branch!r} 未合并默认分支"
        ref_status = ref_fm.get("status", "")
        if ref_status != "backlog":
            return f"未合并分支 {branch!r} 中 status={ref_status}"
    return None


def _local_task_branches() -> list[str]:
    r = _git(["branch", "--format=%(refname:short)", "--list", "t[0-9]*_*"])
    if r.returncode != 0:
        return []
    return sorted(
        branch.strip()
        for branch in r.stdout.splitlines()
        if TASK_BRANCH_RE.fullmatch(branch.strip())
    )


def discover_effective_tasks() -> dict[str, dict]:
    """按 worktree → 未合并 task 分支 → main 发现每个 task 的有效状态。"""
    require_primary_worktree()
    effective = {task["tid"]: task for task in scan_tasks()}

    for branch in _local_task_branches():
        if not has_unmerged_commits(branch):
            continue
        owner_tid = TASK_BRANCH_RE.fullmatch(branch).group(1)
        tasks = scan_tasks_at_ref(branch)
        task = next((item for item in tasks if item["tid"] == owner_tid), None)
        if task is None:
            raise TaskDataError(f"未合并 task 分支 {branch!r} 缺自身 task {owner_tid}")
        branch_task = task
        main_task = effective.get(owner_tid)
        # rewind 保留的分支状态过时：main 已显式回 backlog 时，分支 active/blocked 不覆盖。
        # worktree 从 start 到 finish 一直存在，无登记 worktree 的 active 分支只来自 rewind。
        if (
            main_task is not None
            and main_task["status"] == "backlog"
            and branch_task["status"] in ("active", "blocked")
        ):
            continue
        effective[owner_tid] = branch_task

    primary = primary_worktree_path()
    for path_text, branch in worktree_paths().items():
        path = Path(path_text).resolve()
        if path == primary or not path.is_dir():
            continue
        match = TASK_BRANCH_RE.fullmatch(branch)
        if not match:
            continue
        owner_tid = match.group(1)
        tasks = scan_tasks_in_worktree(path)
        task = next((item for item in tasks if item["tid"] == owner_tid), None)
        if task is None:
            raise TaskDataError(f"登记 worktree {path} 缺自身 task {owner_tid}")
        effective[owner_tid] = task
    return effective


def _dependency_cycle(dependencies: dict[str, list[str]]) -> list[str] | None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(tid: str) -> list[str] | None:
        state[tid] = 1
        stack.append(tid)
        for dependency in dependencies.get(tid, []):
            if dependency not in dependencies:
                continue
            if state.get(dependency, 0) == 0:
                cycle = visit(dependency)
                if cycle:
                    return cycle
            elif state.get(dependency) == 1:
                start = stack.index(dependency)
                return stack[start:] + [dependency]
        stack.pop()
        state[tid] = 2
        return None

    for tid in sorted(dependencies, key=tid_sort_key):
        if state.get(tid, 0) == 0:
            cycle = visit(tid)
            if cycle:
                return cycle
    return None


def cmd_view(args):
    require_primary_worktree()
    try:
        tasks = discover_effective_tasks()

        dependencies: dict[str, list[str]] = {}
        conflicts: dict[str, set[str]] = {tid: set() for tid in tasks}
        for tid, task in tasks.items():
            if task["status"] in ARCHIVED_STATUSES:
                continue
            task_dependencies = parse_tid_list(
                task.get("depends_on", ""), field=f"{tid}.depends_on"
            )
            task_conflicts = parse_tid_list(
                task.get("conflicts_with", ""), field=f"{tid}.conflicts_with"
            )
            for field, references in (
                ("depends_on", task_dependencies),
                ("conflicts_with", task_conflicts),
            ):
                if tid in references:
                    raise TaskDataError(f"invalid_graph: {tid}.{field} 引用自身")
                missing = [reference for reference in references if reference not in tasks]
                if missing:
                    raise TaskDataError(
                        f"invalid_graph: {tid}.{field} 引用不存在 task "
                        f"{','.join(missing)}"
                    )
                dropped = [
                    reference for reference in references
                    if tasks[reference]["status"] == "dropped"
                ]
                if dropped:
                    raise TaskDataError(
                        f"invalid_graph: {tid}.{field} 引用 dropped task "
                        f"{','.join(dropped)}"
                    )
            dependencies[tid] = task_dependencies
            for peer in task_conflicts:
                conflicts[tid].add(peer)
                conflicts[peer].add(tid)

        cycle = _dependency_cycle(dependencies)
        if cycle:
            raise TaskDataError(
                "invalid_graph: depends_on cycle " + " -> ".join(cycle)
            )

        invalid_schedule = [
            f"{tid}={task.get('schedule_status', '')!r}"
            for tid, task in tasks.items()
            if task["status"] == "backlog"
            and task.get("schedule_status", "") not in ("", *SCHEDULE_STATUSES)
        ]
        if invalid_schedule:
            raise TaskDataError(
                "invalid_graph: schedule_status 非法 " + ",".join(invalid_schedule)
            )

        # done 分两种语义：
        # - main_done_set：已合入 main 的 done（scan_tasks 读当前工作区=main 视角），
        #   用于解依赖/解冲突——只有前置真正入 main，下游才算可跑。
        # - effective done（tasks 里 status=done）：含未合并分支的 done，仅计数展示。
        main_tasks = {task["tid"]: task for task in scan_tasks()}
        main_done_set = {
            tid for tid, task in main_tasks.items() if task["status"] == "done"
        }
        effective_done_set = {
            tid for tid, task in tasks.items() if task["status"] == "done"
        }
        unmerged_done = sorted(
            effective_done_set - main_done_set, key=tid_sort_key
        )
        done_set = main_done_set  # 调度判断用 main 视角
        dropped_set = {
            tid for tid, task in tasks.items() if task["status"] == "dropped"
        }
        active_list = sorted(
            (
                tid for tid, task in tasks.items()
                if task["status"] in ("active", "blocked")
            ),
            key=tid_sort_key,
        )
        backlog_tasks = {
            tid: task for tid, task in tasks.items()
            if task["status"] == "backlog"
        }

        # 按阻塞原因分组
        ready: list[str] = []
        waiting_deps: list[tuple[str, str]] = []  # (前置, 后继)
        blocked_conflicts: list[tuple[str, str]] = []  # (tid, active 对端)
        pending_clarify: list[str] = []
        unscheduled: list[str] = []

        active_set = set(active_list)
        for tid in sorted(backlog_tasks, key=tid_sort_key):
            task = backlog_tasks[tid]
            schedule = task.get("schedule_status", "")
            if schedule == "pending_clarification":
                pending_clarify.append(tid)
                continue
            if not schedule:
                unscheduled.append(tid)
                continue
            # scheduled：检查依赖
            missing_deps = [
                dep for dep in dependencies.get(tid, []) if dep not in done_set
            ]
            if missing_deps:
                for dep in sorted(missing_deps, key=tid_sort_key):
                    waiting_deps.append((dep, tid))
                continue
            # 检查冲突：peer 真正占资源（active/blocked/未合 main 的 done）才阻塞；
            # peer 是 backlog 时，序号小者优先，序号大者被序号小者阻塞（强制择一）。
            blocking = []
            for peer in conflicts[tid]:
                if peer in main_done_set or peer in dropped_set:
                    continue
                peer_status = tasks[peer]["status"]
                if peer_status in ("active", "blocked"):
                    blocking.append(peer)
                elif peer_status == "done":
                    # 未合 main 的 done：占住资源，阻塞
                    blocking.append(peer)
                elif peer_status == "backlog":
                    # backlog peer：序号小者优先，序号大者被阻塞
                    if tid_sort_key(peer) < tid_sort_key(tid):
                        blocking.append(peer)
            if blocking:
                for peer in sorted(blocking, key=tid_sort_key):
                    blocked_conflicts.append((tid, peer))
                continue
            ready.append(tid)

        # 下一批：ready 中互相冲突的择优（保留原 next-batch 选择逻辑）
        selected = []
        for tid in ready:
            if any(peer in conflicts[tid] for peer in selected):
                continue
            selected.append(tid)

        # 输出全景
        lines: list[str] = ["== task 全景 =="]

        lines.append("")
        lines.append(f"[运行中] active {len(active_list)}")
        if active_list:
            for tid in active_list:
                task = tasks[tid]
                peer_conflicts = sorted(conflicts[tid] & active_set, key=tid_sort_key)
                peer_conflicts += sorted(
                    (c for c in conflicts[tid] if c not in active_set
                     and tasks[c]["status"] == "backlog"),
                    key=tid_sort_key,
                )
                tag = f"  conflicts: {', '.join(peer_conflicts)}" if peer_conflicts else ""
                lines.append(f"  {tid}  {task['title']}{tag}")
        else:
            lines.append("  -")

        lines.append("")
        lines.append(f"[待运行] backlog {len(backlog_tasks)}")
        if selected:
            lines.append("")
            lines.append("  ▸ 下一批可跑")
            for tid in selected:
                lines.append(f"    {tid}  {tasks[tid]['title']}")
        if waiting_deps:
            lines.append("")
            lines.append("  ▸ 被依赖阻塞")
            for dep, tid in waiting_deps:
                lines.append(f"    {dep} → {tid}")
        if blocked_conflicts:
            lines.append("")
            lines.append("  ▸ 被 active 冲突阻塞")
            for tid, peer in blocked_conflicts:
                lines.append(
                    f"    {tid} ↔ {peer}  — {tid}: {tasks[tid]['title']}"
                )
        if pending_clarify:
            lines.append("")
            lines.append("  ▸ 调度未就绪")
            for tid in pending_clarify:
                lines.append(f"    {tid}  schedule_status=pending_clarification")
        if unscheduled:
            lines.append("")
            lines.append("  ▸ 未排程")
            for tid in unscheduled:
                lines.append(f"    {tid}  {tasks[tid]['title']}")

        lines.append("")
        lines.append(
            f"[已结束] done={len(main_done_set)}  dropped={len(dropped_set)}"
        )
        if unmerged_done:
            lines.append(
                f"  （{len(unmerged_done)} 个 done 在未合并分支，未入 main："
                + " ".join(unmerged_done)
                + "）"
            )

        print("\n".join(lines))
    except TaskDataError as error:
        message = str(error)
        if not message.startswith(("invalid_graph:", "invalid_done:")):
            message = f"invalid_graph: {message}"
        sys.exit(f"view=FAIL：{message}")


def load_task(tid: str) -> tuple[dict, Path, dict, str]:
    """返回 (索引记录, task.md 路径, front matter, 正文)。"""
    task = find_task(tid)
    if not task:
        sys.exit(
            f"{tid} 不存在于当前工作区（{_rel(REPO_ROOT) or REPO_ROOT.name}）。"
            "进行中 task 的文档随其 worktree，请在对应 worktree 内执行"
        )
    path = REPO_ROOT / task["dir"] / "task.md"
    fm, body = parse_front_matter(path)
    return task, path, fm, body


def load_task_at_ref(tid: str, ref: str) -> tuple[dict, dict, str]:
    """返回指定 ref 中的 (索引记录, front matter, 正文)。"""
    task = find_task(tid, scan_tasks_at_ref(ref))
    if not task:
        raise TaskDataError(f"{tid} 不存在于 ref {ref!r}")
    path = f"{task['dir']}/task.md"
    fm, body = parse_front_matter_text(
        git_text_at_ref(ref, path),
        source=f"{ref}:{path}",
    )
    return task, fm, body


def require_status(fm: dict, *allowed: str) -> None:
    if fm["status"] not in allowed:
        sys.exit(f"{fm['tid']} status={fm['status']}，需要 {allowed}")


def append_note(fm: dict, text: str) -> None:
    fm["note"] = f"{fm['note']}; {text}" if fm.get("note") else text


def append_audit(action: str, *, tid: str, fr: str, to: str, reason: str,
                 slug: str = "", title: str = "") -> None:
    """append 一行审计。字段内的 `|` 与换行会被替换，保证行格式可 grep。"""
    def clean(value: str) -> str:
        return re.sub(r"\s+", " ", str(value).replace("|", "/")).strip()

    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(TZ_CN).isoformat(timespec="seconds")
    parts = [ts, action, f"tid={tid}", f"from={fr}", f"to={to}", f"head={_get_head_short()}"]
    if slug:
        parts.append(f"slug={clean(slug)}")
    if title:
        parts.append(f"title={clean(title)}")
    parts.append(f"reason={clean(reason)}")
    with AUDIT_PATH.open("a", encoding="utf-8") as f:
        f.write(" | ".join(parts) + "\n")


# --------------------------------------------------------------------------
# worktree
# --------------------------------------------------------------------------

def worktree_rel_path(tid: str) -> str:
    return f"../{REPO_ROOT.name}_{tid}"


def effective_worktree(fm: dict) -> str:
    """worktree 相对路径：front matter 优先；主仓副本不含该字段（start 后不再回写主仓），
    此时按命名约定推导。路径不存在或未登记时由 remove_worktree 安全放过。"""
    return fm.get("worktree") or worktree_rel_path(fm["tid"])


def link_local_env(worktree: Path) -> list[str]:
    """把主仓未入库的 .env 软链进 worktree（同相对路径）。"""
    linked = []
    for src in sorted(REPO_ROOT.glob(".env")) + sorted(REPO_ROOT.glob("*/.env")):
        if not src.is_file():
            continue
        rel = src.relative_to(REPO_ROOT)
        dst = worktree / rel
        if dst.exists() or dst.is_symlink():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.symlink_to(os.path.relpath(src, dst.parent))
        linked.append(str(rel))
    return linked


def is_managed_env_link(worktree: Path, link: Path) -> bool:
    """仅识别 link_local_env() 为主仓对应 .env 创建的软链。"""
    if not link.is_symlink():
        return False
    try:
        rel = link.relative_to(worktree)
    except ValueError:
        return False
    if rel.name != ".env" or len(rel.parts) not in (1, 2):
        return False
    source = REPO_ROOT / rel
    expected = os.path.relpath(source, link.parent)
    try:
        target = os.readlink(link)
    except OSError:
        return False
    return (
        target == expected
        and link.resolve(strict=False) == source.resolve(strict=False)
    )


def unlink_managed_env_links(worktree: Path) -> None:
    for link in (worktree / ".env", *worktree.glob("*/.env")):
        if is_managed_env_link(worktree, link):
            link.unlink()


def resolve_start_base(base_arg: str | None) -> tuple[str, str]:
    """解析 start base。

    串行（task-run）链式：后一个 task 从上一个已完成 task 分支创建，传 `--base`。
    并行（task-dispatch）扇出：每个 task 从主干 HEAD 创建，不传。
    """
    primary = default_branch()
    base_branch, base_sha = resolve_local_branch(base_arg or primary)
    if base_branch == primary:
        if current_branch() != primary or _get_head() != base_sha:
            raise TaskDataError(f"主工作区 HEAD 与本地 {primary!r} 不一致")
        return base_branch, base_sha

    match = TASK_BRANCH_RE.fullmatch(base_branch)
    if not match:
        raise TaskDataError(
            f"--base 只接受默认分支或本地 task 分支（收到 {base_branch!r}）"
        )
    previous_tid = match.group(1)
    _, previous_fm, _ = load_task_at_ref(previous_tid, base_sha)
    expected_branch = f"{previous_tid}_{previous_fm.get('slug', '')}"
    if base_branch != expected_branch:
        raise TaskDataError(
            f"--base 分支名 {base_branch!r} 与 {previous_tid} slug 不符"
            f"（应为 {expected_branch!r}）；拒绝伪装成 task 分支的普通分支"
        )
    if previous_fm.get("status") not in ARCHIVED_STATUSES:
        raise TaskDataError(
            f"--base {base_branch!r} 对应 {previous_tid} status="
            f"{previous_fm.get('status')!r}，须先完成或 drop"
        )
    registered = [path for path, branch in worktree_paths().items() if branch == base_branch]
    if registered:
        raise TaskDataError(
            f"--base {base_branch!r} 仍登记 worktree：{', '.join(registered)}；"
            "先 cleanup-worktree"
        )
    return base_branch, base_sha


def create_worktree(tid: str, branch: str, base_sha: str) -> str:
    """从固定 base SHA 创建全新的 task branch/worktree；调用方负责补偿失败。"""
    rel = worktree_rel_path(tid)
    path = (REPO_ROOT / rel).resolve()
    if path.exists() or str(path) in worktree_paths():
        raise TaskDataError(f"{rel} 已存在；请先清理后再 start")
    if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode == 0:
        raise TaskDataError(f"分支 {branch!r} 已存在；请先处理后再 start")
    r = _git(["worktree", "add", "-b", branch, str(path), base_sha])
    if r.returncode != 0:
        raise TaskDataError(f"git worktree add 失败：{r.stderr.strip()}")
    return rel


def rollback_start(
    *,
    base_sha: str,
    branch: str,
    worktree_rel: str,
) -> str | None:
    """补偿尚未成功返回的 start；不修改主仓 HEAD 或工作区。

    只清理本次调用可确认归属的资源：worktree 登记分支须等于本次 branch，
    分支 HEAD 须仍等于 base_sha。并发 start 撞车时，他方创建的资源不满足
    上述条件，原样保留并报告，由用户裁决。
    """
    failures = []
    worktree = (REPO_ROOT / worktree_rel).resolve()
    registered_branch = worktree_paths().get(str(worktree))
    if registered_branch == branch:
        if worktree.exists():
            unlink_managed_env_links(worktree)
        r = _git(["worktree", "remove", "--force", str(worktree)])
        if r.returncode != 0:
            failures.append(
                f"强制移除 worktree {worktree_rel} 失败：{r.stderr.strip()}"
            )
    elif registered_branch:
        failures.append(
            f"worktree {worktree_rel} 登记分支为 {registered_branch!r}，"
            f"非本次分支 {branch!r}；可能与其他 start 并发冲突，未移除"
        )
    elif worktree.exists():
        failures.append(f"路径 {worktree_rel} 存在但未登记本次分支，未自动删除未知内容")
    _git(["worktree", "prune"])

    branch_ref = f"refs/heads/{branch}"
    if _git(["rev-parse", "--verify", "--quiet", branch_ref]).returncode == 0:
        r = _git(["rev-parse", branch_ref])
        branch_head = r.stdout.strip() if r.returncode == 0 else ""
        if branch_head != base_sha:
            failures.append(
                f"分支 {branch!r} 已从 base 前进到 {branch_head or 'unknown'}，未自动删除"
            )
        else:
            r = _git(["branch", "-D", branch])
            if r.returncode != 0:
                failures.append(f"删除分支 {branch!r} 失败：{r.stderr.strip()}")

    if worktree.exists():
        failures.append(f"路径 {worktree_rel} 仍存在，未自动删除未知内容")
    return "; ".join(failures) or None


def discard_worktree(rel: str) -> tuple[bool, str]:
    """显式撤回时强制丢弃 task worktree；调用方必须先取得用户确认。"""
    path = (REPO_ROOT / rel).resolve()
    if str(path) not in worktree_paths():
        _git(["worktree", "prune"])
        return True, f"worktree 不在登记表，已 prune：{rel}"
    if Path.cwd().resolve().is_relative_to(path):
        return False, f"当前目录在 {rel} 内，无法移除；请 cd 回主仓"
    unlink_managed_env_links(path)
    r = _git(["worktree", "remove", "--force", str(path)])
    if r.returncode != 0:
        return False, f"git worktree remove --force 失败（{r.stderr.strip()}）"
    _git(["worktree", "prune"])
    return True, f"worktree 已强制移除：{rel}"


def remove_worktree(rel: str) -> tuple[bool, str]:
    """返回 (是否已确实移除, 说明)。失败不代表流程必须中断，由调用方决定。"""
    if not rel:
        return True, "无 worktree"
    path = (REPO_ROOT / rel).resolve()
    if str(path) not in worktree_paths():
        _git(["worktree", "prune"])
        return True, f"worktree 不在登记表，已 prune：{rel}"
    if Path.cwd().resolve().is_relative_to(path):
        return False, f"当前目录在 {rel} 内，无法移除；请 cd 出去后执行 git worktree remove {rel}"
    unlink_managed_env_links(path)
    r = _git(["worktree", "remove", str(path)])
    if r.returncode != 0:
        return False, f"git worktree remove 失败（{r.stderr.strip()}）；请手动处理 {rel}"
    _git(["worktree", "prune"])
    return True, f"worktree 已移除：{rel}"


# --------------------------------------------------------------------------
# 命令
# --------------------------------------------------------------------------

def cmd_add(args):
    require_primary_worktree()
    if not SLUG_RE.match(args.slug):
        sys.exit(f"slug 须匹配 {SLUG_RE.pattern}（收到 {args.slug!r}）")
    if not args.title.strip():
        sys.exit("title 不能为空")
    tasks = scan_tasks()
    for t in tasks:
        if t["slug"] == args.slug:
            sys.exit(f"slug 已存在：{args.slug}（{t['tid']}）")
    n = max((int(TID_RE.match(t["tid"]).group(1)) for t in tasks), default=0) + 1
    tid = f"t{n:03d}"
    task_dir = TASKS_DIR / f"{tid}_{args.slug}"
    if task_dir.exists():
        sys.exit(f"{_rel(task_dir)} 已存在；请提示用户处理")
    if not TEMPLATE_DIR.is_dir():
        sys.exit(f"缺模板目录 {_rel(TEMPLATE_DIR)}")
    template_spec = TEMPLATE_DIR / "spec.md"
    template_task = TEMPLATE_DIR / "task.md"
    if not template_spec.is_file() or not template_task.is_file():
        sys.exit(f"模板目录 {_rel(TEMPLATE_DIR)} 缺 spec.md 或 task.md")
    _, template_task_body = parse_front_matter(template_task)
    template_problems, _ = validate_task_documents(
        template_spec.read_text(encoding="utf-8"),
        template_task_body,
        allow_template_placeholders=True,
    )
    if template_problems:
        sys.exit("模板结构校验失败：" + "；".join(template_problems))

    shutil.copytree(TEMPLATE_DIR, task_dir)
    task_md = task_dir / "task.md"
    fm, body = parse_front_matter(task_md)
    fm.update({
        "tid": tid,
        "slug": args.slug,
        "title": args.title.strip(),
        "status": "backlog",
        "branch": "",
        "worktree": "",
        "review_level": args.review_level,
        "diff_anchor": "",
        "depends_on": "",
        "conflicts_with": "",
        "note": args.note or "",
    })
    fm.pop("schedule_status", None)
    write_front_matter(task_md, fm, body)
    rebuild_index()
    print(f"added {tid} '{fm['title']}' status=backlog review_level={fm['review_level']}")
    print(f"工作区：{_rel(task_dir)}（已从模板复制 spec.md / task.md）")


def cmd_edit(args):
    require_primary_worktree()
    field_names = (
        "title", "note", "note_append", "review_level", "depends_on",
        "depends_append", "depends_remove", "conflicts_with",
        "conflicts_append", "conflicts_remove", "schedule_status",
    )
    values = {name: getattr(args, name, None) for name in field_names}
    if all(value is None for value in values.values()):
        sys.exit(
            "没有要改的字段；传 --title / --note / --note-append / --review-level / "
            "--depends-* / --conflicts-* / --schedule-status"
        )
    if values["note"] is not None and values["note_append"] is not None:
        sys.exit("--note 与 --note-append 互斥")
    dependency_actions = [
        values["depends_on"], values["depends_append"], values["depends_remove"]
    ]
    conflict_actions = [
        values["conflicts_with"], values["conflicts_append"], values["conflicts_remove"]
    ]
    if sum(value is not None for value in dependency_actions) > 1:
        sys.exit("--depends-on / --depends-append / --depends-remove 互斥")
    if sum(value is not None for value in conflict_actions) > 1:
        sys.exit("--conflicts-with / --conflicts-append / --conflicts-remove 互斥")

    tasks = scan_tasks()
    tasks_by_tid = {task["tid"]: task for task in tasks}
    task, path, fm, body = load_task(args.tid)
    if fm["status"] in ARCHIVED_STATUSES:
        sys.exit(f"{args.tid} 已归档（{fm['status']}），不可编辑")
    if fm["status"] != "backlog":
        sys.exit(
            f"{args.tid} status={fm['status']}；edit 只改 main 中未进入链的 backlog，"
            "active/blocked 请在自身 worktree 内编辑"
        )
    covered = task_effective_state(args.tid, fm)
    if covered:
        sys.exit(
            f"{args.tid} 在 main 中为 backlog，但{covered}；"
            "main 副本已滞后，edit 拒绝操作过期状态"
        )

    changed = []
    peer_updates: dict[Path, tuple[dict, str]] = {}
    if values["title"] is not None:
        title = values["title"].strip()
        if not title:
            sys.exit("title 不能为空")
        fm["title"] = title
        changed.append(f"title={title!r}")
    if values["note"] is not None:
        fm["note"] = values["note"]
        changed.append(f"note={values['note']!r}")
    if values["note_append"] is not None:
        if not values["note_append"].strip():
            sys.exit("--note-append 不能为空")
        append_note(fm, values["note_append"])
        changed.append(f"note+={values['note_append']!r}")
    if values["review_level"] is not None:
        fm["review_level"] = values["review_level"]
        changed.append(f"review_level={values['review_level']}")

    if any(value is not None for value in dependency_actions):
        current_dependencies = parse_tid_list(
            fm.get("depends_on", ""), field=f"{args.tid}.depends_on"
        )
        dependencies = list(current_dependencies)
        if values["depends_on"] is not None:
            dependencies = parse_tid_list(values["depends_on"], field="--depends-on")
        elif values["depends_append"] is not None:
            append_tid = parse_tid_list(
                values["depends_append"], field="--depends-append", allow_empty=False
            )
            if len(append_tid) != 1:
                sys.exit("--depends-append 只接受一个 tid")
            dependencies = sorted(set(dependencies + append_tid), key=tid_sort_key)
        elif values["depends_remove"] is not None:
            remove_tid = parse_tid_list(
                values["depends_remove"], field="--depends-remove", allow_empty=False
            )
            if len(remove_tid) != 1:
                sys.exit("--depends-remove 只接受一个 tid")
            if remove_tid[0] not in dependencies:
                sys.exit(f"{args.tid}.depends_on 不含 {remove_tid[0]}")
            dependencies.remove(remove_tid[0])
        validate_tid_references(
            dependencies,
            field="depends_on",
            owner_tid=args.tid,
            tasks_by_tid=tasks_by_tid,
        )
        dropped_dependencies = [
            tid for tid in dependencies if tasks_by_tid[tid]["status"] == "dropped"
        ]
        if dropped_dependencies:
            sys.exit(f"depends_on 不可引用 dropped task：{', '.join(dropped_dependencies)}")
        candidate_dependencies = {
            candidate["tid"]: parse_tid_list(
                candidate.get("depends_on", ""),
                field=f"{candidate['tid']}.depends_on",
            )
            for candidate in tasks
            if candidate["status"] not in ARCHIVED_STATUSES
        }
        candidate_dependencies[args.tid] = list(dependencies)
        cycle = _dependency_cycle(candidate_dependencies)
        if cycle:
            sys.exit(f"depends_on 变更会形成依赖环：{' -> '.join(cycle)}")
        fm["depends_on"] = dump_tid_list(dependencies)
        changed.append(f"depends_on={fm['depends_on']!r}")

    if any(value is not None for value in conflict_actions):
        current_conflicts = parse_tid_list(
            fm.get("conflicts_with", ""), field=f"{args.tid}.conflicts_with"
        )
        conflicts = list(current_conflicts)
        if values["conflicts_with"] is not None:
            conflicts = parse_tid_list(values["conflicts_with"], field="--conflicts-with")
        elif values["conflicts_append"] is not None:
            append_tid = parse_tid_list(
                values["conflicts_append"], field="--conflicts-append", allow_empty=False
            )
            if len(append_tid) != 1:
                sys.exit("--conflicts-append 只接受一个 tid")
            conflicts = sorted(set(conflicts + append_tid), key=tid_sort_key)
        elif values["conflicts_remove"] is not None:
            remove_tid = parse_tid_list(
                values["conflicts_remove"], field="--conflicts-remove", allow_empty=False
            )
            if len(remove_tid) != 1:
                sys.exit("--conflicts-remove 只接受一个 tid")
            if remove_tid[0] not in conflicts:
                sys.exit(f"{args.tid}.conflicts_with 不含 {remove_tid[0]}")
            conflicts.remove(remove_tid[0])
        validate_tid_references(
            conflicts,
            field="conflicts_with",
            owner_tid=args.tid,
            tasks_by_tid=tasks_by_tid,
        )
        dropped_conflicts = [
            tid for tid in conflicts if tasks_by_tid[tid]["status"] == "dropped"
        ]
        if dropped_conflicts:
            sys.exit(f"conflicts_with 不可引用 dropped task：{', '.join(dropped_conflicts)}")

        affected = sorted(set(current_conflicts) | set(conflicts), key=tid_sort_key)
        for peer_tid in affected:
            peer_task = tasks_by_tid[peer_tid]
            # done target 已合 main 或归档，不可编辑：跳过反向边同步，
            # owner 单边增删即可；调度由 view 的 main_done_set 释放。
            if peer_task["status"] == "done":
                continue
            if peer_task["status"] != "backlog":
                sys.exit(
                    f"无法维护冲突反向边：{peer_tid} status={peer_task['status']}，"
                    "须为可编辑 backlog"
                )
            _, peer_path, peer_fm, peer_body = load_task(peer_tid)
            peer_covered = task_effective_state(peer_tid, peer_fm)
            if peer_covered:
                sys.exit(f"无法维护冲突反向边：{peer_tid} {peer_covered}")
            peer_conflicts = parse_tid_list(
                peer_fm.get("conflicts_with", ""),
                field=f"{peer_tid}.conflicts_with",
            )
            if peer_tid in conflicts:
                peer_conflicts = sorted(
                    set(peer_conflicts + [args.tid]), key=tid_sort_key
                )
            else:
                peer_conflicts = [tid for tid in peer_conflicts if tid != args.tid]
            peer_fm["conflicts_with"] = dump_tid_list(peer_conflicts)
            peer_updates[peer_path] = (peer_fm, peer_body)
        fm["conflicts_with"] = dump_tid_list(conflicts)
        changed.append(f"conflicts_with={fm['conflicts_with']!r}")

    if values["schedule_status"] is not None:
        fm["schedule_status"] = values["schedule_status"]
        changed.append(f"schedule_status={values['schedule_status']}")

    for peer_path, (peer_fm, peer_body) in peer_updates.items():
        write_front_matter(peer_path, peer_fm, peer_body)
    write_front_matter(path, fm, body)
    rebuild_index()
    print(f"{args.tid} updated: {', '.join(changed)}")


def cmd_start(args):
    require_primary_worktree()
    base_branch, base_sha = resolve_start_base(getattr(args, "base", None))
    task, ref_fm, ref_task_body = load_task_at_ref(args.tid, base_sha)
    require_status(ref_fm, "backlog")

    spec_path = f"{task['dir']}/spec.md"
    try:
        spec_text = git_text_at_ref(base_sha, spec_path)
    except TaskDataError:
        sys.exit("start=FAIL：缺 spec.md")
    problems, _ = validate_task_documents(spec_text, ref_task_body)
    if problems:
        sys.exit("start=FAIL：" + "；".join(problems))

    branch = f"{ref_fm['tid']}_{ref_fm['slug']}"
    worktree_rel = worktree_rel_path(ref_fm["tid"])
    worktree = (REPO_ROOT / worktree_rel).resolve()
    if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode == 0:
        sys.exit(f"分支 {branch!r} 已存在；请先处理后再 start")
    if worktree.exists() or str(worktree) in worktree_paths():
        sys.exit(f"{worktree_rel} 已存在；请先处理后再 start")

    try:
        rel = create_worktree(ref_fm["tid"], branch, base_sha)
        task_path = worktree / task["dir"] / "task.md"
        fm, body = parse_front_matter(task_path)
        if fm.get("status") != "backlog":
            raise TaskDataError(
                f"{task['dir']}/task.md status={fm.get('status')!r}，需要 backlog"
            )
        fm["status"] = "active"
        fm["branch"] = branch
        fm["worktree"] = worktree_rel
        fm["diff_anchor"] = base_sha
        write_front_matter(task_path, fm, body)
        linked = link_local_env(worktree)
    except (OSError, TaskDataError) as e:
        rollback_error = rollback_start(
            base_sha=base_sha,
            branch=branch,
            worktree_rel=worktree_rel,
        )
        if rollback_error:
            sys.exit(
                f"start 失败（{e}）；自动补偿不完整：{rollback_error}。"
                f"请检查 {worktree_rel}、分支 {branch!r} 与主仓 HEAD 后手动恢复"
            )
        sys.exit(f"start 失败（{e}）；已清理本次新建分支与 worktree，主仓未修改")

    print(
        f"{args.tid} status=active branch={branch} base={base_branch} "
        f"diff_anchor={fm['diff_anchor']}"
    )
    print(f"工作位置：worktree {rel}")
    if linked:
        print(f"已软链本地配置：{', '.join(linked)}")
    print(f"下一步：cd {worktree_rel} 后在该工作区执行 preflight 与后续所有步骤")


def cmd_preflight(args):
    ref_arg = args.ref
    source_ref = ""
    if ref_arg:
        source_ref, ref_sha = resolve_local_branch(ref_arg)
        task, fm, task_body = load_task_at_ref(args.tid, ref_sha)
        task_dir = None
    else:
        task, _, fm, task_body = load_task(args.tid)
        task_dir = REPO_ROOT / task["dir"]
    problems, warnings = [], []

    # 1. 状态
    allow_backlog = args.allow_backlog
    if fm["status"] in ARCHIVED_STATUSES:
        problems.append(f"status={fm['status']}，已归档不可执行")
    elif fm["status"] == "blocked":
        problems.append("status=blocked，须用户放行（加轮 resume 或 drop）后再执行")
    elif fm["status"] == "backlog" and not allow_backlog:
        problems.append("status=backlog，须先 start")
    elif fm["status"] not in ("active", "backlog"):
        problems.append(f"status={fm['status']}，不可执行")

    # 2. spec 完整与未知契约
    spec_rel = f"{task['dir']}/spec.md"
    if source_ref:
        try:
            text = git_text_at_ref(ref_sha, spec_rel)
        except TaskDataError:
            text = ""
            problems.append("缺 spec.md")
    else:
        spec = task_dir / "spec.md"
        if not spec.is_file():
            text = ""
            problems.append("缺 spec.md")
        else:
            text = spec.read_text(encoding="utf-8")
    if text:
        document_problems, document_warnings = validate_task_documents(
            text,
            task_body,
            require_verified=args.require_verified,
        )
        problems.extend(document_problems)
        warnings.extend(document_warnings)

    # 3. review 必要字段
    if fm["status"] == "active" and not fm.get("diff_anchor"):
        problems.append("diff_anchor 为空；review 无法渲染，请 rewind 后重走 start")
    if fm.get("review_level") not in REVIEW_LEVELS:
        problems.append(f"review_level={fm.get('review_level')!r} 非法，须为 {REVIEW_LEVELS}")

    # 4. 工作区一致性
    if source_ref:
        warnings.append(f"ref 快照 {source_ref}：未检查 task worktree 与当前脏改动")
    else:
        if fm["status"] == "backlog":
            covered = task_effective_state(args.tid, fm)
            if covered:
                warnings.append(
                    f"main 中为 backlog，但{covered}；"
                    "main 副本滞后，不能据此重复 start"
                )
        if fm["status"] == "active" and not in_own_task_worktree(fm):
            problems.append(
                f"当前不在 task worktree {effective_worktree(fm)} 的分支 {fm['branch']!r}"
            )

        dirty = porcelain_entries()
        foreign = [p for p in dirty
                   if not p.startswith(task["dir"])
                   and p not in ("docs/tasks_index.json", "docs/archive/tasks_index.json")
                   and not p.startswith(".scratch/")]
        if foreign:
            warnings.append(
                f"工作区有 {len(foreign)} 项与本 task 无关的改动：{', '.join(foreign[:5])}"
            )

    print(f"# preflight {args.tid}")
    if source_ref:
        print(f"  source_ref: {source_ref}")
    for line in warnings:
        print(f"  WARN : {line}")
    for line in problems:
        print(f"  FAIL : {line}")
    if problems:
        print(f"\npreflight=FAIL（{len(problems)} 项）；修复后重跑")
        sys.exit(1)
    print(f"\npreflight=PASS{f'（{len(warnings)} 条警告）' if warnings else ''}")


def cmd_block(args):
    task, path, fm, body = load_task(args.tid)
    require_status(fm, "active")
    require_own_task_worktree(fm)
    fm["status"] = "blocked"
    append_note(fm, f"blocked: {args.reason}")
    write_front_matter(path, fm, body)
    print(f"{args.tid} status=blocked reason={args.reason}")


def cmd_resume(args):
    task, path, fm, body = load_task(args.tid)
    require_status(fm, "blocked")
    require_own_task_worktree(fm)
    fm["status"] = "active"
    write_front_matter(path, fm, body)
    print(f"{args.tid} status=active (resumed)")


def _close_task(args, status: str, note: str | None) -> None:
    """done / dropped 收尾：先做 git 侧动作，再单次写盘，最后归档目录。

    单次写盘是为了避免「front matter 已写 done、目录未归档」的中间态——
    那种状态下 finish/drop/rewind 三条出口全被状态校验挡死，只能手改 front matter。
    归档移动失败时回滚 front matter，同理避免上述死锁。
    """
    task, path, fm, body = load_task(args.tid)
    if status == "done":
        require_status(fm, "active")
        require_own_task_worktree(fm)
    elif fm["status"] in ARCHIVED_STATUSES:
        sys.exit(f"{args.tid} 已是 {fm['status']}")
    elif fm["status"] in ("active", "blocked"):
        require_own_task_worktree(fm)
    else:
        require_primary_worktree()
        if status == "dropped":
            covered = task_effective_state(args.tid, fm)
            if covered:
                sys.exit(
                    f"{args.tid} 在主干中为 backlog，但{covered}；"
                    "主干副本已滞后。请到对应 worktree 执行 drop，或先合并该 task 分支"
                )

    src = REPO_ROOT / task["dir"]
    dst = ARCHIVE_TASKS_DIR / f"{fm['tid']}_{fm['slug']}"
    if dst.exists():
        sys.exit(f"归档目录已存在：{_rel(dst)}（数据冲突，请提示用户）")

    in_own_worktree = in_own_task_worktree(fm)
    if in_own_worktree:
        removed, wt_msg = False, (
            f"worktree {fm['worktree']} 待执行 commit 后从主仓 cleanup-worktree"
        )
    else:
        removed, wt_msg = remove_worktree(effective_worktree(fm))

    orig_fm = dict(fm)
    fm["status"] = status
    if note:
        append_note(fm, note)
    if in_own_worktree or removed:
        fm["worktree"] = ""
    else:
        append_note(fm, f"worktree 未移除：{effective_worktree(fm)}")
    write_front_matter(path, fm, body)

    ARCHIVE_TASKS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(src), str(dst))
    except (OSError, shutil.Error) as e:
        write_front_matter(path, orig_fm, body)
        sys.exit(
            f"归档移动失败（{e}）；front matter 已回滚为 status={orig_fm['status']}，"
            f"目录仍在 {_rel(src)}。排除原因后重试"
        )
    print(f"{args.tid} status={status}; 目录已归档 -> {_rel(dst)}; {wt_msg}")
    if not removed and not in_own_worktree:
        print("WARNING: worktree 未移除，已记入 note；请手动清理", file=sys.stderr)


def cmd_finish(args):
    _close_task(args, "done", None)


def cmd_cleanup_worktree(args):
    """从主仓清理已提交的 task worktree，保留分支。"""
    require_primary_worktree()
    if not TID_RE.fullmatch(args.tid):
        sys.exit(f"tid 非法：{args.tid!r}")
    rel = worktree_rel_path(args.tid)
    path = (REPO_ROOT / rel).resolve()
    paths = worktree_paths()
    registered_branch = paths.get(str(path))
    if registered_branch is None:
        _git(["worktree", "prune"])
        if path.exists():
            sys.exit(f"{rel} 存在但未登记为 git worktree；拒绝删除未知内容")
        print(f"worktree 已清理：{rel}（幂等）")
        return
    if not TASK_BRANCH_RE.fullmatch(registered_branch) or not registered_branch.startswith(f"{args.tid}_"):
        sys.exit(
            f"{rel} 登记分支为 {registered_branch!r}，不属于 {args.tid}；拒绝清理"
        )
    try:
        _, ref_fm, _ = load_task_at_ref(args.tid, registered_branch)
    except TaskDataError as e:
        sys.exit(f"{rel} 登记分支 {registered_branch!r} 中无法读取 {args.tid}：{e}")
    expected_branch = f"{args.tid}_{ref_fm.get('slug', '')}"
    if registered_branch != expected_branch:
        sys.exit(
            f"{rel} 登记分支 {registered_branch!r} 与 {args.tid} slug 不符"
            f"（应为 {expected_branch!r}）；拒绝清理"
        )
    ref_status = ref_fm.get("status", "")
    if ref_status not in ARCHIVED_STATUSES:
        sys.exit(
            f"{args.tid} 在分支 {registered_branch!r} 中 status={ref_status!r}，"
            f"须为 done/dropped；finish/drop 并提交后才能 cleanup-worktree"
        )
    dirty = porcelain_entries(path)
    if dirty:
        sys.exit(
            f"{rel} 有 {len(dirty)} 项未提交改动：{', '.join(dirty[:5])}；"
            "先完成 task commit"
        )
    unlink_managed_env_links(path)
    r = _git(["worktree", "remove", str(path)])
    if r.returncode != 0:
        sys.exit(f"git worktree remove 失败：{r.stderr.strip()}")
    _git(["worktree", "prune"])
    print(f"worktree 已移除：{rel}；分支 {registered_branch!r} 保留")


def _merge_in_progress() -> bool:
    """MERGE_HEAD 是否存在。git-dir 取绝对路径，避免按调用方 cwd 解析。"""
    r = _git(["rev-parse", "--absolute-git-dir"])
    if r.returncode != 0 or not r.stdout.strip():
        raise TaskDataError("无法解析 git 目录，无法判断 merge 状态")
    return (Path(r.stdout.strip()) / "MERGE_HEAD").exists()


def _conflicted_paths() -> list[str]:
    r = _git(["diff", "--name-only", "--diff-filter=U"])
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]


def _resolve_integrate_branch(tid: str) -> tuple[str, str]:
    """定位 task 分支并校验其 tip 中该 task 已终态。"""
    branches = _task_branch_names(tid)
    if not branches:
        raise TaskDataError(f"{tid} 没有本地 task 分支；无可合并内容")
    if len(branches) > 1:
        raise TaskDataError(
            f"{tid} 存在多个本地 task 分支：{', '.join(branches)}；请先处理"
        )
    branch, sha = resolve_local_branch(branches[0])
    _, ref_fm, _ = load_task_at_ref(tid, sha)
    expected = f"{tid}_{ref_fm.get('slug', '')}"
    if branch != expected:
        raise TaskDataError(
            f"分支 {branch!r} 与 {tid} slug 不符（应为 {expected!r}）"
        )
    status = ref_fm.get("status", "")
    if status not in ARCHIVED_STATUSES:
        raise TaskDataError(
            f"{tid} 在分支 {branch!r} 中 status={status!r}，须为 done/dropped"
        )
    return branch, sha


def _commit_index() -> None:
    """重建派生 index 并单独成 commit；无变化则跳过。"""
    rebuild_index()
    paths = [_rel(ACTIVE_PATH), _rel(ARCHIVE_PATH)]
    _git(["add", "--", *paths])
    if _git(["diff", "--cached", "--quiet", "--", *paths]).returncode == 0:
        print("index 无变化，跳过维护 commit")
        return
    r = _git(["commit", "-m", "chore(task): rebuild task indexes", "--", *paths])
    if r.returncode != 0:
        raise TaskDataError(f"index commit 失败：{r.stderr.strip()}")
    print(f"index 维护 commit：{_get_head_short()}")


def _resolve_chain(tail_branch: str, tail_sha: str) -> list[tuple[str, str]]:
    """收集链尾分支的祖先链中全部未合并 task 分支，按祖先顺序返回 [(branch, sha)]。

    用于串行链式（task-run）的 integrate --chain：只合链尾一次，祖先自动跟随。
    要求每个分支 tip 中对应 task 均为 done/dropped，且无 worktree 登记。
    """
    chain: list[tuple[str, str]] = []
    for branch in _local_task_branches():
        if branch == tail_branch:
            continue
        # 候选分支是链尾祖先，且未合入主干
        if _git(["merge-base", "--is-ancestor", f"refs/heads/{branch}", tail_sha]).returncode != 0:
            continue
        if _git(["merge-base", "--is-ancestor", f"refs/heads/{branch}", "HEAD"]).returncode == 0:
            continue
        _, sha = resolve_local_branch(branch)
        tid = TASK_BRANCH_RE.fullmatch(branch).group(1)
        _, ref_fm, _ = load_task_at_ref(tid, sha)
        status = ref_fm.get("status", "")
        if status not in ARCHIVED_STATUSES:
            raise TaskDataError(
                f"链上分支 {branch!r} 中 {tid} status={status!r}，须全部 done/dropped"
            )
        registered = [path for path, name in worktree_paths().items() if name == branch]
        if registered:
            raise TaskDataError(
                f"链上分支 {branch!r} 仍登记 worktree：{', '.join(registered)}；"
                "先 cleanup-worktree"
            )
        chain.append((branch, sha))
    return chain


def cmd_integrate(args):
    """把已完成 task 分支合并进主干，重建 index，删除分支。

    扇出（默认）：合单个分支。链式（--chain）：只合链尾，祖先自动跟随，删整条链分支。
    """
    require_primary_worktree()
    if not TID_RE.fullmatch(args.tid):
        sys.exit(f"tid 非法：{args.tid!r}")
    base = default_branch()

    if args.continue_merge:
        if not _merge_in_progress():
            sys.exit("当前无进行中的 merge；--continue 只用于冲突解决后继续")
        conflicted = _conflicted_paths()
        if conflicted:
            sys.exit(
                f"仍有 {len(conflicted)} 个文件未解决冲突："
                f"{', '.join(conflicted[:5])}；解决并 git add 后重试"
            )
        r = _git(["commit", "--no-edit"])
        if r.returncode != 0:
            sys.exit(f"merge commit 失败：{r.stderr.strip()}")
        print(f"merge 已完成：{_get_head_short()}")
    else:
        if _merge_in_progress():
            sys.exit("存在进行中的 merge；先用 --continue 完成或 git merge --abort")
        dirty = tracked_dirty_entries()
        if dirty:
            sys.exit(
                f"主仓有 {len(dirty)} 项已跟踪文件未提交：{', '.join(dirty[:5])}；"
                "merge 前请先提交或还原。未跟踪文件不阻塞合并"
            )

    try:
        branch, sha = _resolve_integrate_branch(args.tid)
    except TaskDataError as e:
        sys.exit(str(e))

    registered = [path for path, name in worktree_paths().items() if name == branch]
    if registered:
        sys.exit(
            f"分支 {branch!r} 仍登记 worktree：{', '.join(registered)}；"
            f"先 cleanup-worktree {args.tid}"
        )

    chain: list[tuple[str, str]] = []
    if args.chain:
        try:
            chain = _resolve_chain(branch, sha)
        except TaskDataError as e:
            sys.exit(str(e))
        if chain:
            print(f"链上共 {len(chain) + 1} 个分支（含链尾 {branch}）：")
            for b, _ in chain:
                print(f"  {b}")

    if not args.continue_merge:
        if _git(["merge-base", "--is-ancestor", sha, "HEAD"]).returncode == 0:
            print(f"{branch} 已合入 {base}，跳过 merge")
        else:
            r = _git(["merge", "--no-ff", "-m", f"merge({args.tid}): {branch}", branch])
            if r.returncode != 0:
                conflicted = _conflicted_paths()
                if conflicted:
                    print(f"merge 冲突，共 {len(conflicted)} 个文件：", file=sys.stderr)
                    for path in conflicted:
                        print(f"  {path}", file=sys.stderr)
                    sys.exit(
                        f"解决后 git add，再执行 integrate {args.tid} --continue；"
                        "放弃用 git merge --abort"
                    )
                sys.exit(f"merge 失败：{r.stderr.strip()}")
            print(f"merge 完成：{_get_head_short()}")

    try:
        _commit_index()
    except TaskDataError as e:
        sys.exit(str(e))

    if args.keep_branch:
        print(f"分支 {branch!r} 按要求保留")
        return
    to_delete = chain + [(branch, sha)]
    for b, _ in to_delete:
        if _git(["merge-base", "--is-ancestor", f"refs/heads/{b}", "HEAD"]).returncode != 0:
            sys.exit(f"分支 {b!r} 未完全合入 {base}；保留分支")
    for b, _ in to_delete:
        r = _git(["branch", "-d", "--", b])
        if r.returncode != 0:
            sys.exit(f"删除分支 {b!r} 失败：{r.stderr.strip()}；已保留")
        print(f"分支已删除：{b}")


def cmd_drop(args):
    references = task_schedule_references(args.tid)
    if references:
        sys.exit(
            f"{args.tid} 仍被调度图引用：{', '.join(references)}；"
            "先清理引用或重跑 task-schedule"
        )
    _close_task(args, "dropped", f"dropped: {args.reason}")


def cmd_rewind(args):
    task, path, fm, body = load_task(args.tid)
    primary_fm = dict(fm)
    primary_body = body
    worktree_rel = effective_worktree(fm)
    worktree = (REPO_ROOT / worktree_rel).resolve()

    # 新 start 不回写 main；从主仓 rewind 时读取登记 worktree 中的 active 状态。
    if fm["status"] == "backlog" and str(worktree) in worktree_paths():
        worktree_task = worktree / task["dir"] / "task.md"
        if worktree_task.is_file():
            worktree_fm, _ = parse_front_matter(worktree_task)
            if worktree_fm.get("status") in ("active", "blocked"):
                fm = worktree_fm

    effective = fm["status"]
    recorded = primary_fm["status"]
    if effective not in STATUS_ORDER:
        sys.exit(
            f"{args.tid} status={effective}；rewind 只处理 {STATUS_ORDER}"
            "（done/dropped 已归档不可 rewind：放弃用 drop，彻底删除用 purge）"
        )
    target = args.to or DEFAULT_REWIND.get(effective)
    if target is None:
        sys.exit(f"{args.tid} 已是 backlog，无可撤回")
    if target not in STATUS_ORDER:
        sys.exit(f"--to {target!r} 非法；须为 {STATUS_ORDER} 之一")
    if STATUS_ORDER.index(target) >= STATUS_ORDER.index(effective):
        sys.exit(f"rewind 只向后：{effective} -> {target} 不是撤回（前进用 start/block）")

    wt_msg = ""
    if target == "backlog":
        require_primary_worktree()
        branch = fm.get("branch", "")
        registered_branch = worktree_paths().get(str(worktree))
        if str(worktree) in worktree_paths():
            if branch and registered_branch and registered_branch != branch:
                sys.exit(
                    f"{worktree_rel} 登记分支为 {registered_branch!r}，"
                    f"与 {args.tid} front matter 分支 {branch!r} 不符；"
                    "拒绝强制移除其他分支的 worktree"
                )
        dirty = porcelain_entries(worktree) if worktree.is_dir() else []
        anchor = fm.get("diff_anchor", "")
        own_commits = False
        if branch and anchor:
            r = _git(["rev-list", "--count", f"{anchor}..{branch}"])
            own_commits = r.returncode != 0 or r.stdout.strip() != "0"
        if (dirty or own_commits) and not args.yes:
            details = []
            if dirty:
                details.append(f"worktree 有 {len(dirty)} 项未提交改动")
            if own_commits:
                details.append(f"分支 {branch!r} 有当前 task commit")
            print(
                f"WARNING: {'；'.join(details)}；rewind 会丢弃 worktree 改动并使未合并分支游离。\n"
                f"分支 {branch!r} 将保留。恢复方式：\n"
                f"  - 继续用旧分支：git worktree add {worktree_rel} {branch}\n"
                f"  - 或删除后重来：git branch -D {branch}\n"
                "继续？(y/N)",
                file=sys.stderr,
            )
            try:
                answer = input()
            except EOFError:
                answer = ""
            if answer.strip().lower() not in ("y", "yes"):
                sys.exit("rewind aborted by user")
        removed, wt_msg = discard_worktree(worktree_rel)
        if not removed:
            sys.exit(f"{wt_msg}\nrewind 中止：worktree 未清理时不能回到 backlog")
        if branch and not own_commits:
            _git(["branch", "-D", branch])
        fm = primary_fm
        body = primary_body
        fm["branch"] = ""
        fm["worktree"] = ""
        fm["diff_anchor"] = ""
    else:
        require_own_task_worktree(fm)

    fm["status"] = target
    if target == "backlog":
        fm["schedule_status"] = "pending_clarification"
    if effective == recorded:
        transition = f"{effective} -> {target}"
    else:
        transition = (
            f"effective={effective} -> {target}（main 记录为 {recorded}）"
        )
    append_note(fm, f"rewound: {transition}; {args.reason}")
    write_front_matter(path, fm, body)
    if target == "backlog":
        rebuild_index()
    append_audit("rewind", tid=args.tid, fr=effective, to=target, reason=args.reason)
    print(f"{args.tid} status={target} (rewound from {effective}){'; ' + wt_msg if wt_msg else ''}")


def cmd_purge(args):
    require_primary_worktree()
    references = task_schedule_references(args.tid)
    if references:
        sys.exit(
            f"{args.tid} 仍被调度图引用：{', '.join(references)}；"
            "先清理引用后再 purge"
        )
    task, path, fm, body = load_task(args.tid)
    require_status(fm, "backlog")
    task_dir = REPO_ROOT / task["dir"]
    if tracked_anywhere(task["dir"]):
        sys.exit(
            f"{args.tid} 的 task 目录已被任一分支跟踪；"
            "purge 只用于从未提交的误建，请改用 drop 归档"
        )
    if fm.get("branch") and has_unmerged_commits(fm["branch"]):
        sys.exit(f"{args.tid} 分支 {fm['branch']!r} 有未合并 commit；purge 拒绝（请用 drop）")
    append_audit(
        "purge", tid=fm["tid"], fr="backlog", to="deleted", reason=args.reason,
        slug=fm["slug"], title=fm["title"],
    )
    removed, wt_msg = remove_worktree(fm.get("worktree", ""))
    if not removed:
        sys.exit(f"{wt_msg}\npurge 中止：worktree 未清理时删除 task 目录会留下无主工作区")
    shutil.rmtree(task_dir)
    rebuild_index()
    print(f"{args.tid} purged（tid 已释放；审计见 {_rel(AUDIT_PATH)}）")


def cmd_list(args):
    if args.status and args.status not in VALID_STATUSES:
        sys.exit(f"status {args.status!r} 非法；可选 {VALID_STATUSES}")
    ref_arg = args.ref
    if ref_arg and args.rebuild:
        sys.exit("--ref 为只读快照，不能与 --rebuild 同用")
    if ref_arg:
        ref_name, ref_sha = resolve_local_branch(ref_arg)
        tasks = scan_tasks_at_ref(ref_sha)
        source = f"ref={ref_name}@{ref_sha[:12]}"
    elif args.rebuild:
        require_primary_worktree()
        tasks = rebuild_index()
        source = ""
        print(f"index rebuilt: {_rel(ACTIVE_PATH)}, {_rel(ARCHIVE_PATH)}")
    else:
        tasks = scan_tasks()  # 默认只读：罗列不该有写副作用
        source = ""
    rows = [t for t in tasks if not args.status or t["status"] == args.status]
    if not rows:
        print(f"(no tasks) {source}" if source else "(no tasks)")
        return
    if source:
        print(f"source: {source}")
    print("| tid    | title                              | status   | lvl    | branch                        | note |")
    print("|--------|------------------------------------|----------|--------|-------------------------------|------|")
    for t in rows:
        print(
            f"| {t['tid']:<6} | {t['title'][:34]:<34} | {t['status']:<8} | "
            f"{(t.get('review_level') or ''):<6} | {(t.get('branch') or '')[:29]:<29} | {(t.get('note') or '')[:40]} |"
        )


def cmd_show(args):
    ref_arg = args.ref
    if ref_arg:
        ref_name, ref_sha = resolve_local_branch(ref_arg)
        task, fm, _ = load_task_at_ref(args.tid, ref_sha)
        fields = dict(fm)
        fields["source_ref"] = f"{ref_name}@{ref_sha[:12]}"
        fields["dir"] = task["dir"]
        fields["task_md"] = f"{ref_name}:{task['dir']}/task.md"
    else:
        task, path, fm, body = load_task(args.tid)
        fields = dict(fm)
        fields["dir"] = task["dir"]
        fields["task_md"] = _rel(path)
    width = max(len(k) for k in fields)
    for k, v in fields.items():
        print(f"{k.ljust(width)}: {v}")


def main():
    p = argparse.ArgumentParser(
        description="task 状态入口（状态权威 = task.md front matter；JSON 为派生缓存）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="新增 backlog task：分配 tid、从模板建目录、写 front matter")
    a.add_argument("--title", required=True)
    a.add_argument("--slug", required=True)
    a.add_argument("--note", default="")
    a.add_argument("--review-level", choices=REVIEW_LEVELS, default=DEFAULT_REVIEW_LEVEL)
    a.set_defaults(func=cmd_add)

    e = sub.add_parser("edit", help="改 main 中未进入链的 backlog task")
    e.add_argument("tid")
    e.add_argument("--title")
    e.add_argument("--note", help="覆盖 note（传空串则清空）")
    e.add_argument("--note-append", help="在现有 note 后追加")
    e.add_argument("--review-level", choices=REVIEW_LEVELS)
    e.add_argument("--depends-on", help="逗号分隔 tid；传空串清空")
    e.add_argument("--depends-append", help="追加一个依赖 tid")
    e.add_argument("--depends-remove", help="移除一个依赖 tid")
    e.add_argument("--conflicts-with", help="逗号分隔 tid；传空串清空并同步反向边")
    e.add_argument("--conflicts-append", help="追加一个冲突 tid 并同步反向边")
    e.add_argument("--conflicts-remove", help="移除一个冲突 tid 并同步反向边")
    e.add_argument("--schedule-status", choices=SCHEDULE_STATUSES)
    e.set_defaults(func=cmd_edit)

    s = sub.add_parser(
        "start",
        help="backlog -> active：从主干或上一 task 分支建 worktree，不修改主仓",
    )
    s.add_argument("tid")
    s.add_argument(
        "--base",
        help="上一已完成 task 的本地分支（串行链式）；省略时从主干 HEAD 扇出（并行）",
    )
    s.set_defaults(func=cmd_start)

    pf = sub.add_parser("preflight", help="开干前门禁：分支/worktree/工作区/spec/索引交叉校验")
    pf.add_argument("tid")
    pf.add_argument(
        "--allow-backlog",
        action="store_true",
        help="只读检查尚未 start 的 backlog 是否具备开干条件",
    )
    pf.add_argument(
        "--ref",
        help="只读检查指定本地分支快照；不检查 worktree 与当前脏改动",
    )
    pf.add_argument(
        "--require-verified",
        action="store_true",
        help="要求未知契约清单不再含 UNVERIFIED-SPIKE（进入实现前使用）",
    )
    pf.set_defaults(func=cmd_preflight)

    b = sub.add_parser("block", help="active -> blocked")
    b.add_argument("tid")
    b.add_argument("--reason", required=True, choices=BLOCK_REASONS)
    b.set_defaults(func=cmd_block)

    r = sub.add_parser("resume", help="blocked -> active（用户加轮或排除阻塞后）")
    r.add_argument("tid")
    r.set_defaults(func=cmd_resume)

    f = sub.add_parser("finish", help="active -> done；目录归档，提交后从主仓清理 worktree")
    f.add_argument("tid")
    f.set_defaults(func=cmd_finish)

    cw = sub.add_parser(
        "cleanup-worktree",
        help="从主仓清理已提交的 task worktree，保留 task 分支",
    )
    cw.add_argument("tid")
    cw.set_defaults(func=cmd_cleanup_worktree)

    ig = sub.add_parser(
        "integrate",
        help="把已完成 task 分支合并进主干、重建 index、删除分支",
    )
    ig.add_argument("tid")
    ig.add_argument(
        "--continue",
        dest="continue_merge",
        action="store_true",
        help="冲突解决并 git add 后继续未完成的 merge",
    )
    ig.add_argument(
        "--keep-branch",
        action="store_true",
        help="合并后保留 task 分支",
    )
    ig.add_argument(
        "--chain",
        action="store_true",
        help="串行链式：只合链尾，祖先自动跟随，删除整条链的 task 分支",
    )
    ig.set_defaults(func=cmd_integrate)

    d = sub.add_parser("drop", help="任意活跃状态 -> dropped；目录归档")
    d.add_argument("tid")
    d.add_argument("--reason", required=True)
    d.set_defaults(func=cmd_drop)

    rw = sub.add_parser("rewind", help="状态撤回（active->backlog / blocked->active；默认撤一步）")
    rw.add_argument("tid")
    rw.add_argument("--to", choices=("backlog", "active"))
    rw.add_argument("--yes", action="store_true",
                    help="跳过「分支有未合并 commit」的交互确认（agent/脚本场景用）")
    rw.add_argument("--reason", required=True)
    rw.set_defaults(func=cmd_rewind)

    pg = sub.add_parser("purge", help="误建彻底删除（仅 backlog 且任一分支都未跟踪；审计留快照）")
    pg.add_argument("tid")
    pg.add_argument("--reason", required=True)
    pg.set_defaults(func=cmd_purge)

    ls = sub.add_parser("list", help="列出当前工作区或本地分支快照的 task")
    ls.add_argument("--status", choices=VALID_STATUSES)
    ls.add_argument("--ref", help="只读查看指定本地分支中的 task 状态")
    ls.add_argument("--rebuild", action="store_true", help="重建派生索引 JSON（默认只读不写）")
    ls.set_defaults(func=cmd_list)

    sh = sub.add_parser("show", help="显示当前工作区或本地分支中的 task front matter")
    sh.add_argument("tid")
    sh.add_argument("--ref", help="只读查看指定本地分支中的 task 状态")
    sh.set_defaults(func=cmd_show)

    nb = sub.add_parser("view", help="task 全景：运行中 / 待运行分组 / 已结束")
    nb.set_defaults(func=cmd_view)


    args = p.parse_args()
    try:
        args.func(args)
    except TaskDataError as e:
        sys.exit(f"{e}\n数据不一致；请提示用户处理。")


if __name__ == "__main__":
    main()
