"""Canonical lifecycle implementation for the task toolchain."""

import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import repo_task.context as ctx

from .documents import dump_tid_list, parse_front_matter, parse_tid_list, tid_sort_key, validate_task_documents, validate_tid_references, write_front_matter
from .git_ops import _git, has_unmerged_commits, in_own_task_worktree, porcelain_entries, require_own_task_worktree, require_primary_worktree, resolve_local_branch, tracked_anywhere, worktree_paths
from .scheduling import _dependency_cycle
from .store import append_audit, append_note, git_text_at_ref, load_task, load_task_at_ref, rebuild_index, require_status, scan_tasks, scan_tasks_at_ref, task_effective_state, task_schedule_references
from .worktrees import discard_worktree, remove_worktree

def _dependency_path_exists(
    dependencies: dict[str, list[str]], src: str, dst: str
) -> bool:
    """dependencies 图上 src → … → dst 传递可达性（src 直接或间接依赖 dst）。"""
    stack, seen = [src], set()
    while stack:
        node = stack.pop()
        if node == dst:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(dependencies.get(node, []))
    return False

def cmd_add(args):
    require_primary_worktree()
    if not ctx.SLUG_RE.match(args.slug):
        sys.exit(f"slug 须匹配 {ctx.SLUG_RE.pattern}（收到 {args.slug!r}）")
    if not args.title.strip():
        sys.exit("title 不能为空")
    tasks = scan_tasks()
    for t in tasks:
        if t["slug"] == args.slug:
            sys.exit(f"slug 已存在：{args.slug}（{t['tid']}）")
    n = max((int(ctx.TID_RE.match(t["tid"]).group(1)) for t in tasks), default=0) + 1
    tid = f"t{n:03d}"
    task_dir = ctx.TASKS_DIR / f"{tid}_{args.slug}"
    if task_dir.exists():
        sys.exit(f"{ctx._rel(task_dir)} 已存在；请提示用户处理")
    if not ctx.TEMPLATE_DIR.is_dir():
        sys.exit(f"缺模板目录 {ctx._rel(ctx.TEMPLATE_DIR)}")
    template_spec = ctx.TEMPLATE_DIR / "spec.md"
    template_task = ctx.TEMPLATE_DIR / "task.md"
    if not template_spec.is_file() or not template_task.is_file():
        sys.exit(f"模板目录 {ctx._rel(ctx.TEMPLATE_DIR)} 缺 spec.md 或 task.md")
    _, template_task_body = parse_front_matter(template_task)
    template_problems, _ = validate_task_documents(
        template_spec.read_text(encoding="utf-8"),
        template_task_body,
        allow_template_placeholders=True,
    )
    if template_problems:
        sys.exit("模板结构校验失败：" + "；".join(template_problems))

    shutil.copytree(ctx.TEMPLATE_DIR, task_dir)
    task_md = task_dir / "task.md"
    fm, body = parse_front_matter(task_md)
    fm.update({
        "tid": tid,
        "slug": args.slug,
        "title": args.title.strip(),
        "status": "backlog",
        "branch": "",
        "worktree": "",
        "review_level": args.review_level,
        "diff_anchor": "",
        "depends_on": "",
        "conflicts_with": "",
        "note": args.note or "",
    })
    fm.pop("schedule_status", None)
    write_front_matter(task_md, fm, body)
    rebuild_index()
    print(f"added {tid} '{fm['title']}' status=backlog review_level={fm['review_level']}")
    print(f"工作区：{ctx._rel(task_dir)}（已从模板复制 spec.md / task.md）")

def cmd_edit(args):
    require_primary_worktree()
    field_names = (
        "title", "note", "note_append", "review_level", "depends_on",
        "depends_append", "depends_remove", "conflicts_with",
        "conflicts_append", "conflicts_remove", "schedule_status",
    )
    values = {name: getattr(args, name, None) for name in field_names}
    if all(value is None for value in values.values()):
        sys.exit(
            "没有要改的字段；传 --title / --note / --note-append / --review-level / "
            "--depends-* / --conflicts-* / --schedule-status"
        )
    if values["note"] is not None and values["note_append"] is not None:
        sys.exit("--note 与 --note-append 互斥")
    dependency_actions = [
        values["depends_on"], values["depends_append"], values["depends_remove"]
    ]
    conflict_actions = [
        values["conflicts_with"], values["conflicts_append"], values["conflicts_remove"]
    ]
    if sum(value is not None for value in dependency_actions) > 1:
        sys.exit("--depends-on / --depends-append / --depends-remove 互斥")
    if sum(value is not None for value in conflict_actions) > 1:
        sys.exit("--conflicts-with / --conflicts-append / --conflicts-remove 互斥")

    tasks = scan_tasks()
    tasks_by_tid = {task["tid"]: task for task in tasks}
    task, path, fm, body = load_task(args.tid)
    if fm["status"] in ctx.ARCHIVED_STATUSES:
        sys.exit(f"{args.tid} 已归档（{fm['status']}），不可编辑")
    if fm["status"] != "backlog":
        sys.exit(
            f"{args.tid} status={fm['status']}；edit 只改 main 中未进入链的 backlog，"
            "active/blocked 请在自身 worktree 内编辑"
        )
    covered = task_effective_state(args.tid, fm)
    if covered:
        sys.exit(
            f"{args.tid} 在 main 中为 backlog，但{covered}；"
            "main 副本已滞后，edit 拒绝操作过期状态"
        )

    changed = []
    peer_updates: dict[Path, tuple[dict, str]] = {}
    if values["title"] is not None:
        title = values["title"].strip()
        if not title:
            sys.exit("title 不能为空")
        fm["title"] = title
        changed.append(f"title={title!r}")
    if values["note"] is not None:
        fm["note"] = values["note"]
        changed.append(f"note={values['note']!r}")
    if values["note_append"] is not None:
        if not values["note_append"].strip():
            sys.exit("--note-append 不能为空")
        append_note(fm, values["note_append"])
        changed.append(f"note+={values['note_append']!r}")
    if values["review_level"] is not None:
        fm["review_level"] = values["review_level"]
        changed.append(f"review_level={values['review_level']}")

    if any(value is not None for value in dependency_actions):
        current_dependencies = parse_tid_list(
            fm.get("depends_on", ""), field=f"{args.tid}.depends_on"
        )
        dependencies = list(current_dependencies)
        if values["depends_on"] is not None:
            dependencies = parse_tid_list(values["depends_on"], field="--depends-on")
        elif values["depends_append"] is not None:
            append_tid = parse_tid_list(
                values["depends_append"], field="--depends-append", allow_empty=False
            )
            if len(append_tid) != 1:
                sys.exit("--depends-append 只接受一个 tid")
            dependencies = sorted(set(dependencies + append_tid), key=tid_sort_key)
        elif values["depends_remove"] is not None:
            remove_tid = parse_tid_list(
                values["depends_remove"], field="--depends-remove", allow_empty=False
            )
            if len(remove_tid) != 1:
                sys.exit("--depends-remove 只接受一个 tid")
            if remove_tid[0] not in dependencies:
                sys.exit(f"{args.tid}.depends_on 不含 {remove_tid[0]}")
            dependencies.remove(remove_tid[0])
        validate_tid_references(
            dependencies,
            field="depends_on",
            owner_tid=args.tid,
            tasks_by_tid=tasks_by_tid,
        )
        dropped_dependencies = [
            tid for tid in dependencies if tasks_by_tid[tid]["status"] == "dropped"
        ]
        if dropped_dependencies:
            sys.exit(f"depends_on 不可引用 dropped task：{', '.join(dropped_dependencies)}")
        candidate_dependencies = {
            candidate["tid"]: parse_tid_list(
                candidate.get("depends_on", ""),
                field=f"{candidate['tid']}.depends_on",
            )
            for candidate in tasks
            if candidate["status"] not in ctx.ARCHIVED_STATUSES
        }
        candidate_dependencies[args.tid] = list(dependencies)
        cycle = _dependency_cycle(candidate_dependencies)
        if cycle:
            sys.exit(f"depends_on 变更会形成依赖环：{' -> '.join(cycle)}")
        fm["depends_on"] = dump_tid_list(dependencies)
        changed.append(f"depends_on={fm['depends_on']!r}")

    if any(value is not None for value in conflict_actions):
        current_conflicts = parse_tid_list(
            fm.get("conflicts_with", ""), field=f"{args.tid}.conflicts_with"
        )
        conflicts = list(current_conflicts)
        if values["conflicts_with"] is not None:
            conflicts = parse_tid_list(values["conflicts_with"], field="--conflicts-with")
        elif values["conflicts_append"] is not None:
            append_tid = parse_tid_list(
                values["conflicts_append"], field="--conflicts-append", allow_empty=False
            )
            if len(append_tid) != 1:
                sys.exit("--conflicts-append 只接受一个 tid")
            conflicts = sorted(set(conflicts + append_tid), key=tid_sort_key)
        elif values["conflicts_remove"] is not None:
            remove_tid = parse_tid_list(
                values["conflicts_remove"], field="--conflicts-remove", allow_empty=False
            )
            if len(remove_tid) != 1:
                sys.exit("--conflicts-remove 只接受一个 tid")
            if remove_tid[0] not in conflicts:
                sys.exit(f"{args.tid}.conflicts_with 不含 {remove_tid[0]}")
            conflicts.remove(remove_tid[0])
        validate_tid_references(
            conflicts,
            field="conflicts_with",
            owner_tid=args.tid,
            tasks_by_tid=tasks_by_tid,
        )
        dropped_conflicts = [
            tid for tid in conflicts if tasks_by_tid[tid]["status"] == "dropped"
        ]
        if dropped_conflicts:
            sys.exit(f"conflicts_with 不可引用 dropped task：{', '.join(dropped_conflicts)}")

        affected = sorted(set(current_conflicts) | set(conflicts), key=tid_sort_key)
        for peer_tid in affected:
            peer_task = tasks_by_tid[peer_tid]
            # done target 已合 main 或归档，不可编辑：跳过反向边同步，
            # owner 单边增删即可；调度由 view 的 main_done_set 释放。
            if peer_task["status"] == "done":
                continue
            if peer_task["status"] != "backlog":
                sys.exit(
                    f"无法维护冲突反向边：{peer_tid} status={peer_task['status']}，"
                    "须为可编辑 backlog"
                )
            _, peer_path, peer_fm, peer_body = load_task(peer_tid)
            peer_covered = task_effective_state(peer_tid, peer_fm)
            if peer_covered:
                sys.exit(f"无法维护冲突反向边：{peer_tid} {peer_covered}")
            peer_conflicts = parse_tid_list(
                peer_fm.get("conflicts_with", ""),
                field=f"{peer_tid}.conflicts_with",
            )
            if peer_tid in conflicts:
                peer_conflicts = sorted(
                    set(peer_conflicts + [args.tid]), key=tid_sort_key
                )
            else:
                peer_conflicts = [tid for tid in peer_conflicts if tid != args.tid]
            peer_fm["conflicts_with"] = dump_tid_list(peer_conflicts)
            peer_updates[peer_path] = (peer_fm, peer_body)
        fm["conflicts_with"] = dump_tid_list(conflicts)
        changed.append(f"conflicts_with={fm['conflicts_with']!r}")

    if values["schedule_status"] is not None:
        fm["schedule_status"] = values["schedule_status"]
        changed.append(f"schedule_status={values['schedule_status']}")

    # L1 冗余门禁：仅当本次变更触碰了依赖/冲突字段时，校验 owner 相关
    # pair——冲突边两端间不得存在（传递）依赖路径。依赖已蕴含串行，
    # 冗余冲突边无意义（数据卫生，见 blueprint 调度图语义不变式）。
    # 只拦新增/留存于本次变更后图中的 pair，不全图重验，保证
    # --conflicts-remove / --depends-remove 的增量修复路径畅通。
    if any(value is not None for value in dependency_actions + conflict_actions):
        final_depends = parse_tid_list(
            fm.get("depends_on", ""), field=f"{args.tid}.depends_on"
        )
        final_conflicts = parse_tid_list(
            fm.get("conflicts_with", ""), field=f"{args.tid}.conflicts_with"
        )
        # 冲突边双向口径：owner 声明 ∪ peer 声明（防脏数据单边挂边漏检）；
        # peer 反向边若本次已同步变更，须用 peer_updates 里的新值而非旧快照，
        # 否则 --conflicts-remove 的修复路径会被自己的反向边误拦
        updated_peer_fm = {
            peer_fm["tid"]: peer_fm for peer_fm, _ in peer_updates.values()
        }
        conflict_peers = set(final_conflicts)
        for candidate in tasks:
            if candidate["tid"] == args.tid:
                continue
            peer_fm = updated_peer_fm.get(candidate["tid"], candidate)
            peer_conflicts = parse_tid_list(
                peer_fm.get("conflicts_with", ""),
                field=f"{candidate['tid']}.conflicts_with",
            )
            if args.tid in peer_conflicts:
                conflict_peers.add(candidate["tid"])
        candidate_dependencies = {
            candidate["tid"]: parse_tid_list(
                candidate.get("depends_on", ""),
                field=f"{candidate['tid']}.depends_on",
            )
            for candidate in tasks
            if candidate["status"] not in ctx.ARCHIVED_STATUSES
        }
        candidate_dependencies[args.tid] = final_depends
        for peer in sorted(conflict_peers, key=tid_sort_key):
            forward = _dependency_path_exists(
                candidate_dependencies, args.tid, peer
            )
            backward = _dependency_path_exists(
                candidate_dependencies, peer, args.tid
            )
            if forward or backward:
                direction = (
                    f"{args.tid} ⋯depends⋯→ {peer}"
                    if forward
                    else f"{peer} ⋯depends⋯→ {args.tid}"
                )
                sys.exit(
                    f"冲突边与依赖路径冗余：{args.tid} ↔ {peer} 冲突，但{direction}"
                    "（依赖已蕴含串行）；请只保留依赖，删除冲突边"
                )

    for peer_path, (peer_fm, peer_body) in peer_updates.items():
        write_front_matter(peer_path, peer_fm, peer_body)
    write_front_matter(path, fm, body)
    rebuild_index()
    print(f"{args.tid} updated: {', '.join(changed)}")

def cmd_preflight(args):
    ref_arg = args.ref
    source_ref = ""
    if ref_arg:
        source_ref, ref_sha = resolve_local_branch(ref_arg)
        task, fm, task_body = load_task_at_ref(args.tid, ref_sha)
        task_dir = None
    else:
        task, _, fm, task_body = load_task(args.tid)
        task_dir = ctx.REPO_ROOT / task["dir"]
    problems, warnings = [], []

    # 1. 状态
    allow_backlog = args.allow_backlog
    if fm["status"] in ctx.ARCHIVED_STATUSES:
        problems.append(f"status={fm['status']}，已归档不可执行")
    elif fm["status"] == "blocked":
        problems.append("status=blocked，须用户放行（加轮 resume 或 drop）后再执行")
    elif fm["status"] == "backlog" and not allow_backlog:
        problems.append("status=backlog，须先 start")
    elif fm["status"] not in ("active", "backlog"):
        problems.append(f"status={fm['status']}，不可执行")

    # 2. spec 完整与未知契约
    spec_rel = f"{task['dir']}/spec.md"
    if source_ref:
        try:
            text = git_text_at_ref(ref_sha, spec_rel)
        except ctx.TaskDataError:
            text = ""
            problems.append("缺 spec.md")
    else:
        spec = task_dir / "spec.md"
        if not spec.is_file():
            text = ""
            problems.append("缺 spec.md")
        else:
            text = spec.read_text(encoding="utf-8")
    if text:
        document_problems, document_warnings = validate_task_documents(
            text,
            task_body,
            require_verified=args.require_verified,
        )
        problems.extend(document_problems)
        warnings.extend(document_warnings)

    # 3. review 必要字段
    if fm["status"] == "active" and not fm.get("diff_anchor"):
        problems.append("diff_anchor 为空；review 无法渲染，请 rewind 后重走 start")
    if fm.get("review_level") not in ctx.REVIEW_LEVELS:
        problems.append(f"review_level={fm.get('review_level')!r} 非法，须为 {ctx.REVIEW_LEVELS}")

    # 4. 工作区一致性
    if source_ref:
        warnings.append(f"ref 快照 {source_ref}：未检查 task worktree 与当前脏改动")
    else:
        if fm["status"] == "backlog":
            covered = task_effective_state(args.tid, fm)
            if covered:
                warnings.append(
                    f"main 中为 backlog，但{covered}；"
                    "main 副本滞后，不能据此重复 start"
                )
        if fm["status"] == "active" and not in_own_task_worktree(fm):
            problems.append(
                f"当前不在 task worktree {ctx.effective_worktree(fm)} 的分支 {fm['branch']!r}"
            )

        dirty = porcelain_entries()
        foreign = [p for p in dirty
                   if not p.startswith(task["dir"])
                   and p not in ("docs/tasks_index.json", "docs/archive/tasks_index.json")
                   and not p.startswith(".scratch/")]
        if foreign:
            warnings.append(
                f"工作区有 {len(foreign)} 项与本 task 无关的改动：{', '.join(foreign[:5])}"
            )

    # 5. testing.md 占位符 warn（不阻塞）
    testing_md = ctx.REPO_ROOT / "docs" / "blueprint" / "testing.md"
    if testing_md.is_file():
        testing_text = testing_md.read_text(encoding="utf-8")
        missing = [
            ph for ph in ("{doctor_cmd}", "{test_cmd}", "{blackbox_verify}")
            if ph in testing_text
        ]
        if missing:
            warnings.append(
                f"testing.md 仍有未填占位符 {' / '.join(missing)}；"
                "门禁命令未定义，task-work Step 1/3/4 与合并后验证无机械锚点。"
                "项目复制后须在 testing.md 填写实际命令"
            )

    print(f"# preflight {args.tid}")
    if source_ref:
        print(f"  source_ref: {source_ref}")
    for line in warnings:
        print(f"  WARN : {line}")
    for line in problems:
        print(f"  FAIL : {line}")
    if problems:
        print(f"\npreflight=FAIL（{len(problems)} 项）；修复后重跑")
        sys.exit(1)
    print(f"\npreflight=PASS{f'（{len(warnings)} 条警告）' if warnings else ''}")

def cmd_block(args):
    task, path, fm, body = load_task(args.tid)
    require_status(fm, "active")
    require_own_task_worktree(fm)
    fm["status"] = "blocked"
    append_note(fm, f"blocked: {args.reason}")
    write_front_matter(path, fm, body)
    print(f"{args.tid} status=blocked reason={args.reason}")

def cmd_resume(args):
    task, path, fm, body = load_task(args.tid)
    require_status(fm, "blocked")
    require_own_task_worktree(fm)
    fm["status"] = "active"
    write_front_matter(path, fm, body)
    print(f"{args.tid} status=active (resumed)")

def _close_task(args, status: str, note: str | None) -> None:
    """done / dropped 收尾：先做 git 侧动作，再单次写盘，最后归档目录。

    单次写盘是为了避免「front matter 已写 done、目录未归档」的中间态——
    那种状态下 finish/drop/rewind 三条出口全被状态校验挡死，只能手改 front matter。
    归档移动失败时回滚 front matter，同理避免上述死锁。
    """
    task, path, fm, body = load_task(args.tid)
    if status == "done":
        require_status(fm, "active")
        require_own_task_worktree(fm)
    elif fm["status"] in ctx.ARCHIVED_STATUSES:
        sys.exit(f"{args.tid} 已是 {fm['status']}")
    elif fm["status"] in ("active", "blocked"):
        require_own_task_worktree(fm)
    else:
        require_primary_worktree()
        if status == "dropped":
            covered = task_effective_state(args.tid, fm)
            if covered:
                sys.exit(
                    f"{args.tid} 在主干中为 backlog，但{covered}；"
                    "主干副本已滞后。请到对应 worktree 执行 drop，或先合并该 task 分支"
                )

    src = ctx.REPO_ROOT / task["dir"]
    dst = ctx.ARCHIVE_TASKS_DIR / f"{fm['tid']}_{fm['slug']}"
    if dst.exists():
        sys.exit(f"归档目录已存在：{ctx._rel(dst)}（数据冲突，请提示用户）")

    in_own_worktree = in_own_task_worktree(fm)
    if in_own_worktree:
        removed, wt_msg = False, (
            f"worktree {fm['worktree']} 待执行 commit 后从主仓 cleanup-worktree"
        )
    else:
        removed, wt_msg = remove_worktree(ctx.effective_worktree(fm))

    orig_fm = dict(fm)
    fm["status"] = status
    if note:
        append_note(fm, note)
    if in_own_worktree or removed:
        fm["worktree"] = ""
    else:
        append_note(fm, f"worktree 未移除：{ctx.effective_worktree(fm)}")
    write_front_matter(path, fm, body)

    ctx.ARCHIVE_TASKS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(src), str(dst))
    except (OSError, shutil.Error) as e:
        write_front_matter(path, orig_fm, body)
        sys.exit(
            f"归档移动失败（{e}）；front matter 已回滚为 status={orig_fm['status']}，"
            f"目录仍在 {ctx._rel(src)}。排除原因后重试"
        )
    print(f"{args.tid} status={status}; 目录已归档 -> {ctx._rel(dst)}; {wt_msg}")
    if not removed and not in_own_worktree:
        print("WARNING: worktree 未移除，已记入 note；请手动清理", file=sys.stderr)

def cmd_finish(args):
    _close_task(args, "done", None)

def cmd_drop(args):
    references = task_schedule_references(args.tid)
    if references:
        sys.exit(
            f"{args.tid} 仍被调度图引用：{', '.join(references)}；"
            "先清理引用或重跑 task-schedule"
        )
    _close_task(args, "dropped", f"dropped: {args.reason}")

def cmd_rewind(args):
    task, path, fm, body = load_task(args.tid)
    primary_fm = dict(fm)
    primary_body = body
    worktree_rel = ctx.effective_worktree(fm)
    worktree = (ctx.REPO_ROOT / worktree_rel).resolve()

    # 新 start 不回写 main；从主仓 rewind 时读取登记 worktree 中的 active 状态。
    if fm["status"] == "backlog" and str(worktree) in worktree_paths():
        worktree_task = worktree / task["dir"] / "task.md"
        if worktree_task.is_file():
            worktree_fm, _ = parse_front_matter(worktree_task)
            if worktree_fm.get("status") in ("active", "blocked"):
                fm = worktree_fm

    effective = fm["status"]
    recorded = primary_fm["status"]
    if effective not in ctx.STATUS_ORDER:
        sys.exit(
            f"{args.tid} status={effective}；rewind 只处理 {ctx.STATUS_ORDER}"
            "（done/dropped 已归档不可 rewind：放弃用 drop，彻底删除用 purge）"
        )
    target = args.to or ctx.DEFAULT_REWIND.get(effective)
    if target is None:
        sys.exit(f"{args.tid} 已是 backlog，无可撤回")
    if target not in ctx.STATUS_ORDER:
        sys.exit(f"--to {target!r} 非法；须为 {ctx.STATUS_ORDER} 之一")
    if ctx.STATUS_ORDER.index(target) >= ctx.STATUS_ORDER.index(effective):
        sys.exit(f"rewind 只向后：{effective} -> {target} 不是撤回（前进用 start/block）")

    wt_msg = ""
    if target == "backlog":
        require_primary_worktree()
        branch = fm.get("branch", "")
        registered_branch = worktree_paths().get(str(worktree))
        if str(worktree) in worktree_paths():
            if branch and registered_branch and registered_branch != branch:
                sys.exit(
                    f"{worktree_rel} 登记分支为 {registered_branch!r}，"
                    f"与 {args.tid} front matter 分支 {branch!r} 不符；"
                    "拒绝强制移除其他分支的 worktree"
                )
        dirty = porcelain_entries(worktree) if worktree.is_dir() else []
        anchor = fm.get("diff_anchor", "")
        own_commits = False
        if branch and anchor:
            r = _git(["rev-list", "--count", f"{anchor}..{branch}"])
            own_commits = r.returncode != 0 or r.stdout.strip() != "0"
        if (dirty or own_commits) and not args.yes:
            details = []
            if dirty:
                details.append(f"worktree 有 {len(dirty)} 项未提交改动")
            if own_commits:
                details.append(f"分支 {branch!r} 有当前 task commit")
            print(
                f"WARNING: {'；'.join(details)}；rewind 会丢弃 worktree 改动并使未合并分支游离。\n"
                f"分支 {branch!r} 将保留。恢复方式：\n"
                f"  - 继续用旧分支：git worktree add {worktree_rel} {branch}\n"
                f"  - 或删除后重来：git branch -D {branch}\n"
                "继续？(y/N)",
                file=sys.stderr,
            )
            try:
                answer = input()
            except EOFError:
                answer = ""
            if answer.strip().lower() not in ("y", "yes"):
                sys.exit("rewind aborted by user")
        removed, wt_msg = discard_worktree(worktree_rel)
        if not removed:
            sys.exit(f"{wt_msg}\nrewind 中止：worktree 未清理时不能回到 backlog")
        if branch and not own_commits:
            _git(["branch", "-D", branch])
        fm = primary_fm
        body = primary_body
        fm["branch"] = ""
        fm["worktree"] = ""
        fm["diff_anchor"] = ""
    else:
        require_own_task_worktree(fm)

    fm["status"] = target
    if target == "backlog":
        fm["schedule_status"] = "pending_clarification"
    if effective == recorded:
        transition = f"{effective} -> {target}"
    else:
        transition = (
            f"effective={effective} -> {target}（main 记录为 {recorded}）"
        )
    append_note(fm, f"rewound: {transition}; {args.reason}")
    write_front_matter(path, fm, body)
    if target == "backlog":
        rebuild_index()
    append_audit("rewind", tid=args.tid, fr=effective, to=target, reason=args.reason)
    print(f"{args.tid} status={target} (rewound from {effective}){'; ' + wt_msg if wt_msg else ''}")

def cmd_purge(args):
    require_primary_worktree()
    references = task_schedule_references(args.tid)
    if references:
        sys.exit(
            f"{args.tid} 仍被调度图引用：{', '.join(references)}；"
            "先清理引用后再 purge"
        )
    task, path, fm, body = load_task(args.tid)
    require_status(fm, "backlog")
    task_dir = ctx.REPO_ROOT / task["dir"]
    if tracked_anywhere(task["dir"]):
        sys.exit(
            f"{args.tid} 的 task 目录已被任一分支跟踪；"
            "purge 只用于从未提交的误建，请改用 drop 归档"
        )
    if fm.get("branch") and has_unmerged_commits(fm["branch"]):
        sys.exit(f"{args.tid} 分支 {fm['branch']!r} 有未合并 commit；purge 拒绝（请用 drop）")
    append_audit(
        "purge", tid=fm["tid"], fr="backlog", to="deleted", reason=args.reason,
        slug=fm["slug"], title=fm["title"],
    )
    removed, wt_msg = remove_worktree(fm.get("worktree", ""))
    if not removed:
        sys.exit(f"{wt_msg}\npurge 中止：worktree 未清理时删除 task 目录会留下无主工作区")
    shutil.rmtree(task_dir)
    rebuild_index()
    print(f"{args.tid} purged（tid 已释放；审计见 {ctx._rel(ctx.AUDIT_PATH)}）")

def cmd_list(args):
    if args.status and args.status not in ctx.VALID_STATUSES:
        sys.exit(f"status {args.status!r} 非法；可选 {ctx.VALID_STATUSES}")
    ref_arg = args.ref
    if ref_arg and args.rebuild:
        sys.exit("--ref 为只读快照，不能与 --rebuild 同用")
    if ref_arg:
        ref_name, ref_sha = resolve_local_branch(ref_arg)
        tasks = scan_tasks_at_ref(ref_sha)
        source = f"ref={ref_name}@{ref_sha[:12]}"
    elif args.rebuild:
        require_primary_worktree()
        tasks = rebuild_index()
        source = ""
        print(f"index rebuilt: {ctx._rel(ctx.ACTIVE_PATH)}, {ctx._rel(ctx.ARCHIVE_PATH)}")
    else:
        tasks = scan_tasks()  # 默认只读：罗列不该有写副作用
        source = ""
    rows = [t for t in tasks if not args.status or t["status"] == args.status]
    if not rows:
        print(f"(no tasks) {source}" if source else "(no tasks)")
        return
    if source:
        print(f"source: {source}")
    print("| tid    | title                              | status   | lvl    | branch                        | note |")
    print("|--------|------------------------------------|----------|--------|-------------------------------|------|")
    for t in rows:
        print(
            f"| {t['tid']:<6} | {t['title'][:34]:<34} | {t['status']:<8} | "
            f"{(t.get('review_level') or ''):<6} | {(t.get('branch') or '')[:29]:<29} | {(t.get('note') or '')[:40]} |"
        )

def cmd_show(args):
    ref_arg = args.ref
    if ref_arg:
        ref_name, ref_sha = resolve_local_branch(ref_arg)
        task, fm, _ = load_task_at_ref(args.tid, ref_sha)
        fields = dict(fm)
        fields["source_ref"] = f"{ref_name}@{ref_sha[:12]}"
        fields["dir"] = task["dir"]
        fields["task_md"] = f"{ref_name}:{task['dir']}/task.md"
    else:
        task, path, fm, body = load_task(args.tid)
        fields = dict(fm)
        fields["dir"] = task["dir"]
        fields["task_md"] = ctx._rel(path)
    width = max(len(k) for k in fields)
    for k, v in fields.items():
        print(f"{k.ljust(width)}: {v}")
