"""Read-only recovery classification. Never reserve, commit, finish, or remove files."""
import json

from . import context as ctx
from .attempts import current_attempt_record, overlapping_attempts, validate_execution_context
from .documents import parse_front_matter
from .git_ops import _git, require_primary_worktree, worktree_paths
from .ledger import ledger_read
from .locks import control_locked
from .monitoring import implementation_commit, verify_integrate_ready
from .store import discover_effective_tasks, git_text_at_ref, load_task_at_ref


def recovery_state(tid: str) -> dict:
    require_primary_worktree()
    task = discover_effective_tasks().get(tid)
    if task is None:
        raise ctx.TaskDataError(f"{tid} 不存在")
    events = ledger_read()
    record = current_attempt_record(tid, events)
    state = {"tid": tid, "phase": "needs_attention", "action": "停止并报告状态冲突"}
    if record:
        state.update({name: record[name] for name in ("attempt", "execution_id")})
        state["owner_id"] = record.get("owner_id", "")
    if overlapping_attempts(tid, events):
        state["action"] = "停止：存在 overlapping attempt，不猜 identity"
        return state
    worktree = (ctx.REPO_ROOT / ctx.worktree_rel_path(tid)).resolve()
    registered_branch = worktree_paths().get(str(worktree))
    branch = task.get("branch") or f"{tid}_{task['slug']}"
    state.update(branch=branch, worktree=str(worktree))

    def phase(value: str, action: str) -> dict:
        state.update(phase=value, action=action)
        return state

    if not registered_branch:
        if task["status"] in ctx.ARCHIVED_STATUSES and (not record or record["state"] == "integrated"):
            return phase("integrated_or_archived", "检查 Git merge 状态和 integrated 记录；不重复执行")
        if record:
            verdict, _kind, detail = verify_integrate_ready(tid, record["attempt"], record["execution_id"])
            report = record.get("report") or {}
            if verdict == "ready" and record.get("terminal_status") == "completed" and report.get("status") == "done":
                return phase("closed", "保留分支作下一 --base；整链完成后询问合并授权")
            state["action"] = f"停止：无登记 worktree，{detail}"
            return state
        if task["status"] == "backlog":
            return phase("pending", "run-begin（start → reserve → claim）；若同名分支残留则先处理所有权")
        return state
    if registered_branch != branch:
        state["action"] = "停止：登记 worktree 分支与 task ownership 不符"
        return state
    task_dir = worktree / task["dir"]
    state["task_dir"] = str(task_dir)
    fm, _ = parse_front_matter(task_dir / ctx.PROGRESS_FILE)
    if fm.get("branch") != branch or fm.get("tid") != tid:
        return state
    if fm["status"] == "active":
        if record is None:
            if (worktree / "docs/runtime/execution_context.json").exists():
                return phase("runtime_missing", "worktree 留有执行 identity，但主仓 ledger 缺失；停止并恢复 runtime 备份，不 reserve、不猜补 active 历史")
            return phase("started_without_attempt", "只 reserve；不重复 start")
        try:
            validate_execution_context(worktree, tid, record)
        except ctx.TaskDataError as error:
            return phase("runtime_mismatch", str(error))
        if record["state"] == "running":
            if not record.get("owner_id"):
                return phase("executing", "尚未 claim：按原 identity 用 attempt claim 领取执行权（run-begin 中途失败时的现场），不重复 start/reserve")
            return phase("executing", "核对 owner_id；同一 owner 直接续行。跨会话接管必须确认原会话已停止并 attempt claim --replace-owner ... --confirm-stopped，不得直接复用 identity 并行实施")
        if (record.get("report") or {}).get("status") in {"blocked", "failed"}:
            return phase("retry_ready", "task 保持 active；直接 reserve 新 identity 后继续")
        return state
    if fm["status"] != "done" or record is None:
        return state
    if not (task_dir / "handoff.json").exists():
        head = _git(["rev-parse", "HEAD"], root=worktree)
        if head.returncode == 0 and head.stdout.strip() == fm.get("diff_anchor") and record["state"] == "running":
            return phase(
                "finish_interrupted",
                "finish 在生成 handoff.json 前被中断（已 done、无 handoff、无实现 commit）：停止并报告用户；"
                f"不重复 finish、不 reserve。核对 {ctx.PROGRESS_FILE} 验证摘要后，由用户决定补写 handoff.json 并提交，或把 {ctx.PROGRESS_FILE} 还原为 active",
            )
    try:
        handoff = json.loads((task_dir / "handoff.json").read_text(encoding="utf-8"))
        expected = {"tid": tid, "branch": branch, "status": "done",
                    "attempt": record["attempt"], "execution_id": record["execution_id"],
                    "base_sha": fm["diff_anchor"]}
        if any(handoff.get(key) != value for key, value in expected.items()):
            raise ValueError("handoff identity/base 不匹配")
        head = _git(["rev-parse", "HEAD"], root=worktree)
        if head.returncode != 0:
            raise ValueError(head.stderr)
        if head.stdout.strip() == fm["diff_anchor"]:
            if record["state"] == "running":
                return phase("finished_uncommitted", "finish 在提交前被中断：核验 handoff、review 和全部 diff 后暂存并提交实现 commit（沿用原 message）；不重复 finish、不 reserve")
            raise ValueError("未提交实现 commit 却已关闭 attempt")
        implementation_commit(head.stdout.strip(), fm["diff_anchor"])
        _, committed_fm, _ = load_task_at_ref(tid, head.stdout.strip())
        committed = json.loads(git_text_at_ref(head.stdout.strip(), f"{task['dir']}/handoff.json"))
        if committed_fm.get("status") != "done" or committed != handoff:
            raise ValueError("分支 tip 未包含当前 handoff")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ctx.TaskDataError) as error:
        state["action"] = f"停止：{error}"
        return state
    tip_sha = head.stdout.strip()
    diff_anchor = fm.get("diff_anchor", "")
    report = record.get("report") or {}
    report_done = report.get("status") == "done"
    gate_check = record["state"] == "running" or (
        record["state"] == "terminal" and record.get("terminal_status") == "completed"
    )
    if gate_check or report_done:
        verdict, kind, gate_detail = verify_integrate_ready(tid, record["attempt"], record["execution_id"])
        if verdict != "ready" and kind == "review_scope_mismatch":
            if report_done:
                state["action"] = (
                    f"停止：report 已写入 done，但实现 commit 内容偏离 review 内容（{gate_detail}）；"
                    "不可撤销、不可 amend，人工核查"
                )
                return state
            if record["state"] != "running":
                # 正常主路径下 run-close 门禁失败不写 terminal；terminal 已关闭又无 report
                # 只会来自手工 attempt terminal，finish-unwind 不处理，直接停止。
                state["action"] = (
                    f"停止：实现 commit 内容偏离 review 内容（{gate_detail}）；"
                    f"attempt 已是 {record['state']}:{record.get('terminal_status')} 且无 report，"
                    "finish-unwind 只处理 running 现场，人工核查"
                )
                return state
            if _tip_has_successor(tip_sha, branch):
                state["action"] = (
                    f"停止：实现 commit 内容偏离 review 内容（{gate_detail}）；"
                    f"已有后继分支包含当前 tip {tip_sha[:12]}，撤销会使其悬空，人工核查"
                )
                return state
            return phase(
                "review_mismatch",
                f"实现 commit 内容偏离 review 内容（{gate_detail}）。"
                f"无后继分支、report 未写入 done，可执行撤销：运行 "
                f"`task.py finish-unwind {tid} --reason <原因>` 把分支退回 diff_anchor、"
                "把 progress 还原为 active，然后重新 review 再 finish；"
                "禁止 rewind、reserve、amend 修复。",
            )
        if verdict != "ready" and kind == "review_gate":
            if report_done:
                state["action"] = (
                    f"停止：report 已写入 done，但 review 门禁失败（{gate_detail}）；"
                    "不改写实现 commit、不 amend，请人工核查"
                )
                return state
            action = _review_repair_action(
                tid, record["attempt"], record["execution_id"],
                tip_sha, diff_anchor, gate_detail, branch,
            )
            return phase("review_repair", action)
    if not record.get("report"):
        return phase("committed_unreported", "验证最终提交和 review gate；按原 identity 补缺失的 terminal/report，不重复写已有事件")
    if record.get("terminal_status") == "completed" and report_done:
        return phase("reported_uncleaned", "按原 identity exact cleanup；脏改动或门禁失败时保留现场")
    return state


def _tip_has_successor(tip_sha: str, current_branch: str) -> bool:
    """tip 是否已被其它本地分支包含（后继分支或已合入）。"""
    result = _git(["branch", "--format=%(refname:short)"])
    if result.returncode != 0:
        return False
    for branch in [line.strip() for line in result.stdout.splitlines() if line.strip()]:
        if branch == current_branch:
            continue
        if _git(["merge-base", "--is-ancestor", tip_sha, branch]).returncode == 0:
            return True
    return False


def _review_repair_action(tid: str, attempt: int, execution_id: str, tip_sha: str, diff_anchor: str, detail: str, branch: str) -> str:
    if _tip_has_successor(tip_sha, branch):
        return (
            f"review 修复：review 门禁失败（{detail}）。"
            f"已有后继分支以 tip {tip_sha[:12]} 为 ancestor；停止并报告，不 amend。"
        )
    return (
        f"review 修复：review 门禁失败（{detail}）。"
        f"在原 worktree 对当前 tip {tip_sha[:12]} 的交付内容重新 review；"
        "只改 review 过程文件"
        f"（{ctx.PROGRESS_FILE} 正文、review_*.md、渲染的 review prompt），不得改 handoff.json、交付文件或其它路径；"
        "用 `git commit --amend --no-edit` 把改动并入同一实现 commit；"
        f"worktree 干净后在主仓运行 `run-close {tid} --owner-id ... --result done`；"
        "禁止 rewind、reserve。"
    )


def cmd_recovery(args) -> None:
    print(json.dumps(recovery_state(args.tid), ensure_ascii=False, indent=2))


@control_locked
def cmd_ledger_import(args):
    """Recover only a proven completed execution; never invent a running owner."""
    require_primary_worktree()
    if not args.confirm_stopped:
        raise ctx.TaskDataError("导入前须确认原执行会话已停止：--confirm-stopped")
    task = discover_effective_tasks().get(args.tid)
    if not task or task["status"] != "done" or not task.get("branch"):
        raise ctx.TaskDataError("仅可从现存 done task 分支及 committed handoff 重建；active 或缺证据须恢复 runtime 备份")
    branch = task["branch"]
    try:
        handoff = json.loads(git_text_at_ref(branch, f"{task['dir']}/handoff.json"))
    except (ValueError, ctx.TaskDataError) as error:
        raise ctx.TaskDataError(f"拒绝导入：handoff 无法读取（{error}）") from error
    if not isinstance(handoff, dict):
        raise ctx.TaskDataError("拒绝导入：handoff 非 JSON 对象")
    attempt, execution_id = handoff.get("attempt"), handoff.get("execution_id")
    verdict, _kind, detail = verify_integrate_ready(args.tid, attempt, execution_id)
    if verdict != "ready":
        raise ctx.TaskDataError(f"拒绝导入：{detail}")
    worktree = (ctx.REPO_ROOT / ctx.worktree_rel_path(args.tid)).resolve()
    if str(worktree) in worktree_paths():
        from .git_ops import porcelain_entries
        if porcelain_entries(root=worktree):
            raise ctx.TaskDataError("worktree 有未提交改动；先恢复或保存，不据 Git tip 猜测现场")
    sha = _git(["rev-parse", branch]).stdout.strip()
    from .ledger import ledger_locked_append_many

    def build(events):
        if any(event.get("tid") == args.tid for event in events):
            raise ctx.TaskDataError("已有该 tid 的 ledger 事件；拒绝覆盖或猜补部分历史，请恢复备份")
        identity = {"tid": args.tid, "attempt": attempt, "execution_id": execution_id,
                    "recovered_from": sha}
        return [{**identity, "event": "attempt_reserved", "executor": "inline", "state": "running"},
                {**identity, "event": "attempt_terminal", "status": "completed"},
                {**identity, "event": "report", "status": "done", "sha": sha}]

    ledger_locked_append_many(build)
    print(json.dumps({"tid": args.tid, "attempt": attempt, "execution_id": execution_id,
                      "recovered_from": sha}, ensure_ascii=False))
