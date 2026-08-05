"""Canonical integration implementation for the task toolchain."""

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

from .documents import parse_front_matter, validate_task_documents, write_front_matter
from .git_ops import _get_head, _get_head_short, _git, default_branch, porcelain_entries, require_primary_worktree, resolve_local_branch, tracked_dirty_entries, worktree_paths
from .ledger import (
    _ledger_append_safely,
    current_attempt,
    dispatch_for_attempt,
    invalid_overlapping_attempts,
    latest_worker_terminal,
    ledger_read,
)
from .monitoring import verify_integrate_ready
from .store import _local_task_branches, _task_branch_names, git_text_at_ref, load_task_at_ref, rebuild_index, require_status
from .worktrees import create_worktree, link_local_env, resolve_start_base, rollback_start, unlink_managed_env_links

def cmd_start(args):
    require_primary_worktree()
    base_branch, base_sha = resolve_start_base(getattr(args, "base", None))
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
            raise ctx.TaskDataError(
                f"{task['dir']}/task.md status={fm.get('status')!r}，需要 backlog"
            )
        fm["status"] = "active"
        fm["branch"] = branch
        fm["worktree"] = worktree_rel
        fm["diff_anchor"] = base_sha
        write_front_matter(task_path, fm, body)
        linked = link_local_env(worktree)
    except (OSError, ctx.TaskDataError) as e:
        rollback_error = rollback_start(
            base_sha=base_sha,
            branch=branch,
            worktree_rel=worktree_rel,
        )
        if rollback_error:
            sys.exit(
                f"start 失败（{e}）；自动补偿不完整：{rollback_error}。"
                f"请检查 {worktree_rel}、分支 {branch!r} 与主仓 HEAD 后手动恢复"
            )
        sys.exit(f"start 失败（{e}）；已清理本次新建分支与 worktree，主仓未修改")

    print(
        f"{args.tid} status=active branch={branch} base={base_branch} "
        f"diff_anchor={fm['diff_anchor']}"
    )
    print(f"工作位置：worktree {rel}")
    if linked:
        print(f"已软链本地配置：{', '.join(linked)}")
    print(f"下一步：cd {worktree_rel} 后在该工作区执行 preflight 与后续所有步骤")
    _ledger_append_safely({
        "event": "start",
        "tid": args.tid,
        "branch": branch,
        "worktree": worktree_rel,
    })

def _require_parallel_attempt_gate(tid: str, attempt: int | None) -> int | None:
    """Require explicit current attempt and matching worker terminal for dispatched tasks."""
    events = ledger_read()
    current = current_attempt(tid, events)
    if current is None:
        return None
    if attempt is None:
        sys.exit(
            f"{tid} 已存在 dispatch 账本；cleanup/integrate 必须显式给 --attempt"
        )
    if attempt != current:
        sys.exit(
            f"{tid} attempt={attempt} 不是当前有效 attempt={current}；拒绝使用旧 attempt"
        )
    dispatch = dispatch_for_attempt(tid, attempt, events)
    terminal = latest_worker_terminal(tid, attempt, events)
    if dispatch is None:
        sys.exit(f"{tid} attempt={attempt} 无匹配 dispatch")
    if terminal is None:
        sys.exit(
            f"{tid} attempt={attempt} 尚无 worker_terminal；等待 worker 进入终态后再 cleanup/integrate"
        )
    if not dispatch.get("worker_id") or terminal.get("worker_id") != dispatch.get("worker_id"):
        sys.exit(
            f"{tid} attempt={attempt} worker_terminal worker_id 与 dispatch 不匹配；拒绝继续"
        )
    if attempt in invalid_overlapping_attempts(tid, events):
        sys.exit(
            f"{tid} attempt={attempt} 存在非法重叠 dispatch；旧 attempt 未先 worker_terminal"
        )
    return attempt


def cmd_cleanup_worktree(args):
    """从主仓清理已提交的 task worktree，保留分支。"""
    require_primary_worktree()
    if not ctx.TID_RE.fullmatch(args.tid):
        sys.exit(f"tid 非法：{args.tid!r}")
    _require_parallel_attempt_gate(args.tid, getattr(args, "attempt", None))
    rel = ctx.worktree_rel_path(args.tid)
    path = (ctx.REPO_ROOT / rel).resolve()
    paths = worktree_paths()
    registered_branch = paths.get(str(path))
    if registered_branch is None:
        _git(["worktree", "prune"])
        if path.exists():
            sys.exit(f"{rel} 存在但未登记为 git worktree；拒绝删除未知内容")
        print(f"worktree 已清理：{rel}（幂等）")
        return
    if not ctx.TASK_BRANCH_RE.fullmatch(registered_branch) or not registered_branch.startswith(f"{args.tid}_"):
        sys.exit(
            f"{rel} 登记分支为 {registered_branch!r}，不属于 {args.tid}；拒绝清理"
        )
    try:
        _, ref_fm, _ = load_task_at_ref(args.tid, registered_branch)
    except ctx.TaskDataError as e:
        sys.exit(f"{rel} 登记分支 {registered_branch!r} 中无法读取 {args.tid}：{e}")
    expected_branch = f"{args.tid}_{ref_fm.get('slug', '')}"
    if registered_branch != expected_branch:
        sys.exit(
            f"{rel} 登记分支 {registered_branch!r} 与 {args.tid} slug 不符"
            f"（应为 {expected_branch!r}）；拒绝清理"
        )
    ref_status = ref_fm.get("status", "")
    if ref_status not in ctx.ARCHIVED_STATUSES:
        sys.exit(
            f"{args.tid} 在分支 {registered_branch!r} 中 status={ref_status!r}，"
            f"须为 done/dropped；finish/drop 并提交后才能 cleanup-worktree"
        )
    dirty = porcelain_entries(path)
    if dirty:
        sys.exit(
            f"{rel} 有 {len(dirty)} 项未提交改动：{', '.join(dirty[:5])}；"
            "先完成 task commit"
        )
    unlink_managed_env_links(path)
    r = _git(["worktree", "remove", str(path)])
    if r.returncode != 0:
        sys.exit(f"git worktree remove 失败：{r.stderr.strip()}")
    _git(["worktree", "prune"])
    print(f"worktree 已移除：{rel}；分支 {registered_branch!r} 保留")

def _merge_in_progress() -> bool:
    """MERGE_HEAD 是否存在。git-dir 取绝对路径，避免按调用方 cwd 解析。"""
    r = _git(["rev-parse", "--absolute-git-dir"])
    if r.returncode != 0 or not r.stdout.strip():
        raise ctx.TaskDataError("无法解析 git 目录，无法判断 merge 状态")
    return (Path(r.stdout.strip()) / "MERGE_HEAD").exists()

def _conflicted_paths() -> list[str]:
    r = _git(["diff", "--name-only", "--diff-filter=U"])
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]

def _resolve_integrate_branch(tid: str) -> tuple[str, str]:
    """定位 task 分支并校验其 tip 中该 task 已终态。"""
    branches = _task_branch_names(tid)
    if not branches:
        raise ctx.TaskDataError(f"{tid} 没有本地 task 分支；无可合并内容")
    if len(branches) > 1:
        raise ctx.TaskDataError(
            f"{tid} 存在多个本地 task 分支：{', '.join(branches)}；请先处理"
        )
    branch, sha = resolve_local_branch(branches[0])
    _, ref_fm, _ = load_task_at_ref(tid, sha)
    expected = f"{tid}_{ref_fm.get('slug', '')}"
    if branch != expected:
        raise ctx.TaskDataError(
            f"分支 {branch!r} 与 {tid} slug 不符（应为 {expected!r}）"
        )
    status = ref_fm.get("status", "")
    if status not in ctx.ARCHIVED_STATUSES:
        raise ctx.TaskDataError(
            f"{tid} 在分支 {branch!r} 中 status={status!r}，须为 done/dropped"
        )
    return branch, sha

def _commit_index() -> None:
    """重建派生 index 并单独成 commit；无变化则跳过。"""
    rebuild_index()
    paths = [ctx._rel(ctx.ACTIVE_PATH), ctx._rel(ctx.ARCHIVE_PATH)]
    _git(["add", "--", *paths])
    if _git(["diff", "--cached", "--quiet", "--", *paths]).returncode == 0:
        print("index 无变化，跳过维护 commit")
        return
    r = _git(["commit", "-m", "chore(task): rebuild task indexes", "--", *paths])
    if r.returncode != 0:
        raise ctx.TaskDataError(f"index commit 失败：{r.stderr.strip()}")
    print(f"index 维护 commit：{_get_head_short()}")

def _resolve_chain(tail_branch: str, tail_sha: str) -> list[tuple[str, str]]:
    """收集链尾分支的祖先链中全部未合并 task 分支，按祖先顺序返回 [(branch, sha)]。

    用于串行链式（task-run）的 integrate --chain：只合链尾一次，祖先自动跟随。
    要求每个分支 tip 中对应 task 均为 done/dropped，且无 worktree 登记。
    """
    chain: list[tuple[str, str]] = []
    for branch in _local_task_branches():
        if branch == tail_branch:
            continue
        # 候选分支是链尾祖先，且未合入主干
        if _git(["merge-base", "--is-ancestor", f"refs/heads/{branch}", tail_sha]).returncode != 0:
            continue
        if _git(["merge-base", "--is-ancestor", f"refs/heads/{branch}", "HEAD"]).returncode == 0:
            continue
        _, sha = resolve_local_branch(branch)
        tid = ctx.TASK_BRANCH_RE.fullmatch(branch).group(1)
        _, ref_fm, _ = load_task_at_ref(tid, sha)
        status = ref_fm.get("status", "")
        if status not in ctx.ARCHIVED_STATUSES:
            raise ctx.TaskDataError(
                f"链上分支 {branch!r} 中 {tid} status={status!r}，须全部 done/dropped"
            )
        registered = [path for path, name in worktree_paths().items() if name == branch]
        if registered:
            raise ctx.TaskDataError(
                f"链上分支 {branch!r} 仍登记 worktree：{', '.join(registered)}；"
                "先 cleanup-worktree"
            )
        chain.append((branch, sha))
    return chain

def cmd_integrate(args):
    """把已完成 task 分支合并进主干，重建 index，删除分支。

    扇出（默认）：合单个分支。链式（--chain）：只合链尾，祖先自动跟随，删整条链分支。
    """
    require_primary_worktree()
    if not ctx.TID_RE.fullmatch(args.tid):
        sys.exit(f"tid 非法：{args.tid!r}")
    attempt = _require_parallel_attempt_gate(args.tid, getattr(args, "attempt", None))
    base = default_branch()
    merge_sha = ""

    if args.continue_merge:
        if not _merge_in_progress():
            sys.exit("当前无进行中的 merge；--continue 只用于冲突解决后继续")
        conflicted = _conflicted_paths()
        if conflicted:
            sys.exit(
                f"仍有 {len(conflicted)} 个文件未解决冲突："
                f"{', '.join(conflicted[:5])}；解决并 git add 后重试"
            )
    else:
        if _merge_in_progress():
            sys.exit("存在进行中的 merge；先用 --continue 完成或 git merge --abort")
        dirty = tracked_dirty_entries()
        if dirty:
            sys.exit(
                f"主仓有 {len(dirty)} 项已跟踪文件未提交：{', '.join(dirty[:5])}；"
                "merge 前请先提交或还原。未跟踪文件不阻塞合并"
            )

    try:
        branch, sha = _resolve_integrate_branch(args.tid)
    except ctx.TaskDataError as e:
        sys.exit(str(e))

    if attempt is not None:
        verdict, detail = verify_integrate_ready(args.tid, attempt)
        if verdict != "ready":
            sys.exit(
                f"{args.tid} attempt={attempt} refs/handoff 验证失败：{detail}"
            )

    if args.continue_merge:
        # 先解析再提交：MERGE_HEAD 必须等于本 tid 分支 tip，
        # 防把别的 tid 的 merge 提交掉并记错账、删错分支
        r = _git(["rev-parse", "MERGE_HEAD"])
        merge_head = r.stdout.strip() if r.returncode == 0 else ""
        if merge_head != sha:
            sys.exit(
                f"进行中的 merge（MERGE_HEAD {merge_head[:12] or 'unknown'}）与 "
                f"{args.tid} 分支 {branch!r} tip {sha[:12]} 不符；"
                "拒绝 --continue，避免提交并记账到错误的 task"
            )

    registered = [path for path, name in worktree_paths().items() if name == branch]
    if registered:
        sys.exit(
            f"分支 {branch!r} 仍登记 worktree：{', '.join(registered)}；"
            f"先 cleanup-worktree {args.tid}"
        )

    chain: list[tuple[str, str]] = []
    if args.chain:
        try:
            chain = _resolve_chain(branch, sha)
        except ctx.TaskDataError as e:
            sys.exit(str(e))
        if chain:
            print(f"链上共 {len(chain) + 1} 个分支（含链尾 {branch}）：")
            for b, _ in chain:
                print(f"  {b}")

    if args.continue_merge:
        r = _git(["commit", "--no-edit"])
        if r.returncode != 0:
            sys.exit(f"merge commit 失败：{r.stderr.strip()}")
        print(f"merge 已完成：{_get_head_short()}")
        merge_sha = _get_head()
    else:
        if _git(["merge-base", "--is-ancestor", sha, "HEAD"]).returncode == 0:
            print(f"{branch} 已合入 {base}，跳过 merge")
            # 跳过也补记 integrated：防「分支已删/已合但账本永久在飞」
            merge_sha = _get_head()
        else:
            r = _git(["merge", "--no-ff", "-m", f"merge({args.tid}): {branch}", branch])
            if r.returncode != 0:
                conflicted = _conflicted_paths()
                if conflicted:
                    print(f"merge 冲突，共 {len(conflicted)} 个文件：", file=sys.stderr)
                    for path in conflicted:
                        print(f"  {path}", file=sys.stderr)
                    sys.exit(
                        f"解决后 git add，再执行 integrate {args.tid} --continue；"
                        "放弃用 git merge --abort"
                    )
                sys.exit(f"merge 失败：{r.stderr.strip()}")
            print(f"merge 完成：{_get_head_short()}")
            merge_sha = _get_head()

    if merge_sha:
        chain_tids = [ctx.TASK_BRANCH_RE.fullmatch(b).group(1) for b, _ in chain]
        ledger_events = ledger_read()
        for integrated_tid in chain_tids + [args.tid]:
            event = {
                "event": "integrated",
                "tid": integrated_tid,
                "merge_sha": merge_sha,
            }
            integrated_attempt = current_attempt(integrated_tid, ledger_events)
            if integrated_attempt is not None:
                event["attempt"] = integrated_attempt
            _ledger_append_safely(event)

    try:
        _commit_index()
    except ctx.TaskDataError as e:
        sys.exit(str(e))

    if args.keep_branch:
        print(f"分支 {branch!r} 按要求保留")
        return
    to_delete = chain + [(branch, sha)]
    for b, _ in to_delete:
        if _git(["merge-base", "--is-ancestor", f"refs/heads/{b}", "HEAD"]).returncode != 0:
            sys.exit(f"分支 {b!r} 未完全合入 {base}；保留分支")
    for b, _ in to_delete:
        r = _git(["branch", "-d", "--", b])
        if r.returncode != 0:
            sys.exit(f"删除分支 {b!r} 失败：{r.stderr.strip()}；已保留")
        print(f"分支已删除：{b}")
