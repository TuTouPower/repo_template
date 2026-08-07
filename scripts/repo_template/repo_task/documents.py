"""Canonical documents implementation for the task toolchain."""

import os
import re
from pathlib import Path

import repo_task.context as ctx


def _quote(value: str) -> str:
    """YAML 双引号标量：转义反斜杠与双引号。"""
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'

def _unquote(value: str) -> str:
    """还原 _quote 的转义；未加引号的值原样返回。"""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("\"", "'"):
        inner = value[1:-1]
        if value[0] == "'":
            return inner
        out, i = [], 0
        while i < len(inner):
            if inner[i] == "\\" and i + 1 < len(inner):
                out.append(inner[i + 1])
                i += 2
            else:
                out.append(inner[i])
                i += 1
        return "".join(out)
    return value

def parse_front_matter_text(text: str, *, source: str) -> tuple[dict, str]:
    """从文本返回 (front matter dict, 正文)。"""
    if not text.startswith("---"):
        raise ctx.TaskDataError(f"{source}: task.md 必须以 YAML front matter (---) 开头")
    end = text.find("\n---", 3)
    if end == -1:
        raise ctx.TaskDataError(f"{source}: front matter 未闭合（缺结束的 ---）")
    body = text[end + 4:].lstrip("\n")
    fm = {}
    for line in text[3:end].splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        val = val.strip()
        # 未加引号的值剥掉行内注释（# 前须有空格），防照搬文档示例后值被污染
        if val and val[0] not in ("\"", "'"):
            val = val.split(" #", 1)[0].rstrip()
        fm[key.strip()] = _unquote(val)
    return fm, body

def parse_front_matter(path: Path) -> tuple[dict, str]:
    """返回 (front matter dict, 正文)。缺失或不合法抛 ctx.TaskDataError。

    注意：render_review_prompts.py / check_review_status.py 各有简化副本，
    改解析规则需三处同步。
    """
    return parse_front_matter_text(
        path.read_text(encoding="utf-8"),
        source=ctx._rel(path),
    )

def dump_front_matter(fm: dict) -> str:
    """所有值一律双引号包裹并转义，避免特殊字符破坏 YAML。"""
    keys = list(ctx.FRONT_MATTER_KEYS) + [k for k in fm if k not in ctx.FRONT_MATTER_KEYS]
    lines = ["---"]
    lines += [f"{key}: {_quote(fm[key])}" for key in keys if key in fm]
    lines.append("---")
    return "\n".join(lines) + "\n"

def write_front_matter(path: Path, fm: dict, body: str) -> None:
    path.write_text(dump_front_matter(fm) + "\n" + body, encoding="utf-8", newline="\n")

def tid_sort_key(tid: str) -> int:
    match = ctx.TID_RE.fullmatch(tid)
    if not match:
        raise ctx.TaskDataError(f"tid 非法：{tid!r}")
    return int(match.group(1))

def parse_tid_list(value: str, *, field: str, allow_empty: bool = True) -> list[str]:
    """解析 front matter/严格 CLI 使用的逗号分隔规范 tid。"""
    if value == "" and allow_empty:
        return []
    items = [item.strip() for item in value.split(",")]
    if not items or any(not item for item in items):
        raise ctx.TaskDataError(f"{field} 格式非法：{value!r}（须为逗号分隔 tid）")
    invalid = [item for item in items if not ctx.TID_RE.fullmatch(item)]
    if invalid:
        raise ctx.TaskDataError(f"{field} 含非法 tid：{', '.join(invalid)}")
    return sorted(set(items), key=tid_sort_key)

def dump_tid_list(tids) -> str:
    return ",".join(sorted(set(tids), key=tid_sort_key))

def validate_tid_references(
    tids: list[str],
    *,
    field: str,
    owner_tid: str,
    tasks_by_tid: dict[str, dict],
) -> None:
    if owner_tid in tids:
        raise ctx.TaskDataError(f"{field} 不可引用自身 {owner_tid}")
    missing = [tid for tid in tids if tid not in tasks_by_tid]
    if missing:
        raise ctx.TaskDataError(f"{field} 引用不存在 task：{', '.join(missing)}")

def parse_unverified_contracts(spec_text: str) -> dict[str, list[str]]:
    """分类「未知契约清单」直接列表项中的未核实标记。"""
    entries = []
    collecting = False
    fence_marker = None

    for line in spec_text.splitlines():
        if fence_marker is not None:
            fence = ctx.FENCE_CLOSE_RE.match(line)
            if (
                fence
                and fence.group(1)[0] == fence_marker[0]
                and len(fence.group(1)) >= fence_marker[1]
            ):
                fence_marker = None
            continue

        fence = ctx.FENCE_RE.match(line)
        if fence:
            fence_marker = (fence.group(1)[0], len(fence.group(1)))
            continue

        heading = ctx.H3_RE.fullmatch(line)
        if heading:
            if collecting:
                break
            collecting = heading.group(1).strip() == ctx.UNKNOWN_CONTRACT_HEADING
            continue

        if collecting:
            item = ctx.LIST_ITEM_RE.match(line)
            if item:
                entries.append(item.group(1).strip())

    classified = {"blocking": [], "spike": [], "ambiguous": []}
    for entry in entries:
        kinds = {marker.group(1) for marker in ctx.UNVERIFIED_RE.finditer(entry)}
        if "BLOCKING" in kinds:
            classified["blocking"].append(entry)
        if "SPIKE" in kinds:
            classified["spike"].append(entry)
        if None in kinds:
            classified["ambiguous"].append(entry)
    return classified

def unverified_contract_gate(
    spec_text: str,
    *,
    require_verified: bool = False,
) -> tuple[list[str], list[str]]:
    """返回未知契约的 (阻塞项, 警告项)。"""
    contracts = parse_unverified_contracts(spec_text)
    problems, warnings = [], []

    if contracts["ambiguous"]:
        problems.append(
            f"未知契约清单有 {len(contracts['ambiguous'])} 项裸 UNVERIFIED；"
            "须明确改为 UNVERIFIED-BLOCKING 或 UNVERIFIED-SPIKE"
        )
    if contracts["blocking"]:
        problems.append(
            f"未知契约清单有 {len(contracts['blocking'])} 项 UNVERIFIED-BLOCKING；"
            "须由用户或外部环境核实并改写结论"
        )
    if contracts["spike"]:
        message = (
            f"未知契约清单有 {len(contracts['spike'])} 项 UNVERIFIED-SPIKE；"
            "须完成实验并替换为验证结论"
        )
        if require_verified:
            problems.append(message)
        else:
            warnings.append(f"{message}；当前仅可执行 Step 1")

    return problems, warnings

def _visible_markdown_lines(text: str) -> list[str]:
    """返回 fenced code 外的 Markdown 行。"""
    lines = []
    fence_marker = None
    for line in text.splitlines():
        if fence_marker is not None:
            fence = ctx.FENCE_CLOSE_RE.match(line)
            if (
                fence
                and fence.group(1)[0] == fence_marker[0]
                and len(fence.group(1)) >= fence_marker[1]
            ):
                fence_marker = None
            continue
        fence = ctx.FENCE_RE.match(line)
        if fence:
            fence_marker = (fence.group(1)[0], len(fence.group(1)))
            continue
        lines.append(line)
    return lines

def _strip_inline_code(text: str) -> str:
    """剥除 inline code 片段（一对反引号包裹的内容）。

    占位符扫描专用：真实 spec 里的 API 契约 `{ code:"未登录" }`、JSX
    `{author.name + " 头像"}` 常包在 inline code 内，剥除后不再误判为模板占位符。
    """
    return ctx.INLINE_CODE_RE.sub("", text)

def _markdown_headings(text: str) -> list[tuple[int, str]]:
    headings = []
    for line in _visible_markdown_lines(text):
        match = ctx.HEADING_RE.fullmatch(line)
        if match:
            headings.append((len(match.group(1)), match.group(2).strip()))
    return headings

def _missing_heading_sequence(
    text: str,
    required: tuple[tuple[int, str], ...],
) -> list[str]:
    """返回缺失、错层级或乱序的必需标题。"""
    headings = _markdown_headings(text)
    position = 0
    missing = []
    for expected in required:
        try:
            position = headings.index(expected, position) + 1
        except ValueError:
            missing.append(f"{'#' * expected[0]} {expected[1]}")
    return missing

def _extract_markdown_section(text: str, level: int, title: str) -> str | None:
    """提取指定标题到下一个同级或更高层级标题之间的正文。"""
    lines = text.splitlines()
    start = None
    fence_marker = None
    for index, line in enumerate(lines):
        if fence_marker is not None:
            fence = ctx.FENCE_CLOSE_RE.match(line)
            if (
                fence
                and fence.group(1)[0] == fence_marker[0]
                and len(fence.group(1)) >= fence_marker[1]
            ):
                fence_marker = None
            continue
        fence = ctx.FENCE_RE.match(line)
        if fence:
            fence_marker = (fence.group(1)[0], len(fence.group(1)))
            continue
        heading = ctx.HEADING_RE.fullmatch(line)
        if not heading:
            continue
        heading_level = len(heading.group(1))
        heading_title = heading.group(2).strip()
        if start is None:
            if heading_level == level and heading_title == title:
                start = index + 1
        elif heading_level <= level:
            return "\n".join(lines[start:index])
    if start is None:
        return None
    return "\n".join(lines[start:])

AC_ID_RE = re.compile(r"AC-\d{3}")


def extract_ac_ids(spec_text: str) -> list[str]:
    """从 spec 验收标准节提取 AC-NNN 编号，按出现顺序去重。"""
    acceptance = _extract_markdown_section(spec_text, 3, "验收标准")
    if not acceptance:
        return []
    seen: list[str] = []
    for line in acceptance.splitlines():
        for match in AC_ID_RE.findall(line):
            if match not in seen:
                seen.append(match)
    return seen


def _extract_guide_blocks(text: str) -> list[str]:
    """提取 spec 中所有 `<!-- 规范（门禁必留，不得删除） -->...<!-- /规范 -->` 块。

    返回按出现顺序排列的块列表，每块含标记行与正文（行级 strip）。
    规范块是门禁必留的就近规范，agent 只能替换块外占位符。
    """
    blocks = []
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == ctx.SPEC_GUIDE_OPEN:
            current = [stripped]
        elif current is not None:
            current.append(stripped)
            if stripped == ctx.SPEC_GUIDE_CLOSE:
                blocks.append("\n".join(current))
                current = None
    return blocks


def validate_task_documents(
    spec_text: str,
    task_body: str,
    *,
    require_verified: bool = False,
    allow_template_placeholders: bool = False,
) -> tuple[list[str], list[str]]:
    """校验 task 创建骨架与未知契约门禁，返回 (阻塞项, 警告项)。"""
    problems, warnings = [], []

    missing_spec_headings = _missing_heading_sequence(spec_text, ctx.SPEC_REQUIRED_HEADINGS)
    if missing_spec_headings:
        problems.append(
            "spec.md 缺必需标题、标题层级错误或顺序错误："
            + "、".join(missing_spec_headings)
        )

    # 规范块门禁：模板中带 `<!-- 规范 -->` 标记的就近规范逐字保留，
    # agent 只能替换块外占位符，不得删除或改写规范块内容。
    template_spec_path = ctx.TEMPLATE_DIR / "spec.md"
    if template_spec_path.is_file():
        template_blocks = _extract_guide_blocks(
            template_spec_path.read_text(encoding="utf-8")
        )
        if template_blocks:
            spec_blocks = set(_extract_guide_blocks(spec_text))
            missing_blocks = [b for b in template_blocks if b not in spec_blocks]
            if missing_blocks:
                problems.append(
                    f"spec.md 缺或被改 {len(missing_blocks)} 个规范块"
                    "（`<!-- 规范 -->` 标记内的内容不得删除或改写，"
                    "只能替换块外占位符；改模板须同步修改本校验）：\n  - "
                    + "\n  - ".join(
                        block.replace("\n", " ")[:100] for block in missing_blocks
                    )
                )

    acceptance = _extract_markdown_section(spec_text, 3, "验收标准")
    if not acceptance or not re.search(
        r"^\s*-\s*\[ \]\s*\S", acceptance, re.MULTILINE
    ):
        problems.append("spec.md 验收标准为空")

    if acceptance:
        ac_ids: list[str] = []
        for line in acceptance.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- [ ]"):
                continue
            match = re.match(
                r"-\s*\[\s*\]\s*(?:\[deploy\]\s*)?(AC-\d{3})", stripped
            )
            if not match:
                problems.append(
                    "spec.md 验收标准条目缺 AC 编号（格式 `- [ ] AC-NNN：`，"
                    f"[deploy] 位于编号前）：{stripped[:60]}"
                )
                continue
            ac_id = match.group(1)
            if ac_id in ac_ids:
                problems.append(f"spec.md AC 编号重复：{ac_id}")
            ac_ids.append(ac_id)

    missing_task_headings = _missing_heading_sequence(task_body, ctx.TASK_REQUIRED_HEADINGS)
    if missing_task_headings:
        problems.append(
            "task.md 缺必需标题、标题层级错误或顺序错误："
            + "、".join(missing_task_headings)
        )

    visible_task_lines = {line.strip() for line in _visible_markdown_lines(task_body)}
    missing_guidance = [
        line for line in ctx.IMPLEMENTATION_NOTE_GUIDANCE if line not in visible_task_lines
    ]
    if missing_guidance:
        problems.append("task.md 实施笔记缺模板固定说明")

    notes = _extract_markdown_section(task_body, 2, "实施笔记")
    if notes is not None:
        payload_lines = [
            line.strip()
            for line in _visible_markdown_lines(notes)
            if line.strip() and line.strip() not in ctx.IMPLEMENTATION_NOTE_GUIDANCE
        ]
        if not payload_lines:
            problems.append("task.md 实施笔记为空；无事项时写「无」")

    if not allow_template_placeholders:
        visible_text = "\n".join(_visible_markdown_lines(spec_text + "\n" + task_body))
        placeholders = ctx.TEMPLATE_PLACEHOLDER_RE.findall(_strip_inline_code(visible_text))
        if placeholders:
            problems.append(
                f"spec.md / task.md 残留 {len(placeholders)} 个模板占位符："
                + "、".join(sorted(set(placeholders))[:3])
            )

    contract_problems, contract_warnings = unverified_contract_gate(
        spec_text,
        require_verified=require_verified,
    )
    problems.extend(contract_problems)
    warnings.extend(contract_warnings)
    return problems, warnings
