"""状态行 worktree 绑定核心实现（只读账本 + 显式会话绑定）。

绑定由核心命令在拿到 identity 后显式建立：`run-begin` 写入，`run-close` 解除。
本脚本不解析任何终端命令文本，也不依赖宿主私有字段；会话标识由调用方提供
（`--session`，通常即 `owner_id`）。

用法：
  python3 .repo_template/scripts/track_worktree.py bind --session <owner> --tid t113
  python3 .repo_template/scripts/track_worktree.py resolve --session <owner>

`resolve` 只在该会话绑定的 attempt 仍处于打开状态时输出 worktree 绝对路径，
否则输出空字符串；读取或账本异常时输出空并向 stderr 给出可定位诊断，
不伪装成功，也不影响业务事务。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

LOGGER = logging.getLogger(__name__)

STATUSLINE_FILE = os.path.join(".scratch", "statusline_workdir.jsonl")
# 终态事件关闭窗口；仅以该 tid 的「最新事件」作为门禁（见 resolve_worktree）。
TERMINAL_EVENTS = {"report", "integrated", "attempt_terminal"}
# 账本只做尾部读取：绑定去重与窗口判定都只需要尾部若干行。
_LEDGER_TAIL_BYTES = 8192


def _iter_lines_reverse(path: str, block_size: int = _LEDGER_TAIL_BYTES):
    """从文件末尾向前逐行产出非空行；文件缺失视为空账本，其它读失败给诊断。"""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            remainder = b""
            while position > 0:
                read_size = min(block_size, position)
                position -= read_size
                handle.seek(position)
                chunk = handle.read(read_size) + remainder
                lines = chunk.split(b"\n")
                remainder = lines[0]
                for line in reversed(lines[1:]):
                    cleaned = line.strip()
                    if cleaned:
                        yield cleaned.decode("utf-8", errors="replace")
            if remainder.strip():
                yield remainder.strip().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return
    except OSError as error:
        LOGGER.warning("track_worktree: 无法读取 %s：%s", path, error)
        return


def _iter_records_reverse(path: str):
    """逆序产出账本中的合法 JSON 对象；损坏行与非对象行跳过并给诊断。"""
    for line in _iter_lines_reverse(path):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            LOGGER.warning("track_worktree: 跳过损坏账本行 %s：%s", path, error)
            continue
        if not isinstance(record, dict):
            LOGGER.warning("track_worktree: 跳过非对象账本行 %s：%r", path, line[:120])
            continue
        yield record


def resolve_worktree(project: str, tid: str) -> str:
    """该 tid 当前打开的 worktree 绝对路径；已终结或不可判定时返回空。

    事件模型以该 tid 的「最新事件」为终态门禁：最新事件非终态即视为窗口打开，
    此时回溯最近的 start 事件取 worktree，忽略中间的历史终态事件。恢复重试只
    reserve 新 attempt、不追加 start，旧的 report/attempt_terminal 不应关闭窗口。
    """
    ledger = os.path.join(project, "docs", "runtime", "dispatch_ledger.jsonl")
    if not os.path.isfile(ledger):
        return ""
    latest_event = None
    worktree = ""
    for record in _iter_records_reverse(ledger):
        if record.get("tid") != tid:
            continue
        if latest_event is None:
            latest_event = record.get("event")
            if latest_event in TERMINAL_EVENTS:
                return ""
        if record.get("event") == "start":
            worktree = str(record.get("worktree") or "")
            break
    if not worktree:
        LOGGER.warning(
            "track_worktree: 无法判定 worktree（最新事件=%r，未找到 start）："
            "project=%s tid=%r", latest_event, project, tid,
        )
        return ""
    return os.path.abspath(os.path.join(project, worktree))


def _read_last_line(path: str) -> str:
    """只读账本尾部固定字节，返回最后一个非空行。"""
    for line in _iter_lines_reverse(path):
        return line
    return ""


def _identity(record: dict) -> dict:
    """绑定身份：排除生成字段 ts，使重复 bind/unbind 可去重。"""
    return {key: value for key, value in record.items() if key != "ts"}


def append_record(path: str, record: dict) -> None:
    """幂等追加一条绑定记录；失败只报诊断，不影响业务事务。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError as error:
        LOGGER.warning("track_worktree: 无法创建 %s：%s", os.path.dirname(path), error)
        return
    last = _read_last_line(path)
    if last:
        try:
            previous = json.loads(last)
        except json.JSONDecodeError:
            previous = None
        if isinstance(previous, dict) and _identity(previous) == _identity(record):
            return
    line = json.dumps(record, ensure_ascii=False)
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as error:
        LOGGER.warning("track_worktree: 无法写入 %s：%s", path, error)


def _statusline_path(project: str) -> str:
    return os.path.join(project, STATUSLINE_FILE)


def bind(project: str, session: str, tid: str, attempt: int | None = None,
         execution_id: str = "") -> bool:
    """显式建立 session → worktree 绑定；无打开窗口时返回 False 并给诊断。"""
    if not session or not tid:
        LOGGER.warning(
            "track_worktree: 绑定跳过（session/tid 为空）：project=%s session=%r tid=%r",
            project, session, tid,
        )
        return False
    worktree = resolve_worktree(project, tid)
    if not worktree:
        LOGGER.warning(
            "track_worktree: 绑定失败，%r 无打开的 worktree 窗口："
            "project=%s session=%r tid=%r；核对 dispatch_ledger 最新事件与 start 记录",
            tid, project, session, tid,
        )
        return False
    append_record(_statusline_path(project), {
        "session": session,
        "tid": tid,
        "attempt": attempt,
        "execution_id": execution_id,
        "worktree": worktree,
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    return True


def unbind(project: str, session: str, tid: str) -> None:
    if not session or not tid:
        return
    append_record(_statusline_path(project), {
        "session": session,
        "tid": tid,
        "attempt": None,
        "execution_id": "",
        "worktree": "",
        "ts": datetime.now(timezone.utc).isoformat(),
    })


def current_worktree(project: str, session: str) -> str:
    """会话当前 worktree；绑定已解除、窗口已终结或目录已消失时返回空。"""
    if not session:
        return ""
    path = _statusline_path(project)
    if not os.path.isfile(path):
        return ""
    latest = None
    for record in _iter_records_reverse(path):
        if record.get("session") == session:
            latest = record
            break
    if not latest or not latest.get("worktree"):
        return ""
    tid = str(latest.get("tid") or "")
    reopened = resolve_worktree(project, tid)
    if not reopened or not os.path.isdir(reopened):
        LOGGER.warning(
            "track_worktree: 会话绑定 worktree 已失效：project=%s session=%r "
            "tid=%r resolved=%r", project, session, tid, reopened,
        )
        return ""
    return reopened


def main() -> int:
    parser = argparse.ArgumentParser(description="状态行 worktree 绑定（显式会话身份）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    bind_parser = sub.add_parser("bind", help="会话绑定到 task 当前 worktree")
    bind_parser.add_argument("--session", required=True)
    bind_parser.add_argument("--tid", required=True)
    bind_parser.add_argument("--project", default=os.getcwd())
    resolve_parser = sub.add_parser("resolve", help="输出会话当前 worktree（无则空）")
    resolve_parser.add_argument("--session", required=True)
    resolve_parser.add_argument("--project", default=os.getcwd())
    args = parser.parse_args()

    if args.cmd == "bind":
        return 0 if bind(args.project, args.session, args.tid) else 1
    print(current_worktree(args.project, args.session))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
