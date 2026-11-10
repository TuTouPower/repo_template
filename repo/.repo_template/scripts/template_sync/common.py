"""跨模块共享的小工具：git 调用、JSON 读取与缩进探测、时间戳、消费仓锁。"""

from __future__ import annotations

import json
import subprocess
from functools import wraps
from pathlib import Path

from repo_task.file_transaction import (
    FINISHED_STATUSES,
    OPEN_STATUSES,
    SYNC_TRANSACTION_DIR,
    FileTransaction,
    state_transaction,
)
from repo_task.locks import CONTROL_LOCK_NAME, git_common_lock

from . import context as ctx

LEGACY_SYNC_TRANSACTION_DIR = ".scratch/repo_sync_rollback"


def _legacy_transaction_hint(detail: str) -> str:
    return (
        f"{detail}；新 transaction 只读 Git common 目录，看不到 {LEGACY_SYNC_TRANSACTION_DIR}。"
        "请先用升级前的消费仓脚本（旧版 repo_sync.py）执行 `transaction accept` 或 "
        "`transaction rollback`（现场变化时 rollback 需加 --confirm-restore），"
        "或人工核对该目录后再迁移；未自动 accept/rollback"
    )


def open_legacy_sync_transaction(sync: ctx.SyncContext) -> str | None:
    """旧 ``.scratch`` 同步事务未结束或记录损坏时返回阻断说明；否则 None。"""
    journal = sync.consumer / LEGACY_SYNC_TRANSACTION_DIR / "transaction.json"
    if not journal.is_file():
        return None
    try:
        data = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _legacy_transaction_hint(
            f"旧同步事务记录损坏：{LEGACY_SYNC_TRANSACTION_DIR}/transaction.json 无法读取"
        )
    status = data.get("status") if isinstance(data, dict) else None
    if status in FINISHED_STATUSES:
        return None
    if status in OPEN_STATUSES:
        return _legacy_transaction_hint(
            f"旧同步事务未结束：{LEGACY_SYNC_TRANSACTION_DIR} 状态为 {status}"
        )
    return _legacy_transaction_hint(
        f"旧同步事务记录非法：{LEGACY_SYNC_TRANSACTION_DIR}/transaction.json 状态为 {status!r}"
    )


def migration_transaction(sync: ctx.SyncContext) -> FileTransaction:
    """迁移期文件事务：state-root 未迁移时沿用旧 ``.scratch`` 事务目录。

    entity-storage 等步骤先于 state-root 执行。若此时直接写 common dir 的
    ``sync_transaction``，state-root 搬 ``.scratch/repo_sync_rollback`` 时会与
    已有目录冲突；旧目录存在时沿用它，由 state-root 统一迁入 common dir。
    旧目录内事务未结束时立即停止：新 transaction 看不到该目录，不能静默接管。
    """
    blocker = open_legacy_sync_transaction(sync)
    if blocker:
        raise ctx.SyncError(blocker)
    legacy = sync.consumer / LEGACY_SYNC_TRANSACTION_DIR
    if legacy.exists():
        return FileTransaction(sync.consumer, LEGACY_SYNC_TRANSACTION_DIR)
    return state_transaction(sync.consumer, SYNC_TRANSACTION_DIR)


def consumer_locked(function):
    @wraps(function)
    def locked(*args, **kwargs):
        sync = kwargs.get("sync")
        if sync is None and args:
            sync = getattr(args[0], "sync", None)
        if sync is None:
            raise ctx.SyncError(
                f"{function.__name__} 缺少显式 SyncContext；调用方须经 args.sync 传入"
            )
        with git_common_lock(sync.consumer, CONTROL_LOCK_NAME):
            return function(*args, **kwargs)
    return locked


def _git(repo: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise ctx.SyncError(f"git {' '.join(args)} 超时（{timeout}s）") from error


def _load_json_object(path: Path, label: str) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ctx.SyncError(f"{label} 无法读取（{error}）；请核对权限后重试") from error
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ctx.SyncError(
            f"{label} 损坏（{error}）；请核对 JSON 内容、从备份恢复或重新运行 init --source <path|url>"
        ) from error
    if not isinstance(data, dict):
        raise ctx.SyncError(
            f"{label} 格式错误（预期为 JSON 对象）；请修复或重新运行 init --source <path|url>"
        )
    return data


def _detect_json_indent(raw: str) -> int | str:
    """从现有 JSON 文本推断缩进（首个 ^(空格|tab)+\" 行），无匹配回退 2。

    同步改写消费仓 JSON（settings.json / MCP）时沿用原缩进，避免把消费仓
    4 空格体例重排成 2 空格、反而弄红其 prettier 门禁。
    """
    for line in raw.splitlines():
        if not line or line[0] not in (" ", "\t"):
            continue
        stripped = line.lstrip(" \t")
        if stripped.startswith('"'):
            prefix = line[: len(line) - len(stripped)]
            if set(prefix) == {"\t"}:
                return "\t"
            if set(prefix) == {" "}:
                return len(prefix)
    return 2


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")
