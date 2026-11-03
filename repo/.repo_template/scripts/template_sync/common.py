"""跨模块共享的小工具：git 调用、JSON 读取与缩进探测、时间戳、消费仓锁。"""

from __future__ import annotations

import json
import subprocess
from functools import wraps
from pathlib import Path

from repo_task.locks import CONTROL_LOCK_NAME, git_common_lock

from . import context as ctx


def consumer_locked(function):
    @wraps(function)
    def locked(*args, **kwargs):
        with git_common_lock(ctx.CONSUMER, CONTROL_LOCK_NAME):
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
