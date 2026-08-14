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
  baseline 不可解析（no-git / 假 sha / 非 git 仓）
      = 退化为文件系统存在性判断，并在 stderr 声明覆盖降级

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


def _git(args: list[str], *, check: bool = False) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if check and result.returncode != 0:
        sys.exit(f"git {' '.join(args)} 失败：{result.stderr.strip()}")
    return result


def _in_git_repo() -> bool:
    return _git(["rev-parse", "--is-inside-work-tree"]).returncode == 0


def _baseline_ok(baseline: str) -> bool:
    if not baseline or baseline == "no-git":
        return False
    return _git(["rev-parse", "--verify", "--quiet", f"{baseline}^{{commit}}"]).returncode == 0


def _warn_degraded(baseline: str) -> None:
    print(
        f"WARNING: baseline {baseline!r} 不可解析，退化为存在性/未跟踪判断，覆盖降级",
        file=sys.stderr,
    )


def _untracked_files(excludes: list[str]) -> list[str]:
    result = _git(["ls-files", "--others", "--exclude-standard"], check=True)
    paths = [line for line in result.stdout.splitlines() if line]
    return [p for p in paths if not _excluded(p, excludes)]


def _excluded(path: str, excludes: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, glob) for glob in excludes)


def _diff_name_only(baseline: str, excludes: list[str]) -> list[str]:
    result = _git(["diff", "--name-only", baseline], check=True)
    paths = [line for line in result.stdout.splitlines() if line]
    return [p for p in paths if not _excluded(p, excludes)]


def cmd_deliverable(args) -> int:
    path = Path(args.path)
    if _in_git_repo() and _baseline_ok(args.baseline):
        if not path.exists():
            # 含 tracked 文件相对 baseline 被删除的情况
            print("missing")
            return 1
        untracked = _git(
            ["ls-files", "--others", "--exclude-standard", "--", args.path], check=True
        ).stdout.splitlines()
        if any(line for line in untracked):
            print("present — untracked new file")
            return 0
        stat = _git(["diff", "--stat", args.baseline, "--", args.path], check=True).stdout.strip()
        if stat:
            print(f"present — changed vs baseline ({stat.splitlines()[-1].strip()})")
            return 0
        print("present — exists unchanged")
        return 0
    _warn_degraded(args.baseline)
    if path.exists():
        print("present — exists (baseline unavailable)")
        return 0
    print("missing")
    return 1


def cmd_changed_files(args) -> int:
    if not _in_git_repo():
        sys.exit("非 git 仓库，changed-files 不可用")
    if not _baseline_ok(args.baseline):
        _warn_degraded(args.baseline)
        for path in _untracked_files(args.exclude):
            print(path)
        return 0
    seen = set()
    for path in _diff_name_only(args.baseline, args.exclude) + _untracked_files(args.exclude):
        if path not in seen:
            seen.add(path)
            print(path)
    return 0


def cmd_added_lines(args) -> int:
    if not _in_git_repo():
        sys.exit("非 git 仓库，added-lines 不可用")
    if not _baseline_ok(args.baseline):
        _warn_degraded(args.baseline)
    else:
        # 锁定前缀格式：用户 config（diff.noprefix / diff.mnemonicPrefix）会改变
        # '+++ b/<path>' 头部形态，导致 --exclude 匹配失效。
        result = _git(
            ["-c", "diff.noprefix=false", "-c", "diff.mnemonicPrefix=false",
             "diff", args.baseline],
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
    for path in _untracked_files(args.exclude):
        file_path = Path(path)
        if not file_path.is_file():
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in content.splitlines():
            print(line)
    return 0


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
