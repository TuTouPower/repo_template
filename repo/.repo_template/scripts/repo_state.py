#!/usr/bin/env python3
"""repo_state.py — 评估完整工作树相对 baseline commit 的状态。

为什么存在
----------
task 收尾清洁度检查与 deliverable 核对必须看到执行产生的全部改动——无论已
commit 还是留在工作树（staged / unstaged / 未跟踪）。`git diff <baseline>..HEAD`
只比较两个 commit，未 commit 的工作在它面前完全不可见：deliverable 误报
missing，清洁度 grep 误报 0。

策略（完整工作树 vs baseline）
-----------------------------
  tracked 变更（committed + staged + unstaged + deleted）
      = git diff <baseline>          # 单 ref，不是 <baseline>..HEAD
  未跟踪新文件（从未 git add 的 deliverable）
      = git ls-files --others --exclude-standard
  baseline 不可解析或非 git 仓库
      = 立即非零退出（fail-closed），不退化为部分结果或存在性判断

所有 git 命令统一在仓库根目录执行（`git -C <root>`），因此 diff 路径、
ls-files 未跟踪路径与 --exclude glob 都使用同一套「仓库根相对路径」口径；
从任意子目录调用都不会漏检或误检。

本脚本只读，不改仓库与 index。

用法：
  repo_state.py deliverable   <baseline> <path>
      -> "present — <evidence>"（exit 0）| "missing"（exit 1）
  repo_state.py changed-files <baseline> [--exclude GLOB]...
      -> 相对 baseline 变更的路径（tracked + untracked + deleted），每行一个
  repo_state.py added-lines   <baseline> [--exclude GLOB]...
      -> 相对 baseline 的全部新增行：tracked diff 的 '+' 行 + 未跟踪文件全文。
          喂给 grep 做清洁度计数。

--exclude 取 fnmatch glob（如 'docs/*'），同时作用于 diff 路径与未跟踪文件。
"""

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    """返回仓库根绝对路径；非 git 仓库或无法解析时立即退出（fail-closed）。"""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        sys.exit("非 git 仓库，repo_state 不可用")
    return Path(result.stdout.strip())


def _git(args: list[str], *, root: Path, check: bool = False) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if check and result.returncode != 0:
        sys.exit(f"git {' '.join(args)} 失败：{result.stderr.strip()}")
    return result


def _resolve_baseline(baseline: str, root: Path | None = None) -> str:
    """将 baseline 解析为确定 commit SHA；不可解析时返回空字符串。"""
    if not baseline or baseline.startswith("-"):
        return ""
    result = _git(
        ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{baseline}^{{commit}}"],
        root=root or _repo_root(),
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _require_baseline(baseline: str, cmd_name: str) -> tuple[str, Path]:
    root = _repo_root()
    resolved = _resolve_baseline(baseline, root)
    if not resolved:
        sys.exit(f"baseline {baseline!r} 不可解析为有效 commit，{cmd_name} 拒绝降级执行")
    return resolved, root


def _untracked_files(excludes: list[str], root: Path) -> list[str]:
    # -z：NUL 分隔且从不引号化，非 ASCII 路径原样输出（core.quotePath 无效）。
    result = _git(["ls-files", "--others", "--exclude-standard", "-z"], root=root, check=True)
    paths = [line for line in result.stdout.split("\0") if line]
    return [p for p in paths if not _excluded(p, excludes)]


def _excluded(path: str, excludes: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, glob) for glob in excludes)


def _diff_name_only(baseline: str, excludes: list[str], root: Path) -> list[str]:
    # -z：NUL 分隔且从不引号化，非 ASCII 路径原样输出（core.quotePath 无效）。
    result = _git(
        ["diff", "--name-only", "-z", "--end-of-options", baseline], root=root, check=True
    )
    paths = [line for line in result.stdout.split("\0") if line]
    return [p for p in paths if not _excluded(p, excludes)]


def _pathspec(args_path: str, root: Path) -> str | None:
    """把用户传入路径（相对 cwd 或绝对）转成仓库根相对 pathspec；仓库外返回 None。"""
    path = Path(args_path)
    absolute = path if path.is_absolute() else (Path.cwd() / path)
    try:
        return absolute.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def cmd_deliverable(args) -> int:
    baseline, root = _require_baseline(args.baseline, "deliverable")
    rel = _pathspec(args.path, root)
    if rel is None or not (root / rel).exists():
        # 含 tracked 文件相对 baseline 被删除的情况
        print("missing")
        return 1
    untracked = _git(
        ["ls-files", "--others", "--exclude-standard", "--", rel], root=root, check=True
    ).stdout.splitlines()
    if any(line for line in untracked):
        print("present — untracked new file")
        return 0
    stat = _git(
        ["diff", "--stat", "--end-of-options", baseline, "--", rel], root=root, check=True
    ).stdout.strip()
    if stat:
        print(f"present — changed vs baseline ({stat.splitlines()[-1].strip()})")
        return 0
    print("present — exists unchanged")
    return 0


def cmd_changed_files(args) -> int:
    baseline, root = _require_baseline(args.baseline, "changed-files")
    seen = set()
    for path in _diff_name_only(baseline, args.exclude, root) + _untracked_files(args.exclude, root):
        if path not in seen:
            seen.add(path)
            print(path)
    return 0


def cmd_added_lines(args) -> int:
    baseline, root = _require_baseline(args.baseline, "added-lines")
    # 锁定前缀格式：用户 config（diff.noprefix / diff.mnemonicPrefix）会改变
    # '+++ b/<path>' 头部形态，导致 --exclude 匹配失效。
    # core.quotePath=false：非 ASCII 路径原样出现在头部，不转义为 C 引号形式。
    result = _git(
        ["-c", "core.quotePath=false",
         "-c", "diff.noprefix=false", "-c", "diff.mnemonicPrefix=false",
         "diff", "--end-of-options", baseline],
        root=root,
        check=True,
    )
    current_path = ""
    skip_file = False
    for line in result.stdout.splitlines():
        if line.startswith("+++ "):
            current_path = line[4:].removeprefix("b/")
            skip_file = _excluded(current_path, args.exclude)
            continue
        if skip_file:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            print(line[1:])
    failed = False
    for path in _untracked_files(args.exclude, root):
        file_path = root / path
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            print(
                f"WARNING: 未跟踪文件读取时已消失，新增行未计入：{path}",
                file=sys.stderr,
            )
            continue
        except OSError as error:
            print(
                f"WARNING: 未跟踪文件读取失败，新增行未计入：{path}（{error}）",
                file=sys.stderr,
            )
            failed = True
            continue
        for line in content.splitlines():
            print(line)
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="完整工作树 vs baseline 只读取数")
    sub = parser.add_subparsers(dest="cmd", required=True)

    deliverable = sub.add_parser("deliverable", help="核对单个 deliverable 路径")
    deliverable.add_argument("baseline")
    deliverable.add_argument("path")
    deliverable.set_defaults(func=cmd_deliverable)

    changed = sub.add_parser("changed-files", help="列出相对 baseline 变更的路径")
    changed.add_argument("baseline")
    changed.add_argument("--exclude", action="append", default=[])
    changed.set_defaults(func=cmd_changed_files)

    added = sub.add_parser("added-lines", help="输出相对 baseline 的全部新增行")
    added.add_argument("baseline")
    added.add_argument("--exclude", action="append", default=[])
    added.set_defaults(func=cmd_added_lines)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
