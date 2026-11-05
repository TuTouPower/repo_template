"""docs/specs_index.md 的唯一 schema 定义与校验。

finish 与 template_sync 共用本模块，不在别处复读表格。
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from . import context as ctx
from .documents import _visible_markdown_numbered_lines

SPECS_INDEX_REL = "docs/specs_index.md"
HEADER_CELLS = ("slug", "task 清单", "最后更新时间")
SEPARATOR_RE = re.compile(r":?-+:?")
SLUG_RE = ctx.SLUG_RE
TASK_LIST_RE = re.compile(
    r"^(?:t[0-9]{3,}|baseline)(?:[ \t]*,[ \t]*(?:t[0-9]{3,}|baseline))*$"
)
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def _visible_lines(text: str) -> list[tuple[int, str]]:
    """复用 documents 的 fenced code 判断（带行号），额外剔除 HTML 注释。"""
    result: list[tuple[int, str]] = []
    in_comment = False
    for number, line in _visible_markdown_numbered_lines(text):
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        start = line.find("<!--")
        if start != -1:
            end = line.find("-->", start + 4)
            if end == -1:
                in_comment = True
                continue
            line = line[:start] + line[end + 3:]
            if not line.strip():
                continue
        result.append((number, line))
    return result


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _table_blocks(lines: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    blocks: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for number, line in lines:
        if line.lstrip().startswith("|"):
            current.append((number, line))
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _loc(number: int, message: str) -> str:
    return f"{SPECS_INDEX_REL}:{number}: {message}"


def _parse(root: Path | str) -> tuple[list[str], dict[str, list[str]]]:
    root = Path(root)
    path = root / SPECS_INDEX_REL
    if not path.is_file():
        return [_loc(1, f"缺 {SPECS_INDEX_REL}；不能确认长期 spec 是否闭环")], {}
    lines = _visible_lines(path.read_text(encoding="utf-8"))
    blocks = _table_blocks(lines)
    errors: list[str] = []
    if not blocks:
        return [_loc(1, "缺 spec 清单表")], {}
    spec_index = next(
        (index for index, block in enumerate(blocks)
         if block and _cells(block[0][1])[:1] == ["slug"]),
        0,
    )
    for index, block in enumerate(blocks):
        if index == spec_index:
            continue
        first, last = block[0][0], block[-1][0]
        errors.append(_loc(first, f"第 {first}–{last} 行是多余的表；全文只允许一张 spec 清单表"))
    table = blocks[spec_index]
    entries: dict[str, list[str]] = {}

    header_number, header_line = table[0]
    header = _cells(header_line)
    if len(header) != 3:
        errors.append(_loc(header_number, f"表头须为 3 列 slug|task 清单|最后更新时间（实得 {len(header)} 列）"))
    else:
        for index, (got, expected) in enumerate(zip(header, HEADER_CELLS), 1):
            if got != expected:
                errors.append(_loc(header_number, f"第 {index} 列表头须为 {expected}（实得 {got!r}）"))
    for index, cell in enumerate(header, 1):
        if "`" in cell:
            errors.append(_loc(header_number, f"第 {index} 列不得含反引号"))

    if len(table) < 2:
        errors.append(_loc(header_number, "缺分隔行（|---|---|---|）"))
        return errors, entries
    separator_number, separator_line = table[1]
    separator = _cells(separator_line)
    if len(separator) != 3:
        errors.append(_loc(separator_number, f"分隔行须为 3 格（实得 {len(separator)} 格）"))
    else:
        for index, cell in enumerate(separator, 1):
            if not SEPARATOR_RE.fullmatch(cell):
                errors.append(_loc(separator_number, f"第 {index} 格分隔符须匹配 :?-+:?（实得 {cell!r}）"))

    seen: dict[str, int] = {}
    for number, line in table[2:]:
        cells = _cells(line)
        for index, cell in enumerate(cells, 1):
            if "`" in cell:
                errors.append(_loc(number, f"第 {index} 列不得含反引号"))
        if len(cells) != len(header):
            errors.append(_loc(number, f"数据行列数须与表头一致（表头 {len(header)} 列，实得 {len(cells)} 列）"))
        slug = cells[0]
        valid_slug = bool(SLUG_RE.fullmatch(slug))
        if not valid_slug:
            errors.append(_loc(number, f"slug 须匹配 {SLUG_RE.pattern}（实得 {slug!r}）"))
        if slug in seen:
            errors.append(_loc(number, f"slug 重复（首次见第 {seen[slug]} 行）"))
        else:
            seen[slug] = number
        if valid_slug and not (root / "docs/specs" / f"{slug}.md").is_file():
            errors.append(_loc(number, f"引用不存在的 spec：{slug}"))
        if len(cells) != 3 or len(header) != 3:
            continue
        task_list, last_update = cells[1], cells[2]
        if not TASK_LIST_RE.fullmatch(task_list):
            errors.append(_loc(number, f"task 清单须为半角逗号分隔的 tNNN/baseline（实得 {task_list!r}）"))
        if not DATE_RE.fullmatch(last_update):
            errors.append(_loc(number, f"最后更新时间须为 YYYY-MM-DD（实得 {last_update!r}）"))
        else:
            try:
                date.fromisoformat(last_update)
            except ValueError:
                errors.append(_loc(number, f"非法日期：{last_update!r}"))
        tokens = [token.strip() for token in task_list.split(",")]
        if len(tokens) != len(set(tokens)):
            errors.append(_loc(number, f"task 清单 token 重复：{task_list!r}"))
        entries[slug] = tokens
    return errors, entries


def specs_index_errors(root: Path | str) -> list[str]:
    """校验整个文件，返回带行号的违规清单；文件缺失也算一条违规。"""
    return _parse(root)[0]


def parse_specs_index(root: Path | str) -> dict[str, list[str]]:
    """有违规则把全部违规合成一个 ValueError 抛出；否则返回 slug -> token 列表映射。"""
    errors, entries = _parse(root)
    if errors:
        raise ValueError("specs_index 契约违规：\n" + "\n".join(errors))
    return entries
