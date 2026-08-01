#!/usr/bin/env python3
"""_id_scan.py - 跨 worktree 跨分支的编号扫描共用逻辑。

pending.py / findings.py 共用：扫描所有本地分支 git 树 + 所有 worktree 工作区文件，
提取规范条目编号，返回全局最大值。

重复检测语义：
- 同一来源（单个 worktree 或单个分支）内，active 与 archive 两份文件的编号共享一个
  序列，跨文件查重——repo-hygiene 迁移残留导致同一编号同时出现在 active 与 archive 时报错。
- 跨来源（不同 worktree / 不同分支）重复不报——并行 task worktree 各自含主干历史，
  合并前允许暂时重复。

坏文件处理：
- 当前 worktree（repo_root 所在）的主路径（rel_paths[0]）不存在视为空历史；存在但损坏
  （非 UTF-8 / 读取失败）报错——工具调用者维护的权威总账损坏需立即暴露。
- 其他来源（archive、历史分支、其他 worktree）的坏文件静默跳过。
"""

import re
import subprocess
from pathlib import Path

FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")


class IdScanError(ValueError):
    """编号历史文件结构或读取错误。"""


def visible_markdown_lines(text: str) -> list[str]:
    """移除 fenced code、blockquote 与 HTML 注释，保留可参与条目解析的正文行。"""
    lines = []
    fence_marker = None
    in_comment = False
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

        if in_comment:
            end = line.find("-->")
            if end < 0:
                continue
            line = line[end + 3 :]
            in_comment = False

        while "<!--" in line:
            start = line.find("<!--")
            end = line.find("-->", start + 4)
            if end < 0:
                line = line[:start]
                in_comment = True
                break
            line = line[:start] + line[end + 3 :]

        if line.lstrip().startswith(">"):
            continue
        lines.append(line)
    return lines


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


def _read_blob_optional(repo_root: Path, ref: str, rel_path: str) -> str | None:
    """git show ref:rel_path；不存在返回 None，非 UTF-8 返回 None。"""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "show", f"{ref}:{rel_path}"],
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _read_worktree_file_optional(root: Path, rel_path: str) -> str | None:
    f = root / rel_path
    if not f.is_file():
        return None
    try:
        return f.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _read_worktree_main_strict(root: Path, rel_path: str, display: str) -> str | None:
    """读取当前 worktree 主路径。

    不存在视为空历史（返回 None）；存在但损坏（非 UTF-8 / 读取失败）报错。
    """
    f = root / rel_path
    if not f.is_file():
        return None
    try:
        return f.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise IdScanError(f"{display}: 不是合法 UTF-8") from e
    except OSError as e:
        raise IdScanError(f"{display}: 无法读取（{e}）") from e


def _scan_source(
    texts: list[tuple[str, str]],
    entry_re: re.Pattern,
    source_label: str,
) -> list[int]:
    """对同一来源（单 worktree 或单分支）的多份文本提取编号。

    来源内跨文件查重——active 与 archive 同一编号同时出现时报错。
    """
    ids: list[int] = []
    seen: set[int] = set()
    for rel, text in texts:
        for line in visible_markdown_lines(text):
            match = entry_re.match(line)
            if match:
                number = int(match.group(1))
                if number in seen:
                    raise IdScanError(
                        f"{source_label}: 编号重复 {number:03d}（{rel}）"
                    )
                seen.add(number)
                ids.append(number)
    return ids


def _list_local_branches(repo_root: Path) -> list[str]:
    out = _run_git(repo_root, ["for-each-ref", "--format=%(refname)", "refs/heads/"])
    return [line.strip() for line in out.splitlines() if line.strip()]


def _list_worktree_roots(repo_root: Path) -> list[Path]:
    out = _run_git(repo_root, ["worktree", "list", "--porcelain"])
    roots = []
    for line in out.splitlines():
        if line.startswith("worktree "):
            roots.append(Path(line[len("worktree ") :]))
    return roots


def _worktree_main_root(repo_root: Path) -> Path:
    """取 repo_root 所在的主 worktree 根目录（用于主路径严格校验）。"""
    roots = _list_worktree_roots(repo_root)
    resolved_root = repo_root.resolve()
    for root in roots:
        if root.resolve() == resolved_root:
            return root
    return roots[0] if roots else resolved_root


def scan_max_id(
    repo_root: Path,
    entry_re: re.Pattern,
    rel_paths: tuple[str, ...],
) -> int:
    """扫所有本地分支 git 树 + 所有 worktree 工作区，返回最大编号（无则 0）。

    rel_paths[0] 为 active 主路径；其他为 archive 等补充路径。当前 worktree 的主路径
    损坏报错，其余来源坏文件静默跳过。
    """
    maximum = 0

    for ref in _list_local_branches(repo_root):
        texts: list[tuple[str, str]] = []
        for rel in rel_paths:
            text = _read_blob_optional(repo_root, ref, rel)
            if text is not None:
                texts.append((rel, text))
        for number in _scan_source(texts, entry_re, ref):
            maximum = max(maximum, number)

    main_root = _worktree_main_root(repo_root)
    for root in _list_worktree_roots(repo_root):
        is_main_root = root.resolve() == main_root.resolve()
        texts = []
        for idx, rel in enumerate(rel_paths):
            if is_main_root and idx == 0:
                display = f"{root}/{rel}"
                text = _read_worktree_main_strict(root, rel, display)
                if text is not None:
                    texts.append((rel, text))
            else:
                text = _read_worktree_file_optional(root, rel)
                if text is not None:
                    texts.append((rel, text))
        for number in _scan_source(texts, entry_re, f"worktree:{root}"):
            maximum = max(maximum, number)

    return maximum
