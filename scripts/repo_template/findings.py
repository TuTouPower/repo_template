#!/usr/bin/env python3
"""findings.py - 已验证技术发现的条目分配与列举入口。

一条目一文件：`docs/findings/dNNN_slug.md`。发现是长期资产，不迁 archive，
失效时就地改写「现状」字段。

用法：
  python3 scripts/repo_template/findings.py new --slug uv_lock_platform_marker
  python3 scripts/repo_template/findings.py list
  python3 scripts/repo_template/findings.py rename d012 --slug new_slug [--write]

`new` 在 git 公共目录的排他锁内完成「扫描取号 → 建文件」，并发 worker 不会撞号。
`rename` 改条目文件名（保留编号，仅换 slug）：已入库文件用 `git mv` 保留历史，
未入库（新建尚未 git add）文件退化为普通改名；dry-run 默认，加 `--write` 落盘。
rename 不自动改文档正文里的引用——slug 改动需人工核对 docs/ 与 src/ 内的 dNNN 引用，
脚本只负责让文件名与新的 slug 一致。
"""

import argparse
import re
import subprocess
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
    print(path.relative_to(REPO_ROOT).as_posix())


def _run_git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


def cmd_rename(args: argparse.Namespace) -> None:
    """rename：保留编号，仅换 slug。已入库用 git mv、未入库普通改名；dry-run 默认。

    不改文档正文里的引用——slug 改动需人工核对 docs/ 与 src/ 内的 dNNN 引用。
    """
    if not re.fullmatch(r"[a-z0-9]+(_[a-z0-9]+)*", args.slug):
        sys.exit(f"slug 非法（须 snake_case）：{args.slug!r}")
    if not re.fullmatch(r"d\d{3,}", args.entry_id):
        sys.exit(f"entry_id 非法（须 dNNN）：{args.entry_id!r}")
    # 找原文件；目标文件名冲突时只命中目标检查，不被 glob 多重检测截断
    hits = sorted(FINDINGS_DIR.glob(f"{args.entry_id}_*.md"))
    if not hits:
        sys.exit(f"未找到 {args.entry_id}")
    old_path = hits[0]
    new_path = old_path.with_name(f"{args.entry_id}_{args.slug}.md")
    if new_path == old_path:
        sys.exit(f"新 slug 与旧 slug 相同：{args.slug!r}")
    if new_path.exists():
        sys.exit(f"目标文件已存在：{new_path.relative_to(REPO_ROOT).as_posix()}")
    if not args.write:
        print(f"dry-run：{old_path.relative_to(REPO_ROOT).as_posix()} → {new_path.relative_to(REPO_ROOT).as_posix()}")
        print("（rename 不自动改正文 dNNN 引用；如需同步，请人工核对 docs/ 与 src/）")
        return
    r = _run_git(["ls-files", "--error-unmatch", str(old_path.relative_to(REPO_ROOT))])
    if r.returncode == 0:
        r = _run_git(["mv", str(old_path.relative_to(REPO_ROOT)), str(new_path.relative_to(REPO_ROOT))])
        if r.returncode != 0:
            sys.exit(f"git mv 失败：{r.stderr.strip()}")
    else:
        old_path.rename(new_path)
    print(f"已重命名：{new_path.relative_to(REPO_ROOT).as_posix()}")


def cmd_list(_args: argparse.Namespace) -> None:
    rows: list[tuple[str, str]] = []
    if FINDINGS_DIR.is_dir():
        for path in sorted(FINDINGS_DIR.glob("*.md")):
            if not ENTRY_FILE_RE.match(path.name):
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

    list_parser = sub.add_parser("list", help="列举全部发现")
    list_parser.set_defaults(func=cmd_list)

    rename_parser = sub.add_parser("rename", help="改条目 slug（保留编号）")
    rename_parser.add_argument("entry_id", metavar="dNNN", help="要改的发现编号")
    rename_parser.add_argument("--slug", required=True, help="新 snake_case slug")
    rename_parser.add_argument("--write", action="store_true", help="落盘（默认 dry-run）")
    rename_parser.set_defaults(func=cmd_rename)

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
