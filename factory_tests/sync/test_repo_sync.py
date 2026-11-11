"""repo_sync 测试的共享 helper 与 ``env`` fixture 模块。

用例已按被测主题拆分到 ``test_repo_sync_*.py``；本模块只保留其它测试
import 的 ``env`` / ``_git`` 等符号，避免调用方感知拆分。
"""

import contextlib
import json
import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

from factory_paths import FACTORY_ROOT
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = FACTORY_ROOT / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from template_sync import context as ctx
from template_sync.agents_merge import _apply_agents_sectioned, agents_section_status
from template_sync.apply import _assert_distributable_tree, cmd_apply
from template_sync.assets import retired_template_warnings, sync_dir, sync_file
from template_sync.cli import _parse_decisions, main as repo_sync_main
from template_sync.common import _detect_json_indent, _git as sync_git
from template_sync.conventions_migration import conventions_migration_status, migrate_conventions
from template_sync.gates import _run_tests
from template_sync.hooks import (
    _RETIRED_MERGE_GUARD_COMMAND,
    cmd_install_hooks,
    migrate_retired_hooks,
)
from template_sync.shared_files import (
    _PRETTIERIGNORE_HEADER,
    _PRETTIERIGNORE_OLD_HEADER,
    _sanitize_mcp_servers,
    merge_gitignore,
    merge_mcp,
    merge_prettierignore,
)
from template_sync.skills_links import (
    cmd_link_skills,
    opencode_commands_status,
    repair_symlinks,
    skill_status,
    symlink_status,
    sync_opencode_commands,
)
from template_sync.source import assert_not_self, resolve_src, source_identity
from template_sync.state import (
    active_prompts,
    cmd_init,
    cmd_prompt,
    prompt_substrings,
    read_state,
    write_state,
)
from template_sync.status_plan import cmd_plan, cmd_status
from template_sync.workflow_migration import (
    _GUIDE_CLOSE,
    _GUIDE_OPEN,
    _guide_blocks_by_heading,
    _implementation_guidance,
    _migrate_task_text,
    _rename_legacy_title,
    _replace_guide_blocks,
    force_migrate_workflow_tasks,
    workflow_migration_status,
)
from export_template import export_template
from review_support import configure_project_verification


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=check,
    )


from git_repo_support import copy_seed_repo, state_file as repo_state_file, state_dir as repo_state_dir


@pytest.fixture
def env(tmp_path: Path, sync_source_seed: Path) -> dict:
    src = tmp_path / "src"
    copy_seed_repo(sync_source_seed, src)
    # 工厂种子只含 skills/task-run 目录；真实产物必带 registry.json。
    # fail-closed 语义下缺 registry 即阻断命令薄层，故在 fixture 内补登记并提交，
    # 使 apply/status/plan 用例聚焦自身语义而非种子缺件。
    (src / ".repo_template/skills/registry.json").write_text(json.dumps({
        "skills": {"task-run": {"role": "public"}},
    }), encoding="utf-8")
    _git(src, "add", "-A")
    _git(src, "commit", "-m", "register public skills")
    head = _git(src, "rev-parse", "HEAD").stdout.strip()

    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _git(consumer, "init", "-b", "main")
    configure_project_verification(consumer)
    state_file = repo_state_file(consumer, "sync_state.json")
    state_file.write_text(json.dumps({
        "template_source": {"kind": "path", "value": str(src)},
        "last_synced_commit": None,
        "last_synced_at": None,
        "user_prompts": [],
    }, indent=2))

    (consumer / ".repo_template").mkdir(parents=True, exist_ok=True)
    (consumer / ".repo_template/entity_schema.json").write_text('{"version": 1}\n')
    _commit_consumer_baseline(consumer)
    sync = ctx.SyncContext.for_consumer(consumer)
    return {"src": src, "consumer": consumer, "head": head, "state_file": state_file, "sync": sync}


def _add_prompt(sync: ctx.SyncContext, text: str, tags: str = "") -> None:
    cmd_prompt(Namespace(sync=sync, action="add", text=text, tags=tags, id=None))


# ---------------------------------------------------------------------------
# state 字段级原子更新
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# user_prompts 管理
# ---------------------------------------------------------------------------











# ---------------------------------------------------------------------------
# 硬同步覆盖与多余删除
# ---------------------------------------------------------------------------








# ---------------------------------------------------------------------------
# 软链
# ---------------------------------------------------------------------------











def _write_public_registry(consumer: Path, *names: str) -> None:
    """registry 缺失即 fail-closed（DS L245）：命令薄层用例须显式登记 public skill。"""
    (consumer / ".repo_template/skills/registry.json").write_text(
        json.dumps({"skills": {name: {"role": "public"} for name in names}}),
        encoding="utf-8",
    )







































# ---------------------------------------------------------------------------
# AGENTS.md 两区协议

CONSUMER_AGENTS = (
    "消费仓项目介绍与项目规则。\n\n"
    "<!-- repo-template:begin -->\n旧模板区\n<!-- repo-template:end -->\n"
)








# apply 全流程 + 改动清单
# ---------------------------------------------------------------------------

















# ---------------------------------------------------------------------------
# 边界：同一性拒绝 / init
# ---------------------------------------------------------------------------



























# ---------------------------------------------------------------------------
# install-hooks：core.hooksPath 幂等设置
# 旧测试 test_install_hooks_overwrites_stale_path 已删：一律覆盖会摘掉
# husky/lefthook；现语义见 refuses_foreign_path / force_overwrites。
# ---------------------------------------------------------------------------

def _ensure_hook(consumer: Path, *, executable: bool = True) -> Path:
    hook = consumer / ".repo_template/hooks/pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    hook.chmod(0o755 if executable else 0o644)
    return hook














# ---------------------------------------------------------------------------
# F16：apply 中途失败回滚（目录删除 / skill 覆盖须恢复内容）
# ---------------------------------------------------------------------------













def _install_current_workflow_templates(consumer: Path) -> tuple[str, str]:
    source = SCRIPTS_DIR.parent / 'docs/task_template'
    target = consumer / '.repo_template/docs/task_template'
    target.mkdir(parents=True, exist_ok=True)
    task = (source / 'task.md').read_text()
    note = (source / 'progress.md').read_text()
    (target / 'task.md').write_text(task)
    (target / 'progress.md').write_text(note)
    return task, note


def _bind_repo_task_context(monkeypatch: pytest.MonkeyPatch, consumer: Path) -> None:
    del monkeypatch, consumer


def _old_task_documents(spec: str, task: str) -> tuple[str, str]:
    old_spec = spec.replace(
        '只写可观察、可独立验证的行为；每条使用稳定且不复用的 `AC-NNN`。需真实部署或人工环境验证时在编号前加 `[deploy]`。创建时均为未勾选（`- [ ]`）；实施验证通过后由实施 Agent 就地打勾（`- [x]`）；由 Review Agent 独立复核打勾真实性。技术选型不作为行为 AC。',
        '旧版规范文字，只写可观察行为和 AC-NNN。',
    )
    old_task = task.replace('status: backlog', 'status: blocked', 1)
    old_task = old_task.replace('branch: ""\n', 'branch: ""\nschedule_status: scheduled\n', 1)
    old_task = old_task.replace('review_limit: 5\n', '').replace('verify_limit: 5\n', '')
    old_task = old_task.replace(
        '执行期记录关键步骤、决策、验证、阻塞和用户批准的新轮次上限。',
        '执行期边做边写：旧版固定说明。',
    ).replace(
        '创建期不预测实施步骤。只记有追溯价值的内容；无事项时写“无”。',
        '创建期不预测实施步骤——旧版固定说明。',
    )
    return old_spec, old_task






























def _commit_consumer_baseline(consumer):
    _git(consumer, 'config', 'user.email', 'test@example.com')
    _git(consumer, 'config', 'user.name', 'test')
    _git(consumer, 'add', '-A')
    _git(consumer, 'commit', '--allow-empty', '-m', 'baseline')


def _task_branch(consumer, tid, slug, status, *, directory='docs/tasks'):
    """建 task 分支：progress.md 状态为 status，目录位于 directory；回到 main。"""
    branch = f'{tid}_{slug}'
    _git(consumer, 'checkout', '-b', branch)
    task_dir = consumer / directory / branch
    task_dir.mkdir(parents=True)
    (task_dir / 'progress.md').write_text(
        f'---\ntid: "{tid}"\nslug: "{slug}"\nstatus: "{status}"\n---\n正文\n', encoding='utf-8',
    )
    _git(consumer, 'add', '-A')
    _git(consumer, 'commit', '-m', f'{branch} {status}')
    _git(consumer, 'checkout', 'main')
    return branch








