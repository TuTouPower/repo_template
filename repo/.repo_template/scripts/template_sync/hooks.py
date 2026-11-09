"""install-hooks 子命令与已退役 merge guard 配置清理。

status 与 action 共用同一「可处理集合」：status 报告什么，migrate 就处理什么，
避免 plan/apply 显示一致但 action 无法收敛的死锁。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import context as ctx
from .common import _detect_json_indent, _git, consumer_locked


_RETIRED_MERGE_GUARD_COMMAND = 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/merge_guard.py"'


def _load_settings(sync: ctx.SyncContext) -> tuple[dict | None, str | None]:
    """统一读取 .claude/settings.json；损坏或顶层非对象一律转 SyncError。

    链式 .get 在顶层为数组、hooks 为数组/None 时会抛 AttributeError，进而让
    事务 journal 停留在 applying；这里在读取后即做类型校验。
    """
    settings = sync.consumer / ".claude/settings.json"
    if not settings.is_file():
        return None, None
    try:
        raw = settings.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as error:
        raise ctx.SyncError(
            f"{sync.rel(settings)} 无法解析，不能安全移除已退役 merge guard PreToolUse（{error}）"
        ) from error
    if not isinstance(data, dict):
        raise ctx.SyncError(
            f"{sync.rel(settings)} 顶层必须是 JSON 对象，不能安全移除已退役 merge guard PreToolUse"
        )
    return data, raw


def _settings_has_retired_command(data: dict) -> bool:
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    groups = hooks.get("PreToolUse")
    if not isinstance(groups, list):
        return False
    for group in groups:
        if not isinstance(group, dict):
            continue
        entries = group.get("hooks")
        if not isinstance(entries, list):
            continue
        if any(
            isinstance(entry, dict)
            and str(entry.get("command", "")).strip() == _RETIRED_MERGE_GUARD_COMMAND
            for entry in entries
        ):
            return True
    return False


def _remove_retired_merge_guard_setting(
    changed: set[Path], reports: list[str], sync: ctx.SyncContext
) -> None:
    data, raw = _load_settings(sync)
    if data is None or raw is None:
        return
    hooks = data.get("hooks")
    groups = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
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
    settings = sync.consumer / ".claude/settings.json"
    temporary = settings.with_name(settings.name + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=_detect_json_indent(raw)) + "\n",
        encoding="utf-8",
    )
    temporary.replace(settings)
    changed.add(settings)
    reports.append(f"{sync.rel(settings)} 已移除退役 merge guard PreToolUse")


def _retired_guard_state(sync: ctx.SyncContext) -> tuple[Path, bool]:
    legacy = sync.consumer / ".claude/hooks/merge_guard.py"
    retired = sync.consumer / ".repo_template/hooks/merge_guard.py"
    managed = (
        legacy.is_symlink()
        and legacy.resolve(strict=False) == retired.resolve(strict=False)
    )
    return legacy, managed


def retired_hooks_status(sync: ctx.SyncContext) -> list[str]:
    """返回仍残留的退役 hook 现场；与 migrate_retired_hooks 的可处理集合完全一致。"""
    legacy, managed = _retired_guard_state(sync)
    findings: list[str] = []
    if managed:
        findings.append(sync.rel(legacy))
    data, _ = _load_settings(sync)
    if data is not None and _settings_has_retired_command(data):
        findings.append(sync.rel(sync.consumer / ".claude/settings.json"))
    return sorted(set(findings))


def migrate_retired_hooks(sync: ctx.SyncContext) -> list[str]:
    """按 retired_hooks_status 的可处理集合逐项处理，保留消费方自定义 hook。"""
    findings = retired_hooks_status(sync=sync)
    if not findings:
        return []
    changed: set[Path] = set()
    reports: list[str] = []
    legacy, managed = _retired_guard_state(sync)
    settings_rel = sync.rel(sync.consumer / ".claude/settings.json")
    if settings_rel in findings:
        _remove_retired_merge_guard_setting(changed, reports, sync)
    if managed and sync.rel(legacy) in findings:
        legacy.unlink()
        changed.add(legacy)
        reports.append(f"{sync.rel(legacy)} 已退役并移除")
    return reports


@consumer_locked
def cmd_install_hooks(args: argparse.Namespace) -> int:
    """幂等设置 core.hooksPath 指向工具链 hooks。

    git 对相对路径按仓库顶层解析。消费仓 hooks 在 `.repo_template/hooks`；
    工厂仓产物在 `repo/` 下时须写成 `repo/.repo_template/hooks`。已有其它
    hooksPath 时拒绝，须 --force。
    """
    sync: ctx.SyncContext = args.sync
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
