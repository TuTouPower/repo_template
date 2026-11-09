"""AGENTS.md 标题协议：分区识别、开发原则强制更新与分区状态。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import context as ctx


def _markdown_headings(text: str) -> list[tuple[int, str]]:
    """返回所有顶层 ## 标题的 (offset, title)，忽略代码块内的行。"""
    lines = text.splitlines(keepends=True)
    offsets: list[tuple[int, str]] = []
    offset = 0
    in_fence = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
        elif not in_fence and line.startswith("## "):
            offsets.append((offset, stripped))
        offset += len(line)
    return offsets


def _check_agents_structure(text: str) -> tuple[int, int, list[str], list[str]]:
    """检查 AGENTS.md 标题结构。

    返回 (offset_merge, offset_force, between_extra, after_extra)。
    缺少强制标题或标题顺序错误时抛出 SyncError。
    """
    headings = _markdown_headings(text)
    merge_indices = [i for i, (_, t) in enumerate(headings) if t == ctx.AGENTS_MERGE_HEADING]
    force_indices = [i for i, (_, t) in enumerate(headings) if t == ctx.AGENTS_FORCE_HEADING]
    if not merge_indices or not force_indices:
        raise ctx.SyncError(
            "AGENTS.md 缺 ## 目录与读写规则 或 ## 开发原则；"
            "按模板补齐 ## 目录与读写规则、## 开发原则 后重跑"
        )
    if len(merge_indices) > 1:
        raise ctx.SyncError("AGENTS.md 存在重复的 ## 目录与读写规则 标题")
    if len(force_indices) > 1:
        raise ctx.SyncError("AGENTS.md 存在重复的 ## 开发原则 标题")
    idx_merge = merge_indices[0]
    idx_force = force_indices[0]
    if idx_force < idx_merge:
        raise ctx.SyncError("AGENTS.md 标题顺序错误：## 开发原则 位于 ## 目录与读写规则 之前")
    between = [headings[i][1] for i in range(idx_merge + 1, idx_force)]
    after = [headings[i][1] for i in range(idx_force + 1, len(headings))]
    return headings[idx_merge][0], headings[idx_force][0], between, after


def _agents_parts(text: str, *, allow_extra: bool = False) -> dict[str, tuple[int, int, str]]:
    """识别 AGENTS.md 的 intro、merge、force 三部分。

    默认拒绝在 ## 开发原则 之后或 ## 目录与读写规则 之间存在额外 ## 标题。
    """
    headings = _markdown_headings(text)
    offset_merge, offset_force, between, after = _check_agents_structure(text)
    if not allow_extra and (between or after):
        if after:
            raise ctx.SyncError(
                f"AGENTS.md 在 ## 开发原则 之后存在额外标题：{after}；"
                "项目专属规则须移至 ## 目录与读写规则 之前（项目介绍区）"
            )
        raise ctx.SyncError(
            f"AGENTS.md 在 ## 目录与读写规则 和 ## 开发原则 之间存在额外标题：{between}；"
            "该区域仅允许 ## 目录与读写规则 自身"
        )
    if allow_extra and (between or after):
        merge_indices = [i for i, (_, t) in enumerate(headings) if t == ctx.AGENTS_MERGE_HEADING]
        force_indices = [i for i, (_, t) in enumerate(headings) if t == ctx.AGENTS_FORCE_HEADING]
        idx_merge = merge_indices[0]
        idx_force = force_indices[0]
        offset_merge_end = headings[idx_merge + 1][0]
        offset_force_end = headings[idx_force + 1][0] if idx_force + 1 < len(headings) else len(text)
        return {
            "intro": (0, offset_merge, text[:offset_merge]),
            "merge": (offset_merge, offset_merge_end, text[offset_merge:offset_merge_end]),
            "force": (offset_force, offset_force_end, text[offset_force:offset_force_end]),
        }
    return {
        "intro": (0, offset_merge, text[:offset_merge]),
        "merge": (offset_merge, offset_force, text[offset_merge:offset_force]),
        "force": (offset_force, len(text), text[offset_force:]),
    }


def _replace_agents_force(src_text: str, dst_text: str) -> tuple[str, bool, bool, bool]:
    """仅替换开发原则至文件末尾；返回新文本、force 是否变化、merge 是否不同、intro 是否不同。"""
    src_parts = _agents_parts(src_text)
    dst_parts = _agents_parts(dst_text)
    src_force = src_parts["force"]
    dst_force = dst_parts["force"]
    result = dst_text[:dst_force[0]] + src_text[src_force[0]:]
    return (
        result,
        result != dst_text,
        src_parts["merge"][2] != dst_parts["merge"][2],
        src_parts["intro"][2] != dst_parts["intro"][2],
    )


def agents_section_diagnosis(src: Path, sync: ctx.SyncContext) -> list[str]:
    """说明 AGENTS.md 标题协议问题；两侧都完整合规时返回空列表。"""
    issues: list[str] = []
    for label, path in (("模板源", src / "AGENTS.md"), ("消费仓", sync.consumer / "AGENTS.md")):
        if not path.exists():
            continue
        target = path.resolve() if path.is_symlink() else path
        try:
            _agents_parts(target.read_text(encoding="utf-8"))
        except ctx.SyncError as err:
            msg = str(err)
            if not msg.startswith(f"{label} "):
                msg = f"{label} {msg}"
            issues.append(msg)
    return issues


def agents_section_status(src: Path, sync: ctx.SyncContext) -> dict[str, Any] | None:
    sp, dp = src / "AGENTS.md", sync.consumer / "AGENTS.md"
    if not sp.exists() or not dp.exists():
        return None
    dst_path = dp.resolve() if dp.is_symlink() else dp
    try:
        src_parts = _agents_parts(sp.read_text(encoding="utf-8"))
        _, _, dst_between, dst_after = _check_agents_structure(dst_path.read_text(encoding="utf-8"))
        dst_parts = _agents_parts(dst_path.read_text(encoding="utf-8"), allow_extra=True)
    except ctx.SyncError:
        return None
    status: dict[str, Any] = {
        "intro": "protected" if src_parts["intro"][2] != dst_parts["intro"][2] else "same",
        "merge": "different" if src_parts["merge"][2] != dst_parts["merge"][2] else "same",
        "force": "different" if src_parts["force"][2] != dst_parts["force"][2] else "same",
    }
    extra = dst_between + dst_after
    if extra:
        status["extra"] = extra
    return status


def _apply_agents_sectioned(src: Path, changed: set[Path], sync: ctx.SyncContext) -> bool:
    sp, dp = src / "AGENTS.md", sync.consumer / "AGENTS.md"
    if not sp.exists():
        return False
    src_text = sp.read_text(encoding="utf-8")
    # 旧版模板源没有固定标题时，交回旧的整文件裁定逻辑。
    try:
        _agents_parts(src_text)
    except ctx.SyncError:
        return False
    if not dp.exists():
        raise ctx.SyncError("模板 AGENTS.md 已启用分区协议，但消费仓缺少 AGENTS.md")
    target = dp.resolve() if dp.is_symlink() else dp
    dst_text = target.read_text(encoding="utf-8")
    try:
        result, force_changed, merge_diff, intro_diff = _replace_agents_force(src_text, dst_text)
    except ctx.SyncError as error:
        msg = str(error)
        if not msg.startswith("消费仓 "):
            msg = f"消费仓 {msg}"
        raise ctx.SyncError(msg) from error
    if intro_diff:
        print("提示: AGENTS.md 项目介绍与模板不同；按规则保护消费仓内容，不覆盖")
    if merge_diff:
        print("提示: AGENTS.md 目录与读写规则与模板不同；交由 agent 语义合并，脚本不覆盖")
    if force_changed:
        target.write_bytes(result.encode("utf-8"))
        changed.add(target)
    return True
