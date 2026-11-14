"""显式旧总账解析：由统一 entity-storage 迁移调用，运行期编号扫描不导入本模块。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from _id_scan import (
    IdScanError,
    canonical_entry_id,
    slug_from_title,
)

_LEGACY_HEADING_RE = re.compile(
    r"^ {0,3}(?P<hashes>#{1,6})[ \t]+(?P<prefix>[pd])(?P<num>[0-9]{3,})(?![0-9])[ \t]*(?P<title>.*)$"
)
_LEGACY_CANDIDATE_RE = re.compile(r"^ {0,3}#{1,6}[ \t]+(?P<prefix>[pd])[0-9]{3,}\b")
_FENCE_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})")


def _feed_fence(line: str, state: list) -> bool:
    """围栏行返回 True（该行属代码块，不参与标题判定）；state 为 [char, len] 或 [None, 0]。"""
    match = _FENCE_RE.match(line)
    if match is None:
        return state[0] is not None
    seq = match.group("fence")
    char, length = seq[0], len(seq)
    if state[0] is None:
        state[0], state[1] = char, length
        return True
    if char == state[0] and length >= state[1]:
        state[0], state[1] = None, 0
    return True


def legacy_ledger_entries(text: str, prefix: str) -> list[tuple[int, str, str]]:
    """解析旧单文件总账正文，返回 [(编号, 标题, 正文)]；正文不含标题行。

    fence-aware：围栏内 `## pNNN` 不算条目；行首 0-3 空格缩进标题照常解析；
    非条目同级标题不再截断当前条目，而是作为正文保留（挂到最近条目），
     ledger 开头的前言同样并入首条目，杜绝静默丢失。返回前以 fence-aware
    候选标题数核对解析条目数，不一致即中止，避免删源后丢失。
    """
    entries: list[tuple[int, str, str]] = []
    current: tuple[int, str] | None = None  # (number, title)
    body: list[str] = []
    preamble: list[str] = []
    fence: list = [None, 0]

    def flush() -> None:
        if current is not None:
            entries.append((current[0], current[1], "\n".join(body).strip()))

    for line in text.splitlines():
        if _feed_fence(line, fence):
            if current is not None:
                body.append(line)
            else:
                preamble.append(line)
            continue
        match = _LEGACY_HEADING_RE.match(line)
        if match and match.group("prefix") == prefix:
            flush()
            current = (int(match.group("num")), match.group("title").strip())
            body = []
            continue
        if current is not None:
            body.append(line)
        else:
            preamble.append(line)
    flush()

    if entries and any(part.strip() for part in preamble):
        first_number, first_title, first_body = entries[0]
        head = "\n".join(preamble).strip()
        merged = (head + "\n\n" + first_body).strip() if first_body else head
        entries[0] = (first_number, first_title, merged)

    fence_check: list = [None, 0]
    expected = 0
    for line in text.splitlines():
        if _feed_fence(line, fence_check):
            continue
        candidate = _LEGACY_CANDIDATE_RE.match(line)
        if candidate and candidate.group("prefix") == prefix:
            expected += 1
    if expected != len(entries):
        raise IdScanError(
            f"旧总账标题解析不完整：检测到 {expected} 个 {prefix}NNN 标题，"
            f"仅解析出 {len(entries)} 条；拒绝迁移以避免条目静默丢失"
        )
    return entries


class LegacyMigrationItem(NamedTuple):
    """旧总账一条待迁移条目：编号、规范 ID、slug、标题、正文、默认目标路径、来源相对路径、条目位置。"""

    number: int
    entry_id: str
    slug: str
    title: str
    body: str
    target: Path
    ledger_rel: str = ""
    ordinal: int = 0


def plan_legacy_migration(
    repo_root: Path, *, prefix: str, ledger_rel: str, target_dir: Path
) -> list[LegacyMigrationItem]:
    """解析旧单文件总账并校验完整性；不在解析阶段因同号丢弃、合并或拒绝条目。"""
    legacy_path = repo_root / ledger_rel
    if legacy_path.is_symlink():
        raise IdScanError(f"旧总账是软链，拒绝迁移：{ledger_rel}")
    if not legacy_path.is_file():
        raise IdScanError(f"未找到旧单文件总账：{ledger_rel}")
    try:
        text = legacy_path.read_text(encoding="utf-8")
    except OSError as error:
        raise IdScanError(f"读取旧总账失败（{ledger_rel}）：{error}") from error
    entries = legacy_ledger_entries(text, prefix)
    if not entries:
        raise IdScanError(f"{ledger_rel} 未解析出任何 {prefix}NNN 条目")
    items: list[LegacyMigrationItem] = []
    for ordinal, (number, title, body) in enumerate(entries):
        entry_id = canonical_entry_id(prefix, number)
        slug = slug_from_title(title)
        items.append(
            LegacyMigrationItem(
                number,
                entry_id,
                slug,
                title,
                body,
                target_dir / f"{entry_id}_{slug}.md",
                ledger_rel,
                ordinal,
            )
        )
    return items
