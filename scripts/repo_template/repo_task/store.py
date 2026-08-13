"""Canonical store implementation for the task toolchain."""

import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import repo_task.context as ctx

from .documents import parse_front_matter, parse_front_matter_text, parse_tid_list
from .git_ops import _get_head_short, _git, has_unmerged_commits, primary_worktree_path, require_primary_worktree, worktree_paths

def task_schedule_references(target_tid: str, tasks: list[dict] | None = None) -> list[str]:
    """返回非归档 task 中引用 target_tid 的字段，供 drop/purge 拒绝悬空边。

    只扫活跃目录：归档 task 的历史边无脚本清理途径（edit 拒绝归档、归档只准新增），
    若计入会永久锁死被引用 task 的 drop。
    """
    references = []
    for task in scan_tasks() if tasks is None else tasks:
        if task["tid"] == target_tid:
            continue
        if task["status"] in ctx.ARCHIVED_STATUSES:
            continue
        for field in ("depends_on", "conflicts_with"):
            tids = parse_tid_list(task.get(field, ""), field=f"{task['tid']}.{field}")
            if target_tid in tids:
                references.append(f"{task['tid']}.{field}")
    return references

def _task_record(fm: dict, *, directory: str, source: str, archived: bool) -> dict:
    tid = fm.get("tid", "")
    if not ctx.TID_RE.match(tid):
        raise ctx.TaskDataError(f"{source}: front matter tid 非法（{tid!r}）")
    expected = f"{tid}_{fm.get('slug', '')}"
    if Path(directory).name != expected:
        raise ctx.TaskDataError(f"{directory}: 目录名与 front matter 不符（应为 {expected}）")
    status = fm.get("status", "")
    if status not in ctx.VALID_STATUSES:
        raise ctx.TaskDataError(f"{source}: status 非法（{status!r}）")
    if archived != (status in ctx.ARCHIVED_STATUSES):
        raise ctx.TaskDataError(
            f"{source}: status={status} 与所在目录不符"
            f"（位于{'归档' if archived else '活跃'}目录）"
        )
    record = {k: fm.get(k, "") for k in ctx.FRONT_MATTER_KEYS}
    record["dir"] = directory
    return record

def _validate_task_records(tasks: list[dict]) -> list[dict]:
    dup = [tid for tid, n in Counter(t["tid"] for t in tasks).items() if n > 1]
    if dup:
        raise ctx.TaskDataError(f"重复 tid：{sorted(dup)}")
    tasks.sort(key=lambda t: int(ctx.TID_RE.match(t["tid"]).group(1)))
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
            if not directory.is_dir() or (not archived and directory.name == ctx.TEMPLATE_DIR.name):
                continue
            task_md = directory / "task.md"
            if not task_md.is_file():
                raise ctx.TaskDataError(f"{task_md.parent}: 缺 task.md")
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
    return _scan_tasks_in_directories(ctx.TASKS_DIR, ctx.ARCHIVE_TASKS_DIR)

def scan_tasks_in_worktree(root: Path) -> list[dict]:
    """扫描另一登记 worktree 的 task 状态，不修改 task.py 全局路径。"""
    return _scan_tasks_in_directories(
        root / "docs" / "tasks",
        root / "docs" / "archive" / "tasks",
    )

def git_text_at_ref(ref: str, path: str) -> str:
    r = _git(["show", f"{ref}:{path}"])
    if r.returncode != 0:
        raise ctx.TaskDataError(f"{ref}:{path} 不存在或无法读取")
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
        raise ctx.TaskDataError(f"无法读取 ref {ref!r}：{r.stderr.strip()}")
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
        if not archived and name == ctx.TEMPLATE_DIR.name:
            continue
        directory = (
            f"docs/archive/tasks/{name}" if archived else f"docs/tasks/{name}"
        )
        path = f"{directory}/task.md"
        try:
            text = git_text_at_ref(ref, path)
        except ctx.TaskDataError:
            raise ctx.TaskDataError(f"{ref}:{directory}: 缺 task.md") from None
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
            ctx.ACTIVE_PATH,
            [t for t in tasks if t["status"] not in ctx.ARCHIVED_STATUSES],
            "docs/tasks/{tid}_{slug}/task.md front matter",
        ),
        (
            ctx.ARCHIVE_PATH,
            [t for t in tasks if t["status"] in ctx.ARCHIVED_STATUSES],
            "docs/archive/tasks/{tid}_{slug}/task.md front matter",
        ),
    )
    for path, rows, authority in groups:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_by": "scripts/repo_template/task.py",
            "authority": authority,
            "workspace": ctx._rel(ctx.REPO_ROOT) or str(ctx.REPO_ROOT),
            "tasks": rows,
        }
        # 临时文件 + os.replace：并发/崩溃下不落盘截断 JSON（RT-007）
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=4) + "\n",
            encoding="utf-8", newline="\n",
        )
        os.replace(temporary, path)
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
    return [b.strip() for b in r.stdout.splitlines() if ctx.TASK_BRANCH_RE.fullmatch(b.strip())]

def task_effective_state(tid: str, primary_fm: dict) -> str | None:
    """main 副本为 backlog 时，探测其被 worktree/未合并分支覆盖的有效状态。

    返回覆盖证据描述；无覆盖返回 None。main 中非 backlog 状态以 main 为准，
    同样返回 None。只读，供 edit/drop 在主仓拒绝操作过期 backlog 副本。
    """
    if primary_fm.get("status") != "backlog":
        return None
    wt_rel = ctx.worktree_rel_path(tid)
    wt_path = (ctx.REPO_ROOT / wt_rel).resolve()
    registered = worktree_paths().get(str(wt_path))
    if registered:
        task_dir = ctx.REPO_ROOT / "docs" / "tasks" / f"{tid}_{primary_fm.get('slug', '')}"
        wt_task = wt_path / task_dir.relative_to(ctx.REPO_ROOT) / "task.md"
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
        except ctx.TaskDataError:
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
        if ctx.TASK_BRANCH_RE.fullmatch(branch.strip())
    )

def discover_effective_tasks() -> dict[str, dict]:
    """按 worktree → 未合并 task 分支 → main 发现每个 task 的有效状态。"""
    return {tid: task for tid, task, _, _ in _discover_effective_entries()}


def discover_effective_sources() -> dict[str, dict]:
    """每个有效 task 的状态 + 读取来源与位置。

    返回 tid -> {tid, slug, title, status, note, source, read_at}：
    - source="worktree"：read_at=worktree 绝对路径（在其中读 task.md / 跑 show）
    - source="branch"：read_at=未合并分支 short name（用 show/list/preflight --ref）
    - source="main"：read_at=None（主干文档即权威）
    只读；供 skill 依「task 状态读取优先级」在正确位置读取 task。
    """
    out: dict[str, dict] = {}
    for tid, task, source, read_at in _discover_effective_entries():
        out[tid] = {
            "tid": tid,
            "slug": task.get("slug", ""),
            "title": task.get("title", ""),
            "status": task.get("status", ""),
            "note": task.get("note", ""),
            "source": source,
            "read_at": read_at,
        }
    return out


def _discover_effective_entries() -> list[tuple[str, dict, str, str | None]]:
    """(tid, task, source, read_at) 列表，按 worktree → 未合并分支 → main 优先级。"""
    require_primary_worktree()
    effective = {task["tid"]: task for task in scan_tasks()}
    sources: dict[str, tuple[str, str | None]] = {
        tid: ("main", None) for tid in effective
    }

    for branch in _local_task_branches():
        if not has_unmerged_commits(branch):
            continue
        owner_tid = ctx.TASK_BRANCH_RE.fullmatch(branch).group(1)
        tasks = scan_tasks_at_ref(branch)
        task = next((item for item in tasks if item["tid"] == owner_tid), None)
        if task is None:
            raise ctx.TaskDataError(f"未合并 task 分支 {branch!r} 缺自身 task {owner_tid}")
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
        sources[owner_tid] = ("branch", branch)

    primary = primary_worktree_path()
    for path_text, branch in worktree_paths().items():
        path = Path(path_text).resolve()
        if path == primary or not path.is_dir():
            continue
        match = ctx.TASK_BRANCH_RE.fullmatch(branch)
        if not match:
            continue
        owner_tid = match.group(1)
        # 单个 worktree 的 task.md 损坏（agent 写一半崩溃）不得让整个
        # ps/reconcile/view 失败：标记为脏状态并继续观察其余 task。
        try:
            tasks = scan_tasks_in_worktree(path)
            task = next((item for item in tasks if item["tid"] == owner_tid), None)
        except (OSError, ctx.TaskDataError):
            effective[owner_tid] = {
                "tid": owner_tid,
                "slug": "",
                "title": "(worktree task.md 损坏)",
                "status": "blocked",
                "note": f"登记 worktree {path} 的 task.md 无法解析",
            }
            sources[owner_tid] = ("worktree", str(path))
            continue
        if task is None:
            effective[owner_tid] = {
                "tid": owner_tid,
                "slug": "",
                "title": "(worktree 缺自身 task)",
                "status": "blocked",
                "note": f"登记 worktree {path} 缺自身 task {owner_tid}",
            }
            sources[owner_tid] = ("worktree", str(path))
            continue
        effective[owner_tid] = task
        sources[owner_tid] = ("worktree", str(path))
    return [(tid, task, *sources[tid]) for tid, task in effective.items()]

def load_task(tid: str) -> tuple[dict, Path, dict, str]:
    """返回 (索引记录, task.md 路径, front matter, 正文)。"""
    task = find_task(tid)
    if not task:
        sys.exit(
            f"{tid} 不存在于当前工作区（{ctx._rel(ctx.REPO_ROOT) or ctx.REPO_ROOT.name}）。"
            "进行中 task 的文档随其 worktree，请在对应 worktree 内执行"
        )
    path = ctx.REPO_ROOT / task["dir"] / "task.md"
    fm, body = parse_front_matter(path)
    return task, path, fm, body

def load_task_at_ref(tid: str, ref: str) -> tuple[dict, dict, str]:
    """返回指定 ref 中的 (索引记录, front matter, 正文)。"""
    task = find_task(tid, scan_tasks_at_ref(ref))
    if not task:
        raise ctx.TaskDataError(f"{tid} 不存在于 ref {ref!r}")
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

    ctx.AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(ctx.TZ_CN).isoformat(timespec="seconds")
    parts = [ts, action, f"tid={tid}", f"from={fr}", f"to={to}", f"head={_get_head_short()}"]
    if slug:
        parts.append(f"slug={clean(slug)}")
    if title:
        parts.append(f"title={clean(title)}")
    parts.append(f"reason={clean(reason)}")
    try:
        with ctx.AUDIT_PATH.open("a", encoding="utf-8") as f:
            f.write(" | ".join(parts) + "\n")
    except OSError as error:
        # fail-closed：审计失败抛 TaskDataError，调用方据此决定回滚/中止，
        # 避免「状态已迁移、审计缺失、不可重试」（F15）
        raise ctx.TaskDataError(
            f"审计写入失败（{action} tid={tid}）：{error}"
        ) from error
