"""状态行 worktree 绑定核心实现（只读账本 + 显式会话绑定）。

绑定由核心命令在拿到 identity 后显式建立：`run-begin --session-id` 写入，`run-close --session-id` 解除。
本脚本不解析任何终端命令文本，也不依赖宿主私有字段；会话标识由调用方提供
（`--session` 为宿主 session ID，`--owner-id` 为执行权身份）。

用法：
  python3 .repo_template/scripts/track_worktree.py bind --session <host-session> --owner-id <owner> --tid t113
  python3 .repo_template/scripts/track_worktree.py render --session <host-session>

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
from pathlib import Path

import repo_task.context as ctx

LOGGER = logging.getLogger(__name__)

STATUSLINE_FILE = "statusline_workdir.jsonl"
# 终态事件关闭窗口；仅以该 tid 的「最新事件」作为门禁（见 resolve_worktree）。
TERMINAL_EVENTS = {"report", "integrated", "attempt_terminal"}
# 账本只做尾部读取：绑定去重与窗口判定都只需要尾部若干行。
_LEDGER_TAIL_BYTES = 8192
# 回扫双上限：单次解析最多消费的事件行数与字节数，超限即停并给诊断，
# 防止超大账本导致单次状态行查询退化为全文件扫描。
_MAX_SCAN_LINES = 20000
_MAX_SCAN_BYTES = 8 * 1024 * 1024


def _iter_lines_reverse(
    path: str,
    block_size: int = _LEDGER_TAIL_BYTES,
    *,
    max_lines: int = _MAX_SCAN_LINES,
    max_bytes: int = _MAX_SCAN_BYTES,
):
    """从文件末尾向前逐行产出非空行；文件缺失视为空账本，其它读失败给诊断。

    回扫受事件行数与总字节双上限约束：任一超限即停并记 WARNING，避免无界
    回扫把超大账本 whole-file 读入。正常体量下限内即命中，无行为变化。
    """
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            remainder = b""
            yielded = 0
            read_total = 0
            while position > 0:
                if read_total >= max_bytes:
                    LOGGER.warning(
                        "track_worktree: 回扫超字节上限（%d 字节），停止：%s",
                        max_bytes, path,
                    )
                    return
                read_size = min(block_size, position)
                position -= read_size
                handle.seek(position)
                chunk = handle.read(read_size) + remainder
                read_total += read_size
                lines = chunk.split(b"\n")
                remainder = lines[0]
                for line in reversed(lines[1:]):
                    cleaned = line.strip()
                    if cleaned:
                        yielded += 1
                        if yielded > max_lines:
                            LOGGER.warning(
                                "track_worktree: 回扫超事件上限（%d 行），停止：%s",
                                max_lines, path,
                            )
                            return
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


def _expected_worktree_rel(project: str, tid: str) -> str:
    """该 project 下 tid 的唯一合法 worktree 相对路径（派生，不信任账本值）。"""
    return "../" + os.path.basename(os.path.abspath(project)) + "_" + tid


def resolve_worktree(project: str, tid: str) -> str:
    """该 tid 当前打开的 worktree 绝对路径；已终结或不可判定时返回空。

    事件模型以该 tid 的「最新事件」为终态门禁：最新事件非终态即视为窗口打开，
    此时回溯最近的 start 事件取 worktree，忽略中间的历史终态事件。恢复重试只
    reserve 新 attempt、不追加 start，旧的 report/attempt_terminal 不应关闭窗口。

    账本 worktree 值不受信：必须恰等于派生路径（``../{repo}_{tid}``），否则
    视为伪造返回空；防止伪造 worktree:"/" 等把状态行指向任意目录。
    """
    ledger = str(ctx.RepoContext.for_root(Path(project)).ledger_path)
    if not os.path.isfile(ledger):
        return ""
    expected_rel = _expected_worktree_rel(project, tid)
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
    if worktree != expected_rel:
        LOGGER.warning(
            "track_worktree: 账本 worktree 值不受信（%r，期望 %r）："
            "project=%s tid=%r", worktree, expected_rel, project, tid,
        )
        return ""
    return os.path.abspath(os.path.join(project, worktree))


def _current_open_identity(project: str, tid: str):
    """账本上该 tid 当前打开的 (attempt, execution_id, owner_id)；否则返回 None。

    最新事件为终态、或找不到合法 attempt_reserved 时返回 None。owner 取该
    identity 最新 execution_claimed 的 owner_id，无 claim 时为空字符串。
    """
    ledger = str(ctx.RepoContext.for_root(Path(project)).ledger_path)
    if not os.path.isfile(ledger):
        return None
    latest_event = None
    claims: dict[tuple, str] = {}
    identity = None
    for record in _iter_records_reverse(ledger):
        if record.get("tid") != tid:
            continue
        if latest_event is None:
            latest_event = record.get("event")
            if latest_event in TERMINAL_EVENTS:
                return None
        event = record.get("event")
        if event == "execution_claimed":
            attempt, execution_id = record.get("attempt"), record.get("execution_id")
            key = (attempt, execution_id)
            if key not in claims and isinstance(record.get("owner_id"), str):
                claims[key] = record.get("owner_id")
        elif event == "attempt_reserved" and identity is None:
            attempt, execution_id = record.get("attempt"), record.get("execution_id")
            if (
                isinstance(attempt, int) and not isinstance(attempt, bool)
                and attempt > 0
                and isinstance(execution_id, str) and execution_id
            ):
                identity = (attempt, execution_id)
                break
    if identity is None:
        return None
    return (identity[0], identity[1], claims.get(identity, ""))


def _read_last_line(path: str) -> str:
    """只读账本尾部固定字节，返回最后一个非空行。"""
    for line in _iter_lines_reverse(path):
        return line
    return ""


def _identity(record: dict) -> dict:
    """绑定身份：排除生成字段 ts，使重复 bind/unbind 可去重。"""
    return {key: value for key, value in record.items() if key != "ts"}


def append_record(path: str, record: dict) -> bool:
    """幂等追加一条绑定记录；成功（含去重命中）返回 True，失败返回 False。

    中断残留的半行（缺尾换行）先补换行再追加，避免下次追加与残留拼成非法行。
    失败只报诊断，由调用方决定是否阻断（本层不再吞掉持久化结果）。
    """
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    except OSError as error:
        LOGGER.warning("track_worktree: 无法创建 %s：%s", os.path.dirname(path), error)
        return False
    last = _read_last_line(path)
    if last:
        try:
            previous = json.loads(last)
        except json.JSONDecodeError:
            previous = None
        if isinstance(previous, dict) and _identity(previous) == _identity(record):
            return True
    line = json.dumps(record, ensure_ascii=False)
    try:
        with open(path, "a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() > 0:
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    handle.write(b"\n")
            handle.write((line + "\n").encode("utf-8"))
            handle.flush()
    except OSError as error:
        LOGGER.warning("track_worktree: 无法写入 %s：%s", path, error)
        return False
    return True


def _statusline_path(project: str) -> str:
    return str(ctx.RepoContext.for_root(Path(project)).statusline_path)


def bind(project: str, session: str, tid: str, attempt: int | None = None,
         execution_id: str = "", *, owner_id: str) -> bool:
    """显式建立 session → worktree 绑定；无打开窗口或持久化失败返回 False。"""
    if not session or not tid or not owner_id:
        LOGGER.warning(
            "track_worktree: 绑定跳过（session/tid 为空）：project=%s session=%r tid=%r",
            project, session, tid,
        )
        return False
    worktree = resolve_worktree(project, tid)
    if not worktree:
        LOGGER.warning(
            "track_worktree: 绑定失败，%r 无打开的 worktree 窗口："
            "project=%s session=%r tid=%r；核对 ledger 最新事件与 start 记录",
            tid, project, session, tid,
        )
        return False
    if not append_record(_statusline_path(project), {
        "session": session,
        "owner_id": owner_id,
        "tid": tid,
        "attempt": attempt,
        "execution_id": execution_id,
        "worktree": worktree,
        "ts": datetime.now(timezone.utc).isoformat(),
    }):
        LOGGER.warning(
            "track_worktree: 绑定持久化失败：project=%s session=%r tid=%r",
            project, session, tid,
        )
        return False
    return True


def unbind(project: str, session: str, tid: str) -> bool:
    """显式解除绑定；持久化失败返回 False。

    配对语义由 current_worktree 落实：unbind 只标记同 session+同 tid 的绑定，
    跨 tid 的 unbind 不影响其他 tid 的打开绑定（见 current_worktree）。
    """
    if not session or not tid:
        return False
    return append_record(_statusline_path(project), {
        "session": session,
        "tid": tid,
        "attempt": None,
        "execution_id": "",
        "worktree": "",
        "ts": datetime.now(timezone.utc).isoformat(),
    })


def current_worktree(project: str, session: str) -> str:
    """会话当前 worktree；绑定已解除、归属不符、窗口已终结或目录已消失时返回空。

    配对语义：逆序回放该 session 记录，同 tid 的 unbind 只清除同 tid 的绑定，
    跨 tid unbind 不影响其他 tid 的打开绑定。携带 attempt/execution_id 的绑定
    必须与账本当前非终态 identity（含 owner）一致，否则视为归属不符返回空。
    """
    if not session:
        return ""
    path = _statusline_path(project)
    if not os.path.isfile(path):
        return ""
    cleared: set[str] = set()
    candidate = None
    for record in _iter_records_reverse(path):
        if record.get("session") != session:
            continue
        if not record.get("worktree"):
            record_tid = record.get("tid")
            if isinstance(record_tid, str) and record_tid:
                cleared.add(record_tid)
            continue
        record_tid = str(record.get("tid") or "")
        if record_tid in cleared:
            continue
        candidate = record
        break
    if candidate is None or not candidate.get("worktree"):
        return ""
    tid = str(candidate.get("tid") or "")
    if candidate.get("attempt") is not None:
        identity = _current_open_identity(project, tid)
        if identity is not None:
            if (candidate.get("attempt"), candidate.get("execution_id")) != (
                identity[0], identity[1],
            ):
                LOGGER.warning(
                    "track_worktree: 会话绑定归属不符：project=%s session=%r "
                    "tid=%r 绑定=(%r, %r) 账本当前=(%r, %r)",
                    project, session, tid, candidate.get("attempt"),
                    candidate.get("execution_id"), identity[0], identity[1],
                )
                return ""
            if not candidate.get("owner_id") or identity[2] != candidate["owner_id"]:
                LOGGER.warning(
                    "track_worktree: 会话绑定 owner 不符：project=%s session=%r "
                    "tid=%r 账本 owner=%r", project, session, tid, identity[2],
                )
                return ""
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
    bind_parser.add_argument("--owner-id", required=True)
    bind_parser.add_argument("--project", default=os.getcwd())
    bind_parser.add_argument("--attempt", type=int, default=None)
    bind_parser.add_argument("--execution-id", default="")
    resolve_parser = sub.add_parser("resolve", help="输出会话当前 worktree（无则空）")
    resolve_parser.add_argument("--session", required=True)
    resolve_parser.add_argument("--project", default=os.getcwd())
    render_parser = sub.add_parser("render", help="渲染会话当前 worktree 名；未绑定时显示项目名")
    render_parser.add_argument("--session", required=True, help="宿主真实 session ID")
    render_parser.add_argument("--project", default=os.getcwd())
    args = parser.parse_args()

    if args.cmd == "bind":
        return 0 if bind(
            args.project, args.session, args.tid, args.attempt, args.execution_id, owner_id=args.owner_id,
        ) else 1
    worktree = current_worktree(args.project, args.session)
    if args.cmd == "render":
        label = os.path.basename(worktree or os.path.abspath(args.project))
        print("".join(char for char in label if char.isprintable()))
    else:
        print(worktree)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
