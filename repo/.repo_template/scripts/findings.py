#!/usr/bin/env python3
"""findings.py - 已验证技术发现的条目分配与列举入口。

一条目一文件：`docs/findings/dNNN_slug.md`。发现是长期资产，不迁 archive，
失效时就地改写「现状」字段。条目身份（编号与文件名）创建后固定，不提供改名接口。

用法：
  python3 .repo_template/scripts/findings.py new --slug uv_lock_platform_marker
  python3 .repo_template/scripts/findings.py list

`new` 在 git 公共目录的排他锁内完成「扫描取号 → 建文件」，并发 worker 不会撞号。
旧仓残留 docs/findings.md 单文件总账时，先用 固定来源 repo_sync.py apply 内置迁移拆分并随同步结果单次提交，
再分配新编号；编号分配本身不解析旧正文。
"""

from __future__ import annotations

import argparse
from repo_task.context import RepoContext, TaskDataError
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _id_scan import (
    IdScanError,
    allocate,
    parse_entry_file_name,
)
from md_format import MdFormatError, format_file

PREFIX = "d"
SCAN_DIRS = ("docs/findings",)

TEMPLATE = """# {id} {一句话简述}

- 来源：{sNNN spike / tNNN task / 日常}
- 结论：{验证出的事实，一句话说清}
- 证据：{测量数据、日志、官方文档链接或最小复现}
- 影响：{影响哪些模块或后续选择；无则写「无」}
- 现状：有效
"""


def cmd_new(args: argparse.Namespace) -> None:
    repo = args.repo
    try:
        path = allocate(
            repo.root,
            prefix=PREFIX,
            dirs=SCAN_DIRS,
            target_dir=(repo.root / "docs/findings"),
            slug=args.slug,
            body=TEMPLATE,
            formatter=format_file,
        )
    except (IdScanError, MdFormatError) as error:
        sys.exit(f"错误: {error}")
    print(path.relative_to(repo.root).as_posix())


def cmd_list(args: argparse.Namespace) -> None:
    repo = args.repo
    rows: list[tuple[str, str]] = []
    if (repo.root / "docs/findings").is_dir():
        for path in sorted((repo.root / "docs/findings").glob("*.md")):
            if parse_entry_file_name(PREFIX, path.name) is None:
                continue
            # 摘要取 H1 标题（# dNNN 描述）——文件首行必有，字段名变体不影响检索
            title = ""
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            rows.append((path.stem, title))
    if not rows:
        print("(no findings)")
        return
    for stem, title in rows:
        print(f"{stem:<40} {title}")


def main(argv: list[str] | None = None, *, repo: RepoContext | None = None) -> None:
    parser = argparse.ArgumentParser(description="findings 条目分配与列举")
    parser.add_argument("--root", type=Path, help="目标仓库根（默认脚本所在仓库）")
    sub = parser.add_subparsers(dest="command", required=True)

    new_parser = sub.add_parser("new", help="锁内取号并按模板建条目文件")
    new_parser.add_argument("--slug", required=True, help="snake_case 主题标识")
    new_parser.set_defaults(func=cmd_new)

    list_parser = sub.add_parser("list", help="列举全部发现")
    list_parser.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    args.repo = repo if repo is not None else (RepoContext.for_root(args.root) if args.root else RepoContext.from_code_location(__file__))
    try:
        args.func(args)
    except (IdScanError, TaskDataError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
