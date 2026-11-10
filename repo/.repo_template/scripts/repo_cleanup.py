#!/usr/bin/env python3
"""repo_cleanup.py — repo-cleanup skill 机械化执行器。

删除仓库内明确无用的文件系统垃圾（缓存、OS/编辑器垃圾、点名的运行产物）。
扫描、类别匹配、保护名单过滤、dry-run 预览与 apply 删除全部由本脚本执行；
agent 只保留裁定语义：人工额外保留路径（--keep 传入）、审批 commit 门禁、
「需用户决定」项汇报；有主草稿由脚本机械保护。

类别：
  默认（scan/apply 未点名类别时）：pycache / pytest / logs / os / editor / scratch
  点名才清：node / artifacts / data
  scratch / artifacts / data 清目录内容、保留目录本身；
  默认扫描不进入未点名的 node_modules 与 artifacts/data 目录树。
  scratch 默认清理；未关闭 pending 引用与未发布 issue 报告按最后修改时间保留
  3 天，无主内容直接清理。

保护（永不删）：.git/、AGENTS.md、README.md、.gitignore；
仓库根 local/ 整树（用户私有本地文件，任何类别都不进入）；
docs/ 下仅 os/editor 类放行；src/tests/schemas/
config/scripts/.agents/.claude 下的类别命中垃圾可清（文件本身不因「目录」被误删）。

用法：
  repo_cleanup.py scan [CATEGORIES...] [--keep REL...]      # 只列，不删
  repo_cleanup.py apply [CATEGORIES...] [--keep REL...]     # 删除列表内路径

默认不 commit；本脚本不写 git index / 不 commit。
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from pending import open_scratch_references
from repo_task.context import RepoContext, TaskDataError
from repo_task.documents import parse_front_matter_text
from repo_task.locks import CONTROL_LOCK_NAME, LockBusyError, git_common_lock

DEFAULT_CATEGORIES = ("pycache", "pytest", "logs", "os", "editor", "scratch")
NAMED_CATEGORIES = ("node", "artifacts", "data")
ALL_CATEGORIES = DEFAULT_CATEGORIES + NAMED_CATEGORIES
BULK_CATEGORY_DIRS = {"scratch": ".scratch", "artifacts": "artifacts", "data": "data"}
# 有主草稿的最后修改保留期。
OWNED_GRACE_SECONDS = 3 * 86400
ISSUE_DIR = ".scratch/repo_template_issues"
ISSUE_URL_RE = re.compile(r"^https://github\.com/[^/\s]+/[^/\s]+/issues/[1-9][0-9]*$")
_REPORT_RE = re.compile(r"^(\d{8}-\d{6})(\.issue)?\.md$")
# docs/ 下只允许清 OS/编辑器垃圾文件名；其余类别在 docs/ 下全部拒绝。
DOCS_ONLY_CATEGORIES = {"os", "editor"}
HARD_PROTECTED = {
    ".git",
    "AGENTS.md",
    "README.md",
    ".gitignore",
}
# 仓库根下整树保护的目录：用户私有本地文件，scan 不进入、apply 不删除。
PROTECTED_ROOT_DIRS = ("local",)

_GIT_REDIRECT_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_CEILING_DIRECTORIES",
)


def _controlled_git_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env.keys()):
        if key in _GIT_REDIRECT_VARS or key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_"):
            env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


class CleanupError(Exception):
    pass


def repo_root(timeout: int = 30) -> Path:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
            env=_controlled_git_env(),
        )
    except subprocess.TimeoutExpired as err:
        raise CleanupError(f"git rev-parse 超时（{timeout}s）") from err
    except FileNotFoundError as err:
        raise CleanupError("git 不可用") from err
    except OSError as err:
        raise CleanupError(f"git 执行失败：{err}") from err
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


def _is_protected(rel: str) -> bool:
    return rel in HARD_PROTECTED or any(
        rel == root or rel.startswith(root + "/") for root in PROTECTED_ROOT_DIRS
    )


def _prune(names: list[str], excluded: set[str]) -> list[str]:
    """按目录 basename 剪枝，使保护在任意深度生效（Issue #71）。"""
    return [name for name in names if name not in excluded]


@dataclass
class ScanResult:
    hits: list[dict]
    skipped: list[str]
    protected: list[dict]
    scratch_note: str | None = None


def _rel(repo: Path, path: Path) -> str:
    return os.path.relpath(path, repo).replace(os.sep, "/")


def _max_mtime(path: Path) -> float:
    """路径自身及全部子项的最大 lstat 修改时间；不跟随软链。"""
    try:
        newest = path.lstat().st_mtime
    except OSError:
        return 0.0
    if path.is_symlink() or not path.is_dir():
        return newest
    for root, dirs, files in os.walk(path, followlinks=False):
        for name in (*dirs, *files):
            try:
                newest = max(newest, (Path(root) / name).lstat().st_mtime)
            except OSError:
                continue
    return newest


def _days_ago(mtime: float, now: float) -> int:
    return max(0, int((now - mtime) // 86400))


def _issue_state(path: Path) -> tuple[bool, str]:
    """本地完整版报告是否已发布；解析失败按未发布处理。"""
    try:
        fm, _ = parse_front_matter_text(path.read_text(encoding="utf-8"), source=str(path))
    except Exception:
        return False, "front matter 损坏"
    value = fm.get("issue", "")
    if isinstance(value, str) and ISSUE_URL_RE.fullmatch(value.strip()):
        return True, value.strip()
    return False, "未发布"


def _scratch_metadata(repo: Path) -> tuple[dict[str, dict], dict[str, str]]:
    """返回 (有主路径 → 元数据, 无主路径 → 说明)；有主路径含待判定修改时间。"""
    owners: dict[str, dict] = {}
    unowned: dict[str, str] = {}
    pending_dir = repo / "docs/pending"
    if pending_dir.is_dir():
        references = open_scratch_references(RepoContext.for_root(repo))
        for rel, owner in references.items():
            owners[rel] = {"label": f"主人 {owner}", "mtime": _max_mtime(repo / rel)}
    issue_dir = repo / ISSUE_DIR
    if issue_dir.is_dir() and not issue_dir.is_symlink():
        for path in sorted(issue_dir.glob("*.md")):
            match = _REPORT_RE.match(path.name)
            if not match or match.group(2):
                continue
            issue_copy = issue_dir / f"{match.group(1)}.issue.md"
            files = [path] + ([issue_copy] if issue_copy.is_file() else [])
            published, _detail = _issue_state(path)
            if published:
                for item in files:
                    unowned[_rel(repo, item)] = "无主（已发布 issue）"
                continue
            mtime = max(_max_mtime(item) for item in files)
            for item in files:
                owners[_rel(repo, item)] = {"label": "主人 未发布 issue", "mtime": mtime}
        for path in sorted(issue_dir.glob("*.issue.md")):
            match = _REPORT_RE.match(path.name)
            if not match:
                continue
            if not (issue_dir / f"{match.group(1)}.md").is_file():
                unowned[_rel(repo, path)] = "无主（仅 .issue.md）"
    return owners, unowned


def _scratch_reason(
    repo: Path, rel: str, owners: dict[str, dict], unowned: dict[str, str], now: float
) -> tuple[bool, str]:
    matches = [
        path for path in owners
        if rel == path or rel.startswith(path.rstrip("/") + "/")
    ]
    if matches:
        owner = max(matches, key=len)
        meta = owners[owner]
        days = _days_ago(meta["mtime"], now)
        if now - meta["mtime"] < OWNED_GRACE_SECONDS:
            return True, f"{meta['label']}，最后修改 {days} 天前"
        return False, f"{meta['label']}，最后修改 {days} 天前，超期"
    for path, reason in unowned.items():
        if rel == path or rel.startswith(path.rstrip("/") + "/"):
            return False, reason
    return False, "无主"


def _scratch_policy(
    repo: Path,
) -> tuple[dict[str, dict], dict[str, str], str | None]:
    """返回 (owners, unowned, skip_note)；事务已移出 scratch，不再参与清理判定。"""
    try:
        owners, unowned = _scratch_metadata(repo)
    except Exception as error:
        return {}, {}, f"pending 条目损坏，无法确定引用：{error}"
    return owners, unowned, None


def scan_repository(repo: Path, categories: list[str], keeps: list[str]) -> ScanResult:
    """扫描并判定删除候选；带 scratch 时保护未结束事务与在用草稿。"""
    cats = set(categories)
    hits: list[dict] = []
    skipped: list[str] = []
    protected: list[dict] = []
    owners: dict[str, dict] = {}
    unowned: dict[str, str] = {}
    scratch_note: str | None = None
    if "scratch" in cats:
        owners, unowned, scratch_note = _scratch_policy(repo)
        if scratch_note is not None:
            cats.discard("scratch")
    now = time.time()
    protected_refs: set[str] = set()
    if "scratch" in cats:
        for rel, meta in owners.items():
            if now - meta["mtime"] < OWNED_GRACE_SECONDS:
                protected_refs.add(rel)
                protected.append({
                    "path": rel,
                    "reason": f"{meta['label']}，最后修改 {_days_ago(meta['mtime'], now)} 天前",
                })
                skipped.append(rel)
    effective_keeps = [*keeps, *sorted(protected_refs)]

    def reason_for(rel: str) -> tuple[bool, str]:
        return _scratch_reason(repo, rel, owners, unowned, now)

    bulk_roots: set[str] = set(BULK_CATEGORY_DIRS.values())
    for cat, rel_dir in BULK_CATEGORY_DIRS.items():
        if cat not in cats:
            continue
        base = repo / rel_dir
        if not base.is_dir():
            continue
        # 清理根本身若为指向仓库外的软链，防越界不递归（Issue #16, #55）
        if base.is_symlink() and not base.resolve().is_relative_to(repo.resolve()):
            continue
        for root, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = [d for d in dirs if d != ".git"]
            relroot = os.path.relpath(root, repo).replace(os.sep, "/")
            kept_dirs: list[str] = []
            for d in dirs:
                full_d = Path(root) / d
                rel = f"{relroot}/{d}"
                if cat == "scratch" and rel in protected_refs:
                    if rel not in skipped:
                        skipped.append(rel)
                    continue
                if _kept(rel, effective_keeps):
                    skipped.append(rel)
                else:
                    hit = {
                        "path": rel,
                        "category": cat,
                        "kind": "dir" if full_d.is_dir() and not full_d.is_symlink() else "file",
                    }
                    if cat == "scratch":
                        hit["reason"] = reason_for(rel)[1]
                    hits.append(hit)
                kept_dirs.append(d)
            dirs[:] = kept_dirs
            for f in files:
                rel = f"{relroot}/{f}"
                if _kept(rel, effective_keeps):
                    skipped.append(rel)
                    continue
                hit = {"path": rel, "category": cat, "kind": "file"}
                if cat == "scratch":
                    is_protected, reason = reason_for(rel)
                    if is_protected:
                        skipped.append(rel)
                        continue
                    hit["reason"] = reason
                hits.append(hit)

    # 未点名类别的目录整树不进普通 walk：bulk 三类（.scratch/artifacts/data）只由
    # 点名类别整树处理；node_modules 仅在点名 node 时接受 walk 分类。
    prune = {".git", *bulk_roots}
    if "node" not in cats:
        prune.add("node_modules")

    for root, dirs, files in os.walk(repo):
        relroot = os.path.relpath(root, repo)
        relroot = "" if relroot == "." else relroot.replace(os.sep, "/")
        dirs[:] = _prune(dirs, prune)
        if not relroot:
            dirs[:] = _prune(dirs, set(PROTECTED_ROOT_DIRS))
        entries = []
        for d in dirs:
            rel = f"{relroot}/{d}" if relroot else d
            entries.append((rel, True))
        for f in files:
            rel = f"{relroot}/{f}" if relroot else f
            entries.append((rel, False))
        for rel, is_dir in entries:
            if _is_protected(rel):
                continue
            cat = classify(Path(rel).name, is_dir)
            if cat is None or cat not in cats:
                continue
            if rel.startswith("docs/") and cat not in DOCS_ONLY_CATEGORIES:
                continue
            if _kept(rel, effective_keeps):
                skipped.append(rel)
                continue
            hits.append({"path": rel, "category": cat, "kind": "dir" if is_dir else "file"})

    # glob keep 不会在 _kept 里挡住祖先目录；按已跳过路径再滤一遍，避免 rmtree 父目录。
    keep_refs = [s for s in skipped]
    for k in effective_keeps:
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
    hits = sorted(filtered, key=lambda h: h["path"])
    skipped = list(dict.fromkeys(skipped))
    seen: set[str] = set()
    protected = [
        item for item in protected
        if not (item["path"] in seen or seen.add(item["path"]))
    ]
    return ScanResult(hits, skipped, protected, scratch_note)


def collect(repo: Path, categories: list[str], keeps: list[str]) -> tuple[list[dict], list[str]]:
    """返回 (hits, skipped)；skipped 含用户 keep 与在用草稿保护路径。"""
    result = scan_repository(repo, categories, keeps)
    return result.hits, result.skipped


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
        if _is_protected(h["path"]):
            continue
        # 二次防线：apply 独立重查 keep（文件级与目录级），防止调用方
        # 传入未经 collect 过滤的 hits（或 scan/apply 之间 keep 变化）。
        if _kept(h["path"], keeps):
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


def _print_scan(categories: list[str], result: ScanResult) -> None:
    print("## repo-cleanup 预览（未删除）")
    print("模式：dry-run")
    print(f"类别：{', '.join(categories)}")
    if result.scratch_note:
        print(f"scratch 已跳过：{result.scratch_note}；其它类别照常处理")
    print()
    rows = [["路径", "类别", "说明"]]
    for h in result.hits:
        if h["category"] == "scratch" and h.get("reason"):
            note = h["reason"]
        else:
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
    print(f"合计：{len(result.hits)} 项")
    if result.protected:
        print("保留（在用）：")
        for item in result.protected:
            print(f"- {item['path']} — {item['reason']}")
    protected_paths = {item["path"] for item in result.protected}
    user_skipped = [path for path in result.skipped if path not in protected_paths]
    if user_skipped:
        print("跳过（--keep 引用保护）：")
        for path in user_skipped:
            print(f"- {path}")
    print("下一步：确认后 `/repo-cleanup apply`（或带类别）。")


def _print_apply(deleted: list[str], result: ScanResult) -> None:
    print("## repo-cleanup 结果")
    print("模式：apply")
    if result.scratch_note:
        print(f"scratch 已跳过：{result.scratch_note}；其它类别照常处理")
    print("已删除：")
    if not deleted:
        print("- （无）")
    for path in deleted:
        print(f"- {path}")
    if result.protected:
        print("保留（在用）：")
        for item in result.protected:
            print(f"- {item['path']} — {item['reason']}")
    protected_paths = {item["path"] for item in result.protected}
    user_skipped = [path for path in result.skipped if path not in protected_paths]
    if user_skipped:
        print("跳过（保护/不存在）：")
        for path in user_skipped:
            print(f"- {path}")


def _resolve_categories(args) -> list[str]:
    if args.categories:
        for c in args.categories:
            if c not in ALL_CATEGORIES:
                raise CleanupError(f"未知类别 {c!r}；可选 {ALL_CATEGORIES}")
        return args.categories
    return list(DEFAULT_CATEGORIES)


def _execute(args, repo: Path, categories: list[str]) -> None:
    result = scan_repository(repo, categories, args.keep)
    if args._mode == "scan":
        _print_scan(categories, result)
        return
    if getattr(args, "expected_count", None) is not None:
        if len(result.hits) != args.expected_count:
            raise CleanupError(
                f"清理候选数量发生变化（预期 {args.expected_count}，实际 {len(result.hits)}）；"
                "请重新运行 scan 确认后再执行 apply"
            )
    effective_keeps = [*args.keep, *[item["path"] for item in result.protected]]
    deleted = apply_delete(repo, result.hits, effective_keeps)
    _print_apply(deleted, result)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="repo-cleanup 机械化执行器")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd in ("scan", "apply"):
        p = sub.add_parser(cmd, help="scan=只列不删；apply=删除列表内路径")
        p.add_argument("categories", nargs="*", metavar="CAT",
                       help=f"类别（默认 {DEFAULT_CATEGORIES}）；node/artifacts/data 须点名")
        p.add_argument("--keep", action="append", default=[], metavar="REL",
                       help="相对仓库根路径（glob 或前缀），排除不删（人工保留）")
        if cmd == "apply":
            p.add_argument("--expected-count", type=int, default=None,
                           help="核对候选总数；scan 到 apply 期间若候选变化则拒绝删除")
        p.set_defaults(_mode=cmd)
    args = parser.parse_args(argv)
    try:
        repo = repo_root()
        categories = _resolve_categories(args)
        if "scratch" in categories:
            try:
                with git_common_lock(repo, CONTROL_LOCK_NAME, blocking=False):
                    _execute(args, repo, categories)
            except LockBusyError as error:
                raise CleanupError(
                    "控制面忙：同步或 task 控制命令正在运行，稍后重试"
                ) from error
            except (TaskDataError, OSError) as error:
                raise CleanupError(f"无法获取控制面锁：{error}") from error
        else:
            _execute(args, repo, categories)
    except CleanupError as error:
        sys.exit(f"repo_cleanup: {error}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
