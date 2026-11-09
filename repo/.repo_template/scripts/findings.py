#!/usr/bin/env python3
"""findings.py - 已验证技术发现的条目分配与列举入口。

一条目一文件：`docs/findings/dNNN_slug.md`。发现是长期资产，不迁 archive，
失效时就地改写「现状」字段。条目身份（编号与文件名）创建后固定，不提供改名接口。

用法：
  python3 .repo_template/scripts/findings.py new --slug uv_lock_platform_marker
  python3 .repo_template/scripts/findings.py list
  python3 .repo_template/scripts/findings.py migrate-legacy [--write]

`new` 在 git 公共目录的排他锁内完成「扫描取号 → 建文件」，并发 worker 不会撞号。
旧仓残留 docs/findings.md 单文件总账时，先用 migrate-legacy 显式拆分并提交结果，
再分配新编号；编号分配本身不解析旧正文。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _id_scan import (
    IdScanError,
    allocate,
    apply_legacy_migration,
    parse_entry_file_name,
    plan_legacy_migration,
)
from md_format import MdFormatError, format_file

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PREFIX = "d"
FINDINGS_DIR = REPO_ROOT / "docs/findings"
SCAN_DIRS = ("docs/findings",)
LEDGER_REL = "docs/findings.md"

TEMPLATE = """# {id} {一句话简述}

- 来源：{sNNN spike / tNNN task / 日常}
- 结论：{验证出的事实，一句话说清}
- 证据：{测量数据、日志、官方文档链接或最小复现}
- 影响：{影响哪些模块或后续选择；无则写「无」}
- 现状：有效
"""


def cmd_new(args: argparse.Namespace) -> None:
    try:
        path = allocate(
            REPO_ROOT,
            prefix=PREFIX,
            dirs=SCAN_DIRS,
            target_dir=FINDINGS_DIR,
            slug=args.slug,
            body=TEMPLATE,
            formatter=format_file,
            migration="python3 .repo_template/scripts/findings.py migrate-legacy",
        )
    except (IdScanError, MdFormatError) as error:
        sys.exit(f"错误: {error}")
    print(path.relative_to(REPO_ROOT).as_posix())


def _render_finding_entry(entry_id: str, slug: str, title: str, body: str) -> str:
    heading = f"# {entry_id} {title or entry_id}"
    return f"{heading}\n\n{body}\n"


def cmd_migrate_legacy(args: argparse.Namespace) -> None:
    """把当前工作区旧 docs/findings.md 拆成一条目一文件；默认 dry-run。"""
    try:
        items = plan_legacy_migration(
            REPO_ROOT, prefix=PREFIX, ledger_rel=LEDGER_REL, target_dir=FINDINGS_DIR
        )
    except IdScanError as error:
        sys.exit(f"错误: {error}")
    if not args.write:
        sys.stderr.write("dry-run；以下为拟定迁移，加 --write 落盘。\n")
        for item in items:
            sys.stderr.write(
                f"  {item.entry_id} {item.title} -> "
                f"{item.target.relative_to(REPO_ROOT).as_posix()}\n"
            )
        return
    try:
        created = apply_legacy_migration(
            items,
            repo_root=REPO_ROOT,
            prefix=PREFIX,
            ledger_rel=LEDGER_REL,
            render=_render_finding_entry,
            formatter=format_file,
        )
    except (IdScanError, MdFormatError) as error:
        sys.exit(f"错误: {error}")
    print(f"已迁移 {len(created)} 条 -> docs/findings/；来源 docs/findings.md 已移除")


def cmd_list(_args: argparse.Namespace) -> None:
    rows: list[tuple[str, str]] = []
    if FINDINGS_DIR.is_dir():
        for path in sorted(FINDINGS_DIR.glob("*.md")):
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="findings 条目分配与列举")
    sub = parser.add_subparsers(dest="command", required=True)

    new_parser = sub.add_parser("new", help="锁内取号并按模板建条目文件")
    new_parser.add_argument("--slug", required=True, help="snake_case 主题标识")
    new_parser.set_defaults(func=cmd_new)

    migrate_parser = sub.add_parser("migrate-legacy", help="把旧单文件总账拆成一条目一文件")
    migrate_parser.add_argument("--write", action="store_true", help="落盘（默认 dry-run）")
    migrate_parser.set_defaults(func=cmd_migrate_legacy)

    list_parser = sub.add_parser("list", help="列举全部发现")
    list_parser.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except IdScanError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
