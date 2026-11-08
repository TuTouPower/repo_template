"""entity-storage 一次性实体存储与冷文稿归档收敛迁移。

把 Tasks、Pending、Spikes、审计日志与交接历史从旧的多目录/归档布局
一次性迁移到固定路径契约：
- docs/archive/tasks/tNNN_slug/ -> docs/tasks/tNNN_slug/ (需终态证据 done/dropped)
- docs/tasks/tNNN_slug/         -> 保留原路径并校验身份
- docs/pending/todo/pNNN.md     -> docs/pending/pNNN.md (status: todo)
- docs/pending/parked/pNNN.md   -> docs/pending/pNNN.md (status: parked, 保留 reason)
- docs/archive/pending/pNNN.md  -> docs/pending/pNNN.md (status: closed, 保留 resolution 与 reason)
- docs/archive/spikes/sNNN/     -> docs/spikes/sNNN/ (status: completed, 校验闭环证据)
- docs/spikes/sNNN/             -> 保持原路径并校验身份
- docs/archive/tasks_audit.log  -> docs/runtime/tasks_audit.log (保持原始字节)
- docs/archive/handoff.md       -> 确认进入 Git 历史后移除
- 更新维护文档中的确定引用并清理空旧目录。

默认 dry-run；加 --write 在 FileTransaction 内执行，失败完整回滚。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pending import (
    ENTRY_FILE_RE as PENDING_FILE_RE,
    ID_RE as PENDING_ID_RE,
    VALID_RESOLUTIONS as PENDING_VALID_RESOLUTIONS,
    VALID_STATUSES as PENDING_VALID_STATUSES,
    dump_pending_front_matter,
    validate_pending_front_matter,
)
from repo_task.context import (
    PROGRESS_FILE,
    TID_RE,
    VALID_STATUSES as TASK_VALID_STATUSES,
)
from repo_task.documents import atomic_write_text, parse_front_matter_text
from repo_task.file_transaction import FileTransaction
from repo_task.spikes import (
    ENTRY_DIR_RE as SPIKE_DIR_RE,
    SPIKE_SID_RE,
    validate_spike_metadata,
)

from . import context as ctx


class MigrationError(ctx.SyncError):
    """实体存储迁移阻断异常。"""


@dataclass
class PendingItem:
    pid: str
    slug: str
    source_path: Path
    target_path: Path
    status: str
    resolution: str | None = None
    reason: str | None = None
    fix_ref: str | None = None
    body: str = ""


@dataclass
class SpikeItem:
    sid: str
    slug: str
    source_dir: Path
    target_dir: Path
    status: str
    report_content: str = ""


@dataclass
class TaskItem:
    tid: str
    slug: str
    source_dir: Path
    target_dir: Path
    status: str


@dataclass
class MigrationPlan:
    tasks: list[TaskItem]
    pendings: list[PendingItem]
    spikes: list[SpikeItem]
    audit_log_move: tuple[Path, Path] | None
    audit_log_remove_only: Path | None
    handoff_archive_remove: Path | None
    reference_updates: list[tuple[Path, str, str]]
    dirs_to_clean: list[Path]


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def entity_storage_migration_status(sync=None) -> list[str]:
    """检测消费仓是否仍存在旧实体存储布局，返回待迁移项描述列表。"""
    sync = sync or ctx.current_sync()
    consumer = sync.consumer
    problems: list[str] = []

    # 1. 旧 task 归档目录
    archived_tasks_dir = consumer / "docs/archive/tasks"
    if archived_tasks_dir.is_dir():
        children = [p for p in archived_tasks_dir.iterdir() if p.is_dir() and p.name != ".git"]
        if children:
            problems.append(f"docs/archive/tasks 存在 {len(children)} 个归档任务目录")

    # 2. 旧 pending 子目录
    for sub in ("todo", "parked"):
        d = consumer / "docs/pending" / sub
        if d.is_dir():
            files = [p for p in d.glob("p*.md") if p.is_file()]
            if files:
                problems.append(f"docs/pending/{sub} 存在 {len(files)} 个待办文件")
    archived_pending_dir = consumer / "docs/archive/pending"
    if archived_pending_dir.is_dir():
        files = [p for p in archived_pending_dir.glob("p*.md") if p.is_file()]
        if files:
            problems.append(f"docs/archive/pending 存在 {len(files)} 个归档待办文件")

    # 3. 检查扁平 docs/pending 下的文件是否都具备合格的 front matter
    pending_root = consumer / "docs/pending"
    if pending_root.is_dir():
        for p in pending_root.glob("p*.md"):
            if not p.is_file() or not PENDING_FILE_RE.match(p.name):
                continue
            try:
                fm, _ = parse_front_matter_text(p.read_text(encoding="utf-8"), source=sync.rel(p))
                validate_pending_front_matter(fm, filename=p.name, source=sync.rel(p))
            except Exception:
                problems.append(f"{sync.rel(p)} 缺少或包含非法 Front Matter")

    # 4. 旧 spike 归档目录
    archived_spikes_dir = consumer / "docs/archive/spikes"
    if archived_spikes_dir.is_dir():
        children = [p for p in archived_spikes_dir.iterdir() if p.is_dir() and p.name != ".git"]
        if children:
            problems.append(f"docs/archive/spikes 存在 {len(children)} 个归档 spike 目录")

    # 5. 旧 tasks_audit.log
    old_audit = consumer / "docs/archive/tasks_audit.log"
    if old_audit.is_file():
        problems.append("docs/archive/tasks_audit.log 需迁至 docs/runtime/tasks_audit.log")

    # 6. 旧 handoff.md 归档
    old_handoff = consumer / "docs/archive/handoff.md"
    if old_handoff.is_file():
        problems.append("docs/archive/handoff.md 待退役移除")

    return sorted(problems)


def _parse_legacy_pending_content(
    path: Path, raw_text: str, default_status: str, source_label: str
) -> tuple[dict[str, Any], str]:
    """解析旧 pending 文件（可能带或不带 Front Matter，或仅正文中有状态字段）。"""
    fm: dict[str, Any] = {}
    body = raw_text
    if raw_text.startswith("---"):
        try:
            fm, body = parse_front_matter_text(raw_text, source=source_label)
        except Exception:
            fm = {}
            body = raw_text

    file_match = PENDING_FILE_RE.match(path.name)
    if not file_match:
        raise MigrationError(f"{source_label}: 文件名不符合规范 pNNN_slug.md")
    pid = f"p{int(file_match.group(1)):03d}"
    slug = file_match.group(2)

    status = fm.get("status") or default_status
    resolution = fm.get("resolution")
    reason = fm.get("reason")
    fix_ref = fm.get("fix_ref")

    if status == "parked" and not reason:
        # 从正文中解析「- 暂搁：...」
        m = re.search(r"^[ \t]*-[ \t]*暂搁[ \t]*[:：][ \t]*(.*)$", body, re.MULTILINE)
        if m and m.group(1).strip():
            reason = m.group(1).strip()
    if status == "parked" and reason in ("未开", "不办", "无"):
        reason = None

    if status == "closed":
        # 从正文中解析「- 处理：...」
        m = re.search(r"^[ \t]*-[ \t]*处理[ \t]*[:：][ \t]*(.*)$", body, re.MULTILINE)
        if m:
            line = m.group(1).strip()
            res_match = re.search(r"resolution=([a-z]+)", line)
            if res_match:
                resolution = res_match.group(1)
            reason_match = re.search(r"reason=(.*)$", line)
            if reason_match:
                reason = reason_match.group(1).strip()
            elif not reason:
                # 尝试分割解析：例如「t012；已在 main 修复并验证」或纯理由
                parts = [p.strip() for p in re.split(r"[；;]", line) if p.strip()]
                for part in parts:
                    if part.startswith("resolution="):
                        continue
                    if re.match(r"^t\d{3,}$", part) or part in ("main", "PR", "untracked"):
                        if not fix_ref:
                            fix_ref = part
                    elif not reason and part not in ("未开", "不办", "无"):
                        reason = part

        if reason in ("未开", "不办", "无"):
            reason = None

        if not resolution:
            resolution = "resolved"

    result_fm = {
        "pid": pid,
        "slug": slug,
        "status": status,
    }
    if status == "parked":
        if not reason or not str(reason).strip():
            raise MigrationError(f"{source_label}: 暂搁条目缺少暂搁依据 (reason)")
        result_fm["reason"] = str(reason).strip()
    elif status == "closed":
        if not reason or not str(reason).strip():
            raise MigrationError(f"{source_label}: 闭环条目缺少闭环依据 (reason)")
        if resolution not in PENDING_VALID_RESOLUTIONS:
            raise MigrationError(f"{source_label}: 非法 resolution {resolution!r}")
        result_fm["resolution"] = resolution
        result_fm["reason"] = str(reason).strip()
        if fix_ref:
            result_fm["fix_ref"] = str(fix_ref).strip()

    return result_fm, body


def _validate_spike_closed_evidence(report_text: str, source_label: str) -> None:
    """校验归档 spike 报告是否包含有效的闭环结论与 findings 事实。"""
    if not report_text.strip():
        raise MigrationError(f"{source_label}: report.md 为空")
    # 占位符检测
    for ph in ("{结论}", "{采纳决定}", "{Findings}", "{结论与采纳}"):
        if ph in report_text:
            raise MigrationError(f"{source_label}: report.md 仍包含未替换占位符 {ph}")

    # 必须包含结论/采纳相关内容
    has_conclusion = any(
        kw in report_text for kw in ("## 结论", "## 结果", "## 决策", "结论：", "结论:")
    )
    if not has_conclusion:
        raise MigrationError(f"{source_label}: report.md 缺少实质结论与闭环证据")


def _check_worktrees_clean(root: Path) -> None:
    r = _git(root, "worktree", "list", "--porcelain")
    if r.returncode != 0:
        return
    active_wt = []
    current_root = None
    for line in r.stdout.splitlines() + [""]:
        if line.startswith("worktree "):
            current_root = Path(line.removeprefix("worktree ")).resolve()
        elif line.startswith("branch refs/heads/") and current_root:
            branch = line.removeprefix("branch refs/heads/")
            if current_root != root.resolve() and re.match(r"^t\d{3,}_", branch):
                active_wt.append(f"{branch} @ {current_root}")
        elif not line:
            current_root = None
    if active_wt:
        raise MigrationError(
            "迁移前须清理或收尾已登记 task worktree：\n- " + "\n- ".join(active_wt)
        )


def build_migration_plan(sync=None) -> MigrationPlan:
    """预检并构建实体存储一次性迁移计划；若存在冲突或数据不符则阻断。"""
    sync = sync or ctx.current_sync()
    consumer = sync.consumer

    # 1. 检查 worktree 现场
    _check_worktrees_clean(consumer)

    tasks_plan: list[TaskItem] = []
    pendings_plan: list[PendingItem] = []
    spikes_plan: list[SpikeItem] = []
    dirs_to_clean: list[Path] = []

    # 2. Tasks 预检
    seen_tids: dict[str, Path] = {}
    active_tasks_dir = consumer / "docs/tasks"
    archived_tasks_dir = consumer / "docs/archive/tasks"

    # 收集当前 docs/tasks/
    if active_tasks_dir.is_dir():
        for child in sorted(active_tasks_dir.iterdir()):
            if not child.is_dir() or child.name == "task_template" or child.name.startswith("."):
                continue
            m = re.match(r"^(t\d{3,})_([a-z0-9_]+)$", child.name)
            if not m:
                continue
            tid, slug = m.group(1), m.group(2)
            seen_tids[tid] = child
            prog = child / PROGRESS_FILE
            if not prog.is_file():
                raise MigrationError(f"{sync.rel(child)}: 缺失 {PROGRESS_FILE}")
            fm, _ = parse_front_matter_text(prog.read_text(encoding="utf-8"), source=sync.rel(prog))
            if fm.get("tid") != tid:
                raise MigrationError(f"{sync.rel(prog)}: tid 不匹配（{fm.get('tid')} != {tid}）")
            tasks_plan.append(TaskItem(tid, slug, child, child, fm.get("status", "unknown")))

    # 收集 docs/archive/tasks/
    if archived_tasks_dir.is_dir():
        dirs_to_clean.append(archived_tasks_dir)
        for child in sorted(archived_tasks_dir.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            m = re.match(r"^(t\d{3,})_([a-z0-9_]+)$", child.name)
            if not m:
                continue
            tid, slug = m.group(1), m.group(2)
            if tid in seen_tids:
                raise MigrationError(
                    f"同号多处：tid {tid} 同时存在于 {sync.rel(seen_tids[tid])} 和 {sync.rel(child)}"
                )
            target_dir = active_tasks_dir / child.name
            if target_dir.exists():
                raise MigrationError(f"目标占用：{sync.rel(target_dir)} 已存在")

            prog = child / PROGRESS_FILE
            legacy_note = child / "note.md"
            task_file = child / "task.md"
            source_file = prog if prog.is_file() else (legacy_note if legacy_note.is_file() else task_file)
            if not source_file.is_file():
                raise MigrationError(f"{sync.rel(child)}: 缺少任务进度/过程总账文件")
            fm, _ = parse_front_matter_text(source_file.read_text(encoding="utf-8"), source=sync.rel(source_file))
            status = fm.get("status")
            if status not in ("done", "dropped"):
                raise MigrationError(
                    f"{sync.rel(child)}: 归档任务状态必须为 done 或 dropped（实际为 {status!r}）"
                )
            if status == "done":
                # 校验 handoff.json 终态证据
                handoff = child / "handoff.json"
                if not handoff.is_file():
                    raise MigrationError(f"{sync.rel(child)}: done 任务缺失 handoff.json 终态证据")
                try:
                    hdata = json.loads(handoff.read_text(encoding="utf-8"))
                    if hdata.get("tid") != tid:
                        raise MigrationError(f"{sync.rel(handoff)}: handoff tid 与目录编号不符")
                except json.JSONDecodeError as err:
                    raise MigrationError(f"{sync.rel(handoff)}: handoff.json 损坏：{err}") from err

            seen_tids[tid] = child
            tasks_plan.append(TaskItem(tid, slug, child, target_dir, status))

    # 3. Pending 预检
    seen_pids: dict[str, Path] = {}
    pending_sources: list[tuple[Path, str]] = []

    # 扫描旧目录
    p_todo = consumer / "docs/pending/todo"
    if p_todo.is_dir():
        dirs_to_clean.append(p_todo)
        for f in sorted(p_todo.glob("p*.md")):
            if f.is_file() and PENDING_FILE_RE.match(f.name):
                pending_sources.append((f, "todo"))
    p_parked = consumer / "docs/pending/parked"
    if p_parked.is_dir():
        dirs_to_clean.append(p_parked)
        for f in sorted(p_parked.glob("p*.md")):
            if f.is_file() and PENDING_FILE_RE.match(f.name):
                pending_sources.append((f, "parked"))
    p_archived = consumer / "docs/archive/pending"
    if p_archived.is_dir():
        dirs_to_clean.append(p_archived)
        for f in sorted(p_archived.glob("p*.md")):
            if f.is_file() and PENDING_FILE_RE.match(f.name):
                pending_sources.append((f, "closed"))

    # 扫描已在 docs/pending/ 扁平目录的文件
    p_flat = consumer / "docs/pending"
    if p_flat.is_dir():
        for f in sorted(p_flat.glob("p*.md")):
            if f.is_file() and PENDING_FILE_RE.match(f.name):
                pending_sources.append((f, "todo"))

    for path, default_status in pending_sources:
        match = PENDING_FILE_RE.match(path.name)
        assert match is not None
        pid = f"p{int(match.group(1)):03d}"
        if pid in seen_pids and seen_pids[pid].resolve() != path.resolve():
            raise MigrationError(
                f"同号多处：pid {pid} 同时存在于 {sync.rel(seen_pids[pid])} 和 {sync.rel(path)}"
            )
        seen_pids[pid] = path
        target_path = consumer / "docs/pending" / path.name
        if target_path.exists() and target_path.resolve() != path.resolve():
            raise MigrationError(f"目标占用：{sync.rel(target_path)} 已存在")

        content = path.read_text(encoding="utf-8")
        fm, body = _parse_legacy_pending_content(path, content, default_status, sync.rel(path))
        validate_pending_front_matter(fm, filename=path.name, source=sync.rel(path))
        pendings_plan.append(
            PendingItem(
                pid=fm["pid"],
                slug=fm["slug"],
                source_path=path,
                target_path=target_path,
                status=fm["status"],
                resolution=fm.get("resolution"),
                reason=fm.get("reason"),
                fix_ref=fm.get("fix_ref"),
                body=body,
            )
        )

    # 4. Spikes 预检
    seen_sids: dict[str, Path] = {}
    active_spikes_dir = consumer / "docs/spikes"
    archived_spikes_dir = consumer / "docs/archive/spikes"

    # 收集当前 docs/spikes/
    if active_spikes_dir.is_dir():
        for child in sorted(active_spikes_dir.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            m = SPIKE_DIR_RE.match(child.name)
            if not m:
                continue
            sid = f"s{int(m.group(1)):03d}"
            slug = child.name.split("_", 1)[1]
            seen_sids[sid] = child
            rep = child / "report.md"
            if not rep.is_file():
                raise MigrationError(f"{sync.rel(child)}: 缺失 report.md")
            rep_text = rep.read_text(encoding="utf-8")
            if rep_text.startswith("---"):
                fm, _ = parse_front_matter_text(rep_text, source=sync.rel(rep))
                validate_spike_metadata(fm, expected_sid=sid, expected_slug=slug)
                status = fm.get("status", "active")
            else:
                status = "active"
                fm = {"sid": sid, "slug": slug, "status": "active"}
                validate_spike_metadata(fm, expected_sid=sid, expected_slug=slug)
                rep_text = f"---\nsid: \"{sid}\"\nslug: \"{slug}\"\nstatus: \"active\"\n---\n\n" + rep_text.strip() + "\n"
            spikes_plan.append(SpikeItem(sid, slug, child, child, status, rep_text))

    # 收集 docs/archive/spikes/
    if archived_spikes_dir.is_dir():
        dirs_to_clean.append(archived_spikes_dir)
        for child in sorted(archived_spikes_dir.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            m = SPIKE_DIR_RE.match(child.name)
            if not m:
                continue
            sid = f"s{int(m.group(1)):03d}"
            slug = child.name.split("_", 1)[1]
            if sid in seen_sids:
                raise MigrationError(
                    f"同号多处：sid {sid} 同时存在于 {sync.rel(seen_sids[sid])} 和 {sync.rel(child)}"
                )
            target_dir = active_spikes_dir / child.name
            if target_dir.exists():
                raise MigrationError(f"目标占用：{sync.rel(target_dir)} 已存在")
            rep = child / "report.md"
            if not rep.is_file():
                raise MigrationError(f"{sync.rel(child)}: 缺失 report.md 闭环证据")
            rep_text = rep.read_text(encoding="utf-8")
            _validate_spike_closed_evidence(rep_text, sync.rel(rep))

            # 构造 status: completed
            if rep_text.startswith("---"):
                _, body = parse_front_matter_text(rep_text, source=sync.rel(rep))
            else:
                body = rep_text
            new_rep = f"---\nsid: \"{sid}\"\nslug: \"{slug}\"\nstatus: \"completed\"\n---\n\n" + body.strip() + "\n"
            fm = {"sid": sid, "slug": slug, "status": "completed"}
            validate_spike_metadata(fm, expected_sid=sid, expected_slug=slug)

            seen_sids[sid] = child
            spikes_plan.append(SpikeItem(sid, slug, child, target_dir, "completed", new_rep))

    # 5. tasks_audit.log 预检
    old_audit = consumer / "docs/archive/tasks_audit.log"
    new_audit = consumer / "docs/runtime/tasks_audit.log"
    audit_move = None
    audit_remove_only = None
    if old_audit.is_file():
        if new_audit.is_file():
            if new_audit.read_bytes() == old_audit.read_bytes():
                audit_remove_only = old_audit
            else:
                raise MigrationError(
                    f"目标占用：{sync.rel(new_audit)} 已存在且内容与旧审计日志不同，拒绝覆盖"
                )
        else:
            audit_move = (old_audit, new_audit)

    # 6. docs/archive/handoff.md 预检
    old_handoff = consumer / "docs/archive/handoff.md"
    handoff_remove = None
    if old_handoff.is_file():
        # 确认已进入可恢复的 Git 历史
        diff_res = _git(consumer, "diff", "--quiet", "--", "docs/archive/handoff.md")
        cached_diff = _git(consumer, "diff", "--cached", "--quiet", "--", "docs/archive/handoff.md")
        ls_res = _git(consumer, "ls-files", "--error-unmatch", "docs/archive/handoff.md")
        if diff_res.returncode != 0 or cached_diff.returncode != 0 or ls_res.returncode != 0:
            raise MigrationError(
                "docs/archive/handoff.md 含有未提交修改或未入库内容，请先提交或备份到 Git 历史"
            )
        handoff_remove = old_handoff

    # 7. 维护文档中的引用改写计划
    ref_updates: list[tuple[Path, str, str]] = []
    replacement_map = [
        ("docs/archive/tasks/", "docs/tasks/"),
        ("docs/pending/todo/", "docs/pending/"),
        ("docs/pending/parked/", "docs/pending/"),
        ("docs/archive/pending/", "docs/pending/"),
        ("docs/archive/spikes/", "docs/spikes/"),
        ("docs/archive/tasks_audit.log", "docs/runtime/tasks_audit.log"),
    ]
    for rel_doc in ("docs/handoff.md", "docs/specs_index.md"):
        doc_path = consumer / rel_doc
        if doc_path.is_file():
            text = doc_path.read_text(encoding="utf-8")
            new_text = text
            for old_pat, new_pat in replacement_map:
                new_text = new_text.replace(old_pat, new_pat)
            if new_text != text:
                ref_updates.append((doc_path, text, new_text))

    return MigrationPlan(
        tasks=tasks_plan,
        pendings=pendings_plan,
        spikes=spikes_plan,
        audit_log_move=audit_move,
        audit_log_remove_only=audit_remove_only,
        handoff_archive_remove=handoff_remove,
        reference_updates=ref_updates,
        dirs_to_clean=dirs_to_clean,
    )


def _prune_ancestor_paths(paths: set[str]) -> list[str]:
    """若集合中同时存在父目录与子路径，剪除子路径以防 FileTransaction 重复嵌套复制报错。"""
    sorted_paths = sorted(paths, key=lambda p: (p.count("/"), len(p)))
    roots: list[str] = []
    for p in sorted_paths:
        if not any(p == r or p.startswith(r + "/") for r in roots):
            roots.append(p)
    return sorted(roots)


def _collect_transaction_paths(consumer: Path, plan: MigrationPlan) -> list[str]:
    """收集进入文件事务的所有受影响路径（使用顶级或相对路径）。"""
    paths_set: set[str] = set()

    for t in plan.tasks:
        if t.source_dir != t.target_dir:
            paths_set.add(t.source_dir.relative_to(consumer).as_posix())
            paths_set.add(t.target_dir.relative_to(consumer).as_posix())
    if (consumer / "docs/tasks").exists():
        paths_set.add("docs/tasks")
    if (consumer / "docs/archive/tasks").exists():
        paths_set.add("docs/archive/tasks")

    for p in plan.pendings:
        paths_set.add(p.source_path.relative_to(consumer).as_posix())
        paths_set.add(p.target_path.relative_to(consumer).as_posix())
    if (consumer / "docs/pending").exists():
        paths_set.add("docs/pending")
    if (consumer / "docs/archive/pending").exists():
        paths_set.add("docs/archive/pending")

    for s in plan.spikes:
        paths_set.add(s.source_dir.relative_to(consumer).as_posix())
        paths_set.add(s.target_dir.relative_to(consumer).as_posix())
    if (consumer / "docs/spikes").exists():
        paths_set.add("docs/spikes")
    if (consumer / "docs/archive/spikes").exists():
        paths_set.add("docs/archive/spikes")

    if plan.audit_log_move:
        paths_set.add(plan.audit_log_move[0].relative_to(consumer).as_posix())
        paths_set.add(plan.audit_log_move[1].relative_to(consumer).as_posix())
    if plan.audit_log_remove_only:
        paths_set.add(plan.audit_log_remove_only.relative_to(consumer).as_posix())
    if plan.handoff_archive_remove:
        paths_set.add(plan.handoff_archive_remove.relative_to(consumer).as_posix())

    for doc_path, _, _ in plan.reference_updates:
        paths_set.add(doc_path.relative_to(consumer).as_posix())

    return _prune_ancestor_paths(paths_set)


def migrate_entity_storage(sync=None, *, write: bool = False) -> list[str]:
    """执行 entity-storage 迁移：默认 dry-run，加 write=True 在事务中落盘。"""
    sync = sync or ctx.current_sync()
    consumer = sync.consumer

    problems = entity_storage_migration_status(sync=sync)
    if not problems:
        return []

    plan = build_migration_plan(sync=sync)
    reports: list[str] = []

    # 生成拟定改动清单
    for t in plan.tasks:
        if t.source_dir != t.target_dir:
            reports.append(
                f"Task {t.tid}: {sync.rel(t.source_dir)} -> {sync.rel(t.target_dir)} (status: {t.status})"
            )
        else:
            reports.append(f"Task {t.tid}: 原位保留 {sync.rel(t.source_dir)} (status: {t.status})")

    for p in plan.pendings:
        if p.source_path != p.target_path:
            reports.append(
                f"Pending {p.pid}: {sync.rel(p.source_path)} -> {sync.rel(p.target_path)} (status: {p.status})"
            )
        else:
            reports.append(
                f"Pending {p.pid}: 原位更新 {sync.rel(p.target_path)} (status: {p.status})"
            )

    for s in plan.spikes:
        if s.source_dir != s.target_dir:
            reports.append(
                f"Spike {s.sid}: {sync.rel(s.source_dir)} -> {sync.rel(s.target_dir)} (status: {s.status})"
            )
        else:
            reports.append(
                f"Spike {s.sid}: 原位校验 {sync.rel(s.target_dir)} (status: {s.status})"
            )

    if plan.audit_log_move:
        reports.append(
            f"Audit log: {sync.rel(plan.audit_log_move[0])} -> {sync.rel(plan.audit_log_move[1])}"
        )
    if plan.audit_log_remove_only:
        reports.append(f"Audit log: 移除旧归档 {sync.rel(plan.audit_log_remove_only)}")
    if plan.handoff_archive_remove:
        reports.append(f"Handoff archive: 移除 {sync.rel(plan.handoff_archive_remove)}")
    for doc_path, _, _ in plan.reference_updates:
        reports.append(f"Reference update: 更新 {sync.rel(doc_path)} 中过时路径引用")

    if not write:
        print("模式：dry-run；以下为实体存储迁移计划（未写盘，加 --write 执行落盘）：")
        for r in reports:
            print(f"- {r}")
        return reports

    # 真实写入：持久文件事务保护
    tx_paths = _collect_transaction_paths(consumer, plan)
    transaction = FileTransaction(consumer, ".scratch/repo_sync_rollback")
    try:
        transaction.begin(tx_paths)
    except ValueError as err:
        raise MigrationError(f"事务无法启动：{err}") from err

    try:
        # 1. 搬移并更新 Tasks
        for t in plan.tasks:
            if t.source_dir != t.target_dir:
                t.target_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(t.source_dir), str(t.target_dir))

        # 2. 搬移并重写 Pendings
        for p in plan.pendings:
            p.target_path.parent.mkdir(parents=True, exist_ok=True)
            fm_data = {
                "pid": p.pid,
                "slug": p.slug,
                "status": p.status,
            }
            if p.status == "parked":
                fm_data["reason"] = p.reason
            elif p.status == "closed":
                fm_data["resolution"] = p.resolution
                fm_data["reason"] = p.reason
                if p.fix_ref:
                    fm_data["fix_ref"] = p.fix_ref
            new_text = dump_pending_front_matter(fm_data) + p.body.strip() + "\n"
            atomic_write_text(p.target_path, new_text)
            if p.source_path.resolve() != p.target_path.resolve() and p.source_path.exists():
                p.source_path.unlink()

        # 3. 搬移并重写 Spikes
        for s in plan.spikes:
            if s.source_dir != s.target_dir:
                s.target_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(s.source_dir), str(s.target_dir))
            rep_file = s.target_dir / "report.md"
            atomic_write_text(rep_file, s.report_content)

        # 4. 迁移 tasks_audit.log
        if plan.audit_log_move:
            src_log, dst_log = plan.audit_log_move
            dst_log.parent.mkdir(parents=True, exist_ok=True)
            dst_log.write_bytes(src_log.read_bytes())
            src_log.unlink()
        elif plan.audit_log_remove_only:
            plan.audit_log_remove_only.unlink()

        # 5. 移除 docs/archive/handoff.md
        if plan.handoff_archive_remove and plan.handoff_archive_remove.exists():
            plan.handoff_archive_remove.unlink()

        # 6. 更新引用
        for doc_path, _, new_content in plan.reference_updates:
            atomic_write_text(doc_path, new_content)

        # 7. 清理空旧目录
        for d in plan.dirs_to_clean:
            if d.exists() and d.is_dir():
                # 检查目录是否为空（或仅剩 .DS_Store / 空目录）
                try:
                    for sub in list(d.rglob("*")):
                        if sub.is_file() and sub.name in (".DS_Store", ".gitkeep"):
                            sub.unlink()
                    shutil.rmtree(d)
                except OSError:
                    pass

        # 8. 写入后验证：再次查询必须无旧布局残留
        remaining = entity_storage_migration_status(sync=sync)
        if remaining:
            raise MigrationError(
                "实体存储迁移落盘后仍有未收敛项：\n- " + "\n- ".join(remaining)
            )

        transaction.applied(verified=True)
        transaction.accept()
    except Exception as error:
        try:
            transaction.rollback(automatic=True)
        except Exception as rb_err:
            raise MigrationError(
                f"实体存储迁移失败（{error}）；自动回滚失败：{rb_err}；"
                "备份保留在 .scratch/repo_sync_rollback，请人工恢复"
            ) from error
        raise MigrationError(f"实体存储迁移失败，已自动回滚：{error}") from error

    return reports
