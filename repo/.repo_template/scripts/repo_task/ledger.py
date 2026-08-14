"""Locked JSONL storage primitives for task execution events."""

import json
import os
import sys
from datetime import datetime
from typing import Callable

import repo_task.context as ctx

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def _ledger_lock_fh(fh) -> None:
    fh.seek(0)
    if os.name == "nt":
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
    else:
        fcntl.flock(fh, fcntl.LOCK_EX)


def _ledger_unlock_fh(fh) -> None:
    fh.seek(0)
    if os.name == "nt":
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fh, fcntl.LOCK_UN)


def _sanitize(event: dict) -> dict:
    final = {
        key: value.replace("\n", " ").replace("\r", " ")
        if isinstance(value, str) else value
        for key, value in event.items()
    }
    final["ts"] = datetime.now(ctx.TZ_CN).isoformat(timespec="seconds")
    return final


def _read_unlocked() -> list[dict]:
    if not ctx.LEDGER_PATH.is_file():
        return []
    events = []
    for line_no, line in enumerate(
        ctx.LEDGER_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as e:
            # fail-closed：损坏行跳过会让后续事件基于缺失事件续写，导致
            # attempt 号复用或 current identity 误判。拒绝继续，提示手动修复。
            raise ctx.TaskDataError(
                f"调度账本 {ctx._rel(ctx.LEDGER_PATH)} 第 {line_no} 行损坏（{e}）；"
                "拒绝继续以防 attempt 号复用。请手动修复该行后重试"
            ) from None
        if not isinstance(value, dict):
            raise ctx.TaskDataError(
                f"调度账本 {ctx._rel(ctx.LEDGER_PATH)} 第 {line_no} 行非 JSON 对象；拒绝继续"
            )
        events.append(value)
    return events


def _append_many_unlocked(events: list[dict]) -> list[dict]:
    finals = [_sanitize(event) for event in events]
    if not finals:
        return []
    payload = "".join(
        json.dumps(event, ensure_ascii=False) + "\n" for event in finals
    )
    # 无换行尾行（截断残留）会被新事件直接拼接；先补换行把坏尾行隔离为独立行
    if ctx.LEDGER_PATH.is_file() and ctx.LEDGER_PATH.stat().st_size > 0:
        with ctx.LEDGER_PATH.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(-1, os.SEEK_END)
            if stream.read(1) != b"\n":
                with ctx.LEDGER_PATH.open("ab") as fix:
                    fix.write(b"\n")
    with ctx.LEDGER_PATH.open("a", encoding="utf-8") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return finals


def _append_unlocked(event: dict) -> dict:
    return _append_many_unlocked([event])[0]


def _with_lock(callback):
    ctx.LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ctx.LEDGER_PATH.with_suffix(".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_fh:
        # 空 lock 文件上 Windows msvcrt.locking(1 字节) 会失败，先写入 1 字节。
        # 注意：锁是 CLI 单进程独占使用，非线程安全（msvcrt 进程级、fcntl 描述符级）。
        if lock_fh.tell() == 0:
            lock_fh.write("\0")
            lock_fh.flush()
        _ledger_lock_fh(lock_fh)
        try:
            return callback()
        finally:
            _ledger_unlock_fh(lock_fh)


def ledger_read() -> list[dict]:
    """Read all valid JSON object events while holding the ledger lock."""
    return _with_lock(_read_unlocked)


def ledger_append(event: dict) -> dict:
    """Append one sanitized event while holding the ledger lock."""
    return _with_lock(lambda: _append_unlocked(event))


def ledger_locked_append(builder: Callable[[list[dict]], dict]) -> dict:
    """Build and append one event against a fresh locked ledger snapshot."""
    def update():
        events = _read_unlocked()
        return _append_unlocked(builder(events))

    return _with_lock(update)


def ledger_locked_append_many(
    builder: Callable[[list[dict]], list[dict]],
) -> list[dict]:
    """Preflight and append a complete event batch under one ledger lock."""
    def update():
        events = _read_unlocked()
        return _append_many_unlocked(builder(events))

    return _with_lock(update)


def ledger_next_attempt(tid: str, events: list[dict]) -> int:
    attempts = [
        event.get("attempt") for event in events
        if event.get("tid") == tid
        and isinstance(event.get("attempt"), int)
        and not isinstance(event.get("attempt"), bool)
        and event.get("attempt") > 0
    ]
    return max(attempts, default=0) + 1


def ledger_allocate_attempt(
    tid: str,
    builder: Callable[[int, list[dict]], dict],
) -> dict:
    """Atomically allocate the next integer attempt and append its event."""
    def allocate(events: list[dict]) -> dict:
        attempt = ledger_next_attempt(tid, events)
        event = builder(attempt, events)
        event["tid"] = tid
        event["attempt"] = attempt
        return event

    return ledger_locked_append(allocate)


def _ledger_append_safely(event: dict) -> None:
    """Best-effort append for topology and integration events."""
    try:
        if not ctx.LEDGER_PATH.resolve().is_relative_to(ctx.REPO_ROOT.resolve()):
            return
        ledger_append(event)
    except (OSError, ctx.TaskDataError) as error:
        # TaskDataError：ledger 损坏 fail-closed；写失败也不阻断主流程，仅记录。
        # 事件上下文（tid/attempt/execution_id）进 WARNING，便于事后定位缺口（F34）
        print(
            f"WARNING: 调度账本写入失败（{error}）；"
            f"event={event.get('event')} tid={event.get('tid')} "
            f"attempt={event.get('attempt')} "
            f"execution_id={event.get('execution_id')} 事件未记录",
            file=sys.stderr,
        )
