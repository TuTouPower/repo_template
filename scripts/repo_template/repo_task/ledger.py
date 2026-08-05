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
    final["ts"] = datetime.now().astimezone().isoformat(timespec="seconds")
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
        except json.JSONDecodeError:
            print(
                f"WARNING: 调度账本 {ctx._rel(ctx.LEDGER_PATH)} 第 {line_no} 行无法解析，已跳过",
                file=sys.stderr,
            )
            continue
        if isinstance(value, dict):
            events.append(value)
        else:
            print(
                f"WARNING: 调度账本 {ctx._rel(ctx.LEDGER_PATH)} 第 {line_no} 行非 JSON 对象，已跳过",
                file=sys.stderr,
            )
    return events


def _append_many_unlocked(events: list[dict]) -> list[dict]:
    finals = [_sanitize(event) for event in events]
    if not finals:
        return []
    payload = "".join(
        json.dumps(event, ensure_ascii=False) + "\n" for event in finals
    )
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
    except OSError as error:
        print(
            f"WARNING: 调度账本写入失败（{error}）；{event.get('event')} 事件未记录",
            file=sys.stderr,
        )
