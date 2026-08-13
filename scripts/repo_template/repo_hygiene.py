#!/usr/bin/env python3
"""repo_hygiene.py — repo-hygiene skill 的机械迁移动作执行器。

把已闭环 / 已过时内容迁入 docs/archive/ 的确定性文件操作交给脚本执行：
  - archive-handoff：handoff.md 除最新一节外整段迁入 docs/archive/handoff.md（append）
  - archive-spike   ：docs/spikes/sNNN_{slug}/ 整目录迁 docs/archive/spikes/（git mv）
  - archive-review  ：docs/reviews/review_*/ 整目录迁 docs/archive/reviews/（git mv）

「哪些确认完结 / 过时」是语义裁定，属 skill 判断；本脚本只做动作。默认 dry-run，
--write 才落盘。不 commit（commit 门禁在 skill）。

用法：
  repo_hygiene.py archive-handoff [--write]
  repo_hygiene.py archive-spike sNNN [--write]
  repo_hygiene.py archive-review review_xxx [--write]
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SPIKES_DIR = REPO_ROOT / "docs/spikes"
ARCHIVE_SPIKES = REPO_ROOT / "docs/archive/spikes"
REVIEWS_DIR = REPO_ROOT / "docs/reviews"
ARCHIVE_REVIEWS = REPO_ROOT / "docs/archive/reviews"
HANDOFF = REPO_ROOT / "docs/handoff.md"
ARCHIVE_HANDOFF = REPO_ROOT / "docs/archive/handoff.md"
SPIKE_DIR_RE = re.compile(r"^s[0-9]+_[a-z0-9_]+$")
SPIKE_SID_RE = re.compile(r"^s[0-9]+$")
ARCHIVE_HANDOFF_HEADER = (
    "# 历史交接\n\n"
    "由 `repo-hygiene` 从 `docs/handoff.md` 迁入。**只追加**，禁止截断或改写已归档段落。\n"
)


class HygieneError(Exception):
    pass


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


# ---------------------------------------------------------------------------
# archive-handoff
# ---------------------------------------------------------------------------

def _split_handoff(text: str) -> tuple[list[str], list[list[str]]]:
    """返回 (header, sections)。header=首个 H2 前的行；sections=各 H2 节（含标题行）。"""
    lines = text.splitlines()
    h2 = [i for i, line in enumerate(lines) if line.startswith("## ")]
    if not h2:
        return lines, []
    header = lines[: h2[0]]
    sections = []
    for idx, start in enumerate(h2):
        end = h2[idx + 1] if idx + 1 < len(h2) else len(lines)
        sections.append(lines[start:end])
    return header, sections


def _join_blocks(blocks: list[list[str]]) -> str:
    return "\n\n".join("\n".join(b).strip() for b in blocks) + "\n"


def cmd_archive_handoff(args: argparse.Namespace) -> None:
    if not HANDOFF.is_file():
        raise HygieneError(f"缺 {_rel(HANDOFF)}")
    header, sections = _split_handoff(HANDOFF.read_text(encoding="utf-8"))
    if len(sections) <= 1:
        print("handoff 只有 0/1 节，无过时段落可迁")
        return
    stale = sections[:-1]
    latest = sections[-1]
    print(f"将迁移 {len(stale)} 个过时段落（保留最后节：{latest[0] if latest else '?'}）:")
    for section in stale:
        print(f"- {section[0] if section else '?'}")
    if not args.write:
        print("dry-run；加 --write 落盘")
        return
    stale_text = _join_blocks(stale)
    ARCHIVE_HANDOFF.parent.mkdir(parents=True, exist_ok=True)
    if ARCHIVE_HANDOFF.is_file():
        existing = ARCHIVE_HANDOFF.read_text(encoding="utf-8")
        if existing and not existing.endswith("\n"):
            existing += "\n"
        if existing and not existing.endswith("\n\n"):
            existing += "\n"
        ARCHIVE_HANDOFF.write_text(existing + stale_text, encoding="utf-8")
    else:
        ARCHIVE_HANDOFF.write_text(ARCHIVE_HANDOFF_HEADER + stale_text, encoding="utf-8")
    body = "\n".join(header).strip()
    HANDOFF.write_text((body + "\n\n" + _join_blocks([latest])) if body else _join_blocks([latest]), encoding="utf-8")
    print(f"已迁 {len(stale)} 节到 {_rel(ARCHIVE_HANDOFF)}；handoff.md 保留最新节")


# ---------------------------------------------------------------------------
# archive-spike / archive-review（git mv 整目录）
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


def cmd_archive_spike(args: argparse.Namespace) -> None:
    sid = args.sid
    if not SPIKE_SID_RE.match(sid):
        raise HygieneError(f"spike sid 格式应为 sNNN，收到 {sid!r}")
    if not SPIKES_DIR.is_dir():
        raise HygieneError(f"缺 {_rel(SPIKES_DIR)}")
    matches = [p for p in SPIKES_DIR.iterdir()
               if p.is_dir() and p.name.startswith(sid + "_") and SPIKE_DIR_RE.match(p.name)]
    if len(matches) != 1:
        raise HygieneError(f"sid {sid} 匹配 {len(matches)} 个 spike 目录；期望恰好 1 个")
    _move_dir(matches[0], ARCHIVE_SPIKES / matches[0].name, args.write)


def cmd_archive_review(args: argparse.Namespace) -> None:
    name = args.dir
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

    handoff = sub.add_parser("archive-handoff", help="handoff 过时段落迁 archive（保留最新节）")
    handoff.add_argument("--write", action="store_true", help="落盘（默认 dry-run）")
    handoff.set_defaults(func=cmd_archive_handoff)

    spike = sub.add_parser("archive-spike", help="完结 spike 整目录迁 archive")
    spike.add_argument("sid", help="spike sid（如 s001）")
    spike.add_argument("--write", action="store_true")
    spike.set_defaults(func=cmd_archive_spike)

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
