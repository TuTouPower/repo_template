"""Read-only recovery classification. Never reserve, commit, finish, or remove files."""
import json

from . import context as ctx
from .attempts import current_attempt_record, overlapping_attempts
from .documents import parse_front_matter
from .git_ops import _git, require_primary_worktree, worktree_paths
from .ledger import ledger_read
from .monitoring import verify_integrate_ready
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
            verdict, detail = verify_integrate_ready(tid, record["attempt"], record["execution_id"])
            report = record.get("report") or {}
            if verdict == "ready" and record.get("terminal_status") == "completed" and report.get("status") == "done":
                return phase("closed", "保留分支作下一 --base；整链完成后询问合并授权")
            state["action"] = f"停止：无登记 worktree，{detail}"
            return state
        if task["status"] == "backlog":
            return phase("pending", "start → reserve；若同名分支残留则先处理所有权")
        return state
    if registered_branch != branch:
        state["action"] = "停止：登记 worktree 分支与 task ownership 不符"
        return state
    task_dir = worktree / task["dir"]
    state["task_dir"] = str(task_dir)
    fm, _ = parse_front_matter(task_dir / "task.md")
    if fm.get("branch") != branch or fm.get("tid") != tid:
        return state
    if fm["status"] == "active":
        if record is None:
            return phase("started_without_attempt", "只 reserve；不重复 start")
        if record["state"] == "running":
            return phase("executing", "以原 identity 继续 task-work")
        if (record.get("report") or {}).get("status") in {"blocked", "failed"}:
            return phase("retry_ready", "task 保持 active；直接 reserve 新 identity 后继续")
        return state
    if fm["status"] != "done" or record is None:
        return state
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
                return phase("finished_uncommitted", "核验归档 handoff、review 和全部 diff 后完成“收尾与执行 commit”；不 finish、不 reserve")
            raise ValueError("未提交执行 commit 却已关闭 attempt")
        parent = _git(["rev-parse", "HEAD^1"], root=worktree)
        if parent.returncode != 0 or parent.stdout.strip() != fm["diff_anchor"]:
            raise ValueError("HEAD 不是以 diff_anchor 为 first parent 的一个执行 commit")
        _, committed_fm, _ = load_task_at_ref(tid, head.stdout.strip())
        committed = json.loads(git_text_at_ref(head.stdout.strip(), f"{task['dir']}/handoff.json"))
        if committed_fm.get("status") != "done" or committed != handoff:
            raise ValueError("分支 tip 未包含当前归档 handoff")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ctx.TaskDataError) as error:
        state["action"] = f"停止：{error}"
        return state
    tip_sha = head.stdout.strip()
    diff_anchor = fm.get("diff_anchor", "")
    if record.get("state") == "terminal" and record.get("terminal_status") == "completed":
        verdict, gate_detail = verify_integrate_ready(tid, record["attempt"], record["execution_id"])
        if verdict != "ready" and "review gate" in gate_detail:
            action = _evidence_repair_action(
                tid, record["attempt"], record["execution_id"],
                tip_sha, diff_anchor, gate_detail, branch,
            )
            return phase("evidence_repair", action)
    if not record.get("report"):
        return phase("committed_unreported", "验证最终提交和 review gate；按原 identity 补缺失的 terminal/report，不重复写已有事件")
    if record.get("terminal_status") == "completed" and record["report"].get("status") == "done":
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


def _evidence_repair_action(tid: str, attempt: int, execution_id: str, tip_sha: str, diff_anchor: str, detail: str, branch: str) -> str:
    if _tip_has_successor(tip_sha, branch):
        return (
            f"证据修复：review 门禁失败（{detail}）。"
            f"已有后继分支以 tip {tip_sha[:12]} 为 ancestor；停止并报告，不 amend。"
        )
    return (
        f"证据修复：review 门禁失败（{detail}）。"
        f"在原 worktree 对当前 tip {tip_sha[:12]} 的交付内容重审；"
        "只改 review 过程文件；"
        f"git commit --amend 进同一个执行 commit，first parent 必须仍是 diff_anchor {diff_anchor[:12]}；"
        "禁止第二个 commit、rewind、reserve；"
        f"amend 后 worktree 必须干净，再按原 identity attempt={attempt} execution_id={execution_id} exact cleanup。"
    )


def cmd_recovery(args) -> None:
    print(json.dumps(recovery_state(args.tid), ensure_ascii=False, indent=2))
