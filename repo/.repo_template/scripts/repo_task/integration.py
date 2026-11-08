"""Task worktree creation, cleanup, and exact-identity integration."""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import sys
from pathlib import Path

import repo_task.context as ctx

from .locks import control_locked, file_lock

from .attempts import (
    append_integrated,
    append_integrated_batch,
    current_attempt_record,
    require_exact_terminal,
    resolve_identity,
)
from .documents import (
    parse_front_matter,
    parse_front_matter_text,
    validate_task_documents,
    write_front_matter,
)
from .git_ops import (
    _get_head,
    _get_head_short,
    _git,
    _git_bytes,
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
    load_task_backlog_baseline,
    require_status,
)
from .worktrees import (
    create_worktree,
    link_local_files,
    resolve_start_base,
    rollback_start,
    unlink_managed_links,
)


def _order_by_ancestry(
    branches: list[str], *, repo: "ctx.RepoContext",
) -> list[str] | None:
    """若 branches 构成单条祖先链（A→B→C，每个后继含前驱），返回有序列表；
    否则返回 None。用 git merge-base --is-ancestor 判定。"""
    if not branches:
        return []
    # ancestors[a] = 排在 a 之前（a 的祖先）的集合；pairs 语义必须用祖先，不能是后继
    ancestors = {
        a: {b for b in branches if b != a and _is_ancestor(b, a, repo=repo)}
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
            if _is_ancestor(current_tip, b, repo=repo) and ancestors[b] <= set(ordered)
        ]
        if len(direct) != 1:
            return None
        ordered.append(direct[0])
        remaining.discard(direct[0])
    return ordered


def _is_ancestor(
    maybe_ancestor: str, descendant: str, *, repo: "ctx.RepoContext",
) -> bool:
    r = _git(["merge-base", "--is-ancestor", maybe_ancestor, descendant], repo=repo)
    return r.returncode == 0


def _dependency_implementations(dep: str, *, repo: "ctx.RepoContext") -> list[str]:
    """Resolve implementation commits even after chain branches have been deleted.

    handoff.json is durable provenance. Locate the commit that added it on main,
    not a merge subject (chain merges intentionally have no per-task subject).
    The file is added under docs/tasks/ by the implementation commit and moved to
    docs/archive/tasks/ by the integrating merge commit; older history added it
    directly under docs/archive/tasks/. One pathspec covers both locations.
    Unknown provenance is a hard failure, never an empty, permissive dependency.
    """
    refs = [
        resolve_local_branch(branch, repo=repo)[1]
        for branch in _task_branch_names(dep, repo=repo)
    ]
    if refs:
        return refs
    main = default_branch(repo=repo)
    try:
        task, fm, _ = load_task_at_ref(dep, main, repo=repo)
        name = Path(task["dir"]).name
        paths = [f"docs/tasks/{name}/handoff.json", f"docs/archive/tasks/{name}/handoff.json"]
        handoff = json.loads(git_text_at_ref(main, f"{task['dir']}/handoff.json", repo=repo))
        if fm.get("status") != "done" or handoff.get("tid") != dep:
            raise ValueError("dependency is not done")
        base_sha = handoff["base_sha"]
        history = _git([
            "log", "--format=%H", "--no-merges", "--diff-filter=A",
            f"refs/heads/{main}", "--", *paths,
        ], repo=repo)
        if history.returncode != 0:
            raise ValueError(history.stderr.strip())
        for sha in history.stdout.splitlines():
            parent = _git(["rev-parse", f"{sha}^1"], repo=repo)
            if parent.returncode == 0 and parent.stdout.strip() == base_sha:
                for path in paths:
                    try:
                        if json.loads(git_text_at_ref(sha, path, repo=repo)) == handoff:
                            return [sha]
                    except (ctx.TaskDataError, ValueError):
                        continue
    except (ctx.TaskDataError, ValueError, KeyError, TypeError, AttributeError):
        pass
    raise ctx.TaskDataError(
        f"start=FAIL：{dep} 缺依赖实现的可验证 SHA；"
        "须恢复 handoff/实现提交证据，不能仅凭 done 放行"
    )


def _validate_task_dir_tree_modes(
    ref: str, task_rel: str, *, repo: "ctx.RepoContext",
) -> list[tuple[str, str]]:
    """校验 ref:task_rel 下的完整 tree 清单，仅允许 100644/100755 普通文件（A09）。"""
    listing = _git(["ls-tree", "-r", "-z", "--end-of-options", ref, "--", task_rel], repo=repo)
    if listing.returncode != 0:
        raise ctx.TaskDataError(f"无法读取 {ref}:{task_rel}：{listing.stderr.strip()}")
    entries: list[tuple[str, str]] = []
    for item in listing.stdout.split("\0"):
        if not item:
            continue
        meta, rel_path = item.split("\t", 1)
        mode = meta.split()[0]
        if mode not in ("100644", "100755"):
            raise ctx.TaskDataError(
                f"{ref}:{rel_path} 含不支持的 Git 对象模式 mode={mode}"
                "（task 目录仅允许 100644/100755 普通文件，拒绝 symlink/gitlink）"
            )
        entries.append((mode, rel_path))
    if not entries:
        raise ctx.TaskDataError(f"{ref}:{task_rel} 无可物化文件")
    return entries


def _load_validated_start_task(
    tid: str, base_sha: str, effective: dict[str, dict], *, auto_selected: bool = False,
    repo: "ctx.RepoContext",
) -> tuple[dict, dict, str, str]:
    """加载并校验待启动 backlog task 的权威定义 (task, fm, body, source_ref)。

    分离代码基座 (base_sha) 与 backlog 定义权威 (default_branch)：
    1. base_sha 中若已存在 tid，其状态必须仍为 backlog（拒绝重入已执行 task）；
    2. 通过 load_task_backlog_baseline 解析初始 backlog 定义（主干领先于分叉点时以主干为准）；
    3. 校验 task.md 与 progress.md 文档结构，并拦截主仓工作区未提交的图变更。
    """
    try:
        _, base_fm, _ = load_task_at_ref(tid, base_sha, repo=repo)
    except ctx.TaskNotFoundError:
        base_fm = None
    if base_fm is not None:
        require_status(base_fm, "backlog")

    try:
        task, ref_fm, ref_task_body, source_ref = load_task_backlog_baseline(
            tid, base_sha, repo=repo
        )
    except ctx.TaskNotFoundError:
        if tid in effective:
            sys.exit(f"start=FAIL：{tid} 在主干尚未提交；请先提交创建与调度变更后再 start")
        raise
    require_status(ref_fm, "backlog")
    if source_ref != base_sha:
        dirty_in_task = [
            p for p in porcelain_entries(repo=repo)
            if p == task["dir"] or p.startswith(task["dir"] + "/")
        ]
        if dirty_in_task:
            sys.exit(f"start=FAIL：{task['dir']} 在主干有未提交改动；请先提交后再 start")
        try:
            _validate_task_dir_tree_modes(source_ref, task["dir"], repo=repo)
        except ctx.TaskDataError as error:
            sys.exit(f"start=FAIL：{error}")
    spec_path = f"{task['dir']}/task.md"
    try:
        spec_text = git_text_at_ref(source_ref, spec_path, repo=repo)
    except ctx.TaskDataError:
        sys.exit("start=FAIL：缺 task.md")
    problems, _ = validate_task_documents(spec_text, ref_task_body, repo=repo)
    if problems:
        sys.exit("start=FAIL：" + "；".join(problems))
    current = effective.get(tid)
    if current and any(
        current.get(field, "") != ref_fm.get(field, "")
        for field in ("depends_on", "conflicts_with")
    ):
        if auto_selected:
            sys.exit("start=FAIL：自动选择的 base 调度图滞后；先整合图变更")
        sys.exit(
            "start=FAIL：base 调度图滞后于当前 task；先提交图变更并选择包含该图的 base，禁止按旧图启动"
        )
    return task, ref_fm, ref_task_body, source_ref


def _materialize_task_dir_from_ref(
    ref: str, task_rel: str, worktree: Path, *, repo: "ctx.RepoContext",
) -> None:
    """将主干 backlog 权威 ref 中的 task 目录完整物化到新创建的 worktree（A09）。"""
    entries = _validate_task_dir_tree_modes(ref, task_rel, repo=repo)
    blobs: list[tuple[str, str, bytes]] = []
    for mode, rel_path in entries:
        blob = _git_bytes(["show", f"{ref}:{rel_path}"], repo=repo)
        if blob.returncode != 0:
            raise ctx.TaskDataError(f"无法读取 {ref}:{rel_path}")
        blobs.append((mode, rel_path, blob.stdout))
    target_dir = worktree / task_rel
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    for mode, rel_path, content in blobs:
        dest = worktree / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        dest.chmod(0o755 if mode == "100755" else 0o644)


@control_locked
def cmd_start(args):
    repo = args.repo
    require_primary_worktree(repo=repo)
    from .validation import load_policy, ValidationError
    try:
        load_policy(repo.root)
    except ValidationError as error:
        sys.exit(f"start=FAIL：{error}")
    # 前置验证 ledger 可读：创建 branch/worktree 后再追加 ledger 若因损坏
    # fail-closed 抛错，会留下完整副作用却返回非零。提前校验，失败即无副作用。
    ledger_read(repo=repo)
    base_arg = getattr(args, "base", None)
    base_branch, base_sha = resolve_start_base(base_arg, repo=repo)
    effective = discover_effective_tasks(repo=repo)
    task, ref_fm, ref_task_body, source_ref = _load_validated_start_task(
        args.tid, base_sha, effective, repo=repo
    )
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
            c_wt = (repo.primary_root / repo.worktree_rel(c)).resolve()
            if str(c_wt) in worktree_paths(repo=repo):
                running_conflicts.append(c)

    # 用户未显式指定 base 时，若前置未合并主干，自动落到其分支 tip。
    # 多依赖时：所有未合并依赖必须位于同一祖先链（链式自然形态）；
    # 否则拒绝 start——手动合并前置或先 integrate 主干后再跑。
    if base_arg is None and depends_on:
        unmerged_dep_branches = []
        for dep in depends_on:
            for branch in _task_branch_names(dep, repo=repo):
                if has_unmerged_commits(branch, repo=repo):
                    unmerged_dep_branches.append(branch)
        if unmerged_dep_branches:
            chosen_branch = None
            if len(unmerged_dep_branches) == 1:
                chosen_branch = unmerged_dep_branches[0]
            else:
                ordered = _order_by_ancestry(unmerged_dep_branches, repo=repo)
                if ordered is None:
                    sys.exit(
                        f"start=FAIL：{args.tid} 多个未合并依赖分支不构成单条祖先链："
                        f"{', '.join(unmerged_dep_branches)}；"
                        "请先把这些前置 integrate 进主干，或显式指定 --base"
                    )
                chosen_branch = ordered[-1]
            base_branch, base_sha = resolve_start_base(chosen_branch, repo=repo)
            # 重读 task/spec 于新 base，确保文档校验针对实际起点。
            task, ref_fm, ref_task_body, source_ref = _load_validated_start_task(
                args.tid, base_sha, effective, auto_selected=True, repo=repo
            )

    from .validation import parse_policy
    try:
        parse_policy(git_text_at_ref(base_sha, "docs/blueprint/testing.md", repo=repo))
    except (ValidationError, ctx.TaskDataError) as error:
        sys.exit(f"start=FAIL：实际 base 的验证策略未就绪：{error}")

    # 实际起点（显式或自动选出的 base）须包含每个依赖的实现 SHA。
    # 用「实现是否 base 祖先」而非「是否已合入当前 main」推导——已合入 main 的
    # 依赖若合入于 base 分支 fork 之后，base 仍缺其代码（t023 合 main、t025 分支
    # fork 于旧 main 时，start t028 --base t025 会缺 t023）。
    if depends_on:
        missing_in_base = []
        for dep in depends_on:
            for ref in _dependency_implementations(dep, repo=repo):
                if _git(["merge-base", "--is-ancestor", ref, base_sha], repo=repo).returncode != 0:
                    missing_in_base.append(f"{dep}（实现 {ref[:12]} 不在 base {base_branch!r}）")
        if missing_in_base:
            sys.exit(
                f"start=FAIL：{args.tid} base {base_branch!r} 缺依赖实现："
                f"{', '.join(missing_in_base)}；请以依赖分支为 --base 或先 integrate 前置"
            )

    branch = f"{ref_fm['tid']}_{ref_fm['slug']}"
    worktree_rel = repo.worktree_rel(ref_fm["tid"])
    worktree = (repo.primary_root / worktree_rel).resolve()
    if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], repo=repo).returncode == 0:
        sys.exit(f"分支 {branch!r} 已存在；请先处理后再 start")
    if worktree.exists() or str(worktree) in worktree_paths(repo=repo):
        sys.exit(f"{worktree_rel} 已存在；请先处理后再 start")
    created_worktree = False
    try:
        rel = create_worktree(ref_fm["tid"], branch, base_sha, repo=repo)
        created_worktree = True
        if source_ref != base_sha:
            _materialize_task_dir_from_ref(source_ref, task["dir"], worktree, repo=repo)
        task_path = worktree / task["dir"] / ctx.PROGRESS_FILE
        fm, body = parse_front_matter(task_path)
        if fm.get("status") != "backlog":
            raise ctx.TaskDataError(f"{task['dir']}/{ctx.PROGRESS_FILE} status={fm.get('status')!r}，需要 backlog")
        fm["status"] = "active"
        fm["branch"] = branch
        fm["worktree"] = worktree_rel
        fm["diff_anchor"] = base_sha
        write_front_matter(task_path, fm, body)
        linked = link_local_files(worktree, repo=repo)
    except (OSError, ctx.TaskDataError) as error:
        rollback_error = None
        if created_worktree:
            rollback_error = rollback_start(
                base_sha=base_sha, branch=branch, worktree_rel=worktree_rel, repo=repo
            )
        else:
            try:
                branch_result = _git(
                    ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], repo=repo
                )
                if branch_result.returncode not in (0, 1):
                    raise ctx.TaskDataError(f"无法查询分支：{branch_result.stderr.strip()}")
                if (
                    worktree.exists()
                    or str(worktree) in worktree_paths(repo=repo)
                    or branch_result.returncode == 0
                ):
                    rollback_error = "创建结果未确认，已保留可能存在的 worktree 或分支"
            except (OSError, ctx.TaskDataError) as inspect_error:
                rollback_error = f"创建结果未确认，无法核实现场：{inspect_error}"
        if rollback_error:
            sys.exit(
                f"start 失败（{error}）；自动补偿不完整：{rollback_error}。"
                f"请检查 {worktree_rel}、分支 {branch!r} 与主仓 HEAD 后手动恢复"
            )
        msg = "已清理本次新建分支与 worktree，主仓未修改" if created_worktree else "主仓与外部资源未修改"
        sys.exit(f"start 失败（{error}）；{msg}")
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
    }, repo=repo)


def _resolve_integrate_branch(
    tid: str, *, repo: "ctx.RepoContext",
) -> tuple[str, str]:
    branches = _task_branch_names(tid, repo=repo)
    if not branches:
        raise ctx.TaskDataError(f"{tid} 没有本地 task 分支；无可合并内容")
    if len(branches) > 1:
        raise ctx.TaskDataError(f"{tid} 存在多个本地 task 分支：{', '.join(branches)}；请先处理")
    branch, sha = resolve_local_branch(branches[0], repo=repo)
    _, fm, _ = load_task_at_ref(tid, sha, repo=repo)
    expected = f"{tid}_{fm.get('slug', '')}"
    if branch != expected:
        raise ctx.TaskDataError(f"分支 {branch!r} 与 {tid} slug 不符（应为 {expected!r}）")
    if fm.get("status") not in ctx.ARCHIVED_STATUSES:
        raise ctx.TaskDataError(
            f"{tid} 在分支 {branch!r} 中 status={fm.get('status')!r}，须为 done/dropped"
        )
    return branch, sha


def _require_execution_gate(
    tid: str, attempt: int, execution_id: str, *, allow_integrated: bool = False,
    repo: "ctx.RepoContext",
) -> tuple[dict, list[dict]]:
    events = ledger_read(repo=repo)
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


def _verify_exact_handoff(
    tid: str, attempt: int, execution_id: str, *, repo,
) -> None:
    verdict, _kind, detail = verify_integrate_ready(
        tid, attempt, execution_id, repo=repo
    )
    if verdict != "ready":
        raise ctx.TaskDataError(
            f"{tid} attempt={attempt} execution_id={execution_id!r} refs/handoff 验证失败：{detail}"
        )


def _require_handoff_base_merged(
    tid: str, ref: str, base_ref: str, *, repo: "ctx.RepoContext",
) -> None:
    """handoff.base_sha 必须是 base_ref 的祖先；否则前驱会被单 task merge 夹带进主干。"""
    try:
        task, _, _ = load_task_at_ref(tid, ref, repo=repo)
        handoff = json.loads(git_text_at_ref(ref, f"{task['dir']}/handoff.json", repo=repo))
        base_sha = handoff["base_sha"]
    except (ctx.TaskDataError, ValueError, KeyError, TypeError) as error:
        raise ctx.TaskDataError(f"{tid} 的 handoff.base_sha 无法读取：{error}") from error
    if _git(["merge-base", "--is-ancestor", base_sha, base_ref], repo=repo).returncode != 0:
        raise ctx.TaskDataError(
            f"{tid} 的 handoff.base_sha {base_sha[:12]} 不是 {base_ref} 的祖先；"
            "先 integrate 前驱，或用 integrate-chain 整链合并"
        )


def _cleanup_worktree(
    tid: str, attempt: int, execution_id: str, *, repo: "ctx.RepoContext",
) -> str:
    """Exact cleanup of one terminal attempt's worktree; returns the result line."""
    try:
        _require_execution_gate(tid, attempt, execution_id, repo=repo)
        branch, _ = _resolve_integrate_branch(tid, repo=repo)
        _verify_exact_handoff(tid, attempt, execution_id, repo=repo)
    except ctx.TaskDataError as error:
        sys.exit(str(error))
    rel = repo.worktree_rel(tid)
    path = (repo.primary_root / rel).resolve()
    registered_branch = worktree_paths(repo=repo).get(str(path))
    if registered_branch is None:
        _git(["worktree", "prune"], repo=repo)
        if path.exists():
            sys.exit(f"{rel} 存在但未登记为 git worktree；拒绝删除未知内容")
        return f"worktree 已清理：{rel}（幂等）"
    if registered_branch != branch:
        sys.exit(f"{rel} 登记分支为 {registered_branch!r}，预期 {branch!r}；拒绝清理")
    current = _git(["rev-parse", "--abbrev-ref", "HEAD"], root=path, repo=repo)
    if current.returncode != 0 or current.stdout.strip() != branch:
        sys.exit(f"{rel} 当前分支与登记 ownership 不符；拒绝清理")
    dirty = porcelain_entries(path, repo=repo)
    if dirty:
        sys.exit(
            f"{rel} 有 {len(dirty)} 项未提交改动：{', '.join(dirty[:5])}；先完成 task commit"
        )
    unlink_managed_links(path, repo=repo)
    result = _git(["worktree", "remove", str(path)], repo=repo)
    if result.returncode != 0:
        sys.exit(f"git worktree remove 失败：{result.stderr.strip()}")
    _git(["worktree", "prune"], repo=repo)
    return f"worktree 已移除：{rel}；分支 {branch!r} 保留"


@control_locked
def cmd_cleanup_worktree(args):
    repo = args.repo
    require_primary_worktree(repo=repo)
    if not ctx.TID_RE.fullmatch(args.tid):
        sys.exit(f"tid 非法：{args.tid!r}")
    try:
        attempt, execution_id = resolve_identity(
            args.tid, args.attempt, args.execution_id, repo=repo
        )
    except ctx.TaskDataError as error:
        sys.exit(str(error))
    print(_cleanup_worktree(args.tid, attempt, execution_id, repo=repo))


def _merge_in_progress(*, repo: "ctx.RepoContext") -> bool:
    result = _git(["rev-parse", "--absolute-git-dir"], repo=repo)
    if result.returncode != 0 or not result.stdout.strip():
        raise ctx.TaskDataError("无法解析 git 目录，无法判断 merge 状态")
    return (Path(result.stdout.strip()) / "MERGE_HEAD").exists()


def _conflicted_paths(*, repo: "ctx.RepoContext") -> list[str]:
    result = _git(["diff", "--name-only", "--diff-filter=U"], repo=repo)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _stage_archive_moves(
    names: list[str], *, repo: "ctx.RepoContext",
) -> list[tuple[str, str]]:
    """把成员 task 目录 git mv 进归档目录，随 merge commit 一并提交；返回本次移动的 (源, 目标)。

    task 目录名与分支名同为 {tid}_{slug}。目标已存在且源已消失视为已归档（--continue
    重入幂等）；两者同时存在是数据冲突，拒绝。
    """
    moved = []
    for name in names:
        src_rel, dst_rel = f"docs/tasks/{name}", f"docs/archive/tasks/{name}"
        src, dst = repo.root / src_rel, repo.root / dst_rel
        if dst.exists():
            if src.exists():
                raise ctx.TaskDataError(f"{src_rel} 与 {dst_rel} 同时存在，无法归档（数据冲突）")
            continue
        if not src.is_dir():
            raise ctx.TaskDataError(f"成员 task 目录 {src_rel} 不存在，无法归档")
        dst.parent.mkdir(parents=True, exist_ok=True)
        result = _git(["mv", "--", src_rel, dst_rel], repo=repo)
        if result.returncode != 0:
            raise ctx.TaskDataError(f"归档移动失败（git mv {src_rel}）：{result.stderr.strip()}")
        moved.append((src_rel, dst_rel))
    return moved


def _registered_for_branch(branch: str, *, repo: "ctx.RepoContext") -> list[str]:
    return [path for path, name in worktree_paths(repo=repo).items() if name == branch]


def _ensure_primary_merge_ready(*, repo: "ctx.RepoContext") -> None:
    if _merge_in_progress(repo=repo):
        raise ctx.TaskDataError("存在进行中的 merge；先继续或 git merge --abort")
    dirty = tracked_dirty_entries(repo=repo)
    if dirty:
        raise ctx.TaskDataError(
            f"主仓有 {len(dirty)} 项已跟踪文件未提交：{', '.join(dirty[:5])}；"
            "merge 前请先提交或还原。未跟踪文件不阻塞合并"
        )


def _delete_branches(branches: list[str], *, repo: "ctx.RepoContext") -> None:
    base = default_branch(repo=repo)
    for branch in branches:
        if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], repo=repo).returncode != 0:
            continue
        if _git(["merge-base", "--is-ancestor", f"refs/heads/{branch}", "HEAD"], repo=repo).returncode != 0:
            raise ctx.TaskDataError(f"分支 {branch!r} 未完全合入 {base}；保留分支")
    # Delete the tail last so an interrupted cleanup can be re-entered by tail tid.
    for branch in branches:
        if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], repo=repo).returncode != 0:
            continue
        result = _git(["branch", "-d", "--", branch], repo=repo)
        if result.returncode != 0:
            raise ctx.TaskDataError(f"删除分支 {branch!r} 失败：{result.stderr.strip()}；已保留")
        print(f"分支已删除：{branch}")


def _find_merge_commit(
    second_parent: str, subject_prefix: str, *, repo: "ctx.RepoContext",
) -> str | None:
    # 优先在 second_parent 到 HEAD 的祖先路径上查找，避免受后续无关 merge 数量截断（Issue #62）
    candidates: list[str] = []
    if _git(["cat-file", "-e", f"{second_parent}^{{commit}}"], repo=repo).returncode == 0:
        res = _git(
            ["rev-list", "--merges", "--ancestry-path", f"{second_parent}..HEAD"], repo=repo
        )
        if res.returncode == 0 and res.stdout.strip():
            candidates = res.stdout.splitlines()
    if not candidates:
        res = _git(["rev-list", "--merges", "HEAD"], repo=repo)
        if res.returncode == 0 and res.stdout.strip():
            candidates = res.stdout.splitlines()

    for candidate in candidates:
        second = _git(["rev-parse", f"{candidate}^2"], repo=repo)
        subject = _git(["show", "-s", "--format=%s", candidate], repo=repo)
        if (
            second.returncode == 0
            and second.stdout.strip() == second_parent
            and subject.returncode == 0
            and subject.stdout.strip().startswith(subject_prefix)
        ):
            return candidate
    return None


def _merge_head(*, repo: "ctx.RepoContext") -> str:
    result = _git(["rev-parse", "MERGE_HEAD"], repo=repo)
    if result.returncode != 0 or not result.stdout.strip():
        raise ctx.TaskDataError("当前无进行中的 merge")
    return result.stdout.strip()


def _integration_lock_path(repo) -> Path:
    result = _git(["rev-parse", "--absolute-git-dir"], repo=repo)
    if result.returncode != 0 or not result.stdout.strip():
        raise ctx.TaskDataError("无法解析 git 目录")
    return Path(result.stdout.strip()) / "repo-task" / "integrate.lock"


def _repo_from_decorator(args, kwargs):
    repo = kwargs.get("repo")
    if repo is None and args:
        repo = getattr(args[0], "repo", None)
    if repo is None:
        raise ctx.TaskDataError("命令必须显式携带 repo 上下文")
    return repo


def _chain_locked(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            lock_path = _integration_lock_path(_repo_from_decorator(args, kwargs))
        except ctx.TaskDataError as error:
            sys.exit(str(error))
        with file_lock(lock_path):
            return func(*args, **kwargs)
    return wrapper


def _normalize_spec_checkboxes(text: str) -> str:
    return re.sub(r"^(\s*-\s*)\[[xX]\]", r"\1[ ]", text, flags=re.MULTILINE)


def _is_pure_backlog_to_done_member_conflict(
    branch: str, names: list[str], path: str, *, repo: "ctx.RepoContext",
) -> bool:
    """三方比较判断 merge 冲突是否仅源于「主干保留 backlog 初态，分支合法演进为 done 终态」（A06）。

    保护规则：
    1. 存在任何未知 front matter 字段时拒绝自动消解；
    2. 定义字段（tid, slug, title, depends_on, conflicts_with）在主干与分支必须完全相等；
    3. review_level 仅允许相等，或分支经授权 single -> full（note 含升级证据且主干未相对 merge-base 改写）；
    4. review_limit / verify_limit 仅允许相等，或分支经授权调大（note 含 budget 证据且主干未相对 merge-base 改写）；
    5. 主干 note 若非空，必须是分支 note 的精确值或 `; ` 前缀；且主干未相对 merge-base 独立改写 note；
    6. progress.md 正文：若 merge-base 存在该文件，主干正文必须等于 merge-base 正文（无主干独立正文修改）；
       若 merge-base 不存在该文件（分叉后主干新建），主干非占位正文行必须全部保留在分支正文中。
    """
    matched_name = next(
        (name for name in names if path.startswith(f"docs/tasks/{name}/")),
        None,
    )
    if matched_name is None:
        return False
    rel = path[len(f"docs/tasks/{matched_name}/"):]
    if rel not in (ctx.PROGRESS_FILE, "task.md"):
        return False
    progress_path = f"docs/tasks/{matched_name}/{ctx.PROGRESS_FILE}"
    try:
        head_fm, head_body = parse_front_matter_text(
            git_text_at_ref("HEAD", progress_path, repo=repo), source=f"HEAD:{progress_path}"
        )
        branch_fm, branch_body = parse_front_matter_text(
            git_text_at_ref(branch, progress_path, repo=repo), source=f"{branch}:{progress_path}"
        )
    except ctx.TaskDataError:
        return False
    if head_fm.get("status") != "backlog" or branch_fm.get("status") not in ctx.ARCHIVED_STATUSES:
        return False
    if (set(head_fm.keys()) | set(branch_fm.keys())) - set(ctx.FRONT_MATTER_KEYS):
        return False
    if any(head_fm.get(key, "") != "" for key in ("branch", "worktree", "diff_anchor")):
        return False
    if any(
        head_fm.get(key, "") != branch_fm.get(key, "")
        for key in ("tid", "slug", "title", "depends_on", "conflicts_with")
    ):
        return False

    base_fm = None
    base_body = None
    mb = _git(["merge-base", "HEAD", branch], repo=repo)
    if mb.returncode == 0 and mb.stdout.strip():
        mb_sha = mb.stdout.strip()
        try:
            base_fm, base_body = parse_front_matter_text(
                git_text_at_ref(mb_sha, progress_path, repo=repo), source=f"{mb_sha}:{progress_path}"
            )
        except ctx.TaskDataError:
            base_fm, base_body = None, None

    branch_note = branch_fm.get("note", "")
    head_level = head_fm.get("review_level", "full")
    branch_level = branch_fm.get("review_level", "full")
    if branch_level != head_level:
        if not (
            head_level == "single"
            and branch_level == "full"
            and "review single -> full:" in branch_note
            and (base_fm is None or head_level == base_fm.get("review_level", "full"))
        ):
            return False

    for limit_field in ("review_limit", "verify_limit"):
        head_lim = str(head_fm.get(limit_field, "5"))
        branch_lim = str(branch_fm.get(limit_field, "5"))
        if branch_lim != head_lim:
            if not (head_lim.isdigit() and branch_lim.isdigit()):
                return False
            if not (
                int(branch_lim) > int(head_lim)
                and f"{limit_field}: {head_lim}->{branch_lim}" in branch_note
                and (base_fm is None or head_lim == str(base_fm.get(limit_field, "5")))
            ):
                return False

    head_note = head_fm.get("note", "")
    if head_note and not (branch_note == head_note or branch_note.startswith(head_note + "; ")):
        return False
    if base_fm is not None and head_note != base_fm.get("note", ""):
        if not (branch_note == head_note or branch_note.startswith(head_note + "; ")):
            return False

    if rel == ctx.PROGRESS_FILE:
        if base_body is not None:
            return head_body == base_body or head_body == branch_body
        branch_lines = {line.strip() for line in branch_body.splitlines() if line.strip()}
        for raw_line in head_body.splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped == "无" or "待执行时填写" in stripped:
                continue
            if stripped not in branch_lines:
                return False
        return True

    try:
        head_spec = git_text_at_ref("HEAD", path, repo=repo)
        branch_spec = git_text_at_ref(branch, path, repo=repo)
    except ctx.TaskDataError:
        return False
    return _normalize_spec_checkboxes(head_spec) == _normalize_spec_checkboxes(branch_spec)


def _main_touched_member_paths(
    branch: str, names: list[str], *, repo: "ctx.RepoContext",
) -> list[str]:
    """返回主干 HEAD 相对 merge-base(HEAD, branch) 在待归档成员目录下新增或修改的路径。"""
    mb = _git(["merge-base", "HEAD", branch], repo=repo)
    if mb.returncode != 0 or not mb.stdout.strip():
        return []
    prefixes = [f"docs/tasks/{name}" for name in names]
    diff = _git(
        ["diff", "--name-only", "-z", mb.stdout.strip(), "HEAD", "--", *prefixes], repo=repo
    )
    if diff.returncode != 0:
        return []
    return [p for p in diff.stdout.split("\0") if p]


def _resolve_backlog_member_conflicts(
    branch: str, names: list[str], conflicts: list[str], *, repo: "ctx.RepoContext",
) -> list[str]:
    """对待归档成员目录内纯属 backlog -> done 状态迁移的伪冲突收敛为分支终态；
    若主干对成员目录有未报 Git 行级冲突但违反三方保护规则的独立修改，同样标记为冲突阻断（A06）。
    """
    for path in conflicts:
        if not _is_pure_backlog_to_done_member_conflict(branch, names, path, repo=repo):
            continue
        checkout = _git(["checkout", "--theirs", "--", path], repo=repo)
        if checkout.returncode == 0:
            _git(["add", "--", path], repo=repo)
    remaining = set(_conflicted_paths(repo=repo))
    for path in _main_touched_member_paths(branch, names, repo=repo):
        if path in remaining:
            continue
        if _is_pure_backlog_to_done_member_conflict(branch, names, path, repo=repo):
            checkout = _git(["checkout", "--theirs", "--", path], repo=repo)
            if checkout.returncode == 0:
                _git(["add", "--", path], repo=repo)
        else:
            remaining.add(path)
    return sorted(remaining)


def _prepare_native_merge(
    branch: str, message: str, names: list[str], *, repo: "ctx.RepoContext",
) -> None:
    try:
        _expected_auto_merge(branch, repo=repo)
    except ctx.TaskDataError as error:
        sys.exit(f"merge 尚未开始：{error}")
    result = _git(
        ["merge", "--no-ff", "--no-commit", "-m", message, branch], timeout=120, repo=repo
    )
    initial_conflicts = _conflicted_paths(repo=repo)
    if result.returncode != 0 and not initial_conflicts:
        if _merge_in_progress(repo=repo):
            sys.exit(f"merge 未完成：{result.stderr.strip()}；修复后 --continue，或 git merge --abort")
        sys.exit(f"merge 失败：{result.stderr.strip()}")
    conflicts = _resolve_backlog_member_conflicts(branch, names, initial_conflicts, repo=repo)
    if conflicts:
        print(f"merge 冲突，共 {len(conflicts)} 个文件：", file=sys.stderr)
        for path in conflicts:
            print(f"  {path}", file=sys.stderr)
        sys.exit("解决并 git add 后运行项目验证；通过后重跑 --continue，或 git merge --abort")
    try:
        _stage_archive_moves(names, repo=repo)
    except ctx.TaskDataError as error:
        sys.exit(f"合并结果已在工作区但归档移动或 index 重建失败：{error}；修复后重跑 --continue 或 abort")
    print("合并结果已准备但尚未 commit。请运行合并后验证：通过后重跑 --continue；失败则 git merge --abort。")


def _expected_auto_merge(
    expected_head: str, *, repo: "ctx.RepoContext",
) -> tuple[str, set[str]]:
    """Recompute Git's merge result without touching the current index.

    merge-tree returns the synthetic tree first.  With --name-only -z, paths
    between that tree id and the first empty field are the original conflicts.
    """
    result = _git([
        "merge-tree", "--write-tree", "--name-only", "-z",
        "HEAD", expected_head,
    ], timeout=120, repo=repo)
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


def _tree_entries(
    tree: str, directory: str, *, repo: "ctx.RepoContext",
) -> dict[str, tuple[str, str]]:
    """tree 中 directory 下的 {相对路径: (mode, oid)}。"""
    result = _git(["ls-tree", "-r", "-z", tree, "--", directory], repo=repo)
    if result.returncode != 0:
        raise ctx.TaskDataError(f"无法读取 tree 中的 {directory}：{result.stderr.strip()}")
    entries = {}
    for item in result.stdout.split("\0"):
        if item:
            meta, path = item.split("\t", 1)
            mode, _, oid = meta.split()
            entries[path[len(directory) + 1:]] = (mode, oid)
    return entries


def _archive_move_deviations(
    auto_tree: str, current_tree: str, conflict_paths: set[str], names: list[str],
    *, repo: "ctx.RepoContext",
) -> tuple[set[str], list[str]]:
    """校验成员目录是否恰为 Git 自动合并结果里活跃目录的纯移动。

    返回 (豁免路径集合, 偏离路径列表)。活跃目录必须清空；归档目录的每个文件必须与
    自动合并结果中的同名文件内容一致（原始冲突路径允许人工解决内容）。
    """
    exempt: set[str] = set()
    deviations: list[str] = []
    for name in names:
        src, dst = f"docs/tasks/{name}", f"docs/archive/tasks/{name}"
        before = _tree_entries(auto_tree, src, repo=repo)
        if _tree_entries(auto_tree, dst, repo=repo):
            deviations.append(dst)
            continue
        left = _tree_entries(current_tree, src, repo=repo)
        moved = _tree_entries(current_tree, dst, repo=repo)
        exempt.update(f"{src}/{rel}" for rel in before.keys() | left.keys())
        exempt.update(f"{dst}/{rel}" for rel in moved)
        deviations.extend(f"{src}/{rel}" for rel in left)
        for rel, entry in moved.items():
            if f"{src}/{rel}" not in conflict_paths and before.get(rel) != entry:
                deviations.append(f"{dst}/{rel}")
        deviations.extend(
            f"{src}/{rel}" for rel in before
            if rel not in moved and f"{src}/{rel}" not in conflict_paths
        )
    return exempt, sorted(set(deviations))


def _validate_merge_staged_scope(
    expected_head: str, names: list[str], *, repo: "ctx.RepoContext",
) -> None:
    merge_base = _git(["merge-base", "HEAD", expected_head], repo=repo)
    if merge_base.returncode != 0 or not merge_base.stdout.strip():
        raise ctx.TaskDataError("无法解析 merge-base，不能校验 staged 范围")
    # Only paths changed by the task side are allowed. A direct HEAD..tail diff
    # would also include main-only changes and make the whitelist too broad.
    expected_result = _git([
        "diff", "--no-renames", "--name-only", "-z", merge_base.stdout.strip(), expected_head,
    ], repo=repo)
    staged_result = _git(
        ["diff", "--no-renames", "--cached", "--name-only", "-z"], repo=repo
    )
    if expected_result.returncode != 0 or staged_result.returncode != 0:
        raise ctx.TaskDataError("无法校验 merge staged 范围")
    member_prefixes = tuple(
        f"{base}/{name}/" for name in names for base in ("docs/tasks", "docs/archive/tasks")
    )
    expected = {path for path in expected_result.stdout.split("\0") if path}
    staged = {path for path in staged_result.stdout.split("\0") if path}
    unexpected = sorted(
        path for path in staged - expected if not path.startswith(member_prefixes)
    )
    if unexpected:
        raise ctx.TaskDataError(
            "merge staged 范围含无关路径：" + ", ".join(unexpected[:10])
            + "；移出暂存区后再继续"
        )

    auto_tree, conflict_paths = _expected_auto_merge(expected_head, repo=repo)
    current_tree = _git(["write-tree"], repo=repo)
    if current_tree.returncode != 0 or not current_tree.stdout.strip():
        raise ctx.TaskDataError("无法读取当前 staged tree，不能校验 merge 内容")
    changed_result = _git([
        "diff", "--no-renames", "--name-only", "-z", auto_tree, current_tree.stdout.strip(),
    ], repo=repo)
    if changed_result.returncode != 0:
        raise ctx.TaskDataError("无法比较 staged tree 与 Git 自动合并结果")
    changed = {path for path in changed_result.stdout.split("\0") if path}
    archive_exempt, archive_deviations = _archive_move_deviations(
        auto_tree, current_tree.stdout.strip(), conflict_paths, names, repo=repo,
    )
    altered_clean_paths = sorted(
        (changed - conflict_paths - archive_exempt) | set(archive_deviations)
    )
    if altered_clean_paths:
        raise ctx.TaskDataError(
            "非冲突文件偏离 Git 自动合并结果："
            + ", ".join(altered_clean_paths[:10])
            + "；这些内容未被 task handoff/review 覆盖，请恢复后再继续"
        )


def _sync_archive_after_existing_merge(
    names: list[str], *, repo: "ctx.RepoContext",
) -> None:
    """Recovery for a merge created outside task.py: archive members in one commit."""
    moved = _stage_archive_moves(names, repo=repo)
    paths = [path for pair in moved for path in pair]
    if not paths or _git(
        ["diff", "--cached", "--quiet", "--", *paths], repo=repo
    ).returncode == 0:
        return
    result = _git(
        ["commit", "-m", "chore(task): archive completed task dirs", "--", *paths],
        timeout=120, repo=repo,
    )
    if result.returncode != 0:
        raise ctx.TaskDataError(f"已有 merge 的归档与 index 维护 commit 失败：{result.stderr.strip()}")


def _commit_native_merge(
    expected_head: str, subject_prefix: str, names: list[str], *, repo: "ctx.RepoContext",
) -> str:
    if _merge_in_progress(repo=repo):
        conflicts = _conflicted_paths(repo=repo)
        if conflicts:
            raise ctx.TaskDataError(f"仍有未解决冲突：{', '.join(conflicts[:5])}")
        if _merge_head(repo=repo) != expected_head:
            raise ctx.TaskDataError("MERGE_HEAD 与目标分支 tip 不符")
        _stage_archive_moves(names, repo=repo)
        _validate_merge_staged_scope(expected_head, names, repo=repo)
        result = _git(["commit", "--no-edit"], timeout=120, repo=repo)
        if result.returncode != 0:
            raise ctx.TaskDataError(f"merge commit 失败：{result.stderr.strip()}")
        return _get_head(repo=repo)
    existing = _find_merge_commit(expected_head, subject_prefix, repo=repo)
    if existing is None:
        raise ctx.TaskDataError("当前无对应 pending merge 或已完成 merge commit；无法安全继续")
    return existing


@_chain_locked
@control_locked
def cmd_integrate(args):
    repo = args.repo
    require_primary_worktree(repo=repo)
    try:
        attempt, execution_id = resolve_identity(
            args.tid, args.attempt, args.execution_id, repo=repo
        )
        record, _ = _require_execution_gate(
            args.tid, attempt, execution_id, allow_integrated=True, repo=repo
        )
        if record["state"] == "integrated" and not _task_branch_names(args.tid, repo=repo):
            print(f"{args.tid} 已 integrated，分支已清理（幂等）")
            return
        branch, sha = _resolve_integrate_branch(args.tid, repo=repo)
        _verify_exact_handoff(args.tid, attempt, execution_id, repo=repo)
        registered = _registered_for_branch(branch, repo=repo)
        if registered:
            raise ctx.TaskDataError(
                f"分支 {branch!r} 仍登记 worktree：{', '.join(registered)}；先 run-close --result done（或 cleanup-worktree）"
            )
    except ctx.TaskDataError as error:
        sys.exit(str(error))
    subject = f"merge({args.tid}):"
    already_merged = _git(["merge-base", "--is-ancestor", sha, "HEAD"], repo=repo).returncode == 0
    if args.keep_branch and not args.continue_merge and not already_merged and record["state"] != "integrated":
        sys.exit("--keep-branch 只在 --continue 收尾或已合入幂等清理时生效；准备 merge 时不持久化该选项")
    if args.continue_merge:
        try:
            merge_sha = _commit_native_merge(sha, subject, [branch], repo=repo)
            if record["state"] != "integrated":
                append_integrated(args.tid, attempt, execution_id, merge_sha, repo=repo)
            if not args.keep_branch:
                _delete_branches([branch], repo=repo)
        except ctx.TaskDataError as error:
            sys.exit(str(error))
        print(f"integrate 完成：{args.tid} merge={merge_sha[:12]}")
        return
    if record["state"] == "integrated":
        if not args.keep_branch:
            try:
                _delete_branches([branch], repo=repo)
            except ctx.TaskDataError as error:
                sys.exit(str(error))
        print(f"{args.tid} 已 integrated（幂等）")
        return
    try:
        _ensure_primary_merge_ready(repo=repo)
    except ctx.TaskDataError as error:
        sys.exit(str(error))
    if already_merged:
        merge_sha = _find_merge_commit(sha, subject, repo=repo)
        if merge_sha is None:
            sys.exit(
                f"{branch} 已合入 {default_branch(repo=repo)}，但找不到对应的 {subject} merge commit；"
                "拒绝认领无关历史"
            )
        try:
            _sync_archive_after_existing_merge([branch], repo=repo)
            append_integrated(args.tid, attempt, execution_id, merge_sha, repo=repo)
            if not args.keep_branch:
                _delete_branches([branch], repo=repo)
        except ctx.TaskDataError as error:
            sys.exit(str(error))
        print(f"{branch} 已合入，跳过 merge；integrate 完成：merge={merge_sha[:12]}")
        return
    try:
        _require_handoff_base_merged(args.tid, sha, "HEAD", repo=repo)
    except ctx.TaskDataError as error:
        sys.exit(str(error))
    _prepare_native_merge(branch, f"merge({args.tid}): {branch}", [branch], repo=repo)


def _collect_chain(
    tail_tid: str, *, base_ref: str = "HEAD", require_base_merged: bool = True,
    repo: "ctx.RepoContext",
) -> list[tuple[str, str, str]]:
    tail_branch, tail_sha = _resolve_integrate_branch(tail_tid, repo=repo)
    candidates = []
    for branch in _local_task_branches(repo=repo):
        _, sha = resolve_local_branch(branch, repo=repo)
        if _git(["merge-base", "--is-ancestor", sha, tail_sha], repo=repo).returncode != 0:
            continue
        # A chain merge covers only commits added relative to its first parent.
        # Retained branches integrated by an older merge are ancestors of
        # base_ref and must never be claimed by the current batch.
        if _git(["merge-base", "--is-ancestor", sha, base_ref], repo=repo).returncode == 0:
            continue
        match = ctx.TASK_BRANCH_RE.fullmatch(branch)
        if match:
            candidates.append((match.group(1), branch, sha))
    if not any(branch == tail_branch for _, branch, _ in candidates):
        raise ctx.TaskDataError(f"链尾 {tail_branch!r} 不在本次 merge 覆盖范围")
    for index, left in enumerate(candidates):
        for right in candidates[index + 1:]:
            if not (
                _is_ancestor(left[2], right[2], repo=repo)
                or _is_ancestor(right[2], left[2], repo=repo)
            ):
                raise ctx.TaskDataError(f"链成员非线性：{left[1]!r} 与 {right[1]!r}")
    candidates = sorted(candidates, key=lambda item: sum(
        _is_ancestor(other[2], item[2], repo=repo) for other in candidates if other != item
    ))
    if candidates[-1][1] != tail_branch:
        raise ctx.TaskDataError("链尾不是线性 ancestry 的最后成员")
    if require_base_merged:
        _require_handoff_base_merged(candidates[0][0], candidates[0][2], base_ref, repo=repo)
    for previous, current in zip(candidates, candidates[1:]):
        parent = _git(["rev-parse", f"{current[2]}^1"], repo=repo)
        if parent.returncode != 0 or parent.stdout.strip() != previous[2]:
            raise ctx.TaskDataError(
                f"链不连续：{current[1]!r} 的 first parent 不是 {previous[1]!r} tip"
            )
    return candidates


def _chain_continue_base(
    tail_tid: str, tail_sha: str, *, repo: "ctx.RepoContext",
) -> str:
    if _merge_in_progress(repo=repo):
        if _merge_head(repo=repo) != tail_sha:
            raise ctx.TaskDataError("MERGE_HEAD 与链尾 tip 不符")
        return "HEAD"
    subject = f"merge-chain({tail_tid}):"
    existing = _find_merge_commit(tail_sha, subject, repo=repo)
    if existing is None:
        raise ctx.TaskDataError("当前无对应 pending chain merge 或刚完成的 merge commit")
    return f"{existing}^1"


def _preflight_chain(
    tail_tid: str, *, continue_merge: bool = False, repo: "ctx.RepoContext",
) -> list[dict]:
    _, tail_sha = _resolve_integrate_branch(tail_tid, repo=repo)
    base_ref = (
        _chain_continue_base(tail_tid, tail_sha, repo=repo) if continue_merge else "HEAD"
    )
    # 已完成的 chain merge 重入只做清理/补记，不再准备新 merge，基座祖先检查无防护对象。
    require_base_merged = not (continue_merge and not _merge_in_progress(repo=repo))
    chain = _collect_chain(
        tail_tid, base_ref=base_ref, require_base_merged=require_base_merged, repo=repo
    )
    events = ledger_read(repo=repo)
    members = []
    for tid, branch, sha in chain:
        current = current_attempt_record(tid, events)
        if current is None:
            raise ctx.TaskDataError(f"链成员 {tid} 无 current attempt")
        # Continue may re-enter after ledger write but before branch delete.
        _require_execution_gate(
            tid, current["attempt"], current["execution_id"],
            allow_integrated=continue_merge, repo=repo,
        )
        _verify_exact_handoff(
            tid, current["attempt"], current["execution_id"], repo=repo
        )
        registered = _registered_for_branch(branch, repo=repo)
        if registered:
            raise ctx.TaskDataError(f"链成员 {branch!r} 仍登记 worktree：{', '.join(registered)}")
        members.append({
            "tid": tid, "branch": branch, "sha": sha,
            "attempt": current["attempt"], "execution_id": current["execution_id"],
        })
    return members


@_chain_locked
@control_locked
def cmd_integrate_chain(args):
    repo = args.repo
    require_primary_worktree(repo=repo)
    try:
        members = _preflight_chain(
            args.tail_tid, continue_merge=args.continue_merge, repo=repo
        )
    except ctx.TaskDataError as error:
        sys.exit(str(error))
    tail = members[-1]
    subject = f"merge-chain({args.tail_tid}):"
    if args.continue_merge:
        try:
            merge_sha = _commit_native_merge(
                tail["sha"], subject, [member["branch"] for member in members], repo=repo,
            )
            append_integrated_batch(members, merge_sha, repo=repo)
            _delete_branches([member["branch"] for member in members], repo=repo)
        except ctx.TaskDataError as error:
            sys.exit(str(error))
        print(f"integrate-chain 完成：merge={merge_sha[:12]}；成员={len(members)}")
        return
    try:
        _ensure_primary_merge_ready(repo=repo)
    except ctx.TaskDataError as error:
        sys.exit(str(error))
    _prepare_native_merge(
        tail["branch"], f"merge-chain({args.tail_tid}): {tail['branch']}",
        [member["branch"] for member in members], repo=repo,
    )
