#!/usr/bin/env python3
"""pending.py - pending 编号只读分配 + 闭环归档入口。

用法：
  python3 scripts/pending.py next
  python3 scripts/pending.py archive p112 p113 p120-p146 --fix-ref t012 [--write]

`next` 扫描所有本地分支 git 树 + 所有 worktree 工作区的 docs/pending.md 与
docs/archive/pending.md，取全局最大 pNNN 编号加一。不写文件、不预留编号。

`archive` 把 docs/pending.md 中指定 pNNN 条目整条迁入 docs/archive/pending.md
「## 已处理待办」节末尾。强制要求 --fix-ref TID（闭环标识），避免把未闭环条目
误迁 archive。「不办」节条目拒迁。
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _id_scan import IdScanError, scan_max_id

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY_RE = re.compile(r"^ {0,3}###[ \t]+p([0-9]{3,})(?=[ \t]|$)")
REL_PATHS = ("docs/pending.md", "docs/archive/pending.md")
PENDING_PATH = REPO_ROOT / "docs/pending.md"
ARCHIVE_PATH = REPO_ROOT / "docs/archive/pending.md"
ARCHIVE_SECTION_RE = re.compile(r"^ {0,3}##[ \t]+已处理待办[ \t]*$")
SECTION_RE = re.compile(r"^ {0,3}##[ \t]+")
H3_RE = re.compile(r"^ {0,3}###[ \t]+p([0-9]{3,})(?=[ \t]|$)")
HANDLE_RE = re.compile(r"^[ \t]*-[ \t]*处理[ \t]*[:：][ \t]*(.+?)[ \t]*$")
PNNN_RANGE_RE = re.compile(r"^p([0-9]{3,})(?:-p?([0-9]{3,}))?$")


def next_pending_id() -> str:
    maximum = scan_max_id(REPO_ROOT, ENTRY_RE, REL_PATHS)
    return f"p{maximum + 1:03d}"


def cmd_next(_args: argparse.Namespace) -> None:
    print(next_pending_id())


def parse_ids(specs: list[str]) -> list[int]:
    """解析 pNNN / pNNN-pNNN / pNNN-NNN 列表，返回升序去重后的整数列表。"""
    out: list[int] = []
    for spec in specs:
        m = PNNN_RANGE_RE.match(spec)
        if not m:
            raise IdScanError(f"非法编号规格：{spec!r}（期望 pNNN 或 pNNN-pNNN）")
        start = int(m.group(1))
        end_str = m.group(2)
        if end_str is None:
            out.append(start)
            continue
        end = int(end_str)
        # p112-p146 与 p112-146 都接受
        if end < 100:
            end = start - start % 1000 + end
            if end < start:
                end += 1000
        if end < start:
            raise IdScanError(f"区间起点大于终点：{spec!r}")
        out.extend(range(start, end + 1))
    return sorted(set(out))


class Entry:
    """一个 pNNN 条目块。"""

    def __init__(
        self,
        number: int,
        title_line: str,
        body_lines: list[str],
        section: str,
    ) -> None:
        self.number = number
        self.title_line = title_line
        self.body_lines = body_lines
        self.section = section  # 所在节：「待办」/「不办」/ 其它

    def handle_line_index(self) -> int | None:
        """返回 `- 处理:` 行在 body_lines 中的下标；无则 None。"""
        for i, line in enumerate(self.body_lines):
            if HANDLE_RE.match(line):
                return i
        return None

    def set_handle(self, tid: str) -> bool:
        """把 `- 处理:` 行改写为 tid；未找到则返回 False。"""
        idx = self.handle_line_index()
        if idx is None:
            return False
        self.body_lines[idx] = f"- 处理：{tid}"
        return True


class PendingDoc:
    """pending.md 解析结果：entries + 原始行 + 行归属。"""

    def __init__(
        self,
        lines: list[str],
        entries: list[Entry],
        line_owner: list[int | None],
    ) -> None:
        self.lines = lines
        self.entries = entries
        self.line_owner = line_owner

    def render_without(self, drop_numbers: set[int]) -> str:
        """渲染文档，剔除指定编号的 entry 块，收敛多余空行。"""
        drop_indices = {
            i
            for i, e in enumerate(self.entries)
            if e.number in drop_numbers
        }
        kept: list[str] = []
        for idx, line in enumerate(self.lines):
            owner = self.line_owner[idx]
            if owner is not None and owner in drop_indices:
                continue
            kept.append(line)
        text = _collapse_blank_runs(kept)
        if not text.endswith("\n"):
            text += "\n"
        return text


def parse_pending(text: str) -> PendingDoc:
    """解析 docs/pending.md。

    返回 PendingDoc，含按文件顺序的 entries、原始行、行归属（每个原始行属于
    哪个 entry 下标，None 表示不属于任何 entry——H1、节标题、说明、注释等）。

    代码围栏内的 `### pNNN` 不识别为条目（与 `_id_scan` 编号扫描语义一致）。
    """
    lines = text.splitlines()
    in_fence = _fence_mask(lines)
    current_section = ""
    entries: list[Entry] = []
    line_owner: list[int | None] = [None] * len(lines)

    i = 0
    while i < len(lines):
        line = lines[i]
        sec_match = SECTION_RE.match(line)
        if sec_match and not H3_RE.match(line):
            current_section = line.strip().lstrip("#").strip()
            line_owner[i] = None
            i += 1
            continue
        h3 = H3_RE.match(line) if not in_fence[i] else None
        if h3:
            number = int(h3.group(1))
            body: list[str] = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if not in_fence[j] and (
                    H3_RE.match(nxt) or SECTION_RE.match(nxt)
                ):
                    break
                body.append(nxt)
                j += 1
            while body and body[-1].strip() == "":
                body.pop()
            entries.append(Entry(number, line, body, current_section))
            for k in range(i, j):
                line_owner[k] = len(entries) - 1
            i = j
            continue
        i += 1

    return PendingDoc(lines, entries, line_owner)


def _fence_mask(lines: list[str]) -> list[bool]:
    """返回每行是否处于代码围栏内。围栏行本身标记为 True（不识别 H3）。"""
    fence_re = re.compile(r"^ {0,3}(`{3,}|~{3,})")
    mask = [False] * len(lines)
    fence_marker = None
    for i, line in enumerate(lines):
        if fence_marker is None:
            m = fence_re.match(line)
            if m:
                fence_marker = (m.group(1)[0], len(m.group(1)))
                mask[i] = True
            else:
                mask[i] = False
        else:
            mask[i] = True
            close = re.match(r"^ {0,3}(`{3,}|~{3,})[ \t]*$", line)
            if (
                close
                and close.group(1)[0] == fence_marker[0]
                and len(close.group(1)) >= fence_marker[1]
            ):
                fence_marker = None
    return mask


def _collapse_blank_runs(lines: list[str]) -> str:
    out: list[str] = []
    prev_blank = False
    for line in lines:
        is_blank = line.strip() == ""
        if is_blank and prev_blank:
            continue
        out.append(line)
        prev_blank = is_blank
    while out and out[-1].strip() == "":
        out.pop()
    while out and out[0].strip() == "":
        out.pop(0)
    return "\n".join(out) + "\n"


def parse_archive(text: str) -> set[int]:
    """扫 docs/archive/pending.md 中已存在的 pNNN 编号。"""
    existing: set[int] = set()
    # 复用 _id_scan 的可见行过滤（剔除代码围栏 / 引用 / 注释）
    from _id_scan import visible_markdown_lines

    for line in visible_markdown_lines(text):
        m = H3_RE.match(line)
        if m:
            existing.add(int(m.group(1)))
    return existing


def find_archive_section_line(text: str) -> int | None:
    """返回 `## 已处理待办` 节标题的行下标；不存在返回 None。"""
    for i, line in enumerate(text.splitlines()):
        if ARCHIVE_SECTION_RE.match(line):
            return i
    return None


def cmd_archive(args: argparse.Namespace) -> None:
    ids = parse_ids(args.ids)
    if not ids:
        raise IdScanError("未指定要归档的编号")

    if not PENDING_PATH.is_file():
        raise IdScanError(f"{PENDING_PATH} 不存在")
    if not ARCHIVE_PATH.is_file():
        raise IdScanError(f"{ARCHIVE_PATH} 不存在（请先创建并写入「## 已处理待办」节标题）")

    pending_text = PENDING_PATH.read_text(encoding="utf-8")
    archive_text = ARCHIVE_PATH.read_text(encoding="utf-8")

    doc = parse_pending(pending_text)
    by_number = {e.number: e for e in doc.entries}

    missing = [n for n in ids if n not in by_number]
    if missing:
        joined = ", ".join(f"p{n:03d}" for n in missing)
        raise IdScanError(f"docs/pending.md 中未找到：{joined}")

    not_doing = [by_number[n] for n in ids if by_number[n].section == "不办"]
    if not_doing:
        joined = ", ".join(f"p{e.number:03d}" for e in not_doing)
        raise IdScanError(
            f"「不办」节条目属暂搁而非闭环，拒迁 archive：{joined}；"
            f"如需彻底闭环请先在 docs/pending.md 将其移回「待办」节并改 - 处理"
        )

    # archive 已存在的编号报错（防重）；repo-hygiene 也不允许同号双存
    existing_in_archive = parse_archive(archive_text)
    duplicated = [n for n in ids if n in existing_in_archive]
    if duplicated:
        joined = ", ".join(f"p{n:03d}" for n in duplicated)
        raise IdScanError(f"docs/archive/pending.md 中已存在：{joined}（禁止重复归档）")

    # 应用 --fix-ref
    tid = args.fix_ref.strip()
    for n in ids:
        entry = by_number[n]
        ok = entry.set_handle(tid)
        if not ok:
            raise IdScanError(
                f"p{n:03d} 缺少 `- 处理:` 字段，无法写入 tid；请先补字段"
            )

    # 渲染 archive 追加块
    appendage = _render_archive_appendage([by_number[n] for n in ids])

    # 渲染新的 pending.md：只剔除本次迁移的编号
    new_pending = doc.render_without(set(ids))

    if not args.write:
        sys.stderr.write("dry-run；以下为拟定改动，加 --write 落盘。\n")
        sys.stderr.write(
            f"  从 docs/pending.md 迁出 {len(ids)} 条："
            + ", ".join(f"p{n:03d}" for n in ids)
            + "\n"
        )
        sys.stderr.write(f"  追加到 docs/archive/pending.md「## 已处理待办」节末尾\n")
        sys.stderr.write(f"  - 处理 字段统一改写为：{tid}\n")
        return

    # 追加到 archive：定位「## 已处理待办」节，把新块加在该节末尾（文件末尾或下一个 ## 之前）
    archive_lines = archive_text.splitlines()
    sec_idx = find_archive_section_line(archive_text)
    if sec_idx is None:
        raise IdScanError(
            f"{ARCHIVE_PATH} 缺少「## 已处理待办」节标题；请先补齐"
        )
    # 找该节结束位置：下一个节级 ## 或 EOF
    insert_at = len(archive_lines)
    for k in range(sec_idx + 1, len(archive_lines)):
        if SECTION_RE.match(archive_lines[k]) and not H3_RE.match(archive_lines[k]):
            insert_at = k
            break
    # 剥除该节尾部多余空行（保留节标题后直接追加干净内容）
    while insert_at > 0 and archive_lines[insert_at - 1].strip() == "":
        insert_at -= 1
    # 在插入点前确保一个空行分隔
    block = ["", appendage.rstrip("\n"), ""]
    new_archive_lines = archive_lines[:insert_at] + block + archive_lines[insert_at:]
    new_archive_text = "\n".join(new_archive_lines)
    if not new_archive_text.endswith("\n"):
        new_archive_text += "\n"

    PENDING_PATH.write_text(new_pending, encoding="utf-8")
    ARCHIVE_PATH.write_text(new_archive_text, encoding="utf-8")
    sys.stderr.write(
        f"已迁移 {len(ids)} 条到 docs/archive/pending.md："
        + ", ".join(f"p{n:03d}" for n in ids)
        + f"；- 处理 = {tid}\n"
    )


def _render_archive_appendage(entries: list[Entry]) -> str:
    parts: list[str] = []
    for e in entries:
        parts.append(e.title_line.rstrip("\n"))
        for body_line in e.body_lines:
            parts.append(body_line.rstrip("\n"))
        parts.append("")  # 条目之间空行
    return "\n".join(parts).rstrip() + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="pending 编号分配与闭环归档"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    next_parser = sub.add_parser("next", help="输出全局最大编号加一")
    next_parser.set_defaults(func=cmd_next)

    archive_parser = sub.add_parser(
        "archive",
        help="把指定 pNNN 从 docs/pending.md 迁入 docs/archive/pending.md",
    )
    archive_parser.add_argument(
        "ids",
        nargs="+",
        metavar="pNNN|pNNN-pNNN",
        help="要归档的编号或区间（如 p112 p113 或 p120-p146）",
    )
    archive_parser.add_argument(
        "--fix-ref",
        required=True,
        metavar="TID",
        help="闭环 task id（写入 - 处理: 字段，如 t012）",
    )
    archive_parser.add_argument(
        "--write",
        action="store_true",
        help="落盘（默认 dry-run，仅打印拟定改动）",
    )
    archive_parser.set_defaults(func=cmd_archive)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except IdScanError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
