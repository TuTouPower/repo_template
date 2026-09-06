"""Task worktree creation, cleanup, and exact-identity integration."""

import functools
import json
import os
import re
import sys
from pathlib import Path

import repo_task.context as ctx

from .attempts import (
    append_integrated,
    append_integrated_batch,
    current_attempt_record,
    require_exact_terminal,
)
from .documents import parse_front_matter, validate_task_documents, write_front_matter
from .git_ops import (
    _get_head,
    _get_head_short,
    _git,
    default_branch,
    has_unmerged_commits,
    porcelain_entries,
    require_primary_worktree,
    resolve_local_branch,
    tracked_dirty_entries,
    worktree_paths,
)
from .ledger import _ledger_append_safely, ledger_read
from .monitoring import verify_integrate_ready
from .store import (
    _local_task_branches,
    _task_branch_names,
    discover_effective_tasks,
    git_text_at_ref,
    load_task_at_ref,
    rebuild_index,
    require_status,
)
from .worktrees import (
    create_worktree,
    link_local_env,
    resolve_start_base,
    rollback_start,
    unlink_managed_env_links,
)


def _order_by_ancestry(branches: list[str]) -> list[str] | None:
    """若 branches 构成单条祖先链（A→B→C，每个后继含前驱），返回有序列表；
    否则返回 None。用 git merge-base --is-ancestor 判定。"""
    if not branches:
        return []
    # ancestors[a] = 排在 a 之前（a 的祖先）的集合；pairs 语义必须用祖先，不能是后继
    ancestors = {
        a: {b for b in branches if b != a and _is_ancestor(b, a)}
        for a in branches
    }
    heads = [a for a in branches if not ancestors[a]]
    tails = [a for a in branches if not any(a in ancestors[b] for b in branches if b != a)]
    if len(heads) != 1 or len(tails) != 1:
        return None
    # 从 head 起按祖先链顺出
    ordered = [heads[0]]
    remaining = set(branches) - {heads[0]}
    while remaining:
        # 严格单链：candidates 应为恰好一个「紧邻链尾且其全部祖先已入链」的分支
        current_tip = ordered[-1]
        direct = [
            b for b in remaining
            if _is_ancestor(current_tip, b) and ancestors[b] <= set(ordered)
        ]
        if len(direct) != 1:
            return None
        ordered.append(direct[0])
        remaining.discard(direct[0])
    return ordered


def _is_ancestor(maybe_ancestor: str, descendant: str) -> bool:
    r = _git(["merge-base", "--is-ancestor", maybe_ancestor, descendant])
    return r.returncode == 0


def _dependency_implementations(dep: str) -> list[str]:
    """Resolve implementation commits even after chain branches have been deleted.

    The archived handoff is durable provenance. Locate its creation commit on main,
    not a merge subject (chain merges intentionally have no per-task subject).
    Unknown provenance is a hard failure, never an empty, permissive dependency.
    """
    refs = [resolve_local_branch(branch)[1] for branch in _task_branch_names(dep)]
    if refs:
        return refs
    main = default_branch()
    try:
        task, fm, _ = load_task_at_ref(dep, main)
        path = f"{task['dir']}/handoff.json"
        handoff = json.loads(git_text_at_ref(main, path))
        if fm.get("status") != "done" or handoff.get("tid") != dep:
            raise ValueError("dependency is not done")
        base_sha = handoff["base_sha"]
        history = _git([
            "log", "--format=%H", "--no-merges", "--diff-filter=A",
            f"refs/heads/{main}", "--", path,
        ])
        if history.returncode != 0:
            raise ValueError(history.stderr.strip())
        for sha in history.stdout.splitlines():
            parent = _git(["rev-parse", f"{sha}^1"])
            if parent.returncode == 0 and parent.stdout.strip() == base_sha:
                if json.loads(git_text_at_ref(sha, path)) == handoff:
                    return [sha]
    except (ctx.TaskDataError, ValueError, KeyError, TypeError, AttributeError):
        pass
    raise ctx.TaskDataError(
        f"start=FAIL：{dep} 缺依赖实现的可验证 SHA；"
        "须恢复已归档 handoff/实现提交证据，不能仅凭 done 放行"
    )


def cmd_start(args):
    require_primary_worktree()
    # 前置验证 ledger 可读：创建 branch/worktree 后再追加 ledger 若因损坏
    # fail-closed 抛错，会留下完整副作用却返回非零。提前校验，失败即无副作用。
    ledger_read()
    base_arg = getattr(args, "base", None)
    base_branch, base_sha = resolve_start_base(base_arg)
    task, ref_fm, ref_task_body = load_task_at_ref(args.tid, base_sha)
    require_status(ref_fm, "backlog")
    spec_path = f"{task['dir']}/spec.md"
    try:
        spec_text = git_text_at_ref(base_sha, spec_path)
    except ctx.TaskDataError:
        sys.exit("start=FAIL：缺 spec.md")
    problems, _ = validate_task_documents(spec_text, ref_task_body)
    if problems:
        sys.exit("start=FAIL：" + "；".join(problems))

    effective = discover_effective_tasks()
    depends_on = [t for t in str(ref_fm.get("depends_on", "")).split(",") if t.strip()]
    conflicts_with = [t for t in str(ref_fm.get("conflicts_with", "")).split(",") if t.strip()]

    # 依赖硬拒（完成口径：done 即满足，不要求已合并主干；dropped 已归档不产出代码，
    # 引用 dropped 的边非法，start 对 dropped 依赖直接拒）。
    unmet = []
    for dep in depends_on:
        dep_task = effective.get(dep)
        dep_status = dep_task["status"] if dep_task else None
        if dep_status != "done":
            unmet.append(f"{dep}({dep_status or '缺失'})")
    if unmet:
        sys.exit(f"start=FAIL：{args.tid} 依赖未满足：{', '.join(unmet)}")

    # 冲突只警告：「正在运行」= 登记 worktree 存在 且 status=active。
    running_conflicts = []
    for c in conflicts_with:
        c_task = effective.get(c)
        if not c_task:
            continue
        if c_task["status"] == "active":
            c_wt = (ctx.REPO_ROOT / ctx.worktree_rel_path(c)).resolve()
            if str(c_wt) in worktree_paths():
                running_conflicts.append(c)

    # 用户未显式指定 base 时，若前置未合并主干，自动落到其分支 tip。
    # 多依赖时：所有未合并依赖必须位于同一祖先链（链式自然形态）；
    # 否则拒绝 start——手动合并前置或先 integrate 主干后再跑。
    if base_arg is None and depends_on:
        unmerged_dep_branches = []
        for dep in depends_on:
            for branch in _task_branch_names(dep):
                if has_unmerged_commits(branch):
                    unmerged_dep_branches.append(branch)
        if unmerged_dep_branches:
            chosen_branch = None
            if len(unmerged_dep_branches) == 1:
                chosen_branch = unmerged_dep_branches[0]
            else:
                ordered = _order_by_ancestry(unmerged_dep_branches)
                if ordered is None:
                    sys.exit(
                        f"start=FAIL：{args.tid} 多个未合并依赖分支不构成单条祖先链："
                        f"{', '.join(unmerged_dep_branches)}；"
                        "请先把这些前置 integrate 进主干，或显式指定 --base"
                    )
                chosen_branch = ordered[-1]
            base_branch, base_sha = resolve_start_base(chosen_branch)
            # 重读 task/spec 于新 base，确保文档校验针对实际起点。
            task, ref_fm, ref_task_body = load_task_at_ref(args.tid, base_sha)
            require_status(ref_fm, "backlog")
            try:
                spec_text = git_text_at_ref(base_sha, spec_path)
            except ctx.TaskDataError:
                sys.exit("start=FAIL：缺 spec.md")
            problems, _ = validate_task_documents(spec_text, ref_task_body)
            if problems:
                sys.exit("start=FAIL：" + "；".join(problems))

    # 实际起点（显式或自动选出的 base）须包含每个依赖的实现 SHA。
    # 用「实现是否 base 祖先」而非「是否已合入当前 main」推导——已合入 main 的
    # 依赖若合入于 base 分支 fork 之后，base 仍缺其代码（t023 合 main、t025 分支
    # fork 于旧 main 时，start t028 --base t025 会缺 t023）。
    if depends_on:
        missing_in_base = []
        for dep in depends_on:
            for ref in _dependency_implementations(dep):
                if _git(["merge-base", "--is-ancestor", ref, base_sha]).returncode != 0:
                    missing_in_base.append(f"{dep}（实现 {ref[:12]} 不在 base {base_branch!r}）")
        if missing_in_base:
            sys.exit(
                f"start=FAIL：{args.tid} base {base_branch!r} 缺依赖实现："
                f"{', '.join(missing_in_base)}；请以依赖分支为 --base 或先 integrate 前置"
            )

    branch = f"{ref_fm['tid']}_{ref_fm['slug']}"
    worktree_rel = ctx.worktree_rel_path(ref_fm["tid"])
    worktree = (ctx.REPO_ROOT / worktree_rel).resolve()
    if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode == 0:
        sys.exit(f"分支 {branch!r} 已存在；请先处理后再 start")
    if worktree.exists() or str(worktree) in worktree_paths():
        sys.exit(f"{worktree_rel} 已存在；请先处理后再 start")
    try:
        rel = create_worktree(ref_fm["tid"], branch, base_sha)
        task_path = worktree / task["dir"] / "task.md"
        fm, body = parse_front_matter(task_path)
        if fm.get("status") != "backlog":
            raise ctx.TaskDataError(f"{task['dir']}/task.md status={fm.get('status')!r}，需要 backlog")
        fm["status"] = "active"
        fm["branch"] = branch
        fm["worktree"] = worktree_rel
        fm["diff_anchor"] = base_sha
        write_front_matter(task_path, fm, body)
        linked = link_local_env(worktree)
    except (OSError, ctx.TaskDataError) as error:
        rollback_error = rollback_start(
            base_sha=base_sha, branch=branch, worktree_rel=worktree_rel
        )
        if rollback_error:
            sys.exit(
                f"start 失败（{error}）；自动补偿不完整：{rollback_error}。"
                f"请检查 {worktree_rel}、分支 {branch!r} 与主仓 HEAD 后手动恢复"
            )
        sys.exit(f"start 失败（{error}）；已清理本次新建分支与 worktree，主仓未修改")
    print(
        f"{args.tid} status=active branch={branch} base={base_branch} "
        f"diff_anchor={fm['diff_anchor']}"
    )
    print(f"工作位置：worktree {rel}")
    if linked:
        print(f"已软链本地配置：{', '.join(linked)}")
    print(f"下一步：cd {worktree_rel} 后在该工作区执行 preflight 与后续所有步骤")
    if running_conflicts:
        print(
            f"警告：{args.tid} 与正在运行的 task 冲突：{', '.join(running_conflicts)}"
            "（冲突为静态推导，已放行；合并撞车由 git 报错收场）"
        )
    _ledger_append_safely({
        "event": "start", "tid": args.tid, "branch": branch, "worktree": worktree_rel,
    })


def _resolve_integrate_branch(tid: str) -> tuple[str, str]:
    branches = _task_branch_names(tid)
    if not branches:
        raise ctx.TaskDataError(f"{tid} 没有本地 task 分支；无可合并内容")
    if len(branches) > 1:
        raise ctx.TaskDataError(f"{tid} 存在多个本地 task 分支：{', '.join(branches)}；请先处理")
    branch, sha = resolve_local_branch(branches[0])
    _, fm, _ = load_task_at_ref(tid, sha)
    expected = f"{tid}_{fm.get('slug', '')}"
    if branch != expected:
        raise ctx.TaskDataError(f"分支 {branch!r} 与 {tid} slug 不符（应为 {expected!r}）")
    if fm.get("status") not in ctx.ARCHIVED_STATUSES:
        raise ctx.TaskDataError(
            f"{tid} 在分支 {branch!r} 中 status={fm.get('status')!r}，须为 done/dropped"
        )
    return branch, sha


def _require_execution_gate(
    tid: str, attempt: int, execution_id: str, *, allow_integrated: bool = False
) -> tuple[dict, list[dict]]:
    events = ledger_read()
    record = require_exact_terminal(
        tid, attempt, execution_id, events,
        allow_integrated=allow_integrated,
    )
    if record["state"] == "terminal":
        if record["terminal_status"] != "completed":
            raise ctx.TaskDataError(
                f"{tid} attempt={attempt} terminal status={record['terminal_status']!r}，"
                "只有 completed 可 cleanup/integrate"
            )
        report = record.get("report") or {}
        if report.get("status") != "done":
            raise ctx.TaskDataError(
                f"{tid} attempt={attempt} execution_id={execution_id!r} terminal 后 report 未 done"
                f"（report status={report.get('status') or '缺失'}）；先 report"
            )
    return record, events


def _verify_exact_handoff(tid: str, attempt: int, execution_id: str) -> None:
    verdict, detail = verify_integrate_ready(tid, attempt, execution_id)
    if verdict != "ready":
        raise ctx.TaskDataError(
            f"{tid} attempt={attempt} execution_id={execution_id!r} refs/handoff 验证失败：{detail}"
        )


def cmd_cleanup_worktree(args):
    require_primary_worktree()
    if not ctx.TID_RE.fullmatch(args.tid):
        sys.exit(f"tid 非法：{args.tid!r}")
    try:
        _require_execution_gate(args.tid, args.attempt, args.execution_id)
        branch, _ = _resolve_integrate_branch(args.tid)
        _verify_exact_handoff(args.tid, args.attempt, args.execution_id)
    except ctx.TaskDataError as error:
        sys.exit(str(error))
    rel = ctx.worktree_rel_path(args.tid)
    path = (ctx.REPO_ROOT / rel).resolve()
    registered_branch = worktree_paths().get(str(path))
    if registered_branch is None:
        _git(["worktree", "prune"])
        if path.exists():
            sys.exit(f"{rel} 存在但未登记为 git worktree；拒绝删除未知内容")
        print(f"worktree 已清理：{rel}（幂等）")
        return
    if registered_branch != branch:
        sys.exit(f"{rel} 登记分支为 {registered_branch!r}，预期 {branch!r}；拒绝清理")
    current = _git(["rev-parse", "--abbrev-ref", "HEAD"], root=path)
    if current.returncode != 0 or current.stdout.strip() != branch:
        sys.exit(f"{rel} 当前分支与登记 ownership 不符；拒绝清理")
    dirty = porcelain_entries(path)
    if dirty:
        sys.exit(
            f"{rel} 有 {len(dirty)} 项未提交改动：{', '.join(dirty[:5])}；先完成 task commit"
        )
    unlink_managed_env_links(path)
    result = _git(["worktree", "remove", str(path)])
    if result.returncode != 0:
        sys.exit(f"git worktree remove 失败：{result.stderr.strip()}")
    _git(["worktree", "prune"])
    print(f"worktree 已移除：{rel}；分支 {branch!r} 保留")


def _merge_in_progress() -> bool:
    result = _git(["rev-parse", "--absolute-git-dir"])
    if result.returncode != 0 or not result.stdout.strip():
        raise ctx.TaskDataError("无法解析 git 目录，无法判断 merge 状态")
    return (Path(result.stdout.strip()) / "MERGE_HEAD").exists()


def _conflicted_paths() -> list[str]:
    result = _git(["diff", "--name-only", "--diff-filter=U"])
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _stage_indexes() -> None:
    """Rebuild derived indexes inside the pending merge; never create a commit."""
    rebuild_index()
    paths = [ctx._rel(ctx.ACTIVE_PATH), ctx._rel(ctx.ARCHIVE_PATH)]
    result = _git(["add", "--", *paths])
    if result.returncode != 0:
        raise ctx.TaskDataError(f"index git add 失败：{result.stderr.strip()}")


def _registered_for_branch(branch: str) -> list[str]:
    return [path for path, name in worktree_paths().items() if name == branch]


def _ensure_primary_merge_ready() -> None:
    if _merge_in_progress():
        raise ctx.TaskDataError("存在进行中的 merge；先继续或 git merge --abort")
    dirty = tracked_dirty_entries()
    if dirty:
        raise ctx.TaskDataError(
            f"主仓有 {len(dirty)} 项已跟踪文件未提交：{', '.join(dirty[:5])}；"
            "merge 前请先提交或还原。未跟踪文件不阻塞合并"
        )


def _delete_branches(branches: list[str]) -> None:
    base = default_branch()
    for branch in branches:
        if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode != 0:
            continue
        if _git(["merge-base", "--is-ancestor", f"refs/heads/{branch}", "HEAD"]).returncode != 0:
            raise ctx.TaskDataError(f"分支 {branch!r} 未完全合入 {base}；保留分支")
    # Delete the tail last so an interrupted cleanup can be re-entered by tail tid.
    for branch in branches:
        if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode != 0:
            continue
        result = _git(["branch", "-d", "--", branch])
        if result.returncode != 0:
            raise ctx.TaskDataError(f"删除分支 {branch!r} 失败：{result.stderr.strip()}；已保留")
        print(f"分支已删除：{branch}")


def _find_merge_commit(second_parent: str, subject_prefix: str) -> str | None:
    result = _git(["rev-list", "--merges", "--max-count=50", "HEAD"])
    if result.returncode != 0:
        return None
    for candidate in result.stdout.splitlines():
        second = _git(["rev-parse", f"{candidate}^2"])
        subject = _git(["show", "-s", "--format=%s", candidate])
        if (
            second.returncode == 0
            and second.stdout.strip() == second_parent
            and subject.returncode == 0
            and subject.stdout.strip().startswith(subject_prefix)
        ):
            return candidate
    return None


def _merge_head() -> str:
    result = _git(["rev-parse", "MERGE_HEAD"])
    if result.returncode != 0 or not result.stdout.strip():
        raise ctx.TaskDataError("当前无进行中的 merge")
    return result.stdout.strip()


def _integration_lock_path() -> Path:
    result = _git(["rev-parse", "--absolute-git-dir"])
    if result.returncode != 0 or not result.stdout.strip():
        raise ctx.TaskDataError("无法解析 git 目录")
    return Path(result.stdout.strip()) / "repo-task" / "integrate.lock"


def _lock_fh(fh, *, unlock: bool) -> None:
    fh.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_LOCK, 1)
    else:
        import fcntl
        fcntl.flock(fh, fcntl.LOCK_UN if unlock else fcntl.LOCK_EX)


def _chain_locked(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            lock_path = _integration_lock_path()
        except ctx.TaskDataError as error:
            sys.exit(str(error))
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock_fh:
            if lock_fh.tell() == 0:
                lock_fh.write("\0")
                lock_fh.flush()
            _lock_fh(lock_fh, unlock=False)
            try:
                return func(*args, **kwargs)
            finally:
                _lock_fh(lock_fh, unlock=True)
    return wrapper


def _prepare_native_merge(branch: str, message: str) -> None:
    try:
        _expected_auto_merge(branch)
    except ctx.TaskDataError as error:
        sys.exit(f"merge 尚未开始：{error}")
    result = _git(["merge", "--no-ff", "--no-commit", "-m", message, branch], timeout=120)
    if result.returncode != 0:
        conflicts = _conflicted_paths()
        if conflicts:
            print(f"merge 冲突，共 {len(conflicts)} 个文件：", file=sys.stderr)
            for path in conflicts:
                print(f"  {path}", file=sys.stderr)
            sys.exit("解决并 git add 后运行项目验证；通过后重跑 --continue，或 git merge --abort")
        if _merge_in_progress():
            sys.exit(f"merge 未完成：{result.stderr.strip()}；修复后 --continue，或 git merge --abort")
        sys.exit(f"merge 失败：{result.stderr.strip()}")
    try:
        _stage_indexes()
    except ctx.TaskDataError as error:
        sys.exit(f"合并结果已在工作区但 index 重建失败：{error}；修复后重跑 --continue 或 abort")
    print("合并结果已准备但尚未 commit。请运行合并后验证：通过后重跑 --continue；失败则 git merge --abort。")


def _expected_auto_merge(expected_head: str) -> tuple[str, set[str]]:
    """Recompute Git's merge result without touching the current index.

    merge-tree returns the synthetic tree first.  With --name-only -z, paths
    between that tree id and the first empty field are the original conflicts.
    """
    result = _git([
        "merge-tree", "--write-tree", "--name-only", "-z",
        "HEAD", expected_head,
    ], timeout=120)
    fields = result.stdout.split("\0")
    tree = fields[0] if fields else ""
    if result.returncode not in {0, 1} or not re.fullmatch(r"[0-9a-f]{40,64}", tree):
        detail = result.stderr.strip()
        if result.returncode == 129 or "unknown option" in detail.lower():
            raise ctx.TaskDataError("内容门禁需要 Git >= 2.38（merge-tree --write-tree --name-only）")
        raise ctx.TaskDataError(
            "无法重算 Git 自动合并结果，不能校验 staged 内容"
            + (f"：{detail}" if detail else "")
        )
    try:
        end = fields.index("", 1)
    except ValueError:
        end = len(fields)
    return tree, {path for path in fields[1:end] if path}


def _validate_merge_staged_scope(expected_head: str) -> None:
    merge_base = _git(["merge-base", "HEAD", expected_head])
    if merge_base.returncode != 0 or not merge_base.stdout.strip():
        raise ctx.TaskDataError("无法解析 merge-base，不能校验 staged 范围")
    # Only paths changed by the task side are allowed. A direct HEAD..tail diff
    # would also include main-only changes and make the whitelist too broad.
    expected_result = _git([
        "diff", "--name-only", "-z", merge_base.stdout.strip(), expected_head,
    ])
    staged_result = _git(["diff", "--cached", "--name-only", "-z"])
    if expected_result.returncode != 0 or staged_result.returncode != 0:
        raise ctx.TaskDataError("无法校验 merge staged 范围")
    index_paths = {ctx._rel(ctx.ACTIVE_PATH), ctx._rel(ctx.ARCHIVE_PATH)}
    expected = {path for path in expected_result.stdout.split("\0") if path}
    expected.update(index_paths)
    staged = {path for path in staged_result.stdout.split("\0") if path}
    unexpected = sorted(staged - expected)
    if unexpected:
        raise ctx.TaskDataError(
            "merge staged 范围含无关路径：" + ", ".join(unexpected[:10])
            + "；移出暂存区后再继续"
        )

    auto_tree, conflict_paths = _expected_auto_merge(expected_head)
    current_tree = _git(["write-tree"])
    if current_tree.returncode != 0 or not current_tree.stdout.strip():
        raise ctx.TaskDataError("无法读取当前 staged tree，不能校验 merge 内容")
    changed_result = _git([
        "diff", "--name-only", "-z", auto_tree, current_tree.stdout.strip(),
    ])
    if changed_result.returncode != 0:
        raise ctx.TaskDataError("无法比较 staged tree 与 Git 自动合并结果")
    changed = {path for path in changed_result.stdout.split("\0") if path}
    altered_clean_paths = sorted(changed - conflict_paths - index_paths)
    if altered_clean_paths:
        raise ctx.TaskDataError(
            "非冲突文件偏离 Git 自动合并结果："
            + ", ".join(altered_clean_paths[:10])
            + "；这些内容未被 task handoff/review 覆盖，请恢复后再继续"
        )


def _sync_indexes_after_existing_merge() -> None:
    """Recovery for a merge created outside task.py: persist derived indexes."""
    rebuild_index()
    paths = [ctx._rel(ctx.ACTIVE_PATH), ctx._rel(ctx.ARCHIVE_PATH)]
    result = _git(["add", "--", *paths])
    if result.returncode != 0:
        raise ctx.TaskDataError(f"index git add 失败：{result.stderr.strip()}")
    if _git(["diff", "--cached", "--quiet", "--", *paths]).returncode == 0:
        return
    result = _git(["commit", "-m", "chore(task): rebuild task indexes", "--", *paths], timeout=120)
    if result.returncode != 0:
        raise ctx.TaskDataError(f"已有 merge 的 index 维护 commit 失败：{result.stderr.strip()}")


def _commit_native_merge(expected_head: str, subject_prefix: str) -> str:
    if _merge_in_progress():
        conflicts = _conflicted_paths()
        if conflicts:
            raise ctx.TaskDataError(f"仍有未解决冲突：{', '.join(conflicts[:5])}")
        if _merge_head() != expected_head:
            raise ctx.TaskDataError("MERGE_HEAD 与目标分支 tip 不符")
        _stage_indexes()
        _validate_merge_staged_scope(expected_head)
        result = _git(["commit", "--no-edit"], timeout=120)
        if result.returncode != 0:
            raise ctx.TaskDataError(f"merge commit 失败：{result.stderr.strip()}")
        return _get_head()
    existing = _find_merge_commit(expected_head, subject_prefix)
    if existing is None:
        raise ctx.TaskDataError("当前无对应 pending merge 或已完成 merge commit；无法安全继续")
    return existing


@_chain_locked
def cmd_integrate(args):
    require_primary_worktree()
    try:
        record, _ = _require_execution_gate(
            args.tid, args.attempt, args.execution_id, allow_integrated=True
        )
        if record["state"] == "integrated" and not _task_branch_names(args.tid):
            print(f"{args.tid} 已 integrated，分支已清理（幂等）")
            return
        branch, sha = _resolve_integrate_branch(args.tid)
        _verify_exact_handoff(args.tid, args.attempt, args.execution_id)
        registered = _registered_for_branch(branch)
        if registered:
            raise ctx.TaskDataError(
                f"分支 {branch!r} 仍登记 worktree：{', '.join(registered)}；先 cleanup-worktree"
            )
    except ctx.TaskDataError as error:
        sys.exit(str(error))
    subject = f"merge({args.tid}):"
    already_merged = _git(["merge-base", "--is-ancestor", sha, "HEAD"]).returncode == 0
    if args.keep_branch and not args.continue_merge and not already_merged and record["state"] != "integrated":
        sys.exit("--keep-branch 只在 --continue 收尾或已合入幂等清理时生效；准备 merge 时不持久化该选项")
    if args.continue_merge:
        try:
            merge_sha = _commit_native_merge(sha, subject)
            if record["state"] != "integrated":
                append_integrated(args.tid, args.attempt, args.execution_id, merge_sha)
            if not args.keep_branch:
                _delete_branches([branch])
        except ctx.TaskDataError as error:
            sys.exit(str(error))
        print(f"integrate 完成：{args.tid} merge={merge_sha[:12]}")
        return
    if record["state"] == "integrated":
        if not args.keep_branch:
            try:
                _delete_branches([branch])
            except ctx.TaskDataError as error:
                sys.exit(str(error))
        print(f"{args.tid} 已 integrated（幂等）")
        return
    try:
        _ensure_primary_merge_ready()
    except ctx.TaskDataError as error:
        sys.exit(str(error))
    if already_merged:
        merge_sha = _find_merge_commit(sha, subject)
        if merge_sha is None:
            sys.exit(
                f"{branch} 已合入 {default_branch()}，但找不到对应的 {subject} merge commit；"
                "拒绝认领无关历史"
            )
        try:
            _sync_indexes_after_existing_merge()
            append_integrated(args.tid, args.attempt, args.execution_id, merge_sha)
            if not args.keep_branch:
                _delete_branches([branch])
        except ctx.TaskDataError as error:
            sys.exit(str(error))
        print(f"{branch} 已合入，跳过 merge；integrate 完成：merge={merge_sha[:12]}")
        return
    _prepare_native_merge(branch, f"merge({args.tid}): {branch}")


def _collect_chain(
    tail_tid: str, *, base_ref: str = "HEAD",
) -> list[tuple[str, str, str]]:
    tail_branch, tail_sha = _resolve_integrate_branch(tail_tid)
    candidates = []
    for branch in _local_task_branches():
        _, sha = resolve_local_branch(branch)
        if _git(["merge-base", "--is-ancestor", sha, tail_sha]).returncode != 0:
            continue
        # A chain merge covers only commits added relative to its first parent.
        # Retained branches integrated by an older merge are ancestors of
        # base_ref and must never be claimed by the current batch.
        if _git(["merge-base", "--is-ancestor", sha, base_ref]).returncode == 0:
            continue
        match = ctx.TASK_BRANCH_RE.fullmatch(branch)
        if match:
            candidates.append((match.group(1), branch, sha))
    if not any(branch == tail_branch for _, branch, _ in candidates):
        raise ctx.TaskDataError(f"链尾 {tail_branch!r} 不在本次 merge 覆盖范围")
    for index, left in enumerate(candidates):
        for right in candidates[index + 1:]:
            if not (_is_ancestor(left[2], right[2]) or _is_ancestor(right[2], left[2])):
                raise ctx.TaskDataError(f"链成员非线性：{left[1]!r} 与 {right[1]!r}")
    candidates = sorted(candidates, key=lambda item: sum(
        _is_ancestor(other[2], item[2]) for other in candidates if other != item
    ))
    if candidates[-1][1] != tail_branch:
        raise ctx.TaskDataError("链尾不是线性 ancestry 的最后成员")
    for previous, current in zip(candidates, candidates[1:]):
        parent = _git(["rev-parse", f"{current[2]}^1"])
        if parent.returncode != 0 or parent.stdout.strip() != previous[2]:
            raise ctx.TaskDataError(
                f"链不连续：{current[1]!r} first parent 不是 {previous[1]!r} tip"
            )
    return candidates


def _chain_continue_base(tail_tid: str, tail_sha: str) -> str:
    if _merge_in_progress():
        if _merge_head() != tail_sha:
            raise ctx.TaskDataError("MERGE_HEAD 与链尾 tip 不符")
        return "HEAD"
    subject = f"merge-chain({tail_tid}):"
    existing = _find_merge_commit(tail_sha, subject)
    if existing is None:
        raise ctx.TaskDataError("当前无对应 pending chain merge 或刚完成的 merge commit")
    return f"{existing}^1"


def _preflight_chain(tail_tid: str, *, continue_merge: bool = False) -> list[dict]:
    _, tail_sha = _resolve_integrate_branch(tail_tid)
    base_ref = _chain_continue_base(tail_tid, tail_sha) if continue_merge else "HEAD"
    chain = _collect_chain(tail_tid, base_ref=base_ref)
    events = ledger_read()
    members = []
    for tid, branch, sha in chain:
        current = current_attempt_record(tid, events)
        if current is None:
            raise ctx.TaskDataError(f"链成员 {tid} 无 current attempt")
        # Continue may re-enter after ledger write but before branch delete.
        _require_execution_gate(
            tid, current["attempt"], current["execution_id"],
            allow_integrated=continue_merge,
        )
        _verify_exact_handoff(tid, current["attempt"], current["execution_id"])
        registered = _registered_for_branch(branch)
        if registered:
            raise ctx.TaskDataError(f"链成员 {branch!r} 仍登记 worktree：{', '.join(registered)}")
        members.append({
            "tid": tid, "branch": branch, "sha": sha,
            "attempt": current["attempt"], "execution_id": current["execution_id"],
        })
    return members


@_chain_locked
def cmd_integrate_chain(args):
    require_primary_worktree()
    try:
        members = _preflight_chain(args.tail_tid, continue_merge=args.continue_merge)
    except ctx.TaskDataError as error:
        sys.exit(str(error))
    tail = members[-1]
    subject = f"merge-chain({args.tail_tid}):"
    if args.continue_merge:
        try:
            merge_sha = _commit_native_merge(tail["sha"], subject)
            append_integrated_batch(members, merge_sha)
            _delete_branches([member["branch"] for member in members])
        except ctx.TaskDataError as error:
            sys.exit(str(error))
        print(f"integrate-chain 完成：merge={merge_sha[:12]}；成员={len(members)}")
        return
    try:
        _ensure_primary_merge_ready()
    except ctx.TaskDataError as error:
        sys.exit(str(error))
    _prepare_native_merge(tail["branch"], f"merge-chain({args.tail_tid}): {tail['branch']}")
