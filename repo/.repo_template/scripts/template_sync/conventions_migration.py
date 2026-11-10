"""消费仓 conventions.md 的模板内部实现段落迁移。"""

from __future__ import annotations

import re
from pathlib import Path

from . import context as ctx

SECTION_HEADINGS = ("## 原子写", "## 路径隐私")
POINTER = "模板内部实现（原子写、路径隐私）见 `.repo_template/docs/architecture.md`；本文只写项目约定。"


def _known_template_lines() -> set[str]:
    path = ctx.TOOLKIT_ROOT / "docs/architecture.md"
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8")
    lines: set[str] = set()
    for heading in SECTION_HEADINGS:
        start = text.find(heading)
        if start < 0:
            continue
        match = re.search(r"^## ", text[start + len(heading):], re.MULTILINE)
        end = start + len(heading) + (match.start() if match else len(text) - start - len(heading))
        lines.update(line.strip() for line in text[start:end].splitlines() if line.strip())
    return lines


def _migrate_conventions_text(text: str) -> tuple[str, list[str]]:
    known = _known_template_lines()
    removed: list[str] = []
    for heading in SECTION_HEADINGS:
        if heading not in text:
            continue
        start = text.index(heading)
        match = re.search(r"^## ", text[start + len(heading):], re.MULTILINE)
        end = start + len(heading) + (match.start() if match else len(text) - start - len(heading))
        section = text[start:end]
        kept = [
            re.sub(r"from\s+task\s+import\s+_?atomic_write_text", "from repo_task.documents import atomic_write_text", line).replace("_atomic_write_text", "atomic_write_text")
            for line in section.splitlines()
            if line.strip() == heading or line.strip() not in known
        ]
        if len([line for line in kept if line.strip() and line.strip() != heading]) == 0:
            text = text[:start] + text[end:]
            removed.append(heading)
        else:
            text = text[:start] + "\n".join(kept).rstrip() + "\n" + text[end:]
            removed.append(heading)
    if removed and POINTER not in text:
        text = text.rstrip() + "\n\n" + POINTER + "\n"
    return text, removed


def conventions_migration_status(sync: ctx.SyncContext) -> list[str]:
    if not sync.conventions_path.is_file():
        return []
    before = sync.conventions_path.read_text(encoding="utf-8")
    after, _removed = _migrate_conventions_text(before)
    return [sync.rel(sync.conventions_path)] if after != before else []


def migrate_conventions(changed: set[Path], sync: ctx.SyncContext) -> list[str]:
    if not sync.conventions_path.is_file():
        return []
    before = sync.conventions_path.read_text(encoding="utf-8")
    after, removed = _migrate_conventions_text(before)
    if after != before:
        sync.conventions_path.write_text(after, encoding="utf-8")
        changed.add(sync.conventions_path)
        return [f"{sync.rel(sync.conventions_path)} 已移出模板内部实现段落（{', '.join(removed)}）"]
    return []
