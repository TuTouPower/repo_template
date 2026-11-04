"""宿主 skill 软链与 opencode 命令薄层的状态、修复与 link-skills 子命令。"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from repo_task.file_transaction import FileTransaction

from . import context as ctx
from .assets import _dir_has_diff
from .common import _load_json_object, consumer_locked
from .hooks import _remove_retired_merge_guard_setting


def _ensure_symlink(link: Path, target_rel: str, expected: Path, changed: set[Path], reports: list[str]) -> None:
    if link.is_symlink():
        if link.resolve() == expected:
            return
        link.unlink()
        os.symlink(target_rel, link)
        changed.add(link)
        return
    if link.is_dir():
        # 旧架构残留：宿主位曾是模板同步进去的真实目录。新架构真相源收敛到
        # .repo_template/skills/，宿主位只留软链。备份旧内容后转软链。
        shutil.rmtree(link)
        os.symlink(target_rel, link)
        changed.add(link)
        reports.append(f"{ctx._rel(link)} 旧真实目录已转软链（原内容入回滚栈）")
        return
    if link.exists():
        reports.append(f"{ctx._rel(link)} 非本机制管理，跳过")
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target_rel, link)
    changed.add(link)


def repair_symlinks(changed: set[Path]) -> list[str]:
    """建/修宿主软链，指向 .repo_template/。清理已删除 skill 的残留软链。非本机制链接报告不碰。"""
    reports: list[str] = []
    valid_names = set()
    if ctx.SKILLS_SRC.exists():
        for entry in ctx.SKILLS_SRC.iterdir():
            if not entry.is_dir():
                continue
            valid_names.add(entry.name)
            expected = entry.resolve()
            _ensure_symlink(
                ctx.SKILLS_CLAUDE / entry.name,
                os.path.join("..", "..", ".repo_template", "skills", entry.name),
                expected, changed, reports,
            )
            _ensure_symlink(
                ctx.SKILLS_AGENTS / entry.name,
                os.path.join("..", "..", ".repo_template", "skills", entry.name),
                expected, changed, reports,
            )
    for host_dir in (ctx.SKILLS_CLAUDE, ctx.SKILLS_AGENTS):
        if not host_dir.exists():
            continue
        for item in sorted(host_dir.iterdir()):
            if not item.is_symlink() or item.name in valid_names:
                continue
            try:
                target_resolved = item.resolve(strict=False)
                skills_src_resolved = ctx.SKILLS_SRC.resolve()
                is_managed = (
                    target_resolved != skills_src_resolved
                    and target_resolved.is_relative_to(skills_src_resolved)
                )
            except (OSError, ValueError):
                is_managed = False
            if is_managed:
                item.unlink()
                changed.add(item)
                reports.append(f"{ctx._rel(item)} 已清理（对应 skill 已删除）")
    legacy_guard = ctx.CONSUMER / ".claude/hooks/merge_guard.py"
    retired_target = ctx.CONSUMER / ".repo_template/hooks/merge_guard.py"
    managed_retired_guard = (
        legacy_guard.is_symlink()
        and legacy_guard.resolve(strict=False) == retired_target.resolve(strict=False)
    )
    guard_missing = not legacy_guard.exists() and not legacy_guard.is_symlink()
    if managed_retired_guard or guard_missing:
        _remove_retired_merge_guard_setting(changed, reports)
    if managed_retired_guard:
        legacy_guard.unlink()
        changed.add(legacy_guard)
        reports.append(f"{ctx._rel(legacy_guard)} 已退役并移除")
    return reports


def _skill_description(skill_dir: Path, name: str) -> str:
    """读 SKILL.md frontmatter 的 description；缺失/无意义时回退。"""
    desc = f"Run {name} skill"
    try:
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return desc
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                if line.startswith("description:"):
                    desc = line.split(":", 1)[1].strip()
                    break
    if not desc or desc.lower() == "none":
        desc = f"Run {name} skill"
    return desc.replace('"', "'")


def _expected_opencode_command_content(name: str) -> str:
    return (
        "---\n"
        f'description: "{_skill_description(ctx.SKILLS_SRC / name, name)}"\n'
        "---\n"
        f"> {ctx.COMMAND_MARKER} '{name}'. Do not edit directly.\n\n"
        f"Load and execute the `{name}` skill via the `skill` tool, "
        "following its SKILL.md instructions.\n\n"
        "User arguments: $ARGUMENTS\n"
    )


def _load_skill_roles() -> dict | None:
    path = ctx.SKILLS_SRC / "registry.json"
    if not path.exists():
        return None
    data = _load_json_object(path, "skills/registry.json")
    roles = data.get("skills")
    if not isinstance(roles, dict):
        raise ctx.SyncError("skills/registry.json 结构错误：缺 skills 对象")
    return roles


def _public_skill_names(names: list[str]) -> list[str]:
    """校验 registry 覆盖全部 skill；未登记即 fail-closed，status 与 apply 同口径。"""
    roles = _load_skill_roles()
    if roles is None:
        return names
    missing = sorted(name for name in names if name not in roles)
    if missing:
        raise ctx.SyncError(
            "skills/registry.json 未登记 skill：" + ", ".join(missing) + "；先补登记后重跑"
        )
    return [name for name in names if roles[name].get("role") == "public"]


def opencode_commands_status() -> list[dict]:
    """返回 opencode 命令薄层状态：[{"name": ..., "rel": ..., "state": ..., "desc": ...}]。"""
    items = []
    if not ctx.SKILLS_SRC.exists():
        return items
    names = sorted(e.name for e in ctx.SKILLS_SRC.iterdir() if e.is_dir())
    names = _public_skill_names(names)
    for name in names:
        dest = ctx.COMMANDS_OPENCODE / f"{name}.md"
        expected = _expected_opencode_command_content(name)
        if not dest.exists():
            items.append({
                "name": name,
                "rel": ctx._rel(dest),
                "state": "create",
                "desc": "缺失命令薄层（apply 生成）",
            })
        elif not dest.is_file():
            items.append({
                "name": name,
                "rel": ctx._rel(dest),
                "state": "unmanaged",
                "desc": "非文件目标，跳过",
            })
        else:
            try:
                existing = dest.read_text(encoding="utf-8")
                if existing == expected:
                    items.append({
                        "name": name,
                        "rel": ctx._rel(dest),
                        "state": "same",
                        "desc": "一致",
                    })
                elif ctx.COMMAND_MARKER not in existing:
                    items.append({
                        "name": name,
                        "rel": ctx._rel(dest),
                        "state": "unmanaged",
                        "desc": "疑似手写命令，跳过",
                    })
                else:
                    items.append({
                        "name": name,
                        "rel": ctx._rel(dest),
                        "state": "update",
                        "desc": "描述或正文已漂移（apply 更新）",
                    })
            except OSError:
                continue

    if ctx.COMMANDS_OPENCODE.exists() and ctx.COMMANDS_OPENCODE.is_dir():
        for md in sorted(ctx.COMMANDS_OPENCODE.glob("*.md")):
            if md.stem not in names:
                try:
                    text = md.read_text(encoding="utf-8")
                    if ctx.COMMAND_MARKER in text:
                        items.append({
                            "name": md.stem,
                            "rel": ctx._rel(md),
                            "state": "cleanup",
                            "desc": "skill 已移除，残留薄层（apply 清理）",
                        })
                except OSError:
                    continue
    return items


def sync_opencode_commands(changed: set[Path]) -> list[str]:
    """项目级 opencode 命令薄层：每个 skill 生成同名 /<skill> 命令。

    opencode 能直接识别 .agents/skills，但缺 / 触发器。命令正文只做一件事：
    让 agent 经 skill 工具加载执行对应 skill，$ARGUMENTS 透传。description
    取自 SKILL.md frontmatter（SKILL.md 是唯一真相源）。
    手写命令（无本机制标记）永不覆盖/删除；skill 删除后仅清理带标记残留。
    """
    reports: list[str] = []
    if not ctx.SKILLS_SRC.exists():
        return reports
    ctx.COMMANDS_OPENCODE.mkdir(parents=True, exist_ok=True)
    names = sorted(e.name for e in ctx.SKILLS_SRC.iterdir() if e.is_dir())
    names = _public_skill_names(names)
    for name in names:
        content = _expected_opencode_command_content(name)
        dest = ctx.COMMANDS_OPENCODE / f"{name}.md"
        if dest.exists() and not dest.is_file():
            continue
        if dest.is_file():
            existing = dest.read_text(encoding="utf-8")
            if existing == content:
                continue
            if ctx.COMMAND_MARKER not in existing:
                reports.append(f"{ctx._rel(dest)} 疑似手写命令（与 skill 同名），跳过")
                continue
        dest.write_text(content, encoding="utf-8")
        changed.add(dest)
    for md in ctx.COMMANDS_OPENCODE.glob("*.md"):
        if md.stem in names:
            continue
        try:
            if ctx.COMMAND_MARKER in md.read_text(encoding="utf-8"):
                md.unlink()
                changed.add(md)
        except OSError:
            continue
    return reports


def skill_status(src: Path) -> list[dict]:
    """skill 状态：模板侧 override；消费侧独有 keep；旧真实目录 stale。"""
    out: list[dict] = []
    src_skills = src / ".repo_template/skills"
    if src_skills.exists():
        for s in src_skills.iterdir():
            if s.is_dir():
                d = ctx.SKILLS_SRC / s.name
                diff = not d.exists() or _dir_has_diff(s, d)
                out.append({"name": s.name, "action": "override" if diff else "same"})
    if ctx.SKILLS_AGENTS.exists():
        for d in ctx.SKILLS_AGENTS.iterdir():
            if not d.is_dir():
                continue
            if d.is_symlink():
                continue
            if src_skills.exists() and (src_skills / d.name).exists():
                out.append({"name": d.name, "action": "stale"})
                continue
            out.append({"name": d.name, "action": "keep"})
    return out


def symlink_status() -> list[dict]:
    out: list[dict] = []
    valid_names = set()
    hosts = (
        (ctx.SKILLS_CLAUDE, ".claude/skills"),
        (ctx.SKILLS_AGENTS, ".agents/skills"),
    )
    if ctx.SKILLS_SRC.exists():
        for entry in ctx.SKILLS_SRC.iterdir():
            if not entry.is_dir():
                continue
            valid_names.add(entry.name)
            for host, host_rel in hosts:
                link = host / entry.name
                rel = f"{host_rel}/{entry.name}"
                if link.is_symlink() and link.resolve() == entry.resolve():
                    out.append({"name": entry.name, "rel": rel, "state": "ok"})
                elif link.exists() and not link.is_symlink():
                    out.append({"name": entry.name, "rel": rel, "state": "convert"})
                else:
                    out.append({"name": entry.name, "rel": rel, "state": "repair"})
    for host, host_rel in hosts:
        if not host.exists():
            continue
        for entry in sorted(host.iterdir()):
            if not entry.is_symlink() or entry.name in valid_names:
                continue
            try:
                target_resolved = entry.resolve(strict=False)
                skills_src_resolved = ctx.SKILLS_SRC.resolve()
                is_managed = (
                    target_resolved != skills_src_resolved
                    and target_resolved.is_relative_to(skills_src_resolved)
                )
            except (OSError, ValueError):
                is_managed = False
            if is_managed:
                out.append({
                    "name": entry.name,
                    "rel": f"{host_rel}/{entry.name}",
                    "state": "cleanup",
                })
    return out


@consumer_locked
def cmd_link_skills(args: argparse.Namespace) -> int:
    changed: set[Path] = set()
    transaction = FileTransaction(ctx.CONSUMER, ".scratch/repo_sync_rollback")
    paths = [".claude/skills", ".agents/skills", ".opencode/commands"]
    try:
        transaction.begin(paths)
    except ValueError as error:
        raise ctx.SyncError(str(error)) from error
    try:
        reports = repair_symlinks(changed)
        reports += sync_opencode_commands(changed)
        transaction.applied()
        transaction.accept()
    except (OSError, ValueError, ctx.SyncError, subprocess.SubprocessError) as error:
        rollback_ok = True
        try:
            transaction.rollback(automatic=True)
        except Exception as rollback_error:
            rollback_ok = False
            print(
                f"WARNING: link-skills 回滚失败（{rollback_error}），"
                "备份保留在 .scratch/repo_sync_rollback",
                file=sys.stderr,
            )
        suffix = "已回滚" if rollback_ok else "回滚未完成，请人工恢复"
        raise ctx.SyncError(f"link-skills 失败（{error}）；{suffix}") from error
    for r in reports:
        print(f"提示: {r}")
    if not changed:
        print("软链均已正确")
    for path in sorted(changed, key=str):
        print(f"已修: {ctx._rel(path)}")
    return 0
