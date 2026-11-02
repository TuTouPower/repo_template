"""Project verification policy and executable gates, shared by task and sync."""

import re
import subprocess
from pathlib import Path

import repo_task.context as ctx

from .locks import validation_environment


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
        if not blocks or not any(block.strip() for block in blocks):
            raise ValidationError(f"{name} 必须有可执行 bash/sh 代码块，或写「无」并在 verification_notes 说明理由")
        commands[name] = "\n".join(blocks)
    return commands


def run_checks(root: Path, *, timeout: int = 300) -> list[str]:
    commands = load_policy(root)
    completed = []
    for name, command in commands.items():
        if command:
            try:
                result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", command],
                                        cwd=root, capture_output=True, text=True, timeout=timeout,
                                        env=validation_environment(root))
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ValidationError(f"{name} 执行失败：{error}") from error
            if result.returncode:
                raise ValidationError(f"{name} 失败（exit {result.returncode}）：\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}")
        completed.append(f"{name}: {'PASS' if command else '无（项目已明确选择）'}")
    return completed
