#!/usr/bin/env python3
"""repo_cleanup.py — repo-cleanup skill 机械化执行器。

删除仓库内明确无用的文件系统垃圾（缓存、OS/编辑器垃圾、点名的运行产物）。
扫描、类别匹配、保护名单过滤、dry-run 预览与 apply 删除全部由本脚本执行；
agent 只保留裁定语义：scratch 活跃引用路径（--keep 传入）、审批 commit 门禁、
「需用户决定」项汇报。

类别：
  默认（scan/apply 未点名类别时）：pycache / pytest / logs / os / editor
  点名才清：node / scratch / artifacts / data
  scratch / artifacts / data 清目录内容、保留目录本身。

保护（永不删）：.git/、AGENTS.md、README.md、CLAUDE.md、.gitignore、
docs/archive/tasks_audit.log；docs/ 下仅 os/editor 类放行；src/tests/schemas/
config/scripts/.agents/.claude 下的类别命中垃圾可清（文件本身不因「目录」被误删）。

用法：
  repo_cleanup.py scan [CATEGORIES...] [--keep REL...]      # 只列，不删
  repo_cleanup.py apply [CATEGORIES...] [--keep REL...]     # 删除列表内路径

默认不 commit；本脚本不写 git index / 不 commit。
"""

import argparse
import fnmatch
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_CATEGORIES = ("pycache", "pytest", "logs", "os", "editor")
NAMED_CATEGORIES = ("node", "scratch", "artifacts", "data")
ALL_CATEGORIES = DEFAULT_CATEGORIES + NAMED_CATEGORIES
BULK_CATEGORY_DIRS = {"scratch": ".scratch", "artifacts": "artifacts", "data": "data"}
# docs/ 下只允许清 OS/编辑器垃圾文件名；其余类别在 docs/ 下全部拒绝。
DOCS_ONLY_CATEGORIES = {"os", "editor"}
HARD_PROTECTED = {
    ".git",
    "AGENTS.md",
    "README.md",
    "CLAUDE.md",
    ".gitignore",
    "docs/archive/tasks_audit.log",
}


class CleanupError(Exception):
    pass


def repo_root() -> Path:
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        raise CleanupError(f"不在 git 仓库：{r.stderr.strip()}")
    return Path(r.stdout.strip())


def classify(name: str, is_dir: bool) -> str | None:
    if is_dir:
        if name == "__pycache__":
            return "pycache"
        if name == ".pytest_cache":
            return "pytest"
        if name == "node_modules":
            return "node"
        return None
    if name.endswith((".pyc", ".pyo", ".pyd")):
        return "pycache"
    if name.endswith(".log"):
        return "logs"
    if name in (".DS_Store", "Thumbs.db", "desktop.ini"):
        return "os"
    if name.endswith(("~", ".swp", ".swo")):
        return "editor"
    return None


def _is_glob(pattern: str) -> bool:
    return any(ch in pattern for ch in "*?[")


def _kept(rel: str, keeps: list[str]) -> bool:
    """rel 命中 keep（精确 / glob / 前缀），或是具体 keep 路径的祖先目录。"""
    for k in keeps:
        k = k.strip()
        if not k:
            continue
        k_norm = k.rstrip("/")
        if fnmatch.fnmatch(rel, k) or fnmatch.fnmatch(rel, k_norm):
            return True
        if rel.startswith(k_norm + "/"):
            return True
        if not _is_glob(k_norm) and k_norm.startswith(rel + "/"):
            return True
    return False


def _dir_holds_keep(rel: str, refs: list[str]) -> bool:
    prefix = rel.rstrip("/") + "/"
    return any(ref.rstrip("/").startswith(prefix) for ref in refs if ref.strip())


def _prune(relroot: str, names: list[str], excluded: set[str]) -> list[str]:
    out = []
    for n in names:
        rel = f"{relroot}/{n}" if relroot else n
        if rel in excluded:
            continue
        out.append(n)
    return out


def collect(repo: Path, categories: list[str], keeps: list[str]) -> tuple[list[dict], list[str]]:
    """返回 (hits, skipped)。hits 每项含 path / category / kind；skipped 为 keep 排除路径。"""
    cats = set(categories)
    hits: list[dict] = []
    skipped: list[str] = []

    bulk_roots: set[str] = set()
    for cat, rel_dir in BULK_CATEGORY_DIRS.items():
        if cat not in cats:
            continue
        base = repo / rel_dir
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            rel = p.relative_to(repo).as_posix()
            if _kept(rel, keeps):
                skipped.append(rel)
                continue
            hits.append({"path": rel, "category": cat, "kind": "dir" if p.is_dir() else "file"})
        bulk_roots.add(rel_dir)

    for root, dirs, files in os.walk(repo):
        relroot = os.path.relpath(root, repo)
        relroot = "" if relroot == "." else relroot.replace(os.sep, "/")
        dirs[:] = _prune(relroot, dirs, {".git", *bulk_roots})
        entries = []
        for d in dirs:
            rel = f"{relroot}/{d}" if relroot else d
            entries.append((rel, True))
        for f in files:
            rel = f"{relroot}/{f}" if relroot else f
            entries.append((rel, False))
        for rel, is_dir in entries:
            if rel in HARD_PROTECTED:
                continue
            cat = classify(Path(rel).name, is_dir)
            if cat is None or cat not in cats:
                continue
            if rel.startswith("docs/") and cat not in DOCS_ONLY_CATEGORIES:
                continue
            hits.append({"path": rel, "category": cat, "kind": "dir" if is_dir else "file"})

    # glob keep 不会在 _kept 里挡住祖先目录；按已跳过路径再滤一遍，避免 rmtree 父目录。
    keep_refs = [s for s in skipped]
    for k in keeps:
        k = k.strip().rstrip("/")
        if k and not _is_glob(k):
            keep_refs.append(k)
    filtered: list[dict] = []
    for h in hits:
        if h["kind"] == "dir" and _dir_holds_keep(h["path"], keep_refs):
            if h["path"] not in skipped:
                skipped.append(h["path"])
            continue
        filtered.append(h)
    hits = filtered
    hits.sort(key=lambda h: h["path"])
    return hits, skipped


def apply_delete(repo: Path, hits: list[dict], keeps: list[str] | None = None) -> list[str]:
    keeps = keeps or []
    files = [h for h in hits if h["kind"] != "dir"]
    dirs = sorted(
        [h for h in hits if h["kind"] == "dir"],
        key=lambda h: h["path"].count("/"),
        reverse=True,
    )
    deleted = []
    for h in files + dirs:
        target = repo / h["path"]
        if not target.exists() and not target.is_symlink():
            continue
        if h["path"] in HARD_PROTECTED:
            continue
        if h["kind"] == "dir":
            if _dir_holds_keep(h["path"], keeps):
                continue
            if target.is_symlink():
                target.unlink()
            else:
                shutil.rmtree(target)
        else:
            target.unlink()
        deleted.append(h["path"])
    return deleted


def _table(rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "|".join(["------"] * len(rows[0])) + "|"]
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def _print_scan(categories: list[str], hits: list[dict], skipped: list[str]) -> None:
    print("## repo-cleanup 预览（未删除）")
    print("模式：dry-run")
    print(f"类别：{', '.join(categories)}")
    print()
    rows = [["路径", "类别", "说明"]]
    for h in hits:
        note = "目录" if h["kind"] == "dir" else "文件"
        if h["category"] in BULK_CATEGORY_DIRS:
            note = "目录内容"
        rows.append([f"./{h['path']}", h["category"], note])
    if len(rows) == 1:
        print("（无命中）")
    else:
        for line in _table(rows):
            print(line)
    print()
    print(f"合计：{len(hits)} 项")
    if skipped:
        print("跳过（--keep 引用保护）：")
        for s in skipped:
            print(f"- {s}")
    print("下一步：确认后 `/repo-cleanup apply`（或带类别）。")


def _print_apply(deleted: list[str], skipped: list[str]) -> None:
    print("## repo-cleanup 结果")
    print("模式：apply")
    print("已删除：")
    if not deleted:
        print("- （无）")
    for d in deleted:
        print(f"- {d}")
    print("跳过（保护/不存在）：")
    for s in skipped:
        print(f"- {s}")


def _resolve_categories(args) -> list[str]:
    if args.categories:
        for c in args.categories:
            if c not in ALL_CATEGORIES:
                raise CleanupError(f"未知类别 {c!r}；可选 {ALL_CATEGORIES}")
        return args.categories
    return list(DEFAULT_CATEGORIES)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="repo-cleanup 机械化执行器")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd in ("scan", "apply"):
        p = sub.add_parser(cmd, help="scan=只列不删；apply=删除列表内路径")
        p.add_argument("categories", nargs="*", metavar="CAT",
                       help=f"类别（默认 {DEFAULT_CATEGORIES}）；node/scratch/artifacts/data 须点名")
        p.add_argument("--keep", action="append", default=[], metavar="REL",
                       help="相对仓库根路径（glob 或前缀），排除不删（scratch 活跃引用）")
        p.set_defaults(_mode=cmd)
    args = parser.parse_args(argv)
    try:
        repo = repo_root()
        categories = _resolve_categories(args)
        hits, skipped = collect(repo, categories, args.keep)
        if args._mode == "scan":
            _print_scan(categories, hits, skipped)
        else:
            deleted = apply_delete(repo, hits, args.keep)
            _print_apply(deleted, skipped)
    except CleanupError as error:
        sys.exit(f"repo_cleanup: {error}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
