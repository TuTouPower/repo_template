"""Mechanical closure checks for specs and formal spike evidence."""

import re

from . import context as ctx
from .documents import (
    _visible_markdown_lines,
    _strip_inline_code,
    parse_front_matter,
    parse_front_matter_text,
)
from .spikes import SpikeMetadataError, validate_spike_metadata
from .specs_index import parse_specs_index


def _prose(text):
    return "\n".join(_visible_markdown_lines(re.sub(r"<!--.*?-->", "", text, flags=re.S)))


def _references(text, prefix):
    # Inline bare IDs and docs links are references; executable inline snippets
    # and fenced examples are not. IDs use the allocator's minimum three digits.
    text = _prose(text)
    def inline(match):
        content = match.group(1).strip()
        if re.fullmatch(rf"{prefix}[0-9]{{3,}}", content) or content.startswith("docs/"):
            return content
        return ""
    text = re.sub(r"`([^`\n]+)`", inline, text)
    identifiers = set(re.findall(rf"\b{prefix}[0-9]{{3,}}\b", text))
    directory = "findings" if prefix == "d" else "spikes"
    identifiers.update(re.findall(rf"docs/{directory}/({prefix}[0-9]{{3,}})_[a-z0-9_]+", text))
    return identifiers


def validate_spike(root, directory):
    report = directory / "report.md"
    if not report.is_file():
        raise ValueError(f"正式 spike 缺 report.md：{directory.name}")
    text = report.read_text(encoding="utf-8")
    if text.startswith("---"):
        try:
            fm, prose_text = parse_front_matter_text(text, source=report.as_posix())
            validate_spike_metadata(fm)
        except SpikeMetadataError as err:
            raise ValueError(f"spike 元数据非法：{err}") from err
    else:
        prose_text = text
    prose = _prose(prose_text)
    if ctx.TEMPLATE_PLACEHOLDER_RE.search(_strip_inline_code(prose)):
        raise ValueError(f"spike report 仍有占位符：{directory.name}")
    findings = _references(prose_text, "d")
    if not findings or any(not list((root / "docs/findings").glob(f"{did}_*.md")) for did in findings):
        raise ValueError(f"spike 结论须关联现存 findings：{directory.name}")
    if not re.search(r"(?m)^ {0,3}##[ \t]+结论[ \t]*$", prose) or not re.search(
        r"(?m)^ {0,3}[-*+][ \t]+决定[ \t]*[:：][ \t]*[是否](?:[ \t]*$|[ \t，。,;；（(])", prose
    ):
        raise ValueError(f"spike 缺结论或采纳决定：{directory.name}")
    if not re.search(r"后续 task[ \t]*[:：][ \t]*`?t[0-9]{3,}\b", prose) and not re.search(r"无需后续理由[ \t]*[:：][ \t]*\S+", prose):
        raise ValueError(f"spike 须有后续 tid 或无需后续理由：{directory.name}")


def _resolve_and_validate_spike(root, sid, visited=None):
    if visited is None:
        visited = []
    if sid in visited:
        chain = " -> ".join(visited + [sid])
        raise ValueError(f"正式 spike 替代链存在循环引用：{chain}")
    visited.append(sid)

    directories = [path for path in (root / "docs/spikes").glob(f"{sid}_*") if path.is_dir()]
    if len(directories) != 1:
        raise ValueError(f"正式 spike 引用缺失或重复：{sid}")
    spike_dir = directories[0]
    report = spike_dir / "report.md"
    if not report.is_file():
        raise ValueError(f"正式 spike 缺 report.md：{spike_dir.name}")

    try:
        fm, _ = parse_front_matter(report)
        validate_spike_metadata(fm, expected_sid=sid)
    except (ctx.TaskDataError, SpikeMetadataError) as err:
        raise ValueError(f"正式 spike {sid} 元数据校验失败：{err}") from err

    status = fm.get("status")
    if status == "active":
        raise ValueError(f"正式 spike 尚未完成（status 为 active）：{sid}")
    elif status == "completed":
        validate_spike(root, spike_dir)
        return spike_dir
    elif status == "superseded":
        successor_sid = fm.get("superseded_by")
        if not successor_sid:
            raise ValueError(f"正式 spike {sid} 已被替代但未指定后继 superseded_by")
        return _resolve_and_validate_spike(root, successor_sid, visited)
    else:
        raise ValueError(f"正式 spike {sid} 状态非法：{status!r}")


def check_finalization(root, tid, task_dir):
    entries = parse_specs_index(root)
    included = any(tid in tokens for tokens in entries.values())
    if included:
        matches = [path for parent in ("docs/tasks", "docs/archive/tasks")
                   for path in (root / parent).glob(f"{tid}_*/{ctx.PROGRESS_FILE}")]
        if len(matches) != 1 or matches[0].parent.resolve() != task_dir.resolve():
            raise ValueError(f"当前 specs_index task 引用缺失或重复：{tid}")
    note_text = (task_dir / ctx.PROGRESS_FILE).read_text(encoding="utf-8")
    if not included and not re.search(r"(?m)^- specs_index: not_applicable: \S.+$", note_text):
        raise ValueError(f"当前 task 未进入 specs_index；无长期 spec 变更时在 {ctx.PROGRESS_FILE} 写 - specs_index: not_applicable: 原因")
    combined = note_text + "\n" + (task_dir / "task.md").read_text(encoding="utf-8")
    for sid in _references(combined, "s"):
        _resolve_and_validate_spike(root, sid)
