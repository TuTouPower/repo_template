"""Task worktree creation, cleanup, and exact-identity integration."""

import json
import os
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
        tid, attempt, execution_id, events, allow_integrated=allow_integrated
    )
    if record["state"] == "terminal" and record["terminal_status"] != "completed":
        raise ctx.TaskDataError(
            f"{tid} attempt={attempt} terminal status={record['terminal_status']!r}，"
            "只有 completed 可 cleanup/integrate"
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


def _commit_index() -> None:
    rebuild_index()
    paths = [ctx._rel(ctx.ACTIVE_PATH), ctx._rel(ctx.ARCHIVE_PATH)]
    _git(["add", "--", *paths])
    if _git(["diff", "--cached", "--quiet", "--", *paths]).returncode == 0:
        print("index 无变化，跳过维护 commit")
        return
    result = _git(["commit", "-m", "chore(task): rebuild task indexes", "--", *paths])
    if result.returncode != 0:
        raise ctx.TaskDataError(f"index commit 失败：{result.stderr.strip()}")
    print(f"index 维护 commit：{_get_head_short()}")


def _registered_for_branch(branch: str) -> list[str]:
    return [path for path, name in worktree_paths().items() if name == branch]


def _ensure_primary_merge_ready() -> None:
    if _merge_in_progress():
        raise ctx.TaskDataError("存在进行中的 merge；先完成当前事务")
    dirty = tracked_dirty_entries()
    if dirty:
        raise ctx.TaskDataError(
            f"主仓有 {len(dirty)} 项已跟踪文件未提交：{', '.join(dirty[:5])}；"
            "merge 前请先提交或还原。未跟踪文件不阻塞合并"
        )


def _delete_branches(branches: list[str]) -> None:
    base = default_branch()
    for branch in branches:
        if _git(["merge-base", "--is-ancestor", f"refs/heads/{branch}", "HEAD"]).returncode != 0:
            raise ctx.TaskDataError(f"分支 {branch!r} 未完全合入 {base}；保留分支")
    for branch in branches:
        result = _git(["branch", "-d", "--", branch])
        if result.returncode != 0:
            raise ctx.TaskDataError(f"删除分支 {branch!r} 失败：{result.stderr.strip()}；已保留")
        print(f"分支已删除：{branch}")


def cmd_integrate(args):
    """Integrate exactly one terminal attempt."""
    require_primary_worktree()
    try:
        record, _ = _require_execution_gate(
            args.tid, args.attempt, args.execution_id, allow_integrated=True
        )
        branch, sha = _resolve_integrate_branch(args.tid)
        _verify_exact_handoff(args.tid, args.attempt, args.execution_id)
    except ctx.TaskDataError as error:
        sys.exit(str(error))
    registered = _registered_for_branch(branch)
    if registered:
        sys.exit(
            f"分支 {branch!r} 仍登记 worktree：{', '.join(registered)}；"
            f"先 cleanup-worktree {args.tid} --attempt {args.attempt} "
            f"--execution-id {args.execution_id}"
        )
    if args.continue_merge:
        if not _merge_in_progress():
            sys.exit("当前无进行中的 merge；--continue 只用于冲突解决后继续")
        conflicted = _conflicted_paths()
        if conflicted:
            sys.exit(
                f"仍有 {len(conflicted)} 个文件未解决冲突：{', '.join(conflicted[:5])}；"
                "解决并 git add 后重试"
            )
        merge_head = _git(["rev-parse", "MERGE_HEAD"])
        if merge_head.returncode != 0 or merge_head.stdout.strip() != sha:
            sys.exit("进行中的 MERGE_HEAD 与 exact task branch sha 不符；拒绝 --continue")
        result = _git(["commit", "--no-edit"])
        if result.returncode != 0:
            sys.exit(f"merge commit 失败：{result.stderr.strip()}")
        merge_sha = _get_head()
        print(f"merge 已完成：{_get_head_short()}")
    else:
        try:
            _ensure_primary_merge_ready()
        except ctx.TaskDataError as error:
            sys.exit(str(error))
        if _git(["merge-base", "--is-ancestor", sha, "HEAD"]).returncode == 0:
            print(f"{branch} 已合入 {default_branch()}，跳过 merge")
            merge_sha = _get_head()
        else:
            result = _git(["merge", "--no-ff", "-m", f"merge({args.tid}): {branch}", branch])
            if result.returncode != 0:
                conflicted = _conflicted_paths()
                if conflicted:
                    print(f"merge 冲突，共 {len(conflicted)} 个文件：", file=sys.stderr)
                    for path in conflicted:
                        print(f"  {path}", file=sys.stderr)
                    sys.exit(
                        f"解决后 git add，再执行 integrate {args.tid} --attempt {args.attempt} "
                        f"--execution-id {args.execution_id} --continue"
                    )
                sys.exit(f"merge 失败：{result.stderr.strip()}")
            merge_sha = _get_head()
            print(f"merge 完成：{_get_head_short()}")
    try:
        _commit_index()
        if record["state"] != "integrated":
            append_integrated(args.tid, args.attempt, args.execution_id, merge_sha)
        if args.keep_branch:
            print(f"分支 {branch!r} 按要求保留")
        else:
            _delete_branches([branch])
    except ctx.TaskDataError as error:
        sys.exit(str(error))


def _integration_tx_path() -> Path:
    result = _git(["rev-parse", "--absolute-git-dir"])
    if result.returncode != 0 or not result.stdout.strip():
        raise ctx.TaskDataError("无法解析 absolute git dir")
    return Path(result.stdout.strip()) / "repo-task" / "integrate-chain.json"


def _write_chain_tx(payload: dict) -> Path:
    path = _integration_tx_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)
    return path


def _read_chain_tx() -> tuple[Path, dict]:
    path = _integration_tx_path()
    if not path.is_file():
        raise ctx.TaskDataError("不存在 integrate-chain transaction；禁止恢复其他 merge")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ctx.TaskDataError(f"integrate-chain transaction 无法读取：{error}") from None
    if (
        not isinstance(payload, dict)
        or payload.get("phase") not in {"prepared", "merged", "indexed", "awaiting_verification"}
        or not isinstance(payload.get("members"), list)
        or not payload["members"]
    ):
        raise ctx.TaskDataError("integrate-chain transaction 格式非法")
    return path, payload


def _update_chain_tx(payload: dict, phase: str, **fields) -> dict:
    updated = {**payload, **fields, "phase": phase}
    _write_chain_tx(updated)
    return updated


def _collect_chain(tail_tid: str) -> list[tuple[str, str, str]]:
    tail_branch, tail_sha = _resolve_integrate_branch(tail_tid)
    candidates = []
    for branch in _local_task_branches():
        _, sha = resolve_local_branch(branch)
        if _git(["merge-base", "--is-ancestor", sha, tail_sha]).returncode != 0:
            continue
        if _git(["merge-base", "--is-ancestor", sha, "HEAD"]).returncode == 0:
            continue
        match = ctx.TASK_BRANCH_RE.fullmatch(branch)
        if match is None:
            continue
        candidates.append((match.group(1), branch, sha))
    if not any(branch == tail_branch for _, branch, _ in candidates):
        raise ctx.TaskDataError(f"链尾 {tail_branch!r} 已合入或不在未合并 task 分支集合")
    for index, left in enumerate(candidates):
        for right in candidates[index + 1:]:
            left_before = _git(["merge-base", "--is-ancestor", left[2], right[2]]).returncode == 0
            right_before = _git(["merge-base", "--is-ancestor", right[2], left[2]]).returncode == 0
            if not (left_before or right_before):
                raise ctx.TaskDataError(
                    f"链成员非线性：{left[1]!r} 与 {right[1]!r} 无祖先关系"
                )
    candidates.sort(key=lambda item: sum(
        _git(["merge-base", "--is-ancestor", other[2], item[2]]).returncode == 0
        for other in candidates if other != item
    ))
    if candidates[-1][1] != tail_branch:
        raise ctx.TaskDataError("链尾不是线性 ancestry 的最后成员")
    for previous, current in zip(candidates, candidates[1:]):
        parent = _git(["rev-parse", f"{current[2]}^1"])
        if parent.returncode != 0 or parent.stdout.strip() != previous[2]:
            raise ctx.TaskDataError(
                f"链不连续：{current[1]!r} first parent 不是前一成员 {previous[1]!r} tip"
            )
    return candidates


def _preflight_chain(tail_tid: str) -> list[dict]:
    chain = _collect_chain(tail_tid)
    events = ledger_read()
    members = []
    for tid, branch, sha in chain:
        current = current_attempt_record(tid, events)
        if current is None:
            raise ctx.TaskDataError(f"链成员 {tid} 无 current attempt")
        _require_execution_gate(tid, current["attempt"], current["execution_id"])
        resolved_branch, resolved_sha = _resolve_integrate_branch(tid)
        if resolved_branch != branch or resolved_sha != sha:
            raise ctx.TaskDataError(f"链成员 {tid} branch sha 在预检中变化")
        _verify_exact_handoff(tid, current["attempt"], current["execution_id"])
        registered = _registered_for_branch(branch)
        if registered:
            raise ctx.TaskDataError(
                f"链成员 {branch!r} 仍登记 worktree：{', '.join(registered)}；先 cleanup-worktree"
            )
        members.append({
            "tid": tid,
            "branch": branch,
            "sha": sha,
            "attempt": current["attempt"],
            "execution_id": current["execution_id"],
        })
    return members


def _validate_tx_members(payload: dict, tail_tid: str) -> list[dict]:
    if payload.get("tail_tid") != tail_tid:
        raise ctx.TaskDataError(
            f"transaction tail_tid={payload.get('tail_tid')!r}，拒绝恢复 {tail_tid!r}"
        )
    members = payload["members"]
    if members[-1].get("tid") != tail_tid:
        raise ctx.TaskDataError("transaction 链尾成员与 tail_tid 不符")
    allow_integrated = payload["phase"] in {"merged", "indexed", "awaiting_verification"}
    previous = None
    for member in members:
        tid = member.get("tid")
        branch = member.get("branch")
        sha = member.get("sha")
        if not all(isinstance(value, str) and value for value in (tid, branch, sha)):
            raise ctx.TaskDataError("transaction member 字段非法")
        branches = _task_branch_names(tid)
        if branches != [branch]:
            raise ctx.TaskDataError(
                f"transaction 成员 {tid} 分支集合漂移：{branches!r}，预期 {[branch]!r}"
            )
        _, actual_sha = resolve_local_branch(branch)
        if actual_sha != sha:
            raise ctx.TaskDataError(f"transaction 成员 {branch!r} tip 漂移")
        _require_execution_gate(
            tid, member.get("attempt"), member.get("execution_id"),
            allow_integrated=allow_integrated,
        )
        _verify_exact_handoff(tid, member["attempt"], member["execution_id"])
        registered = _registered_for_branch(branch)
        if registered:
            raise ctx.TaskDataError(
                f"transaction 成员 {branch!r} 又登记了 worktree：{', '.join(registered)}"
            )
        if previous is not None:
            parent = _git(["rev-parse", f"{sha}^1"])
            if parent.returncode != 0 or parent.stdout.strip() != previous["sha"]:
                raise ctx.TaskDataError(
                    f"transaction ancestry 漂移：{branch!r} 不再紧邻 {previous['branch']!r}"
                )
        previous = member
    return members


def _record_prepared_merge(payload: dict) -> dict:
    """Recover the merge commit even if the phase write was interrupted."""
    if _merge_in_progress():
        merge_head = _git(["rev-parse", "MERGE_HEAD"])
        if merge_head.returncode != 0 or merge_head.stdout.strip() != payload["members"][-1]["sha"]:
            raise ctx.TaskDataError("MERGE_HEAD 与 transaction 链尾 sha 不符；拒绝恢复")
        conflicted = _conflicted_paths()
        if conflicted:
            raise ctx.TaskDataError(
                f"仍有 {len(conflicted)} 个文件未解决冲突：{', '.join(conflicted[:5])}"
            )
        result = _git(["commit", "--no-edit"])
        if result.returncode != 0:
            raise ctx.TaskDataError(f"chain merge commit 失败：{result.stderr.strip()}")
        return _update_chain_tx(payload, "merged", merge_sha=_get_head())
    head = _get_head()
    first_parent = _git(["rev-parse", "HEAD^1"])
    second_parent = _git(["rev-parse", "HEAD^2"])
    if (
        first_parent.returncode == 0
        and second_parent.returncode == 0
        and first_parent.stdout.strip() == payload.get("base_head")
        and second_parent.stdout.strip() == payload["members"][-1]["sha"]
    ):
        return _update_chain_tx(payload, "merged", merge_sha=head)
    raise ctx.TaskDataError("prepared transaction 无对应 merge 状态或 merge commit")


def _record_index_phase(payload: dict) -> dict:
    merge_sha = payload.get("merge_sha")
    if not isinstance(merge_sha, str) or not merge_sha:
        raise ctx.TaskDataError("merged transaction 缺 merge_sha")
    head = _get_head()
    if head == merge_sha:
        _commit_index()
        head = _get_head()
    elif _git(["rev-parse", "HEAD^1"]).stdout.strip() != merge_sha:
        raise ctx.TaskDataError("当前 HEAD 既非 merge_sha，也非其紧邻 index 维护 commit")
    return _update_chain_tx(payload, "indexed", index_sha=head)


def _record_integrated_phase(payload: dict) -> dict:
    index_sha = payload.get("index_sha")
    if _get_head() != index_sha:
        raise ctx.TaskDataError("indexed transaction 的 index_sha 与当前 HEAD 不符")
    append_integrated_batch(payload["members"], payload["merge_sha"])
    return _update_chain_tx(payload, "awaiting_verification")


def _delete_chain_branches(members: list[dict]) -> None:
    for member in members:
        branch = member["branch"]
        if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode != 0:
            continue
        if _git(["merge-base", "--is-ancestor", f"refs/heads/{branch}", "HEAD"]).returncode != 0:
            raise ctx.TaskDataError(f"分支 {branch!r} 未完全合入 {default_branch()}；保留事务")
        result = _git(["branch", "-d", "--", branch])
        if result.returncode != 0:
            raise ctx.TaskDataError(
                f"删除分支 {branch!r} 失败：{result.stderr.strip()}；保留事务可重试"
            )
        print(f"分支已删除：{branch}")


def _resume_chain_to_verification(payload: dict) -> dict:
    if payload["phase"] == "prepared":
        payload = _record_prepared_merge(payload)
    _validate_tx_members(payload, payload["tail_tid"])
    if payload["phase"] == "merged":
        payload = _record_index_phase(payload)
    if payload["phase"] == "indexed":
        payload = _record_integrated_phase(payload)
    return payload


def cmd_integrate_chain(args):
    require_primary_worktree()
    if args.continue_merge:
        try:
            tx_path, payload = _read_chain_tx()
            if payload.get("tail_tid") != args.tail_tid:
                raise ctx.TaskDataError(
                    f"transaction tail_tid={payload.get('tail_tid')!r}，"
                    f"拒绝恢复 {args.tail_tid!r}"
                )
            starting_phase = payload["phase"]
            if starting_phase == "awaiting_verification":
                members = _validate_tx_members(payload, args.tail_tid)
                append_integrated_batch(members, payload["merge_sha"])
                if _get_head() != payload.get("index_sha"):
                    raise ctx.TaskDataError(
                        "外部验证后 HEAD 与 transaction index_sha 不符；拒绝删除分支"
                    )
                _delete_chain_branches(members)
                tx_path.unlink(missing_ok=True)
                print(
                    f"chain transaction 已完成：merge={payload['merge_sha'][:12]}；"
                    f"成员={len(members)}"
                )
                return
            payload = _resume_chain_to_verification(payload)
        except ctx.TaskDataError as error:
            sys.exit(str(error))
        print(
            f"chain 已收尾到 phase={payload['phase']}；merge={payload['merge_sha'][:12]}。"
            "请执行合并后验证；通过后再次运行同一 integrate-chain --continue "
            "以删除分支并清除 transaction"
        )
        return
    try:
        if _integration_tx_path().exists():
            raise ctx.TaskDataError(
                "已存在 integrate-chain transaction；先按原 tail_tid --continue 恢复"
            )
        _ensure_primary_merge_ready()
        members = _preflight_chain(args.tail_tid)
    except ctx.TaskDataError as error:
        sys.exit(str(error))
    payload = {
        "version": 2,
        "phase": "prepared",
        "tail_tid": args.tail_tid,
        "base_head": _get_head(),
        "merge_sha": None,
        "index_sha": None,
        "members": members,
    }
    tx_path = _write_chain_tx(payload)
    tail = members[-1]
    result = _git([
        "merge", "--no-ff", "-m", f"merge-chain({args.tail_tid}): {tail['branch']}",
        tail["branch"],
    ])
    if result.returncode != 0:
        conflicted = _conflicted_paths()
        if conflicted:
            print(f"chain merge 冲突，共 {len(conflicted)} 个文件：", file=sys.stderr)
            for path in conflicted:
                print(f"  {path}", file=sys.stderr)
            sys.exit(
                f"解决后 git add，再执行 integrate-chain {args.tail_tid} --continue；"
                f"transaction={tx_path}"
            )
        tx_path.unlink(missing_ok=True)
        sys.exit(f"chain merge 失败：{result.stderr.strip()}")
    payload = _update_chain_tx(payload, "merged", merge_sha=_get_head())
    try:
        payload = _resume_chain_to_verification(payload)
    except ctx.TaskDataError as error:
        sys.exit(
            f"chain merge 已发生，transaction 保留于 {tx_path}：{error}；"
            f"修复后执行 integrate-chain {args.tail_tid} --continue"
        )
    print(
        f"chain merge 已完成：{payload['merge_sha'][:12]}；成员={len(members)}；"
        "分支与 transaction 保留。请执行合并后验证；通过后运行 "
        f"integrate-chain {args.tail_tid} --continue"
    )
