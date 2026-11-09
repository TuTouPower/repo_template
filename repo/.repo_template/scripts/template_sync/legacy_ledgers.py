"""显式旧总账迁移；运行期编号扫描不导入本模块。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, NamedTuple

from _id_scan import (
    IdScanError, _assert_no_symlink, _fsync_dir,
    _stage_file, _publish_staged_file, _cleanup_staging, canonical_entry_id,
    id_lock, parse_entry_file_name, reclaim_staging, slug_from_title,
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
    """旧总账一条待迁移条目：编号、规范 ID、slug、标题、正文、目标文件。"""

    number: int
    entry_id: str
    slug: str
    title: str
    body: str
    target: Path


def plan_legacy_migration(
    repo_root: Path, *, prefix: str, ledger_rel: str, target_dir: Path
) -> list[LegacyMigrationItem]:
    """解析旧单文件总账，返回待迁移条目；完整性校验与重复编号在此统一完成。"""
    legacy_path = repo_root / ledger_rel
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
    seen: set[int] = set()
    for number, title, body in entries:
        if number in seen:
            raise IdScanError(f"{ledger_rel} 存在重复编号 {prefix}{number:03d}；迁移中止")
        seen.add(number)
        entry_id = canonical_entry_id(prefix, number)
        slug = slug_from_title(title)
        items.append(
            LegacyMigrationItem(
                number, entry_id, slug, title, body,
                target_dir / f"{entry_id}_{slug}.md",
            )
        )
    return items


def _remove_legacy_ledger(repo_root: Path, ledger_rel: str) -> None:
    """仅删除源文件；Git index 由用户提交迁移结果时统一暂存。"""
    ledger = repo_root / ledger_rel
    if ledger.is_symlink():
        raise IdScanError(f"拒绝删除软链旧总账：{ledger_rel}")
    ledger.unlink()
    _fsync_dir(ledger.parent)


def apply_legacy_migration(
    items: list[LegacyMigrationItem],
    *,
    repo_root: Path,
    prefix: str,
    ledger_rel: str,
    render: Callable[[str, str, str, str], str],
    formatter: Callable[[Path], None] | None = None,
) -> list[Path]:
    """锁内重验旧总账、预扫描、逐条 O_EXCL 原子发布，最后删除旧总账。

    预扫描、稳定性校验、发布、删源全部在 id_lock 内，杜绝锁外 TOCTOU 覆盖；
    发布经 staging + O_EXCL + fsync，目标已存在即中止不覆盖；总账已被并发
    迁移（源消失但目标齐全）时幂等返回，不回滚删除他人条目。
    """
    if not items:
        raise IdScanError("无可迁移条目")
    target_dir = items[0].target.parent
    created: list[Path] = []
    stagings: list[Path] = []
    try:
        with id_lock(repo_root):
            _assert_no_symlink(repo_root, target_dir)
            target_dir.mkdir(parents=True, exist_ok=True)
            _fsync_dir(target_dir.parent if target_dir.parent.exists() else repo_root)
            _assert_no_symlink(repo_root, target_dir)
            for item in items:
                _assert_no_symlink(repo_root, item.target)
            reclaim_staging(target_dir)
            ledger_path = repo_root / ledger_rel
            if ledger_path.is_symlink():
                raise IdScanError(f"拒绝迁移软链旧总账：{ledger_rel}")
            if not ledger_path.is_file():
                missing = [item.target for item in items if not item.target.is_file()]
                if not missing:
                    return [item.target for item in items]
                raise IdScanError(
                    f"旧总账已消失但仍缺 {len(missing)} 个目标（{missing[0].name}…）；"
                    "拒绝回滚删除已发布条目，请人工核对后重试"
                )
            try:
                fresh_text = ledger_path.read_text(encoding="utf-8")
            except OSError as error:
                raise IdScanError(f"读取旧总账失败（{ledger_rel}）：{error}") from error
            fresh = legacy_ledger_entries(fresh_text, prefix)
            if [(n, t, b) for n, t, b in fresh] != [
                (item.number, item.title, item.body) for item in items
            ]:
                raise IdScanError("旧总账在加锁前已被并发修改；迁移中止，请重新执行 repo_sync migrate")
            for item in items:
                occupied = sorted(
                    path
                    for path in target_dir.glob("*.md")
                    if not path.is_symlink()
                    and (parsed := parse_entry_file_name(prefix, path.name))
                    and parsed[0] == item.number
                )
                if occupied:
                    raise IdScanError(
                        f"{item.entry_id} 已有条目文件："
                        f"{', '.join(path.as_posix() for path in occupied)}；迁移中止"
                    )
                if item.target.is_symlink() or item.target.exists():
                    raise IdScanError(f"{item.target} 已存在；迁移中止")
            for item in items:
                staging = _stage_file(
                    target_dir,
                    f"{item.entry_id}_{item.slug}",
                    render(item.entry_id, item.slug, item.title, item.body).encode("utf-8"),
                )
                stagings.append(staging)
                try:
                    if formatter is not None:
                        formatter(staging)
                    _publish_staged_file(staging, item.target)
                    stagings.remove(staging)
                except BaseException:
                    _cleanup_staging(staging)
                    if staging in stagings:
                        stagings.remove(staging)
                    raise
                created.append(item.target)
            _remove_legacy_ledger(repo_root, ledger_rel)
    except BaseException as error:
        for staging in list(stagings):
            _cleanup_staging(staging)
        for path in created:
            # 仅删除本轮 O_EXCL 新建的目标；幂等路径（created 为空）不删他人条目。
            path.unlink(missing_ok=True)
        if isinstance(error, OSError) and not isinstance(error, IdScanError):
            raise IdScanError(f"旧总账迁移 I/O 失败：{error}") from error
        raise
    return created
