"""消费仓 conventions.md 的原子写约定迁移。"""

from __future__ import annotations

import re
from pathlib import Path

from . import context as ctx


def _migrate_conventions_text(text: str) -> str:
    # 替换旧导入语句
    text = re.sub(
        r"from\s+task\s+import\s+(_atomic_write_text|atomic_write_text)(?:（[^）\n]*）)?",
        "from repo_task.documents import atomic_write_text",
        text,
    )
    # 替换 helper 归属描述
    text = re.sub(
        r"([`'\"\(]?(?:\.repo_template/)?(?:scripts/)?task\.py\s*(?:的)?\s*)(_atomic_write_text|atomic_write_text)",
        r"repo_task.documents 的 atomic_write_text",
        text,
    )
    return text


def conventions_migration_status() -> list[str]:
    if not ctx.CONVENTIONS_PATH.is_file():
        return []
    before = ctx.CONVENTIONS_PATH.read_text(encoding="utf-8")
    after = _migrate_conventions_text(before)
    if after != before:
        return [ctx._rel(ctx.CONVENTIONS_PATH)]
    return []


def migrate_conventions(changed: set[Path]) -> list[str]:
    if not ctx.CONVENTIONS_PATH.is_file():
        return []
    before = ctx.CONVENTIONS_PATH.read_text(encoding="utf-8")
    after = _migrate_conventions_text(before)
    if after != before:
        ctx.CONVENTIONS_PATH.write_text(after, encoding="utf-8")
        changed.add(ctx.CONVENTIONS_PATH)
        return [f"{ctx._rel(ctx.CONVENTIONS_PATH)} 已自动迁移原子写约定为 from repo_task.documents import atomic_write_text（保留消费仓自定义内容）"]
    return []
