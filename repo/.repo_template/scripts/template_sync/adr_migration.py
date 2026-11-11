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
HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+|$)(.*)$")
NNN_TITLE_RE = re.compile(r"([0-9]+)[ \t]+(.+?)[ \t]*$")
DATE_RE = re.compile(r"[（(](\d{4}-\d{2}-\d{2})[）)]\s*$")
SUPERSEDES_RE = re.compile(r"^\s*[-*]?\s*替代\s*[:：]\s*([0-9]+)\s*$", re.MULTILINE)


def _heading(line: str) -> tuple[int, str] | None:
    match = HEADING_RE.match(line)
    if match is None:
        return None
    return len(match.group(1)), match.group(2)


def _entries(text: str) -> tuple[list[dict], list[str]]:
    lines = text.splitlines()
    fence = FenceState()
    entries: list[dict] = []
    current = None
    preface: list[str] = []
    first_seen: dict[int, int] = {}
    duplicate: tuple[str, int, int] | None = None
    for lineno, line in enumerate(lines, 1):
        if fence.feed(line):
            (current["lines"] if current else preface).append(line)
            continue
        heading = _heading(line)
        level = heading[0] if heading else None
        if level == 1 and current is not None:
            raise ctx.SyncError(
                f"{SOURCE} 第 {lineno} 行条目正文出现围栏外 H1：{line.strip()}；"
                "正文只允许 H3 及以下标题，拒绝迁移"
            )
        if level == 2:
            title_match = NNN_TITLE_RE.fullmatch(heading[1])
            if title_match is None:
                raise ctx.SyncError(
                    f"{SOURCE} 第 {lineno} 行围栏外 H2 不是 `## NNN 标题`：{line.strip()}；"
                    "拒绝迁移，请先补编号或移除该标题"
                )
            nnn_text = title_match.group(1)
            nnn = int(nnn_text)
            if duplicate is None:
                if nnn in first_seen:
                    duplicate = (nnn_text, first_seen[nnn], lineno)
                else:
                    first_seen[nnn] = lineno
            if current:
                entries.append(current)
            current = {"nnn": nnn, "nnn_text": nnn_text, "title": title_match.group(2), "lines": [f"# {nnn_text} {title_match.group(2)}"]}
            continue
        if current is None and level is not None and level >= 3:
            raise ctx.SyncError(
                f"第 {lineno} 行前言出现 H3+ 标题，ADR 条目须为 `## NNN 标题`"
            )
        (current["lines"] if current else preface).append(line)
    if current:
        entries.append(current)
    if duplicate is not None:
        nnn_text, first, second = duplicate
        raise ctx.SyncError(
            f"decisions.md 编号 {nnn_text} 重复（第 {first}、{second} 行）；拒绝迁移，请先改号"
        )
    return entries, preface


def _preface_report(preface: list[str]) -> list[str]:
    text = "\n".join(preface).strip()
    if not text:
        return []
    return [f"{SOURCE} 前言（不生成 ADR，迁移后从仓库移除，请据本报告复核）：\n{text}"]


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
    return f"{date.replace('-', '')}_{_slug(entry['title'])}.md"


def _allocate_filenames(sync: ctx.SyncContext, dated: list[tuple[dict, str]]) -> dict[int, str]:
    """按 NNN 升序为每条迁移 ADR 分配最终文件名。

    同名冲突（同日期同 slug，含目标目录已存在文件）追加 `_2`、`_3`…；
    分配在写入前完成，无法分配时抛错、零写入。
    """
    target = sync.consumer / TARGET
    used = {path.name for path in target.iterdir()} if target.is_dir() else set()
    allocated: dict[int, str] = {}
    for entry, date in sorted(dated, key=lambda item: item[0]["nnn"]):
        base = _filename(entry, date)[: -len(".md")]
        candidate = f"{base}.md"
        suffix = 2
        while candidate in used:
            if suffix > 10000:
                raise ctx.SyncError(f"ADR {entry['nnn_text']} 文件名冲突无法分配：{base}.md 已有过多同名文件")
            candidate = f"{base}_{suffix}.md"
            suffix += 1
        used.add(candidate)
        allocated[entry["nnn"]] = candidate
    return allocated


def adr_migration_status(sync: ctx.SyncContext) -> list[str]:
    source = sync.consumer / SOURCE
    if not source.is_file():
        return []
    try:
        entries, preface = _entries(source.read_text(encoding="utf-8"))
    except ctx.SyncError as error:
        return [str(error)]
    if not entries:
        return ["decisions.md 无条目，迁移将移除（前言见报告）", *_preface_report(preface)]
    return [f"{SOURCE} -> {TARGET}（{len(entries)} 条）", *_preface_report(preface)]


def migrate_adr(sync: ctx.SyncContext, *, write: bool = False) -> list[str]:
    source = sync.consumer / SOURCE
    if not source.is_file():
        return []
    entries, preface = _entries(source.read_text(encoding="utf-8"))
    if not entries:
        reports = _preface_report(preface)
        if write:
            source.unlink()
        return reports
    dated = []
    for entry in entries:
        date_match = DATE_RE.search(entry["title"])
        date = date_match.group(1) if date_match else _date_from_git(sync.consumer, entry["nnn_text"])
        dated.append((entry, date))
    filenames = _allocate_filenames(sync, dated)
    prepared = []
    for entry, date in dated:
        filename = filenames[entry["nnn"]]
        body = "\n".join(entry["lines"])
        prepared.append((entry, filename, body))
    by_nnn = {entry["nnn"]: filename for entry, filename, _body in prepared}
    fronts: dict[str, list[str]] = {}
    for entry, filename, body in prepared:
        supersedes = SUPERSEDES_RE.search(body)
        front = []
        if supersedes:
            old_text = supersedes.group(1)
            old = int(old_text)
            if old not in by_nnn:
                raise ctx.SyncError(f"ADR {entry['nnn_text']} 的替代 {old_text} 不存在")
            front = ["---", f"supersedes: [{by_nnn[old]}]", "---", ""]
        fronts[filename] = front
    reports = _preface_report(preface) + [
        f"{SOURCE} -> {TARGET}/{filename}" for _entry, filename, _body in prepared
    ]
    if not write:
        return reports
    target = sync.consumer / TARGET
    target.mkdir(parents=True, exist_ok=True)
    for _entry, filename, body in prepared:
        (target / filename).write_text(
            "\n".join(fronts[filename]) + body.rstrip() + "\n", encoding="utf-8"
        )
    source.unlink()
    return reports
