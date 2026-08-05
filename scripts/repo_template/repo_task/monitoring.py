"""Repository observation, handoff verification, and attempt-based monitoring."""

import hashlib
import json
import os
import stat
from datetime import datetime, timedelta
from pathlib import Path

import repo_task.context as ctx

from .attempts import (
    append_observation,
    attempt_for_identity,
    attempts_for_tid,
    current_attempt_record,
    in_flight_attempts,
    next_attempt,
    overlapping_attempts,
    project_attempts,
)
from .git_ops import _git, _git_bytes, has_unmerged_commits, worktree_paths
from .store import _task_branch_names, git_text_at_ref, load_task_at_ref


def _ledger_tid_sort_key(tid: str):
    match = ctx.TID_RE.fullmatch(tid or "")
    return (0, int(match.group(1)), "") if match else (1, 0, tid or "")


def _parse_instant(text: str) -> datetime | None:
    if not text:
        return None
    try:
        instant = datetime.fromisoformat(text)
    except ValueError:
        return None
    return instant.astimezone() if instant.tzinfo is None else instant


def is_silent(last_change: str, silent_minutes: int, now: datetime) -> bool:
    instant = _parse_instant(last_change)
    return instant is not None and now - instant > timedelta(minutes=silent_minutes)


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


_FINGERPRINT_LARGE_FILE_LIMIT = 1024 * 1024  # 1 MB
_FINGERPRINT_LARGE_FILE_PREFIX = 8192  # 大文件只读前 8 KB


def repository_fingerprint(root: Path) -> dict:
    """Hash HEAD, binary diffs, and sorted non-ignored untracked entries.

    超过 1 MB 的 untracked 常规文件不全量读入，只哈希 (size, mtime_ns,
    前 8 KB 内容)，避免 observe 被大文件拖慢；静默检测对超大文件变化
    的精度随之降级（仅内容前缀变化可感知）。
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
            if info.st_size > _FINGERPRINT_LARGE_FILE_LIMIT:
                # 大文件：只记 size + mtime_ns + 前 8 KB，不读全量。
                with path.open("rb") as fh:
                    prefix = fh.read(_FINGERPRINT_LARGE_FILE_PREFIX)
                content = (
                    f"large:{info.st_size}:{info.st_mtime_ns}:".encode("ascii") + prefix
                )
            else:
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


def latest_observation(
    events_for_tid: list[dict], attempt: int, execution_id: str
) -> dict | None:
    matches = [
        event for event in events_for_tid
        if event.get("event") == "observation"
        and event.get("attempt") == attempt
        and event.get("execution_id") == execution_id
    ]
    return matches[-1] if matches else None


def _attempt_worktree(
    tid: str, attempt: int, execution_id: str, events: list[dict]
) -> tuple[dict, Path, str]:
    record = attempt_for_identity(tid, attempt, execution_id, events)
    current = current_attempt_record(tid, events)
    if record is None:
        raise ctx.TaskDataError(f"{tid} attempt={attempt} execution_id={execution_id!r} 未 reserve")
    if current is None or current["attempt"] != attempt or current["execution_id"] != execution_id:
        raise ctx.TaskDataError(f"{tid} attempt={attempt} execution_id={execution_id!r} 不是当前 identity")
    if record["state"] != "running":
        raise ctx.TaskDataError(f"{tid} attempt={attempt} state={record['state']!r}，不可 observe")
    if record["executor"] != "agent" or record.get("bound") is None:
        raise ctx.TaskDataError("observe 只适用于已 bind 的 executor=agent running attempt")
    starts = [
        event for event in events
        if event.get("event") == "start" and event.get("tid") == tid and event.get("worktree")
    ]
    start = starts[-1] if starts else {}
    worktree_rel = start.get("worktree") or ctx.worktree_rel_path(tid)
    worktree = (ctx.REPO_ROOT / worktree_rel).resolve()
    branch = worktree_paths().get(str(worktree))
    if not worktree.is_dir() or not branch:
        raise ctx.TaskDataError(f"{tid} worktree 不存在或未登记：{worktree_rel}")
    match = ctx.TASK_BRANCH_RE.fullmatch(branch)
    if not match or match.group(1) != tid:
        raise ctx.TaskDataError(f"{tid} worktree 归属不符：登记分支 {branch!r}")
    if start.get("branch") and start["branch"] != branch:
        raise ctx.TaskDataError(f"{tid} worktree 归属不符：start={start['branch']!r}，当前={branch!r}")
    current_branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], root=worktree)
    if current_branch.returncode != 0 or current_branch.stdout.strip() != branch:
        raise ctx.TaskDataError(f"{tid} worktree 当前分支与登记不符")
    return record, worktree, worktree_rel


def observe_attempt(
    tid: str,
    attempt: int,
    execution_id: str,
    events: list[dict],
    *,
    now: datetime | None = None,
) -> dict:
    record, worktree, worktree_rel = _attempt_worktree(tid, attempt, execution_id, events)
    snapshot = repository_fingerprint(worktree)
    tev = [event for event in events if event.get("tid") == tid]
    prior = latest_observation(tev, attempt, execution_id)
    changed = prior is None or prior.get("fingerprint") != snapshot["fingerprint"]
    if changed:
        observation = append_observation(tid, attempt, execution_id, {
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
        "execution_id": execution_id,
        "executor": record["executor"],
        "host_worker_id": record["host_worker_id"],
        "fingerprint": snapshot["fingerprint"],
        "changed": changed,
        "last_change": observation.get("ts", ""),
        "silent_minutes": silence,
        "head": snapshot["head"],
        "worktree": worktree_rel,
        "dirty": observation.get("dirty", worktree_dirty_summary(worktree)),
    }


_HANDOFF_TYPES = {
    "tid": str,
    "attempt": int,
    "execution_id": str,
    "status": str,
    "branch": str,
    "base_sha": str,
    "tests": str,
    "blackbox": str,
    "review": str,
    "pending": list,
    "findings": list,
}


def _validate_string_list(name: str, value) -> str | None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return f"{name} 必须是字符串数组"
    return None


def verify_integrate_ready(
    tid: str,
    attempt: int,
    execution_id: str,
) -> tuple[str, str]:
    """Verify one execution commit and exact handoff provenance."""
    if (
        not isinstance(attempt, int)
        or isinstance(attempt, bool)
        or attempt <= 0
        or not isinstance(execution_id, str)
        or not execution_id
    ):
        return "contract", "verify 必须提供正整数 attempt 与非空 execution_id"
    branches = _task_branch_names(tid)
    if not branches:
        return "incomplete", "无本地 task 分支"
    if len(branches) > 1:
        return "contract", f"存在多个分支：{', '.join(branches)}"
    branch = branches[0]
    try:
        task, fm, _ = load_task_at_ref(tid, branch)
    except ctx.TaskDataError as error:
        return "incomplete", str(error)
    status = fm.get("status")
    if status not in ctx.ARCHIVED_STATUSES:
        return "incomplete", f"分支 {branch!r} tip status={status!r} 非终态"
    handoff_path = f"{task['dir']}/handoff.json"
    try:
        handoff = json.loads(git_text_at_ref(branch, handoff_path))
    except ctx.TaskDataError:
        return "contract", f"分支 {branch!r} tip 缺 {handoff_path}"
    except json.JSONDecodeError:
        return "contract", f"分支 {branch!r} tip {handoff_path} 无法解析"
    if not isinstance(handoff, dict):
        return "contract", f"{handoff_path} 非 JSON 对象"
    for key, expected in _HANDOFF_TYPES.items():
        if key not in handoff:
            return "contract", f"{handoff_path} 缺必填字段 {key}"
        if (
            not isinstance(handoff[key], expected)
            or (key == "attempt" and isinstance(handoff[key], bool))
            or (key == "attempt" and handoff[key] <= 0)
            or (expected is str and not handoff[key])
        ):
            return "contract", f"{handoff_path} 字段 {key} 类型或值非法"
    for key in ("pending", "findings"):
        problem = _validate_string_list(key, handoff[key])
        if problem:
            return "contract", f"{handoff_path} {problem}"
    expected_values = {
        "tid": tid,
        "attempt": attempt,
        "execution_id": execution_id,
        "status": status,
        "branch": branch,
    }
    for key, expected in expected_values.items():
        if handoff.get(key) != expected:
            return "contract", f"{handoff_path} {key}={handoff.get(key)!r} 与当前 {expected!r} 不符"
    tip_result = _git(["rev-parse", f"refs/heads/{branch}^{{commit}}"])
    parent_result = _git(["rev-parse", f"refs/heads/{branch}^1"])
    base_result = _git(["rev-parse", f"{handoff['base_sha']}^{{commit}}"])
    if tip_result.returncode != 0 or parent_result.returncode != 0:
        return "contract", f"分支 {branch!r} tip 或 first parent 无法解析 commit"
    if base_result.returncode != 0:
        return "contract", f"{handoff_path} base_sha 无法解析 commit"
    first_parent = parent_result.stdout.strip()
    diff_anchor = fm.get("diff_anchor")
    if not isinstance(diff_anchor, str) or not diff_anchor:
        return "contract", f"分支 {branch!r} task diff_anchor 缺失或非法"
    if (
        base_result.stdout.strip() != first_parent
        or handoff["base_sha"] != first_parent
        or diff_anchor != first_parent
    ):
        return "contract", (
            f"{handoff_path} base_sha={handoff['base_sha']!r}、task diff_anchor="
            f"{diff_anchor!r} 与 branch tip first parent {first_parent!r} 不一致；"
            "一个 task 必须恰有一个执行 commit"
        )
    detail = "分支 tip terminal + exact handoff + diff_anchor/first-parent provenance"
    if not has_unmerged_commits(branch):
        detail += "（已合入）"
    return "ready", detail


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
    records = project_attempts(events)
    ledger_tids = {record["tid"] for record in records.values()}
    # 合并 effective 中 active/blocked 的 tid：frontmatter 被手工改状态但未走
    # reserve 的脏状态也要可见，避免状态不一致被掩盖（无 reserve 标注）。
    active_tids = {
        tid for tid, task in effective.items()
        if task.get("status") in ("active", "blocked")
    }
    rows = []
    for tid in sorted(ledger_tids | active_tids, key=_ledger_tid_sort_key):
        record = current_attempt_record(tid, events)
        effective_status = effective.get(tid, {}).get("status", "")
        base = {
            "tid": tid,
            "attempt": record["attempt"] if record else None,
            "execution_id": record["execution_id"] if record else "",
            "executor": record["executor"] if record else "",
            "model": record["model"] if record else "",
            "host_worker_id": record["host_worker_id"] if record else "",
            "last_activity": "-",
            "note": "",
        }
        if record is None:
            # frontmatter active/blocked 但无 attempt：脏状态，标注无 reserve。
            base["state"] = f"{effective_status}(无 reserve)"
            rows.append(base)
            continue
        if main_statuses.get(tid) in ctx.ARCHIVED_STATUSES:
            base["state"] = main_statuses[tid]
            rows.append(base)
            continue
        report = record.get("report")
        if record["state"] == "integrated":
            base["state"] = "done"
        elif record["state"] == "escalated":
            base["state"] = "escalated"
            base["note"] = (record.get("escalated") or {}).get("reason", "")
        elif record["state"] == "reserved":
            base["state"] = "reserved(未bind)"
        elif record["state"] == "running":
            verdict, detail = verifier(
                record["tid"], record["attempt"], record["execution_id"]
            )
            if verdict == "ready":
                base["state"] = "ready待terminal"
                base["note"] = detail
            elif record["executor"] != "agent" or record.get("bound") is None:
                base["state"] = "running(inline)"
            else:
                observation = observer(
                    [e for e in events if e.get("tid") == tid],
                    record["attempt"],
                    record["execution_id"],
                )
                if observation is None:
                    base["state"] = "running(未观察)"
                else:
                    base["last_activity"] = observation.get("ts", "-")
                    base["state"] = "silent?" if is_silent(
                        base["last_activity"], silent_minutes, now
                    ) else "progressing"
                    base["note"] = observation.get("dirty", "")
        else:
            verdict, detail = verifier(
                record["tid"], record["attempt"], record["execution_id"]
            )
            if verdict == "ready":
                base["state"] = "done待合并"
                base["note"] = detail
            elif report and report.get("status") == "blocked":
                base["state"] = "blocked"
                base["note"] = report.get("reason", "")
            elif record["terminal_status"] in ("failed", "stopped") or (
                report and report.get("status") == "failed"
            ):
                fail_class = (report or {}).get("class", "task")
                base["state"] = f"failed:{fail_class}"
                base["note"] = (report or {}).get("reason", "")
            else:
                base["state"] = f"terminal:{record['terminal_status']}"
                base["note"] = detail if verdict == "contract" else ""
        if record["attempt"] in overlapping_attempts(tid, events):
            base["state"] = "contract:overlap"
        rows.append(base)
    return rows


def dispatch_mode(tid: str, events_for_tid: list[dict]) -> str:
    starts = [event for event in events_for_tid if event.get("event") == "start" and event.get("worktree")]
    worktree_rel = starts[-1]["worktree"] if starts else ctx.worktree_rel_path(tid)
    if (ctx.REPO_ROOT / worktree_rel).resolve().is_dir():
        return "resume"
    if any(has_unmerged_commits(branch) for branch in _task_branch_names(tid)):
        return "resume"
    return "restart"


def _parent_dispatch_model(tid: str, parent_attempt: int, events: list[dict]) -> str | None:
    records = [record for record in attempts_for_tid(tid, events) if record["attempt"] == parent_attempt]
    return records[-1]["model"] if records else None


def _retry_or_escalate_action(
    tid: str,
    parent_attempt: int,
    *,
    fail_class: str,
    detail: str,
    events: list[dict],
    max_auto_retries: int,
    mode_probe=dispatch_mode,
) -> dict:
    reason = f"#{parent_attempt} {fail_class} 失败：{detail}"
    # 重试额度按该 tid 在 exact identity 之前发生的显式失败事件数计数，
    # 而非 attempt 号差——escalate 轮次不计入重试额度。
    prior_failures = 0
    for record in attempts_for_tid(tid, events):
        if record["attempt"] >= parent_attempt:
            continue
        terminal = record.get("terminal") or {}
        report = record.get("report") or {}
        if (
            terminal.get("status") in ("failed", "stopped")
            or report.get("status") == "failed"
        ):
            prior_failures += 1
    if prior_failures >= max_auto_retries:
        record = current_attempt_record(tid, events)
        return {
            "action": "escalate", "tid": tid, "attempt": parent_attempt,
            "execution_id": record["execution_id"] if record else "",
            "model": None, "reason": f"{reason}；自动重试额度（{max_auto_retries}）用尽",
        }
    tev = [event for event in events if event.get("tid") == tid]
    mode = mode_probe(tid, tev)
    parent_model = _parent_dispatch_model(tid, parent_attempt, events)
    if fail_class == "contract":
        if mode == "restart":
            record = current_attempt_record(tid, events)
            return {
                "action": "escalate", "tid": tid, "attempt": parent_attempt,
                "execution_id": record["execution_id"] if record else "",
                "model": None, "reason": f"{reason}；无现场可续：worktree/分支缺失",
            }
        reason += "（同模型 resume：补交接单）"
    return {
        "action": "dispatch", "tid": tid, "attempt": next_attempt(tid, events),
        "model": parent_model, "mode": mode, "reason": reason,
    }


def _escalate_latched_attempts(events: list[dict]) -> set[tuple[str, int]]:
    return {
        (record["tid"], record["attempt"])
        for record in project_attempts(events).values()
        if record["state"] == "escalated"
    }


def compute_reconcile_plan(
    events: list[dict],
    schedule: dict,
    *,
    limit: int,
    scope: set[str] | None,
    silent_minutes: int,
    max_auto_retries: int,
    now: datetime,
    observer=latest_observation,
    verifier=verify_integrate_ready,
    mode_probe=dispatch_mode,
) -> dict:
    main_done_set = schedule.get("main_done_set", set())
    dropped_set = schedule.get("dropped_set", set())
    effective_tasks = schedule.get("tasks", {})
    allowed = lambda tid: scope is None or tid in scope
    actions = []
    occupancy = 0
    silent_hold = False
    in_flight = in_flight_attempts(events)
    in_flight_tids = {
        tid for tid, _, _ in in_flight if tid not in main_done_set and tid not in dropped_set
    }
    for tid, attempt, record in in_flight:
        if tid in main_done_set or tid in dropped_set:
            continue
        if attempt in overlapping_attempts(tid, events):
            if allowed(tid):
                actions.append({
                    "action": "await-terminal", "tid": tid, "attempt": attempt,
                    "execution_id": record["execution_id"], "model": record["model"],
                    "reason": "检测到重叠 attempt；停止自动处置",
                })
            occupancy += 1
            continue
        report = record.get("report")
        verdict, detail = verifier(tid, attempt, record["execution_id"])
        if record["state"] == "reserved":
            reserved_ts = (record.get("reserved") or {}).get("ts", "")
            reserved_at = _parse_instant(reserved_ts)
            stale = (
                reserved_at is not None
                and now - reserved_at > timedelta(minutes=silent_minutes)
            )
            if stale:
                # 悬挂超时：宿主可能从未启动，升级用户裁决，不无限 await-bind 占槽。
                if allowed(tid):
                    actions.append({
                        "action": "escalate", "tid": tid, "attempt": attempt,
                        "execution_id": record["execution_id"], "executor": record["executor"],
                        "model": record["model"], "host_worker_id": record["host_worker_id"],
                        "reason": f"reserved 悬挂超过 {silent_minutes} 分钟未 bind；"
                                  "宿主可能未启动，须用户裁决",
                    })
                continue
            if allowed(tid):
                actions.append({
                    "action": "await-bind", "tid": tid, "attempt": attempt,
                    "execution_id": record["execution_id"], "executor": record["executor"],
                    "model": record["model"], "host_worker_id": record["host_worker_id"],
                    "reason": "agent attempt 已 reserve，等待 bind execution/host identity",
                })
            occupancy += 1
            continue
        if record["state"] == "running":
            if verdict in ("ready", "contract"):
                # running 且 refs 已终态：仍做轻量观察，避免 worker 崩溃后
                # 分支被外部推到终态的场景无告警永久占槽。
                observation = None
                if record["executor"] == "agent" and record.get("bound") is not None:
                    tev = [event for event in events if event.get("tid") == tid]
                    observation = observer(tev, attempt, record["execution_id"])
                if (
                    observation
                    and is_silent(observation.get("ts", ""), silent_minutes, now)
                ):
                    fingerprint = observation.get("fingerprint", "")
                    already = any(
                        event.get("fingerprint") == fingerprint
                        for event in record.get("silent_alerted", [])
                    )
                    if not already and allowed(tid):
                        changed_at = _parse_instant(observation.get("ts", ""))
                        elapsed = (
                            max(0, int((now - changed_at).total_seconds() // 60))
                            if changed_at else 0
                        )
                        actions.append({
                            "action": "alert-silent", "tid": tid, "attempt": attempt,
                            "execution_id": record["execution_id"],
                            "executor": record["executor"], "model": record["model"],
                            "host_worker_id": record["host_worker_id"],
                            "fingerprint": fingerprint,
                            "last_activity": observation.get("ts", ""),
                            "silent_minutes": elapsed,
                            "reason": f"running 且 refs {verdict}，但连续 {elapsed} 分钟"
                                      "无仓库变化；worker 可能已崩溃，只告警不重派",
                        })
                if allowed(tid):
                    actions.append({
                        "action": "await-terminal", "tid": tid, "attempt": attempt,
                        "execution_id": record["execution_id"], "executor": record["executor"],
                        "model": record["model"], "host_worker_id": record["host_worker_id"],
                        "reason": f"attempt 尚在 running；{verdict}：{detail}",
                    })
                occupancy += 1
                continue
            observation = None
            if record["executor"] == "agent" and record.get("bound") is not None:
                tev = [event for event in events if event.get("tid") == tid]
                observation = observer(tev, attempt, record["execution_id"])
            if observation and is_silent(observation.get("ts", ""), silent_minutes, now):
                silent_hold = True
                fingerprint = observation.get("fingerprint", "")
                already = any(
                    event.get("fingerprint") == fingerprint
                    for event in record.get("silent_alerted", [])
                )
                if not already and allowed(tid):
                    changed_at = _parse_instant(observation.get("ts", ""))
                    elapsed = max(0, int((now - changed_at).total_seconds() // 60)) if changed_at else 0
                    actions.append({
                        "action": "alert-silent", "tid": tid, "attempt": attempt,
                        "execution_id": record["execution_id"], "executor": record["executor"],
                        "model": record["model"], "host_worker_id": record["host_worker_id"],
                        "fingerprint": fingerprint, "last_activity": observation.get("ts", ""),
                        "silent_minutes": elapsed, "head": observation.get("head", ""),
                        "worktree": observation.get("worktree", ""),
                        "dirty": observation.get("dirty", ""),
                        "reason": f"连续 {elapsed} 分钟无仓库可见变化，只告警不重派",
                    })
            occupancy += 1
            continue
        if verdict == "ready" and record["terminal_status"] == "completed":
            if allowed(tid):
                actions.append({
                    "action": "integrate", "tid": tid, "attempt": attempt,
                    "execution_id": record["execution_id"], "executor": record["executor"],
                    "model": None, "host_worker_id": record["host_worker_id"],
                    "reason": f"terminal completed + refs 验证通过（{detail}）",
                })
            occupancy += 1
            continue
        if (
            (report and report.get("status") == "blocked")
            or effective_tasks.get(tid, {}).get("status") == "blocked"
        ):
            action = {
                "action": "escalate", "tid": tid, "attempt": attempt,
                "execution_id": record["execution_id"], "model": None,
                "host_worker_id": record["host_worker_id"],
                "reason": "task blocked；须用户裁决",
            }
        elif record["terminal_status"] in ("failed", "stopped") or (
            report and report.get("status") == "failed"
        ):
            if record["terminal_status"] in ("failed", "stopped") and not report:
                # 裸 terminal failed/stopped：先等 report 落账，禁止自动 redispatch，
                # 避免新 attempt 成为 current 后旧 identity 的 class/reason 永久丢失。
                if allowed(tid):
                    actions.append({
                        "action": "await-report", "tid": tid, "attempt": attempt,
                        "execution_id": record["execution_id"], "executor": record["executor"],
                        "model": record["model"], "host_worker_id": record["host_worker_id"],
                        "reason": "terminal failed/stopped 但尚无 report；"
                                  "coordinator 先写 report 再进入 retry/escalate",
                    })
                occupancy += 1
                continue
            action = _retry_or_escalate_action(
                tid, attempt,
                fail_class=(report or {}).get("class") or "task",
                detail=(report or {}).get("reason", record["terminal_status"]),
                events=events,
                max_auto_retries=max_auto_retries, mode_probe=mode_probe,
            )
        elif record["terminal_status"] == "completed":
            action = {
                "action": "escalate", "tid": tid, "attempt": attempt,
                "execution_id": record["execution_id"], "model": None,
                "host_worker_id": record["host_worker_id"],
                "reason": f"completed attempt 尚未达到 integrate-ready（{verdict}：{detail}）；"
                "必须先修复并 integrate 或明确 escalate，禁止自动 retry",
            }
        else:
            occupancy += 1
            continue
        if allowed(tid):
            actions.append(action)
        if action["action"] == "dispatch":
            occupancy += 1

    free = limit - occupancy
    latched = _escalate_latched_attempts(events)
    if free > 0 and not silent_hold:
        conflicts = schedule.get("conflicts", {})
        for tid in schedule.get("selected", []):
            if free <= 0:
                break
            current = current_attempt_record(tid, events)
            is_latched = current is not None and (tid, current["attempt"]) in latched
            if tid in in_flight_tids or is_latched or not allowed(tid):
                continue
            if conflicts.get(tid, set()) & in_flight_tids:
                continue
            reason = "空槽补位：下一批可跑"
            actions.append({
                "action": "dispatch", "tid": tid,
                "attempt": next_attempt(tid, events), "model": None, "reason": reason,
            })
            in_flight_tids.add(tid)
            free -= 1
    return {
        "actions": actions,
        "occupancy": {"used": occupancy, "limit": limit},
        "silent_hold": silent_hold,
    }
