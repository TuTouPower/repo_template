"""一次性迁移：specs_index.md + archive specs -> docs/specs front matter。"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from repo_task.documents import atomic_write_text, parse_front_matter_text
from repo_task.specs import (
    SPEC_SLUG_RE,
    SPEC_STATUSES,
    SpecError,
    _tokens,
    load_specs,
    render_spec_text,
)
from .specs_index_migration import parse_specs_index

from . import context as ctx

INDEX = "docs/specs_index.md"
ACTIVE_DIR = "docs/specs"
ARCHIVE_DIR = "docs/archive/specs"

# front matter 中迁移后保留的字段；其余字段列入报告后丢弃。
_KNOWN_FM_KEYS = ("slug", "status", "tasks")


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


def _has_front_matter(path: Path) -> bool:
    return path.read_text(encoding="utf-8").startswith("---")


def specs_migration_status(sync: ctx.SyncContext) -> list[str]:
    findings = []
    if (sync.consumer / INDEX).is_file():
        findings.append(f"{INDEX} 待删除并迁移到 front matter")
    archive = sync.consumer / ARCHIVE_DIR
    if archive.is_dir():
        findings.extend(f"{path.relative_to(sync.consumer).as_posix()} 待迁回 docs/specs" for path in archive.glob("*.md"))
    for path in (sync.consumer / ACTIVE_DIR).glob("*.md"):
        if not _has_front_matter(path):
            findings.append(f"{path.relative_to(sync.consumer).as_posix()} 待补 front matter")
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
        elif fm_status in SPEC_STATUSES:
            status = fm_status
        else:
            raise ctx.SyncError(
                f"{path.name} front matter status 只允许 active/retired（实得 {fm_status!r}）"
            )
        if "tasks" in fm:
            try:
                tasks = _tokens(fm["tasks"])
            except SpecError as error:
                raise ctx.SyncError(f"{path.name} {error}") from error
            if slug in index:
                if tasks != index[slug]:
                    raise ctx.SyncError(
                        f"{path.name} front matter tasks={tasks} 与索引 {index[slug]} 冲突"
                    )
            else:
                reports.append(
                    f"{path.name} 无索引行，沿用 front matter tasks: [{', '.join(tasks)}]"
                )
        elif slug in index:
            tasks = index[slug]
        else:
            tasks = ["baseline"]
            reports.append(f"{path.name} 无索引行，使用 tasks: [baseline]，请人工确认")
        extras = {key: value for key, value in fm.items() if key not in _KNOWN_FM_KEYS}
        if extras:
            dropped = "、".join(f"{key}={value}" for key, value in extras.items())
            reports.append(f"{path.name} front matter 丢弃多余字段：{dropped}")
    else:
        body = text
        status = "retired" if archived else "active"
        if slug in index:
            tasks = index[slug]
        else:
            tasks = ["baseline"]
            reports.append(f"{path.name} 无索引行，使用 tasks: [baseline]，请人工确认")
    if archived:
        reports.append(f"{rel} -> docs/specs/{path.name}（retired）")
    else:
        reports.append(f"{rel} -> front matter")
    return SpecPlan(
        source=path, target=target, slug=slug, status=status, tasks=tasks, body=body,
        reports=reports,
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
