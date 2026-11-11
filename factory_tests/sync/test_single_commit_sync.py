"""迁移与同步共用事务：失败/拒绝恢复，批准后单 commit。"""

import argparse
import json
from pathlib import Path

import pytest

from git_repo_support import install_fake_md_kx
from repo_task.context import common_state_dir
from repo_task.file_transaction import SYNC_TRANSACTION_DIR, state_transaction
from template_sync import context as ctx
from template_sync.apply import cmd_apply
from template_sync.migrations import MIGRATIONS, Migration
from template_sync.preflight import sync_preflight
from template_sync.transaction import cmd_transaction
from test_repo_sync import env, _git, _commit_consumer_baseline, _install_current_workflow_templates
from test_sync_preflight import _assets


def _prepare(env, tmp_path, monkeypatch):
    install_fake_md_kx(tmp_path, monkeypatch)
    _commit_consumer_baseline(env['consumer'])
    return argparse.Namespace(sync=env['sync'], skip_tests=True, decisions={})


def _legacy(env):
    root = env['consumer']
    for rel, content in {
        'docs/pending.md': '# Pending\n\n## p001 legacy\n\n原始内容。\n',
        'docs/findings.md': '# Findings\n\n## d001 legacy\n\n原始结论。\n',
        'docs/blueprint/decisions.md': '# ADR\n\n## 001 SQLite（2026-01-02）\n\n原始决策。\n',
        'docs/specs/feature.md': '# Feature\n\n原始需求。\n',
        'docs/blueprint/conventions.md': '### 原子写\n- from task import _atomic_write_text 旧写法\n',
        'docs/runtime/dispatch_ledger.jsonl': '{"event":"old"}\n',
        'docs/archive/spikes/s001_old/report.md': '# Spike\n\n## 结论\n\n采纳 d001。\n',
    }.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    legacy = env['sync'].legacy_state_path()
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"user_prompts":[{"text":"keep"}]}')
    for name in ('review_20260101', 'review_20260102'):
        path = root / 'docs/archive/reviews' / name / 'review.md'
        path.parent.mkdir(parents=True)
        path.write_text('PASS\n')
    # 规范 workflow 在预检中按转换后的 progress 验证。
    _install_current_workflow_templates(env['src'])
    directory = root / 'docs/archive/tasks/t001_old'
    directory.mkdir(parents=True)
    (directory / 'spec.md').write_text('# Task\n')
    (directory / 'task.md').write_text('---\ntid: t001\nslug: old\nstatus: done\n---\n# Task 过程总账\n')
    guard = root / '.claude/hooks/merge_guard.py'
    guard.parent.mkdir(parents=True)
    guard.symlink_to('../../.repo_template/hooks/merge_guard.py')
    _git(env['src'], 'add', '-A')
    _git(env['src'], 'commit', '-m', 'workflow templates')
    _git(root, 'add', '-A')
    _git(root, 'commit', '-m', 'legacy layout')


def test_apply_and_rejection_restore_entire_baseline(env, tmp_path, monkeypatch):
    args = _prepare(env, tmp_path, monkeypatch)
    _legacy(env)
    before = _assets(env['consumer'])
    state_before = env['state_file'].read_bytes()
    head = _git(env['consumer'], 'rev-parse', 'HEAD').stdout
    assert cmd_apply(args) == 0
    assert _git(env['consumer'], 'rev-parse', 'HEAD').stdout == head
    tx = state_transaction(env['consumer'], SYNC_TRANSACTION_DIR)
    assert tx.read()['status'] == 'applied'
    assert (common_state_dir(env['consumer']) / 'ledger.jsonl').read_bytes() == b'{"event":"old"}\n'
    tx.rollback()
    assert _assets(env['consumer']) == before
    assert env['state_file'].read_bytes() == state_before
    assert not (common_state_dir(env['consumer']) / 'ledger.jsonl').exists()
    assert not _git(env['consumer'], 'status', '--porcelain').stdout


@pytest.mark.parametrize('phase', [migration.kind for migration in MIGRATIONS] + ['sync', 'format', 'validate'])
def test_failure_anywhere_rolls_back_all_types(env, tmp_path, monkeypatch, phase):
    args = _prepare(env, tmp_path, monkeypatch)
    _legacy(env)
    # agents-zones/state-root 的注入同样必须在有效待办中运行。
    from template_sync import apply as module
    original = Migration.execute
    def execute(self, sync, src):
        reports = original(self, sync, src)
        if self.kind == phase:
            raise OSError('injected failure')
        return reports
    monkeypatch.setattr(Migration, 'execute', execute)
    if phase in {'sync', 'format', 'validate'}:
        name = {'sync': '_apply_sync_write', 'format': '_format_markdown', 'validate': '_validate_result'}[phase]
        original_phase = getattr(module, name)
        def fail(*args, **kwargs):
            original_phase(*args, **kwargs)
            raise OSError('injected failure')
        monkeypatch.setattr(module, name, fail)
    if phase == 'agents-zones':
        # 无旧 AGENTS，使用 execute 注入额外声明而不引入无关历史数据。
        monkeypatch.setattr(module, '_preflight', lambda src, sync: list(MIGRATIONS))
    before = _assets(env['consumer'])
    state_before = env['state_file'].read_bytes()
    assert cmd_apply(args) == 1
    assert _assets(env['consumer']) == before
    assert env['state_file'].read_bytes() == state_before
    assert not (common_state_dir(env['consumer']) / 'ledger.jsonl').exists()
    assert state_transaction(env['consumer'], SYNC_TRANSACTION_DIR).read()['status'] == 'rolled_back'


@pytest.mark.parametrize('dirty', ['tracked', 'staged', 'untracked'])
def test_dirty_workspace_is_zero_write_blocker(env, tmp_path, monkeypatch, dirty, capsys):
    args = _prepare(env, tmp_path, monkeypatch)
    path = env['consumer'] / ('README.md' if dirty == 'untracked' else '.repo_template/entity_schema.json')
    path.write_text('dirty\n')
    if dirty == 'staged':
        _git(env['consumer'], 'add', '-A')
    before = _assets(env['consumer'])
    assert cmd_apply(args) == 1
    assert '完全干净' in capsys.readouterr().err
    assert _assets(env['consumer']) == before
    assert state_transaction(env['consumer'], SYNC_TRANSACTION_DIR).read() is None


def test_data_blocker_does_not_start_transaction(env, tmp_path, monkeypatch, capsys):
    args = _prepare(env, tmp_path, monkeypatch)
    _legacy(env)
    path = env['consumer'] / 'docs/blueprint/decisions.md'
    path.write_text('# ADR\n\n## 001 A\n\n## 001 B\n')
    _git(env['consumer'], 'add', '-A')
    _git(env['consumer'], 'commit', '-m', 'duplicate ADR')
    before = _assets(env['consumer'])
    assert cmd_apply(args) == 1
    assert '编号 001 重复' in capsys.readouterr().err
    assert _assets(env['consumer']) == before
    assert state_transaction(env['consumer'], SYNC_TRANSACTION_DIR).read() is None


@pytest.mark.parametrize('rel', ['docs/archive/tasks', 'docs/archive/spikes', 'docs/archive/pending',
                                'docs/pending/todo', 'docs/pending/parked'])
def test_archive_non_entities_report_delete_and_restore(env, tmp_path, monkeypatch, rel, capsys):
    args = _prepare(env, tmp_path, monkeypatch)
    directory = env['consumer'] / rel
    directory.mkdir(parents=True)
    for name in ('README.md', 'binary.bin', '.gitkeep', '.DS_Store', 'p1_x.md'):
        (directory / name).write_bytes(b'raw\x00\xff')
    (directory / 't13_x/nested').mkdir(parents=True)
    (directory / 't13_x/nested/note.bin').write_bytes(b'nested\x00')
    (directory / 'link').symlink_to(tmp_path / 'absent')
    _git(env['consumer'], 'add', '-A')
    _git(env['consumer'], 'commit', '-m', 'archive non entities')
    before = _assets(env['consumer'])
    assert not sync_preflight(env['src'], env['sync']).error
    assert cmd_apply(args) == 0
    output = capsys.readouterr().out
    assert output.count('删除旧归档目录非实体项：') == 7
    assert f'{rel}/t13_x/nested/note.bin' in output
    assert not directory.exists()
    state_transaction(env['consumer'], SYNC_TRANSACTION_DIR).rollback()
    assert _assets(env['consumer']) == before


def test_accept_requires_single_commit_and_unchanged_common_state(env, tmp_path, monkeypatch):
    args = _prepare(env, tmp_path, monkeypatch)
    _legacy(env)
    assert cmd_apply(args) == 0
    from template_sync import transaction as module
    monkeypatch.setattr(module, '_run_tests', lambda root: True)
    action = argparse.Namespace(sync=env['sync'], action='accept')
    with pytest.raises(ctx.SyncError, match='须提交'):
        cmd_transaction(action)
    _git(env['consumer'], 'add', '-A')
    _git(env['consumer'], 'commit', '-m', 'sync')
    tx = state_transaction(env['consumer'], SYNC_TRANSACTION_DIR)
    with pytest.raises(ValueError, match='已提交'):
        tx.rollback(confirm_restore=True)
    baseline = env['state_file'].read_bytes()
    env['state_file'].write_bytes(b'{}')
    with pytest.raises(ctx.SyncError, match='common 状态发生变化'):
        cmd_transaction(action)
    env['state_file'].write_bytes(baseline)
    assert cmd_transaction(action) == 0
    assert tx.read()['status'] == 'accepted'


@pytest.mark.parametrize('error_type', [KeyboardInterrupt, SystemExit])
def test_catchable_interrupt_restores_everything(env, tmp_path, monkeypatch, error_type):
    args = _prepare(env, tmp_path, monkeypatch)
    _legacy(env)
    before = _assets(env['consumer'])
    state_before = env['state_file'].read_bytes()
    def interrupt(*args, **kwargs):
        raise error_type('interrupted')
    monkeypatch.setattr('template_sync.apply._format_markdown', interrupt)
    with pytest.raises(error_type):
        cmd_apply(args)
    assert _assets(env['consumer']) == before
    assert env['state_file'].read_bytes() == state_before
    assert state_transaction(env['consumer'], SYNC_TRANSACTION_DIR).read()['status'] == 'rolled_back'


@pytest.mark.parametrize('inside_consumer', [False, True])
def test_legacy_state_symlink_does_not_escape_transaction(env, tmp_path, monkeypatch, inside_consumer):
    args = _prepare(env, tmp_path, monkeypatch)
    external = (env['consumer'] if inside_consumer else tmp_path) / 'private_state'
    external.mkdir()
    target = external / 'sync_state.json'
    target.write_text('{"user_prompts":[]}')
    parent = env['sync'].legacy_state_path().parent
    parent.parent.mkdir(parents=True)
    parent.symlink_to(external, target_is_directory=True)
    _git(env['consumer'], 'add', '-A')
    _git(env['consumer'], 'commit', '-m', 'legacy state symlink')
    before = target.read_bytes()
    assert cmd_apply(args) == 1
    assert target.read_bytes() == before
    assert state_transaction(env['consumer'], SYNC_TRANSACTION_DIR).read() is None


@pytest.mark.parametrize('rel,is_directory', [('docs/tasks/t13_x', True), ('docs/pending/p1_x.md', False),
                                            ('docs/spikes/s1_x', True), ('docs/spikes/s1_x.md', False)])
def test_invalid_current_entity_names_are_preflight_blockers(env, tmp_path, monkeypatch, rel, is_directory):
    args = _prepare(env, tmp_path, monkeypatch)
    path = env['consumer'] / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if is_directory:
        path.mkdir()
        (path / 'payload.txt').write_text('invalid current entity')
    else:
        path.write_text('invalid current entity')
    _git(env['consumer'], 'add', '-A')
    _git(env['consumer'], 'commit', '-m', 'invalid current entity')
    before = _assets(env['consumer'])
    assert cmd_apply(args) == 1
    assert _assets(env['consumer']) == before
    assert state_transaction(env['consumer'], SYNC_TRANSACTION_DIR).read() is None


def test_non_task_worktree_blocks_preflight_and_apply(env, tmp_path, monkeypatch, capsys):
    args = _prepare(env, tmp_path, monkeypatch)
    linked = tmp_path / "linked"
    _git(env["consumer"], "worktree", "add", "-b", "feature", str(linked))
    result = sync_preflight(env["src"], env["sync"])
    assert result.error and "worktree" in result.error
    assert cmd_apply(args) == 1
    assert "worktree" in capsys.readouterr().err
    assert state_transaction(env["consumer"], SYNC_TRANSACTION_DIR).read() is None


def test_accept_rejects_commit_outside_transaction_scope(env, tmp_path, monkeypatch):
    args = _prepare(env, tmp_path, monkeypatch)
    assert cmd_apply(args) == 0
    (env["consumer"] / "notes.txt").write_text("outside\n", encoding="utf-8")
    _git(env["consumer"], "add", "-A")
    _git(env["consumer"], "commit", "-m", "sync plus unrelated")
    monkeypatch.setattr("template_sync.transaction._run_tests", lambda root: True)
    with pytest.raises(ctx.SyncError, match="事务范围外"):
        cmd_transaction(argparse.Namespace(sync=env["sync"], action="accept"))
    assert state_transaction(env["consumer"], SYNC_TRANSACTION_DIR).read()["status"] == "applied"


def test_legacy_archive_symlink_is_preflight_blocker(env, tmp_path, monkeypatch, capsys):
    args = _prepare(env, tmp_path, monkeypatch)
    external = tmp_path / "external"
    external.mkdir()
    link = env["consumer"] / "docs/archive/tasks"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(external, target_is_directory=True)
    _git(env["consumer"], "add", "-A")
    _git(env["consumer"], "commit", "-m", "archive symlink")
    result = sync_preflight(env["src"], env["sync"])
    assert result.error and "软链目录" in result.error
    assert cmd_apply(args) == 1
    assert "软链目录" in capsys.readouterr().err
    assert state_transaction(env["consumer"], SYNC_TRANSACTION_DIR).read() is None
