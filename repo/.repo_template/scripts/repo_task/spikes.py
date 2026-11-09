"""repo_task.spikes - Spike entity metadata schema and resolution helpers."""

from __future__ import annotations

import re
from pathlib import Path

from .context import TaskDataError
from .documents import parse_front_matter_text, parse_front_matter

SPIKE_SID_RE = re.compile(r"^s[0-9]{3,}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")
# 与 _id_scan.CANONICAL_NUMBER 同一规则：三位允许 0xx，四位及以上首位不得为 0。
# 拒绝 s0001_x 这类「按编号 startswith 找不到」的非规范目录名。
ENTRY_DIR_RE = re.compile(r"^s(0[0-9]{2}|[1-9][0-9]{2,})_[a-z0-9_]+$")
VALID_STATUSES = frozenset({"active", "completed", "superseded"})
ALLOWED_FRONT_MATTER_KEYS = frozenset({"sid", "slug", "status", "superseded_by", "reason"})


class SpikeMetadataError(TaskDataError, ValueError):
    """Spike front matter schema or state error."""


def validate_spike_metadata(
    fm: dict,
    *,
    expected_sid: str | None = None,
    expected_slug: str | None = None,
) -> None:
    if not isinstance(fm, dict):
        raise SpikeMetadataError(f"spike front matter 必须为 dict，收到 {type(fm).__name__}")

    for key, val in fm.items():
        if not isinstance(key, str):
            raise SpikeMetadataError(f"spike front matter 键必须为 str：{key!r}")
        if not isinstance(val, str):
            raise SpikeMetadataError(
                f"spike front matter 字段 {key!r} 类型非法：期望 str，收到 {type(val).__name__}"
            )

    unknown_keys = set(fm) - ALLOWED_FRONT_MATTER_KEYS
    if unknown_keys:
        raise SpikeMetadataError(
            f"spike front matter 包含未知字段：{', '.join(sorted(unknown_keys))}"
        )

    for k in ("sid", "slug", "status", "superseded_by", "reason"):
        v = fm.get(k)
        if v is not None and ("\n" in v or "\r" in v):
            raise SpikeMetadataError(f"spike {k} 严禁包含换行符：{v!r}")

    sid = fm.get("sid")
    if not sid or not sid.strip():
        raise SpikeMetadataError("spike sid 不能为空")
    if not SPIKE_SID_RE.match(sid):
        raise SpikeMetadataError(f"spike sid 格式非法：{sid!r}（期望形如 s001）")
    if expected_sid is not None and sid != expected_sid:
        raise SpikeMetadataError(f"spike sid 与预期不符：{sid} != {expected_sid}")

    slug = fm.get("slug")
    if not slug or not slug.strip():
        raise SpikeMetadataError("spike slug 不能为空")
    if not SLUG_RE.match(slug):
        raise SpikeMetadataError(f"spike slug 格式非法：{slug!r}（期望 snake_case）")
    if expected_slug is not None and slug != expected_slug:
        raise SpikeMetadataError(f"spike slug 与预期不符：{slug} != {expected_slug}")

    status = fm.get("status")
    if not status or not status.strip():
        raise SpikeMetadataError("spike status 不能为空")
    if status not in VALID_STATUSES:
        raise SpikeMetadataError(
            f"spike status 非法：{status!r}（必须是 active/completed/superseded）"
        )

    if status in ("active", "completed"):
        if fm.get("superseded_by"):
            raise SpikeMetadataError(f"status 为 {status} 时不得填写 superseded_by")
        if fm.get("reason"):
            raise SpikeMetadataError(f"status 为 {status} 时不得填写 reason")
    elif status == "superseded":
        succ = fm.get("superseded_by")
        if not succ or not succ.strip() or not SPIKE_SID_RE.match(succ):
            raise SpikeMetadataError(
                f"status 为 superseded 时必须提供合法的 superseded_by（收到 {succ!r}）"
            )
        reason = fm.get("reason")
        if not reason or not reason.strip():
            raise SpikeMetadataError("status 为 superseded 时 reason 不能为空")


def find_spike_dir(repo_root: Path, sid: str) -> Path:
    spikes_dir = repo_root / "docs/spikes"
    if not spikes_dir.is_dir():
        raise SpikeMetadataError("缺目录 docs/spikes")
    try:
        target_number = int(sid[1:])
    except ValueError as error:
        raise SpikeMetadataError(f"spike sid 格式非法：{sid!r}") from error
    matches = [
        p
        for p in spikes_dir.iterdir()
        if p.is_dir()
        and (match := ENTRY_DIR_RE.match(p.name))
        and int(match.group(1)) == target_number
    ]
    if not matches:
        raise SpikeMetadataError(f"未找到 spike：{sid}")
    if len(matches) > 1:
        raise SpikeMetadataError(
            f"spike {sid} 匹配到多个目录：{', '.join(p.name for p in sorted(matches, key=lambda x: x.name))}"
        )
    return matches[0]
