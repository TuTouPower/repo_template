"""提交后取消（cancelled）与无变更（no_op）两个同步事务终态。"""

import argparse
import re

import pytest

from git_repo_support import install_fake_md_kx
from repo_task.context import common_state_dir
from repo_task.file_transaction import SYNC_TRANSACTION_DIR, open_transactions, state_transaction
from template_sync import context as ctx
from template_sync.apply import cmd_apply
from template_sync.transaction import cmd_transaction
from test_repo_sync import env, _git, _commit_consumer_baseline


def _prepare(env, tmp_path, monkeypatch):
    install_fake_md_kx(tmp_path, monkeypatch)
    _commit_consumer_baseline(env['consumer'])
    return argparse.Namespace(sync=env['sync'], skip_tests=True, decisions={})


def _apply_args(sync):
    return argparse.Namespace(sync=sync, skip_tests=True, decisions={})


def _cancel_args(sync, confirmed):
    return argparse.Namespace(sync=sync, action='cancel', confirm_post_commit=confirmed)


def _commit_and_revert(consumer):
    _git(consumer, 'add', '-A')
    _git(consumer, 'commit', '-m', 'sync')
    sync_sha = _git(consumer, 'rev-parse', 'HEAD').stdout.strip()
    _git(consumer, 'revert', '--no-edit', 'HEAD')
    assert not _git(consumer, 'status', '--porcelain').stdout.strip()
    return sync_sha


def test_cancel_post_commit_reaches_terminal_and_unblocks_next_apply(env, tmp_path, monkeypatch):
    args = _prepare(env, tmp_path, monkeypatch)
    consumer = env['consumer']
    baseline_state = env['state_file'].read_bytes()
    assert cmd_apply(args) == 0
    tx = state_transaction(consumer, SYNC_TRANSACTION_DIR)
    assert tx.read()['status'] == 'applied'
    after = tx.read()['after']
    state_after = tx.read()['state_after']

    sync_sha = _commit_and_revert(consumer)
    env['state_file'].write_bytes(baseline_state)

    with pytest.raises(ctx.SyncError, match='--confirm-post-commit'):
        cmd_transaction(_cancel_args(env['sync'], False))
    assert tx.read()['status'] == 'applied'

    assert cmd_transaction(_cancel_args(env['sync'], True)) == 0
    data = tx.read()
    assert data['status'] == 'cancelled'
    assert data['cancelled_commit'] == sync_sha
    assert re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00$', data['cancelled_at'])
    assert data['after'] == after and data['state_after'] == state_after
    assert (tx.backup / '__state__' / 'sync_state.json').exists()
    assert open_transactions(consumer) == []
    assert cmd_apply(_apply_args(env['sync'])) == 0
    assert tx.read()['status'] == 'applied'


def test_cancel_post_commit_rejects_when_common_state_not_restored(env, tmp_path, monkeypatch):
    args = _prepare(env, tmp_path, monkeypatch)
    consumer = env['consumer']
    assert cmd_apply(args) == 0
    _commit_and_revert(consumer)
    tx = state_transaction(consumer, SYNC_TRANSACTION_DIR)
    assert tx.read()['status'] == 'applied'
    assert (common_state_dir(consumer) / 'sync_state.json').read_bytes() != (tx.backup / '__state__' / 'sync_state.json').read_bytes()

    with pytest.raises(ctx.SyncError, match='未恢复'):
        cmd_transaction(_cancel_args(env['sync'], True))
    assert tx.read()['status'] == 'applied'


def test_no_op_sync_does_not_require_commit_or_accept(env, tmp_path, monkeypatch):
    args = _prepare(env, tmp_path, monkeypatch)
    consumer = env['consumer']
    from template_sync import transaction as module
    monkeypatch.setattr(module, '_run_tests', lambda root: True)

    assert cmd_apply(args) == 0
    _git(consumer, 'add', '-A')
    _git(consumer, 'commit', '-m', 'sync')
    assert cmd_transaction(argparse.Namespace(sync=env['sync'], action='accept')) == 0
    tx = state_transaction(consumer, SYNC_TRANSACTION_DIR)
    assert tx.read()['status'] == 'accepted'

    assert cmd_apply(args) == 0
    assert tx.read()['status'] == 'no_op'
    assert not _git(consumer, 'status', '--porcelain').stdout.strip()
    assert open_transactions(consumer) == []
    assert cmd_apply(_apply_args(env['sync'])) == 0
