#!/usr/bin/env python3
"""md_format.py - 模板仓/消费仓统一的 Markdown 格式化入口（md_kx 包装）。

选文件、黑名单、缺二进制在仓库内解决，不依赖 md_kx 的 exclude（Python 3.13+ 才
生效）。风格只读 `.md_kx.toml`，本脚本不重复传 `--table-mode`。

用法：
  python3 .repo_template/scripts/md_format.py PATH...      # 点名路径（须在仓库内）
  python3 .repo_template/scripts/md_format.py --changed    # 相对 HEAD 的改动 + staged + 未跟踪 .md
  python3 .repo_template/scripts/md_format.py --all        # 全部已跟踪非黑名单 .md
  python3 .repo_template/scripts/md_format.py --check      # 只检查不写入，格式漂移非 0

库函数（供 pending/findings/spikes new 等）：format_paths() 缺二进制时跳过并
警告，不阻断（CI 无二进制时现有测试不红）；CLI 缺二进制硬失败。
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# 黑名单：不格式化的已跟踪 md（方案 B）。
BLACKLIST_PREFIXES = ("docs/archive/", ".scratch/")


class MdFormatError(Exception):
    """格式化失败。"""


def find_md_kx() -> str | None:
    """找 PATH 上的 md_kx。CLI 找不到时硬失败由调用方处理。"""
    return shutil.which("md_kx")


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _is_blacklisted(rel: str) -> bool:
    return rel.startswith(BLACKLIST_PREFIXES)


def _git(*args: str, root: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root or REPO_ROOT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30,
    )


def collect_all() -> list[str]:
    """全部已跟踪非黑名单 .md。"""
    result = _git("ls-files", "-z", "--", "*.md")
    if result.returncode != 0:
        raise MdFormatError(f"git ls-files 失败：{result.stderr.strip()}")
    return [
        rel for rel in result.stdout.split("\0")
        if rel and not _is_blacklisted(rel)
    ]


def collect_changed() -> list[str]:
    """相对 HEAD 的已跟踪改动 + staged + 未跟踪 .md（扣黑名单与 .scratch/）。"""
    rels: set[str] = set()
    result = _git("diff", "--name-only", "-z", "HEAD", "--", "*.md")
    if result.returncode != 0:
        raise MdFormatError(f"git diff 失败：{result.stderr.strip()}")
    rels.update(rel for rel in result.stdout.split("\0") if rel)
    result = _git("diff", "--cached", "--name-only", "-z", "--", "*.md")
    if result.returncode != 0:
        raise MdFormatError(f"git diff --cached 失败：{result.stderr.strip()}")
    rels.update(rel for rel in result.stdout.split("\0") if rel)
    result = _git("ls-files", "--others", "--exclude-standard", "-z", "--", "*.md")
    if result.returncode != 0:
        raise MdFormatError(f"git ls-files --others 失败：{result.stderr.strip()}")
    rels.update(rel for rel in result.stdout.split("\0") if rel)
    return [rel for rel in sorted(rels) if not _is_blacklisted(rel)]


def format_paths(rel_paths: list[str], *, check: bool = False) -> list[str]:
    """对指定相对路径跑 md_kx；返回变更/漂移的文件列表。

    check=True 时判断漂移（md_kx --check 对漂移文件 exit 1）不写入。
    md_kx 缺失或非漂移类失败抛 MdFormatError（CLI 语义）。
    """
    executable = find_md_kx()
    if not executable:
        raise MdFormatError(
            "找不到 md_kx；请安装（uv tool install md_kx）或加入 PATH。"
        )
    changed: list[str] = []
    for rel in rel_paths:
        if _is_blacklisted(rel):
            raise MdFormatError(f"{rel} 在黑名单，拒绝格式化")
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        if check:
            result = subprocess.run(
                [executable, "--check", str(path)], capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=30,
            )
            if result.returncode == 1:
                changed.append(rel)
            elif result.returncode != 0:
                raise MdFormatError(
                    f"md_kx --check 失败（{rel}）：{result.stderr.strip()}"
                )
            continue
        before = path.read_bytes()
        result = subprocess.run(
            [executable, str(path)], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        if result.returncode != 0:
            raise MdFormatError(f"md_kx 失败（{rel}）：{result.stderr.strip()}")
        after = path.read_bytes()
        if before != after:
            changed.append(rel)
    return changed


def format_new_file(rel: str) -> bool:
    """供 pending/findings/spikes new：格式化刚写的条目文件（方案 C）。

    缺 md_kx 时跳过并打警告，不阻断（CI 无二进制时现有测试不红）。
    """
    executable = find_md_kx()
    if not executable:
        print("WARNING: 未安装 md_kx，跳过 Markdown 格式化", file=sys.stderr)
        return False
    try:
        format_paths([rel])
    except MdFormatError as error:
        print(f"WARNING: md_kx 格式化失败（{rel}）：{error}", file=sys.stderr)
        return False
    return True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="md_kx 包装：Markdown 统一格式化")
    parser.add_argument("paths", nargs="*", help="点名相对路径")
    parser.add_argument("--changed", action="store_true", help="相对 HEAD 的改动 + staged + 未跟踪 .md")
    parser.add_argument("--all", action="store_true", help="全部已跟踪非黑名单 .md")
    parser.add_argument("--check", action="store_true", help="只检查不写入，漂移非 0")
    args = parser.parse_args(argv)

    try:
        if args.changed and args.all:
            raise MdFormatError("--changed 与 --all 互斥")
        if args.paths:
            rels = [p for p in args.paths if not _is_blacklisted(p)]
        elif args.changed:
            rels = collect_changed()
        elif args.all or args.check:
            # --check 独立调用隐含检查全部非黑名单 md（方案 B 用法）
            rels = collect_all()
        else:
            parser.print_help()
            return
        changed = format_paths(rels, check=args.check)
    except MdFormatError as error:
        print(f"错误: {error}", file=sys.stderr)
        sys.exit(1)

    if args.check:
        if changed:
            print(f"格式漂移：{', '.join(changed)}", file=sys.stderr)
            sys.exit(1)
        print("格式一致")
        return
    if changed:
        print("已格式化：\n" + "\n".join(changed))
    else:
        print("无改动")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
