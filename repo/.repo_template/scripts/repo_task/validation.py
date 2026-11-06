"""Project verification policy parsing, shared by task and sync."""

import re
from pathlib import Path

import repo_task.context as ctx


class ValidationError(ValueError):
    pass


def sections(text: str) -> dict[str, str]:
    """按 H2 切分。围栏按 (字符, 长度) 识别，闭栏须同字符且不短于开栏。"""
    result = {}
    current = None
    fence = None
    for line in text.splitlines():
        if fence is None:
            marker = ctx.FENCE_RE.match(line)
            if marker:
                fence = (marker.group(1)[0], len(marker.group(1)))
                if current is not None:
                    result[current] += line + "\n"
                continue
            heading = re.match(r"^##\s+(.+?)\s*$", line)
            if heading:
                current = heading[1]
                if current in result:
                    raise ValidationError(f"testing.md 重复章节：{current}")
                result[current] = ""
            elif current is not None:
                result[current] += line + "\n"
        else:
            close = ctx.FENCE_CLOSE_RE.match(line)
            if close and close.group(1)[0] == fence[0] and len(close.group(1)) >= fence[1]:
                fence = None
            if current is not None:
                result[current] += line + "\n"
    return result


def _bash_blocks(body: str) -> list[str]:
    """提取 bash/sh 围栏块正文；支持任意长度围栏与嵌套较短围栏行。"""
    blocks = []
    fence = None
    current = None
    for line in body.splitlines():
        if fence is None:
            marker = ctx.FENCE_RE.match(line)
            if marker:
                raw = marker.group(1)
                fence = (raw[0], len(raw))
                info = line[marker.end():].strip()
                current = [] if info in ("bash", "sh") else None
            continue
        close = ctx.FENCE_CLOSE_RE.match(line)
        if close and close.group(1)[0] == fence[0] and len(close.group(1)) >= fence[1]:
            if current is not None:
                blocks.append("\n".join(current))
            fence = None
            current = None
            continue
        if current is not None:
            current.append(line)
    return blocks


def load_policy(root: Path) -> dict[str, str]:
    path = root / "docs/blueprint/testing.md"
    if not path.is_file():
        raise ValidationError("缺 docs/blueprint/testing.md；先明确项目验证策略")
    return parse_policy(path.read_text(encoding="utf-8"))


def parse_policy(text: str) -> dict[str, str]:
    values = sections(text)
    if values.get("verification_policy", "").strip() != "project_configured":
        raise ValidationError("项目验证策略尚未确认：verification_policy 须为 project_configured；模板 contract 不能代替业务验收")
    if not values.get("verification_notes", "").strip():
        raise ValidationError("verification_notes 必须记录验证范围及不适用门禁的理由")
    if "尚未选择业务验证策略" in values["verification_notes"]:
        raise ValidationError("verification_notes 仍为模板占位说明；须填写项目实际验证范围")
    commands = {}
    for name in ("doctor_cmd", "test_cmd", "blackbox_verify"):
        body = values.get(name, "").strip()
        if body == "无":
            commands[name] = ""
            continue
        blocks = _bash_blocks(body)
        if len(blocks) > 1:
            raise ValidationError(
                f"{name} 只允许一个 bash/sh 代码块，检测到 {len(blocks)} 个；"
                "示例、占位符或说明块请改用非 bash/sh 围栏或写在正文，避免被拼接成真实门禁执行"
            )
        if not blocks or not blocks[0].strip():
            raise ValidationError(f"{name} 必须有可执行 bash/sh 代码块，或写「无」并在 verification_notes 说明理由")
        commands[name] = blocks[0]
    return commands
