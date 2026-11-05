"""消费仓 docs/specs_index.md 的破坏性 schema 收敛与契约报告。"""

from __future__ import annotations

from pathlib import Path

from repo_task.specs_index import SPECS_INDEX_REL, specs_index_errors

from . import context as ctx

LEGACY_HEADER = ("slug", "task 清单", "最后更新时间", "supersedes")
LEGACY_NOTES = (
    "替代旧需求时在 `supersedes` 列填旧 slug；无则填 `-`。",
    "`supersedes` 列填该 spec 替代的旧 spec slug，用于追踪需求替代关系；无则填 `-`。",
)


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _migrate_specs_index_text(text: str) -> str | None:
    """移除旧说明句；4 列 supersedes 且该列无真实数据时收敛为 3 列；否则不动表格。"""
    result = text
    for note in LEGACY_NOTES:
        result = result.replace(note, "")
    lines = result.split("\n")
    header_index = None
    for index, line in enumerate(lines):
        if line.lstrip().startswith("|"):
            if _cells(line) == list(LEGACY_HEADER):
                header_index = index
            break
    if header_index is not None:
        table_lines = []
        cursor = header_index
        while cursor < len(lines) and lines[cursor].lstrip().startswith("|"):
            table_lines.append(cursor)
            cursor += 1
        if len(table_lines) >= 2 and all(
            len(_cells(lines[index])) == 4 and _cells(lines[index])[3] in ("-", "")
            for index in table_lines[2:]
        ):
            table_set = set(table_lines)
            lines = [
                "|" + "|".join(_cells(line)[:3]) + "|" if index in table_set else line
                for index, line in enumerate(lines)
            ]
            result = "\n".join(lines)
    return result if result != text else None


def specs_index_migration_status() -> list[str]:
    path = ctx.CONSUMER / SPECS_INDEX_REL
    if not path.is_file():
        return []
    before = path.read_text(encoding="utf-8")
    after = _migrate_specs_index_text(before)
    if after is not None and after != before:
        return [ctx._rel(path)]
    return []


def migrate_specs_index(changed: set[Path]) -> list[str]:
    path = ctx.CONSUMER / SPECS_INDEX_REL
    if not path.is_file():
        return []
    before = path.read_text(encoding="utf-8")
    after = _migrate_specs_index_text(before)
    if after is None or after == before:
        return []
    path.write_text(after, encoding="utf-8")
    changed.add(path)
    return [f"{ctx._rel(path)} 已迁移 specs_index 旧说明句/无数据 supersedes 列（保留其余内容）"]


def specs_index_report() -> list[str]:
    return specs_index_errors(ctx.CONSUMER)
