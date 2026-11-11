"""一次性迁移：specs_index.md + archive specs -> docs/specs front matter。"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from repo_task.documents import atomic_write_text, dump_front_matter, parse_front_matter_text
from repo_task.specs import (
    SPEC_FM_KEYS,
    SPEC_SLUG_RE,
    SPEC_STATUSES,
    SpecError,
    load_specs,
    parse_spec_text,
    render_spec_text,
)
from .specs_index_migration import parse_specs_index

from . import context as ctx

INDEX = "docs/specs_index.md"
ACTIVE_DIR = "docs/specs"
ARCHIVE_DIR = "docs/archive/specs"


@dataclass
class SpecPlan:
    """单份 spec 的完整迁移计划。

    由 _plan_specs 一次算出：目标值（slug/status/tasks/body）与全部校验、
    冲突检查都在计划阶段完成，任一失败即抛 SyncError，计划阶段零写入。
    """

    source: Path
    target: Path
    slug: str
    status: str
    tasks: list[str]
    body: str
    reports: list[str] = field(default_factory=list)


def _index_map(sync: ctx.SyncContext) -> dict[str, list[str]]:
    path = sync.consumer / INDEX
    if not path.is_file():
        return {}
    try:
        return parse_specs_index(sync.consumer)
    except ValueError as error:
        raise ctx.SyncError(str(error)) from error


def specs_migration_status(sync: ctx.SyncContext) -> list[str]:
    """常驻门禁只检测旧布局遗留物，不检测内容状态或契约违规。

    ① docs/specs_index.md 存在；② docs/archive/specs 下还有 spec；
    ③ docs/specs 下的 spec 缺当前布局要求的完整 front matter（三键）。
    空行、逗号后空格等空白与写法属格式，不进本门禁；值非法、多余字段
    属契约违规，由 validate_specs 报告（finish/doctor/apply 已接入）。
    """
    findings = []
    if (sync.consumer / INDEX).is_file():
        findings.append(f"{INDEX} 待删除并迁移到 front matter")
    archive = sync.consumer / ARCHIVE_DIR
    if archive.is_dir():
        findings.extend(
            f"{path.relative_to(sync.consumer).as_posix()} 待迁回 docs/specs"
            for path in sorted(archive.glob("*.md"))
        )
    active = sync.consumer / ACTIVE_DIR
    for path in sorted(active.glob("*.md")):
        rel = path.relative_to(sync.consumer).as_posix()
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            findings.append(f"{rel} 缺 front matter，待迁移")
            continue
        try:
            fm, _ = parse_front_matter_text(text, source=rel)
        except ctx.TaskDataError:
            continue
        if not SPEC_FM_KEYS.issubset(fm):
            findings.append(f"{rel} front matter 未含 slug/status/tasks 三键，待迁移")
    return findings


def _plan_spec(
    sync: ctx.SyncContext,
    path: Path,
    target: Path,
    index: dict[str, list[str]],
    *,
    archived: bool,
) -> SpecPlan:
    slug = path.stem
    if not SPEC_SLUG_RE.fullmatch(slug):
        raise ctx.SyncError(f"spec slug 非法，停止迁移：{path.name}")
    if archived and target.exists():
        raise ctx.SyncError(f"同名 spec 冲突，停止迁移：{path} -> {target}")
    rel = path.relative_to(sync.consumer).as_posix()
    reports: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ctx.SyncError(f"spec 无法读取：{path}（{error}）") from error
    fm: dict = {}
    if text.startswith("---"):
        try:
            fm, body = parse_front_matter_text(text, source=rel)
        except ctx.TaskDataError as error:
            raise ctx.SyncError(str(error)) from error
        fm_slug = fm.get("slug")
        if fm_slug is not None and fm_slug != slug:
            raise ctx.SyncError(
                f"{path.name} front matter slug={fm_slug!r} 与文件名 {slug!r} 不一致，停止迁移"
            )
        fm_status = fm.get("status")
        if archived:
            if fm_status is not None and fm_status not in SPEC_STATUSES:
                raise ctx.SyncError(
                    f"{path.name} front matter status 只允许 active/retired（实得 {fm_status!r}）"
                )
            status = "retired"
            if fm_status is not None and fm_status != "retired":
                reports.append(
                    f"{path.name} front matter status 由 {fm_status} 改为 retired"
                    "（archive 来源固定 retired）"
                )
        elif fm_status is None:
            status = "active"
        else:
            status = fm_status
        extras = {key: value for key, value in fm.items() if key not in SPEC_FM_KEYS}
        if extras:
            dropped = "、".join(f"{key}={value}" for key, value in extras.items())
            reports.append(f"{path.name} front matter 丢弃多余字段：{dropped}")
        if "tasks" in fm:
            tasks_value = fm["tasks"]
        elif slug in index:
            tasks_value = ", ".join(index[slug])
        else:
            tasks_value = "baseline"
            reports.append(f"{path.name} 无索引行，使用 tasks: [baseline]，请人工确认")
    else:
        body = text
        status = "retired" if archived else "active"
        if slug in index:
            tasks_value = ", ".join(index[slug])
        else:
            tasks_value = "baseline"
            reports.append(f"{path.name} 无索引行，使用 tasks: [baseline]，请人工确认")

    # 单一 codec：先用通用工具把「剥离 extras、补齐后的三键」写成待校验文本，
    # 再由公开 parse_spec_text 统一严格校验并归一化 tasks；写盘只走 render_spec_text。
    candidate = dump_front_matter(
        {"slug": slug, "status": status, "tasks": tasks_value}
    ) + "\n" + body
    try:
        record = parse_spec_text(candidate, source=rel, path=target)
    except SpecError as error:
        raise ctx.SyncError(f"{path.name} {error}") from error
    except ctx.TaskDataError as error:
        raise ctx.SyncError(str(error)) from error
    tasks = record["tasks"]
    if "tasks" in fm and slug in index and tasks != index[slug]:
        raise ctx.SyncError(
            f"{path.name} front matter tasks={tasks} 与索引 {index[slug]} 冲突"
        )
    if "tasks" in fm and slug not in index:
        reports.append(
            f"{path.name} 无索引行，沿用 front matter tasks: [{', '.join(tasks)}]"
        )
    if archived:
        reports.append(f"{rel} -> docs/specs/{path.name}（retired）")
    else:
        reports.append(f"{rel} -> front matter")
    return SpecPlan(
        source=path, target=target, slug=record["slug"], status=record["status"],
        tasks=tasks, body=record["body"], reports=reports,
    )


def _plan_specs(sync: ctx.SyncContext, index: dict[str, list[str]]) -> list[SpecPlan]:
    """一次遍历 active 与 archive，算出完整迁移计划。

    所有校验与冲突检查在此完成；任一失败抛 SyncError，本函数零写入，
    调用方在拿到完整计划前不得写盘。
    """
    active = sync.consumer / ACTIVE_DIR
    plans = [
        _plan_spec(sync, path, path, index, archived=False)
        for path in sorted(active.glob("*.md"))
    ]
    archive = sync.consumer / ARCHIVE_DIR
    if archive.is_dir():
        plans.extend(
            _plan_spec(sync, path, active / path.name, index, archived=True)
            for path in sorted(archive.glob("*.md"))
        )
    return plans


def migrate_specs_layout(sync: ctx.SyncContext, *, write: bool = False) -> list[str]:
    index = _index_map(sync)
    plans = _plan_specs(sync, index)
    reports = [line for plan in plans for line in plan.reports]
    if not write:
        return reports
    archive = sync.consumer / ARCHIVE_DIR
    for plan in plans:
        if plan.source != plan.target:
            plan.target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(plan.source), str(plan.target))
        atomic_write_text(
            plan.target,
            render_spec_text(plan.slug, plan.status, plan.tasks, plan.body),
        )
    index_path = sync.consumer / INDEX
    if index_path.exists():
        index_path.unlink()
    if archive.is_dir() and not any(archive.iterdir()):
        archive.rmdir()
    try:
        for spec in load_specs(sync.consumer):
            if spec["slug"] != spec["path"].stem:
                raise ctx.SyncError(f"迁移后 slug 与文件名不一致：{spec['path']}")
    except SpecError as error:
        raise ctx.SyncError(str(error)) from error
    return reports
