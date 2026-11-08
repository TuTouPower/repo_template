"""install-hooks 子命令与已退役 merge guard 配置清理。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import context as ctx
from .common import _detect_json_indent, _git, consumer_locked


_RETIRED_MERGE_GUARD_COMMAND = 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/merge_guard.py"'
def _remove_retired_merge_guard_setting(changed: set[Path], reports: list[str], sync=None) -> None:
    sync = sync or ctx.current_sync()
    settings = sync.consumer / ".claude/settings.json"
    if not settings.is_file():
        return
    try:
        raw = settings.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as error:
        raise ctx.SyncError(
            f"{sync.rel(settings)} 无法解析，不能安全移除已退役 merge guard PreToolUse（{error}）"
        ) from error
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return
    groups = hooks.get("PreToolUse")
    if not isinstance(groups, list):
        return
    changed_settings = False
    kept_groups = []
    for group in groups:
        if not isinstance(group, dict):
            kept_groups.append(group)
            continue
        entries = group.get("hooks")
        if not isinstance(entries, list):
            kept_groups.append(group)
            continue
        kept_entries = [
            entry for entry in entries
            if not (
                isinstance(entry, dict)
                and str(entry.get("command", "")).strip() == _RETIRED_MERGE_GUARD_COMMAND
            )
        ]
        if len(kept_entries) != len(entries):
            changed_settings = True
        if kept_entries:
            updated = dict(group)
            updated["hooks"] = kept_entries
            kept_groups.append(updated)
    if not changed_settings:
        return
    if kept_groups:
        hooks["PreToolUse"] = kept_groups
    else:
        hooks.pop("PreToolUse", None)
    if not hooks:
        data.pop("hooks", None)
    temporary = settings.with_name(settings.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=_detect_json_indent(raw)) + "\n", encoding="utf-8")
    temporary.replace(settings)
    changed.add(settings)
    reports.append(f"{sync.rel(settings)} 已移除退役 merge guard PreToolUse")


def _retired_guard_state(sync=None) -> tuple[Path, bool, bool]:
    sync = sync or ctx.current_sync()
    legacy = sync.consumer / ".claude/hooks/merge_guard.py"
    retired = sync.consumer / ".repo_template/hooks/merge_guard.py"
    managed = (
        legacy.is_symlink()
        and legacy.resolve(strict=False) == retired.resolve(strict=False)
    )
    return legacy, managed, not legacy.exists() and not legacy.is_symlink()


def retired_hooks_status(sync=None) -> list[str]:
    """返回仍残留的退役 hook 现场（链接或 settings 中的命令）。"""
    sync = sync or ctx.current_sync()
    legacy, managed, missing_link = _retired_guard_state(sync=sync)
    findings: list[str] = []
    if managed:
        findings.append(sync.rel(legacy))
    settings = sync.consumer / ".claude/settings.json"
    if settings.is_file():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as error:
            raise ctx.SyncError(
                f"{sync.rel(settings)} 无法解析，不能安全移除已退役 merge guard PreToolUse（{error}）"
            ) from error
        groups = (data or {}).get("hooks", {}).get("PreToolUse", []) if isinstance(data, dict) else []
        if isinstance(groups, list):
            for group in groups:
                entries = group.get("hooks") if isinstance(group, dict) else None
                if isinstance(entries, list) and any(
                    isinstance(entry, dict)
                    and str(entry.get("command", "")).strip() == _RETIRED_MERGE_GUARD_COMMAND
                    for entry in entries
                ):
                    findings.append(sync.rel(settings))
                    break
    if missing_link and not findings:
        return []
    return sorted(set(findings))


def migrate_retired_hooks(sync=None) -> list[str]:
    """显式移除由模板管理的退役 merge_guard 链接与设置项，保留消费方自定义 hook。"""
    sync = sync or ctx.current_sync()
    legacy, managed, missing_link = _retired_guard_state(sync=sync)
    changed: set[Path] = set()
    reports: list[str] = []
    if not retired_hooks_status(sync=sync):
        return reports
    if managed or missing_link:
        _remove_retired_merge_guard_setting(changed, reports, sync=sync)
    if managed:
        legacy.unlink()
        changed.add(legacy)
        reports.append(f"{sync.rel(legacy)} 已退役并移除")
    return reports


@consumer_locked
def cmd_install_hooks(args: argparse.Namespace, sync=None) -> int:
    """幂等设置 core.hooksPath 指向工具链 hooks。

    git 对相对路径按仓库顶层解析。消费仓 hooks 在 `.repo_template/hooks`；
    工厂仓产物在 `repo/` 下时须写成 `repo/.repo_template/hooks`。已有其它
    hooksPath 时拒绝，须 --force。
    """
    sync = getattr(args, "sync", None) or sync or ctx.current_sync()
    hooks_dir = sync.consumer / ".repo_template" / "hooks"
    hook_script = hooks_dir / "pre-commit"
    if not hook_script.is_file():
        print("错误: 缺 hook 脚本 .repo_template/hooks/pre-commit", file=sys.stderr)
        return 1
    if not os.access(hook_script, os.X_OK):
        print("错误: .repo_template/hooks/pre-commit 不可执行", file=sys.stderr)
        return 1
    toplevel = _git(sync.consumer, "rev-parse", "--show-toplevel").stdout.strip()
    if not toplevel:
        print("错误: 无法解析 git 仓库顶层", file=sys.stderr)
        return 1
    try:
        hooks_rel = hooks_dir.resolve().relative_to(Path(toplevel).resolve()).as_posix()
    except ValueError:
        print(
            f"错误: hooks 目录不在仓库顶层内：{hooks_dir} / {toplevel}",
            file=sys.stderr,
        )
        return 1
    current = _git(sync.consumer, "config", "--get", "core.hooksPath").stdout.strip()
    if current == hooks_rel:
        print(f"core.hooksPath 已指向 {hooks_rel}，无需改动")
        return 0
    if current and not getattr(args, "force", False):
        print(
            f"错误: core.hooksPath 已是 {current}；"
            f"覆盖为 {hooks_rel} 须加 --force",
            file=sys.stderr,
        )
        return 1
    result = _git(sync.consumer, "config", "core.hooksPath", hooks_rel)
    if result.returncode != 0:
        print(f"错误: 设置 core.hooksPath 失败：{result.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"core.hooksPath 已设为 {hooks_rel}")
    return 0
