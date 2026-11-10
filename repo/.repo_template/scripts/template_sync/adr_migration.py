"""一次性把 docs/blueprint/decisions.md 拆成一条一文件 ADR。"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from _id_scan import slug_from_title
from repo_task.context import FenceState

from . import context as ctx

SOURCE = "docs/blueprint/decisions.md"
TARGET = "docs/blueprint/decisions"
ENTRY_RE = re.compile(r"^##\s+([0-9]+)\s+(.+?)\s*$")
DATE_RE = re.compile(r"[（(](\d{4}-\d{2}-\d{2})[）)]\s*$")
SUPERSEDES_RE = re.compile(r"^\s*[-*]?\s*替代\s*[:：]\s*([0-9]+)\s*$", re.MULTILINE)


def _entries(text: str) -> list[dict]:
    lines = text.splitlines()
    fence = FenceState()
    entries: list[dict] = []
    current = None
    preface: list[str] = []
    for line in lines:
        if fence.feed(line):
            if current:
                current["lines"].append(line)
            continue
        match = ENTRY_RE.match(line)
        if match:
            if current:
                entries.append(current)
            current = {"nnn": match.group(1), "title": match.group(2), "lines": [line]}
        elif current:
            current["lines"].append(line)
        else:
            preface.append(line)
    if current:
        entries.append(current)
    if not entries:
        raise ctx.SyncError(f"{SOURCE} 未找到任何 ## NNN 条目")
    return entries


def _date_from_git(root: Path, nnn: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "log", "--reverse", "-S", f"## {nnn}", "--format=%ad", "--date=short", "--", SOURCE],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    dates = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or not dates:
        raise ctx.SyncError(f"ADR {nnn} 无标题日期且无法从 Git 历史取得日期")
    return dates[0]


def _slug(title: str) -> str:
    base = DATE_RE.sub("", title).strip()
    slug = slug_from_title(base)
    return slug or "entry"


def _filename(entry: dict, date: str) -> str:
    return f"{date.replace('-', '')}_{entry['nnn']}_{_slug(entry['title'])}.md"


def adr_migration_status(sync: ctx.SyncContext) -> list[str]:
    source = sync.consumer / SOURCE
    if not source.is_file():
        return []
    try:
        entries = _entries(source.read_text(encoding="utf-8"))
    except ctx.SyncError as error:
        return [str(error)]
    return [f"{SOURCE} -> {TARGET}（{len(entries)} 条）"]


def migrate_adr(sync: ctx.SyncContext, *, write: bool = False) -> list[str]:
    source = sync.consumer / SOURCE
    if not source.is_file():
        return []
    entries = _entries(source.read_text(encoding="utf-8"))
    prepared = []
    for entry in entries:
        date_match = DATE_RE.search(entry["title"])
        date = date_match.group(1) if date_match else _date_from_git(sync.consumer, entry["nnn"])
        filename = _filename(entry, date)
        prepared.append((entry, date, filename))
    by_nnn = {entry["nnn"]: filename for entry, _date, filename in prepared}
    reports = [f"{SOURCE} -> {TARGET}/{filename}" for _entry, _date, filename in prepared]
    if not write:
        return reports
    target = sync.consumer / TARGET
    target.mkdir(parents=True, exist_ok=True)
    for entry, _date, filename in prepared:
        body = "\n".join(entry["lines"])
        heading = f"## {entry['nnn']} {entry['title']}"
        if heading in body:
            body = body.replace(heading, f"# {entry['nnn']} {entry['title']}", 1)
        supersedes = SUPERSEDES_RE.search(body)
        front = []
        if supersedes:
            old = supersedes.group(1)
            if old not in by_nnn:
                raise ctx.SyncError(f"ADR {entry['nnn']} 的替代 {old} 不存在")
            front = ["---", f"supersedes: [{by_nnn[old]}]", "---", ""]
        (target / filename).write_text("\n".join(front) + body.rstrip() + "\n", encoding="utf-8")
    source.unlink()
    return reports
