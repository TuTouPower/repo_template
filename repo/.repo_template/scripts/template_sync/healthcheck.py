"""只读健康检查：安装入口、hooks、来源、验证策略与占位符。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from repo_task.validation import ValidationError, load_policy

from . import context as ctx
from .skills_links import symlink_status
from .state import read_state


REQUIRED_HOOKS = ("pre-commit", "commit-msg", "pre-merge-commit")


def _hook_entry_issue(path: Path) -> str | None:
    """单一入口判定：软链悬空 / 解析后非文件 / 不可执行，返回点名原因。"""
    if path.is_symlink() and not path.exists():
        return "软链悬空（指向不存在目标）"
    if not path.is_file():
        return "缺失或非文件"
    if not os.access(path, os.X_OK):
        return "不可执行"
    return None


def _check_required_entries(sync: ctx.SyncContext) -> list[str]:
    """必需 hook 入口 + 宿主 skill 链接有效性，全部收敛到本检查。

    复用 skills_links.symlink_status 判定每个登记链接，不再用宿主目录
    is_dir() 作为链接有效的弱代理。
    """
    issues: list[str] = []
    hooks_dir = sync.consumer / ".repo_template/hooks"
    for name in REQUIRED_HOOKS:
        problem = _hook_entry_issue(hooks_dir / name)
        if problem:
            issues.append(
                f".repo_template/hooks/{name} {problem}；"
                "运行 repo_sync.py install-hooks 重新同步模板 hooks"
            )
    for host_rel in (".agents/skills", ".claude/skills"):
        if not (sync.consumer / host_rel).is_dir():
            issues.append(f"缺 {host_rel}；运行 repo_sync.py link-skills")
    for item in symlink_status(sync):
        if item["state"] == "ok":
            continue
        issues.append(
            f"{item['rel']} skill 链接无效（{item['state']}："
            "悬空/指向仓外/类型不符）；运行 repo_sync.py link-skills"
        )
    return issues


def _hook_file_has(path: Path, needle: str) -> bool:
    return path.is_file() and needle in path.read_text(encoding="utf-8")


def _check_hook_snippet(sync: ctx.SyncContext) -> str | None:
    husky_pre = sync.consumer / ".husky/pre-commit"
    husky_msg = sync.consumer / ".husky/commit-msg"
    husky_ok = _hook_file_has(husky_pre, ".repo_template/hooks/pre-commit") and _hook_file_has(
        husky_msg, ".repo_template/hooks/commit-msg"
    )
    lefthook = sync.consumer / "lefthook.yml"
    lefthook_text = lefthook.read_text(encoding="utf-8") if lefthook.is_file() else ""
    lefthook_ok = (
        ".repo_template/hooks/pre-commit" in lefthook_text
        and ".repo_template/hooks/commit-msg" in lefthook_text
    )
    if husky_ok or lefthook_ok:
        problem = _hook_entry_issue(sync.consumer / ".repo_template/hooks/commit-msg")
        if problem:
            return (
                "hooks 接入片段齐全，但真实入口 "
                f".repo_template/hooks/commit-msg {problem}；"
                "运行 repo_sync.py install-hooks 重新同步模板 hooks"
            )
        return None
    missing = []
    if not _hook_file_has(husky_pre, ".repo_template/hooks/pre-commit"):
        missing.append(".husky/pre-commit")
    if not _hook_file_has(husky_msg, ".repo_template/hooks/commit-msg"):
        missing.append(".husky/commit-msg")
    if not lefthook_ok:
        missing.append("lefthook.yml（pre-commit 与 commit-msg）")
    return "hooks 未接入：缺少 " + "、".join(missing) + "；在项目 hook 中调用模板 pre-commit 与 commit-msg"


def run_healthcheck(sync: ctx.SyncContext) -> list[str]:
    issues = _check_required_entries(sync)
    core = shutil.which("git")
    if core:
        import subprocess

        result = subprocess.run(
            ["git", "-C", str(sync.consumer), "config", "--get", "core.hooksPath"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        configured = result.stdout.strip()
        if configured:
            if configured not in {".repo_template/hooks", ".husky/_"}:
                issues.append(f"core.hooksPath={configured}，未指向模板 hooks；需要人工接入")
            elif configured == ".husky/_":
                missing = _check_hook_snippet(sync)
                if missing:
                    issues.append(missing)
        else:
            issues.append("未设置 core.hooksPath；运行 repo_sync.py install-hooks 启用模板 hooks")
    state = read_state(sync)
    if not state:
        issues.append("未初始化：缺 sync_state.json；运行 repo_sync.py init --source <path|url>")
    if not shutil.which("md_kx"):
        issues.append("缺 md_kx；安装 md-kx 或修正 PATH")
    try:
        load_policy(sync.consumer)
    except (ValidationError, OSError, ValueError) as error:
        issues.append(f"验证策略不可用：{error}")
    template = sync.consumer / ".repo_template/docs/task_template/task.md"
    if template.is_file() and "{spec 路径" not in template.read_text(encoding="utf-8"):
        issues.append("task 模板缺“关联 spec”行")
    return issues
