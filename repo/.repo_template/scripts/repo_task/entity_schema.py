"""当前实体协议边界；不包含任何旧路径或旧格式解析。"""
from __future__ import annotations

import json
from pathlib import Path

from .context import TaskDataError

SCHEMA_PATH = ".repo_template/entity_schema.json"
SCHEMA_VERSION = 1


def validate_schema(data, *, source: str) -> None:
    if not isinstance(data, dict) or set(data) != {"version"} or type(data["version"]) is not int or data["version"] != SCHEMA_VERSION:
        raise TaskDataError(f"不支持的实体 schema：{source} = {data!r}")


def current_schema(root: Path) -> bool:
    path = root / SCHEMA_PATH
    if path.is_symlink():
        raise TaskDataError(f"实体 schema 标记不能是软链：{path}")
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise TaskDataError(f"无法读取实体 schema 标记 {path}：{error}") from error
    validate_schema(data, source=str(path))
    return True
