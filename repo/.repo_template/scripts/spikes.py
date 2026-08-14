#!/usr/bin/env python3
"""spikes.py - spike 目录的条目分配与列举入口。

一 spike 一目录：`docs/spikes/sNNN_{slug}/`，`report.md` 从模板复制；有实验代码
自行加 `code/`。完结后由 `repo-hygiene` 整目录迁 `docs/archive/spikes/`。

用法：
  python3 .repo_template/scripts/spikes.py new --slug uv_lock_platform_marker
  python3 .repo_template/scripts/spikes.py list

`new` 在 git 公共目录的排他锁内完成「扫描取号 → 建目录」，并发 worker 不会撞号。
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _id_scan import IdScanError, allocate_dir
from md_format import format_new_file

TOOLKIT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = TOOLKIT_ROOT.parent
PREFIX = "s"
SPIKES_DIR = REPO_ROOT / "docs/spikes"
ARCHIVE_DIR = REPO_ROOT / "docs/archive/spikes"
SCAN_DIRS = ("docs/spikes", "docs/archive/spikes")
TEMPLATE_PATH = TOOLKIT_ROOT / "docs/spike_report_template.md"
ENTRY_DIR_RE = re.compile(r"^s([0-9]{3,})_[a-z0-9_]+$")


def cmd_new(args: argparse.Namespace) -> None:
    if not TEMPLATE_PATH.is_file():
        raise IdScanError(f"缺模板 {TEMPLATE_PATH.relative_to(REPO_ROOT).as_posix()}")
    path = allocate_dir(
        REPO_ROOT,
        prefix=PREFIX,
        dirs=SCAN_DIRS,
        target_dir=SPIKES_DIR,
        slug=args.slug,
        files={"report.md": TEMPLATE_PATH.read_text(encoding="utf-8")},
    )
    format_new_file(path.relative_to(REPO_ROOT).as_posix())
    print(path.relative_to(REPO_ROOT).as_posix())


def cmd_list(_args: argparse.Namespace) -> None:
    rows: list[tuple[str, str]] = []
    for state, directory in (("active", SPIKES_DIR), ("archived", ARCHIVE_DIR)):
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.is_dir() and ENTRY_DIR_RE.match(path.name):
                rows.append((state, path.name))
    if not rows:
        print("(no spikes)")
        return
    for state, name in sorted(rows, key=lambda row: row[1]):
        print(f"{state:<9} {name}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="spike 条目分配与列举")
    sub = parser.add_subparsers(dest="command", required=True)

    new_parser = sub.add_parser("new", help="锁内取号并从模板建 spike 目录")
    new_parser.add_argument("--slug", required=True, help="snake_case 主题标识")
    new_parser.set_defaults(func=cmd_new)

    list_parser = sub.add_parser("list", help="列举进行中与已归档 spike")
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
