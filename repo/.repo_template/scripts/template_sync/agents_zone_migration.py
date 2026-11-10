"""一次性把旧 AGENTS.md 拆为项目区与模板区。"""

from __future__ import annotations

import re
from pathlib import Path

from . import context as ctx
from .agents_merge import agents_parts
from .common import _git, _load_json_object

HEADING = "## 目录与读写规则"
DEVELOP = "## 开发原则"
PATH_RE = re.compile(r"^\|\s*`([^`]+)`")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
MANIFEST_REL = ".repo_template/sync_manifest.json"


def agents_zone_migration_status(sync: ctx.SyncContext) -> list[str]:
    path = sync.consumer / "AGENTS.md"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    return [] if ctx.AGENTS_TEMPLATE_BEGIN in text and ctx.AGENTS_TEMPLATE_END in text else [sync.rel(path)]


def _table_paths(section: str) -> set[str]:
    paths: set[str] = set()
    for line in section.splitlines():
        match = PATH_RE.match(line.strip())
        if match:
            paths.add(match.group(1))
    return paths


def _table_header(section: str) -> list[str]:
    return [line for line in section.splitlines() if line.strip().startswith("|")][:2]


def _sync_commit(sync: ctx.SyncContext) -> str:
    manifest = sync.consumer / MANIFEST_REL
    if manifest.exists():
        data = _load_json_object(manifest, f"清单文件 {sync.rel(manifest)}")
        resolved = data.get("resolved_commit")
        if isinstance(resolved, str) and resolved.strip():
            return resolved.strip()
    if sync.state_path.exists():
        data = _load_json_object(sync.state_path, f"状态文件 {sync.rel(sync.state_path)}")
        resolved = data.get("last_synced_commit")
        if isinstance(resolved, str) and resolved.strip():
            return resolved.strip()
    raise ctx.SyncError(
        "无法确定 AGENTS.md 同步基线：sync_manifest.json 的 resolved_commit "
        "与 sync_state.json 的 last_synced_commit 均缺失；拒绝猜测旧模板分类"
    )


def _historical_agents(sync: ctx.SyncContext, src: Path, commit: str) -> str:
    if not COMMIT_RE.fullmatch(commit):
        raise ctx.SyncError(f"同步提交非法：{commit!r}；须为 Git commit SHA")
    failures: list[str] = []
    for spec in (f"{commit}:repo/AGENTS.md", f"{commit}:AGENTS.md"):
        result = _git(src, "show", spec)
        if result.returncode == 0:
            return result.stdout
        failures.append(result.stderr.strip() or result.stdout.strip())
    detail = "；".join(item for item in failures if item)
    raise ctx.SyncError(
        f"无法从模板来源 {src} 读取同步提交 {commit} 的 AGENTS.md"
        + (f"（{detail}）" if detail else "")
    )


def _legacy_sections(text: str, label: str) -> tuple[str, str, str]:
    heading_at = text.find(HEADING)
    develop_at = text.find(DEVELOP)
    if heading_at < 0 or develop_at < 0 or develop_at < heading_at:
        raise ctx.SyncError(f"{label} 缺旧版 {HEADING} 或 {DEVELOP}，拒绝猜测分区")
    before, rest = text.split(HEADING, 1)
    directory, develop = rest.split(DEVELOP, 1)
    return before, directory, develop


def _trim_blank(lines: list[str]) -> list[str]:
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _classify_directory(
    section: str, historical: set[str], historical_paths: set[str]
) -> tuple[list[str], list[str], list[str]]:
    rows: list[str] = []
    extras: list[str] = []
    reports: list[str] = []
    extra_mode = False
    for line in section.splitlines():
        stripped = line.strip()
        if extra_mode:
            extras.append(line)
            if stripped:
                reports.append(f"保留表外内容：{stripped}")
            continue
        if not stripped:
            continue
        match = PATH_RE.match(stripped)
        if match:
            path = match.group(1)
            if line in historical:
                continue
            rows.append(line)
            if path in historical_paths:
                reports.append(f"保留改写模板行：`{path}`")
            else:
                reports.append(f"保留未知行：`{path}`")
            continue
        if line in historical:
            continue
        if stripped.startswith("|"):
            rows.append(line)
            reports.append(f"保留未知行：{stripped}")
            continue
        if stripped.startswith("#"):
            extra_mode = True
        extras.append(line)
        reports.append(f"保留表外内容：{stripped}")
    return rows, _trim_blank(extras), reports


def _classify_develop(section: str, historical: set[str]) -> tuple[list[str], list[str]]:
    kept: list[str] = []
    reports: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped:
            if kept and kept[-1]:
                kept.append("")
            continue
        if line in historical:
            continue
        kept.append(line)
        reports.append(f"保留项目开发原则：{stripped}")
    return _trim_blank(kept), reports


def _render_project(
    before: str,
    rows: list[str],
    header: list[str],
    extras: list[str],
    develop: list[str],
) -> str:
    project = before.rstrip("\n") + "\n"
    if rows:
        project += "\n## 项目目录与读写规则\n\n" + "\n".join(header + rows) + "\n"
    if extras:
        project += "\n" + "\n".join(extras) + "\n"
    if develop:
        project += "\n## 项目开发原则\n\n" + "\n".join(develop) + "\n"
    return project


def migrate_agents_zones(sync: ctx.SyncContext, src: Path, *, write: bool = False) -> list[str]:
    target = sync.consumer / "AGENTS.md"
    if not target.is_file():
        return []
    if not agents_zone_migration_status(sync):
        return []
    source_text = (src / "AGENTS.md").read_text(encoding="utf-8")
    source_parts = agents_parts(source_text)
    commit = _sync_commit(sync)
    historical_text = _historical_agents(sync, src, commit)
    _historical_before, historical_dir, historical_dev = _legacy_sections(
        historical_text, f"历史模板 AGENTS.md（{commit}）"
    )
    before, directory, develop = _legacy_sections(
        target.read_text(encoding="utf-8"), "消费仓 AGENTS.md"
    )
    rows, extras, reports = _classify_directory(
        directory, set(historical_dir.splitlines()), _table_paths(historical_dir)
    )
    develop_lines, develop_reports = _classify_develop(
        develop, set(historical_dev.splitlines())
    )
    reports.extend(develop_reports)
    project = _render_project(
        before, rows, _table_header(historical_dir), extras, develop_lines
    )
    result = project + source_parts["template"]
    if not write:
        return [f"{sync.rel(target)} 拟拆为项目区 + 模板区；待保留 {len(reports)} 项"] + reports
    target.write_text(result, encoding="utf-8")
    return [f"{sync.rel(target)} 已拆为项目区 + 模板区"] + reports
