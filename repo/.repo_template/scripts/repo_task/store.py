"""Canonical store implementation for the task toolchain."""

from __future__ import annotations

import copy
import json
import os
import re
import sys
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import repo_task.context as ctx

from .documents import atomic_write_text, parse_front_matter, parse_front_matter_text, parse_tid_list
from .git_ops import (
    _get_head_short,
    _git,
    batch_cat_file,
    default_branch,
    has_unmerged_commits,
    primary_worktree_path,
    require_primary_worktree,
    worktree_paths,
)


@dataclass(frozen=True)
class CommitTaskSnapshot:
    commit_sha: str
    tasks: tuple[dict, ...]
    task_by_tid: dict[str, dict]
    document_by_tid: dict[str, tuple[dict, str]]


class CommitSnapshotCache:
    """操作内确定 commit 只读解析结果缓存。

    显式注入到调用链，不设模块级全局实例；有界 LRU 防止长操作内内存单调增长。
    repo_key 由调用方带上模板名，同一 commit 换模板名不会命中旧快照。
    """

    def __init__(self, max_entries: int = 256) -> None:
        self._max_entries = max_entries
        self._commit_cache: OrderedDict[tuple[str, str], CommitTaskSnapshot] = OrderedDict()
        self._owner_cache: OrderedDict[tuple[str, str, str], tuple[dict, dict, str]] = OrderedDict()

    @staticmethod
    def _get(store: OrderedDict, key):
        if key not in store:
            return None
        store.move_to_end(key)
        return store[key]

    def _set(self, store: OrderedDict, key, value) -> None:
        store[key] = value
        store.move_to_end(key)
        while len(store) > self._max_entries:
            store.popitem(last=False)

    def get_commit(self, repo_key: str, sha: str) -> CommitTaskSnapshot | None:
        return self._get(self._commit_cache, (repo_key, sha))

    def set_commit(self, repo_key: str, sha: str, snapshot: CommitTaskSnapshot) -> None:
        self._set(self._commit_cache, (repo_key, sha), snapshot)

    def get_owner(self, repo_key: str, sha: str, tid: str) -> tuple[dict, dict, str] | None:
        return self._get(self._owner_cache, (repo_key, sha, tid))

    def set_owner(self, repo_key: str, sha: str, tid: str, bundle: tuple[dict, dict, str]) -> None:
        self._set(self._owner_cache, (repo_key, sha, tid), bundle)

    def clear(self) -> None:
        self._commit_cache.clear()
        self._owner_cache.clear()


def _cache_key(target: Path, template_name: str) -> str:
    """缓存键含模板名，避免同一 commit 换模板名返回错误快照（DS L214）。"""
    return f"{target.resolve()}|{template_name}"


def task_schedule_references(
    target_tid: str, tasks: list[dict] | None = None, *, repo: "ctx.RepoContext | None" = None,
) -> list[str]:
    """返回非终态 task 中引用 target_tid 的字段，供 drop/purge 拒绝悬空边。

    只扫活跃目录：done/dropped/integrated task 的历史边无脚本清理途径，
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
        if task["status"] in ctx.TERMINAL_STATUSES:
            continue
        for field in ("depends_on", "conflicts_with"):
            tids = parse_tid_list(task.get(field, ""), field=f"{task['tid']}.{field}")
            if target_tid in tids:
                references.append(f"{task['tid']}.{field}")
    return references


def task_record(fm: dict, *, directory: str, source: str) -> dict:
    """校验 task front matter 与目录身份，返回统一状态记录。"""
    tid = fm.get("tid", "")
    if not ctx.TID_RE.match(tid):
        raise ctx.TaskDataError(f"{source}: front matter tid 非法（{tid!r}）")
    expected = f"{tid}_{fm.get('slug', '')}"
    if Path(directory).name != expected:
        raise ctx.TaskDataError(f"{directory}: 目录名与 front matter 不符（应为 {expected}）")
    status = fm.get("status", "")
    if status not in ctx.VALID_STATUSES:
        raise ctx.TaskDataError(f"{source}: status 非法（{status!r}）")
    record = {k: fm.get(k, "") for k in ctx.FRONT_MATTER_KEYS}
    record["dir"] = directory
    return record

def _validate_task_records(tasks: list[dict]) -> list[dict]:
    dup = [tid for tid, n in Counter(t["tid"] for t in tasks).items() if n > 1]
    if dup:
        raise ctx.TaskDataError(f"重复 tid：{sorted(dup)}")
    tasks.sort(key=lambda t: int(ctx.TID_RE.match(t["tid"]).group(1)))
    return tasks

def _scan_tasks_in_directory(
    tasks_dir: Path, *, template_name: str = "task_template",
) -> list[dict]:
    tasks = []
    if not tasks_dir.is_dir():
        return []
    for directory in sorted(tasks_dir.iterdir()):
        if not directory.is_dir() or directory.name == template_name:
            continue
        note_md = directory / ctx.PROGRESS_FILE
        if not note_md.is_file():
            raise ctx.TaskDataError(f"{note_md.parent}: 缺 {ctx.PROGRESS_FILE}")
        fm, _ = parse_front_matter_text(
            note_md.read_text(encoding="utf-8"), source=str(note_md)
        )
        tasks.append(task_record(
            fm,
            directory=f"docs/tasks/{directory.name}",
            source=str(note_md),
        ))
    return _validate_task_records(tasks)

def scan_tasks(*, repo: "ctx.RepoContext") -> list[dict]:
    """扫描当前工作区 task，按 tid 升序返回状态记录。"""
    return _scan_tasks_in_directory(
        repo.tasks_dir, template_name=repo.template_dir.name
    )

def scan_tasks_in_worktree(root: Path) -> list[dict]:
    """扫描另一登记 worktree 的 task 状态，不修改 task.py 全局路径。"""
    return _scan_tasks_in_directory(
        root / "docs" / "tasks",
    )

def git_text_at_ref(
    ref: str, path: str, *, root: Path | None = None,
    repo: "ctx.RepoContext",
) -> str:
    r = _git(["show", f"{ref}:{path}"], root=root, repo=repo)
    if r.returncode != 0:
        raise ctx.TaskDataError(f"{ref}:{path} 不存在或无法读取")
    return r.stdout

def _load_tasks_at_commit(
    commit_sha: str,
    *,
    ref_label: str | None = None,
    root: Path | None = None,
    repo: "ctx.RepoContext | None" = None,
    template_name: str | None = None,
    cache: CommitSnapshotCache | None = None,
) -> CommitTaskSnapshot:
    target = root or (repo.root if repo is not None else None)
    if target is None:
        raise ctx.TaskDataError("读取 commit tasks 需要显式 repo 或 root")
    if template_name is None:
        if repo is None:
            raise ctx.TaskDataError("扫描 ref 需要显式 repo 或 template_name")
        template_name = repo.template_dir.name
    repo_key = _cache_key(target, template_name)
    cache_store = cache if cache is not None else CommitSnapshotCache()

    cached = cache_store.get_commit(repo_key, commit_sha)
    if cached is not None:
        return cached

    display_ref = ref_label or commit_sha
    r = _git(
        [
            "ls-tree", "-r", "-z", "--end-of-options", commit_sha, "--",
            "docs/tasks",
        ],
        root=root, repo=repo,
    )
    if r.returncode != 0:
        raise ctx.TaskDataError(f"无法读取 ref {display_ref!r}：{r.stderr.strip()}")

    tree_blobs: dict[str, str] = {}
    discovered_dirs: set[str] = set()

    for item in r.stdout.split("\0"):
        if not item:
            continue
        meta, path = item.split("\t", 1)
        parts = meta.split()
        if len(parts) >= 3 and parts[1] == "blob":
            tree_blobs[path] = parts[2]

        if path.startswith("docs/tasks/"):
            rest = path[len("docs/tasks/") :]
            if "/" in rest:
                name = rest.split("/", 1)[0]
                discovered_dirs.add(name)

    needed_notes: list[tuple[str, str, str]] = []
    for name in sorted(discovered_dirs):
        if name == template_name:
            continue
        directory = f"docs/tasks/{name}"
        progress_path = f"{directory}/{ctx.PROGRESS_FILE}"
        oid = tree_blobs.get(progress_path)
        if not oid:
            raise ctx.TaskDataError(f"{display_ref}:{directory}: 缺 {ctx.PROGRESS_FILE}")
        needed_notes.append((directory, progress_path, oid))

    oids = [oid for _, _, oid in needed_notes]
    blob_map = batch_cat_file(oids, root=root, repo=repo)

    tasks: list[dict] = []
    task_by_tid: dict[str, dict] = {}
    document_by_tid: dict[str, tuple[dict, str]] = {}

    for directory, progress_path, oid in needed_notes:
        raw_content = blob_map.get(oid)
        if raw_content is None:
            raise ctx.TaskDataError(f"{display_ref}:{directory}: 缺 {ctx.PROGRESS_FILE}")
        text = raw_content.decode("utf-8", errors="replace")
        fm, body = parse_front_matter_text(text, source=f"{display_ref}:{progress_path}")
        record = task_record(
            fm,
            directory=directory,
            source=f"{display_ref}:{progress_path}",
        )
        tid = record["tid"]
        tasks.append(record)
        task_by_tid[tid] = record
        document_by_tid[tid] = (fm, body)

    validated_tasks = _validate_task_records(tasks)
    snapshot = CommitTaskSnapshot(
        commit_sha=commit_sha,
        tasks=tuple(validated_tasks),
        task_by_tid={t["tid"]: t for t in validated_tasks},
        document_by_tid=document_by_tid,
    )
    cache_store.set_commit(repo_key, commit_sha, snapshot)
    return snapshot


def scan_tasks_at_ref(
    ref: str, *, root: Path | None = None, repo: "ctx.RepoContext",
    template_name: str | None = None,
    cache: CommitSnapshotCache | None = None,
) -> list[dict]:
    """扫描指定 commit/ref 中的 task，不签出、不写工作区。

    与 scan_tasks() 一致：只枚举 docs/tasks 下一级目录，
    每个目录必须存在根 progress.md；嵌套 progress.md 忽略，缺根 progress.md 报数据损坏。
    """
    commit_sha = _resolve_commit_sha(ref, label=f"ref {ref!r}", root=root, repo=repo)
    snapshot = _load_tasks_at_commit(
        commit_sha,
        ref_label=ref,
        root=root,
        repo=repo,
        template_name=template_name,
        cache=cache,
    )
    return [copy.deepcopy(t) for t in snapshot.tasks]


def find_task(tid: str, tasks: list[dict]) -> dict | None:
    for t in tasks:
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
    cache: CommitSnapshotCache | None = None,
) -> dict:
    """只读 ref 中该 tid 自身的 task，避免为一条分支扫描全部 task（Issue #77）。"""
    if repo is None:
        raise ctx.TaskDataError("读取 owner task 需要显式 repo")
    target = root or repo.root
    template_name = repo.template_dir.name
    repo_key = _cache_key(target, template_name)
    cache_store = cache if cache is not None else CommitSnapshotCache()

    commit_sha = None
    if ref and not ref.startswith("-"):
        r_sha = _git(
            ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"],
            root=root, repo=repo,
        )
        if r_sha.returncode == 0 and r_sha.stdout.strip():
            commit_sha = r_sha.stdout.strip()

    if commit_sha is not None:
        snap = cache_store.get_commit(repo_key, commit_sha)
        if snap is not None:
            task = snap.task_by_tid.get(tid)
            if task is not None:
                return copy.deepcopy(task)
            raise ctx.TaskNotFoundError(f"{ref}: 缺自身 task {tid}")

        owner_bundle = cache_store.get_owner(repo_key, commit_sha, tid)
        if owner_bundle is not None:
            return copy.deepcopy(owner_bundle[0])

    r = _git([
        "ls-tree", "-r", "-z", "--end-of-options", ref, "--",
        "docs/tasks",
    ], root=root, repo=repo)
    if r.returncode != 0:
        raise ctx.TaskDataError(f"无法读取 ref {ref!r}：{r.stderr.strip()}")
    tree_blobs: dict[str, str] = {}
    paths: set[str] = set()
    for item in r.stdout.split("\0"):
        if not item:
            continue
        meta, p = item.split("\t", 1)
        paths.add(p)
        parts = meta.split()
        if len(parts) >= 3 and parts[1] == "blob":
            tree_blobs[p] = parts[2]

    prefix = "docs/tasks/"
    for path in sorted(paths):
        if not path.startswith(prefix):
            continue
        rest = path[len(prefix):]
        if "/" not in rest:
            continue
        name = rest.split("/", 1)[0]
        if name == template_name:
            continue
        if not name.startswith(f"{tid}_"):
            continue
        directory = f"docs/tasks/{name}"
        note_path = f"{directory}/{ctx.PROGRESS_FILE}"
        if note_path not in paths:
            raise ctx.TaskDataError(f"{ref}:{directory}: 缺 {ctx.PROGRESS_FILE}")
        oid = tree_blobs.get(note_path)
        if not oid:
            raise ctx.TaskDataError(f"{ref}:{directory}: 缺 {ctx.PROGRESS_FILE}")
        raw_map = batch_cat_file([oid], root=root, repo=repo)
        raw = raw_map.get(oid)
        if raw is None:
            raise ctx.TaskDataError(f"{ref}:{directory}: 缺 {ctx.PROGRESS_FILE}")
        text = raw.decode("utf-8", errors="replace")
        fm, body = parse_front_matter_text(
            text, source=f"{ref}:{note_path}"
        )
        record = task_record(
            fm, directory=directory, source=f"{ref}:{note_path}"
        )
        if commit_sha is not None:
            cache_store.set_owner(repo_key, commit_sha, tid, (record, fm, body))
        return record
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
    cache = CommitSnapshotCache()
    for tid, task, source, read_at in _discover_effective_entries(repo=repo, cache=cache):
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
    *, repo: "ctx.RepoContext", cache: CommitSnapshotCache,
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
            branch_task = load_owner_task_at_ref(owner_tid, branch, repo=repo, cache=cache)
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
    cache: CommitSnapshotCache | None = None,
) -> tuple[dict, dict, str]:
    """返回指定 ref 中的 (索引记录, front matter, 正文)。"""
    commit_sha = _resolve_commit_sha(ref, label=f"ref {ref!r}", root=root, repo=repo)
    snapshot = _load_tasks_at_commit(
        commit_sha,
        ref_label=ref,
        root=root,
        repo=repo,
        cache=cache,
    )
    task = snapshot.task_by_tid.get(tid)
    if not task:
        raise ctx.TaskNotFoundError(f"{tid} 不存在于 ref {ref!r}")
    fm, body = snapshot.document_by_tid[tid]
    return copy.deepcopy(task), copy.deepcopy(fm), body


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
    cache: CommitSnapshotCache | None = None,
) -> tuple[dict, dict, str]:
    target = root or repo.root
    repo_key = _cache_key(target, repo.template_dir.name)
    cache_store = cache if cache is not None else CommitSnapshotCache()

    snap = cache_store.get_commit(repo_key, ref_sha)
    if snap is not None:
        task = snap.task_by_tid.get(tid)
        if task is not None and tid in snap.document_by_tid:
            fm, body = snap.document_by_tid[tid]
            return copy.deepcopy(task), copy.deepcopy(fm), body
        raise ctx.TaskNotFoundError(f"{ref_sha}: 缺自身 task {tid}")

    owner_bundle = cache_store.get_owner(repo_key, ref_sha, tid)
    if owner_bundle is not None:
        record, fm, body = owner_bundle
        return copy.deepcopy(record), copy.deepcopy(fm), body

    record = load_owner_task_at_ref(tid, ref_sha, root=root, repo=repo, cache=cache_store)
    owner_bundle = cache_store.get_owner(repo_key, ref_sha, tid)
    if owner_bundle is not None:
        r, f, b = owner_bundle
        return copy.deepcopy(r), copy.deepcopy(f), b

    path = f"{record['dir']}/{ctx.PROGRESS_FILE}"
    fm, body = parse_front_matter_text(
        git_text_at_ref(ref_sha, path, root=root, repo=repo),
        source=f"{ref_sha}:{path}",
    )
    cache_store.set_owner(repo_key, ref_sha, tid, (record, fm, body))
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

    r = _git(
        ["ls-tree", "-z", "--end-of-options", source_sha, "--", progress_path, spec_rel],
        root=root, repo=repo,
    )
    if r.returncode != 0:
        raise ctx.TaskDataError(f"无法读取 {source_sha}:{progress_path}：{r.stderr.strip()}")

    blobs: dict[str, str] = {}
    for item in r.stdout.split("\0"):
        if not item:
            continue
        meta, p = item.split("\t", 1)
        parts = meta.split()
        if len(parts) >= 3 and parts[1] == "blob":
            blobs[p] = parts[2]

    if progress_path not in blobs:
        raise ctx.TaskNotFoundError(f"{source_sha[:12]}:{progress_path} 不存在或无法读取")

    needed_oids = [blobs[progress_path]]
    if spec_rel in blobs:
        needed_oids.append(blobs[spec_rel])

    cat_map = batch_cat_file(needed_oids, root=root, repo=repo)
    prog_bytes = cat_map.get(blobs[progress_path])
    if prog_bytes is None:
        raise ctx.TaskNotFoundError(f"{source_sha[:12]}:{progress_path} 不存在或无法读取")

    source_text = prog_bytes.decode("utf-8", errors="replace")
    source_fm, _ = parse_front_matter_text(source_text, source=f"{source_sha}:{progress_path}")
    if source_fm.get("status") != "backlog":
        raise ctx.TaskDataError(
            f"spec_source_sha {source_sha[:12]} 处 {tid} status={source_fm.get('status')!r} 非 backlog"
        )

    if spec_rel not in blobs:
        raise ctx.TaskNotFoundError(f"{source_sha[:12]}:{spec_rel} 不存在或无法读取")
    spec_bytes = cat_map.get(blobs[spec_rel])
    if spec_bytes is None:
        raise ctx.TaskNotFoundError(f"{source_sha[:12]}:{spec_rel} 不存在或无法读取")

    return spec_bytes.decode("utf-8", errors="replace"), source_sha

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
