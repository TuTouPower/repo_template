"""entity-storage 一次性统一实体迁移。

单一只读计划统一负责：当前实体清单、旧总账拆分、旧目录转换、重复编号修复与引用更新。
把 Tasks、Pending、Findings、Spikes、审计日志与交接历史收敛到固定路径契约：
- docs/archive/tasks/tNNN_slug/ -> docs/tasks/tNNN_slug/ (校验 done/dropped)
- docs/pending/todo|parked、docs/archive/pending、docs/pending/*.md、docs/pending.md -> docs/pending/
- docs/findings/*.md、docs/findings.md -> docs/findings/
- docs/archive/spikes/sNNN/、docs/spikes/sNNN/ -> docs/spikes/
- docs/archive/tasks_audit.log -> docs/runtime/tasks_audit.log
- docs/archive/handoff.md 移除；维护文档引用更新；清理空旧目录

d / p / s 同号多条在计划阶段一次性改号消除（保留者按首次提交时间稳定确定），
t 重复仍阻断。status / plan / apply 共用同一计划构建；写盘先写全部最终目标、再删源。
计划与执行均不管理事务，由 apply 统一备份、校验与回滚。
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from _id_scan import (
    ENTITIES,
    IdScanError,
    canonical_entry_id,
    parse_entry_dir_name,
    parse_entry_file_name,
)
from pending import (
    ALLOWED_FM_KEYS as PENDING_ALLOWED_FM_KEYS,
    ENTRY_FILE_RE as PENDING_FILE_RE,
    VALID_RESOLUTIONS as PENDING_VALID_RESOLUTIONS,
    dump_pending_front_matter,
    validate_pending_front_matter,
)
from repo_task.context import (
    RepoContext,
    PROGRESS_FILE,
    TaskDataError,
)
from repo_task.documents import (
    atomic_write_text,
    dump_front_matter,
    parse_front_matter_text,
)
from repo_task.store import task_record, scan_tasks
from repo_task.entity_schema import SCHEMA_PATH, SCHEMA_VERSION, current_schema
from repo_task.spikes import (
    ENTRY_DIR_RE as SPIKE_DIR_RE,
    validate_spike_metadata,
)

from . import context as ctx
from .common import _git
from .duplicate_ids_migration import (
    CandidateIdentity,
    assign_final_ids,
    build_reference_reports,
    check_other_branch_duplicates,
    collect_tracked_project_files,
    is_protected_task_asset,
    occupied_max_ids,
    update_exact_path_references,
    validate_and_rewrite_h1,
)
from .legacy_ledgers import plan_legacy_migration

# 旧布局目录由唯一实体描述表派生（当前目录之外的 history_dirs），不再独立硬编码。
_ENTITY_TYPE = {"t": "tasks", "p": "pending", "d": "findings", "s": "spikes"}
LEGACY_ENTITY_DIRS = {
    d: _ENTITY_TYPE[p]
    for p, spec in ENTITIES.items()
    for d in spec.history_dirs
    if d != spec.current_dir
}
_CLEANABLE_NAMES = frozenset({".DS_Store", ".gitkeep"})
_STALE_INDEX_RELS = ("docs/tasks_index.json", "docs/archive/tasks_index.json")
LEDGER_RELS = ("docs/pending.md", "docs/findings.md")
TASK_DIR_RE = ctx.TASK_DIR_RE


class MigrationError(ctx.SyncError):
    """实体存储迁移阻断异常。"""


def _render_front_matter(fm: dict, body: str) -> str:
    """统一 Front Matter 序列化：YAML 双引号转义 + 正文保真。

    消除按字段白名单重建与 f-string 拼接——含引号/反斜杠的 reason 不再写出非法 YAML，
    未知字段由 validate_* 校验阻断而非静默丢弃。
    """
    clean = body.lstrip("\r\n").rstrip()
    return dump_front_matter(fm) + "\n" + (clean + "\n" if clean else "")


def _validate_spike_fm(fm: dict, sid: str, slug: str, rep: Path, sync: ctx.SyncContext) -> None:
    try:
        validate_spike_metadata(fm, expected_sid=sid, expected_slug=slug)
    except (ValueError, TaskDataError) as err:
        raise MigrationError(f"{sync.rel(rep)}: spike Front Matter 非法：{err}") from err


def _reject_symlink_entities(consumer: Path, sync: ctx.SyncContext) -> None:
    """迁移前对每个实体目录 lstat：拒绝任何软链，防沿软链写/删仓外文件。"""
    roots = (
        "docs/tasks", "docs/archive/tasks",
        "docs/pending", "docs/pending/todo", "docs/pending/parked", "docs/archive/pending",
        "docs/findings",
        "docs/spikes", "docs/archive/spikes",
    )
    for rel in roots:
        parent = consumer / rel
        if parent.is_symlink():
            raise MigrationError(f"{rel} 是软链目录，拒绝沿软链迁移实体")
        if not parent.is_dir():
            continue
        for child in parent.iterdir():
            if child.is_symlink() and rel not in LEGACY_ENTITY_DIRS:
                raise MigrationError(
                    f"{sync.rel(child)} 是软链，拒绝沿软链迁移实体（仓外写入风险）"
                )


def _assert_within_consumer(path: Path, sync: ctx.SyncContext) -> None:
    if not sync.contains(path):
        raise MigrationError(f"{sync.rel(path)} 解析到消费仓外，拒绝写入")


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
    content: str = ""
    final_id: str = ""
    source_rel: str = ""
    from_ledger: bool = False
    doc_base_rel: str = ""


@dataclass
class FindingItem:
    orig_id: str
    final_id: str
    slug: str
    source_path: Path
    target_path: Path
    content: str = ""
    source_rel: str = ""
    from_ledger: bool = False
    doc_base_rel: str = ""


@dataclass
class SpikeItem:
    sid: str
    slug: str
    source_dir: Path
    target_dir: Path
    status: str
    report_content: str = ""
    final_id: str = ""
    source_rel: str = ""


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
    findings: list[FindingItem] = field(default_factory=list)
    legacy_ledgers: list[Path] = field(default_factory=list)
    audit_log_move: tuple[Path, Path] | None = None
    handoff_archive_remove: Path | None = None
    reference_updates: list[tuple[Path, str, str]] = field(default_factory=list)
    dirs_to_clean: list[Path] = field(default_factory=list)
    stale_index_paths: list[Path] = field(default_factory=list)
    non_entities: list[Path] = field(default_factory=list)
    renumber_reports: list[str] = field(default_factory=list)
    reference_reports: list[str] = field(default_factory=list)
    transaction_extra_rels: list[str] = field(default_factory=list)


def validate_current_entities(sync: ctx.SyncContext) -> None:
    """目标模型严格验收：t / p / d / s 全部命名、软链、形状与编号唯一性零容忍。"""
    try:
        _reject_symlink_entities(sync.consumer, sync)
        scan_tasks(repo=RepoContext.for_root(sync.consumer))

        for directory, prefix, label in (
            ("docs/pending", "p", "pending"),
            ("docs/findings", "d", "finding"),
        ):
            base = sync.consumer / directory
            if not base.is_dir():
                continue
            seen: set[str] = set()
            for path in sorted(base.iterdir()):
                if path.is_dir() and not path.name.startswith(".tmp_"):
                    raise MigrationError(f"当前 {label} 布局禁止嵌套目录：{sync.rel(path)}")
                if path.is_symlink() or not path.is_file():
                    continue
                name = path.name
                if not re.match(rf"^{prefix}[0-9]", name):
                    continue
                parsed = parse_entry_file_name(prefix, name)
                if parsed is None:
                    raise MigrationError(f"{sync.rel(path)} 文件名不符合规范 {prefix}NNN_slug.md")
                eid = canonical_entry_id(prefix, parsed[0])
                if eid in seen:
                    raise MigrationError(f"重复 {label}：{eid}")
                seen.add(eid)
                if prefix == "p":
                    fm, _ = parse_front_matter_text(
                        path.read_text(encoding="utf-8"), source=sync.rel(path)
                    )
                    validate_pending_front_matter(fm, filename=name, source=sync.rel(path))
                else:
                    validate_and_rewrite_h1(
                        path.read_text(encoding="utf-8"), "d", eid, eid,
                        source_label=sync.rel(path),
                    )

        seen_spikes: set[str] = set()
        spikes_dir = sync.consumer / "docs/spikes"
        if spikes_dir.is_dir():
            for path in sorted(spikes_dir.iterdir()):
                if path.name.startswith(".") or path.name in _CLEANABLE_NAMES or not path.name.startswith("s"):
                    continue
                if not path.is_dir():
                    raise MigrationError(f"非法 spike 项：{sync.rel(path)}")
                match = SPIKE_DIR_RE.fullmatch(path.name)
                if match is None:
                    raise MigrationError(f"非法 spike 目录：{sync.rel(path)}")
                sid = f"s{int(match.group(1)):03d}"
                if sid in seen_spikes:
                    raise MigrationError(f"重复 spike：{sid}")
                seen_spikes.add(sid)
                report = path / "report.md"
                fm, _ = parse_front_matter_text(report.read_text(encoding="utf-8"), source=sync.rel(report))
                _validate_spike_fm(fm, sid, path.name.split("_", 1)[1], report, sync)
    except (OSError, TaskDataError, ValueError) as error:
        raise MigrationError(f"目标实体 schema 非法：{error}") from error


def entity_storage_migration_status(
    sync: ctx.SyncContext, *, plan: "MigrationPlan | None" = None
) -> list[str]:
    """统一检测：旧布局/旧总账/重复编号/引用/其它分支冲突；status/plan/apply 共用同一计划。"""
    consumer = sync.consumer
    problems: list[str] = []
    try:
        if not current_schema(consumer):
            problems.append(f"{SCHEMA_PATH} 缺失；需完成实体转换与目标验收后建立 schema 边界")
    except TaskDataError as error:
        problems.append(f"BLOCKED: {error}")
    try:
        validate_current_entities(sync)
    except MigrationError as error:
        problems.append(str(error))

    for ledger in LEDGER_RELS:
        if (consumer / ledger).exists() or (consumer / ledger).is_symlink():
            problems.append(f"{ledger} 待拆分并纳入统一编号")

    for rel in LEGACY_ENTITY_DIRS:
        directory = consumer / rel
        if directory.is_symlink():
            problems.append(f"BLOCKED: {rel} 是软链目录，拒绝迁移；请替换为真实目录")
        elif directory.is_dir():
            problems.append(f"{rel} 待收敛并清理")
    problems.extend(_non_entity_reports(sync))

    old_audit = consumer / "docs/archive/tasks_audit.log"
    new_audit = consumer / "docs/runtime/tasks_audit.log"
    if old_audit.is_file():
        if new_audit.is_file():
            problems.append(
                f"目标占用：{sync.rel(new_audit)} 已存在，禁止擅自覆盖或删除旧归档日志，请人工介入处理"
            )
        else:
            problems.append("docs/archive/tasks_audit.log 需迁至 docs/runtime/tasks_audit.log")
    if (consumer / "docs/archive/handoff.md").is_file():
        problems.append("docs/archive/handoff.md 待退役移除")
    for rel in _STALE_INDEX_RELS:
        if (consumer / rel).is_file():
            problems.append(f"{rel} 为已废弃派生索引，迁移将删除")
    try:
        _reject_symlink_entities(consumer, sync)
    except MigrationError as error:
        problems.append(f"BLOCKED: {error}")

    # 复用本轮统一计划（显式注入或 apply 注入）或按需构建；暴露其它分支阻断、改号与引用待办
    resolved = plan if plan is not None else getattr(sync, "entity_plan", None)
    try:
        resolved = resolved if resolved is not None else build_migration_plan(sync=sync)
    except (MigrationError, ctx.SyncError, TaskDataError, IdScanError, OSError, ValueError) as error:
        marker = f"BLOCKED: {error}"
        if marker not in problems:
            problems.append(marker)
    else:
        problems.extend(resolved.renumber_reports)
        for path, _, _ in resolved.reference_updates:
            problems.append(f"更新普通文档精确路径引用：{sync.rel(path)}")
        problems.extend(resolved.reference_reports)

    return sorted(set(problems))


def _parse_legacy_pending_content(
    path: Path, raw_text: str, default_status: str, source_label: str, source_sub: str
) -> tuple[dict[str, Any], str]:
    """解析旧 pending 文件（可能带或不带 Front Matter，或仅正文中有状态字段）。"""
    fm: dict[str, Any] = {}
    body = raw_text
    if raw_text.startswith("---"):
        try:
            fm, body = parse_front_matter_text(raw_text, source=source_label)
        except (TaskDataError, ValueError, KeyError) as err:
            raise MigrationError(f"{source_label}: Front Matter 解析失败：{err}") from err
        unknown = set(fm) - PENDING_ALLOWED_FM_KEYS
        if unknown:
            raise MigrationError(
                f"{source_label}: 包含未知 Front Matter 字段 {sorted(unknown)}，拒绝丢弃迁移"
            )

    file_match = PENDING_FILE_RE.match(path.name)
    if not file_match:
        raise MigrationError(f"{source_label}: 文件名不符合规范 pNNN_slug.md")
    pid = f"p{int(file_match.group(1)):03d}"
    slug = file_match.group(2)

    # 身份一致性：原始 FM 先校验，声明 pid/slug 与文件名不符即 BLOCKED，
    # 禁止按文件名静默改写身份（与 validate_pending_front_matter 同口径）。
    declared_pid = fm.get("pid")
    if declared_pid is not None and str(declared_pid) != pid:
        raise MigrationError(
            f"{source_label}: 身份与文件名矛盾（声明 pid {declared_pid!r} != 文件编号 {pid}），"
            "拒绝静默改写，请人工对齐后重跑"
        )
    declared_slug = fm.get("slug")
    if declared_slug is not None and str(declared_slug) != slug:
        raise MigrationError(
            f"{source_label}: 身份与文件名矛盾（声明 slug {declared_slug!r} != 文件名 slug {slug!r}），"
            "拒绝静默改写，请人工对齐后重跑"
        )

    declared_status = fm.get("status")
    # 目录来源与 Front Matter 状态一致性检查（契约第 253 行）
    if source_sub == "archive":
        if declared_status is not None and declared_status != "closed":
            raise MigrationError(
                f"{source_label}: 状态与所在目录矛盾（归档目录中却声明 status: {declared_status!r}）"
            )
        status = "closed"
    elif source_sub == "todo":
        if declared_status is not None and declared_status != "todo":
            raise MigrationError(
                f"{source_label}: 状态与所在目录矛盾（todo 目录中却声明 status: {declared_status!r}）"
            )
        status = "todo"
    elif source_sub == "parked":
        if declared_status is not None and declared_status != "parked":
            raise MigrationError(
                f"{source_label}: 状态与所在目录矛盾（parked 目录中却声明 status: {declared_status!r}）"
            )
        status = "parked"
    elif source_sub == "flat":
        if not declared_status:
            raise MigrationError(f"{source_label}: 扁平 docs/pending 下必须声明 status")
        status = declared_status
    else:
        status = declared_status or default_status

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
        # 从正文中解析「- 处理：...」。逐段识别具名字段，无条件保留 fix_ref；
        # 裸 tid / main / PR 等仍按旧规则收作 fix_ref，避免与 reason= 同时出现时丢失。
        m = re.search(r"^[ \t]*-[ \t]*处理[ \t]*[:：][ \t]*(.*)$", body, re.MULTILINE)
        if m:
            line = m.group(1).strip()
            free_parts: list[str] = []
            for part in [p.strip() for p in re.split(r"[；;]", line) if p.strip()]:
                if part.startswith("resolution="):
                    resolution = part.split("=", 1)[1].strip()
                elif part.startswith("fix_ref="):
                    fix_ref = part.split("=", 1)[1].strip()
                elif part.startswith("reason="):
                    reason = part.split("=", 1)[1].strip()
                else:
                    free_parts.append(part)
            for part in free_parts:
                if re.match(r"^t\d{3,}$", part) or part in ("main", "PR", "untracked"):
                    if not fix_ref:
                        fix_ref = part
                elif not reason and part not in ("未开", "不办", "无"):
                    reason = part

        if reason in ("未开", "不办", "无"):
            reason = None

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
        # 绝不使用默认 resolution = "resolved" 兜底；缺少闭环结论或依据直接阻断
        if not resolution or resolution not in PENDING_VALID_RESOLUTIONS:
            raise MigrationError(
                f"{source_label}: 闭环条目缺少有效闭环结论 (resolution 缺失或非法：{resolution!r})"
            )
        if not reason or not str(reason).strip():
            raise MigrationError(f"{source_label}: 闭环条目缺少闭环依据 (reason)")
        result_fm["resolution"] = resolution
        result_fm["reason"] = str(reason).strip()
        if fix_ref:
            result_fm["fix_ref"] = str(fix_ref).strip()

    return result_fm, body


def _validate_spike_findings_references(
    report_text: str, source_label: str, consumer: Path, finding_ids=(),
) -> None:
    """校验 spike 报告引用的 findings 文件在 docs/findings/ 中是否真实存在。"""
    finding_refs = set(re.findall(r"\b(d\d{3,})\b", report_text))
    finding_refs.update(re.findall(r"docs/findings/(d\d{3,})_[a-z0-9_]+", report_text))
    for did in sorted(finding_refs):
        findings_dir = consumer / "docs/findings"
        matches = list(findings_dir.glob(f"{did}_*.md")) if findings_dir.is_dir() else []
        if did not in finding_ids and not any(m.is_file() for m in matches):
            raise MigrationError(
                f"{source_label}: 引用的 finding {did} 在 docs/findings/ 中不存在真实文件"
            )


def _validate_spike_closed_evidence(
    report_text: str, source_label: str, consumer: Path, finding_ids=(),
) -> None:
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

    # 必须包含有效 findings 关联（如含有 dNNN 引用）
    finding_refs = set(re.findall(r"\b(d\d{3,})\b", report_text))
    finding_refs.update(re.findall(r"docs/findings/(d\d{3,})_[a-z0-9_]+", report_text))
    if not finding_refs:
        raise MigrationError(
            f"{source_label}: report.md 缺少有效 findings 关联（未包含 dNNN 引用）"
        )
    _validate_spike_findings_references(report_text, source_label, consumer, finding_ids)


def _clean_empty_dir_strictly(dir_path: Path, sync: ctx.SyncContext) -> None:
    """严格清理空目录：若非空直接抛出 MigrationError 阻断，严禁 rmtree 或静默忽略。"""
    if not dir_path.exists():
        return
    if not dir_path.is_dir():
        raise MigrationError(f"清理失败：{sync.rel(dir_path)} 不是目录")

    # 自底向上清理空子目录；模板自带占位文件（.DS_Store / .gitkeep）同样清理，
    # 否则旧消费仓的 .gitkeep 会让整个迁移事务在清理阶段回滚。
    for item in sorted(dir_path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if item.is_file():
            if item.name in _CLEANABLE_NAMES:
                item.unlink()
            else:
                raise MigrationError(
                    f"清理旧目录失败：{sync.rel(dir_path)} 仍包含非空残留文件 {sync.rel(item)}"
                )
        elif item.is_dir():
            try:
                item.rmdir()
            except OSError as err:
                raise MigrationError(
                    f"清理旧子目录失败（非空）：{sync.rel(item)} - {err}"
                ) from err

    try:
        dir_path.rmdir()
    except OSError as err:
        raise MigrationError(
            f"清理旧目录失败（非空）：{sync.rel(dir_path)} - {err}"
        ) from err


def _check_worktrees_clean(root: Path) -> None:
    try:
        r = _git(root, "worktree", "list", "--porcelain")
    except ctx.SyncError as error:
        raise MigrationError(f"无法查询 git worktree（{error}）；拒绝放行迁移") from error
    if r.returncode != 0:
        raise MigrationError(f"无法查询 git worktree（{r.stderr.strip()}）；拒绝放行迁移")
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


def _is_entity(item: Path, kind: str) -> bool:
    if item.is_symlink():
        return False
    pattern = {"tasks": TASK_DIR_RE, "spikes": SPIKE_DIR_RE, "pending": PENDING_FILE_RE}[kind]
    return bool(pattern.fullmatch(item.name)) and (item.is_file() if kind == "pending" else item.is_dir())


def _non_entities(sync: ctx.SyncContext) -> list[Path]:
    return [
        item for rel, kind in LEGACY_ENTITY_DIRS.items()
        if (directory := sync.consumer / rel).is_dir() and not directory.is_symlink()
        for item in sorted(directory.iterdir()) if not _is_entity(item, kind)
    ]


def _non_entity_reports(sync: ctx.SyncContext) -> list[str]:
    reports = []
    for item in _non_entities(sync):
        kind = "软链" if item.is_symlink() else "目录" if item.is_dir() else "文件"
        line = f"删除旧归档目录非实体项：{sync.rel(item)}（{kind}）"
        if kind == "目录":
            files = [sync.rel(path) for path in sorted(item.rglob("*")) if path.is_file() or path.is_symlink()]
            line += "；内部文件：" + ("、".join(files) or "（空）")
        reports.append(line)
    return reports


def _task_sources(sync: ctx.SyncContext, workflow_writes: dict[Path, str]) -> tuple[list[TaskItem], list[Path]]:
    """收集 tasks（当前 + 归档）；t 重复仍阻断，不参与自动改号。"""
    consumer = sync.consumer
    items: list[TaskItem] = []
    dirs_to_clean: list[Path] = []
    seen: dict[str, Path] = {}
    active_dir = consumer / "docs/tasks"
    archive_dir = consumer / "docs/archive/tasks"

    if active_dir.is_dir():
        for child in sorted(active_dir.iterdir()):
            if not child.is_dir() or child.name == "task_template":
                continue
            m = TASK_DIR_RE.fullmatch(child.name)
            if not m:
                raise MigrationError(f"非法 task 目录：{sync.rel(child)}")
            tid, slug = m.group(1), m.group(2)
            if tid in seen:
                raise MigrationError(f"同号多处：{tid} @ {seen[tid]} 与 {child}")
            seen[tid] = child
            prog = child / PROGRESS_FILE
            if not prog.is_file() and prog not in workflow_writes:
                raise MigrationError(f"{sync.rel(child)}: 缺失 {PROGRESS_FILE}")
            fm, _ = parse_front_matter_text(
                workflow_writes[prog] if prog in workflow_writes else prog.read_text(encoding="utf-8"),
                source=sync.rel(prog),
            )
            try:
                task_record(fm, directory=f"docs/tasks/{child.name}", source=sync.rel(prog))
            except TaskDataError as error:
                raise MigrationError(str(error)) from error
            items.append(TaskItem(tid, slug, child, child, fm["status"]))

    if archive_dir.is_dir():
        dirs_to_clean.append(archive_dir)
        for child in sorted(archive_dir.iterdir()):
            if not _is_entity(child, "tasks"):
                continue
            m = re.match(r"^(t\d{3,})_([a-z0-9_]+)$", child.name)
            if not m:
                continue
            tid, slug = m.group(1), m.group(2)
            if tid in seen:
                raise MigrationError(
                    f"同号多处：tid {tid} 同时存在于 {sync.rel(seen[tid])} 和 {sync.rel(child)}"
                )
            target_dir = active_dir / child.name
            if target_dir.exists():
                raise MigrationError(f"目标占用：{sync.rel(target_dir)} 已存在")
            prog = child / PROGRESS_FILE
            if not prog.is_file() and prog not in workflow_writes:
                raise MigrationError(f"{sync.rel(child)} 缺 progress.md；须先转换 workflow schema")
            fm, _ = parse_front_matter_text(
                workflow_writes[prog] if prog in workflow_writes else prog.read_text(encoding="utf-8"),
                source=sync.rel(prog),
            )
            try:
                task_record(fm, directory=f"docs/tasks/{child.name}", source=sync.rel(prog))
            except TaskDataError as error:
                raise MigrationError(str(error)) from error
            status = fm.get("status")
            if status not in ("done", "dropped"):
                raise MigrationError(
                    f"{sync.rel(child)}: 归档任务状态与目录矛盾，必须为 done 或 dropped（实际为 {status!r}）"
                )
            seen[tid] = child
            items.append(TaskItem(tid, slug, child, target_dir, status))
    return items, dirs_to_clean


def _collect_sources(
    sync: ctx.SyncContext,
) -> tuple[dict[str, list[CandidateIdentity]], dict[tuple, dict], list[Path], list[Path], dict[str, list[int]]]:
    """统一收集 d / p / s 全部来源（当前扁平、旧目录、旧总账），返回候选与内容数据。"""
    consumer = sync.consumer
    candidates: dict[str, list[CandidateIdentity]] = {"d": [], "p": [], "s": []}
    source_data: dict[tuple, dict] = {}
    dirs_to_clean: list[Path] = []
    legacy_ledgers: list[Path] = []
    ledger_nums: dict[str, list[int]] = {"d": [], "p": []}

    # ---- findings：当前扁平文件 + docs/findings.md 旧总账 ----
    fdir = consumer / "docs/findings"
    if fdir.is_dir():
        for path in sorted(fdir.iterdir()):
            if path.name.startswith(".tmp_"):
                continue
            # 软链策略单点在 _reject_symlink_entities（build/status 入口已调用）
            if path.is_dir():
                raise MigrationError(f"当前 findings 布局禁止嵌套目录：{sync.rel(path)}")
            if not path.is_file():
                continue
            if not re.match(r"^d[0-9]", path.name):
                continue
            parsed = parse_entry_file_name("d", path.name)
            if parsed is None:
                raise MigrationError(f"{sync.rel(path)} 文件名不符合规范 dNNN_slug.md")
            num, slug = parsed
            eid = canonical_entry_id("d", num)
            rel = f"docs/findings/{path.name}"
            cand = CandidateIdentity.from_file("d", num, eid, slug, rel)
            candidates["d"].append(cand)
            source_data[("d", cand.identity_key, cand.ordinal)] = {"kind": "finding_file", "path": path, "text": path.read_text(encoding="utf-8")}

    f_ledger = consumer / "docs/findings.md"
    if f_ledger.exists() or f_ledger.is_symlink():
        if f_ledger.is_symlink():
            raise MigrationError(f"旧总账是软链，拒绝迁移：docs/findings.md")
        legacy_ledgers.append(f_ledger)
        for item in plan_legacy_migration(consumer, prefix="d", ledger_rel="docs/findings.md", target_dir=fdir):
            cand = CandidateIdentity.from_ledger(
                "d", item.number, item.entry_id, item.slug, "docs/findings.md", item.ordinal
            )
            candidates["d"].append(cand)
            ledger_nums["d"].append(item.number)
            source_data[("d", cand.identity_key, cand.ordinal)] = {
                "kind": "finding_ledger", "title": item.title, "body": item.body,
            }

    # ---- pending：扁平 + todo/parked/archive 旧目录 + docs/pending.md 旧总账 ----
    p_sources: list[tuple[Path, str]] = []  # (path, source_sub)
    for rel_dir, sub in (
        ("docs/pending", "flat"),
        ("docs/pending/todo", "todo"),
        ("docs/pending/parked", "parked"),
        ("docs/archive/pending", "archive"),
    ):
        base = consumer / rel_dir
        if rel_dir in LEGACY_ENTITY_DIRS and base.is_dir():
            dirs_to_clean.append(base)
        if not base.is_dir():
            continue
        if sub == "flat":
            for f in sorted(base.glob("p*.md")):
                if f.is_dir() and not f.is_symlink():
                    raise MigrationError(f"当前 pending 布局禁止嵌套目录：{sync.rel(f)}")
                if not f.is_file() or not PENDING_FILE_RE.match(f.name):
                    raise MigrationError(f"非法 pending 项：{sync.rel(f)}")
                p_sources.append((f, "flat"))
        else:
            for f in sorted(base.glob("p*.md")):
                if not f.is_symlink() and f.is_file() and PENDING_FILE_RE.fullmatch(f.name):
                    p_sources.append((f, sub))

    for path, sub in p_sources:
        match = PENDING_FILE_RE.match(path.name)
        num, slug = int(match.group(1)), match.group(2)
        eid = canonical_entry_id("p", num)
        rel = path.relative_to(consumer).as_posix()
        fm, body = _parse_legacy_pending_content(path, path.read_text(encoding="utf-8"), sub, rel, sub)
        try:
            validate_pending_front_matter(fm, filename=path.name, source=rel)
        except (TaskDataError, ValueError, KeyError) as err:
            raise MigrationError(f"{rel} 非法 Front Matter：{err}") from err
        cand = CandidateIdentity.from_file("p", num, eid, slug, rel)
        candidates["p"].append(cand)
        source_data[("p", cand.identity_key, cand.ordinal)] = {"kind": "pending_file", "path": path, "fm": fm, "body": body}

    p_ledger = consumer / "docs/pending.md"
    if p_ledger.exists() or p_ledger.is_symlink():
        if p_ledger.is_symlink():
            raise MigrationError(f"旧总账是软链，拒绝迁移：docs/pending.md")
        legacy_ledgers.append(p_ledger)
        for item in plan_legacy_migration(consumer, prefix="p", ledger_rel="docs/pending.md", target_dir=consumer / "docs/pending"):
            cand = CandidateIdentity.from_ledger(
                "p", item.number, item.entry_id, item.slug, "docs/pending.md", item.ordinal
            )
            candidates["p"].append(cand)
            ledger_nums["p"].append(item.number)
            source_data[("p", cand.identity_key, cand.ordinal)] = {
                "kind": "pending_ledger", "title": item.title, "body": item.body,
            }

    # ---- spikes：当前 + 归档 ----
    s_sources: list[tuple[Path, bool]] = []  # (dir, from_archive)
    adir = consumer / "docs/spikes"
    sdir = consumer / "docs/archive/spikes"
    if adir.is_dir():
        for child in sorted(adir.iterdir()):
            if child.name.startswith(".") or child.name in _CLEANABLE_NAMES or not child.name.startswith("s"):
                continue
            m = SPIKE_DIR_RE.fullmatch(child.name)
            if not child.is_dir() or m is None:
                raise MigrationError(f"非法 spike 项：{sync.rel(child)}")
            s_sources.append((child, False))
    if sdir.is_dir():
        dirs_to_clean.append(sdir)
        for child in sorted(sdir.iterdir()):
            if _is_entity(child, "spikes"):
                s_sources.append((child, True))

    for child, from_archive in s_sources:
        m = SPIKE_DIR_RE.fullmatch(child.name)
        num, slug = int(m.group(1)), child.name.split("_", 1)[1]
        eid = canonical_entry_id("s", num)
        rel = child.relative_to(consumer).as_posix()
        rep = child / "report.md"
        if rep.is_symlink() or not rep.is_file():
            raise MigrationError(f"{sync.rel(child)}: 缺 report.md")
        rep_text = rep.read_text(encoding="utf-8")
        declared_status = None
        if rep_text.startswith("---"):
            fm, body = parse_front_matter_text(rep_text, source=sync.rel(rep))
            declared_status = fm.get("status")
            status = declared_status or "active"
        else:
            fm = {"sid": eid, "slug": slug}
            body = rep_text
            status = "active"
        if from_archive:
            if declared_status == "active":
                raise MigrationError(f"{sync.rel(rep)}: 归档 spike 状态与目录矛盾（声明为 active）")
            status = "superseded" if declared_status == "superseded" else "completed"
        final_fm = dict(fm)
        final_fm["status"] = status
        if not final_fm.get("sid"):
            final_fm["sid"] = eid
        if not final_fm.get("slug"):
            final_fm["slug"] = slug
        _validate_spike_fm(final_fm, eid, slug, rep, sync)
        cand = CandidateIdentity.from_file("s", num, eid, slug, rel, is_dir=True)
        candidates["s"].append(cand)
        source_data[("s", cand.identity_key, cand.ordinal)] = {
            "kind": "spike_dir", "path": child, "fm": final_fm, "body": body,
            "report_text": rep_text, "from_archive": from_archive,
        }

    return candidates, source_data, dirs_to_clean, legacy_ledgers, ledger_nums


def build_migration_plan(
    sync: ctx.SyncContext, *, workflow_writes: dict[Path, str] | None = None,
) -> MigrationPlan:
    """统一只读计划：status / plan / apply 共用；先算最终编号与内容，再决定引用与删除。"""
    consumer = sync.consumer
    workflow_writes = workflow_writes or {}

    for issue in check_other_branch_duplicates(consumer):
        raise MigrationError(issue)

    try:
        current_schema(consumer)
    except TaskDataError as error:
        raise MigrationError(str(error)) from error

    for rel in LEGACY_ENTITY_DIRS:
        if (consumer / rel).is_symlink():
            raise MigrationError(f"{rel} 是软链目录；请替换为真实目录后再迁移")

    _check_worktrees_clean(consumer)
    _reject_symlink_entities(consumer, sync)

    tasks_plan, task_dirs = _task_sources(sync, workflow_writes)
    candidates, source_data, source_dirs, legacy_ledgers, ledger_nums = _collect_sources(sync)
    dirs_to_clean = task_dirs + source_dirs

    # 计算各类本地最大占用号，并入旧总账解析出的全部编号
    occupied = occupied_max_ids(consumer, ledger_nums)
    decisions = assign_final_ids(consumer, candidates, occupied)

    # spike 报告的 findings 引用校验：以全部 finding 来源编号为准
    all_finding_ids = {c.orig_id for c in candidates["d"]}
    for key, data in source_data.items():
        if data["kind"] == "spike_dir":
            if data["from_archive"] and data["fm"].get("status") == "completed":
                _validate_spike_closed_evidence(data["report_text"], key[1], consumer, all_finding_ids)
            else:
                _validate_spike_findings_references(data["report_text"], key[1], consumer, all_finding_ids)

    # 生成最终目标与内容
    findings_plan: list[FindingItem] = []
    pendings_plan: list[PendingItem] = []
    spikes_plan: list[SpikeItem] = []
    renumber_reports: list[str] = []
    renumbered_decisions: list = []
    all_entity_texts: dict[str, tuple[str, str]] = {}

    for prefix in ("d", "p", "s"):
        for cand in candidates[prefix]:
            dec = decisions[(prefix, cand.identity_key, cand.ordinal)]
            data = source_data[(prefix, cand.identity_key, cand.ordinal)]
            target_path = consumer / dec.target_rel
            from_ledger = cand.ledger
            if dec.renumbered:
                renumber_reports.append(
                    f"{dec.orig_id}: {dec.source_rel} -> {dec.target_rel}（保留 {dec.kept_source_rel}）"
                )
                renumbered_decisions.append(dec)

            if prefix == "d":
                if data["kind"] == "finding_ledger":
                    text = f"# {dec.final_id} {data['title']}\n\n{data['body'].strip()}\n"
                else:
                    text = validate_and_rewrite_h1(
                        data["text"], "d", dec.orig_id, dec.final_id, source_label=cand.identity_key
                    )
                findings_plan.append(FindingItem(
                    dec.orig_id, dec.final_id, cand.slug,
                    consumer / "docs/findings.md" if from_ledger else data["path"],
                    target_path, text, cand.identity_key, from_ledger,
                    cand.doc_base,
                ))
                all_entity_texts[dec.target_rel] = (dec.final_id, text)
            elif prefix == "p":
                fm = dict(data["fm"]) if data["kind"] == "pending_file" else {
                    "pid": dec.final_id, "slug": cand.slug, "status": "todo"
                }
                fm["pid"] = dec.final_id
                if data["kind"] == "pending_ledger":
                    body = f"# {dec.final_id} {data['title']}\n\n{data['body'].strip()}\n"
                else:
                    body = validate_and_rewrite_h1(
                        data["body"], "p", dec.orig_id, dec.final_id, source_label=cand.identity_key
                    )
                validate_pending_front_matter(fm, filename=target_path.name, source=dec.target_rel)
                content = dump_pending_front_matter(fm) + body.lstrip("\r\n").rstrip() + ("\n" if body.strip() else "")
                pendings_plan.append(PendingItem(
                    fm["pid"], cand.slug,
                    consumer / "docs/pending.md" if from_ledger else data["path"],
                    target_path, fm["status"], fm.get("resolution"), fm.get("reason"),
                    fm.get("fix_ref"), body, content, dec.final_id, cand.identity_key, from_ledger,
                    cand.doc_base,
                ))
                all_entity_texts[dec.target_rel] = (dec.final_id, content)
            else:
                fm = dict(data["fm"])
                fm["sid"] = dec.final_id
                validate_spike_metadata(fm, expected_sid=dec.final_id, expected_slug=cand.slug)
                new_body = validate_and_rewrite_h1(
                    data["body"], "s", dec.orig_id, dec.final_id, source_label=cand.identity_key
                )
                report_content = _render_front_matter(fm, new_body)
                spikes_plan.append(SpikeItem(
                    dec.final_id, cand.slug, data["path"], target_path,
                    data["fm"]["status"], report_content, dec.final_id, cand.identity_key,
                ))
                all_entity_texts[dec.target_rel + "/report.md"] = (dec.final_id, report_content)

    # 最终目标冲突校验：目标集合唯一，且不得覆盖未纳入计划的现存文件
    all_targets = [p.target_path for p in findings_plan] + [p.target_path for p in pendings_plan]
    all_target_dirs = [p.target_dir for p in spikes_plan] + [t.target_dir for t in tasks_plan]
    if len({p.as_posix() for p in all_targets}) != len(all_targets):
        raise MigrationError("最终目标文件冲突：存在两个来源指向同一路径")
    if len({p.as_posix() for p in all_target_dirs}) != len(all_target_dirs):
        raise MigrationError("最终目标目录冲突：存在两个来源指向同一路径")
    source_paths = {
        p.source_path.as_posix() for p in findings_plan + pendings_plan if not p.from_ledger
    } | {s.source_dir.as_posix() for s in spikes_plan} | {t.source_dir.as_posix() for t in tasks_plan}
    for p in all_targets + all_target_dirs:
        if (p.exists() or p.is_symlink()) and p.as_posix() not in source_paths:
            raise MigrationError(f"最终目标占用：{sync.rel(p)} 已存在且不属于本轮迁移来源")

    # 引用更新：只按本轮真实来源→目标映射（含旧目录搬迁、改号、tasks 归档搬迁）
    path_mappings: list[tuple[str, str, bool]] = []
    for prefix in ("d", "p", "s"):
        for cand in candidates[prefix]:
            if cand.ledger:  # 旧总账条目无旧文件路径
                continue
            dec = decisions[(prefix, cand.identity_key, cand.ordinal)]
            if cand.doc_base != dec.target_rel:
                path_mappings.append((cand.doc_base, dec.target_rel, cand.is_dir))
    for t in tasks_plan:
        if t.source_dir != t.target_dir:
            path_mappings.append((
                t.source_dir.relative_to(consumer).as_posix(),
                t.target_dir.relative_to(consumer).as_posix(),
                True,
            ))

    # 实体最终文本中的精确路径引用：按来源位置解析旧相对链接，按最终位置输出
    for finding in findings_plan:
        new_text = update_exact_path_references(
            finding.content, doc_rel=finding.doc_base_rel,
            final_doc_rel=finding.target_path.relative_to(consumer).as_posix(),
            path_mappings=path_mappings, consumer=consumer,
        )
        finding.content = new_text
        target_rel = finding.target_path.relative_to(consumer).as_posix()
        all_entity_texts[target_rel] = (finding.final_id, new_text)
    for pending in pendings_plan:
        pending.content = update_exact_path_references(
            pending.content, doc_rel=pending.doc_base_rel,
            final_doc_rel=pending.target_path.relative_to(consumer).as_posix(),
            path_mappings=path_mappings, consumer=consumer,
        )
        all_entity_texts[pending.target_path.relative_to(consumer).as_posix()] = (
            pending.final_id, pending.content
        )
    for spike in spikes_plan:
        spike.report_content = update_exact_path_references(
            spike.report_content, doc_rel=spike.source_rel + "/report.md",
            final_doc_rel=spike.target_dir.relative_to(consumer).as_posix() + "/report.md",
            path_mappings=path_mappings, consumer=consumer,
        )
        all_entity_texts[
            spike.target_dir.relative_to(consumer).as_posix() + "/report.md"
        ] = (spike.final_id, spike.report_content)

    # 外部项目文档的精确路径引用；受保护 task 核心资产只读报告。
    # 「计划接管路径」单一派生：实体最终目标 / 来源文件 / 旧总账 / spike report 精确去重；
    # 整目录搬迁实体（spike 与归档 task）统一用 (源目录前缀 → 目标目录前缀) 表达，
    # 其内部普通 .md 的引用改写写向最终位置，避免按旧路径回写导致清理失败。
    reference_updates: list[tuple[Path, str, str]] = []
    transaction_extra_rels: list[str] = []
    outside_doc_texts: dict[str, str] = {}
    protected_asset_texts: dict[str, str] = {}
    entity_rels = {p.as_posix() for p in all_targets}
    source_rels = {
        cand.doc_base for prefix in candidates for cand in candidates[prefix] if not cand.ledger
    }
    ledger_rels = {p.relative_to(consumer).as_posix() for p in legacy_ledgers}
    handled_rels = set(entity_rels) | source_rels | ledger_rels
    en_bloc_moves: list[tuple[str, str]] = []  # (源目录前缀, 目标目录前缀)
    for s in spikes_plan:
        src_pref = s.source_rel + "/"
        dst_pref = s.target_dir.relative_to(consumer).as_posix() + "/"
        handled_rels.add(s.source_rel + "/report.md")
        handled_rels.add(dst_pref + "report.md")
        en_bloc_moves.append((src_pref, dst_pref))
    for t in tasks_plan:
        if t.source_dir != t.target_dir:
            en_bloc_moves.append((
                t.source_dir.relative_to(consumer).as_posix() + "/",
                t.target_dir.relative_to(consumer).as_posix() + "/",
            ))

    for rel in collect_tracked_project_files(consumer):
        if rel in handled_rels:
            continue
        move = next((m for m in en_bloc_moves if rel.startswith(m[0])), None)
        final_rel = (move[1] + rel[len(move[0]):]) if move else rel
        path = consumer / rel
        if path.is_symlink() or not path.is_file():
            continue
        protected = is_protected_task_asset(rel)
        if not protected and not (rel.endswith(".md") or rel == "README.md"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if protected:
            protected_asset_texts[final_rel] = text
            continue
        new_text = update_exact_path_references(
            text, doc_rel=rel, final_doc_rel=final_rel,
            path_mappings=path_mappings, consumer=consumer,
        )
        outside_doc_texts[final_rel] = new_text
        if new_text != text:
            reference_updates.append((consumer / final_rel, text, new_text))
            transaction_extra_rels.append(final_rel)

    bare_reports, protected_reports = build_reference_reports(
        renumbered=renumbered_decisions,
        entity_final_texts=all_entity_texts,
        outside_doc_texts=outside_doc_texts,
        protected_asset_texts=protected_asset_texts,
    )
    reference_reports = bare_reports + protected_reports

    # 审计日志 / handoff 归档 / 陈旧派生索引
    old_audit = consumer / "docs/archive/tasks_audit.log"
    new_audit = consumer / "docs/runtime/tasks_audit.log"
    audit_move = None
    if old_audit.is_file():
        if new_audit.is_file():
            raise MigrationError(
                f"目标占用：{sync.rel(new_audit)} 已存在，禁止擅自覆盖或删除旧归档日志，请人工介入处理"
            )
        audit_move = (old_audit, new_audit)

    old_handoff = consumer / "docs/archive/handoff.md"
    handoff_remove = None
    if old_handoff.is_file():
        diff_res = _git(consumer, "diff", "--quiet", "--", "docs/archive/handoff.md")
        cached_diff = _git(consumer, "diff", "--cached", "--quiet", "--", "docs/archive/handoff.md")
        ls_res = _git(consumer, "ls-files", "--error-unmatch", "docs/archive/handoff.md")
        if diff_res.returncode != 0 or cached_diff.returncode != 0 or ls_res.returncode != 0:
            raise MigrationError(
                "docs/archive/handoff.md 含有未提交修改或未入库内容，请先提交或备份到 Git 历史"
            )
        handoff_remove = old_handoff

    stale_index_paths = [consumer / rel for rel in _STALE_INDEX_RELS if (consumer / rel).is_file()]

    return MigrationPlan(
        tasks=tasks_plan, pendings=pendings_plan, spikes=spikes_plan,
        findings=findings_plan, legacy_ledgers=legacy_ledgers,
        audit_log_move=audit_move, handoff_archive_remove=handoff_remove,
        reference_updates=reference_updates, dirs_to_clean=dirs_to_clean,
        stale_index_paths=stale_index_paths, non_entities=_non_entities(sync),
        renumber_reports=renumber_reports, reference_reports=reference_reports,
        transaction_extra_rels=sorted(set(transaction_extra_rels)),
    )


def _plan_has_work(plan: MigrationPlan) -> bool:
    """计划是否含真实写入（新建/搬迁/改号/引用更新/删除）。"""
    if any(t.source_dir != t.target_dir for t in plan.tasks):
        return True
    if any(p.source_path != p.target_path or p.from_ledger for p in plan.pendings):
        return True
    if any(f.source_path != f.target_path or f.from_ledger for f in plan.findings):
        return True
    if any(s.source_dir != s.target_dir for s in plan.spikes):
        return True
    if plan.legacy_ledgers or plan.audit_log_move or plan.handoff_archive_remove:
        return True
    if plan.reference_updates or plan.dirs_to_clean or plan.stale_index_paths or plan.non_entities:
        return True
    if plan.renumber_reports:
        return True
    return False


def migrate_entity_storage(sync: ctx.SyncContext, *, write: bool = False) -> list[str]:
    """只构建统一计划或写盘；事务统一由 apply 编排。写盘先写全部最终目标、再删源。"""
    consumer = sync.consumer
    if not entity_storage_migration_status(sync):
        return []
    plan = (
        sync.entity_plan
        if getattr(sync, "entity_plan", None) is not None
        else build_migration_plan(sync=sync)
    )
    # schema 缺失时即便实体已就位也须写盘补建 schema 边界（原契约：迁移建立 schema）
    if not _plan_has_work(plan) and current_schema(consumer):
        return []
    reports: list[str] = [f"目标实体 schema 验收并写入 {SCHEMA_PATH}"]

    for t in plan.tasks:
        if t.source_dir != t.target_dir:
            reports.append(f"Task {t.tid}: {sync.rel(t.source_dir)} -> {sync.rel(t.target_dir)} (status: {t.status})")
        else:
            reports.append(f"Task {t.tid}: 原位保留 {sync.rel(t.source_dir)} (status: {t.status})")
    for p in plan.pendings:
        reports.append(f"Pending {p.final_id}: {p.source_rel} -> {sync.rel(p.target_path)} (status: {p.status})")
    for f in plan.findings:
        reports.append(f"Finding {f.final_id}: {f.source_rel} -> {sync.rel(f.target_path)}")
    for s in plan.spikes:
        reports.append(f"Spike {s.final_id}: {s.source_rel} -> {sync.rel(s.target_dir)} (status: {s.status})")
    if plan.legacy_ledgers:
        for ledger in plan.legacy_ledgers:
            reports.append(f"旧总账拆分并移除：{sync.rel(ledger)}")
    if plan.audit_log_move:
        reports.append(f"Audit log: {sync.rel(plan.audit_log_move[0])} -> {sync.rel(plan.audit_log_move[1])}")
    if plan.handoff_archive_remove:
        reports.append(f"Handoff archive: 移除 {sync.rel(plan.handoff_archive_remove)}")
    for doc_path, _, _ in plan.reference_updates:
        reports.append(f"Reference update: 更新 {sync.rel(doc_path)} 中精确路径引用")
    for index_path in plan.stale_index_paths:
        reports.append(f"Stale index: 删除 {sync.rel(index_path)}（已废弃派生索引）")
    reports.extend(plan.renumber_reports)
    reports.extend(plan.reference_reports)
    reports.extend(_non_entity_reports(sync))

    if not write:
        return reports

    # 1. 删除旧归档目录中的非实体项
    for item in plan.non_entities:
        if item.is_symlink() or not item.is_dir():
            item.unlink()
        else:
            shutil.rmtree(item)

    # 2. 先写全部文件类最终目标（findings / pendings），内容已在计划中确定
    all_target_paths: set[str] = set()
    for f in plan.findings:
        _assert_within_consumer(f.target_path, sync)
        f.target_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(f.target_path, f.content)
        all_target_paths.add(f.target_path.as_posix())
    for p in plan.pendings:
        _assert_within_consumer(p.target_path, sync)
        p.target_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(p.target_path, p.content)
        all_target_paths.add(p.target_path.as_posix())

    # 3. spike 目录统一先搬入 staging，再从 staging 发布到最终目标，规避改号链冲突
    staged: list[tuple[Path, SpikeItem]] = []
    for index, s in enumerate(plan.spikes):
        _assert_within_consumer(s.source_dir, sync)
        stage = s.source_dir.parent / f".tmp_mig_{index}_{s.source_dir.name}"
        os.replace(s.source_dir, stage)
        staged.append((stage, s))
    for stage, s in staged:
        _assert_within_consumer(s.target_dir, sync)
        s.target_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage, s.target_dir)
        atomic_write_text(s.target_dir / "report.md", s.report_content)
        all_target_paths.add((s.target_dir / "report.md").as_posix())

    # 4. tasks 目录搬迁
    for t in plan.tasks:
        if t.source_dir != t.target_dir:
            _assert_within_consumer(t.source_dir, sync)
            _assert_within_consumer(t.target_dir, sync)
            t.target_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(t.source_dir), str(t.target_dir))

    # 5. 删除不再作为目标的文件类源（旧目录 / 改号前文件），绝不删最终目标
    for item in [*plan.findings, *plan.pendings]:
        if item.from_ledger:
            continue
        sp = item.source_path
        if (
            sp.as_posix() not in all_target_paths
            and sp != item.target_path
            and (sp.exists() or sp.is_symlink())
        ):
            _assert_within_consumer(sp, sync)
            sp.unlink()

    # 6. 删除旧总账
    for ledger in plan.legacy_ledgers:
        if ledger.exists() or ledger.is_symlink():
            _assert_within_consumer(ledger, sync)
            ledger.unlink()

    # 7. 审计日志 / handoff / 陈旧派生索引
    if plan.audit_log_move:
        src_log, dst_log = plan.audit_log_move
        _assert_within_consumer(src_log, sync)
        _assert_within_consumer(dst_log, sync)
        dst_log.parent.mkdir(parents=True, exist_ok=True)
        dst_log.write_bytes(src_log.read_bytes())
        src_log.unlink()
    if plan.handoff_archive_remove and plan.handoff_archive_remove.exists():
        _assert_within_consumer(plan.handoff_archive_remove, sync)
        plan.handoff_archive_remove.unlink()
    for index_path in plan.stale_index_paths:
        if index_path.is_file():
            _assert_within_consumer(index_path, sync)
            index_path.unlink()

    # 8. 应用外部普通文档精确路径引用更新
    for doc_path, _, new_content in plan.reference_updates:
        _assert_within_consumer(doc_path, sync)
        atomic_write_text(doc_path, new_content)

    # 9. 清理空旧目录（严格逐级 rmdir）
    for d in plan.dirs_to_clean:
        if d.exists() and d.is_dir():
            _clean_empty_dir_strictly(d, sync)

    # 10. 严格目标验收 + 建立 schema + 轻量收敛确认（不重建完整计划）
    _assert_converged(consumer, sync)
    atomic_write_text(consumer / SCHEMA_PATH, json.dumps({"version": SCHEMA_VERSION}) + "\n")
    return reports


def _assert_converged(consumer: Path, sync: ctx.SyncContext) -> None:
    """写盘后确认本轮改动落地：零重复/软链、旧总账与旧布局已消失，不重建计划。"""
    validate_current_entities(sync)
    for ledger in LEDGER_RELS:
        if (consumer / ledger).exists() or (consumer / ledger).is_symlink():
            raise MigrationError(f"迁移后仍残留旧总账：{ledger}")
    for rel in LEGACY_ENTITY_DIRS:
        if (consumer / rel).is_dir():
            raise MigrationError(f"迁移后仍残留旧布局目录：{rel}")
