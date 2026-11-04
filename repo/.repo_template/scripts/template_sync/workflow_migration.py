"""apply 时强制迁移 docs/tasks 与 docs/archive/tasks 下的 task 文档到当前 workflow schema。

迁移一步到位，支持两种旧布局：
- 最早：spec.md（需求规格）+ task.md（含 front matter 的过程总账）
- 中间：task.md（需求规格）+ note.md（含 front matter 的过程总账，标题「Task 过程总账」）
目标布局：task.md + progress.md（标题「Task 进展」）。note.md 与 progress.md 并存时拒绝，不猜测合并。
"""

from __future__ import annotations

import re
from pathlib import Path

from repo_task.context import PROGRESS_FILE, PROGRESS_TITLE, TASK_BRANCH_RE
from repo_task.git_ops import default_branch

from . import context as ctx
from .common import _git


_GUIDE_OPEN = "<!-- 规范（门禁必留，不得删除） -->"
_GUIDE_CLOSE = "<!-- /规范 -->"
_LEGACY_NOTE_FILE = "note.md"
_LEGACY_NOTE_TITLE = "Task 过程总账"
_OLD_NOTE_PREFIXES = (
    "执行期边做边写：",
    "创建期不预测实施步骤——",
    "执行期记录关键步骤、决策、验证、阻塞和用户批准的新轮次上限。",
    "创建期不预测实施步骤。只记有追溯价值的内容；无事项时写“无”。",
)


def _fence_states(lines: list[str]) -> list[bool]:
    """标记每行是否属于代码围栏；闭围栏须与开围栏字符相同且长度不短于开围栏。"""
    states: list[bool] = []
    fence_char = ""
    fence_len = 0
    for line in lines:
        match = re.match(r"^(`{3,}|~{3,})(.*)$", line.strip())
        if fence_char:
            states.append(True)
            if (
                match
                and match.group(1)[0] == fence_char
                and len(match.group(1)) >= fence_len
                and not match.group(2).strip()
            ):
                fence_char = ""
                fence_len = 0
        elif match:
            fence_char = match.group(1)[0]
            fence_len = len(match.group(1))
            states.append(True)
        else:
            states.append(False)
    return states


def _guide_blocks_by_heading(text: str) -> dict[str, list[str]]:
    heading = ""
    blocks: dict[str, list[str]] = {}
    lines = text.splitlines()
    fenced = _fence_states(lines)
    index = 0
    while index < len(lines):
        line = lines[index]
        if fenced[index]:
            index += 1
            continue
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
    fenced = _fence_states(lines)
    output: list[str] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if fenced[index] or not stripped.startswith("### "):
            output.append(lines[index])
            index += 1
            continue
        heading = stripped[4:].strip()
        end = index + 1
        while end < len(lines):
            marker = lines[end].strip()
            if not fenced[end] and (marker.startswith("### ") or marker.startswith("## ")):
                break
            end += 1
        section = lines[index:end]
        content: list[str] = []
        cursor = 1
        old_block_count = 0
        while cursor < len(section):
            if fenced[index + cursor] or section[cursor].strip() != _GUIDE_OPEN:
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


def _rename_legacy_title(lines: list[str], frontmatter_end: int) -> None:
    """把正文中围栏外的一级标题「Task 过程总账」就地改为「Task 进展」；只改第一处。"""
    start = frontmatter_end + 1 if frontmatter_end else 0
    body = lines[start:]
    fenced = _fence_states(body)
    for offset, line in enumerate(body):
        if fenced[offset]:
            continue
        if line.strip() == f"# {_LEGACY_NOTE_TITLE}":
            lines[start + offset] = f"# {PROGRESS_TITLE}"
            return


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
    _rename_legacy_title(migrated, frontmatter_end)
    migrated_text = "\n".join(migrated).rstrip() + "\n"
    migrated_text = re.sub(
        r"-\s*spec\s*[:：]\s*\[`?spec\.md`?\]\(spec\.md\)",
        "- task：[`task.md`](task.md)",
        migrated_text,
    )
    for old_name in ("task.md", _LEGACY_NOTE_FILE):
        migrated_text = migrated_text.replace(
            f"无长期 spec 变更时在 {old_name} 写", f"无长期 spec 变更时在 {PROGRESS_FILE} 写"
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


_FRONT_MATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.DOTALL)
_STATUS_LINE_RE = re.compile(r"(?m)^status:[ \t]*[\"']?([A-Za-z]+)[\"']?[ \t]*$")


def _branch_task_status(branch: str) -> str:
    """分支 tip 中该 task 的状态；活跃与归档目录都查，读不到返回空串。"""
    for directory in ("docs/tasks", "docs/archive/tasks"):
        for name in (PROGRESS_FILE, _LEGACY_NOTE_FILE, "task.md"):
            shown = _git(ctx.CONSUMER, "show", f"refs/heads/{branch}:{directory}/{branch}/{name}")
            if shown.returncode != 0:
                continue
            front_matter = _FRONT_MATTER_RE.match(shown.stdout)
            status = _STATUS_LINE_RE.search(front_matter.group(1)) if front_matter else None
            if status:
                return status.group(1)
    return ""


def _unintegrated_done_branches() -> tuple[str, list[str]]:
    """返回 (默认分支, 状态为 done 但尚未合入默认分支的 task 分支)。

    done task 的目录由 integrate 的 merge commit 归档；旧布局下这类分支的目录已在分支内
    归档，新工具链不处理这种中间形态，必须先 integrate。git 查询失败 fail-closed。
    """
    listed = _git(ctx.CONSUMER, "branch", "--format=%(refname:short)", "--list", "t[0-9]*_*")
    if listed.returncode != 0:
        raise ctx.SyncError(f"无法查询 task 分支（{listed.stderr.strip()}）；拒绝放行 schema 迁移")
    branches = [line.strip() for line in listed.stdout.splitlines() if TASK_BRANCH_RE.fullmatch(line.strip())]
    base = default_branch(ctx.CONSUMER)
    if not branches:
        return base, []
    unmerged = _git(ctx.CONSUMER, "branch", "--format=%(refname:short)", "--no-merged", base, "--list", "t[0-9]*_*")
    if unmerged.returncode != 0:
        raise ctx.SyncError(
            f"无法判断 task 分支是否已合入 {base}（{unmerged.stderr.strip()}）；拒绝放行 schema 迁移"
        )
    pending = {line.strip() for line in unmerged.stdout.splitlines()}
    return base, [branch for branch in sorted(branches) if branch in pending and _branch_task_status(branch) == "done"]


def _unintegrated_done_message(base: str, branches: list[str]) -> str:
    return (
        f"以下 task 分支状态为 done 但尚未合入 {base}：{', '.join(branches)}；"
        "先在主仓 integrate（或 integrate-chain）合入后再同步"
    )


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
    note_path = task_dir / PROGRESS_FILE
    legacy_note_path = task_dir / _LEGACY_NOTE_FILE

    candidates: list[str] = []
    errors: list[str] = []
    write_ops: list[tuple[Path, str]] = []
    delete_ops: list[Path] = []

    if legacy_note_path.exists() and note_path.exists():
        return [], [f"{ctx._rel(task_dir)}: note.md 与 {PROGRESS_FILE} 并存，拒绝猜测合并"], {
            "legacy": False, "write": [], "delete": [],
        }
    source_note_path = legacy_note_path if legacy_note_path.is_file() else note_path

    # 判断是否为旧版文件布局：
    # 1. 存在 spec.md（旧版需求规格）
    # 2. 或者尚无过程文件，但 task.md 存在且内容包含 front matter（旧版过程总账）
    is_legacy_layout = False
    if spec_path.is_file():
        is_legacy_layout = True
    elif not source_note_path.is_file() and task_path.is_file():
        try:
            head = task_path.read_text(encoding="utf-8")
            if head.startswith("---") and ("status:" in head or "tid:" in head):
                is_legacy_layout = True
        except OSError:
            pass

    if is_legacy_layout:
        # 旧版布局迁到 task.md 需求规格与当前过程文件。
        # 过程记录优先读取独立过程文件，否则读取含 front matter 的旧 task.md。
        if task_path.is_file() and not source_note_path.is_file():
            candidates.append(ctx._rel(task_path))
            try:
                old_note_text = task_path.read_text(encoding="utf-8")
                new_note_text = _migrate_task_text(old_note_text, note_template)
                write_ops.append((note_path, new_note_text))
            except ctx.SyncError as error:
                errors.append(f"{ctx._rel(task_path)}: {error}")
            except OSError as error:
                errors.append(f"{ctx._rel(task_path)}: {error}")
        elif source_note_path.is_file():
            try:
                old_note_text = source_note_path.read_text(encoding="utf-8")
                new_note_text = _migrate_task_text(old_note_text, note_template)
                if new_note_text != old_note_text or source_note_path != note_path:
                    candidates.append(ctx._rel(source_note_path))
                    write_ops.append((note_path, new_note_text))
            except ctx.SyncError as error:
                errors.append(f"{ctx._rel(source_note_path)}: {error}")
            except OSError as error:
                errors.append(f"{ctx._rel(source_note_path)}: {error}")

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
        # 分离布局：task.md 为需求规格，独立过程文件迁到当前名称。
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

        if source_note_path.is_file():
            try:
                before = source_note_path.read_text(encoding="utf-8")
                after = _migrate_task_text(before, note_template)
                if after != before or source_note_path != note_path:
                    candidates.append(ctx._rel(source_note_path))
                    write_ops.append((note_path, after))
            except ctx.SyncError as error:
                errors.append(f"{ctx._rel(source_note_path)}: {error}")
            except OSError as error:
                errors.append(f"{ctx._rel(source_note_path)}: {error}")

    if source_note_path == legacy_note_path and any(path == note_path for path, _ in write_ops):
        delete_ops.append(legacy_note_path)
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
    note_template_path = src / ".repo_template/docs/task_template" / PROGRESS_FILE
    try:
        worktrees = _registered_task_worktrees()
        base, unintegrated = _unintegrated_done_branches()
    except ctx.SyncError as err:
        return [], [], [str(err)]
    layout_errors = [_unintegrated_done_message(base, unintegrated)] if unintegrated else []
    if not task_template_path.is_file() or not note_template_path.is_file():
        return [], worktrees, layout_errors
    task_template = task_template_path.read_text(encoding="utf-8")
    note_template = note_template_path.read_text(encoding="utf-8")
    if not _guide_blocks_by_heading(task_template) or "## 实施笔记" not in note_template:
        return [], worktrees, layout_errors

    candidates: list[str] = []
    errors: list[str] = list(layout_errors)
    for task_dir in _collect_workflow_task_dirs():
        dir_candidates, dir_errors, _ = _inspect_workflow_task_dir(
            task_dir, task_template, note_template
        )
        errors.extend(dir_errors)
        if not dir_errors:
            candidates.extend(dir_candidates)
    return sorted(candidates), worktrees, sorted(errors)


def force_migrate_workflow_tasks(changed: set[Path]) -> list[str]:
    base, unintegrated = _unintegrated_done_branches()
    if unintegrated:
        raise ctx.SyncError(_unintegrated_done_message(base, unintegrated))
    task_template_path = ctx.CONSUMER / ".repo_template/docs/task_template/task.md"
    note_template_path = ctx.CONSUMER / ".repo_template/docs/task_template" / PROGRESS_FILE
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
            plan["write"], key=lambda item: 0 if item[0].name == PROGRESS_FILE else 1
        )
        for path, content in sorted_writes:
            path.write_text(content, encoding="utf-8")
            changed.add(path)
            reports.append(f"{ctx._rel(path)} 已强制迁移到当前 workflow schema")
        for path in plan["delete"]:
            if path.exists():
                path.unlink()
                changed.add(path)
                target = PROGRESS_FILE if path.name == _LEGACY_NOTE_FILE else "task.md"
                reports.append(f"{ctx._rel(path)} 已删除（迁移到 {target}）")
    return reports
