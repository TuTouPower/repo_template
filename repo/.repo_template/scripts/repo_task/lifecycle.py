"""Canonical lifecycle implementation for the task toolchain."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

import repo_task.context as ctx

from .locks import control_locked

from .documents import dump_tid_list, parse_front_matter, parse_tid_list, tid_sort_key, validate_task_documents, validate_tid_references, write_front_matter, write_front_matter_many
from .git_ops import _get_head, _git, has_unmerged_commits, in_own_task_worktree, porcelain_entries, require_own_task_worktree, require_primary_worktree, resolve_local_branch, tracked_anywhere, worktree_paths
from .locks import TASK_ID_LOCK_NAME, git_common_lock
from .scheduling import _dependency_cycle
from .store import append_audit, append_note, git_text_at_ref, load_task, load_task_at_ref, rebuild_index, require_status, scan_tasks, scan_tasks_at_ref, task_effective_state, task_schedule_references
from .worktrees import discard_worktree, remove_worktree

@control_locked
def cmd_add(args):
    require_primary_worktree()
    if not ctx.SLUG_RE.match(args.slug):
        sys.exit(f"slug 须匹配 {ctx.SLUG_RE.pattern}（收到 {args.slug!r}）")
    if not args.title.strip():
        sys.exit("title 不能为空")
    if not ctx.TEMPLATE_DIR.is_dir():
        sys.exit(f"缺模板目录 {ctx._rel(ctx.TEMPLATE_DIR)}")
    template_task = ctx.TEMPLATE_DIR / "task.md"
    template_note = ctx.TEMPLATE_DIR / ctx.PROGRESS_FILE
    if not template_task.is_file() or not template_note.is_file():
        sys.exit(f"模板目录 {ctx._rel(ctx.TEMPLATE_DIR)} 缺 task.md 或 {ctx.PROGRESS_FILE}")
    _, template_note_body = parse_front_matter(template_note)
    template_problems, _ = validate_task_documents(
        template_task.read_text(encoding="utf-8"),
        template_note_body,
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
        note_md = task_dir / ctx.PROGRESS_FILE
        fm, body = parse_front_matter(note_md)
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
        write_front_matter(note_md, fm, body)
        try:
            rebuild_index()
        except (OSError, ctx.TaskDataError) as error:
            shutil.rmtree(task_dir, ignore_errors=True)
            sys.exit(f"add 失败（{error}）；已回滚新建目录 {ctx._rel(task_dir)}")
    print(f"added {tid} '{fm['title']}' status=backlog review_level={fm['review_level']}")
    print(f"工作区：{ctx._rel(task_dir)}（已从模板复制 task.md / {ctx.PROGRESS_FILE}）")

@control_locked
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
            if (
                fence
                and fence.group(1)[0] == fence_marker[0]
                and len(fence.group(1)) >= fence_marker[1]
            ):
                fence_marker = None
                if current:
                    found[current] = True
            continue
        fence = ctx.FENCE_RE.match(line)
        if fence:
            if current:
                found[current] = True
            fence_marker = (fence.group(1)[0], len(fence.group(1)))
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
        msg = "status=backlog，须先 start"
        if source_ref:
            covered = task_effective_state(args.tid, fm)
            if covered:
                msg += (
                    f"（快照滞后：{covered}；"
                    "--ref 仅读取 Git 提交快照，请在 task worktree 内部直接运行 preflight）"
                )
            else:
                msg += f"（ref 快照 {source_ref} 中仍为 backlog；--ref 仅读取 Git 提交快照）"
        problems.append(msg)
    elif fm["status"] not in ("active", "backlog"):
        problems.append(f"status={fm['status']}，不可执行")

    # 2. task 需求规格完整与未知契约
    task_doc_rel = f"{task['dir']}/task.md"
    if source_ref:
        try:
            text = git_text_at_ref(ref_sha, task_doc_rel)
        except ctx.TaskDataError:
            text = ""
            problems.append("缺 task.md")
    else:
        task_doc = task_dir / "task.md"
        if not task_doc.is_file():
            text = ""
            problems.append("缺 task.md")
        else:
            text = task_doc.read_text(encoding="utf-8")
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
        if fm["status"] == "backlog":
            uncommitted = [
                p for p in dirty
                if not p.startswith(task["dir"])
                and p not in ("docs/tasks_index.json", "docs/archive/tasks_index.json")
                and not p.startswith(".scratch/")
            ]
            if uncommitted:
                warnings.append(
                    f"工作区有 {len(uncommitted)} 项未提交改动：{', '.join(uncommitted[:5])}；建议清理或提交后再 start"
                )
        elif fm["status"] == "active":
            cross_task = [
                p for p in dirty
                if p.startswith("docs/tasks/")
                and not p.startswith(task["dir"])
            ]
            if cross_task:
                warnings.append(
                    f"工作区混入其他 task 目录改动：{', '.join(cross_task[:5])}"
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

    from .validation import load_policy, parse_policy, ValidationError
    try:
        if source_ref:
            parse_policy(git_text_at_ref(ref_sha, "docs/blueprint/testing.md"))
        else:
            load_policy(ctx.REPO_ROOT)
    except (ValidationError, ctx.TaskDataError) as error:
        (warnings if creation else problems).append(str(error))

    if not source_ref and fm["status"] == "active":
        from .attempts import current_attempt_record
        from .git_ops import primary_worktree_path
        from .ledger import ledger_read
        record = current_attempt_record(args.tid, ledger_read(primary_worktree_path() / "docs/runtime/dispatch_ledger.jsonl"))
        if record and record["state"] == "running":
            owner = record.get("owner_id")
            if not owner or owner != getattr(args, "owner_id", None):
                problems.append("执行权未领取或 owner_id 不匹配；--owner-id 须与 run-begin 领取的 owner_id 一致（未 claim 的现场先按 recovery 补 claim）")

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


def cmd_review_upgrade(args):
    _, path, fm, body = load_task(args.tid)
    require_status(fm, "active")
    require_own_task_worktree(fm)
    if not args.reason.strip():
        sys.exit("review 升级必须记录 --reason")
    if fm["review_level"] != "single":
        sys.exit("仅允许 active single -> full；不支持降级或重复升级")
    fm["review_level"] = "full"
    append_note(fm, f"review single -> full: {args.reason.strip()}")
    write_front_matter(path, fm, body)
    print(f"{args.tid} review_level=full；旧 general review 不再满足门禁，须重审 code/test")


def _require_acs_checked(task_md_path: Path) -> None:
    if not task_md_path.is_file():
        return
    from .documents import extract_ac_statuses
    ac_statuses = extract_ac_statuses(task_md_path.read_text(encoding="utf-8"))
    if not ac_statuses:
        sys.exit(f"finish=FAIL：{ctx._rel(task_md_path)} 验收标准未定义任何 AC")
    uncompleted = [ac_id for ac_id, checked in ac_statuses if not checked]
    if uncompleted:
        sys.exit(
            f"finish=FAIL：{ctx._rel(task_md_path)} 存在未完成 AC（未打勾）：{', '.join(uncompleted)}"
        )


def _check_done_ready(tid: str, task_dir: Path) -> None:
    """done 的写前检查：AC 全勾、finalization 闭环。"""
    _require_acs_checked(task_dir / "task.md")
    from .finalization import check_finalization
    try:
        from .validation import load_policy
        load_policy(ctx.REPO_ROOT)
        check_finalization(ctx.REPO_ROOT, tid, task_dir)
    except ValueError as error:
        sys.exit(f"finish=FAIL：{error}")


def _require_archive_target_free(fm: dict) -> Path:
    dst = ctx.ARCHIVE_TASKS_DIR / f"{fm['tid']}_{fm['slug']}"
    if dst.exists():
        sys.exit(f"归档目录已存在：{ctx._rel(dst)}（数据冲突，请提示用户）")
    return dst


def _close_task(args, status: str, note: str | None, *, prechecked: bool = False) -> None:
    """done / dropped 收尾：先做 git 侧动作，再单次写盘；dropped 最后归档目录。

    done 只在原位写 `status: done`，目录留在 `docs/tasks/`，由 integrate 的 merge commit
    移入归档。dropped 仍由本函数把目录移入 `docs/archive/tasks/`。
    单次写盘是为了避免「front matter 已写 dropped、目录未归档」的中间态——
    那种状态下 drop/rewind 两条出口全被状态校验挡死，只能手改 front matter。
    归档移动失败时回滚 front matter，同理避免上述死锁。
    prechecked=True 表示调用方（finish）已完成 done 的写前检查。
    """
    task, path, fm, body = load_task(args.tid)
    archive = status != "done"
    if status == "done":
        require_status(fm, "active")
        require_own_task_worktree(fm)
        if not prechecked:
            _check_done_ready(args.tid, path.parent)
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
    dst = _require_archive_target_free(fm) if archive else None

    in_own_worktree = in_own_task_worktree(fm)
    if in_own_worktree:
        removed, wt_msg = False, (
            f"worktree {fm['worktree']} 待主仓 run-close 清理"
        )
    else:
        removed, wt_msg = remove_worktree(
            ctx.effective_worktree(fm), expected_branch=fm.get("branch")
        )

    orig_fm = dict(fm)
    fm = dict(fm)
    fm["status"] = status
    if note:
        append_note(fm, note)
    if in_own_worktree or removed:
        fm["worktree"] = ""
    else:
        append_note(fm, f"worktree 未移除：{ctx.effective_worktree(fm)}")
    write_front_matter(path, fm, body)
    if not archive:
        print(f"{args.tid} status={status}; 目录留在 {task['dir']}（integrate 时归档）; {wt_msg}")
        return

    ctx.ARCHIVE_TASKS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(src), str(dst))
    except (OSError, shutil.Error) as e:
        # 移动失败：front matter 已写成目标状态但目录仍在原处。完整回滚原始 front matter（Issue #51）
        rollback_note = ""
        if src.exists():
            try:
                write_front_matter(path, orig_fm, body)
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
    # main 上直接 drop 会改变 task 的目录归属；同步两个派生索引。
    # active worktree 中的 drop 由后续 task commit/integrate 收尾索引，
    # 此处不能提前把索引写进执行分支，否则会制造 merge 冲突。
    if not in_own_worktree:
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

def _unstaged_outside(task_dirs: tuple[str, ...]) -> list[str]:
    """task 目录之外未暂存或未跟踪的非忽略路径；git 失败时 fail-closed。"""
    result = _git(["status", "--porcelain", "-z", "--untracked-files=all"])
    if result.returncode != 0:
        raise ctx.TaskDataError(f"git status 失败：{result.stderr.strip()}")
    fields, index, found = result.stdout.split("\0"), 0, set()
    while index < len(fields):
        entry = fields[index]
        index += 1
        if not entry:
            continue
        code, path = entry[:2], entry[3:]
        if code[0] in ("R", "C"):
            index += 1
        if any(path == item or path.startswith(item + "/") for item in task_dirs):
            continue
        if code == "??" or code[1] != " ":
            found.add(path)
    return sorted(found)


def _git_index_path() -> Path:
    result = _git(["rev-parse", "--absolute-git-dir"])
    if result.returncode != 0 or not result.stdout.strip():
        raise ctx.TaskDataError("无法解析 git 目录，无法保存暂存区快照")
    return Path(result.stdout.strip()) / "index"


def _hooks_mirror_template() -> bool:
    result = _git(["config", "--get", "core.hooksPath"])
    configured = result.stdout.strip() if result.returncode == 0 else ""
    if not configured:
        return False
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = ctx.REPO_ROOT / path
    return path.resolve() == (ctx.REPO_ROOT / ".repo_template" / "hooks").resolve()


def _load_md_format():
    try:
        import md_format
    except ImportError:
        return None
    return md_format


def _markdown_paths_to_check(md_format, task_dir: Path) -> list[str]:
    result = _git(["diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR", "--", "*.md"])
    if result.returncode != 0:
        sys.exit(f"finish=FAIL：无法列出已暂存 .md：{result.stderr.strip()}；零写入退出")
    rels = {
        path for path in result.stdout.split("\0")
        if path and not path.startswith(md_format.BLACKLIST_PREFIXES)
    }
    for path in task_dir.rglob("*.md"):
        rel = path.relative_to(ctx.REPO_ROOT).as_posix()
        if not rel.startswith(md_format.BLACKLIST_PREFIXES):
            rels.add(rel)
    return sorted(rels)


def _check_markdown_format(task_dir: Path) -> None:
    md_format = _load_md_format()
    if md_format is None:
        print("WARN: 缺 md_format.py，跳过 Markdown 格式检查", file=sys.stderr)
        return
    rels = _markdown_paths_to_check(md_format, task_dir)
    if not rels:
        return
    if md_format.find_md_kx() is None:
        if _hooks_mirror_template():
            sys.exit(
                "finish=FAIL：已启用 .repo_template/hooks 的 pre-commit，但找不到 md_kx；"
                "安装后重跑 finish。零写入退出"
            )
        print("WARN: 未安装 md_kx，跳过 Markdown 格式检查", file=sys.stderr)
        return
    try:
        drifted = md_format.format_paths(rels, check=True)
    except md_format.MdFormatError as error:
        sys.exit(f"finish=FAIL：Markdown 格式检查失败：{error}；零写入退出")
    if drifted:
        sys.exit(
            "finish=FAIL：Markdown 格式漂移："
            + ", ".join(drifted)
            + "。先运行 `python3 .repo_template/scripts/md_format.py --changed`；"
            "交付内容变化须重新 review。零写入退出"
        )


def _finish_prechecks(args) -> dict:
    """finish 的全部前置检查；任何一项失败都在写入前 sys.exit，零写入。"""
    from .attempts import current_attempt_record, resolve_identity
    from .git_ops import primary_worktree_path
    from .ledger import ledger_read
    from .monitoring import build_handoff, review_scope_fingerprint
    from .review import ReviewDataError, evaluate_review

    tid = args.tid
    message_path = Path(args.message_file).expanduser().resolve()
    try:
        message = message_path.read_text(encoding="utf-8")
    except OSError as error:
        sys.exit(f"finish=FAIL：无法读取 --message-file {args.message_file}（{error}）")
    if not message.strip():
        sys.exit(f"finish=FAIL：--message-file {args.message_file} 为空；写入实现 commit message")
    task, path, fm, body = load_task(tid)
    require_status(fm, "active")
    require_own_task_worktree(fm)
    task_dir = path.parent
    if message_path.is_relative_to(task_dir.resolve()):
        sys.exit(
            f"finish=FAIL：--message-file {args.message_file} 位于当前 task 目录内，"
            "会被一并提交；移到仓外或 .scratch/。零写入退出"
        )

    # 1. 严格 preflight（等价 preflight --require-verified --owner-id）
    try:
        cmd_preflight(argparse.Namespace(
            tid=tid, allow_backlog=False, creation=False, ref=None,
            require_verified=True, owner_id=args.owner_id,
        ))
    except SystemExit as stop:
        if stop.code not in (0, None):
            sys.exit("finish=FAIL：严格 preflight 未通过（见上）；零写入退出")
        raise

    # 2. AC 全部打勾 + finalization 闭环；done 归档目标只读占用检查
    _check_done_ready(tid, task_dir)
    _require_archive_target_free(fm)

    # 3. Markdown 零写入格式检查（含已暂存 .md 与当前 task 目录）
    _check_markdown_format(task_dir)

    # 4. 除 finish 自身改动的 task 目录外，交付文件须已暂存且无未跟踪文件
    loose = _unstaged_outside((task["dir"],))
    if loose:
        shown = ", ".join(loose[:8]) + (f" 等共 {len(loose)} 项" if len(loose) > 8 else "")
        sys.exit(
            f"finish=FAIL：存在未暂存或未跟踪的非忽略文件：{shown}。"
            "交付文件先 `git add` 暂存（`git add -N` 不算暂存）；临时文件删除或移入 .scratch/；"
            "message file 放在仓外或 .scratch/。零写入退出"
        )
    head = _get_head()
    if head != fm.get("diff_anchor"):
        sys.exit(
            f"finish=FAIL：HEAD {head[:12]} 不是 diff_anchor {str(fm.get('diff_anchor'))[:12]}；"
            "finish 之前不得创建中间 commit，实现 commit 由 finish 提交。零写入退出"
        )

    # 5. review 门禁
    scope = review_scope_fingerprint(fm.get("diff_anchor", ""), task["dir"])
    try:
        review = evaluate_review(task_dir, fm, scope or None)
    except (ReviewDataError, ctx.TaskDataError) as error:
        sys.exit(f"finish=FAIL：review 门禁无法评估：{error}")
    if review["overall"] != "PASS":
        sys.exit(
            f"finish=FAIL：review 门禁未通过 overall={review['overall']} "
            f"review_scope={review['review_scope']} "
            f"missing_disposition={review['missing_disposition'] or '-'} "
            f"ac_review={review['ac_review']} "
            f"ac_review_detail={review['ac_review_detail']} "
            f"next_action={review['next_action']}；零写入退出"
        )

    # 6. 身份与执行权：execution_context 与主仓 ledger 一致，且 owner 持有 running attempt
    try:
        attempt, execution_id = resolve_identity(tid)
        ledger = primary_worktree_path() / "docs/runtime/dispatch_ledger.jsonl"
        record = current_attempt_record(tid, ledger_read(ledger, tid=tid))
    except ctx.TaskDataError as error:
        sys.exit(f"finish=FAIL：{error}；零写入退出")
    if record["state"] != "running" or record.get("owner_id") != args.owner_id:
        sys.exit(
            f"finish=FAIL：attempt={attempt} state={record['state']!r} "
            f"owner_id={record.get('owner_id')!r}，与 --owner-id 不符；零写入退出"
        )

    # 7. 机器生成 handoff.json 内容（progress.md 验证摘要为空或仍是占位时拒绝）
    try:
        handoff = build_handoff(
            tid=tid, attempt=attempt, execution_id=execution_id, branch=fm["branch"],
            base_sha=head, review_scope=scope, note_body=body, root=ctx.REPO_ROOT,
        )
    except ctx.TaskDataError as error:
        sys.exit(f"finish=FAIL：{error}；零写入退出")
    return {
        "args": args, "message_path": message_path, "note_path": path, "task_dir": task_dir,
        "task_rel": task["dir"], "handoff": handoff, "diff_anchor": head,
        "review_scope": scope,
    }


def _rollback_finish(plan: dict, note_text: str, index_bytes: bytes) -> list[str]:
    """撤销 finish 自身的写入：handoff.json、front matter、暂存。返回失败项。"""
    from .documents import atomic_write_text

    problems = []
    try:
        (ctx.REPO_ROOT / plan["task_rel"] / "handoff.json").unlink(missing_ok=True)
    except OSError as error:
        problems.append(f"handoff.json 删除失败（{error}）")
    try:
        atomic_write_text(plan["note_path"], note_text)
    except (OSError, ctx.TaskDataError) as error:
        problems.append(f"{ctx.PROGRESS_FILE} front matter 还原失败（{error}）")
    try:
        index_path = _git_index_path()
        temporary = index_path.with_name("index.finish_rollback")
        temporary.write_bytes(index_bytes)
        os.replace(temporary, index_path)
    except (OSError, ctx.TaskDataError) as error:
        problems.append(f"暂存区还原失败（{error}）")
    return problems


def cmd_finish(args):
    """active -> done 原子收尾：前置检查零写入，写入后提交；提交失败整体回滚。"""
    plan = _finish_prechecks(args)
    message_path, tid = plan["message_path"], args.tid
    note_text = plan["note_path"].read_text(encoding="utf-8")
    index_bytes = _git_index_path().read_bytes()

    def fail(reason: str) -> None:
        problems = _rollback_finish(plan, note_text, index_bytes)
        if problems:
            sys.exit(
                f"finish=FAIL：{reason}\n回滚未完成：{'；'.join(problems)}。"
                f"现场不一致，运行 `python3 .repo_template/scripts/task.py recovery {tid}` 判定，"
                "勿重复 finish"
            )
        sys.exit(f"finish=FAIL：{reason}\nfinish 自身写入已全部回滚（front matter、handoff.json、暂存）")

    try:
        _close_task(args, "done", None, prechecked=True)
    except SystemExit as stop:
        fail(f"done 写入失败：{stop.code}")
    try:
        from .documents import atomic_write_text
        atomic_write_text(
            ctx.REPO_ROOT / plan["task_rel"] / "handoff.json",
            json.dumps(plan["handoff"], ensure_ascii=False, indent=2) + "\n",
        )
        staged = _git(["add", "-A", "--", plan["task_rel"]])
        if staged.returncode != 0:
            raise ctx.TaskDataError(f"git add 失败：{staged.stderr.strip()}")
        committed = _git(["commit", "-F", str(message_path)], timeout=600)
    except (OSError, ctx.TaskDataError) as error:
        fail(str(error))
    if committed.returncode != 0:
        if _get_head() != plan["diff_anchor"]:
            sys.exit(
                "finish=FAIL：git commit 返回非零但 HEAD 已前进；不回滚。"
                f"核对 `git log` 后运行 `python3 .repo_template/scripts/task.py recovery {tid}`"
            )
        detail = (committed.stderr.strip() or committed.stdout.strip())[-1500:]
        fail(f"git commit 失败（pre-commit / commit-msg hook 拒绝或其它原因）：\n{detail}")
    from .monitoring import review_scope_fingerprint
    actual_scope = review_scope_fingerprint(plan["diff_anchor"], plan["task_rel"], ref="HEAD")
    if actual_scope != plan["review_scope"]:
        sys.exit(
            f"finish=FAIL：提交内容与 review 内容不一致（review_scope={plan['review_scope']!r}，"
            f"HEAD 重算={actual_scope!r}）；HEAD 已前移不回滚，"
            f"运行 `python3 .repo_template/scripts/task.py recovery {tid}`：判为 review_mismatch 后用 finish-unwind 撤销实现 commit"
        )
    print(f"finish=OK {tid} commit={_get_head()}")

@control_locked
def cmd_drop(args):
    references = task_schedule_references(args.tid)
    if references:
        sys.exit(
            f"{args.tid} 仍被调度图引用：{', '.join(references)}；"
            "先清理引用或重跑 task-schedule"
        )
    _close_task(args, "dropped", f"dropped: {args.reason}")

@control_locked
def cmd_rewind(args):
    task, path, fm, body = load_task(args.tid)
    primary_fm = dict(fm)
    primary_body = body
    worktree_rel = ctx.effective_worktree(fm)
    worktree = (ctx.REPO_ROOT / worktree_rel).resolve()

    # 新 start 不回写 main；从主仓 rewind 时读取登记 worktree 中的 active 状态。
    if fm["status"] == "backlog" and str(worktree) in worktree_paths():
        worktree_note = worktree / task["dir"] / ctx.PROGRESS_FILE
        if worktree_note.is_file():
            worktree_fm, _ = parse_front_matter(worktree_note)
            if worktree_fm.get("status") == "active":
                fm = worktree_fm

    effective = fm["status"]
    recorded = primary_fm["status"]
    if effective not in ctx.STATUS_ORDER:
        sys.exit(
            f"{args.tid} status={effective}；rewind 只处理 {ctx.STATUS_ORDER}。"
            "done/dropped 不可 rewind；review 失效的修复见 recovery"
            "（amend 补审进同一实现 commit），禁止 rewind/reserve 新 attempt。"
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
    # 审计前移：audit 失败则状态未写（progress.md 仍 active），rewind 可重试。
    # 若先写 front matter 再审计，失败后「状态已迁 + 审计缺失 + 不可重试」（F15）。
    try:
        append_audit("rewind", tid=args.tid, fr=effective, to=target, reason=args.reason)
    except ctx.TaskDataError as error:
        append_note(fm, f"rewind audit failed: {error}")
        if recorded == target:
            state_hint = f"main 记录已是 {recorded}，重试会以「已是 {target}」退出"
        else:
            state_hint = f"{ctx.PROGRESS_FILE} 仍为 {effective}"
        try:
            write_front_matter(path, fm, body)
        except (OSError, ctx.TaskDataError) as write_error:
            sys.exit(
                f"rewind 审计写入失败（{error}）；{state_hint}；"
                f"结果写回 {ctx.PROGRESS_FILE} 也失败（{write_error}）。"
                "worktree/分支可能已清理，请人工核查后补记审计与状态"
            )
        sys.exit(
            f"rewind 审计写入失败（{error}）；{state_hint}；"
            f"已把 rewind 结果与审计失败写入 {ctx.PROGRESS_FILE} 供追溯，"
            "本次不会自动补审计，请人工补记"
        )
    try:
        write_front_matter(path, fm, body)
    except (OSError, ctx.TaskDataError) as error:
        try:
            append_audit("rewind_failed", tid=args.tid, fr=target, to=effective, reason=f"write_front_matter failed: {error}")
        except Exception:
            pass
        sys.exit(
            f"rewind 状态写入失败（{error}）；权威状态仍处 {effective}，审计已记录失败标记，请排查原因后重试"
        )
    if target == "backlog":
        rebuild_index()
    print(f"{args.tid} status={target} (rewound from {effective}){'; ' + wt_msg if wt_msg else ''}")

@control_locked
def cmd_finish_unwind(args):
    """撤销与 review 内容不一致的实现 commit：分支退回 diff_anchor，progress 还原 active。

    只处理 recovery `review_mismatch` 现场：attempt 为 running、恰好一个实现 commit、
    tip 重算指纹与 handoff.review_scope 不一致、无后继分支、report 未写入 done、
    worktree 干净。撤销后重新 review 再 finish；禁止用 rewind/amend 处理此类现场。
    """
    from .attempts import current_attempt_record, resolve_identity
    from .ledger import ledger_read
    from .monitoring import implementation_commit, verify_integrate_ready
    from .store import discover_effective_tasks
    require_primary_worktree()
    tid = args.tid
    try:
        attempt, execution_id = resolve_identity(tid, args.attempt, args.execution_id)
    except ctx.TaskDataError as error:
        sys.exit(str(error))
    record = current_attempt_record(tid, ledger_read())
    if record is None:
        sys.exit(f"{tid} 无 attempt 记录；按 recovery 判定处理，不撤销")
    report = (record.get("report") or {})
    if report.get("status") == "done":
        sys.exit(f"{tid} report 已写入 done；实现 commit 不可撤销，停止并人工核查")
    # 正常主路径下 run-close 门禁失败不写 terminal；terminal 已关闭又无 report 只会来自
    # 手工 attempt terminal，撤销后 task 回 active 将无路可走，此处直接拒绝。
    if record["state"] != "running":
        sys.exit(f"{tid} attempt state={record['state']!r}；finish-unwind 只处理 running 现场，停止并人工核查")
    effective = discover_effective_tasks().get(tid)
    if effective is None:
        sys.exit(f"{tid} 不存在")
    branch = effective.get("branch") or f"{tid}_{effective['slug']}"
    worktree = (ctx.REPO_ROOT / ctx.worktree_rel_path(tid)).resolve()
    registered = worktree_paths().get(str(worktree))
    if registered is None:
        sys.exit(f"{tid} 无登记 worktree；按 recovery 判定处理，不撤销")
    if registered != branch:
        sys.exit(f"{tid} 登记分支 {registered!r} 与 task ownership {branch!r} 不符；停止并报告")
    task_dir = worktree / effective["dir"]
    try:
        wt_fm, wt_body = parse_front_matter(task_dir / ctx.PROGRESS_FILE)
    except (OSError, ctx.TaskDataError) as error:
        sys.exit(f"{tid} worktree 进展无法解析（{error}）；停止并人工核查")
    if wt_fm.get("status") != "done":
        sys.exit(f"{tid} worktree 进展 status={wt_fm.get('status')!r}；只有 done 且提交偏离时可撤销")
    diff_anchor = wt_fm.get("diff_anchor") or ""
    head = _git(["rev-parse", "HEAD"], root=worktree)
    if head.returncode != 0:
        sys.exit(f"{tid} worktree HEAD 无法解析：{head.stderr.strip()}")
    tip_sha = head.stdout.strip()
    if tip_sha == diff_anchor:
        sys.exit(f"{tid} 分支 tip 已在 diff_anchor，无实现 commit 可撤销；按 recovery 判定处理")
    try:
        implementation_commit(tip_sha, diff_anchor)
    except ctx.TaskDataError as error:
        sys.exit(f"{tid} 分支形状不符（{error}）；只有恰好一个实现 commit 时可撤销")
    try:
        _, committed_fm, _ = load_task_at_ref(tid, tip_sha)
        committed = json.loads(git_text_at_ref(tip_sha, f"{effective['dir']}/handoff.json"))
        handoff = json.loads((task_dir / "handoff.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError, ctx.TaskDataError) as error:
        sys.exit(f"{tid} tip 执行回执无法核验（{error}）；停止并人工核查")
    expected = {"tid": tid, "branch": branch, "status": "done",
                "attempt": attempt, "execution_id": execution_id, "base_sha": diff_anchor}
    if (
        any(handoff.get(key) != value for key, value in expected.items())
        or committed != handoff or committed_fm.get("status") != "done"
    ):
        sys.exit(f"{tid} tip 未包含当前 handoff；现场与 ledger 不一致，停止并人工核查")
    verdict, kind, detail = verify_integrate_ready(tid, attempt, execution_id)
    if verdict == "ready":
        sys.exit(f"{tid} 门禁已通过，无需撤销；按正常流程 run-close")
    if kind != "review_scope_mismatch":
        sys.exit(f"{tid} 当前失败不是内容不一致（{detail}）；按 recovery 判定处理，不撤销")
    listing = _git(["branch", "--format=%(refname:short)"])
    if listing.returncode != 0:
        sys.exit(f"无法列出分支：{listing.stderr.strip()}")
    for other in [name.strip() for name in listing.stdout.splitlines() if name.strip()]:
        if other == branch:
            continue
        if _git(["merge-base", "--is-ancestor", tip_sha, other]).returncode == 0:
            sys.exit(f"已有后继分支 {other!r} 包含当前 tip；撤销会使其悬空，停止并报告")
    dirty = porcelain_entries(worktree)
    if dirty:
        sys.exit(f"worktree 有 {len(dirty)} 项未提交改动：{', '.join(dirty[:5])}；先恢复干净再撤销")
    reset = _git(["reset", "--soft", diff_anchor], root=worktree)
    if reset.returncode != 0:
        sys.exit(f"{tid} git reset --soft 失败（{reset.stderr.strip()}）；分支未动，人工核查后重试")
    wt_fm = dict(wt_fm)
    wt_fm["status"] = "active"
    # _close_task 在 done 写入时清空了 worktree 归属；撤销后必须恢复，
    # 否则后续 finish 的 require_own_task_worktree 认不出自身 worktree。
    wt_fm["worktree"] = ctx.worktree_rel_path(tid)
    append_note(wt_fm, f"finish-unwind: 撤销与 review 不一致的实现 commit {tip_sha[:12]}，回到 active 重做（{args.reason}）")
    try:
        write_front_matter(task_dir / ctx.PROGRESS_FILE, wt_fm, wt_body)
    except (OSError, ctx.TaskDataError) as error:
        sys.exit(
            f"{tid} 分支已退回 {diff_anchor[:12]}，但 {ctx.PROGRESS_FILE} 写回 active 失败（{error}）；"
            "手动把 status 改回 active 后重新 review 再 finish"
        )
    try:
        append_audit("finish_unwind", tid=tid, fr="done", to="active", reason=args.reason)
    except ctx.TaskDataError as error:
        sys.exit(
            f"{tid} 已撤销且进展已回 active，仅审计缺失（{error}）；"
            "人工补记审计后重新 review 再 finish"
        )
    print(
        f"{tid} 已撤销实现 commit {tip_sha[:12]}：分支退回 {diff_anchor[:12]}，进展回到 active。"
        "旧 handoff.json 仍暂存于 worktree，finish 会重新生成，无需手动处理。"
        "后续：重新 review（报告须对应当前内容），再跑 finish；禁止 amend 旧 commit。"
    )

@control_locked
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
            slug=fm.get("slug", ""), title=fm.get("title", ""),
        )
    except (OSError, ctx.TaskDataError) as error:
        restored = False
        try:
            os.replace(tomb, task_dir)  # 恢复，避免已删但无审计
            restored = True
            rebuild_index()  # 目录恢复后重建派生索引，保持与权威目录严格一致（Issue #35）
        except OSError:
            pass
        if restored:
            sys.exit(f"purge 收尾失败（{error}）；任务目录及派生索引已完整恢复")
        else:
            sys.exit(f"purge 收尾失败（{error}）；目录保留于 {ctx._rel(tomb)}，请人工处理")
    try:
        shutil.rmtree(tomb)
    except OSError as error:
        print(
            f"{args.tid} purged（tid 已释放；审计见 {ctx._rel(ctx.AUDIT_PATH)}）；"
            f"警告：临时文件清理失败（{error}），残留于 {ctx._rel(tomb)}，请人工删除"
        )
        return
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
        fields["progress_md"] = f"{ref_name}:{task['dir']}/{ctx.PROGRESS_FILE}"
        fields["task_md"] = f"{ref_name}:{task['dir']}/task.md"
    else:
        task, path, fm, body = load_task(args.tid)
        fields = dict(fm)
        fields["dir"] = task["dir"]
        fields["progress_md"] = ctx._rel(path)
        task_md = path.parent / "task.md"
        fields["task_md"] = (
            ctx._rel(task_md) if task_md.is_file() else f"{ctx._rel(task_md)}（缺失）"
        )
    width = max(len(k) for k in fields)
    for k, v in fields.items():
        print(f"{k.ljust(width)}: {v}")
