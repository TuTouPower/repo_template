"""Mechanical closure checks for specs and formal spike evidence."""

import re

from . import context as ctx
from .documents import _visible_markdown_lines, _strip_inline_code


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
    directory = "findings" if prefix == "d" else "(?:archive/)?spikes"
    identifiers.update(re.findall(rf"docs/{directory}/({prefix}[0-9]{{3,}})_[a-z0-9_]+", text))
    return identifiers


def validate_spike(root, directory):
    report = directory / "report.md"
    if not report.is_file():
        raise ValueError(f"正式 spike 缺 report.md：{directory.name}")
    text = report.read_text(encoding="utf-8")
    prose = _prose(text)
    if ctx.TEMPLATE_PLACEHOLDER_RE.search(_strip_inline_code(prose)):
        raise ValueError(f"spike report 仍有占位符：{directory.name}")
    findings = _references(text, "d")
    if not findings or any(not list((root / "docs/findings").glob(f"{did}_*.md")) for did in findings):
        raise ValueError(f"spike 结论须关联现存 findings：{directory.name}")
    if not re.search(r"(?m)^ {0,3}##[ \t]+结论[ \t]*$", prose) or not re.search(
        r"(?m)^ {0,3}[-*+][ \t]+决定[ \t]*[:：][ \t]*[是否](?:[ \t]*$|[ \t，。,;；（(])", prose
    ):
        raise ValueError(f"spike 缺结论或采纳决定：{directory.name}")
    if not re.search(r"后续 task[ \t]*[:：][ \t]*`?t[0-9]{3,}\b", prose) and not re.search(r"无需后续理由[ \t]*[:：][ \t]*\S+", prose):
        raise ValueError(f"spike 须有后续 tid 或无需后续理由：{directory.name}")


def check_finalization(root, tid, task_dir):
    index = root / "docs/specs_index.md"
    if not index.is_file():
        raise ValueError("缺 docs/specs_index.md；不能确认长期 spec 是否闭环")
    text = re.sub(r"<!--.*?-->", "", index.read_text(encoding="utf-8"), flags=re.S)
    included = False
    seen = set()
    warnings = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip("|").split("|")]
        slug = cells[0]
        if slug == "slug" or re.fullmatch(r"[-: ]+", slug):
            continue
        tids = re.findall(r"\bt[0-9]{3,}\b", cells[1]) if len(cells) > 1 else []
        if tid not in tids:
            if not ctx.SLUG_RE.fullmatch(slug) or len(cells) != 3 or not (root / "docs/specs" / f"{slug}.md").is_file():
                warnings.append(f"历史 specs_index 条目需维护：{slug}；不阻断当前 {tid}")
            continue
        if not ctx.SLUG_RE.fullmatch(slug) or len(cells) != 3 or slug in seen:
            raise ValueError(f"specs_index 行格式错误或重复：{line}")
        seen.add(slug)
        if not (root / "docs/specs" / f"{slug}.md").is_file():
            raise ValueError(f"specs_index 引用不存在的 spec：{slug}")
        included = True
    if included:
        matches = [path for parent in ("docs/tasks", "docs/archive/tasks")
                   for path in (root / parent).glob(f"{tid}_*/task.md")]
        if len(matches) != 1 or matches[0].parent.resolve() != task_dir.resolve():
            raise ValueError(f"当前 specs_index task 引用缺失或重复：{tid}")
    task_text = (task_dir / "task.md").read_text(encoding="utf-8")
    if not included and not re.search(r"(?m)^- specs_index: not_applicable: \S.+$", task_text):
        raise ValueError("当前 task 未进入 specs_index；无长期 spec 变更时在 task.md 写 - specs_index: not_applicable: 原因")
    combined = task_text + "\n" + (task_dir / "spec.md").read_text(encoding="utf-8")
    for sid in _references(combined, "s"):
        directories = [path for parent in ("docs/spikes", "docs/archive/spikes")
                       for path in (root / parent).glob(f"{sid}_*") if path.is_dir()]
        if len(directories) != 1:
            raise ValueError(f"正式 spike 引用缺失或重复：{sid}")
        validate_spike(root, directories[0])
    return warnings
