"""AGENTS.md 两区协议：项目区原样保留，模板区整块替换。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import context as ctx


def _zone_bounds(text: str) -> tuple[int, int]:
    begin = text.find(ctx.AGENTS_TEMPLATE_BEGIN)
    end = text.find(ctx.AGENTS_TEMPLATE_END)
    if begin < 0 or end < 0:
        raise ctx.SyncError(
            "AGENTS.md 缺 repo-template 两区标记；先运行 "
            "`repo_sync.py migrate agents-zones --write`"
        )
    if text.count(ctx.AGENTS_TEMPLATE_BEGIN) != 1 or text.count(ctx.AGENTS_TEMPLATE_END) != 1:
        raise ctx.SyncError("AGENTS.md 的 repo-template 标记必须各出现一次")
    if end < begin:
        raise ctx.SyncError("AGENTS.md 的 repo-template:end 位于 begin 之前")
    tail = text[end + len(ctx.AGENTS_TEMPLATE_END):]
    if tail.strip():
        raise ctx.SyncError("AGENTS.md 的 repo-template:end 之后仍有内容；项目规则须放在 begin 之前")
    return begin, len(text)


def agents_parts(text: str) -> dict[str, str]:
    begin, end = _zone_bounds(text)
    return {
        "project": text[:begin],
        "template": text[begin:end],
    }


def replace_template_zone(src_text: str, dst_text: str) -> tuple[str, bool, bool]:
    """把源模板区整块写入目标；返回 (结果, 是否变化, 项目区是否曾不同)。

    项目区由消费仓拥有，原样保留；模板区由模板拥有，整块替换。
    """
    src = agents_parts(src_text)
    dst = agents_parts(dst_text)
    result = dst["project"] + src["template"]
    return result, result != dst_text, dst["project"] != src["project"]


def agents_section_diagnosis(src: Path, sync: ctx.SyncContext) -> list[str]:
    issues: list[str] = []
    for label, path in (("模板源", src / "AGENTS.md"), ("消费仓", sync.consumer / "AGENTS.md")):
        if not path.exists():
            continue
        target = path.resolve() if path.is_symlink() else path
        try:
            agents_parts(target.read_text(encoding="utf-8"))
        except ctx.SyncError as error:
            issues.append(f"{label} {error}")
    return issues


def agents_section_status(src: Path, sync: ctx.SyncContext) -> dict[str, Any] | None:
    sp, dp = src / "AGENTS.md", sync.consumer / "AGENTS.md"
    if not sp.exists() or not dp.exists():
        return None
    target = dp.resolve() if dp.is_symlink() else dp
    try:
        src_parts = agents_parts(sp.read_text(encoding="utf-8"))
        dst_parts = agents_parts(target.read_text(encoding="utf-8"))
    except ctx.SyncError:
        return None
    return {
        "marker": "ok",
        "project": "protected" if dst_parts["project"] != src_parts["project"] else "same",
        "template": "different" if dst_parts["template"] != src_parts["template"] else "same",
    }


def _apply_agents_sectioned(src: Path, changed: set[Path], sync: ctx.SyncContext) -> bool:
    sp, dp = src / "AGENTS.md", sync.consumer / "AGENTS.md"
    if not sp.exists():
        return False
    if not dp.exists():
        dp.write_text(sp.read_text(encoding="utf-8"), encoding="utf-8")
        changed.add(dp)
        print("提示: 消费仓缺 AGENTS.md，已按模板初始化项目区与模板区")
        return True
    target = dp.resolve() if dp.is_symlink() else dp
    result, template_changed, project_diff = replace_template_zone(
        sp.read_text(encoding="utf-8"), target.read_text(encoding="utf-8")
    )
    if project_diff:
        print("提示: AGENTS.md 项目区与模板不同；按规则保留消费仓内容，不覆盖")
    if template_changed:
        target.write_text(result, encoding="utf-8")
        changed.add(target)
    return True


def _agents_parts(text: str, *, allow_extra: bool = False) -> dict[str, tuple[int, int, str]]:
    """兼容旧调用：返回项目区/模板区偏移；新代码只应使用 agents_parts。"""
    begin, end = _zone_bounds(text)
    return {
        "intro": (0, begin, text[:begin]),
        "merge": (begin, end, text[begin:end]),
        "force": (end, end, ""),
    }


def _replace_agents_force(src_text: str, dst_text: str) -> tuple[str, bool, bool, bool]:
    """兼容旧调用，映射到两区替换；force 字段不再存在。"""
    result, changed, project_diff = replace_template_zone(src_text, dst_text)
    return result, changed, False, project_diff
