"""Canonical git_ops implementation for the task toolchain."""

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

import repo_task.context as ctx


def _git(args: list, *, root: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root or ctx.REPO_ROOT), *args],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _git_bytes(args: list[str], *, root: Path | None = None) -> subprocess.CompletedProcess:
    """Run Git without text decoding for binary-safe snapshots and diffs."""
    return subprocess.run(
        ["git", "-C", str(root or ctx.REPO_ROOT), *args],
        capture_output=True,
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
        raise ctx.TaskDataError(f"本地分支名非法：{name!r}")
    ref = f"refs/heads/{name}"
    if _git(["show-ref", "--verify", "--quiet", ref]).returncode != 0:
        raise ctx.TaskDataError(f"本地分支不存在：{name!r}")
    r = _git(["rev-parse", f"{ref}^{{commit}}"])
    if r.returncode != 0 or not r.stdout.strip():
        raise ctx.TaskDataError(f"无法解析本地分支 {name!r} 的 HEAD")
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
    """`git worktree list --porcelain` → {绝对路径: 分支名}。

    键统一为 Path.resolve() 后的字符串，与所有调用方 str(path) 比较对齐。
    git 输出正斜杠路径，Path.resolve() 在 Windows 给反斜杠——规范化消除差异。
    """
    result, current = {}, None
    r = _git(["worktree", "list", "--porcelain"])
    if r.returncode != 0:
        return result
    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            raw = line[len("worktree "):].strip()
            current = str(Path(raw).resolve()) if raw else None
            if current:
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
    return ctx.REPO_ROOT.resolve() == primary_worktree_path()

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
    return (ctx.REPO_ROOT / ctx.effective_worktree(fm)).resolve()

def in_own_task_worktree(fm: dict) -> bool:
    expected = task_worktree_path(fm)
    return (
        ctx.REPO_ROOT.resolve() == expected
        and str(expected) in worktree_paths()
        and current_branch() == fm.get("branch", "")
    )

def require_own_task_worktree(fm: dict) -> None:
    if not in_own_task_worktree(fm):
        sys.exit(
            f"{fm['tid']} 必须在自身 worktree {ctx.effective_worktree(fm)} 的分支 "
            f"{fm.get('branch')!r} 执行"
        )
