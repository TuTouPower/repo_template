"""跨模块共享的小工具：git 调用、JSON 读取与缩进探测、时间戳、消费仓锁。"""

from __future__ import annotations

import json
import subprocess
from functools import wraps
from pathlib import Path

from repo_task.file_transaction import (
    FINISHED_STATUSES,
    SYNC_TRANSACTION_DIR,
    FileTransaction,
    state_transaction,
)
from repo_task.locks import CONTROL_LOCK_NAME, git_common_lock

from . import context as ctx


LEGACY_TRANSACTION_DIRS = (
    ".scratch/repo_sync_rollback", ".scratch/task_graph_transaction",
)


def finished_legacy_transactions(sync: ctx.SyncContext) -> list[Path]:
    """预检全部旧事务，返回可由 state-root 标准事务删除的已结束目录。

    不接管旧 journal；未结束、损坏或无法安全访问的目录一律停止，保留原件。
    """
    finished = []
    for rel in LEGACY_TRANSACTION_DIRS:
        directory = sync.consumer / rel
        try:
            if directory.parent.is_symlink() or directory.is_symlink():
                raise ValueError("路径是软链")
            if not directory.exists():
                continue
            if not directory.is_dir():
                raise ValueError("路径不是目录")
            journal = directory / "transaction.json"
            if journal.is_symlink():
                raise ValueError("journal 是软链")
            if not journal.exists() and not any(directory.iterdir()):
                finished.append(directory)
                continue
            data = json.loads(journal.read_text(encoding="utf-8"))
            status = data.get("status") if isinstance(data, dict) else None
            if not isinstance(status, str) or status not in FINISHED_STATUSES:
                raise ValueError(f"未结束或状态非法：{status!r}")
        except (OSError, UnicodeError, ValueError) as error:
            raise ctx.SyncError(
                f"旧文件事务 {rel} 无法退役（{error}）；停止迁移并保留备份，"
                "须先使用对应旧版本恢复或人工核验；当前 transaction 不接管旧记录"
            ) from error
        finished.append(directory)
    return finished


def migration_transaction(sync: ctx.SyncContext) -> FileTransaction:
    """迁移期文件事务：恒落在 Git common 状态目录的 ``sync_transaction``。

    旧 ``.scratch`` 事务只做退役预检，不沿用其存储位置。state-root 作为独立
    迁移 step 自建同一标准事务，不套在本函数之上，避免同目录嵌套冲突；
    崩溃恢复统一走 ``transaction status/rollback``。
    """
    finished_legacy_transactions(sync)
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
