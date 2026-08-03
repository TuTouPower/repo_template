#!/usr/bin/env python3
"""_id_scan.py - 跨 worktree 跨分支的条目编号扫描与互斥分配。

pending.py / findings.py 共用。条目以「一条目一文件」形式存放，文件名形如
`p047_cli_exit_code.md` / `d012_uv_lock_marker.md`，编号直接来自文件名，无需解析正文。

扫描来源：
- 所有本地分支的 git 树（`git ls-tree`）；
- 所有登记 worktree 的工作区文件（含未提交条目）。

重复检测语义：
- 同一来源（单个分支或单个 worktree）内同号出现在多个状态目录时报错——迁移残留需立即暴露。
- 跨来源重复不报——并行 task worktree 各自含主干历史，合并前允许暂时重复。

互斥分配：
`allocate` 在 git 公共目录的锁文件上取排他锁，锁内完成「扫描取号 → 建文件」。
释放锁时新条目已落盘，后续扫描必然可见，不存在两个进程取到同号的窗口。
"""

import fcntl
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path

LOCK_NAME = "repo_template_id.lock"


class IdScanError(ValueError):
    """编号扫描或分配失败。"""


def _run_git(repo_root: Path, args: list[str]) -> str:
    """运行 git 子进程；失败统一包装为 IdScanError。"""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        raise IdScanError(f"git {' '.join(args)} 失败：{stderr or e.returncode}") from e
    except FileNotFoundError as e:
        raise IdScanError("git 不可用，无法扫描分支与 worktree") from e
    return result.stdout


def git_common_dir(repo_root: Path) -> Path:
    """返回所有 worktree 共享的 git 公共目录绝对路径。"""
    text = _run_git(repo_root, ["rev-parse", "--git-common-dir"]).strip()
    if not text:
        raise IdScanError("无法解析 git 公共目录")
    path = Path(text)
    return path if path.is_absolute() else (repo_root / path).resolve()


@contextmanager
def id_lock(repo_root: Path):
    """在 git 公共目录上取排他锁，覆盖「扫描取号 → 建文件」全过程。"""
    lock_path = git_common_dir(repo_root) / LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def local_branches(repo_root: Path) -> list[str]:
    text = _run_git(
        repo_root, ["for-each-ref", "--format=%(refname:short)", "refs/heads"]
    )
    return [line.strip() for line in text.splitlines() if line.strip()]


def worktree_roots(repo_root: Path) -> list[Path]:
    """返回所有登记 worktree 的根目录（含主仓）。"""
    text = _run_git(repo_root, ["worktree", "list", "--porcelain"])
    roots: list[Path] = []
    for line in text.splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree ") :].strip())
            if path.is_dir():
                roots.append(path.resolve())
    return roots


def _entry_number(prefix: str, name: str) -> int | None:
    match = re.fullmatch(rf"{prefix}([0-9]{{3,}})_[a-z0-9_]+\.md", name)
    return int(match.group(1)) if match else None


def _numbers_from_branch(
    repo_root: Path, branch: str, prefix: str, dirs: tuple[str, ...]
) -> dict[int, list[str]]:
    text = _run_git(
        repo_root, ["ls-tree", "-r", "--name-only", branch, "--", *dirs]
    )
    found: dict[int, list[str]] = {}
    for rel in text.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        number = _entry_number(prefix, Path(rel).name)
        if number is not None:
            found.setdefault(number, []).append(rel)
    return found


def _numbers_from_worktree(
    root: Path, prefix: str, dirs: tuple[str, ...]
) -> dict[int, list[str]]:
    found: dict[int, list[str]] = {}
    for rel_dir in dirs:
        base = root / rel_dir
        if not base.is_dir():
            continue
        for path in base.rglob("*.md"):
            number = _entry_number(prefix, path.name)
            if number is not None:
                found.setdefault(number, []).append(str(path.relative_to(root)))
    return found


def _check_duplicates(source: str, found: dict[int, list[str]], prefix: str) -> None:
    duplicated = {n: paths for n, paths in found.items() if len(paths) > 1}
    if duplicated:
        detail = "；".join(
            f"{prefix}{n:03d} 出现在 {', '.join(sorted(paths))}"
            for n, paths in sorted(duplicated.items())
        )
        raise IdScanError(f"{source} 中条目编号重复：{detail}")


def scan_max_id(repo_root: Path, prefix: str, dirs: tuple[str, ...]) -> int:
    """扫描所有本地分支与 worktree，返回已用最大编号；无条目返回 0。"""
    maximum = 0
    for branch in local_branches(repo_root):
        found = _numbers_from_branch(repo_root, branch, prefix, dirs)
        _check_duplicates(f"分支 {branch}", found, prefix)
        maximum = max([maximum, *found])
    for root in worktree_roots(repo_root):
        found = _numbers_from_worktree(root, prefix, dirs)
        _check_duplicates(f"worktree {root}", found, prefix)
        maximum = max([maximum, *found])
    return maximum


SLUG_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")


def allocate(
    repo_root: Path,
    *,
    prefix: str,
    dirs: tuple[str, ...],
    target_dir: Path,
    slug: str,
    body: str,
) -> Path:
    """锁内分配编号并落盘条目文件，返回新文件路径。"""
    if not SLUG_RE.match(slug):
        raise IdScanError(f"slug 须匹配 {SLUG_RE.pattern}（收到 {slug!r}）")
    with id_lock(repo_root):
        number = scan_max_id(repo_root, prefix, dirs) + 1
        entry_id = f"{prefix}{number:03d}"
        path = target_dir / f"{entry_id}_{slug}.md"
        if path.exists():
            raise IdScanError(f"{path} 已存在；请先处理后重试")
        target_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(body.replace("{id}", entry_id), encoding="utf-8", newline="\n")
    return path
