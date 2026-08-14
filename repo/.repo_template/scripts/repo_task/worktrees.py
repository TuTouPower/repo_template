"""Canonical worktrees implementation for the task toolchain."""

import os
import re
from pathlib import Path

import repo_task.context as ctx

from .git_ops import _get_head, _git, current_branch, default_branch, resolve_local_branch, worktree_paths
from .store import load_task_at_ref

def link_local_env(worktree: Path) -> list[str]:
    """把主仓未入库的 .env 软链进 worktree（同相对路径）。

    用 rglob 覆盖任意层级嵌套；跳过 .git、node_modules、.scratch 等
    噪声目录，避免递归进依赖/缓存树。先物化源列表再建链，防止 rglob
    在遍历中把刚创建的软链当新源导致级联嵌套。
    """
    linked = []
    skip_dirs = {".git", "node_modules", ".scratch", "__pycache__", "venv", ".venv", "target"}
    try:
        worktree_resolved = worktree.resolve()
    except OSError:
        worktree_resolved = None
    sources = [
        src for src in ctx.REPO_ROOT.rglob(".env")
        if src.is_file()
    ]
    for src in sources:
        try:
            rel = src.relative_to(ctx.REPO_ROOT)
        except ValueError:
            continue
        if any(part in skip_dirs for part in rel.parts[:-1]):
            continue
        if worktree_resolved is not None:
            try:
                src.resolve().relative_to(worktree_resolved)
                continue  # 跳过目标 worktree 自身内的 .env
            except ValueError:
                pass
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
    if rel.name != ".env":
        return False
    source = ctx.REPO_ROOT / rel
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
    # 用 os.walk 而非 Path.rglob：rglob 在 Python 3.10+ 会跳过 dangling
    # symlink（源已删的 .env 链接），清理时恰恰需要删除这类残留。
    for root, dirs, files in os.walk(worktree, followlinks=False):
        if ".env" in files:
            link = Path(root) / ".env"
            if is_managed_env_link(worktree, link):
                link.unlink()

def resolve_start_base(base_arg: str | None) -> tuple[str, str]:
    """解析 start base。

    `task-run` 链式：后一个 task 从上一个已完成 task 分支创建，传 `--base`。
    多会话手动并发：用户未传 `--base` 且最新前置未合并主干时，由 `cmd_start`
    自动落到前置分支 tip（见 integration.py）；否则从主干 HEAD 创建。
    """
    primary = default_branch()
    base_branch, base_sha = resolve_local_branch(base_arg or primary)
    if base_branch == primary:
        if current_branch() != primary or _get_head() != base_sha:
            raise ctx.TaskDataError(f"主工作区 HEAD 与本地 {primary!r} 不一致")
        return base_branch, base_sha

    match = ctx.TASK_BRANCH_RE.fullmatch(base_branch)
    if not match:
        raise ctx.TaskDataError(
            f"--base 只接受默认分支或本地 task 分支（收到 {base_branch!r}）"
        )
    previous_tid = match.group(1)
    _, previous_fm, _ = load_task_at_ref(previous_tid, base_sha)
    expected_branch = f"{previous_tid}_{previous_fm.get('slug', '')}"
    if base_branch != expected_branch:
        raise ctx.TaskDataError(
            f"--base 分支名 {base_branch!r} 与 {previous_tid} slug 不符"
            f"（应为 {expected_branch!r}）；拒绝伪装成 task 分支的普通分支"
        )
    if previous_fm.get("status") not in ctx.ARCHIVED_STATUSES:
        raise ctx.TaskDataError(
            f"--base {base_branch!r} 对应 {previous_tid} status="
            f"{previous_fm.get('status')!r}，须先完成或 drop"
        )
    registered = [path for path, branch in worktree_paths().items() if branch == base_branch]
    if registered:
        raise ctx.TaskDataError(
            f"--base {base_branch!r} 仍登记 worktree：{', '.join(registered)}；"
            "先 cleanup-worktree"
        )
    return base_branch, base_sha

def create_worktree(tid: str, branch: str, base_sha: str) -> str:
    """从固定 base SHA 创建全新的 task branch/worktree；调用方负责补偿失败。"""
    rel = ctx.worktree_rel_path(tid)
    path = (ctx.REPO_ROOT / rel).resolve()
    if path.exists() or str(path) in worktree_paths():
        raise ctx.TaskDataError(f"{rel} 已存在；请先清理后再 start")
    if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode == 0:
        raise ctx.TaskDataError(f"分支 {branch!r} 已存在；请先处理后再 start")
    r = _git(["worktree", "add", "-b", branch, str(path), base_sha])
    if r.returncode != 0:
        raise ctx.TaskDataError(f"git worktree add 失败：{r.stderr.strip()}")
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
    worktree = (ctx.REPO_ROOT / worktree_rel).resolve()
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
    path = (ctx.REPO_ROOT / rel).resolve()
    if str(path) not in worktree_paths():
        prune = _git(["worktree", "prune"])
        if prune.returncode != 0:
            return (
                False,
                f"git worktree prune 失败（{prune.stderr.strip()}）；"
                f"无法确认 {rel} 清理状态",
            )
        return True, f"worktree 不在登记表，已 prune：{rel}"
    if Path.cwd().resolve().is_relative_to(path):
        return False, f"当前目录在 {rel} 内，无法移除；请 cd 回主仓"
    unlink_managed_env_links(path)
    r = _git(["worktree", "remove", "--force", str(path)])
    if r.returncode != 0:
        return False, f"git worktree remove --force 失败（{r.stderr.strip()}）"
    prune = _git(["worktree", "prune"])
    if prune.returncode != 0:
        return (
            False,
            f"worktree 已移除但 prune 失败（{prune.stderr.strip()}）；请手动 prune {rel}",
        )
    return True, f"worktree 已强制移除：{rel}"

def remove_worktree(rel: str, *, expected_branch: str | None = None) -> tuple[bool, str]:
    """返回 (是否已确实移除, 说明)。失败不代表流程必须中断，由调用方决定。

    expected_branch 非空时校验登记分支归属，防止 front matter 损坏/冲突解决
    错误导致一个 task 清理另一个 task 的工作区。
    """
    if not rel:
        return True, "无 worktree"
    path = (ctx.REPO_ROOT / rel).resolve()
    registered_branch = worktree_paths().get(str(path))
    if registered_branch is None:
        prune = _git(["worktree", "prune"])
        if prune.returncode != 0:
            return (
                False,
                f"git worktree prune 失败（{prune.stderr.strip()}）；"
                f"无法确认 {rel} 清理状态",
            )
        return True, f"worktree 不在登记表，已 prune：{rel}"
    if expected_branch and registered_branch != expected_branch:
        return False, (
            f"{rel} 登记分支为 {registered_branch!r}，预期 {expected_branch!r}；"
            "拒绝移除其他 task 的 worktree"
        )
    if Path.cwd().resolve().is_relative_to(path):
        return False, f"当前目录在 {rel} 内，无法移除；请 cd 出去后执行 git worktree remove {rel}"
    unlink_managed_env_links(path)
    r = _git(["worktree", "remove", str(path)])
    if r.returncode != 0:
        return False, f"git worktree remove 失败（{r.stderr.strip()}）；请手动处理 {rel}"
    prune = _git(["worktree", "prune"])
    if prune.returncode != 0:
        return (
            False,
            f"worktree 已移除但 prune 失败（{prune.stderr.strip()}）；请手动 prune {rel}",
        )
    return True, f"worktree 已移除：{rel}"
