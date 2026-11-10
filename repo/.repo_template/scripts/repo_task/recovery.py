"""Read-only recovery classification. Never reserve, commit, finish, or remove files."""
import json
import sys

from . import context as ctx
from .attempts import current_attempt_record, execution_context_path, overlapping_attempts, validate_execution_context
from .documents import parse_front_matter
from .git_ops import _git, is_ancestor, require_primary_worktree, worktree_paths
from .ledger import ledger_read
from .locks import control_locked
from .monitoring import implementation_commit, verify_integrate_ready
from .store import discover_effective_tasks, git_text_at_ref, load_task_at_ref


def recovery_state(tid: str, *, repo) -> dict:
    require_primary_worktree(repo=repo)
    corrupt_detail = ""
    try:
        tasks = discover_effective_tasks(repo=repo)
    except ctx.TaskDataError as error:
        # 现场损坏时仍允许 recovery 依据持久 journal 给出诊断，不伪装为正常状态（A05）。
        tasks = discover_effective_tasks(allow_corrupt=True, repo=repo)
        corrupt_detail = str(error)
    task = tasks.get(tid)
    if task is None:
        raise ctx.TaskDataError(f"{tid} 不存在")
    events = ledger_read(repo=repo)
    record = current_attempt_record(tid, events)
    state = {"tid": tid, "phase": "needs_attention", "action": "停止并报告状态冲突"}
    txn_committed = False
    if record:
        state.update({name: record[name] for name in ("attempt", "execution_id")})
        state["owner_id"] = record.get("owner_id", "")
    if overlapping_attempts(tid, events):
        state["action"] = "停止：存在 overlapping attempt，不猜 identity"
        return state
    worktree = (repo.primary_root / repo.worktree_rel(tid)).resolve()
    registered_branch = worktree_paths(repo=repo).get(str(worktree))
    branch = task.get("branch") or f"{tid}_{task['slug']}"
    state.update(branch=branch, worktree=str(worktree))

    clear_hint = (
        f"；收尾完成后运行 `python3 .repo_template/scripts/task.py finish-txn-clear {tid}` "
        "清理已完成事务"
    )

    def phase(value: str, action: str) -> dict:
        state.update(phase=value, action=action)
        if txn_committed:
            state["finish_txn"] = "committed"
            state["action"] += clear_hint
        return state

    if registered_branch and worktree.is_dir():
        from .commit_txn import judge as judge_txn, load as load_txn

        try:
            txn = load_txn(worktree)
        except ctx.TaskDataError as error:
            return phase("finish_txn_corrupt", f"提交事务 journal 损坏：{error}；人工核查后清理")
        if txn is not None:
            verdict, detail = judge_txn(worktree, txn)
            # committed 不短路：落入常规状态判定，给出 committed_unreported /
            # reported_uncleaned / review_mismatch 等准确 phase（P1-2）。
            if verdict == "committed":
                txn_committed = True
            else:
                actions = {
                    "rollback_safe": "HEAD 未前进且现场未变；重跑 finish 会先幂等回滚该事务再继续",
                    "unknown": "提交结果未知或现场被外部修改；保留 journal，人工核查 HEAD、git log 与工作区后决定",
                }
                return phase(f"finish_txn_{verdict}", f"{detail}；{actions[verdict]}")
    if task.get("status") == "corrupt":
        return phase("corrupt", corrupt_detail or task.get("note") or "现场数据损坏；人工核查后处理")

    if not registered_branch:
        if task["status"] in ctx.TERMINAL_STATUSES and (not record or record["state"] == "integrated"):
            return phase("integrated_or_terminal", "检查 Git merge 状态和 integrated 记录；不重复执行")
        if record:
            verdict, _kind, detail = verify_integrate_ready(
                tid, record["attempt"], record["execution_id"], repo=repo
            )
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
    try:
        fm, _ = parse_front_matter(task_dir / ctx.PROGRESS_FILE)
    except (OSError, ctx.TaskDataError) as error:
        return phase("corrupt", f"worktree 进展无法解析（{error}）；人工核查后处理")
    if fm.get("branch") != branch or fm.get("tid") != tid:
        return state
    if fm.get("status") not in (*ctx.TERMINAL_STATUSES, "active", "backlog"):
        return phase(
            "corrupt",
            f"worktree 进展 status 非法或缺失（{fm.get('status')!r}）；人工核查后处理",
        )
    if fm["status"] == "active":
        if record is None:
            if execution_context_path(worktree).exists():
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
            return phase(
                "retry_ready",
                "task 保持 active；reserve 新 identity 后继续。新 attempt 须重新绑定状态行："
                "若 bind 失败会输出含 session/tid/identity 的诊断，按诊断排查，不猜补 start 历史",
            )
        return state
    if fm["status"] != "done" or record is None:
        return state
    if not (task_dir / "handoff.json").exists():
        head = _git(["rev-parse", "HEAD"], root=worktree, repo=repo)
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
                    "base_sha": fm["diff_anchor"],
                    "spec_source_sha": fm.get("spec_source_sha", "")}
        if any(handoff.get(key) != value for key, value in expected.items()):
            raise ValueError("handoff identity/base 不匹配")
        head = _git(["rev-parse", "HEAD"], root=worktree)
        if head.returncode != 0:
            raise ValueError(head.stderr)
        if head.stdout.strip() == fm["diff_anchor"]:
            if record["state"] == "running":
                return phase("finished_uncommitted", "finish 在提交前被中断：核验 handoff、review 和全部 diff 后暂存并提交实现 commit（沿用原 message）；不重复 finish、不 reserve")
            raise ValueError("未提交实现 commit 却已关闭 attempt")
        implementation_commit(head.stdout.strip(), fm["diff_anchor"], repo=repo)
        _, committed_fm, _ = load_task_at_ref(tid, head.stdout.strip(), repo=repo)
        committed = json.loads(
            git_text_at_ref(head.stdout.strip(), f"{task['dir']}/handoff.json", repo=repo)
        )
        if committed_fm.get("status") != "done" or committed != handoff:
            raise ValueError("分支 tip 未包含当前 handoff")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ctx.TaskDataError) as error:
        state["action"] = f"停止：{error}"
        return state
    tip_sha = head.stdout.strip()
    # P1-3：recovery 边界统一校验 tip commit 引入的新增行隐私，泄漏则给出专用
    # phase，不再让泄漏提交经 committed_unreported / run-close 一路合入主干。
    try:
        from .lifecycle import scan_tip_privacy_leaks
        tip_leaks = scan_tip_privacy_leaks(tip_sha, repo=repo)
    except ctx.TaskDataError as error:
        state["action"] = f"停止：{error}"
        return state
    if tip_leaks:
        shown = "；".join(tip_leaks[:8])
        return phase(
            "committed_privacy_leak",
            f"实现 commit {tip_sha[:12]} 引入开发者家目录绝对路径泄漏（{shown}）；"
            "HEAD 已前移不回滚，停止收尾/合入。用 "
            f"`git log -S <泄漏路径片段> --oneline -- {branch}` 定位引入提交："
            "确属非个人环境固定路径，在 repo_task/privacy.py FIXED_PATH_EXCEPTIONS "
            "显式豁免；否则移除泄漏内容后重新 review，report 未 done 且无后继分支时"
            "可用 finish-unwind 退回重做，人工修正后重跑 finish",
        )
    diff_anchor = fm.get("diff_anchor", "")
    report = record.get("report") or {}
    report_done = report.get("status") == "done"
    gate_check = record["state"] == "running" or (
        record["state"] == "terminal" and record.get("terminal_status") == "completed"
    )
    if gate_check or report_done:
        verdict, kind, gate_detail = verify_integrate_ready(
            tid, record["attempt"], record["execution_id"], repo=repo
        )
        if verdict != "ready" and kind in {"review_scope_mismatch", "finish_tree_mismatch"}:
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
            try:
                has_successor = _tip_has_successor(tip_sha, branch, repo=repo)
            except ctx.TaskDataError as error:
                state["action"] = f"停止：后继分支判定失败（{error}）；人工核查"
                return state
            if has_successor:
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
            try:
                action = _review_repair_action(
                    tid, record["attempt"], record["execution_id"],
                    tip_sha, diff_anchor, gate_detail, branch, repo=repo,
                )
            except ctx.TaskDataError as error:
                state["action"] = f"停止：review 修复判定失败（{error}）；人工核查"
                return state
            return phase("review_repair", action)
        if verdict != "ready" and kind == "privacy":
            # tip 引入隐私泄漏：与 committed_privacy_leak 同源（变更集口径），这里是
            # 门禁视角的显式映射，不再落到 committed_unreported 让 run-close 补 report。
            locate = (
                f"用 `git log -S <泄漏路径片段> --oneline -- {branch}` 定位引入提交："
                "确属非个人环境固定路径，在 repo_task/privacy.py FIXED_PATH_EXCEPTIONS "
                "显式豁免；否则移除泄漏内容后重新 review 再收尾"
            )
            if report_done:
                state["action"] = (
                    f"停止：report 已写入 done，但实现 commit 引入隐私泄漏（{gate_detail}）；"
                    f"不可撤销、不可 amend，人工核查。{locate}"
                )
                return state
            return phase(
                "committed_privacy_leak",
                f"实现 commit {tip_sha[:12]} 引入隐私泄漏（{gate_detail}）；"
                f"HEAD 已前移不回滚。{locate}；report 未 done 且无后继分支时"
                "可用 finish-unwind 退回重做",
            )
    if not record.get("report"):
        return phase("committed_unreported", "验证最终提交和 review gate；按原 identity 补缺失的 terminal/report，不重复写已有事件")
    if record.get("terminal_status") == "completed" and report_done:
        return phase("reported_uncleaned", "按原 identity exact cleanup；脏改动或门禁失败时保留现场")
    return state


def _tip_has_successor(tip_sha: str, current_branch: str, *, repo: "ctx.RepoContext") -> bool:
    """tip 是否已被其它本地分支包含（后继分支或已合入）。"""
    result = _git(["branch", "--format=%(refname:short)"], repo=repo)
    if result.returncode != 0:
        raise ctx.TaskDataError(f"无法列出本地分支，后继判定失败：{result.stderr.strip()}")
    for branch in [line.strip() for line in result.stdout.splitlines() if line.strip()]:
        if branch == current_branch:
            continue
        if is_ancestor(tip_sha, branch, repo=repo):
            return True
    return False


def _review_repair_action(
    tid: str, attempt: int, execution_id: str, tip_sha: str, diff_anchor: str,
    detail: str, branch: str, *, repo: "ctx.RepoContext",
) -> str:
    if _tip_has_successor(tip_sha, branch, repo=repo):
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
    repo = args.repo
    print(json.dumps(recovery_state(args.tid, repo=repo), ensure_ascii=False, indent=2))


@control_locked
def cmd_ledger_import(args):
    """Recover only a proven completed execution; never invent a running owner."""
    repo = args.repo
    require_primary_worktree(repo=repo)
    if not args.confirm_stopped:
        raise ctx.TaskDataError("导入前须确认原执行会话已停止：--confirm-stopped")
    task = discover_effective_tasks(repo=repo).get(args.tid)
    if not task or task["status"] != "done" or not task.get("branch"):
        raise ctx.TaskDataError("仅可从现存 done task 分支及 committed handoff 重建；active 或缺证据须恢复 runtime 备份")
    branch = task["branch"]
    try:
        handoff = json.loads(git_text_at_ref(branch, f"{task['dir']}/handoff.json", repo=repo))
    except (ValueError, ctx.TaskDataError) as error:
        raise ctx.TaskDataError(f"拒绝导入：handoff 无法读取（{error}）") from error
    if not isinstance(handoff, dict):
        raise ctx.TaskDataError("拒绝导入：handoff 非 JSON 对象")
    attempt, execution_id = handoff.get("attempt"), handoff.get("execution_id")
    verdict, _kind, detail = verify_integrate_ready(args.tid, attempt, execution_id, repo=repo)
    if verdict != "ready":
        raise ctx.TaskDataError(f"拒绝导入：{detail}")
    worktree = (repo.primary_root / repo.worktree_rel(args.tid)).resolve()
    if str(worktree) in worktree_paths(repo=repo):
        from .git_ops import porcelain_entries
        if porcelain_entries(root=worktree, repo=repo):
            raise ctx.TaskDataError("worktree 有未提交改动；先恢复或保存，不据 Git tip 猜测现场")
    sha = _git(["rev-parse", branch], repo=repo).stdout.strip()
    from .ledger import ledger_locked_append_many

    def build(events):
        if any(event.get("tid") == args.tid for event in events):
            raise ctx.TaskDataError("已有该 tid 的 ledger 事件；拒绝覆盖或猜补部分历史，请恢复备份")
        identity = {"tid": args.tid, "attempt": attempt, "execution_id": execution_id,
                    "recovered_from": sha}
        return [{**identity, "event": "attempt_reserved", "executor": "inline", "state": "running"},
                {**identity, "event": "attempt_terminal", "status": "completed"},
                {**identity, "event": "report", "status": "done", "sha": sha}]

    ledger_locked_append_many(build, repo=repo)
    print(json.dumps({"tid": args.tid, "attempt": attempt, "execution_id": execution_id,
                      "recovered_from": sha}, ensure_ascii=False))


@control_locked
def cmd_finish_txn_clear(args):
    """显式清理已完成（committed）的 finish 提交事务（P1-2 出口）。

    unknown 保留现场、rollback_safe 交给重跑 finish 幂等回滚、committed 且 tip
    隐私核对通过才 finalize，避免清理掉一个泄漏提交的唯一现场证据。与 finish
    共用同一 txn 锁，防止清理与提交并发交错。
    """
    from .commit_txn import judge, load, txn_lock_path
    from .lifecycle import _accept_finish_txn
    from .locks import file_lock
    from .store import git_text_at_ref

    repo = args.repo
    require_primary_worktree(repo=repo)
    tid = args.tid
    worktree = (repo.primary_root / repo.worktree_rel(tid)).resolve()
    with file_lock(txn_lock_path(worktree)):
        txn = load(worktree)
        if txn is None:
            print(f"{tid} 无遗留提交事务")
            return
        if txn.get("tid") != tid:
            sys.exit(f"拒绝清理：worktree 内是 {txn.get('tid')} 的提交事务，不是 {tid}")
        verdict, detail = judge(worktree, txn)
        if verdict == "unknown":
            sys.exit(f"拒绝清理：提交结果未知（{detail}）；保留 journal 与现场，按 recovery 人工核查")
        if verdict == "rollback_safe":
            sys.exit(f"拒绝清理：HEAD 未前进（{detail}）；重跑 finish 会幂等回滚该事务，无需手工清理")
        head = _git(["rev-parse", "HEAD"], root=worktree)
        if head.returncode != 0 or not head.stdout.strip():
            sys.exit("拒绝清理：无法读取 worktree HEAD")
        head_sha = head.stdout.strip()
        # 核对 journal 对应实现 commit：tip 的执行回执身份须与 journal 一致，
        # 否则实现 commit 对应关系不明（被外部提交顶替），保留现场人工核查。
        try:
            committed_handoff = json.loads(
                git_text_at_ref(head_sha, f"{txn['task_rel']}/handoff.json", repo=repo)
            )
        except (ValueError, ctx.TaskDataError) as error:
            sys.exit(f"拒绝清理：实现 commit 缺少可核对的执行回执（{error}）；保留现场人工核查")
        from .handoff import validate_handoff
        validate_handoff(committed_handoff, source=f"{head_sha}:{txn['task_rel']}/handoff.json")
        if not isinstance(committed_handoff, dict) or any(
            committed_handoff.get(key) != txn[key]
            for key in ("tid", "attempt", "execution_id")
        ):
            sys.exit(
                "拒绝清理：tip 执行回执与该提交事务身份不符，实现 commit 对应关系不明；"
                "保留 journal 与现场，按 recovery 人工核查"
            )
        _accept_finish_txn(worktree, txn)
        print(f"{tid} 已清理已完成的提交事务 journal")
