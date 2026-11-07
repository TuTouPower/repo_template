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


def conventions_migration_status(sync=None) -> list[str]:
    sync = sync or ctx.current_sync()
    if not sync.conventions_path.is_file():
        return []
    before = sync.conventions_path.read_text(encoding="utf-8")
    after = _migrate_conventions_text(before)
    if after != before:
        return [sync.rel(sync.conventions_path)]
    return []


def migrate_conventions(changed: set[Path], sync=None) -> list[str]:
    sync = sync or ctx.current_sync()
    if not sync.conventions_path.is_file():
        return []
    before = sync.conventions_path.read_text(encoding="utf-8")
    after = _migrate_conventions_text(before)
    if after != before:
        sync.conventions_path.write_text(after, encoding="utf-8")
        changed.add(sync.conventions_path)
        return [f"{sync.rel(sync.conventions_path)} 已自动迁移原子写约定为 from repo_task.documents import atomic_write_text（保留消费仓自定义内容）"]
    return []
