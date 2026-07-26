#!/usr/bin/env python3
"""task.py - task 状态唯一操作入口。

状态权威 = 每个 task 目录下 `task.md` 的 YAML front matter。每个 task 只写自己那份文件，
跨分支 merge 不产生同步点。

**工作区语义**：本脚本一律操作**当前所在工作区**（主仓或 worktree），不跨区读写。
`start` 之后 task 文档随 worktree 走，`finish` 的归档移动也在 worktree 内完成，
随 task commit 进分支，合并回主干时带回。因此主仓的 `list` / `doctor` 只能看到
已合并与未 start 的 task；进行中 task 的最新状态在各自 worktree 内查。

docs/tasks_index.json 与 docs/archive/tasks_index.json 是**派生缓存**（已 gitignore）：
由 `task.py list` 或任何写命令自动重建，不入库、不参与 merge、可随时删除重建。

数据：
  docs/tasks/{tid}_{slug}/task.md          活跃 task 状态权威
  docs/archive/tasks/{tid}_{slug}/task.md  归档 task 状态权威
  docs/tasks_index.json                    活跃派生缓存
  docs/archive/tasks_index.json            归档派生缓存
  docs/archive/tasks_audit.log             rewind/purge 审计（append-only）

命令：
  task.py add --title TITLE --slug SLUG [--note NOTE] [--review-level LEVEL] [--depends-on TIDS]
  task.py edit TID [--title TITLE] [--note NOTE | --note-append NOTE] [--review-level LEVEL]
  task.py start TID [--no-worktree]   # 主仓执行：提交 front matter → 建 worktree
  task.py preflight TID               # 开干前门禁
  task.py block TID --reason blackbox|review|infra
  task.py resume TID
  task.py finish TID              # done + 目录归档 + worktree 清理
  task.py drop TID --reason TEXT  # dropped + 目录归档 + worktree 清理
  task.py rewind TID [--to backlog|active] --reason TEXT
  task.py purge TID --reason TEXT
  task.py list [--status STATUS] [--rebuild]
  task.py show TID
  task.py doctor
"""

import argparse
import hashlib
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
REPO_ROOT = SCRIPT_DIR.parent

ACTIVE_PATH = REPO_ROOT / "docs/tasks_index.json"
ARCHIVE_PATH = REPO_ROOT / "docs/archive/tasks_index.json"
AUDIT_PATH = REPO_ROOT / "docs/archive/tasks_audit.log"
TASKS_DIR = REPO_ROOT / "docs" / "tasks"
ARCHIVE_TASKS_DIR = REPO_ROOT / "docs" / "archive" / "tasks"
TEMPLATE_DIR = TASKS_DIR / "task_template"

VALID_STATUSES = ("backlog", "active", "blocked", "done", "dropped")
ARCHIVED_STATUSES = ("done", "dropped")
# 仅活跃目录内可 rewind 的状态及其顺序（防 forward）
STATUS_ORDER = ("backlog", "active", "blocked")
DEFAULT_REWIND = {"active": "backlog", "blocked": "active"}  # 撤一步映射
BLOCK_REASONS = ("blackbox", "review", "infra")
REVIEW_LEVELS = ("full", "single")
DEFAULT_REVIEW_LEVEL = "full"
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
TID_RE = re.compile(r"^t([0-9]+)$")
PLACEHOLDER_RE = re.compile(r"\{[^{}\n]{1,80}\}")
CONTRACT_HEADING = "## 契约区"
TZ_CN = timezone(timedelta(hours=8))

FRONT_MATTER_KEYS = (
    "tid", "slug", "title", "status", "branch", "worktree",
    "review_level", "depends_on", "diff_anchor", "contract_hash", "note",
)


class TaskDataError(Exception):
    """task 数据不一致。除 doctor 外的命令捕获后退出；doctor 收集后统一报告。"""


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


def _get_head_short() -> str:
    r = _git(["rev-parse", "--short", "HEAD"])
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


def parse_front_matter(path: Path) -> tuple[dict, str]:
    """返回 (front matter dict, 正文)。缺失或不合法抛 TaskDataError。"""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise TaskDataError(f"{_rel(path)}: task.md 必须以 YAML front matter (---) 开头")
    end = text.find("\n---", 3)
    if end == -1:
        raise TaskDataError(f"{_rel(path)}: front matter 未闭合（缺结束的 ---）")
    body = text[end + 4:].lstrip("\n")
    fm = {}
    for line in text[3:end].splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        fm[key.strip()] = _unquote(val)
    return fm, body


def dump_front_matter(fm: dict) -> str:
    """所有值一律双引号包裹并转义，避免特殊字符破坏 YAML。"""
    keys = list(FRONT_MATTER_KEYS) + [k for k in fm if k not in FRONT_MATTER_KEYS]
    lines = ["---"]
    lines += [f"{key}: {_quote(fm[key])}" for key in keys if key in fm]
    lines.append("---")
    return "\n".join(lines) + "\n"


def write_front_matter(path: Path, fm: dict, body: str) -> None:
    path.write_text(dump_front_matter(fm) + "\n" + body, encoding="utf-8", newline="\n")


# --------------------------------------------------------------------------
# 扫描与派生索引
# --------------------------------------------------------------------------

def scan_tasks(collect_errors: list | None = None) -> list[dict]:
    """扫描活跃与归档 task 目录，按 tid 升序返回状态记录。

    collect_errors 非 None 时把问题追加进去并跳过该 task（doctor 用），
    否则抛 TaskDataError。
    """
    def fail(msg: str) -> bool:
        if collect_errors is None:
            raise TaskDataError(msg)
        collect_errors.append(msg)
        return True

    tasks = []
    for base in (TASKS_DIR, ARCHIVE_TASKS_DIR):
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if not d.is_dir() or d.name == TEMPLATE_DIR.name:
                continue
            task_md = d / "task.md"
            if not task_md.is_file():
                fail(f"{_rel(d)}: 缺 task.md")
                continue
            try:
                fm, _ = parse_front_matter(task_md)
            except TaskDataError as e:
                fail(str(e))
                continue
            tid = fm.get("tid", "")
            if not TID_RE.match(tid):
                fail(f"{_rel(task_md)}: front matter tid 非法（{tid!r}）")
                continue
            expected = f"{tid}_{fm.get('slug', '')}"
            if d.name != expected:
                fail(f"{_rel(d)}: 目录名与 front matter 不符（应为 {expected}）")
                continue
            status = fm.get("status", "")
            if status not in VALID_STATUSES:
                fail(f"{_rel(task_md)}: status 非法（{status!r}）")
                continue
            archived_dir = base is ARCHIVE_TASKS_DIR
            if archived_dir != (status in ARCHIVED_STATUSES):
                fail(
                    f"{_rel(task_md)}: status={status} 与所在目录不符"
                    f"（位于{'归档' if archived_dir else '活跃'}目录）"
                )
                continue
            record = {k: fm.get(k, "") for k in FRONT_MATTER_KEYS if k != "contract_hash"}
            record["dir"] = _rel(d)
            tasks.append(record)

    dup = [tid for tid, n in Counter(t["tid"] for t in tasks).items() if n > 1]
    if dup:
        fail(f"重复 tid：{sorted(dup)}")
        tasks = [t for t in tasks if t["tid"] not in dup]
    tasks.sort(key=lambda t: int(TID_RE.match(t["tid"]).group(1)))
    return tasks


def rebuild_index(tasks: list[dict] | None = None) -> list[dict]:
    """把扫描结果写入两个派生缓存 JSON。"""
    tasks = scan_tasks() if tasks is None else tasks
    groups = (
        (ACTIVE_PATH, [t for t in tasks if t["status"] not in ARCHIVED_STATUSES]),
        (ARCHIVE_PATH, [t for t in tasks if t["status"] in ARCHIVED_STATUSES]),
    )
    for path, rows in groups:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_by": "scripts/task.py",
            "authority": "docs/tasks/{tid}_{slug}/task.md front matter",
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
# spec 契约区
# --------------------------------------------------------------------------

def _section_bounds(text: str, heading: str) -> tuple[int, int] | None:
    """定位二级小节正文范围：标题须独占一行，代码围栏内的同名行不算。"""
    lines = text.splitlines(keepends=True)
    offset, start, in_code = 0, None, False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
        elif not in_code:
            if start is None and stripped == heading:
                start = offset + len(line)
            elif start is not None and stripped.startswith("## "):
                return start, offset
        offset += len(line)
    return (start, len(text)) if start is not None else None


def extract_section(text: str, heading: str) -> str:
    bounds = _section_bounds(text, heading)
    return text[bounds[0]:bounds[1]].strip() if bounds else ""


def contract_hash(spec_path: Path) -> str:
    """spec.md 契约区正文的 sha256 前 12 位；无契约区返回空串。"""
    if not spec_path.is_file():
        return ""
    section = extract_section(spec_path.read_text(encoding="utf-8"), CONTRACT_HEADING)
    if not section:
        return ""
    normalized = "\n".join(line.rstrip() for line in section.splitlines())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def find_placeholders(path: Path) -> list[str]:
    """返回文件中残留的 {占位符}（代码块内的一律忽略）。"""
    if not path.is_file():
        return []
    hits, in_code = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if not in_code:
            hits.extend(PLACEHOLDER_RE.findall(line))
    return sorted(set(hits))


# --------------------------------------------------------------------------
# worktree
# --------------------------------------------------------------------------

def worktree_rel_path(tid: str) -> str:
    return f"../{REPO_ROOT.name}_{tid}"


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


def create_worktree(tid: str, branch: str) -> tuple[str, list[str]]:
    rel = worktree_rel_path(tid)
    path = (REPO_ROOT / rel).resolve()
    if path.exists():
        if str(path) in worktree_paths():
            print(f"worktree 已存在，复用：{rel}", file=sys.stderr)
            return rel, link_local_env(path)
        sys.exit(f"{rel} 已存在且不是本仓 worktree；请先清理或用 --no-worktree")
    args = ["worktree", "add", str(path)]
    if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode == 0:
        args.append(branch)
    else:
        args += ["-b", branch]
    r = _git(args)
    if r.returncode != 0:
        sys.exit(f"git worktree add 失败：{r.stderr.strip()}")
    return rel, link_local_env(path)


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
    for link in (path / ".env", *path.glob("*/.env")):
        if link.is_symlink():
            link.unlink()
    r = _git(["worktree", "remove", str(path)])
    if r.returncode != 0:
        return False, f"git worktree remove 失败（{r.stderr.strip()}）；请手动处理 {rel}"
    _git(["worktree", "prune"])
    return True, f"worktree 已移除：{rel}"


# --------------------------------------------------------------------------
# 命令
# --------------------------------------------------------------------------

def _normalize_depends(raw: str | None, tasks: list[dict], self_tid: str = "") -> str:
    if not raw:
        return ""
    known = {t["tid"] for t in tasks}
    by_tid = {t["tid"]: t for t in tasks}
    out = []
    for item in re.split(r"[,\s]+", raw.strip()):
        if not item:
            continue
        if not TID_RE.match(item):
            sys.exit(f"--depends-on 需要 tNNN 形式（收到 {item!r}）")
        if item == self_tid:
            sys.exit(f"--depends-on 不能指向自身（{item}）")
        if item not in known:
            sys.exit(f"--depends-on 引用了不存在的 {item}")
        out.append(item)
    out = list(dict.fromkeys(out))

    # 环检测：沿 depends_on 前向搜索，若能回到 self_tid 则成环
    if self_tid:
        seen, stack = set(), list(out)
        while stack:
            cur = stack.pop()
            if cur == self_tid:
                sys.exit(f"--depends-on 会形成循环依赖（{self_tid} → … → {self_tid}）")
            if cur in seen:
                continue
            seen.add(cur)
            dep = by_tid.get(cur)
            if dep:
                stack.extend(x for x in re.split(r"[,\s]+", dep.get("depends_on", "") or "") if x)
    return ",".join(out)


def _depends_list(fm: dict) -> list[str]:
    return [x for x in re.split(r"[,\s]+", fm.get("depends_on", "") or "") if x]


def cmd_add(args):
    if not SLUG_RE.match(args.slug):
        sys.exit(f"slug 须匹配 {SLUG_RE.pattern}（收到 {args.slug!r}）")
    if not args.title.strip():
        sys.exit("title 不能为空")
    tasks = scan_tasks()
    for t in tasks:
        if t["slug"] == args.slug:
            sys.exit(f"slug 已存在：{args.slug}（{t['tid']}）")
    depends_on = _normalize_depends(args.depends_on, tasks)
    n = max((int(TID_RE.match(t["tid"]).group(1)) for t in tasks), default=0) + 1
    tid = f"t{n:03d}"
    task_dir = TASKS_DIR / f"{tid}_{args.slug}"
    if task_dir.exists():
        sys.exit(f"{_rel(task_dir)} 已存在；请提示用户处理")
    if not TEMPLATE_DIR.is_dir():
        sys.exit(f"缺模板目录 {_rel(TEMPLATE_DIR)}")

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
        "depends_on": depends_on,
        "diff_anchor": "",
        "contract_hash": "",
        "note": args.note or "",
    })
    write_front_matter(task_md, fm, body)
    rebuild_index()
    print(f"added {tid} '{fm['title']}' status=backlog review_level={fm['review_level']}")
    print(f"工作区：{_rel(task_dir)}（已从模板复制 spec.md / task.md / review.md）")


def cmd_edit(args):
    fields = (args.title, args.note, args.note_append, args.review_level, args.depends_on)
    if all(v is None for v in fields):
        sys.exit("没有要改的字段；传 --title / --note / --note-append / --review-level / --depends-on")
    if args.note is not None and args.note_append is not None:
        sys.exit("--note 与 --note-append 互斥")
    task, path, fm, body = load_task(args.tid)
    if fm["status"] in ARCHIVED_STATUSES:
        sys.exit(f"{args.tid} 已归档（{fm['status']}），不可编辑")
    changed = []
    if args.title is not None:
        title = args.title.strip()
        if not title:
            sys.exit("title 不能为空")
        fm["title"] = title
        changed.append(f"title={title!r}")
    if args.note is not None:
        fm["note"] = args.note
        changed.append(f"note={args.note!r}")
    if args.note_append is not None:
        if not args.note_append.strip():
            sys.exit("--note-append 不能为空")
        append_note(fm, args.note_append)
        changed.append(f"note+={args.note_append!r}")
    if args.review_level is not None:
        fm["review_level"] = args.review_level
        changed.append(f"review_level={args.review_level}")
    if args.depends_on is not None:
        fm["depends_on"] = _normalize_depends(args.depends_on, scan_tasks(), self_tid=fm["tid"])
        changed.append(f"depends_on={fm['depends_on']!r}")
    write_front_matter(path, fm, body)
    rebuild_index()
    print(f"{args.tid} updated: {', '.join(changed)}")


def cmd_start(args):
    task, path, fm, body = load_task(args.tid)
    require_status(fm, "backlog")

    blockers = [d for d in _depends_list(fm) if (find_task(d) or {}).get("status") != "done"]
    if blockers:
        sys.exit(f"{args.tid} 依赖未完成：{', '.join(blockers)}；先完成或用 edit 改 depends_on")

    spec = REPO_ROOT / task["dir"] / "spec.md"
    placeholders = find_placeholders(spec) + find_placeholders(path)
    if placeholders:
        sys.exit(
            f"{args.tid} 的 spec.md / task.md 仍有模板占位符：{', '.join(sorted(set(placeholders))[:6])}；"
            "填完再 start（否则契约区会锁定占位内容）"
        )

    branch = f"{fm['tid']}_{fm['slug']}"
    fm["status"] = "active"
    fm["branch"] = branch
    fm["contract_hash"] = contract_hash(spec)
    fm["diff_anchor"] = _get_head_short()

    if args.no_worktree:
        dirty = [p for p in porcelain_entries() if not p.startswith(task["dir"])]
        if dirty and not args.force:
            sys.exit(
                f"主工作区有 {len(dirty)} 项与本 task 无关的未提交改动："
                f"{', '.join(dirty[:5])}\n"
                "切分支会把它们带走（未提交改动不跟 branch）。先提交或 stash；"
                "确认无碍时加 --force"
            )
        fm["worktree"] = ""
        write_front_matter(path, fm, body)
        rebuild_index()
        exists = _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode == 0
        checkout = ["switch", branch] if exists else ["switch", "-c", branch]
        r = _git(checkout)
        if r.returncode != 0:
            sys.exit(f"git {' '.join(checkout)} 失败：{r.stderr.strip()}")
        loc, linked = f"当前工作区（--no-worktree，分支 {branch}）", []
    else:
        # 先提交 front matter，worktree 才能签出到新状态（worktree 只签出已提交内容）
        write_front_matter(path, fm, body)
        rebuild_index()
        rel_task_md = f"{task['dir']}/task.md"
        if _git(["add", "--", rel_task_md]).returncode != 0:
            sys.exit(f"git add {rel_task_md} 失败")
        r = _git(["commit", "-m", f"chore({fm['tid']}): start", "--", rel_task_md])
        if r.returncode != 0:
            sys.exit(
                f"提交 {rel_task_md} 失败：{r.stderr.strip() or r.stdout.strip()}\n"
                "worktree 只签出已提交内容，start 必须先提交 front matter"
            )
        fm["diff_anchor"] = _get_head_short()
        write_front_matter(path, fm, body)
        rel, linked = create_worktree(fm["tid"], branch)
        fm["worktree"] = rel
        write_front_matter(path, fm, body)
        # worktree 内的 task.md 是签出版本，补齐 worktree/diff_anchor 两个字段
        wt_task_md = (REPO_ROOT / rel).resolve() / task["dir"] / "task.md"
        if wt_task_md.is_file():
            wt_fm, wt_body = parse_front_matter(wt_task_md)
            wt_fm["worktree"] = rel
            wt_fm["diff_anchor"] = fm["diff_anchor"]
            write_front_matter(wt_task_md, wt_fm, wt_body)
        rebuild_index()
        loc = f"worktree {rel}"

    print(f"{args.tid} status=active branch={branch} diff_anchor={fm['diff_anchor']}")
    print(f"工作位置：{loc}")
    if linked:
        print(f"已软链本地配置：{', '.join(linked)}")
    if fm["contract_hash"]:
        print(f"契约区已锁定：contract_hash={fm['contract_hash']}")
    else:
        print("WARNING: spec.md 无「## 契约区」小节，契约冻结校验不可用", file=sys.stderr)
    if not args.no_worktree:
        print(f"下一步：cd {worktree_rel_path(fm['tid'])} 后在该工作区执行 preflight 与后续所有步骤")


def cmd_preflight(args):
    task, path, fm, body = load_task(args.tid)
    task_dir = REPO_ROOT / task["dir"]
    problems, warnings, checks = [], [], []

    checks.append(f"工作区={_rel(REPO_ROOT) or REPO_ROOT.name}")
    checks.append(f"status={fm['status']}")
    if fm["status"] in ARCHIVED_STATUSES:
        problems.append(f"status={fm['status']}，已归档不可执行")
    elif fm["status"] == "blocked":
        problems.append("status=blocked，须用户放行（加轮 resume 或 drop）后再执行")

    for name in ("spec.md", "task.md"):
        hits = find_placeholders(task_dir / name)
        if hits:
            problems.append(f"{name} 残留模板占位符：{', '.join(hits[:6])}")
    checks.append("占位符扫描完成")

    spec = task_dir / "spec.md"
    if not spec.is_file():
        problems.append("缺 spec.md")
    else:
        text = spec.read_text(encoding="utf-8")
        if not _section_bounds(text, CONTRACT_HEADING):
            problems.append("spec.md 缺「## 契约区」小节（须独占一行）")
        if not re.search(r"^\s*-\s*\[ \]\s*\S", text, re.MULTILINE):
            problems.append("spec.md 验收标准为空")
        current = contract_hash(spec)
        recorded = fm.get("contract_hash", "")
        if fm["status"] == "active" and recorded and current != recorded:
            problems.append(
                f"契约区已被改动（记录 {recorded} → 当前 {current}）；"
                "执行期禁止改契约区，请回退改动，或请用户确认后 rewind 重走 start"
            )
        checks.append(f"contract_hash={current or '（无）'}")

    if fm["status"] == "active" and not fm.get("diff_anchor"):
        problems.append("diff_anchor 为空；review 无法渲染，请 rewind 后重走 start")

    if fm.get("review_level") not in REVIEW_LEVELS:
        problems.append(f"review_level={fm.get('review_level')!r} 非法，须为 {REVIEW_LEVELS}")

    for dep in _depends_list(fm):
        dep_task = find_task(dep)
        if not dep_task:
            warnings.append(f"依赖 {dep} 不在当前工作区（可能在其它 worktree 或尚未合并）")
        elif dep_task["status"] != "done":
            problems.append(f"依赖 {dep} 状态为 {dep_task['status']}，未完成")

    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    checks.append(f"当前分支={branch}")
    if fm["status"] == "active" and fm["branch"] and branch != fm["branch"]:
        problems.append(f"当前分支 {branch} 与 task 分支 {fm['branch']} 不符")

    wt_rel = fm.get("worktree", "")
    others = [t for t in scan_tasks()
              if t["tid"] != fm["tid"] and t["status"] in ("active", "blocked")]
    if wt_rel:
        registered = {Path(p).resolve() for p in worktree_paths()}
        if REPO_ROOT.resolve() not in registered:
            problems.append("当前目录不是本仓登记的 worktree；请在 task 自己的 worktree 内执行")
        elif REPO_ROOT.name != f"{Path(wt_rel).name}":
            problems.append(
                f"本次在 {_rel(REPO_ROOT) or REPO_ROOT.name} 运行，但本 task 的工作区是 {wt_rel}；请先 cd 过去"
            )
        else:
            checks.append(f"worktree 隔离={wt_rel}")
    elif fm["status"] == "active":
        if others:
            problems.append(
                f"本 task 未用 worktree，且另有进行中 task（{', '.join(t['tid'] for t in others)}）"
                "共享同一工作区；未提交改动会被切分支抹掉。请 rewind 后重新 start"
            )
        else:
            warnings.append("未用 worktree（--no-worktree）；禁止长期保留未提交改动")

    dirty = porcelain_entries()
    foreign = [p for p in dirty
               if not p.startswith(task["dir"])
               and p not in ("docs/tasks_index.json", "docs/archive/tasks_index.json")
               and not p.startswith(".scratch/")]
    if foreign:
        warnings.append(f"工作区有 {len(foreign)} 项与本 task 无关的改动：{', '.join(foreign[:5])}")
    checks.append(f"工作区脏项={len(dirty)}")

    scan_errors = []
    scanned = scan_tasks(collect_errors=scan_errors)
    problems.extend(scan_errors)
    known_branches = {t["branch"] for t in scanned if t.get("branch")}
    stale = [b for b in _list_task_branches() if b not in known_branches]
    if stale:
        warnings.append(
            f"分支在当前工作区找不到对应 task：{', '.join(stale)}"
            "（进行中 task 的文档在各自 worktree，未必是孤儿）"
        )
    checks.append("索引↔目录↔分支交叉校验完成")

    print(f"# preflight {args.tid}")
    for line in checks:
        print(f"  check: {line}")
    for line in warnings:
        print(f"  WARN : {line}")
    for line in problems:
        print(f"  FAIL : {line}")
    if problems:
        print(f"\npreflight=FAIL（{len(problems)} 项）；修复后重跑，勿绕过")
        sys.exit(1)
    print(f"\npreflight=PASS{f'（{len(warnings)} 条警告）' if warnings else ''}")


def _list_task_branches() -> list[str]:
    r = _git(["branch", "--list", "--format=%(refname:short)", "t[0-9]*_*"])
    return [b.strip() for b in r.stdout.splitlines() if b.strip()] if r.returncode == 0 else []


def cmd_block(args):
    task, path, fm, body = load_task(args.tid)
    require_status(fm, "active")
    fm["status"] = "blocked"
    append_note(fm, f"blocked: {args.reason}")
    write_front_matter(path, fm, body)
    rebuild_index()
    print(f"{args.tid} status=blocked reason={args.reason}")


def cmd_resume(args):
    task, path, fm, body = load_task(args.tid)
    require_status(fm, "blocked")
    fm["status"] = "active"
    write_front_matter(path, fm, body)
    rebuild_index()
    print(f"{args.tid} status=active (resumed)")


def _close_task(args, status: str, note: str | None) -> None:
    """done / dropped 收尾：先做 git 侧动作，再单次写盘，最后归档目录。

    单次写盘是为了避免「front matter 已写 done、目录未归档」的中间态——
    那种状态下 finish/drop/rewind 三条出口全被状态校验挡死，只能手改 front matter。
    """
    task, path, fm, body = load_task(args.tid)
    if status == "done":
        require_status(fm, "active")
    elif fm["status"] in ARCHIVED_STATUSES:
        sys.exit(f"{args.tid} 已是 {fm['status']}")

    src = REPO_ROOT / task["dir"]
    dst = ARCHIVE_TASKS_DIR / f"{fm['tid']}_{fm['slug']}"
    if dst.exists():
        sys.exit(f"归档目录已存在：{_rel(dst)}（数据冲突，请提示用户）")

    in_own_worktree = bool(fm.get("worktree")) and REPO_ROOT.name == Path(fm["worktree"]).name
    if in_own_worktree:
        removed, wt_msg = False, (
            f"worktree {fm['worktree']} 保留（当前正在其中运行）；"
            "合并回主干后在主仓执行 git worktree remove 清理"
        )
    else:
        removed, wt_msg = remove_worktree(fm.get("worktree", ""))

    fm["status"] = status
    if note:
        append_note(fm, note)
    if removed:
        fm["worktree"] = ""
    else:
        append_note(fm, f"worktree 未移除：{fm.get('worktree', '')}")
    write_front_matter(path, fm, body)

    ARCHIVE_TASKS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    rebuild_index()
    print(f"{args.tid} status={status}; 目录已归档 -> {_rel(dst)}; {wt_msg}")
    if not removed and not in_own_worktree:
        print("WARNING: worktree 未移除，已记入 note；请手动清理", file=sys.stderr)


def cmd_finish(args):
    _close_task(args, "done", None)


def cmd_drop(args):
    _close_task(args, "dropped", f"dropped: {args.reason}")


def cmd_rewind(args):
    task, path, fm, body = load_task(args.tid)
    current = fm["status"]
    if current not in STATUS_ORDER:
        sys.exit(
            f"{args.tid} status={current}；rewind 只处理 {STATUS_ORDER}"
            "（done/dropped 已归档不可 rewind：放弃用 drop，彻底删除用 purge）"
        )
    target = args.to or DEFAULT_REWIND.get(current)
    if target is None:
        sys.exit(f"{args.tid} 已是 backlog，无可撤回")
    if target not in STATUS_ORDER:
        sys.exit(f"--to {target!r} 非法；须为 {STATUS_ORDER} 之一")
    if STATUS_ORDER.index(target) >= STATUS_ORDER.index(current):
        sys.exit(f"rewind 只向后：{current} -> {target} 不是撤回（前进用 start/block）")

    wt_msg = ""
    if target == "backlog":
        if fm.get("branch") and has_unmerged_commits(fm["branch"]):
            print(
                f"WARNING: 分支 {fm['branch']!r} 有未合并进 {default_branch()} 的 commit；"
                "rewind 会使其游离。继续？(y/N)",
                file=sys.stderr,
            )
            try:
                answer = input()
            except EOFError:
                answer = ""
            if answer.strip().lower() not in ("y", "yes"):
                sys.exit("rewind aborted by user")
        removed, wt_msg = remove_worktree(fm.get("worktree", ""))
        if not removed:
            sys.exit(
                f"{wt_msg}\nrewind 中止：worktree 未清理时回到 backlog 会留下无主工作区"
            )
        fm["branch"] = ""
        fm["worktree"] = ""
        fm["contract_hash"] = ""
        fm["diff_anchor"] = ""

    fm["status"] = target
    append_note(fm, f"rewound: {current} -> {target}; {args.reason}")
    write_front_matter(path, fm, body)
    rebuild_index()
    append_audit("rewind", tid=args.tid, fr=current, to=target, reason=args.reason)
    print(f"{args.tid} status={target} (rewound from {current}){'; ' + wt_msg if wt_msg else ''}")


def cmd_purge(args):
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
    tasks = rebuild_index()
    rows = [t for t in tasks if not args.status or t["status"] == args.status]
    if args.rebuild:
        print(f"index rebuilt: {_rel(ACTIVE_PATH)}, {_rel(ARCHIVE_PATH)}")
    if not rows:
        print("(no tasks)")
        return
    print("| tid    | title                              | status   | lvl    | branch                        | note |")
    print("|--------|------------------------------------|----------|--------|-------------------------------|------|")
    for t in rows:
        print(
            f"| {t['tid']:<6} | {t['title'][:34]:<34} | {t['status']:<8} | "
            f"{(t.get('review_level') or ''):<6} | {(t.get('branch') or '')[:29]:<29} | {(t.get('note') or '')[:40]} |"
        )


def cmd_show(args):
    task, path, fm, body = load_task(args.tid)
    fields = dict(fm)
    fields["dir"] = task["dir"]
    fields["task_md"] = _rel(path)
    width = max(len(k) for k in fields)
    for k, v in fields.items():
        print(f"{k.ljust(width)}: {v}")


def cmd_doctor(args):
    """一致性检查。扫描异常不中断，全部收集后统一报告。"""
    problems = []
    tasks = scan_tasks(collect_errors=problems)
    try:
        rebuild_index(tasks)
    except OSError as e:
        problems.append(f"派生索引写入失败：{e}")

    for t in tasks:
        d = REPO_ROOT / t["dir"]
        for name in ("spec.md", "task.md"):
            if not (d / name).is_file():
                problems.append(f"{t['tid']}: 缺 {name}")
        if (d / "plan.md").is_file():
            problems.append(f"{t['tid']}: 存在废弃的 plan.md（内容并入 spec.md 上下文区后删除）")
        if t["status"] == "active":
            if not t.get("branch"):
                problems.append(f"{t['tid']}: active 但 branch 为空")
            if not t.get("diff_anchor"):
                problems.append(f"{t['tid']}: active 但 diff_anchor 为空，review 无法渲染")
        if t.get("review_level") and t["review_level"] not in REVIEW_LEVELS:
            problems.append(f"{t['tid']}: review_level={t['review_level']} 非法")

    known = {t["branch"] for t in tasks if t.get("branch")}
    notes = []
    for b in _list_task_branches():
        if b not in known:
            notes.append(f"分支 {b} 在当前工作区无对应 task（可能其文档在别的 worktree 或未合并）")
    for wt, branch in worktree_paths().items():
        if Path(wt).resolve() == REPO_ROOT.resolve():
            continue
        if branch and branch not in known:
            notes.append(f"worktree {wt}（分支 {branch}）在当前工作区无对应 task")

    print(f"工作区：{_rel(REPO_ROOT) or REPO_ROOT.name}；扫描 {len(tasks)} 个 task；索引已重建")
    for n in notes:
        print(f"  note : {n}")
    if not problems:
        print("doctor=PASS")
        return
    for p in problems:
        print(f"  FAIL : {p}")
    print(f"\ndoctor=FAIL（{len(problems)} 项）；请提示用户处理，勿手工改索引")
    sys.exit(1)


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
    a.add_argument("--depends-on", help="前置 tid，逗号分隔")
    a.set_defaults(func=cmd_add)

    e = sub.add_parser("edit", help="改活跃 task 的 title / note / review_level / depends_on")
    e.add_argument("tid")
    e.add_argument("--title")
    e.add_argument("--note", help="覆盖 note（传空串则清空）")
    e.add_argument("--note-append", help="在现有 note 后追加")
    e.add_argument("--review-level", choices=REVIEW_LEVELS)
    e.add_argument("--depends-on", help="前置 tid，逗号分隔；传空串清空")
    e.set_defaults(func=cmd_edit)

    s = sub.add_parser(
        "start",
        help="backlog -> active：写并提交 front matter、建 worktree、软链 .env、锁契约区、记 diff_anchor",
    )
    s.add_argument("tid")
    s.add_argument("--no-worktree", action="store_true",
                   help="仅在用户明确指令时使用：不建 worktree，直接在当前工作区切分支")
    s.add_argument("--force", action="store_true",
                   help="配合 --no-worktree：主工作区有无关脏改动时仍继续")
    s.set_defaults(func=cmd_start)

    pf = sub.add_parser("preflight", help="开干前门禁：占位符/契约区/依赖/分支/worktree/工作区/索引交叉校验")
    pf.add_argument("tid")
    pf.set_defaults(func=cmd_preflight)

    b = sub.add_parser("block", help="active -> blocked")
    b.add_argument("tid")
    b.add_argument("--reason", required=True, choices=BLOCK_REASONS)
    b.set_defaults(func=cmd_block)

    r = sub.add_parser("resume", help="blocked -> active（用户加轮或排除阻塞后）")
    r.add_argument("tid")
    r.set_defaults(func=cmd_resume)

    f = sub.add_parser("finish", help="active -> done；目录归档 + worktree 清理")
    f.add_argument("tid")
    f.set_defaults(func=cmd_finish)

    d = sub.add_parser("drop", help="任意活跃状态 -> dropped；目录归档 + worktree 清理")
    d.add_argument("tid")
    d.add_argument("--reason", required=True)
    d.set_defaults(func=cmd_drop)

    rw = sub.add_parser("rewind", help="状态撤回（active->backlog / blocked->active；默认撤一步）")
    rw.add_argument("tid")
    rw.add_argument("--to", choices=("backlog", "active"))
    rw.add_argument("--reason", required=True)
    rw.set_defaults(func=cmd_rewind)

    pg = sub.add_parser("purge", help="误建彻底删除（仅 backlog 且任一分支都未跟踪；审计留快照）")
    pg.add_argument("tid")
    pg.add_argument("--reason", required=True)
    pg.set_defaults(func=cmd_purge)

    ls = sub.add_parser("list", help="列出当前工作区的 task（扫描 task.md 并重建派生索引）")
    ls.add_argument("--status", choices=VALID_STATUSES)
    ls.add_argument("--rebuild", action="store_true", help="额外打印索引重建路径")
    ls.set_defaults(func=cmd_list)

    sh = sub.add_parser("show", help="显示单条 task 的 front matter")
    sh.add_argument("tid")
    sh.set_defaults(func=cmd_show)

    dr = sub.add_parser("doctor", help="一致性检查：目录、front matter、分支与 worktree")
    dr.set_defaults(func=cmd_doctor)

    args = p.parse_args()
    try:
        args.func(args)
    except TaskDataError as e:
        sys.exit(f"{e}\n数据不一致，请用 scripts/task.py doctor 查看全部问题并提示用户处理。")


if __name__ == "__main__":
    main()
