"""Canonical monitoring implementation for the task toolchain."""

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import repo_task.context as ctx

from .git_ops import _git, _git_bytes, has_unmerged_commits, worktree_paths
from .ledger import (
    current_attempt,
    dispatch_events,
    dispatch_for_attempt,
    invalid_overlapping_attempts,
    latest_worker_terminal as ledger_latest_worker_terminal,
    ledger_next_attempt,
)
from .store import _task_branch_names, git_text_at_ref, load_task_at_ref

def _ledger_tid_sort_key(tid: str):
    """账本 tid 排序：规范 tid 按序号，其余按字符串排最后。"""
    match = ctx.TID_RE.fullmatch(tid or "")
    return (0, int(match.group(1)), "") if match else (1, 0, tid or "")

def _parse_instant(text: str) -> datetime | None:
    if not text:
        return None
    try:
        instant = datetime.fromisoformat(text)
    except ValueError:
        return None
    if instant.tzinfo is None:
        return instant.astimezone()  # naive 时间戳按本地时区解释，避免与 aware 比较 TypeError
    return instant

def is_silent(last_change: str, silent_minutes: int, now: datetime) -> bool:
    """Return whether an observation fingerprint has stayed unchanged past the threshold."""
    instant = _parse_instant(last_change)
    if instant is None:
        return False
    return now - instant > timedelta(minutes=silent_minutes)


def _hash_part(digest, label: bytes, value: bytes) -> None:
    digest.update(len(label).to_bytes(4, "big"))
    digest.update(label)
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def _required_git_bytes(args: list[str], root: Path) -> bytes:
    result = _git_bytes(args, root=root)
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise ctx.TaskDataError(f"无法计算仓库状态指纹：git {' '.join(args)}：{error}")
    return result.stdout


def repository_fingerprint(root: Path) -> dict:
    """Hash HEAD, binary diffs, and sorted non-ignored untracked entries.

    File mtimes never participate. Regular files are hashed as bytes; symlinks hash only
    their link target and are never followed outside the worktree.
    """
    root = root.resolve()
    head = _required_git_bytes(["rev-parse", "HEAD"], root).strip()
    staged = _required_git_bytes(
        ["diff", "--binary", "--cached", "--no-ext-diff", "--full-index", "--"], root
    )
    unstaged = _required_git_bytes(
        ["diff", "--binary", "--no-ext-diff", "--full-index", "--"], root
    )
    untracked_raw = _required_git_bytes(
        ["ls-files", "--others", "--exclude-standard", "-z"], root
    )
    untracked = sorted(path for path in untracked_raw.split(b"\0") if path)

    digest = hashlib.sha256()
    _hash_part(digest, b"head", head)
    _hash_part(digest, b"staged", staged)
    _hash_part(digest, b"unstaged", unstaged)
    for raw_path in untracked:
        path = root / os.fsdecode(raw_path)
        try:
            info = path.lstat()
        except FileNotFoundError:
            raise ctx.TaskDataError(f"计算仓库状态指纹时文件消失：{os.fsdecode(raw_path)}")
        file_type = stat.S_IFMT(info.st_mode)
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISLNK(info.st_mode):
            kind = b"symlink"
            content = os.fsencode(os.readlink(path))
        elif stat.S_ISREG(info.st_mode):
            kind = b"file"
            content = path.read_bytes()
        else:
            kind = f"special:{file_type:o}".encode("ascii")
            content = b""
        _hash_part(digest, b"path", raw_path)
        _hash_part(digest, b"kind", kind)
        _hash_part(digest, b"mode", f"{mode:o}".encode("ascii"))
        _hash_part(digest, b"content", content)

    return {
        "fingerprint": digest.hexdigest(),
        "head": head.decode("ascii", errors="replace"),
        "untracked_count": len(untracked),
    }


def worktree_dirty_summary(root: Path) -> str:
    result = _git_bytes(["status", "--porcelain=v1", "-z"], root=root)
    if result.returncode != 0:
        return "status-unavailable"
    staged = unstaged = untracked = 0
    fields = result.stdout.split(b"\0")
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if not entry:
            continue
        code = entry[:2]
        if code == b"??":
            untracked += 1
        else:
            if code[:1] not in (b" ", b"?"):
                staged += 1
            if code[1:2] not in (b" ", b"?"):
                unstaged += 1
        if code[:1] in (b"R", b"C"):
            index += 1
    if not any((staged, unstaged, untracked)):
        return "clean"
    return f"staged={staged} unstaged={unstaged} untracked={untracked}"


def latest_observation(events_for_tid: list[dict], attempt: int) -> dict | None:
    observations = [
        event for event in events_for_tid
        if event.get("event") == "observation" and event.get("attempt") == attempt
    ]
    return observations[-1] if observations else None


def latest_worker_terminal(events_for_tid: list[dict], attempt: int) -> dict | None:
    """Return the latest terminal event for one exact attempt."""
    return ledger_latest_worker_terminal(
        next((event.get("tid") for event in events_for_tid if event.get("tid")), ""),
        attempt,
        events_for_tid,
    )


def _call_verifier(verifier, tid: str, attempt: int | None) -> tuple[str, str]:
    """Call old one-argument test verifiers while passing attempt to real verifiers."""
    try:
        return verifier(tid, attempt)
    except TypeError as error:
        try:
            return verifier(tid)
        except TypeError:
            raise error


def _parallel_dispatch(dispatch: dict) -> bool:
    """A worker-id dispatch is the coordinator/worker path requiring terminal proof."""
    return bool(dispatch.get("worker_id"))


def _attempt_worktree(tid: str, attempt: int, events: list[dict]) -> tuple[dict, Path, str]:
    dispatch = next((
        event for event in events
        if event.get("event") == "dispatch"
        and event.get("tid") == tid
        and event.get("attempt") == attempt
    ), None)
    if dispatch is None:
        raise ctx.TaskDataError(f"{tid} attempt={attempt} 未 dispatch")
    if not any(item_tid == tid and item_attempt == attempt for item_tid, item_attempt, _ in _in_flight_attempts(events)):
        raise ctx.TaskDataError(f"{tid} attempt={attempt} 不是当前在飞 attempt")
    starts = [
        event for event in events
        if event.get("event") == "start" and event.get("tid") == tid and event.get("worktree")
    ]
    start = starts[-1] if starts else {}
    worktree_rel = start.get("worktree") or ctx.worktree_rel_path(tid)
    worktree = (ctx.REPO_ROOT / worktree_rel).resolve()
    branch = worktree_paths().get(str(worktree))
    if not worktree.is_dir() or not branch:
        raise ctx.TaskDataError(f"{tid} attempt={attempt} worktree 不存在或未登记：{worktree_rel}")
    match = ctx.TASK_BRANCH_RE.fullmatch(branch)
    if not match or match.group(1) != tid:
        raise ctx.TaskDataError(
            f"{tid} attempt={attempt} worktree 归属不符：登记分支 {branch!r}"
        )
    if start.get("branch") and start["branch"] != branch:
        raise ctx.TaskDataError(
            f"{tid} attempt={attempt} worktree 归属不符：start={start['branch']!r}，当前={branch!r}"
        )
    current = _git(["rev-parse", "--abbrev-ref", "HEAD"], root=worktree)
    if current.returncode != 0 or current.stdout.strip() != branch:
        raise ctx.TaskDataError(f"{tid} attempt={attempt} worktree 当前分支与登记不符")
    return dispatch, worktree, worktree_rel


def observe_attempt(tid: str, attempt: int, events: list[dict], *, now: datetime | None = None) -> dict:
    """Observe one running attempt, appending only the first or a changed fingerprint."""
    from .ledger import ledger_append

    dispatch, worktree, worktree_rel = _attempt_worktree(tid, attempt, events)
    snapshot = repository_fingerprint(worktree)
    prior = latest_observation([event for event in events if event.get("tid") == tid], attempt)
    changed = prior is None or prior.get("fingerprint") != snapshot["fingerprint"]
    if changed:
        observation = ledger_append({
            "event": "observation",
            "tid": tid,
            "attempt": attempt,
            "fingerprint": snapshot["fingerprint"],
            "head": snapshot["head"],
            "worktree": worktree_rel,
            "dirty": worktree_dirty_summary(worktree),
        })
    else:
        observation = prior
    current_time = now or datetime.now().astimezone()
    changed_at = _parse_instant(observation.get("ts", ""))
    silence = max(0, int((current_time - changed_at).total_seconds() // 60)) if changed_at else 0
    return {
        "tid": tid,
        "attempt": attempt,
        "worker_id": dispatch.get("worker_id", ""),
        "fingerprint": snapshot["fingerprint"],
        "changed": changed,
        "last_change": observation.get("ts", ""),
        "silent_minutes": silence,
        "head": snapshot["head"],
        "worktree": worktree_rel,
        "dirty": observation.get("dirty", worktree_dirty_summary(worktree)),
    }

def verify_integrate_ready(tid: str, attempt: int | None = None) -> tuple[str, str]:
    """refs 机器验证，返回 (verdict, detail)。并行 attempt 会严格校验 handoff 归属。"""
    branches = _task_branch_names(tid)
    if not branches:
        return "incomplete", "无本地 task 分支"
    if len(branches) > 1:
        # integrate 会拒绝多分支，判 contract 走重试预算后自然 escalate
        return "contract", f"存在多个分支：{', '.join(branches)}"
    branch = branches[0]
    try:
        task, fm, _ = load_task_at_ref(tid, branch)
    except ctx.TaskDataError as error:
        return "incomplete", str(error)
    if fm.get("status") not in ctx.ARCHIVED_STATUSES:
        return "incomplete", f"分支 {branch!r} tip status={fm.get('status')!r} 非终态"
    try:
        handoff_text = git_text_at_ref(branch, f"{task['dir']}/handoff.json")
    except ctx.TaskDataError:
        return "contract", f"分支 {branch!r} tip 缺 {task['dir']}/handoff.json"
    try:
        handoff = json.loads(handoff_text)
    except json.JSONDecodeError:
        return "contract", f"分支 {branch!r} tip {task['dir']}/handoff.json 无法解析"
    handoff_path = f"{task['dir']}/handoff.json"
    if not isinstance(handoff, dict):
        return "contract", f"{handoff_path} 非 JSON 对象"
    if handoff.get("status") not in ctx.ARCHIVED_STATUSES:
        return "contract", f"{handoff_path} status={handoff.get('status')!r} 缺失或非终态"
    if handoff.get("tid") != tid:
        return "contract", f"{handoff_path} tid={handoff.get('tid')!r} 与 {tid} 不符"
    if handoff.get("branch") != branch:
        return "contract", f"{handoff_path} branch={handoff.get('branch')!r} 与分支 {branch!r} 不符"
    if attempt is not None and handoff.get("attempt") != attempt:
        return (
            "contract",
            f"{handoff_path} attempt={handoff.get('attempt')!r} 与当前 attempt={attempt} 不符",
        )
    if not has_unmerged_commits(branch):
        return "ready", "分支 tip done + handoff 齐备（已合入）"
    return "ready", "分支 tip done + handoff 齐备"

def compute_ps_rows(
    events: list[dict],
    effective: dict[str, dict],
    main_statuses: dict[str, str],
    *,
    silent_minutes: int,
    now: datetime,
    observer=latest_observation,
    verifier=verify_integrate_ready,
) -> list[dict]:
    """Build the dispatch process table from ledger observations and refs."""
    ledger_tids = {event["tid"] for event in events if event.get("tid")}
    active_tids = {
        tid for tid, task in effective.items()
        if task["status"] in ("active", "blocked", "backlog")
    }
    rows = []
    for tid in sorted(ledger_tids | active_tids, key=_ledger_tid_sort_key):
        tev = [event for event in events if event.get("tid") == tid]
        dispatches = dispatch_events(tid, events)
        attempt = current_attempt(tid, events)
        dispatch = dispatch_for_attempt(tid, attempt, events) if attempt is not None else {}
        model = dispatch.get("model", "")
        worker_id = dispatch.get("worker_id", "")
        last_activity = "-"
        note = ""
        terminal = latest_worker_terminal(tev, attempt) if attempt is not None else None
        terminal_status = terminal.get("status") if terminal else None
        invalid_overlap = (
            _parallel_dispatch(dispatch)
            and attempt in invalid_overlapping_attempts(tid, events)
            if attempt is not None else False
        )
        if main_statuses.get(tid) in ctx.ARCHIVED_STATUSES:
            rows.append({
                "tid": tid, "attempt": attempt, "model": model, "worker_id": worker_id,
                "state": main_statuses[tid], "last_activity": last_activity, "note": note,
            })
            continue
        reports = [
            event for event in tev
            if event.get("event") == "report" and event.get("attempt") == attempt
        ]
        latest_report = reports[-1] if reports else None
        disposition = None
        for event in tev:
            if event.get("attempt") == attempt and event.get("event") in ("failed", "report"):
                disposition = event
        integrated = any(
            event.get("event") == "integrated"
            and (event.get("attempt") == attempt or (
                not _parallel_dispatch(dispatch) and event.get("attempt") is None
            ))
            for event in tev
        )
        effective_status = effective.get(tid, {}).get("status", "")
        verdict, detail = (
            _call_verifier(verifier, tid, attempt) if dispatches
            else ("incomplete", "")
        )
        terminal_pending = _parallel_dispatch(dispatch) and (
            terminal is None or invalid_overlap
        )
        if terminal_pending:
            if invalid_overlap:
                state = "contract待worker终止"
                note = f"非法重叠：attempt={attempt} 之前的 attempt 未先 terminal"
            elif verdict == "ready":
                state = "ready待worker终止"
                note = detail
            elif disposition and (
                disposition.get("event") == "failed" or disposition.get("status") == "failed"
            ):
                state = "failed待worker终止"
                note = disposition.get("reason", "")
            elif (
                latest_report and latest_report.get("status") == "blocked"
            ) or effective_status == "blocked":
                state = "blocked待worker终止"
                note = (latest_report or {}).get("reason", "")
            elif latest_report and latest_report.get("status") == "done" and not integrated:
                state = "reported待worker终止"
                note = latest_report.get("sha", "")
            else:
                observation = observer(tev, attempt)
                if observation is None:
                    state = "dispatched(未观察)"
                else:
                    last_activity = observation.get("ts", "-")
                    state = (
                        "silent?"
                        if is_silent(last_activity, silent_minutes, now)
                        else "progressing"
                    )
                    note = observation.get("dirty", "")
        elif verdict == "ready":
            state = "done待合并"
            note = detail
        elif disposition and (
            disposition.get("event") == "failed" or disposition.get("status") == "failed"
        ):
            state = f"failed:{disposition.get('class', '')}"
            note = disposition.get("reason", "")
        elif (
            latest_report and latest_report.get("status") == "blocked"
        ) or effective_status == "blocked":
            state = "blocked"
            note = (latest_report or {}).get("reason", "")
        elif latest_report and latest_report.get("status") == "done" and not integrated:
            state = "reported(未验证)"
            note = latest_report.get("sha", "")
        elif dispatches:
            observation = observer(tev, attempt)
            if observation is None:
                state = "dispatched(未观察)"
            else:
                last_activity = observation.get("ts", "-")
                state = (
                    "silent?"
                    if is_silent(last_activity, silent_minutes, now)
                    else "progressing"
                )
                note = observation.get("dirty", "")
        elif effective_status == "backlog":
            state = "pending"
        else:
            state = effective_status or "?"
        if terminal_status:
            note = f"{note}；" if note else ""
            note += f"worker_terminal={terminal_status}"
        elif terminal_pending:
            note = f"{note}；" if note else ""
            note += "worker_terminal=pending"
        rows.append({
            "tid": tid, "attempt": attempt, "model": model, "worker_id": worker_id,
            "state": state, "last_activity": last_activity, "note": note,
        })
    return rows

def _in_flight_attempts(events: list[dict]) -> list[tuple[str, int, dict]]:
    """Return attempts still requiring coordinator action, preserving invalid overlaps."""
    tids = sorted(
        {e["tid"] for e in events if e.get("tid")}, key=_ledger_tid_sort_key
    )
    in_flight = []
    for tid in tids:
        tev = [e for e in events if e.get("tid") == tid]
        latest_dispatch: dict[int, tuple[int, dict]] = {}
        for i, event in enumerate(tev):
            if event.get("event") == "dispatch" and isinstance(event.get("attempt"), int):
                latest_dispatch[event["attempt"]] = (i, event)
        for attempt, (index, dispatch) in sorted(latest_dispatch.items()):
            parallel = _parallel_dispatch(dispatch)
            ended = False
            terminal_seen = False
            for later in tev[index + 1:]:
                if (
                    later.get("event") == "worker_terminal"
                    and later.get("attempt") == attempt
                ):
                    terminal_seen = True
                    continue
                if later.get("event") in ("integrated", "escalated"):
                    exact_match = later.get("attempt") == attempt or (
                        not parallel and later.get("attempt") is None
                    )
                    if exact_match and (not parallel or terminal_seen):
                        ended = True
                        break
                if (
                    later.get("event") == "dispatch"
                    and isinstance(later.get("attempt"), int)
                    and later["attempt"] > attempt
                    and (not parallel or terminal_seen)
                ):
                    ended = True
                    break
            if not ended:
                in_flight.append((tid, attempt, dispatch))
    return in_flight

def breaker_states(events: list[dict]) -> dict[str, str]:
    """session 级模型熔断状态：每模型取最新 breaker 事件，state=open 即熔断中。"""
    states: dict[str, str] = {}
    for e in events:
        if e.get("event") == "breaker" and e.get("model"):
            states[e["model"]] = e.get("state", "open")
    return states

def _pick_unbroken_model(
    ladder: list[str], start: int, breakers: dict[str, str]
) -> tuple[str | None, str]:
    """从阶梯第 start 档起选第一个未熔断模型；返回 (model, 降档说明)，全熔断返回 (None, "")。"""
    skipped = []
    for i in range(start, len(ladder)):
        model = ladder[i]
        if breakers.get(model) == "open":
            skipped.append(model)
            continue
        note = f"{'、'.join(skipped)} 熔断，降 {model}" if skipped else ""
        return model, note
    return None, ""

def dispatch_mode(tid: str, events_for_tid: list[dict]) -> str:
    """redispatch 模式：worktree 存在或分支有未合并 commit（有产出可续）→ resume；
    无分支无 worktree → restart（可直接 start）。"""
    start_events = [
        e for e in events_for_tid
        if e.get("event") == "start" and e.get("worktree")
    ]
    worktree_rel = start_events[-1]["worktree"] if start_events else ctx.worktree_rel_path(tid)
    if (ctx.REPO_ROOT / worktree_rel).resolve().is_dir():
        return "resume"
    if any(has_unmerged_commits(b) for b in _task_branch_names(tid)):
        return "resume"
    return "restart"

def _parent_dispatch_model(
    tid: str, parent_attempt: int, events: list[dict]
) -> str | None:
    for e in reversed(events):
        if (
            e.get("tid") == tid
            and e.get("event") == "dispatch"
            and e.get("attempt") == parent_attempt
        ):
            return e.get("model")
    return None

def _retry_or_escalate_action(
    tid: str,
    parent_attempt: int,
    *,
    fail_class: str,
    detail: str,
    events: list[dict],
    ladder: list[str] | None,
    breakers: dict[str, str],
    max_auto_retries: int,
    mode_probe=dispatch_mode,
) -> dict:
    """失败重试策略：已用 attempt 数未超额度则 redispatch，否则 escalate。

    - contract：不走路梯降档，同模型 resume（缺交接单换模型无效）；
      无现场（worktree/分支均无产出）时 escalate；
    - 其余类：模型按阶梯降档并跳过熔断档；阶梯钳回父 attempt 同模型时
      仅 infra escalate（已无未尝试模型），resource/task 允许同模型 resume；
    - mode 由 mode_probe 判定：resume（有产出续跑）/ restart（无产出从头）。
    """
    used = ledger_next_attempt(tid, events) - 1
    reason = f"#{parent_attempt} {fail_class} 失败：{detail}"
    if used > max_auto_retries:
        return {
            "action": "escalate", "tid": tid, "attempt": parent_attempt,
            "model": None,
            "reason": f"{reason}；自动重试额度（{max_auto_retries}）用尽",
        }
    tev = [e for e in events if e.get("tid") == tid]
    mode = mode_probe(tid, tev)
    parent_model = _parent_dispatch_model(tid, parent_attempt, events)
    if fail_class == "contract":
        if mode == "restart":
            return {
                "action": "escalate", "tid": tid, "attempt": parent_attempt,
                "model": None,
                "reason": f"{reason}；无现场可续：worktree/分支缺失",
            }
        return {
            "action": "redispatch", "tid": tid, "attempt": used + 1,
            "model": parent_model, "mode": "resume",
            "reason": f"{reason}（同模型 resume：原 worktree 补交接单）",
        }
    if ladder:
        model, note = _pick_unbroken_model(
            ladder, min(used, len(ladder) - 1), breakers
        )
        if model is None:
            return {
                "action": "escalate", "tid": tid, "attempt": parent_attempt,
                "model": None, "reason": f"{reason}；模型阶梯全部熔断",
            }
        if model == parent_model and fail_class == "infra":
            return {
                "action": "escalate", "tid": tid, "attempt": parent_attempt,
                "model": None,
                "reason": f"{reason}；阶梯内已无未尝试模型（{model}）",
            }
        if note:
            reason = f"{reason}（{note}）"
    else:
        model = parent_model
        if model and breakers.get(model) == "open":
            return {
                "action": "escalate", "tid": tid, "attempt": parent_attempt,
                "model": None,
                "reason": f"{reason}；模型阶梯全部熔断（{model} 熔断，无阶梯可降）",
            }
    return {
        "action": "redispatch", "tid": tid, "attempt": used + 1,
        "model": model, "mode": mode, "reason": reason,
    }

def _escalate_latched_attempts(events: list[dict]) -> set[tuple[str, int]]:
    """Return exact attempts whose latest disposition is terminal escalation."""
    latched: set[tuple[str, int]] = set()
    for tid in {e.get("tid") for e in events if e.get("tid")}:
        attempts = sorted({
            event.get("attempt") for event in events
            if event.get("tid") == tid and isinstance(event.get("attempt"), int)
        })
        for attempt in attempts:
            relevant = [
                event for event in events
                if event.get("tid") == tid
                and event.get("attempt") == attempt
                and event.get("event") in ("dispatch", "escalated")
            ]
            if relevant and relevant[-1].get("event") == "escalated":
                latched.add((tid, attempt))
    return latched

def compute_reconcile_plan(
    events: list[dict],
    schedule: dict,
    *,
    limit: int,
    scope: set[str] | None,
    ladder: list[str] | None,
    silent_minutes: int,
    max_auto_retries: int,
    now: datetime,
    observer=latest_observation,
    verifier=verify_integrate_ready,
    mode_probe=dispatch_mode,
) -> dict:
    """Compute actions while requiring worker terminal proof for parallel attempts."""
    main_done_set = schedule.get("main_done_set", set())
    dropped_set = schedule.get("dropped_set", set())
    effective_tasks = schedule.get("tasks", {})
    breakers = breaker_states(events)

    def allowed(tid: str) -> bool:
        return scope is None or tid in scope

    actions: list[dict] = []
    occupancy = 0
    silent_hold = False
    in_flight = _in_flight_attempts(events)
    in_flight_tids = {
        tid for tid, _, _ in in_flight
        if tid not in main_done_set and tid not in dropped_set
    }
    for tid, attempt, dispatch in in_flight:
        if tid in main_done_set or tid in dropped_set:
            continue
        tev = [event for event in events if event.get("tid") == tid]
        reports = [
            event for event in tev
            if event.get("event") == "report" and event.get("attempt") == attempt
        ]
        report = reports[-1] if reports else None
        disposition = None
        for event in tev:
            if event.get("attempt") == attempt and event.get("event") in ("failed", "report"):
                disposition = event

        verdict, detail = _call_verifier(verifier, tid, attempt)
        terminal = latest_worker_terminal(tev, attempt)
        parallel = _parallel_dispatch(dispatch)
        invalid_overlap = parallel and attempt in invalid_overlapping_attempts(tid, events)
        terminal_pending = parallel and (terminal is None or invalid_overlap)

        if terminal_pending:
            if invalid_overlap or verdict in ("ready", "contract") or (
                disposition and (
                    disposition.get("event") == "failed"
                    or disposition.get("status") == "failed"
                )
            ):
                if allowed(tid):
                    reason = (
                        f"非法重叠：attempt={attempt} 之前的 attempt 未先 terminal"
                        if invalid_overlap
                        else f"{verdict}：{detail}"
                    )
                    actions.append({
                        "action": "await-worker-terminal", "tid": tid,
                        "attempt": attempt, "model": None, "reason": reason,
                    })
                occupancy += 1
                continue
            if (
                disposition
                and disposition.get("event") == "report"
                and disposition.get("status") == "blocked"
            ) or effective_tasks.get(tid, {}).get("status") == "blocked":
                if allowed(tid):
                    actions.append({
                        "action": "await-worker-terminal", "tid": tid, "attempt": attempt,
                        "model": None,
                        "reason": "blocked 已报告但 worker 未 terminal；等待终止，不替代派发",
                    })
                occupancy += 1
                continue

        if verdict == "ready":
            if allowed(tid):
                reason = (
                    f"report done + refs 验证通过（{detail}）"
                    if report and report.get("status") == "done"
                    else f"refs 派生：{detail}"
                )
                actions.append({
                    "action": "integrate", "tid": tid, "attempt": attempt,
                    "model": None, "reason": reason,
                })
            occupancy += 1
            continue
        if verdict == "contract":
            action = _retry_or_escalate_action(
                tid, attempt,
                fail_class="contract", detail=detail,
                events=events, ladder=ladder, breakers=breakers,
                max_auto_retries=max_auto_retries, mode_probe=mode_probe,
            )
            if allowed(tid):
                actions.append(action)
            if action["action"] == "redispatch":
                occupancy += 1
            continue
        if (
            disposition
            and disposition.get("event") == "report"
            and disposition.get("status") == "blocked"
        ):
            if allowed(tid):
                actions.append({
                    "action": "escalate", "tid": tid, "attempt": attempt,
                    "model": None,
                    "reason": "task blocked：review/黑盒满轮，须用户裁决",
                })
            continue
        if effective_tasks.get(tid, {}).get("status") == "blocked":
            if allowed(tid):
                actions.append({
                    "action": "escalate", "tid": tid, "attempt": attempt,
                    "model": None,
                    "reason": "分支已 blocked（worker 已 block、report 未落账）；须用户裁决",
                })
            continue

        failed = None
        if disposition:
            if disposition.get("event") == "failed" or disposition.get("status") == "failed":
                failed = disposition
        if failed:
            action = _retry_or_escalate_action(
                tid, attempt,
                fail_class=failed.get("class") or "task",
                detail=failed.get("reason", ""),
                events=events, ladder=ladder, breakers=breakers,
                max_auto_retries=max_auto_retries, mode_probe=mode_probe,
            )
            if allowed(tid):
                actions.append(action)
            if action["action"] == "redispatch":
                occupancy += 1
            continue

        observation = observer(tev, attempt)
        if observation and is_silent(observation.get("ts", ""), silent_minutes, now):
            silent_hold = True
            fingerprint = observation.get("fingerprint", "")
            already_alerted = any(
                event.get("event") == "silent_alerted"
                and event.get("attempt") == attempt
                and event.get("fingerprint") == fingerprint
                for event in tev
            )
            if not already_alerted and allowed(tid):
                changed_at = _parse_instant(observation.get("ts", ""))
                elapsed = max(0, int((now - changed_at).total_seconds() // 60)) if changed_at else 0
                actions.append({
                    "action": "alert-silent", "tid": tid, "attempt": attempt,
                    "model": dispatch.get("model"),
                    "worker_id": dispatch.get("worker_id", ""),
                    "fingerprint": fingerprint,
                    "last_activity": observation.get("ts", ""),
                    "silent_minutes": elapsed,
                    "head": observation.get("head", ""),
                    "worktree": observation.get("worktree", ""),
                    "dirty": observation.get("dirty", ""),
                    "reason": f"连续 {elapsed} 分钟无仓库可见变化，worker 可能出现问题",
                })
        occupancy += 1

    latched_attempts = _escalate_latched_attempts(events)
    free = limit - occupancy
    if free > 0 and not silent_hold:
        conflicts = schedule.get("conflicts", {})
        for tid in schedule.get("selected", []):
            if free <= 0:
                break
            current = current_attempt(tid, events)
            latched = current is not None and (tid, current) in latched_attempts
            if tid in in_flight_tids or latched or not allowed(tid):
                continue
            if conflicts.get(tid, set()) & in_flight_tids:
                continue
            reason = "空槽补位：下一批可跑"
            model = None
            if ladder:
                model, note = _pick_unbroken_model(ladder, 0, breakers)
                if model is None:
                    actions.append({
                        "action": "escalate", "tid": tid, "attempt": None,
                        "model": None, "reason": "模型阶梯全部熔断",
                    })
                    in_flight_tids.add(tid)
                    free -= 1
                    continue
                if note:
                    reason = f"{reason}（{note}）"
            actions.append({
                "action": "dispatch", "tid": tid,
                "attempt": ledger_next_attempt(tid, events),
                "model": model, "reason": reason,
            })
            in_flight_tids.add(tid)
            free -= 1
    return {
        "actions": actions,
        "occupancy": {"used": occupancy, "limit": limit},
        "silent_hold": silent_hold,
    }
