#!/usr/bin/env python3
"""repo_hygiene.py — repo-hygiene skill 的机械迁移动作执行器。

把已闭环 / 已过时冷文稿迁入 docs/archive/ 的确定性文件操作交给脚本执行：
  - archive-review：docs/reviews/review_*/ 整目录迁 docs/archive/reviews/
    （单次目录 move + git add -A，失败整目录回滚，不留部分状态）

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
# archive-review（单次目录移动 + 统一 git add）
# ---------------------------------------------------------------------------


def _delivery_files(dst: Path) -> list[Path]:
    """显式枚举进入 index 的交付文件：任何层级的 `_meta/` 过程文件永不被 add。

    不依赖消费仓 .gitignore 是否配了 `_meta/` 忽略规则（DS L261）：旧仓缺该规则时，
    隐式 `git add -A -- <src> <dst>` 会把过程文件 stage 进 index；此处只 add 枚举出的
    交付文件，契约明确优于约定。
    """
    files = []
    for path in sorted(dst.rglob("*")):
        rel = path.relative_to(dst)
        if "_meta" in rel.parts:
            continue
        if path.is_symlink():
            # 链接本身入 index，不跟随目标；目录软链亦只收录这一条，不下钻。
            files.append(path)
            continue
        if path.is_dir():
            continue
        if any((dst / parent).is_symlink() for parent in rel.parents if parent != Path(".")):
            # rglob 跟随目录软链下钻出的仓外目标：顶层链接本身已收录，此处跳过。
            continue
        files.append(path)
    return files


def _move_dir(src: Path, dst: Path, write: bool) -> None:
    if dst.exists():
        raise HygieneError(f"目标已存在 {_rel(dst)}；不覆盖或合并")
    if not write:
        print(f"将迁移（dry-run）：{_rel(src)} -> {_rel(dst)}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    # 整目录一次 move：要么全成功要么全失败，不产生「源与目标各留一部分」的中间状态。
    # git mv 只搬 tracked 文件、需二次搬运 gitignore 残留；残留搬运失败时源与目标
    # 会各留一部分且无回滚，下次运行因 dst.exists() 永久拒绝（DS L261）。
    try:
        shutil.move(str(src), str(dst))
    except OSError as error:
        raise HygieneError(f"移动目录失败：{error}") from error
    # 只 add 显式枚举的交付文件 + 源删除：_meta/ 等过程文件随目录一起移动，
    # 但永不进入 index（即使消费仓 .gitignore 缺忽略规则）。
    targets = [_rel(src), *(_rel(path) for path in _delivery_files(dst))]
    r = _git("add", "-A", "--", *targets)
    if r.returncode != 0:
        shutil.move(str(dst), str(src))
        raise HygieneError(f"git add -A 失败，已回滚目录：{r.stderr.strip()}")
    print(f"已迁移：{_rel(src)} -> {_rel(dst)}")


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
