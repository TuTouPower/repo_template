#!/usr/bin/env python3
"""findings.py - 已验证技术发现的条目分配与列举入口。

一条目一文件：`docs/findings/dNNN_slug.md`。发现是长期资产，不迁 archive，
失效时就地改写「现状」字段。

用法：
  python3 scripts/repo_template/findings.py new --slug uv_lock_platform_marker
  python3 scripts/repo_template/findings.py list

`new` 在 git 公共目录的排他锁内完成「扫描取号 → 建文件」，并发 worker 不会撞号。
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _id_scan import IdScanError, allocate

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PREFIX = "d"
FINDINGS_DIR = REPO_ROOT / "docs/findings"
SCAN_DIRS = ("docs/findings",)
ENTRY_FILE_RE = re.compile(r"^d([0-9]{3,})_[a-z0-9_]+\.md$")

TEMPLATE = """# {id} {一句话简述}

- 来源：{sNNN spike / tNNN task / 日常}
- 结论：{验证出的事实，一句话说清}
- 证据：{测量数据、日志、官方文档链接或最小复现}
- 影响：{影响哪些模块或后续选择；无则写「无」}
- 现状：有效
"""


def cmd_new(args: argparse.Namespace) -> None:
    path = allocate(
        REPO_ROOT,
        prefix=PREFIX,
        dirs=SCAN_DIRS,
        target_dir=FINDINGS_DIR,
        slug=args.slug,
        body=TEMPLATE,
    )
    print(str(path.relative_to(REPO_ROOT)))


def cmd_list(_args: argparse.Namespace) -> None:
    rows: list[tuple[str, str]] = []
    if FINDINGS_DIR.is_dir():
        for path in sorted(FINDINGS_DIR.glob("*.md")):
            if not ENTRY_FILE_RE.match(path.name):
                continue
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

    list_parser = sub.add_parser("list", help="列举全部发现")
    list_parser.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except IdScanError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
