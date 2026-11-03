"""apply 时强制迁移 active/backlog task 文档到当前 workflow schema。"""

from __future__ import annotations

import re
from pathlib import Path

from . import context as ctx
from .common import _git


_GUIDE_OPEN = "<!-- 规范（门禁必留，不得删除） -->"
_GUIDE_CLOSE = "<!-- /规范 -->"
_OLD_NOTE_PREFIXES = (
    "执行期边做边写：",
    "创建期不预测实施步骤——",
    "执行期记录关键步骤、决策、验证、阻塞和用户批准的新轮次上限。",
    "创建期不预测实施步骤。只记有追溯价值的内容；无事项时写“无”。",
)


def _guide_blocks_by_heading(text: str) -> dict[str, list[str]]:
    heading = ""
    blocks: dict[str, list[str]] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("### "):
            heading = line[4:].strip()
        if line.strip() == _GUIDE_OPEN:
            block = [line]
            index += 1
            while index < len(lines):
                block.append(lines[index])
                if lines[index].strip() == _GUIDE_CLOSE:
                    break
                index += 1
            if not block or block[-1].strip() != _GUIDE_CLOSE:
                raise ctx.SyncError("当前 spec 模板规范块未闭合")
            blocks.setdefault(heading, []).append("\n".join(block))
        index += 1
    return blocks


def _replace_guide_blocks(text: str, current_template: str) -> str:
    current = _guide_blocks_by_heading(current_template)
    if not current:
        return text
    lines = text.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped.startswith("### "):
            output.append(lines[index])
            index += 1
            continue
        heading = stripped[4:].strip()
        end = index + 1
        while end < len(lines):
            marker = lines[end].strip()
            if marker.startswith("### ") or marker.startswith("## "):
                break
            end += 1
        section = lines[index:end]
        content: list[str] = []
        cursor = 1
        old_block_count = 0
        while cursor < len(section):
            if section[cursor].strip() != _GUIDE_OPEN:
                content.append(section[cursor])
                cursor += 1
                continue
            old_block_count += 1
            cursor += 1
            while cursor < len(section) and section[cursor].strip() != _GUIDE_CLOSE:
                cursor += 1
            if cursor >= len(section):
                raise ctx.SyncError(f"存量 spec 的 {heading!r} 规范块未闭合，拒绝猜测迁移")
            cursor += 1
        replacements = current.get(heading, [])
        if not replacements and old_block_count == 0:
            output.extend(section)
            index = end
            continue
        while content and not content[0].strip():
            content.pop(0)
        while content and not content[-1].strip():
            content.pop()
        rendered: list[str] = [section[0], ""]
        for block_index, block in enumerate(replacements):
            if block_index:
                rendered.append("")
            rendered.extend(block.splitlines())
        if replacements and content:
            rendered.append("")
        rendered.extend(content)
        rendered.append("")
        output.extend(rendered)
        index = end
    return "\n".join(output).rstrip() + "\n"


def _implementation_guidance(task_template: str) -> list[str]:
    lines = task_template.splitlines()
    start = next(
        (index + 1 for index, line in enumerate(lines) if line.strip() == "## 实施笔记"),
        None,
    )
    if start is None:
        return []
    section = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if line.strip() and line.strip() != "无":
            section.append(line.strip())
        if len(section) == 2:
            break
    return section


def _migrate_task_text(text: str, task_template: str) -> str:
    lines = text.splitlines()
    migrated = []
    has_review = False
    has_verify = False
    in_frontmatter = False
    frontmatter_end = 0
    for index, line in enumerate(lines):
        if index == 0 and line.strip() == "---":
            in_frontmatter = True
        elif in_frontmatter and line.strip() == "---":
            frontmatter_end = index
            in_frontmatter = False
        elif in_frontmatter:
            key = line.partition(":")[0].strip()
            if key == "schedule_status":
                continue
            has_review = has_review or bool(re.match(r'^review_limit\s*:', line))
            has_verify = has_verify or bool(re.match(r'^verify_limit\s*:', line))
            if re.match(r'^status\s*:\s*["\']?blocked["\']?\s*$', line):
                line = 'status: "active"'
        migrated.append(line)

    guidance = _implementation_guidance(task_template)
    section_start = next(
        (index + 1 for index, line in enumerate(migrated) if line.strip() == "## 实施笔记"),
        None,
    )
    if section_start is not None and guidance:
        section_end = next(
            (i for i in range(section_start, len(migrated)) if migrated[i].startswith("## ")),
            len(migrated),
        )
        content = [
            line for line in migrated[section_start:section_end]
            if not any(line.strip().startswith(prefix) for prefix in _OLD_NOTE_PREFIXES)
        ]
        while content and not content[0].strip():
            content.pop(0)
        migrated[section_start:section_end] = [
            "", guidance[0], "", *guidance[1:], "", *content,
        ]
    if frontmatter_end and (not has_review or not has_verify):
        close = next(i for i in range(1, len(migrated)) if migrated[i].strip() == "---")
        fields = []
        if not has_review:
            fields.append('review_limit: "5"')
        if not has_verify:
            fields.append('verify_limit: "5"')
        migrated[close:close] = fields
    migrated_text = "\n".join(migrated).rstrip() + "\n"
    migrated_text = re.sub(
        r"-\s*spec\s*[:：]\s*\[`?spec\.md`?\]\(spec\.md\)",
        "- task：[`task.md`](task.md)",
        migrated_text,
    )
    migrated_text = migrated_text.replace(
        "无长期 spec 变更时在 task.md 写", "无长期 spec 变更时在 note.md 写"
    )
    return migrated_text


def _registered_task_worktrees() -> list[tuple[Path, str]]:
    result = _git(ctx.CONSUMER, "worktree", "list", "--porcelain")
    if result.returncode != 0:
        raise ctx.SyncError(f"无法查询 git worktree（{result.stderr.strip()}）；拒绝放行 schema 迁移")
    rows: list[tuple[Path, str]] = []
    root: Path | None = None
    for raw in result.stdout.splitlines() + [""]:
        if raw.startswith("worktree "):
            root = Path(raw.removeprefix("worktree ")).resolve()
        elif raw.startswith("branch refs/heads/") and root is not None:
            branch = raw.removeprefix("branch refs/heads/")
            if root != ctx.CONSUMER.resolve() and re.match(r"^t[0-9]+_", branch):
                rows.append((root, branch))
        elif not raw:
            root = None
    return rows


def _collect_workflow_task_dirs() -> list[Path]:
    """返回消费仓所有需要检查的 task 目录（含 docs/tasks 与 docs/archive/tasks）。"""
    dirs: list[Path] = []
    for rel_parent in ("docs/tasks", "docs/archive/tasks"):
        parent = ctx.CONSUMER / rel_parent
        if not parent.is_dir():
            continue
        for child in sorted(parent.iterdir()):
            if not child.is_dir():
                continue
            if rel_parent == "docs/tasks" and child.name == "task_template":
                continue
            dirs.append(child)
    return dirs


def _inspect_workflow_task_dir(
    task_dir: Path,
    task_template: str,
    note_template: str,
) -> tuple[list[str], list[str], dict]:
    """分析单个 task 目录的 workflow schema 与布局迁移需求。

    返回 (candidates, errors, plan)
    plan 包含:
      - 'write': list[tuple[Path, str]] 待写文件 (path, content)
      - 'delete': list[Path] 待删文件
    """
    spec_path = task_dir / "spec.md"
    task_path = task_dir / "task.md"
    note_path = task_dir / "note.md"

    candidates: list[str] = []
    errors: list[str] = []
    write_ops: list[tuple[Path, str]] = []
    delete_ops: list[Path] = []

    # 判断是否为旧版文件布局：
    # 1. 存在 spec.md（旧版需求规格）
    # 2. 或者尚无 note.md，但 task.md 存在且内容包含 front matter（旧版过程总账）
    is_legacy_layout = False
    if spec_path.is_file():
        is_legacy_layout = True
    elif not note_path.is_file() and task_path.is_file():
        try:
            head = task_path.read_text(encoding="utf-8")
            if head.startswith("---") and ("status:" in head or "tid:" in head):
                is_legacy_layout = True
        except OSError:
            pass

    if is_legacy_layout:
        # 旧版布局向新版 (task.md 需求规格, note.md 过程总账) 迁移
        # 1. 过程总账：来源于旧 task.md（若存在且尚未有 note.md）或现存 note.md
        if task_path.is_file() and not note_path.is_file():
            candidates.append(ctx._rel(task_path))
            try:
                old_note_text = task_path.read_text(encoding="utf-8")
                new_note_text = _migrate_task_text(old_note_text, note_template)
                write_ops.append((note_path, new_note_text))
            except ctx.SyncError as error:
                errors.append(f"{ctx._rel(task_path)}: {error}")
            except OSError as error:
                errors.append(f"{ctx._rel(task_path)}: {error}")
        elif note_path.is_file():
            try:
                old_note_text = note_path.read_text(encoding="utf-8")
                new_note_text = _migrate_task_text(old_note_text, note_template)
                if new_note_text != old_note_text:
                    candidates.append(ctx._rel(note_path))
                    write_ops.append((note_path, new_note_text))
            except ctx.SyncError as error:
                errors.append(f"{ctx._rel(note_path)}: {error}")
            except OSError as error:
                errors.append(f"{ctx._rel(note_path)}: {error}")

        # 2. 需求规格：来源于旧 spec.md，迁移写入 task.md，并删除原 spec.md
        if spec_path.is_file():
            candidates.append(ctx._rel(spec_path))
            try:
                old_spec_text = spec_path.read_text(encoding="utf-8")
                new_task_text = _replace_guide_blocks(old_spec_text, task_template)
                write_ops.append((task_path, new_task_text))
                delete_ops.append(spec_path)
            except ctx.SyncError as error:
                errors.append(f"{ctx._rel(spec_path)}: {error}")
            except OSError as error:
                errors.append(f"{ctx._rel(spec_path)}: {error}")
        elif not spec_path.is_file() and task_path.is_file() and not any(p == task_path for p, _ in write_ops):
            delete_ops.append(task_path)
    else:
        # 新版布局：task.md 为需求规格，note.md 为过程总账
        if task_path.is_file():
            try:
                before = task_path.read_text(encoding="utf-8")
                after = _replace_guide_blocks(before, task_template)
                if after != before:
                    candidates.append(ctx._rel(task_path))
                    write_ops.append((task_path, after))
            except ctx.SyncError as error:
                errors.append(f"{ctx._rel(task_path)}: {error}")
            except OSError as error:
                errors.append(f"{ctx._rel(task_path)}: {error}")

        if note_path.is_file():
            try:
                before = note_path.read_text(encoding="utf-8")
                after = _migrate_task_text(before, note_template)
                if after != before:
                    candidates.append(ctx._rel(note_path))
                    write_ops.append((note_path, after))
            except ctx.SyncError as error:
                errors.append(f"{ctx._rel(note_path)}: {error}")
            except OSError as error:
                errors.append(f"{ctx._rel(note_path)}: {error}")

    plan = {
        "legacy": is_legacy_layout,
        "write": write_ops,
        "delete": delete_ops,
    }
    return candidates, errors, plan


def workflow_migration_status(
    src: Path,
) -> tuple[list[str], list[tuple[Path, str]], list[str]]:
    task_template_path = src / ".repo_template/docs/task_template/task.md"
    note_template_path = src / ".repo_template/docs/task_template/note.md"
    try:
        worktrees = _registered_task_worktrees()
    except ctx.SyncError as err:
        return [], [], [str(err)]
    if not task_template_path.is_file() or not note_template_path.is_file():
        return [], worktrees, []
    task_template = task_template_path.read_text(encoding="utf-8")
    note_template = note_template_path.read_text(encoding="utf-8")
    if not _guide_blocks_by_heading(task_template) or "## 实施笔记" not in note_template:
        return [], worktrees, []

    candidates: list[str] = []
    errors: list[str] = []
    for task_dir in _collect_workflow_task_dirs():
        dir_candidates, dir_errors, _ = _inspect_workflow_task_dir(
            task_dir, task_template, note_template
        )
        errors.extend(dir_errors)
        if not dir_errors:
            candidates.extend(dir_candidates)
    return sorted(candidates), worktrees, sorted(errors)


def force_migrate_workflow_tasks(changed: set[Path]) -> list[str]:
    task_template_path = ctx.CONSUMER / ".repo_template/docs/task_template/task.md"
    note_template_path = ctx.CONSUMER / ".repo_template/docs/task_template/note.md"
    if not task_template_path.is_file() or not note_template_path.is_file():
        return []
    task_template = task_template_path.read_text(encoding="utf-8")
    note_template = note_template_path.read_text(encoding="utf-8")
    if not _guide_blocks_by_heading(task_template) or "## 实施笔记" not in note_template:
        return []
    registered = _registered_task_worktrees()
    if registered:
        detail = ", ".join(f"{branch}@{root}" for root, branch in registered)
        raise ctx.SyncError(
            "workflow schema 强制更新前必须先完成或 rewind 已登记 task worktree：" + detail
        )

    all_errors: list[str] = []
    all_plans = []
    for task_dir in _collect_workflow_task_dirs():
        _, errors, plan = _inspect_workflow_task_dir(
            task_dir, task_template, note_template
        )
        all_errors.extend(errors)
        if plan["write"] or plan["delete"]:
            all_plans.append(plan)

    if all_errors:
        raise ctx.SyncError("workflow schema 无法安全迁移：" + "；".join(sorted(all_errors)))

    reports: list[str] = []
    for plan in all_plans:
        sorted_writes = sorted(
            plan["write"], key=lambda item: 0 if item[0].name == "note.md" else 1
        )
        for path, content in sorted_writes:
            path.write_text(content, encoding="utf-8")
            changed.add(path)
            reports.append(f"{ctx._rel(path)} 已强制迁移到当前 workflow schema")
        for path in plan["delete"]:
            if path.exists():
                path.unlink()
                changed.add(path)
                reports.append(f"{ctx._rel(path)} 已删除（迁移到 task.md）")
    return reports
