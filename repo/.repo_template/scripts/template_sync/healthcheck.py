"""只读健康检查：安装入口、hooks、来源、验证策略与占位符。"""

from __future__ import annotations

import shutil
from pathlib import Path

from repo_task.validation import ValidationError, load_policy

from . import context as ctx
from .state import read_state


def _check_hook_snippet(sync: ctx.SyncContext) -> str:
    husky = sync.consumer / ".husky/pre-commit"
    if husky.is_file() and ".repo_template/hooks/pre-commit" in husky.read_text(encoding="utf-8"):
        return ""
    lefthook = sync.consumer / "lefthook.yml"
    if lefthook.is_file() and ".repo_template/hooks/pre-commit" in lefthook.read_text(encoding="utf-8"):
        return ""
    return "hooks 未接入：在 .husky/pre-commit 或 lefthook.yml 调用 python3 .repo_template/hooks/pre-commit"


def run_healthcheck(sync: ctx.SyncContext) -> list[str]:
    issues: list[str] = []
    hooks = sync.consumer / ".repo_template/hooks/pre-commit"
    if not hooks.is_file():
        issues.append("缺 .repo_template/hooks/pre-commit")
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
            issues.append(_check_hook_snippet(sync))
    state = read_state(sync)
    if not state:
        issues.append("未初始化：缺 sync_state.json；运行 repo_sync.py init --source <path|url>")
    for rel in (".agents/skills", ".claude/skills"):
        if not (sync.consumer / rel).is_dir():
            issues.append(f"缺 {rel}；运行 repo_sync.py link-skills")
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
