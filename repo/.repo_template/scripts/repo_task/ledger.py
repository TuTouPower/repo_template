"""Locked JSONL storage primitives for task execution events."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Callable

import repo_task.context as ctx

from .locks import file_lock


def _sanitize(event: dict) -> dict:
    final = {
        key: value.replace("\n", " ").replace("\r", " ")
        if isinstance(value, str) else value
        for key, value in event.items()
    }
    final["ts"] = datetime.now(ctx.TZ_CN).isoformat(timespec="seconds")
    return final


def _read_unlocked(
    path=None, *, tid: str | None = None, repo: "ctx.RepoContext | None" = None,
) -> list[dict]:
    repo = repo or ctx.current_context()
    path = path or repo.ledger_path
    try:
        stream = path.open("r", encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as error:
        # fail-closed：账本存在但不可读与「不存在」必须区分。按空账本继续会让
        # attempt reserve 从 1 重新分配，破坏 exact identity（Issue #70）。
        raise ctx.TaskDataError(
            f"调度账本 {repo.rel(path)} 存在但无法读取（{error}）；"
            "拒绝继续以防 attempt 号复用。请确认权限/文件系统状态后重试"
        ) from error
    events = []
    tid_needle = f'"{tid}"' if tid else None
    try:
        with stream:
            for line_no, raw_line in enumerate(stream, 1):
                line = raw_line.strip()
                if not line:
                    continue
                # 若指定了 tid，先通过快速子串预筛，跳过不含目标 tid 的无关行 JSON 解析开销（Issue #57）
                if tid_needle and tid_needle not in line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as e:
                    # fail-closed：损坏行跳过会让后续事件基于缺失事件续写，导致
                    # attempt 号复用或 current identity 误判。拒绝继续，提示手动修复。
                    raise ctx.TaskDataError(
                        f"调度账本 {repo.rel(path)} 第 {line_no} 行损坏（{e}）；"
                        "拒绝继续以防 attempt 号复用。请手动修复该行后重试"
                    ) from None
                if not isinstance(value, dict):
                    raise ctx.TaskDataError(
                        f"调度账本 {repo.rel(path)} 第 {line_no} 行非 JSON 对象；拒绝继续"
                    )
                if tid and value.get("tid") != tid:
                    continue
                events.append(value)
    except OSError as error:
        raise ctx.TaskDataError(
            f"调度账本 {repo.rel(path)} 读取中断（{error}）；"
            "拒绝继续以防 attempt 号复用"
        ) from error
    return events


def _append_many_unlocked(
    events: list[dict], *, repo: "ctx.RepoContext | None" = None,
) -> list[dict]:
    repo = repo or ctx.current_context()
    finals = [_sanitize(event) for event in events]
    if not finals:
        return []
    payload = "".join(
        json.dumps(event, ensure_ascii=False) + "\n" for event in finals
    ).encode("utf-8")
    # 单次 a+b 打开并在末尾检测尾字节是否缺换行（避免多次打开大文件）（Issue #57）
    repo.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with repo.ledger_path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        if size > 0:
            stream.seek(size - 1)
            if stream.read(1) != b"\n":
                stream.write(b"\n")
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return finals


def _append_unlocked(event: dict, *, repo: "ctx.RepoContext | None" = None) -> dict:
    return _append_many_unlocked([event], repo=repo)[0]


def _with_lock(callback, path=None, *, repo: "ctx.RepoContext | None" = None):
    repo = repo or ctx.current_context()
    path = path or repo.ledger_path
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with file_lock(lock_path):
        return callback()


def ledger_read(
    path=None, *, tid: str | None = None, repo: "ctx.RepoContext | None" = None,
) -> list[dict]:
    """Read all valid JSON object events while holding the ledger lock."""
    repo = repo or ctx.current_context()
    return _with_lock(lambda: _read_unlocked(path, tid=tid, repo=repo), path, repo=repo)


def ledger_append(event: dict, *, repo: "ctx.RepoContext | None" = None) -> dict:
    """Append one sanitized event while holding the ledger lock."""
    repo = repo or ctx.current_context()
    return _with_lock(lambda: _append_unlocked(event, repo=repo), repo=repo)


def ledger_locked_append(
    builder: Callable[[list[dict]], dict], *, repo: "ctx.RepoContext | None" = None,
) -> dict:
    """Build and append one event against a fresh locked ledger snapshot."""
    repo = repo or ctx.current_context()

    def update():
        events = _read_unlocked(repo=repo)
        return _append_unlocked(builder(events), repo=repo)

    return _with_lock(update, repo=repo)


def ledger_locked_append_many(
    builder: Callable[[list[dict]], list[dict]], *, repo: "ctx.RepoContext | None" = None,
) -> list[dict]:
    """Preflight and append a complete event batch under one ledger lock."""
    repo = repo or ctx.current_context()

    def update():
        events = _read_unlocked(repo=repo)
        return _append_many_unlocked(builder(events), repo=repo)

    return _with_lock(update, repo=repo)


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
    *, repo: "ctx.RepoContext | None" = None,
) -> dict:
    """Atomically allocate the next integer attempt and append its event."""
    repo = repo or ctx.current_context()

    def allocate():
        events = _read_unlocked(tid=tid, repo=repo)
        attempt = ledger_next_attempt(tid, events)
        event = builder(attempt, events)
        event["tid"] = tid
        event["attempt"] = attempt
        return _append_unlocked(event, repo=repo)

    return _with_lock(allocate, repo=repo)


def _ledger_append_safely(
    event: dict, *, repo: "ctx.RepoContext | None" = None,
) -> None:
    """Best-effort append for topology and integration events."""
    repo = repo or ctx.current_context()
    try:
        ledger_append(event, repo=repo)
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
