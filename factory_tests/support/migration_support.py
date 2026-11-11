"""迁移器接口测试夹具：调用方持有事务，验证写入范围与失败恢复。

不模拟 CLI；正式跨类型编排由 test_single_commit_sync 覆盖。
"""

from repo_task.file_transaction import SYNC_TRANSACTION_DIR, state_transaction
from template_sync.migrations import MIGRATIONS
from template_sync.source import resolve_src
from template_sync.state import read_state
from factory_paths import FACTORY_ROOT


def _outside_snapshot(root, paths):
    result = {}
    for path in root.rglob('*'):
        rel = path.relative_to(root).as_posix()
        if rel == '.git' or rel.startswith('.git/'):
            continue
        if any(rel == name or rel.startswith(name + '/') or name.startswith(rel + '/') for name in paths):
            continue
        result[rel] = (path.lstat().st_mode, str(path.readlink()) if path.is_symlink()
                       else path.read_bytes() if path.is_file() else None)
    return result


def migration(kind):
    return next(item for item in MIGRATIONS if item.kind == kind)


def findings(kind, sync, src=None):
    return migration(kind).findings(sync, src or FACTORY_ROOT / 'repo')


def exercise_migrations(args):
    sync = args.sync
    state = read_state(sync)
    src = resolve_src(state, sync=sync) if state and state.get('template_source') else FACTORY_ROOT / 'repo'
    selected = MIGRATIONS if args.kind == 'all' else (migration(args.kind),)
    active = [item for item in selected if item.findings(sync, src)]
    if not args.write:
        for item in active:
            print(item.kind + ': ' + '\n'.join(item._run(sync, src, write=False)))
        print('未写盘')
        return []
    if not active:
        return []
    from template_sync.apply import _transaction_roots
    transaction = state_transaction(sync.consumer, SYNC_TRANSACTION_DIR)
    transaction.begin(_transaction_roots([path for item in active for path in item.ROOTS]),
                      state_paths=sorted({path for item in active for path in item.STATE_ROOTS}))
    from repo_task.context import common_state_dir
    state_root = common_state_dir(sync.consumer)
    outside = _outside_snapshot(sync.consumer, transaction.read()['paths'])
    try:
        reports = []
        for item in active:
            before = _outside_snapshot(sync.consumer, item.ROOTS)
            common_before = _outside_snapshot(state_root, (*item.STATE_ROOTS, SYNC_TRANSACTION_DIR))
            reports.extend(item.execute(sync, src))
            assert _outside_snapshot(sync.consumer, item.ROOTS) == before, item.kind
            assert _outside_snapshot(state_root, (*item.STATE_ROOTS, SYNC_TRANSACTION_DIR)) == common_before, item.kind
        assert _outside_snapshot(sync.consumer, transaction.read()['paths']) == outside
        transaction.applied()
    except Exception:
        transaction.rollback(automatic=True)
        raise
    print('\n'.join(reports))
    return reports
