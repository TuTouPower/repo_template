"""一次性把旧 AGENTS.md 拆为项目区与模板区。"""

from __future__ import annotations

import re
from pathlib import Path

from . import context as ctx
from .agents_merge import agents_parts

HEADING = "## 目录与读写规则"
DEVELOP = "## 开发原则"
PATH_RE = re.compile(r"^\|\s*`([^`]+)`")


def agents_zone_migration_status(sync: ctx.SyncContext) -> list[str]:
    path = sync.consumer / "AGENTS.md"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    return [] if ctx.AGENTS_TEMPLATE_BEGIN in text and ctx.AGENTS_TEMPLATE_END in text else [sync.rel(path)]


def _table_paths(section: str) -> set[str]:
    paths: set[str] = set()
    for line in section.splitlines():
        match = PATH_RE.match(line.strip())
        if match:
            paths.add(match.group(1))
    return paths


def _split_old(text: str, template_paths: set[str]) -> tuple[str, list[str], list[str]]:
    if HEADING not in text or DEVELOP not in text:
        raise ctx.SyncError("AGENTS.md 缺旧版 ## 目录与读写规则 或 ## 开发原则，拒绝猜测分区")
    before, rest = text.split(HEADING, 1)
    table, develop = rest.split(DEVELOP, 1)
    project_rows: list[str] = []
    template_rows: list[str] = []
    unknown: list[str] = []
    for line in table.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        path_match = PATH_RE.match(stripped)
        if path_match and path_match.group(1) not in template_paths:
            project_rows.append(line)
        else:
            template_rows.append(line)
        if path_match and path_match.group(1) not in template_paths:
            unknown.append(path_match.group(1))
    project = before.rstrip("\n") + "\n"
    if project_rows:
        header = [line for line in table.splitlines() if line.strip().startswith("|")][:2]
        project += "\n## 项目目录与读写规则\n\n"
        project += "\n".join(header + project_rows) + "\n"
    return project, template_rows, unknown


def migrate_agents_zones(sync: ctx.SyncContext, src: Path, *, write: bool = False) -> list[str]:
    target = sync.consumer / "AGENTS.md"
    if not target.is_file():
        return []
    if not agents_zone_migration_status(sync):
        return []
    source_text = (src / "AGENTS.md").read_text(encoding="utf-8")
    source_parts = agents_parts(source_text)
    template_paths = _table_paths(source_parts["template"])
    project, _template_rows, unknown = _split_old(target.read_text(encoding="utf-8"), template_paths)
    result = project + source_parts["template"]
    if not write:
        return [f"{sync.rel(target)} 拟拆为项目区 + 模板区；项目行 {len(unknown)} 条保留"]
    target.write_text(result, encoding="utf-8")
    reports = [f"{sync.rel(target)} 已拆为项目区 + 模板区"]
    if unknown:
        reports.append("保留项目行：" + ", ".join(sorted(set(unknown))))
    return reports
