"""AGENTS.md 标题协议：分区识别、开发原则强制更新与分区状态。"""

from __future__ import annotations

from pathlib import Path

from . import context as ctx


def _markdown_section(text: str, heading: str) -> tuple[int, int, str] | None:
    """返回 heading 所在 section 的 (start, end, text)，section 结束于下一个同级标题。"""
    lines = text.splitlines(keepends=True)
    offsets: list[tuple[int, str]] = []
    offset = 0
    for line in lines:
        if line.startswith("## "):
            offsets.append((offset, line.strip()))
        offset += len(line)
    for index, (start, title) in enumerate(offsets):
        if title != heading:
            continue
        end = offsets[index + 1][0] if index + 1 < len(offsets) else len(text)
        return start, end, text[start:end]
    return None


def _agents_parts(text: str) -> dict[str, tuple[int, int, str]]:
    """识别 AGENTS.md 的 intro、merge、force 三部分；缺少强制标题则拒绝同步。"""
    merge = _markdown_section(text, ctx.AGENTS_MERGE_HEADING)
    force = _markdown_section(text, ctx.AGENTS_FORCE_HEADING)
    if merge is None or force is None:
        raise ctx.SyncError("AGENTS.md 缺少 ## 目录与读写规则 或 ## 开发原则，拒绝猜测同步")
    intro_end = merge[0]
    return {"intro": (0, intro_end, text[:intro_end]), "merge": merge, "force": force}


def _replace_agents_force(src_text: str, dst_text: str) -> tuple[str, bool, bool, bool]:
    """仅替换开发原则；返回新文本、force 是否变化、merge 是否不同、intro 是否不同。"""
    src_parts = _agents_parts(src_text)
    dst_parts = _agents_parts(dst_text)
    src_force = src_parts["force"]
    dst_force = dst_parts["force"]
    result = dst_text[:dst_force[0]] + src_text[src_force[0]:src_force[1]] + dst_text[dst_force[1]:]
    return (
        result,
        result != dst_text,
        src_parts["merge"][2] != dst_parts["merge"][2],
        src_parts["intro"][2] != dst_parts["intro"][2],
    )


def agents_section_diagnosis(src: Path) -> list[str]:
    """说明 AGENTS.md 标题协议缺在哪一侧；两侧都完整时返回空列表。"""
    issues: list[str] = []
    for label, path in (("模板源", src / "AGENTS.md"), ("消费仓", ctx.CONSUMER / "AGENTS.md")):
        if not path.exists():
            continue
        target = path.resolve() if path.is_symlink() else path
        try:
            _agents_parts(target.read_text(encoding="utf-8"))
        except ctx.SyncError:
            issues.append(f"{label} AGENTS.md 缺 ## 目录与读写规则 或 ## 开发原则")
    return issues


def agents_section_status(src: Path) -> dict[str, str] | None:
    sp, dp = src / "AGENTS.md", ctx.CONSUMER / "AGENTS.md"
    if not sp.exists() or not dp.exists():
        return None
    dst_path = dp.resolve() if dp.is_symlink() else dp
    try:
        src_parts = _agents_parts(sp.read_text(encoding="utf-8"))
        dst_parts = _agents_parts(dst_path.read_text(encoding="utf-8"))
    except ctx.SyncError:
        return None
    return {
        "intro": "protected" if src_parts["intro"][2] != dst_parts["intro"][2] else "same",
        "merge": "different" if src_parts["merge"][2] != dst_parts["merge"][2] else "same",
        "force": "different" if src_parts["force"][2] != dst_parts["force"][2] else "same",
    }


def _apply_agents_sectioned(src: Path, changed: set[Path]) -> bool:
    sp, dp = src / "AGENTS.md", ctx.CONSUMER / "AGENTS.md"
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
        raise ctx.SyncError(
            "消费仓 AGENTS.md 缺 ## 目录与读写规则 或 ## 开发原则；"
            "按模板补齐 ## 目录与读写规则、## 开发原则 后重跑"
        ) from error
    if intro_diff:
        print("提示: AGENTS.md 项目介绍与模板不同；按规则保护消费仓内容，不覆盖")
    if merge_diff:
        print("提示: AGENTS.md 目录与读写规则与模板不同；交由 agent 语义合并，脚本不覆盖")
    if force_changed:
        target.write_bytes(result.encode("utf-8"))
        changed.add(target)
    return True
