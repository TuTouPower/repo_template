#!/usr/bin/env python3
"""repo_hygiene.py — repo-hygiene skill 的机械迁移动作执行器。

把已闭环 / 已过时冷文稿迁入 docs/archive/ 的确定性文件操作交给脚本执行：
  - archive-review：docs/reviews/review_*/ 整目录迁 docs/archive/reviews/（git mv）

「哪些确认完结 / 过时」是语义裁定，属 skill 判断；本脚本只做动作。默认 dry-run，
--write 才落盘。不 commit（commit 门禁在 skill）。

用法：
  repo_hygiene.py archive-review review_xxx [--write]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REVIEWS_DIR = REPO_ROOT / "docs/reviews"
ARCHIVE_REVIEWS = REPO_ROOT / "docs/archive/reviews"


class HygieneError(Exception):
    pass


def _git(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as err:
        raise HygieneError(f"git {' '.join(args)} 超时（{timeout}s）") from err


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


# ---------------------------------------------------------------------------
# archive-review（git mv 整目录）
# ---------------------------------------------------------------------------

def _move_leftover(src: Path, dst: Path) -> None:
    """git mv 只搬已跟踪文件；把源侧残留（gitignore 的 _meta/ 等）并入目标。"""
    if not src.exists():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for item in sorted(src.iterdir(), key=lambda p: p.name):
        dest = dst / item.name
        if dest.exists():
            if item.is_dir() and dest.is_dir():
                _move_leftover(item, dest)
                continue
            raise HygieneError(f"残留与目标冲突：{_rel(item)}")
        shutil.move(str(item), str(dest))
    try:
        src.rmdir()
    except OSError as error:
        raise HygieneError(f"源目录未清空 {_rel(src)}：{error}") from error


def _move_dir(src: Path, dst: Path, write: bool) -> None:
    if dst.exists():
        raise HygieneError(f"目标已存在 {_rel(dst)}；不覆盖或合并")
    if write:
        dst.parent.mkdir(parents=True, exist_ok=True)
        r = _git("mv", _rel(src), _rel(dst))
        if r.returncode != 0:
            raise HygieneError(f"git mv 失败：{r.stderr.strip()}")
        if src.exists():
            _move_leftover(src, dst)
        print(f"已迁移：{_rel(src)} -> {_rel(dst)}")
    else:
        print(f"将迁移（dry-run）：{_rel(src)} -> {_rel(dst)}")


def cmd_archive_review(args: argparse.Namespace) -> None:
    name = args.dir
    if "/" in name or "\\" in name or ".." in name or Path(name).name != name:
        raise HygieneError(f"review 目录名非法：{name!r} 不能包含路径分隔符或父目录引用")
    if not name.startswith("review_"):
        raise HygieneError(f"review 目录名应以 review_ 开头，收到 {name!r}")
    if not REVIEWS_DIR.is_dir():
        raise HygieneError(f"缺 {_rel(REVIEWS_DIR)}")
    src = REVIEWS_DIR / name
    if not src.is_dir():
        raise HygieneError(f"review 目录不存在：{_rel(src)}")
    _move_dir(src, ARCHIVE_REVIEWS / name, args.write)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="repo-hygiene 机械迁移动作")
    sub = parser.add_subparsers(dest="cmd", required=True)

    review = sub.add_parser("archive-review", help="确认过时的 review 目录迁 archive")
    review.add_argument("dir", help="review 目录名（如 review_my_check）")
    review.add_argument("--write", action="store_true")
    review.set_defaults(func=cmd_archive_review)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except HygieneError as error:
        sys.exit(f"repo_hygiene: {error}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
