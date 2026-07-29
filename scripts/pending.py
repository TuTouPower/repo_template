#!/usr/bin/env python3
"""pending.py - pending 编号只读分配入口。

用法：
  python3 scripts/pending.py next

编号历史 = docs/pending.md + docs/archive/pending.md 中规范 H3 条目
（不分普通待办 / bug，共享一条 pNNN 序列）。
本命令只计算并输出下一个编号，不写文件、不预留编号。
"""

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PENDING_PATH = REPO_ROOT / "docs" / "pending.md"
ARCHIVE_PENDING_PATH = REPO_ROOT / "docs" / "archive" / "pending.md"
PENDING_PATHS = (PENDING_PATH, ARCHIVE_PENDING_PATH)
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
ENTRY_RE = re.compile(r"^ {0,3}###[ \t]+p([0-9]{3,})(?=[ \t]|$)")


class PendingDataError(ValueError):
    """pending 总账结构或读取错误。"""


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


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


def read_pending_ids(path: Path) -> list[int]:
    """读取单份 pending 文档中的规范 H3 编号。"""
    if not path.is_file():
        raise PendingDataError(f"{_display_path(path)}: pending 编号历史文件不存在")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise PendingDataError(f"{_display_path(path)}: 不是合法 UTF-8") from e
    except OSError as e:
        raise PendingDataError(f"{_display_path(path)}: 无法读取（{e}）") from e

    ids = []
    for line in visible_markdown_lines(text):
        match = ENTRY_RE.match(line)
        if match:
            ids.append(int(match.group(1)))
    return ids


def next_pending_id(paths: tuple[Path, ...] | None = None) -> str:
    """返回 active + archive 历史中的全局最大编号加一。"""
    scan_paths = PENDING_PATHS if paths is None else paths
    seen = set()
    maximum = 0
    for path in scan_paths:
        for number in read_pending_ids(path):
            pending_id = f"p{number:03d}"
            if number in seen:
                raise PendingDataError(f"pending 编号重复：{pending_id}")
            seen.add(number)
            maximum = max(maximum, number)
    return f"p{maximum + 1:03d}"


def cmd_next(_args: argparse.Namespace) -> None:
    print(next_pending_id())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="从 active/archive pending 历史计算下一个 pNNN"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    next_parser = sub.add_parser("next", help="输出历史全局最大编号加一")
    next_parser.set_defaults(func=cmd_next)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except PendingDataError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
