#!/usr/bin/env python3
"""_id_scan.py - 跨 worktree 跨分支的条目编号扫描与互斥分配。

pending.py / findings.py 共用。条目以「一条目一文件」形式存放，文件名形如
`p047_cli_exit_code.md` / `d012_uv_lock_marker.md`，编号直接来自文件名，无需解析正文。

扫描来源：
- 所有本地分支的 git 树（`git ls-tree` 文件名 + legacy 单文件正文编号）；
- 所有登记 worktree 的工作区文件（含未提交条目）。

旧格式曾把同类条目集中在一个单文件（`docs/pending.md`、`docs/findings.md` 等，
编号只在正文小节标题里）。未合并旧分支里这种文件的编号对文件名扫描不可见，
分支扫描额外用正则解析 `{目录}.md` 正文的小节标题编号，防止分配重号。

重复检测语义：
- 同一来源（单个分支或单个 worktree）内同号出现在多个状态目录时报错——迁移残留需立即暴露。
- 跨来源重复不报——并行 task worktree 各自含主干历史，合并前允许暂时重复。

互斥分配：
`allocate` 在 git 公共目录的锁文件上取排他锁，锁内完成「扫描取号 → 建文件」。
释放锁时新条目已落盘，后续扫描必然可见，不存在两个进程取到同号的窗口。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from repo_task.locks import TASK_ID_LOCK_NAME, git_common_lock

LOCK_NAME = TASK_ID_LOCK_NAME


class IdScanError(ValueError):
    """编号扫描或分配失败。"""


def _run_git(repo_root: Path, args: list[str]) -> str:
    """运行 git 子进程；失败统一包装为 IdScanError。"""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=30,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        raise IdScanError(f"git {' '.join(args)} 失败：{stderr or e.returncode}") from e
    except FileNotFoundError as e:
        raise IdScanError("git 不可用，无法扫描分支与 worktree") from e
    return result.stdout


@contextmanager
def id_lock(repo_root: Path):
    """在 git 公共目录上取排他锁，覆盖「扫描取号 → 建文件」全过程。

    委托 repo_task.locks.git_common_lock：锁文件初始化与同线程重入由公共 helper 处理，兼容 Windows
    msvcrt（空文件锁 1 字节超 EOF 抛 OSError，RT-011）。git 失败包装为 IdScanError。
    """
    try:
        with git_common_lock(repo_root, LOCK_NAME):
            yield
    except OSError as error:
        raise IdScanError(str(error)) from error


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


def _entry_number(prefix: str, name: str, *, kind: str) -> int | None:
    suffix = r"\.md" if kind == "file" else ""
    match = re.fullmatch(rf"{prefix}([0-9]{{3,}})_[a-z0-9_]+{suffix}", name)
    return int(match.group(1)) if match else None


def _numbers_from_branch(
    repo_root: Path, branch: str, prefix: str, dirs: tuple[str, ...], kind: str
) -> dict[int, list[str]]:
    text = _run_git(
        repo_root, ["ls-tree", "-r", "--name-only", branch, "--", *dirs]
    )
    found: dict[int, list[str]] = {}
    for rel in text.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        parts = Path(rel).parts
        # 目录型条目在 ls-tree -r 下只出现为其内部文件路径；身份取条目目录的完整
        # 相对路径，同号出现在 active 与 archive 两侧才不会被目录基名折叠漏检。
        candidates = (
            [(parts[-1], rel)]
            if kind == "file"
            else [(part, "/".join(parts[: i + 1])) for i, part in enumerate(parts)]
        )
        for name, identity in candidates:
            number = _entry_number(prefix, name, kind=kind)
            if number is not None:
                paths = found.setdefault(number, [])
                if identity not in paths:
                    paths.append(identity)
                break
    return found


def legacy_ledger_sources(
    repo_root: Path, prefix: str, dirs: tuple[str, ...]
) -> list[str]:
    """返回仍含旧单文件总账（`{dir}.md`）的来源：分支名或 worktree 路径。

    编号分配不再解析旧正文；存在旧总账时必须先显式迁移或把旧分支/工作区
    隔离出当前操作集合，避免漏号撞号。
    """
    legacy_names = {f"{directory}.md" for directory in dirs}
    sources: list[str] = []
    for branch in local_branches(repo_root):
        listed = _run_git(
            repo_root, ["ls-tree", "--name-only", branch, "--", *sorted(legacy_names)]
        )
        if listed.strip():
            sources.append(f"分支 {branch}")
    for root in worktree_roots(repo_root):
        if any((root / name).is_file() for name in legacy_names):
            sources.append(f"worktree {root}")
    return sorted(set(sources))


def require_no_legacy_ledgers(
    repo_root: Path, prefix: str, dirs: tuple[str, ...], *, migration: str
) -> None:
    sources = legacy_ledger_sources(repo_root, prefix, dirs)
    if sources:
        hint = migration or "pending.py / findings.py migrate-legacy"
        raise IdScanError(
            "存在旧单文件总账（" + "；".join(sources) + "）；编号分配已不解析旧正文。"
            f"先执行显式迁移：{hint}，并提交迁移结果后再分配；或把上述来源合并/删除"
        )


def _numbers_from_worktree(
    root: Path, prefix: str, dirs: tuple[str, ...], kind: str
) -> dict[int, list[str]]:
    found: dict[int, list[str]] = {}
    for rel_dir in dirs:
        base = root / rel_dir
        if not base.is_dir():
            continue
        entries = base.rglob("*.md") if kind == "file" else base.iterdir()
        for path in entries:
            if kind == "dir" and not path.is_dir():
                continue
            number = _entry_number(prefix, path.name, kind=kind)
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


def scan_max_id(
    repo_root: Path, prefix: str, dirs: tuple[str, ...], kind: str = "file"
) -> int:
    """扫描所有本地分支与 worktree，返回已用最大编号；无条目返回 0。

    kind="file" 时条目是 `{prefix}NNN_{slug}.md`；kind="dir" 时是同名目录。
    """
    maximum = 0
    for branch in local_branches(repo_root):
        found = _numbers_from_branch(repo_root, branch, prefix, dirs, kind)
        _check_duplicates(f"分支 {branch}", found, prefix)
        maximum = max([maximum, *found])
    for root in worktree_roots(repo_root):
        found = _numbers_from_worktree(root, prefix, dirs, kind)
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
    formatter=None,
    migration: str = "",
) -> Path:
    """锁内完成「取号 → 生成最终内容 → 格式化 → 原子发布」，返回新文件路径。

    formatter 接收临时文件路径；抛错时不发布正式条目并清理临时文件。
    存在旧单文件总账时拒绝分配，要求先显式迁移（migration 为提示命令）。
    """
    if not SLUG_RE.match(slug):
        raise IdScanError(f"slug 须匹配 {SLUG_RE.pattern}（收到 {slug!r}）")
    with id_lock(repo_root):
        require_no_legacy_ledgers(repo_root, prefix, dirs, migration=migration)
        number = scan_max_id(repo_root, prefix, dirs) + 1
        entry_id = f"{prefix}{number:03d}"
        path = target_dir / f"{entry_id}_{slug}.md"
        if path.exists():
            raise IdScanError(f"{path} 已存在；请先处理后重试")
        target_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = target_dir / f".tmp_{entry_id}_{slug}.md"
        try:
            tmp_path.write_bytes(
                body.replace("{id}", entry_id).replace("{slug}", slug).encode("utf-8")
            )
            if formatter is not None:
                formatter(tmp_path)
            if not tmp_path.read_text(encoding="utf-8").strip():
                raise IdScanError("生成条目内容为空；拒绝发布")
            os.replace(tmp_path, path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
    return path


def _safe_file_name(name: str) -> str:
    """files 键必须是目标目录内的相对路径；拒绝绝对路径、..、反斜杠与盘符。"""
    if not name or "\x00" in name:
        raise IdScanError(f"文件名非法：{name!r}")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in name
        or ":" in name
        or not path.parts
    ):
        raise IdScanError(
            f"文件名非法（不得为绝对路径或越出目标目录）：{name!r}"
        )
    return path.as_posix()


def allocate_dir(
    repo_root: Path,
    *,
    prefix: str,
    dirs: tuple[str, ...],
    target_dir: Path,
    slug: str,
    files: dict[str, str],
    formatter=None,
    migration: str = "",
) -> Path:
    """锁内分配编号、在临时目录准备完整内容并格式化，随后原子发布目录条目。

    files 为 {相对文件名: 内容}；内容中的 `{id}` 替换为分配到的编号。
    formatter 接收临时文件路径；抛错时不发布正式目录。
    存在旧单文件总账时拒绝分配，要求先显式迁移。
    """
    if not SLUG_RE.match(slug):
        raise IdScanError(f"slug 须匹配 {SLUG_RE.pattern}（收到 {slug!r}）")
    safe_files = {_safe_file_name(name): content for name, content in files.items()}
    with id_lock(repo_root):
        require_no_legacy_ledgers(repo_root, prefix, dirs, migration=migration)
        number = scan_max_id(repo_root, prefix, dirs, kind="dir") + 1
        entry_id = f"{prefix}{number:03d}"
        path = target_dir / f"{entry_id}_{slug}"
        if path.exists():
            raise IdScanError(f"{path} 已存在；请先处理后重试")
        target_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir = target_dir / f".tmp_{entry_id}_{slug}"
        try:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
            tmp_dir.mkdir(parents=True)
            for name, content in safe_files.items():
                dest_file = tmp_dir / name
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                dest_file.write_bytes(content.replace("{id}", entry_id).encode("utf-8"))
                if formatter is not None:
                    formatter(dest_file)
                if not dest_file.read_text(encoding="utf-8").strip():
                    raise IdScanError(f"生成条目文件内容为空：{name}；拒绝发布")
            os.replace(tmp_dir, path)
        except BaseException:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
    return path


_LEGACY_HEADING_RE = re.compile(
    r"^(?P<hashes>#{2,3})[ \t]+(?P<prefix>[pd])(?P<num>[0-9]{3,})(?![0-9])[ \t]*(?P<title>.*)$"
)


def legacy_ledger_entries(text: str, prefix: str) -> list[tuple[int, str, str]]:
    """解析旧单文件总账正文，返回 [(编号, 标题, 正文)]；正文不含标题行。"""
    entries: list[tuple[int, str, str]] = []
    current: tuple[int, str, int] | None = None  # (number, title, level)
    body: list[str] = []

    def flush() -> None:
        if current is not None:
            entries.append((current[0], current[1], "\n".join(body).strip()))

    for line in text.splitlines():
        match = _LEGACY_HEADING_RE.match(line)
        if match and match.group("prefix") == prefix:
            flush()
            current = (int(match.group("num")), match.group("title").strip(), len(match.group("hashes")))
            body = []
            continue
        if current is not None:
            heading = re.match(r"^(#{1,6})[ \t]+", line)
            if heading and len(heading.group(1)) <= current[2]:
                flush()
                current = None
                body = []
                continue
            body.append(line)
    flush()
    return entries


def slug_from_title(title: str) -> str:
    # 去掉标题末尾的括号说明（如「（2026-07-01 发现）」）再取 slug。
    title = re.sub(r"[（(][^）)]*[）)]\s*$", "", title.strip())
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return slug or "entry"
