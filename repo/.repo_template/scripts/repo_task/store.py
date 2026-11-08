"""Canonical store implementation for the task toolchain."""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import repo_task.context as ctx

from .documents import atomic_write_text, parse_front_matter, parse_front_matter_text, parse_tid_list
from .git_ops import _get_head_short, _git, default_branch, has_unmerged_commits, primary_worktree_path, require_primary_worktree, worktree_paths

def task_schedule_references(
    target_tid: str, tasks: list[dict] | None = None, *, repo: "ctx.RepoContext | None" = None,
) -> list[str]:
    """返回非归档 task 中引用 target_tid 的字段，供 drop/purge 拒绝悬空边。

    只扫活跃目录：归档 task 的历史边无脚本清理途径（edit 拒绝归档、归档只准新增），
    若计入会永久锁死被引用 task 的 drop。
    """
    references = []
    if tasks is None:
        if repo is None:
            raise ValueError("task_schedule_references 需要 tasks 或 repo")
        tasks = scan_tasks(repo=repo)
    for task in tasks:
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
    # 归档目录只容纳 done/dropped。done 也可停在活跃目录（已 finish、未 integrate，
    # 目录由 integrate 的 merge commit 归档）；dropped 由 drop 脚本直接移入归档目录。
    misplaced = status not in ctx.ARCHIVED_STATUSES if archived else status == "dropped"
    if misplaced:
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

def _scan_tasks_in_directories(
    tasks_dir: Path, archive_dir: Path, *, template_name: str = "task_template",
) -> list[dict]:
    tasks = []
    for base, archived, rel_base in (
        (tasks_dir, False, "docs/tasks"),
        (archive_dir, True, "docs/archive/tasks"),
    ):
        if not base.is_dir():
            continue
        for directory in sorted(base.iterdir()):
            if not directory.is_dir() or (not archived and directory.name == template_name):
                continue
            note_md = directory / ctx.PROGRESS_FILE
            if not note_md.is_file():
                raise ctx.TaskDataError(f"{note_md.parent}: 缺 {ctx.PROGRESS_FILE}")
            fm, _ = parse_front_matter_text(
                note_md.read_text(encoding="utf-8"), source=str(note_md)
            )
            tasks.append(_task_record(
                fm,
                directory=f"{rel_base}/{directory.name}",
                source=str(note_md),
                archived=archived,
            ))
    return _validate_task_records(tasks)

def scan_tasks(*, repo: "ctx.RepoContext") -> list[dict]:
    """扫描当前工作区 task，按 tid 升序返回状态记录。"""
    return _scan_tasks_in_directories(
        repo.tasks_dir, repo.archive_tasks_dir, template_name=repo.template_dir.name
    )

def scan_tasks_in_worktree(root: Path) -> list[dict]:
    """扫描另一登记 worktree 的 task 状态，不修改 task.py 全局路径。"""
    return _scan_tasks_in_directories(
        root / "docs" / "tasks",
        root / "docs" / "archive" / "tasks",
    )

def git_text_at_ref(
    ref: str, path: str, *, root: Path | None = None,
    repo: "ctx.RepoContext",
) -> str:
    r = _git(["show", f"{ref}:{path}"], root=root, repo=repo)
    if r.returncode != 0:
        raise ctx.TaskDataError(f"{ref}:{path} 不存在或无法读取")
    return r.stdout

def scan_tasks_at_ref(
    ref: str, *, root: Path | None = None, repo: "ctx.RepoContext",
    template_name: str | None = None,
) -> list[dict]:
    """扫描指定 commit/ref 中的 task，不签出、不写工作区。

    与 scan_tasks() 一致：只枚举 docs/tasks 与 docs/archive/tasks 下一级目录，
    每个目录必须存在根 progress.md；嵌套 progress.md 忽略，缺根 progress.md 报数据损坏。
    """
    r = _git([
        "ls-tree", "-r", "--name-only", "--end-of-options", ref, "--",
        "docs/tasks", "docs/archive/tasks",
    ], root=root, repo=repo)
    if template_name is None:
        if repo is None:
            raise ctx.TaskDataError("扫描 ref 需要显式 repo 或 template_name")
        template_name = repo.template_dir.name
    if r.returncode != 0:
        raise ctx.TaskDataError(f"无法读取 ref {ref!r}：{r.stderr.strip()}")
    discovered_dirs: set[tuple[str, str, bool]] = set()
    for path in sorted(r.stdout.splitlines()):
        if path.startswith("docs/tasks/"):
            archived = False
            rel_base = "docs/tasks"
            rest = path[len("docs/tasks/"):]
        elif path.startswith("docs/archive/tasks/"):
            archived = True
            rel_base = "docs/archive/tasks"
            rest = path[len("docs/archive/tasks/"):]
        else:
            continue
        if "/" in rest:
            name = rest.split("/", 1)[0]
            discovered_dirs.add((rel_base, name, archived))
    tasks = []
    for rel_base, name, archived in sorted(discovered_dirs):
        if not archived and name == template_name:
            continue
        directory = f"{rel_base}/{name}"
        path = f"{directory}/{ctx.PROGRESS_FILE}"
        try:
            text = git_text_at_ref(ref, path, root=root, repo=repo)
        except ctx.TaskDataError:
            raise ctx.TaskDataError(f"{ref}:{directory}: 缺 {ctx.PROGRESS_FILE}") from None
        fm, _ = parse_front_matter_text(text, source=f"{ref}:{path}")
        tasks.append(_task_record(
            fm,
            directory=directory,
            source=f"{ref}:{path}",
            archived=archived,
        ))
    return _validate_task_records(tasks)

def find_task(tid: str, tasks: list[dict] | None = None) -> dict | None:
    for t in (scan_tasks() if tasks is None else tasks):
        if t["tid"] == tid:
            return t
    return None

def _task_branch_names(tid: str, *, repo: "ctx.RepoContext") -> list[str]:
    """本地属于该 tid 的 task 分支名列表（形如 {tid}_{slug}）。"""
    r = _git(["branch", "--format=%(refname:short)", "--list", f"{tid}_*"], repo=repo)
    if r.returncode != 0:
        return []
    return [b.strip() for b in r.stdout.splitlines() if ctx.TASK_BRANCH_RE.fullmatch(b.strip())]

def load_owner_task_at_ref(
    tid: str, ref: str, *, root: Path | None = None,
    repo: "ctx.RepoContext",
) -> dict:
    """只读 ref 中该 tid 自身的 task，避免为一条分支扫描全部 task（Issue #77）。"""
    r = _git([
        "ls-tree", "-r", "--name-only", "--end-of-options", ref, "--",
        "docs/tasks", "docs/archive/tasks",
    ], root=root, repo=repo)
    if repo is None:
        raise ctx.TaskDataError("读取 owner task 需要显式 repo")
    template_name = repo.template_dir.name
    if r.returncode != 0:
        raise ctx.TaskDataError(f"无法读取 ref {ref!r}：{r.stderr.strip()}")
    paths = set(r.stdout.splitlines())
    for rel_base, archived in (
        ("docs/tasks", False),
        ("docs/archive/tasks", True),
    ):
        prefix = rel_base + "/"
        for path in sorted(paths):
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix):]
            if "/" not in rest:
                continue
            name = rest.split("/", 1)[0]
            if rel_base == "docs/tasks" and name == template_name:
                continue
            if not name.startswith(f"{tid}_"):
                continue
            directory = f"{rel_base}/{name}"
            note_path = f"{directory}/{ctx.PROGRESS_FILE}"
            if note_path not in paths:
                raise ctx.TaskDataError(f"{ref}:{directory}: 缺 {ctx.PROGRESS_FILE}")
            text = git_text_at_ref(ref, note_path, root=root, repo=repo)
            fm, _ = parse_front_matter_text(
                text, source=f"{ref}:{note_path}"
            )
            return _task_record(
                fm, directory=directory, source=f"{ref}:{note_path}", archived=archived
            )
    raise ctx.TaskNotFoundError(f"{ref}: 缺自身 task {tid}")


def task_effective_state(
    tid: str, primary_fm: dict, *, repo: "ctx.RepoContext",
) -> str | None:
    """main 副本为 backlog 时，探测其被 worktree/未合并分支覆盖的有效状态。

    返回覆盖证据描述；无覆盖返回 None。main 中非 backlog 状态以 main 为准，
    同样返回 None。只读，供 edit/drop 在主仓拒绝操作过期 backlog 副本。
    """
    if primary_fm.get("status") != "backlog":
        return None
    wt_rel = repo.worktree_rel(tid)
    wt_path = (repo.primary_root / wt_rel).resolve()
    registered = worktree_paths(repo=repo).get(str(wt_path))
    if registered:
        task_dir = repo.tasks_dir / f"{tid}_{primary_fm.get('slug', '')}"
        wt_note = wt_path / task_dir.relative_to(repo.root) / ctx.PROGRESS_FILE
        if wt_note.is_file():
            wt_fm, _ = parse_front_matter(wt_note)
            wt_status = wt_fm.get("status", "")
            if wt_status != "backlog":
                return (
                    f"登记 worktree {wt_rel} 中 status={wt_status}"
                    f"（分支 {registered!r}）"
                )
        return f"路径 {wt_rel} 登记为分支 {registered!r} 的 worktree"
    for branch in _task_branch_names(tid, repo=repo):
        if not has_unmerged_commits(branch, repo=repo):
            continue
        try:
            ref_fm = load_owner_task_at_ref(tid, branch, repo=repo)
        except ctx.TaskDataError:
            return f"本地分支 {branch!r} 未合并默认分支"
        ref_status = ref_fm.get("status", "")
        if ref_status != "backlog":
            return f"未合并分支 {branch!r} 中 status={ref_status}"
    return None

def _unmerged_task_branches(*, repo: "ctx.RepoContext") -> list[str]:
    """一次 git 调用取全部未合并 task 分支；失败时回退逐分支保守判定。"""
    branches = _local_task_branches(repo=repo)
    if not branches:
        return []
    base = default_branch(repo=repo)
    r = _git([
        "branch", "--format=%(refname:short)", "--no-merged", base,
        "--list", "t[0-9]*_*",
    ], repo=repo)
    if r.returncode != 0:
        return [branch for branch in branches if has_unmerged_commits(branch, repo=repo)]
    unmerged = {
        branch.strip()
        for branch in r.stdout.splitlines()
        if ctx.TASK_BRANCH_RE.fullmatch(branch.strip())
    }
    return [branch for branch in branches if branch in unmerged]


def _local_task_branches(*, repo: "ctx.RepoContext") -> list[str]:
    r = _git(["branch", "--format=%(refname:short)", "--list", "t[0-9]*_*"], repo=repo)
    if r.returncode != 0:
        return []
    return sorted(
        branch.strip()
        for branch in r.stdout.splitlines()
        if ctx.TASK_BRANCH_RE.fullmatch(branch.strip())
    )

def discover_effective_bundle(
    *, allow_corrupt: bool = False, repo: "ctx.RepoContext",
) -> tuple[dict[str, dict], dict[str, dict]]:
    """一次有效状态发现，同时返回 (tasks, sources)（Issue #77, A05）。

    allow_corrupt=False（默认）时，遇到任何损坏的分支或 worktree 立即抛出 TaskDataError，
    阻断调度与写操作；allow_corrupt=True 仅供只读状态观察入口展示 corrupt 诊断记录。
    """
    tasks: dict[str, dict] = {}
    sources: dict[str, dict] = {}
    for tid, task, source, read_at in _discover_effective_entries(repo=repo):
        if not allow_corrupt and task.get("status") == "corrupt":
            raise ctx.TaskDataError(task.get("note") or f"{tid} 状态损坏（来源 {source}）")
        tasks[tid] = task
        sources[tid] = {
            "tid": tid,
            "slug": task.get("slug", ""),
            "title": task.get("title", ""),
            "status": task.get("status", ""),
            "note": task.get("note", ""),
            "source": source,
            "read_at": read_at,
        }
    return tasks, sources


def discover_effective_tasks(
    *, allow_corrupt: bool = False, repo: "ctx.RepoContext",
) -> dict[str, dict]:
    """按 worktree → 未合并 task 分支 → main 发现每个 task 的有效状态。"""
    return discover_effective_bundle(allow_corrupt=allow_corrupt, repo=repo)[0]


def discover_effective_sources(
    *, allow_corrupt: bool = True, repo: "ctx.RepoContext",
) -> dict[str, dict]:
    """每个有效 task 的状态 + 读取来源与位置。

    返回 tid -> {tid, slug, title, status, note, source, read_at}：
    - source="worktree"：read_at=worktree 绝对路径（在其中读 progress.md / 跑 show）
    - source="branch"：read_at=未合并分支 short name（用 show/list/preflight --ref）
    - source="main"：read_at=None（主干文档即权威）
    只读；供 skill 依「task 状态读取优先级」在正确位置读取 task。
    """
    return discover_effective_bundle(allow_corrupt=allow_corrupt, repo=repo)[1]


def _discover_effective_entries(
    *, repo: "ctx.RepoContext",
) -> list[tuple[str, dict, str, str | None]]:
    """(tid, task, source, read_at) 列表，按 worktree → 未合并分支 → main 优先级。"""
    require_primary_worktree(repo=repo)
    effective = {task["tid"]: task for task in scan_tasks(repo=repo)}
    sources: dict[str, tuple[str, str | None]] = {
        tid: ("main", None) for tid in effective
    }

    for branch in _unmerged_task_branches(repo=repo):
        owner_tid = ctx.TASK_BRANCH_RE.fullmatch(branch).group(1)
        try:
            branch_task = load_owner_task_at_ref(owner_tid, branch, repo=repo)
        except ctx.TaskNotFoundError:
            # 从未合并链尾分叉出的新分支在 finish 提交前，其 git tree 尚不含主干新建的 owner_tid。
            continue
        except ctx.TaskDataError as error:
            effective[owner_tid] = {
                "tid": owner_tid,
                "slug": "",
                "title": f"(分支 {branch} {ctx.PROGRESS_FILE} 损坏)",
                "status": "corrupt",
                "note": f"未合并分支 {branch} 数据损坏：{error}",
            }
            sources[owner_tid] = ("branch", branch)
            continue
        main_task = effective.get(owner_tid)
        # rewind 保留的分支状态过时：main 已显式回 backlog 时，分支 active 不覆盖。
        # worktree 从 start 到 finish 一直存在，无登记 worktree 的 active 分支只来自 rewind。
        if (
            main_task is not None
            and main_task["status"] == "backlog"
            and branch_task["status"] == "active"
        ):
            continue
        effective[owner_tid] = branch_task
        sources[owner_tid] = ("branch", branch)

    primary = primary_worktree_path(repo=repo)
    for path_text, branch in worktree_paths(repo=repo).items():
        path = Path(path_text).resolve()
        if path == primary or not path.is_dir():
            continue
        match = ctx.TASK_BRANCH_RE.fullmatch(branch)
        if not match:
            continue
        owner_tid = match.group(1)
        # 单个 worktree 的 progress.md 损坏（agent 写一半崩溃）标记为显式 corrupt 状态，
        # 不伪装为正常 active，观察入口可见而调度/写操作阻断（A05）。
        try:
            tasks = scan_tasks_in_worktree(path)
            task = next((item for item in tasks if item["tid"] == owner_tid), None)
        except (OSError, ctx.TaskDataError) as error:
            effective[owner_tid] = {
                "tid": owner_tid,
                "slug": "",
                "title": f"(worktree {ctx.PROGRESS_FILE} 损坏)",
                "status": "corrupt",
                "note": f"登记 worktree {path} 的 {ctx.PROGRESS_FILE} 无法解析：{error}",
            }
            sources[owner_tid] = ("worktree", str(path))
            continue
        if task is None:
            effective[owner_tid] = {
                "tid": owner_tid,
                "slug": "",
                "title": "(worktree 缺自身 task)",
                "status": "corrupt",
                "note": f"登记 worktree {path} 缺自身 task {owner_tid}",
            }
            sources[owner_tid] = ("worktree", str(path))
            continue
        effective[owner_tid] = task
        sources[owner_tid] = ("worktree", str(path))
    return [(tid, task, *sources[tid]) for tid, task in effective.items()]

def load_task(
    tid: str, *, repo: "ctx.RepoContext",
) -> tuple[dict, Path, dict, str]:
    """返回 (索引记录, progress.md 路径, front matter, 正文)。"""
    task = find_task(tid, scan_tasks(repo=repo))
    if not task:
        sys.exit(
            f"{tid} 不存在于当前工作区（{repo.rel(repo.root) or repo.name}）。"
            "进行中 task 的文档随其 worktree，请在对应 worktree 内执行"
        )
    path = repo.root / task["dir"] / ctx.PROGRESS_FILE
    fm, body = parse_front_matter(path)
    return task, path, fm, body

def load_task_at_ref(
    tid: str, ref: str, *, root: Path | None = None,
    repo: "ctx.RepoContext",
) -> tuple[dict, dict, str]:
    """返回指定 ref 中的 (索引记录, front matter, 正文)。"""
    task = find_task(tid, scan_tasks_at_ref(ref, root=root, repo=repo))
    if not task:
        raise ctx.TaskNotFoundError(f"{tid} 不存在于 ref {ref!r}")
    path = f"{task['dir']}/{ctx.PROGRESS_FILE}"
    fm, body = parse_front_matter_text(
        git_text_at_ref(ref, path, root=root, repo=repo),
        source=f"{ref}:{path}",
    )
    return task, fm, body


def _resolve_commit_sha(
    ref: str, *, label: str, root: Path | None = None,
    repo: "ctx.RepoContext",
) -> str:
    if not ref or ref.startswith("-"):
        raise ctx.TaskDataError(f"无法解析 {label} {ref!r} 为 commit")
    r = _git(
        ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"],
        root=root, repo=repo,
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise ctx.TaskDataError(f"无法解析 {label} {ref!r} 为 commit")
    return r.stdout.strip()


def _resolve_primary_commit(
    root: Path | None = None, *, repo: "ctx.RepoContext",
) -> tuple[str, str]:
    primary = default_branch(root=root, repo=repo)
    sha = _resolve_commit_sha(
        f"refs/heads/{primary}", label=f"主干分支 {primary!r}", root=root, repo=repo
    )
    return primary, sha


def _owner_bundle_at_ref(
    tid: str, ref_sha: str, *, root: Path | None = None,
    repo: "ctx.RepoContext",
) -> tuple[dict, dict, str]:
    record = load_owner_task_at_ref(tid, ref_sha, root=root, repo=repo)
    path = f"{record['dir']}/{ctx.PROGRESS_FILE}"
    fm, body = parse_front_matter_text(
        git_text_at_ref(ref_sha, path, root=root, repo=repo),
        source=f"{ref_sha}:{path}",
    )
    return record, fm, body


def load_task_backlog_baseline(
    tid: str, *, root: Path | None = None,
    repo: "ctx.RepoContext",
) -> tuple[dict, dict, str, str]:
    """解析 task 启动时的初始 backlog 定义 (task, fm, body, source_commit_sha)。

    Issue #90：主干 default_branch 是所有 backlog task 创建与调度维护的唯一权威。
    只从主干 backlog 单源读取并锁定主干 commit SHA；
    缺失、状态不符和数据损坏明确报错，不回退到代码 anchor。
    """
    primary_branch, primary_sha = _resolve_primary_commit(root=root, repo=repo)
    task, fm, body = _owner_bundle_at_ref(tid, primary_sha, root=root, repo=repo)
    if fm.get("status") != "backlog":
        raise ctx.TaskDataError(
            f"{tid} 在主干 {primary_branch!r} ({primary_sha[:12]}) 中 "
            f"status={fm.get('status')!r} 非 backlog"
        )
    return task, fm, body, primary_sha


def _path_exists_in_tree(
    ref_sha: str, rel_path: str, *, root: Path | None = None,
    repo: "ctx.RepoContext",
) -> bool:
    r = _git(
        ["ls-tree", "--name-only", "--end-of-options", ref_sha, "--", rel_path],
        root=root, repo=repo,
    )
    if r.returncode != 0:
        raise ctx.TaskDataError(f"无法读取 {ref_sha}:{rel_path}：{r.stderr.strip()}")
    return bool(r.stdout.strip())


def resolve_baseline_spec_text(
    tid: str, spec_rel: str, spec_source_sha: str, *, root: Path | None = None,
    repo: "ctx.RepoContext | None" = None,
) -> tuple[str, str]:
    """解析 spec 的契约基线文本与来源 commit SHA，供 render_review_prompts 与监控共用（A04/C90）。

    Issue #90：读取启动时锁定的固定来源 commit SHA (spec_source_sha)；
    验证该位置的初始 backlog 状态；缺失、空值、非 backlog、Git 故障或数据损坏明确报错，
    不回退到 diff_anchor 猜测。
    """
    repo = repo or ctx.RepoContext.for_root(root or Path.cwd())
    if not spec_source_sha or not isinstance(spec_source_sha, str):
        raise ctx.TaskDataError(f"spec_source_sha 缺失或为空（got {spec_source_sha!r}）")
    source_sha = _resolve_commit_sha(spec_source_sha, label="spec_source_sha", root=root, repo=repo)
    progress_path = f"{Path(spec_rel).parent.as_posix()}/{ctx.PROGRESS_FILE}"
    if not _path_exists_in_tree(source_sha, progress_path, root=root, repo=repo):
        raise ctx.TaskNotFoundError(f"{source_sha[:12]}:{progress_path} 不存在或无法读取")
    source_text = git_text_at_ref(source_sha, progress_path, root=root, repo=repo)
    source_fm, _ = parse_front_matter_text(source_text, source=f"{source_sha}:{progress_path}")
    if source_fm.get("status") != "backlog":
        raise ctx.TaskDataError(
            f"spec_source_sha {source_sha[:12]} 处 {tid} status={source_fm.get('status')!r} 非 backlog"
        )
    if not _path_exists_in_tree(source_sha, spec_rel, root=root, repo=repo):
        raise ctx.TaskNotFoundError(f"{source_sha[:12]}:{spec_rel} 不存在或无法读取")
    return git_text_at_ref(source_sha, spec_rel, root=root, repo=repo), source_sha

def require_status(fm: dict, *allowed: str) -> None:
    if fm["status"] not in allowed:
        sys.exit(f"{fm['tid']} status={fm['status']}，需要 {allowed}")

def append_note(fm: dict, text: str) -> None:
    fm["note"] = f"{fm['note']}; {text}" if fm.get("note") else text

def append_audit(action: str, *, tid: str, fr: str, to: str, reason: str,
                 slug: str = "", title: str = "",
                 repo: "ctx.RepoContext") -> None:
    """append 一行审计。字段内的 `|` 与换行会被替换，保证行格式可 grep。"""
    def clean(value: str) -> str:
        return re.sub(r"\s+", " ", str(value).replace("|", "/")).strip()

    repo.audit_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(ctx.TZ_CN).isoformat(timespec="seconds")
    parts = [ts, action, f"tid={tid}", f"from={fr}", f"to={to}", f"head={_get_head_short(repo=repo)}"]
    if slug:
        parts.append(f"slug={clean(slug)}")
    if title:
        parts.append(f"title={clean(title)}")
    parts.append(f"reason={clean(reason)}")
    try:
        with repo.audit_path.open("a", encoding="utf-8") as f:
            f.write(" | ".join(parts) + "\n")
    except OSError as error:
        # fail-closed：审计失败抛 TaskDataError，调用方据此决定回滚/中止，
        # 避免「状态已迁移、审计缺失、不可重试」（F15）
        raise ctx.TaskDataError(
            f"审计写入失败（{action} tid={tid}）：{error}"
        ) from error
