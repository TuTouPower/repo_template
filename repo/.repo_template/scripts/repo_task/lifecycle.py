"""Canonical lifecycle implementation for the task toolchain."""

import os
import re
import shutil
import sys
from pathlib import Path

import repo_task.context as ctx

from .documents import dump_tid_list, parse_front_matter, parse_tid_list, tid_sort_key, validate_task_documents, validate_tid_references, write_front_matter, write_front_matter_many
from .git_ops import _git, has_unmerged_commits, in_own_task_worktree, porcelain_entries, require_own_task_worktree, require_primary_worktree, resolve_local_branch, tracked_anywhere, worktree_paths
from .locks import TASK_ID_LOCK_NAME, git_common_lock
from .scheduling import _dependency_cycle
from .store import append_audit, append_note, git_text_at_ref, load_task, load_task_at_ref, rebuild_index, require_status, scan_tasks, scan_tasks_at_ref, task_effective_state, task_schedule_references
from .worktrees import discard_worktree, remove_worktree

def cmd_add(args):
    require_primary_worktree()
    if not ctx.SLUG_RE.match(args.slug):
        sys.exit(f"slug 须匹配 {ctx.SLUG_RE.pattern}（收到 {args.slug!r}）")
    if not args.title.strip():
        sys.exit("title 不能为空")
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

    # 取号 + 建目录持 git 公共锁，与 pending/findings 取号互斥，防并发撞号（RT-009）
    with git_common_lock(ctx.REPO_ROOT, TASK_ID_LOCK_NAME):
        tasks = scan_tasks()
        for t in tasks:
            if t["slug"] == args.slug:
                sys.exit(f"slug 已存在：{args.slug}（{t['tid']}）")
        n = max((int(ctx.TID_RE.match(t["tid"]).group(1)) for t in tasks), default=0) + 1
        tid = f"t{n:03d}"
        task_dir = ctx.TASKS_DIR / f"{tid}_{args.slug}"
        if task_dir.exists():
            sys.exit(f"{ctx._rel(task_dir)} 已存在；请提示用户处理")
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
        try:
            rebuild_index()
        except (OSError, ctx.TaskDataError) as error:
            shutil.rmtree(task_dir, ignore_errors=True)
            sys.exit(f"add 失败（{error}）；已回滚新建目录 {ctx._rel(task_dir)}")
    print(f"added {tid} '{fm['title']}' status=backlog review_level={fm['review_level']}")
    print(f"工作区：{ctx._rel(task_dir)}（已从模板复制 spec.md / task.md）")

def cmd_edit(args):
    require_primary_worktree()
    field_names = (
        "title", "note", "note_append", "review_level", "depends_on",
        "depends_append", "depends_remove", "conflicts_with",
        "conflicts_append", "conflicts_remove",
    )
    values = {name: getattr(args, name, None) for name in field_names}
    if all(value is None for value in values.values()):
        sys.exit(
            "没有要改的字段；传 --title / --note / --note-append / --review-level / "
            "--depends-* / --conflicts-*"
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
            "active 请在自身 worktree 内编辑"
        )
    covered = task_effective_state(args.tid, fm)
    if covered:
        sys.exit(
            f"{args.tid} 在 main 中为 backlog，但{covered}；"
            "main 副本已滞后，edit 拒绝操作过期状态"
        )

    changed = []
    peer_conflict_update = None
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
            peer_tid = remove_tid[0]
            _peer_task, peer_path, peer_fm, peer_body = load_task(peer_tid)
            peer_conflicts = parse_tid_list(
                peer_fm.get("conflicts_with", ""), field=f"{peer_tid}.conflicts_with"
            )
            local_declares = peer_tid in conflicts
            peer_declares = args.tid in peer_conflicts
            if not local_declares and not peer_declares:
                sys.exit(f"{args.tid} 与 {peer_tid} 不存在有效 conflicts_with 关系")
            if local_declares:
                conflicts.remove(peer_tid)
            if peer_declares:
                if peer_fm["status"] != "backlog" or task_effective_state(peer_tid, peer_fm):
                    sys.exit(
                        f"{peer_tid} 仍声明与 {args.tid} 冲突，但其状态不可安全编辑；"
                        "请先处理该 task 后再移除关系"
                    )
                peer_conflicts.remove(args.tid)
                peer_fm["conflicts_with"] = dump_tid_list(peer_conflicts)
                peer_conflict_update = (peer_path, peer_fm, peer_body)
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

        fm["conflicts_with"] = dump_tid_list(conflicts)
        changed.append(f"conflicts_with={fm['conflicts_with']!r}")


    if peer_conflict_update is not None:
        write_front_matter_many([peer_conflict_update, (path, fm, body)])
    else:
        write_front_matter(path, fm, body)
    rebuild_index()
    print(f"{args.tid} updated: {', '.join(changed)}")

_TESTING_PENDING_RE = re.compile(
    r"^[\s`*_/、。:：-]*(待填|待补|未填|占位|todo|tbd)[\s`*_/、。:：-]*$", re.IGNORECASE
)


def _testing_line_has_content(line: str, placeholders: tuple[str, ...]) -> bool:
    """正文行剥去门禁占位符字面后是否仍承载内容。

    覆盖章节体内占位符回显（`{doctor_cmd}` 待填）不算已配置；对其他占位符的
    有效引用（如「同 {test_cmd}」）算已配置。
    """
    body = re.sub(r"\{(" + "|".join(placeholders) + r")\}", "", line).strip()
    if not body:
        return False
    return not _TESTING_PENDING_RE.fullmatch(body)


def _missing_testing_sections(text: str) -> list[str]:
    """检查三个门禁章节是否已配置（章节体存在非占位内容）。

    章节名是 preflight 与 skill 的机械锚点。用 HEADING_RE 识别标题行（同级及
    更高层标题结束当前章节，章节内 ### 小节仍属章节），用 FENCE_RE/
    FENCE_CLOSE_RE 跳过 fenced code——fence 内的 `#` 注释行不是标题，fence
    本身计作已配置。正文行剥去占位符字面后无剩余内容（或仅为待填标记）的
    章节视为未配置；正文写「无」按 task-preflight / task-work skill 的门禁
    语义裁定，此处视为已配置。
    """
    sections = ("doctor_cmd", "test_cmd", "blackbox_verify")
    found = {name: False for name in sections}
    current = None
    current_level = 0
    fence_marker = None
    for line in text.splitlines():
        if fence_marker is not None:
            fence = ctx.FENCE_CLOSE_RE.match(line)
            if fence and fence.group(1)[0] == fence_marker:
                fence_marker = None
                if current:
                    found[current] = True
            continue
        fence = ctx.FENCE_RE.match(line)
        if fence:
            if current:
                found[current] = True
            fence_marker = fence.group(1)[0]
            continue
        heading = ctx.HEADING_RE.fullmatch(line)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip().strip("`").lower()
            if current and level > current_level:
                found[current] = True
                continue
            current = title if title in found else None
            current_level = level if current else 0
            continue
        if current and line.strip() and _testing_line_has_content(line, sections):
            found[current] = True
    return [f"{{{name}}}" for name in sections if not found[name]]


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
    creation = getattr(args, "creation", False)
    if creation and (fm["status"] != "backlog" or args.require_verified):
        sys.exit("--creation 仅用于 backlog，且不能与 --require-verified 同用")
    allow_backlog = args.allow_backlog or creation
    if fm["status"] in ctx.ARCHIVED_STATUSES:
        problems.append(f"status={fm['status']}，已归档不可执行")
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
            creation=creation,
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
        missing = _missing_testing_sections(testing_text)
        if missing:
            warnings.append(
                f"testing.md 仍有未填占位符 {' / '.join(missing)}；"
                "门禁命令未定义，task-work 的前置、测试、黑盒与合并后验证无机械锚点。"
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

def cmd_limits(args):
    """Increase persisted execution budgets without changing task status."""
    task, path, fm, body = load_task(args.tid)
    require_status(fm, "active")
    require_own_task_worktree(fm)
    from .review import review_limits
    limits = review_limits(fm)
    changes = []
    for field, arg_name in (("review_limit", "review"), ("verify_limit", "verify")):
        value = getattr(args, arg_name, None)
        if value is None:
            continue
        if value <= limits[field]:
            sys.exit(f"{field} 必须大于当前上限 {limits[field]}；不重置历史轮次")
        changes.append(f"{field}: {limits[field]}->{value}")
        limits[field] = value
    if not changes:
        sys.exit("至少传 --review 或 --verify，且只能增加绝对上限")
    reason = (args.reason or "").strip()
    if not reason:
        sys.exit("调整轮次上限必须 --reason 记录用户授权")
    fm.update({key: str(value) for key, value in limits.items()})
    append_note(fm, f"budget: {', '.join(changes)}; {reason}")
    write_front_matter(path, fm, body)
    print(f"{args.tid} limits updated: {', '.join(changes)}")


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
    elif fm["status"] == "active":
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
        removed, wt_msg = remove_worktree(
            ctx.effective_worktree(fm), expected_branch=fm.get("branch")
        )

    fm = dict(fm)
    orig_status = fm["status"]
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
        # 移动失败：front matter 已写成目标状态但目录仍在原处。回滚 front matter
        # 避免「状态已迁 + 目录未归档」的中间态；回滚本身失败则如实报告现场。
        rollback_note = ""
        if src.exists():
            try:
                fm["status"] = orig_status
                write_front_matter(path, fm, body)
                rollback_note = "；front matter 已回滚"
            except (OSError, ctx.TaskDataError) as rb:
                rollback_note = f"；front matter 回滚失败（{rb}），仍是 status={status}"
        else:
            rollback_note = "；源目录不存在，无法回滚 front matter"
        sys.exit(
            f"归档移动失败（{e}）{rollback_note}；"
            f"目录在 {ctx._rel(src) if src.exists() else ctx._rel(dst)}。排除原因后重试"
        )
    # 归档已成功；此后只派生索引重建，失败不反向搬动状态权威（目录/front matter
    # 保持归档终态），只提示索引待重建。
    # 条件等价于 not in_own_worktree：done 分支前置校验无条件要求自身 worktree，
    # done 恒 in_own_worktree=True。写 dropped 是为保持该不变量显式可见。
    # main 上直接 drop 会改变 task 的目录归属；同步两个派生索引。
    # active worktree 中的 finish/drop 由后续 task commit/integrate 收尾索引，
    # 此处不能提前把索引写进执行分支，否则会制造 merge 冲突。
    if status == "dropped" and not in_own_worktree:
        try:
            rebuild_index()
        except (OSError, ctx.TaskDataError) as e:
            sys.exit(
                f"{args.tid} status={status}; 目录已归档 -> {ctx._rel(dst)}; {wt_msg}\n"
                f"派生索引重建失败（{e}）；归档状态已生效，"
                "运行 `task.py list --rebuild` 修复索引后随维护 commit 入库"
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
            if worktree_fm.get("status") == "active":
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
        sys.exit(f"rewind 只向后：{effective} -> {target} 不是撤回（前进用 start；执行受阻不改变 task 状态）")

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
            result = _git(["branch", "-D", branch])
            if result.returncode != 0:
                print(
                    f"WARNING: 删除分支 {branch!r} 失败（{result.stderr.strip()}）；"
                    f"请手动执行 git branch -D {branch}", file=sys.stderr
                )
        fm = primary_fm
        body = primary_body
        fm["branch"] = ""
        fm["worktree"] = ""
        fm["diff_anchor"] = ""
    else:
        require_own_task_worktree(fm)

    fm["status"] = target
    if effective == recorded:
        transition = f"{effective} -> {target}"
    else:
        transition = (
            f"effective={effective} -> {target}（main 记录为 {recorded}）"
        )
    append_note(fm, f"rewound: {transition}; {args.reason}")
    # 审计前移：audit 失败则状态未写（task.md 仍 active），rewind 可重试。
    # 若先写 front matter 再审计，失败后「状态已迁 + 审计缺失 + 不可重试」（F15）。
    try:
        append_audit("rewind", tid=args.tid, fr=effective, to=target, reason=args.reason)
    except ctx.TaskDataError as error:
        sys.exit(
            f"rewind 审计写入失败（{error}）；task.md 未写仍为 {effective}。"
            "worktree/分支可能已清理；直接重试 rewind 将正常完成"
        )
    write_front_matter(path, fm, body)
    if target == "backlog":
        rebuild_index()
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
    removed, wt_msg = remove_worktree(
        fm.get("worktree", ""), expected_branch=fm.get("branch")
    )
    if not removed:
        sys.exit(f"{wt_msg}\npurge 中止：worktree 未清理时删除 task 目录会留下无主工作区")
    # 先原子移动到可恢复 tombstone，完成 index/审计后再最终删除；
    # 避免 rmtree 成功但收尾失败 → 已删却无审计、index 残留旧项。
    tomb = ctx.REPO_ROOT / ".scratch" / f"purge_{fm['tid']}_{fm['slug']}"
    tomb.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(task_dir, tomb)
    except OSError as error:
        sys.exit(f"purge 中止：无法移动到 tombstone（{error}）")
    try:
        rebuild_index()
        append_audit(
            "purge", tid=fm["tid"], fr="backlog", to="deleted", reason=args.reason,
            slug=fm["slug"], title=fm["title"],
        )
    except (OSError, ctx.TaskDataError) as error:
        try:
            os.replace(tomb, task_dir)  # 恢复，避免已删但无审计
        except OSError:
            pass
        sys.exit(
            f"purge 收尾失败（{error}）；目录保留于 {ctx._rel(tomb)}，请人工处理"
        )
    shutil.rmtree(tomb)
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
