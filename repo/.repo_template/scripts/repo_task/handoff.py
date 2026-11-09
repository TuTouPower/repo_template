"""唯一 handoff 结构契约；提交、依赖与集成入口复用。"""
from __future__ import annotations

from .context import TaskDataError

HANDOFF_TYPES = {
    "tid": str,
    "attempt": int,
    "execution_id": str,
    "status": str,
    "branch": str,
    "base_sha": str,
    "spec_source_sha": str,
    "tests": str,
    "blackbox": str,
    "review": str,
    "review_scope": str,
    "pending": list,
    "findings": list,
}



def validate_handoff(value, *, source: str) -> None:
    if not isinstance(value, dict):
        raise TaskDataError(f"{source}: handoff 必须为 JSON 对象")
    for key, expected in HANDOFF_TYPES.items():
        item = value.get(key)
        if not isinstance(item, expected) or (expected is int and isinstance(item, bool)):
            raise TaskDataError(f"{source}: handoff 字段 {key} 缺失或类型非法")
        if expected is str and not item.strip():
            raise TaskDataError(f"{source}: handoff 字段 {key} 不能为空")
    if value["attempt"] <= 0 or value["status"] != "done":
        raise TaskDataError(f"{source}: handoff attempt 必须为正整数，status 必须为 done")
    for key in ("pending", "findings"):
        if any(not isinstance(item, str) for item in value[key]):
            raise TaskDataError(f"{source}: handoff {key} 必须为字符串数组")
