"""repo_sync workflow / conventions 迁移。

从 test_repo_sync 拆分；fixture 与 helper 仍由 test_repo_sync 提供。"""

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
from template_sync.agents_merge import _apply_agents_sectioned, _replace_agents_force, agents_section_status
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


from test_repo_sync import (
    CONSUMER_AGENTS, _add_prompt, _bind_repo_task_context, _commit_consumer_baseline,
    _ensure_hook, _git, _install_current_workflow_templates, _old_task_documents,
    _task_branch, _write_public_registry, env,
)


def test_guide_blocks_ignore_fenced_headings_and_blocks():
    text = (
        "### 范围\n\n"
        "```markdown\n### 验收标准\n"
        f"{_GUIDE_OPEN}\n伪规范\n{_GUIDE_CLOSE}\n"
        "```\n\n"
        f"{_GUIDE_OPEN}\n真规范\n{_GUIDE_CLOSE}\n"
    )
    blocks = _guide_blocks_by_heading(text)
    assert set(blocks) == {"范围"}
    assert len(blocks["范围"]) == 1
    assert "真规范" in blocks["范围"][0]
    assert "伪规范" not in blocks["范围"][0]

def test_replace_guide_blocks_ignores_fenced_heading():
    current = f"### 验收标准\n\n{_GUIDE_OPEN}\n现行规范\n{_GUIDE_CLOSE}\n"
    old = "### 范围\n\n```markdown\n### 验收标准\n旧代码\n```\n\n保留内容\n"
    assert _replace_guide_blocks(old, current) == old

def test_rename_legacy_title_tracks_mixed_and_longer_fences():
    mixed = [
        "# Task spec", "```markdown", "~~~",
        "# Task 过程总账", "~~~", "```", "# Task 过程总账",
    ]
    _rename_legacy_title(mixed, 0)
    assert mixed[3] == "# Task 过程总账"
    assert mixed[6] == "# Task 进展"

    longer = [
        "````markdown", "```", "# Task 过程总账", "````", "# Task 过程总账",
    ]
    _rename_legacy_title(longer, 0)
    assert longer[2] == "# Task 过程总账"
    assert longer[4] == "# Task 进展"

def test_legacy_full_spec_schema_migrates_as_heading_groups(env):
    current_spec, _ = _install_current_workflow_templates(env['consumer'])
    legacy = (Path(__file__).parent / 'fixtures/spec_pre_workflow_simplification.md').read_text()
    migrated = _replace_guide_blocks(legacy, current_spec)
    current_blocks = _guide_blocks_by_heading(current_spec)
    migrated_blocks = _guide_blocks_by_heading(migrated)
    assert migrated_blocks == current_blocks
    assert _replace_guide_blocks(migrated, current_spec) == migrated
    assert '已判定不写测试的分支与原因' not in migrated
    assert 'mock 边界、fixture 来源、断言目标' not in migrated

def test_missing_guide_block_is_restored_and_reported(env):
    consumer = env['consumer']
    current_spec, current_task = _install_current_workflow_templates(consumer)
    _install_current_workflow_templates(env['src'])
    missing = current_spec.replace(
        _guide_blocks_by_heading(current_spec)['验收标准'][0] + '\n\n', '', 1
    )
    task_dir = consumer / 'docs/tasks/t001_missing'
    task_dir.mkdir(parents=True)
    (task_dir / 'task.md').write_text(missing)
    (task_dir / 'progress.md').write_text(current_task)
    migrations, worktrees, errors = workflow_migration_status(env['src'], sync=env["sync"])
    assert 'docs/tasks/t001_missing/task.md' in migrations
    assert worktrees == [] and errors == []
    force_migrate_workflow_tasks(set(), sync=env["sync"])
    assert (task_dir / 'task.md').read_text() == current_spec

def test_migration_status_reports_malformed_spec_without_raising(env):
    consumer = env['consumer']
    current_spec, current_task = _install_current_workflow_templates(consumer)
    _install_current_workflow_templates(env['src'])
    malformed = current_spec.replace(_GUIDE_CLOSE, '', 1)
    task_dir = consumer / 'docs/tasks/t001_malformed'
    task_dir.mkdir(parents=True)
    (task_dir / 'task.md').write_text(malformed)
    (task_dir / 'progress.md').write_text(current_task)
    migrations, worktrees, errors = workflow_migration_status(env['src'], sync=env["sync"])
    assert migrations == [] and worktrees == []
    assert len(errors) == 1 and 't001_malformed/task.md' in errors[0]
    assert cmd_status(Namespace(sync=env["sync"])) == 0
    assert cmd_plan(Namespace(sync=env["sync"])) == 0
    with pytest.raises(ctx.SyncError, match='无法安全迁移'):
        force_migrate_workflow_tasks(set(), sync=env["sync"])

def test_workflow_migrations_are_idempotent_for_current_schema(env):
    current_spec, current_task = _install_current_workflow_templates(env['consumer'])
    assert _replace_guide_blocks(current_spec, current_spec) == current_spec
    assert _migrate_task_text(current_task, current_task) == current_task

@pytest.mark.parametrize('parent', ['docs/tasks', 'docs/archive/tasks'])
@pytest.mark.parametrize('layout', ['spec_task', 'task_note'])
def test_progress_migration_preserves_task_history(env, parent, layout):
    consumer = env['consumer']
    spec, progress = _install_current_workflow_templates(consumer)
    _install_current_workflow_templates(env['src'])
    task_dir = consumer / parent / 't001_history'
    task_dir.mkdir(parents=True)
    old = progress.replace('# Task 进展', '# Task 过程总账').replace(
        'note: ""', 'note: "保留审计说明"',
    ) + '\n```markdown\n# Task 过程总账\n```\n\n保留实施证据。\n'
    spec_name, progress_name = ('spec.md', 'task.md') if layout == 'spec_task' else ('task.md', 'note.md')
    (task_dir / spec_name).write_text(spec)
    (task_dir / progress_name).write_text(old)
    (task_dir / 'review_general.md').write_text('历史 review，不补 TICK 标签。\n')
    (task_dir / 'handoff.json').write_text('{"history": true}\n')
    migrations, worktrees, errors = workflow_migration_status(env['src'], sync=env["sync"])
    assert str((task_dir / progress_name).relative_to(consumer)) in migrations
    assert worktrees == [] and errors == []

    changed = set()
    force_migrate_workflow_tasks(changed, sync=env["sync"])

    assert (task_dir / 'task.md').read_text() == spec
    assert (task_dir / 'progress.md').read_text() == old.replace('# Task 过程总账', '# Task 进展', 1)
    assert not (task_dir / 'note.md').exists()
    assert not (task_dir / 'spec.md').exists()
    assert (task_dir / 'review_general.md').read_text() == '历史 review，不补 TICK 标签。\n'
    assert (task_dir / 'handoff.json').read_text() == '{"history": true}\n'
    assert task_dir / 'progress.md' in changed
    assert task_dir / progress_name in changed
    assert force_migrate_workflow_tasks(set(), sync=env["sync"]) == []

@pytest.mark.parametrize('parent', ['docs/tasks', 'docs/archive/tasks'])
def test_progress_migration_rejects_duplicate_names_before_any_writes(env, parent):
    consumer = env['consumer']
    spec, progress = _install_current_workflow_templates(consumer)
    _install_current_workflow_templates(env['src'])
    for slug in ('a_ready', 'z_conflict'):
        directory = consumer / parent / f't001_{slug}'
        directory.mkdir(parents=True)
        (directory / 'task.md').write_text(spec)
        (directory / 'note.md').write_text(progress.replace('# Task 进展', '# Task 过程总账'))
    (directory / 'progress.md').write_text(progress)
    before = {path: path.read_bytes() for path in (consumer / parent).rglob('*') if path.is_file()}

    _, _, errors = workflow_migration_status(env['src'], sync=env["sync"])
    assert any('note.md' in error and 'progress.md' in error for error in errors)
    changed = set()
    with pytest.raises(ctx.SyncError, match='note.md.*progress.md'):
        force_migrate_workflow_tasks(changed, sync=env["sync"])
    assert changed == set()
    assert {path: path.read_bytes() for path in (consumer / parent).rglob('*') if path.is_file()} == before

def test_migrate_all_failure_keeps_completed_steps_and_blocks_only_failed_kind(env, monkeypatch):
    """A10（逐项事务语义）：显式 migrate all 中途失败时，已完成步骤保留、仅失败步骤未写入；
    旧布局与历史文件原样保留，不存在跨类型全局回滚。"""
    consumer = env['consumer']
    _bind_repo_task_context(monkeypatch, consumer)
    spec, progress = _install_current_workflow_templates(env['src'])
    task_dir = consumer / 'docs/archive/tasks/t001_history'
    task_dir.mkdir(parents=True)
    expected_progress = (
        progress.replace('tid: t000', 'tid: t001')
        .replace('slug: example_slug', 'slug: history')
        .replace('status: backlog', 'status: done')
    )
    old = expected_progress.replace('# Task 进展', '# Task 过程总账')
    (task_dir / 'task.md').write_text(spec)
    (task_dir / 'note.md').write_text(old)
    state_before = env['state_file'].read_bytes()

    from template_sync import migrate as migrate_mod

    conv_file = consumer / "docs/blueprint/conventions.md"
    conv_file.parent.mkdir(parents=True, exist_ok=True)
    conv_file.write_text(
        "### 原子写\n- from task import _atomic_write_text 旧写法\n", encoding="utf-8"
    )

    def boom(*_args, **_kwargs):
        raise ctx.SyncError("模拟 conventions 迁移失败")

    monkeypatch.setitem(migrate_mod._STEPS, "conventions", boom)
    with pytest.raises(ctx.SyncError, match="模拟 conventions 迁移失败"):
        migrate_mod.cmd_migrate(Namespace(sync=env["sync"], kind="all", write=True))
    # 前一步 workflow 与 entity-storage 已完成；失败步骤自身未写入
    assert (consumer / "docs/tasks/t001_history/progress.md").is_file()
    assert conv_file.read_text(encoding="utf-8").endswith("旧写法\n")
    assert env["state_file"].read_bytes() == state_before

def test_force_migrate_workflow_updates_old_task_documents(env):
    consumer = env['consumer']
    current_spec, current_task = _install_current_workflow_templates(consumer)
    _install_current_workflow_templates(env['src'])
    old_spec, old_task = _old_task_documents(current_spec, current_task)

    # 1. 活跃任务：构造真实旧版文件布局 (spec.md 为需求规格, task.md 为过程总账)
    task_dir = consumer / 'docs/tasks/t001_old'
    task_dir.mkdir(parents=True)
    active_old_task = (
        old_task.replace('tid: t000', 'tid: t001')
        .replace('slug: example_slug', 'slug: old')
    )
    (task_dir / 'spec.md').write_text(old_spec, encoding="utf-8")
    (task_dir / 'task.md').write_text(active_old_task, encoding="utf-8")

    # 2. 归档任务：构造真实旧版文件布局 (spec.md + task.md)
    archive_dir = consumer / 'docs/archive/tasks/t002_archived_old'
    archive_dir.mkdir(parents=True)
    archived_old_task = (
        old_task.replace('tid: t000', 'tid: t002')
        .replace('slug: example_slug', 'slug: archived_old')
        .replace('status: blocked', 'status: done')
    )
    (archive_dir / 'spec.md').write_text(old_spec, encoding="utf-8")
    (archive_dir / 'task.md').write_text(archived_old_task, encoding="utf-8")

    # 迁移前：store.py 扫描会因为缺少 progress.md 拒绝扫描
    from repo_task import store as task_store, context as task_ctx
    with pytest.raises(task_ctx.TaskDataError, match="缺 progress.md"):
        task_store._scan_tasks_in_directory(consumer / 'docs/tasks')

    # 迁移状态应检出活跃和归档下的旧文件
    migrations, worktrees, errors = workflow_migration_status(env['src'], sync=env["sync"])
    assert 'docs/tasks/t001_old/spec.md' in migrations
    assert 'docs/tasks/t001_old/task.md' in migrations
    assert 'docs/archive/tasks/t002_archived_old/spec.md' in migrations
    assert 'docs/archive/tasks/t002_archived_old/task.md' in migrations
    assert worktrees == [] and errors == []

    changed: set[Path] = set()
    reports = force_migrate_workflow_tasks(changed, sync=env["sync"])

    # 验证活跃任务已完成布局转换
    assert not (task_dir / 'spec.md').exists()
    assert (task_dir / 'task.md').is_file()
    assert (task_dir / 'progress.md').is_file()

    migrated_spec = (task_dir / 'task.md').read_text(encoding="utf-8")
    migrated_task = (task_dir / 'progress.md').read_text(encoding="utf-8")
    assert '旧版规范文字' not in migrated_spec
    for blocks in _guide_blocks_by_heading(current_spec).values():
        for block in blocks:
            assert block in migrated_spec
    assert 'status: "active"' in migrated_task
    assert 'schedule_status' not in migrated_task
    assert 'review_limit: "5"' in migrated_task
    assert 'verify_limit: "5"' in migrated_task
    for line in _implementation_guidance(current_task):
        assert line in migrated_task
    assert '旧版固定说明' not in migrated_task
    assert task_dir / 'spec.md' in changed
    assert task_dir / 'task.md' in changed
    assert task_dir / 'progress.md' in changed

    # 验证归档任务已完成布局转换
    assert not (archive_dir / 'spec.md').exists()
    assert (archive_dir / 'task.md').is_file()
    assert (archive_dir / 'progress.md').is_file()
    migrated_archive_task = (archive_dir / 'progress.md').read_text(encoding="utf-8")
    assert 'status: done' in migrated_archive_task or 'status: "done"' in migrated_archive_task
    assert archive_dir / 'spec.md' in changed
    assert archive_dir / 'task.md' in changed
    assert archive_dir / 'progress.md' in changed

    # 幂等性：迁移后再查 status 为空，再次执行不写盘
    assert _replace_guide_blocks(migrated_spec, current_spec) == migrated_spec
    assert _migrate_task_text(migrated_task, current_task) == migrated_task
    assert force_migrate_workflow_tasks(set(), sync=env["sync"]) == []
    migrations, worktrees, errors = workflow_migration_status(env['src'], sync=env["sync"])
    assert migrations == [] and worktrees == [] and errors == []

    # 迁移后：store.py 扫描正常解析任务
    tasks = task_store._scan_tasks_in_directory(consumer / 'docs/tasks')
    assert [t['tid'] for t in tasks] == ['t001']
    assert tasks[0]['status'] == 'active' and tasks[0]['dir'] == 'docs/tasks/t001_old'

def test_apply_refuses_old_workflow_layout_until_explicit_migration(env, capsys):
    """A10：apply 不再自动迁移；先 migrate workflow-schema，再 apply 成功且不生成索引。"""
    consumer = env["consumer"]
    spec, progress = _install_current_workflow_templates(env["src"])

    task_dir = consumer / "docs/tasks/t001_old"
    task_dir.mkdir(parents=True)
    (task_dir / "task.md").write_text(spec, encoding="utf-8")
    old_note = (
        progress.replace("tid: t000", "tid: t001")
        .replace("slug: example_slug", "slug: old")
        .replace("status: backlog", "status: blocked")
        .replace("# Task 进展", "# Task 过程总账")
    )
    (task_dir / "note.md").write_text(old_note, encoding="utf-8")

    assert cmd_apply(Namespace(sync=env["sync"], 
        allow_dirty_source=True, skip_tests=True, decisions={},
    )) == 1
    err = capsys.readouterr().err
    assert "migrate all" in err
    assert (task_dir / "note.md").is_file()

    from template_sync.migrate import cmd_migrate

    # dry-run: 默认不落盘
    assert cmd_migrate(Namespace(sync=env["sync"], kind="workflow-schema", write=False)) == 0
    assert (task_dir / "note.md").is_file()
    assert not (task_dir / "progress.md").exists()

    # write: 落盘迁移
    assert cmd_migrate(Namespace(sync=env["sync"], kind="workflow-schema", write=True)) == 0
    assert (task_dir / "progress.md").is_file()
    assert not (task_dir / "note.md").exists()

    assert cmd_apply(Namespace(sync=env["sync"], 
        allow_dirty_source=True, skip_tests=True, decisions={},
    )) == 0
    assert not (consumer / "docs/tasks_index.json").exists()
    assert not (consumer / "docs/archive/tasks_index.json").exists()

def test_task_migration_only_reads_schema_fields_from_frontmatter(env):
    _, current_task = _install_current_workflow_templates(env['consumer'])
    old_task = current_task.replace('review_limit: 5\n', '').replace('verify_limit: 5\n', '')
    old_task = old_task.replace(
        '无\n\n## Review 处置',
        'status: blocked\nreview_limit: 正文不是字段\nverify_limit: 正文不是字段\n\n## Review 处置',
    )
    migrated = _migrate_task_text(old_task, current_task)
    assert 'status: blocked' in migrated
    assert 'review_limit: 正文不是字段' in migrated
    assert 'verify_limit: 正文不是字段' in migrated
    frontmatter = migrated.split('---', 2)[1]
    assert 'review_limit: "5"' in frontmatter
    assert 'verify_limit: "5"' in frontmatter

def test_force_migrate_workflow_rejects_registered_task_worktree(env):
    consumer = env['consumer']
    current_spec, current_task = _install_current_workflow_templates(consumer)
    task_dir = consumer / 'docs/tasks/t001_old'
    task_dir.mkdir(parents=True)
    (task_dir / 'task.md').write_text(current_spec)
    (task_dir / 'progress.md').write_text(current_task)
    _git(consumer, 'init', '-b', 'main')
    _git(consumer, 'config', 'user.email', 'test@example.com')
    _git(consumer, 'config', 'user.name', 'test')
    _git(consumer, 'add', '-A')
    _git(consumer, 'commit', '-m', 'init')
    worktree = consumer.parent / 'consumer_t001'
    _git(consumer, 'worktree', 'add', '-b', 't001_old', str(worktree))

    with pytest.raises(ctx.SyncError, match='必须先完成或 rewind'):
        force_migrate_workflow_tasks(set(), sync=env["sync"])

@pytest.mark.parametrize('directory', ['docs/tasks', 'docs/archive/tasks'])
def test_workflow_migration_blocks_unintegrated_done_branch(env, directory):
    consumer = env['consumer']
    _install_current_workflow_templates(consumer)
    _commit_consumer_baseline(consumer)
    branch = _task_branch(consumer, 't001', 'old', 'done', directory=directory)

    _, _, errors = workflow_migration_status(env['src'], sync=env["sync"])
    assert any(branch in error and 'integrate' in error for error in errors)
    with pytest.raises(ctx.SyncError, match=f'{branch}.*integrate'):
        force_migrate_workflow_tasks(set(), sync=env["sync"])

    # 合入默认分支后放行
    _git(consumer, 'merge', '--no-ff', '-m', f'merge({branch})', branch)
    _, _, errors = workflow_migration_status(env['src'], sync=env["sync"])
    assert errors == []
    force_migrate_workflow_tasks(set(), sync=env["sync"])

@pytest.mark.parametrize('status', ['active', 'backlog'])
def test_workflow_migration_ignores_unmerged_non_done_branch(env, status):
    consumer = env['consumer']
    _install_current_workflow_templates(consumer)
    _commit_consumer_baseline(consumer)
    _task_branch(consumer, 't001', 'old', status)

    _, _, errors = workflow_migration_status(env['src'], sync=env["sync"])
    assert errors == []
    force_migrate_workflow_tasks(set(), sync=env["sync"])

def test_workflow_migration_lists_every_unintegrated_done_branch(env):
    consumer = env['consumer']
    _install_current_workflow_templates(consumer)
    _commit_consumer_baseline(consumer)
    first = _task_branch(consumer, 't001', 'old', 'done')
    second = _task_branch(consumer, 't002', 'newer', 'done')

    with pytest.raises(ctx.SyncError) as raised:
        force_migrate_workflow_tasks(set(), sync=env["sync"])
    assert first in str(raised.value) and second in str(raised.value)

def test_migrate_conventions_updates_atomic_write_preserving_custom_content(env):
    """conventions 路径由 SyncContext 派生，无模块级 CONVENTIONS_PATH 可替换。"""
    consumer = env['consumer']
    conv_file = consumer / "docs/blueprint/conventions.md"
    conv_file.parent.mkdir(parents=True, exist_ok=True)

    original_text = (
        "# 约定\n\n"
        "## 项目自定义约定\n"
        "- 保持现有 React 风格。\n\n"
        "### 原子写\n"
        "- 写权威/派生状态数据走 tmp 文件 + fsync + os.replace 原子写（.repo_template/scripts/task.py 的 _atomic_write_text），防中断半写。"
        "t063 曾实现。脚本层跨模块复用时 from task import _atomic_write_text（task.py 有 __main__ guard，import 安全）。\n"
    )
    conv_file.write_text(original_text, encoding="utf-8")

    assert conventions_migration_status(env["sync"]) == [env["sync"].rel(conv_file)]

    changed = set()
    reports = migrate_conventions(changed, env["sync"])
    assert len(reports) == 1
    assert conv_file in changed

    migrated_text = conv_file.read_text(encoding="utf-8")
    assert "保持现有 React 风格" in migrated_text
    assert "t063 曾实现" in migrated_text
    assert "from repo_task.documents import atomic_write_text" in migrated_text
    assert "_atomic_write_text" not in migrated_text
    assert "from task import" not in migrated_text

    # 幂等：再次执行无迁移
    assert conventions_migration_status(env["sync"]) == []
    assert migrate_conventions(set(), env["sync"]) == []
