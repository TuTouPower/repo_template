"""repo_task.spikes - Spike entity metadata schema and resolution helpers."""

from __future__ import annotations

import re
from pathlib import Path

from .documents import parse_front_matter_text, parse_front_matter

SPIKE_SID_RE = re.compile(r"^s[0-9]{3,}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")
ENTRY_DIR_RE = re.compile(r"^s([0-9]{3,})_[a-z0-9_]+$")
VALID_STATUSES = frozenset({"active", "completed", "superseded"})
ALLOWED_FRONT_MATTER_KEYS = frozenset({"sid", "slug", "status", "superseded_by", "reason"})


class SpikeMetadataError(ValueError):
    """Spike front matter schema or state error."""


def validate_spike_metadata(
    fm: dict,
    *,
    expected_sid: str | None = None,
    expected_slug: str | None = None,
) -> None:
    unknown_keys = set(fm) - ALLOWED_FRONT_MATTER_KEYS
    if unknown_keys:
        raise SpikeMetadataError(
            f"spike front matter 包含未知字段：{', '.join(sorted(unknown_keys))}"
        )

    sid = fm.get("sid")
    if not sid or not SPIKE_SID_RE.match(sid):
        raise SpikeMetadataError(f"spike sid 格式非法：{sid!r}（期望形如 s001）")
    if expected_sid is not None and sid != expected_sid:
        raise SpikeMetadataError(f"spike sid 与预期不符：{sid} != {expected_sid}")

    slug = fm.get("slug")
    if not slug or not SLUG_RE.match(slug):
        raise SpikeMetadataError(f"spike slug 格式非法：{slug!r}（期望 snake_case）")
    if expected_slug is not None and slug != expected_slug:
        raise SpikeMetadataError(f"spike slug 与预期不符：{slug} != {expected_slug}")

    status = fm.get("status")
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
        if not succ or not SPIKE_SID_RE.match(succ):
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
    matches = [
        p
        for p in spikes_dir.iterdir()
        if p.is_dir() and p.name.startswith(f"{sid}_") and ENTRY_DIR_RE.match(p.name)
    ]
    if not matches:
        raise SpikeMetadataError(f"未找到 spike：{sid}")
    if len(matches) > 1:
        raise SpikeMetadataError(
            f"spike {sid} 匹配到多个目录：{', '.join(p.name for p in sorted(matches, key=lambda x: x.name))}"
        )
    return matches[0]
