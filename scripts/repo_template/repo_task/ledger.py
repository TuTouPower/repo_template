"""Canonical ledger implementation for the task toolchain."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import repo_task.context as ctx

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def _ledger_lock_fh(fh) -> None:
    """Take an exclusive cross-platform lock on the ledger lock file."""
    fh.seek(0)
    if os.name == "nt":
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
    else:
        fcntl.flock(fh, fcntl.LOCK_EX)

def _ledger_unlock_fh(fh) -> None:
    """释放排他锁。"""
    fh.seek(0)
    if os.name == "nt":
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fh, fcntl.LOCK_UN)

def ledger_append(event: dict) -> dict:
    """补 ts 后持排他锁 append 一行 JSON；字符串值内换行替换为空格。

    锁文件 = 账本旁 dispatch_ledger.lock（跟随 ctx.LEDGER_PATH 派生），
    防并发追加交错产生坏行；ledger_read 只读不加锁。
    """
    final = {
        key: (value.replace("\n", " ").replace("\r", " ") if isinstance(value, str) else value)
        for key, value in event.items()
    }
    final["ts"] = datetime.now().astimezone().isoformat(timespec="seconds")
    ctx.LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ctx.LEDGER_PATH.with_suffix(".lock")
    with lock_path.open("w", encoding="utf-8") as lock_fh:
        _ledger_lock_fh(lock_fh)
        try:
            with ctx.LEDGER_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(final, ensure_ascii=False) + "\n")
        finally:
            _ledger_unlock_fh(lock_fh)
    return final

def ledger_read() -> list[dict]:
    """读取账本全部事件；文件不存在返回 []；空行跳过。

    截断/损坏行（如追加写被打断）stderr 警告并跳过——自愈优先于中断，
    坏行不应让整个调度控制面失读。
    """
    if not ctx.LEDGER_PATH.is_file():
        return []
    events = []
    for line_no, line in enumerate(
        ctx.LEDGER_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            print(
                f"WARNING: 调度账本 {ctx._rel(ctx.LEDGER_PATH)} 第 {line_no} 行无法解析，已跳过",
                file=sys.stderr,
            )
    return events

def ledger_next_attempt(tid: str, events: list[dict]) -> int:
    """该 tid 账本中 max(attempt)+1；从未派发返回 1。"""
    attempts = [
        e["attempt"]
        for e in events
        if e.get("tid") == tid and isinstance(e.get("attempt"), int)
    ]
    return max(attempts, default=0) + 1


def dispatch_events(tid: str, events: list[dict]) -> list[dict]:
    """Return dispatch events for one tid, preserving ledger order."""
    return [
        event for event in events
        if event.get("event") == "dispatch" and event.get("tid") == tid
    ]


def current_attempt(tid: str, events: list[dict]) -> int | None:
    """Return the highest explicitly dispatched attempt for ``tid``."""
    attempts = [
        event.get("attempt") for event in dispatch_events(tid, events)
        if isinstance(event.get("attempt"), int)
    ]
    return max(attempts, default=None)


def dispatch_for_attempt(tid: str, attempt: int, events: list[dict]) -> dict | None:
    """Return the last dispatch record for one exact ``(tid, attempt)``."""
    matches = [
        event for event in dispatch_events(tid, events)
        if event.get("attempt") == attempt
    ]
    return matches[-1] if matches else None


def latest_worker_terminal(tid: str, attempt: int, events: list[dict]) -> dict | None:
    """Return the latest terminal event for one exact attempt."""
    matches = [
        event for event in events
        if event.get("event") == "worker_terminal"
        and event.get("tid") == tid
        and event.get("attempt") == attempt
    ]
    return matches[-1] if matches else None


def invalid_overlapping_attempts(tid: str, events: list[dict]) -> set[int]:
    """Return attempts involved in a dispatch issued before its predecessor terminal."""
    tev = [event for event in events if event.get("tid") == tid]
    dispatches = [
        (index, event) for index, event in enumerate(tev)
        if event.get("event") == "dispatch" and isinstance(event.get("attempt"), int)
    ]
    invalid: set[int] = set()
    for higher_index, higher in dispatches:
        for lower_index, lower in dispatches:
            if lower["attempt"] >= higher["attempt"] or lower_index >= higher_index:
                continue
            terminal_before = any(
                event.get("event") == "worker_terminal"
                and event.get("attempt") == lower["attempt"]
                for event in tev[lower_index + 1:higher_index]
            )
            if not terminal_before:
                invalid.update((lower["attempt"], higher["attempt"]))
    return invalid


def has_dispatch(tid: str, events: list[dict]) -> bool:
    """Whether the ledger contains any dispatch for ``tid``."""
    return bool(dispatch_events(tid, events))

def _ledger_append_safely(event: dict) -> None:
    """start/integrate 自动记账；失败只警告，不影响命令本身。

    账本路径不在本仓内时跳过（测试重定向 ctx.REPO_ROOT 而未重定向 ctx.LEDGER_PATH 的情形，
    避免向真实仓库的运行态目录写入）。
    """
    try:
        if not ctx.LEDGER_PATH.resolve().is_relative_to(ctx.REPO_ROOT.resolve()):
            return
        ledger_append(event)
    except OSError as error:
        print(
            f"WARNING: 调度账本写入失败（{error}）；{event.get('event')} 事件未记录",
            file=sys.stderr,
        )
