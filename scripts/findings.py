#!/usr/bin/env python3
"""findings.py - findings 编号只读分配入口。

用法：
  python3 scripts/findings.py next

扫描所有本地分支 git 树 + 所有 worktree 工作区的 docs/findings.md 与 docs/archive/findings.md，
取全局最大 dNNN 编号加一。不写文件、不预留编号。
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _id_scan import IdScanError, scan_max_id

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY_RE = re.compile(r"^ {0,3}##[ \t]+d([0-9]{3,})(?=[ \t]|$)")
REL_PATHS = ("docs/findings.md", "docs/archive/findings.md")


def next_finding_id() -> str:
    maximum = scan_max_id(REPO_ROOT, ENTRY_RE, REL_PATHS)
    return f"d{maximum + 1:03d}"


def cmd_next(_args: argparse.Namespace) -> None:
    print(next_finding_id())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="从所有分支/worktree 的 findings 历史计算下一个 dNNN"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    next_parser = sub.add_parser("next", help="输出全局最大编号加一")
    next_parser.set_defaults(func=cmd_next)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except IdScanError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
