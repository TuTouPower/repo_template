"""Cross-stage workflow contracts, exercised in isolated real Git repositories."""
import json
from pathlib import Path

import pytest

from test_dispatch_integration import (
    git_repo, _git, _task_cli, _prepare_done, _cleanup, _worktree,
    _reserve, _identity_args, _handoff, _read_ledger,
)
from repo_task.documents import parse_front_matter, write_front_matter
from repo_task.monitoring import review_scope_fingerprint


@pytest.mark.parametrize('dependencies,base_args', [
    ('t001', ('--base', 't002_beta')),
    ('t001,t002', ()),
])
def test_start_rejects_dependency_merged_by_chain_but_missing_in_old_base(git_repo, dependencies, base_args):
    path = git_repo / 'docs/tasks/t003_gamma/task.md'
    fm, body = parse_front_matter(path)
    fm['depends_on'] = dependencies
    write_front_matter(path, fm, body)
    _git(git_repo, 'add', '-A')
    _git(git_repo, 'commit', '-m', 'schedule')
    i2, b2, _ = _prepare_done(git_repo, 't002', 'beta')
    _cleanup(git_repo, 't002', i2)
    i1, _, _ = _prepare_done(
        git_repo, 't001', 'alpha',
        mutate=lambda w: (w / 'required.txt').write_text('dependency implementation'),
    )
    _cleanup(git_repo, 't001', i1)
    for args in ((), ('--continue',)):
        result = _task_cli(git_repo, 'integrate-chain', 't001', *args)
        assert result.returncode == 0, result.stderr
    assert not _git(git_repo, 'branch', '--list', 't001_alpha').stdout.strip()
    result = _task_cli(git_repo, 'start', 't003', *base_args)
    assert result.returncode != 0
    assert '缺依赖实现' in result.stderr
    assert not _worktree(git_repo, 't003').exists()
    # A base that actually contains all dependencies is still accepted.
    for args in ((), ('--continue',)):
        result = _task_cli(git_repo, 'integrate-chain', 't002', *args)
        assert result.returncode == 0, result.stderr
    result = _task_cli(git_repo, 'start', 't003', '--base', 'main')
    assert result.returncode == 0, result.stderr
    assert (_worktree(git_repo, 't003') / 'required.txt').is_file()


def test_cleanup_rejects_finalization_changes_after_review(git_repo):
    identity, _, _ = _prepare_done(git_repo, 't001', 'alpha')
    w = _worktree(git_repo, 't001')
    (w / 'README.md').write_text('unreviewed finalization change\n')
    _git(w, 'add', '-A')
    _git(w, 'commit', '--amend', '--no-edit')
    result = _task_cli(git_repo, 'cleanup-worktree', 't001', *_identity_args(identity))
    assert result.returncode != 0
    assert 'review' in result.stderr.lower()
    assert _worktree(git_repo, 't001').is_dir()


@pytest.mark.parametrize('defect', ['missing', 'failed'])
def test_cleanup_checks_report_not_only_handoff_claim(git_repo, defect):
    identity, _, _ = _prepare_done(git_repo, 't001', 'alpha')
    w = _worktree(git_repo, 't001')
    report = w / 'docs/archive/tasks/t001_alpha/review_code.md'
    if defect == 'missing':
        report.unlink(missing_ok=True)
    else:
        report.write_text('verdict: FAIL\n')
    _git(w, 'add', '-A')
    _git(w, 'commit', '--amend', '--no-edit')
    result = _task_cli(git_repo, 'cleanup-worktree', 't001', *_identity_args(identity))
    assert result.returncode != 0
    assert 'review' in result.stderr.lower()
    assert w.is_dir()


def test_task_work_requires_untracked_deliverables_visible_to_review():
    skill = (
        Path(__file__).parents[1] / 'skills/task-work/SKILL.md'
    ).read_text(encoding='utf-8')
    prompt = (
        Path(__file__).parents[1] / 'docs/review_prompts/share_prompt.txt'
    ).read_text(encoding='utf-8')
    assert 'git ls-files --others --exclude-standard' in skill
    assert 'git add -N -- <path...>' in skill
    assert '完整 worktree 内容变化' in prompt
    assert '未跟踪交付文件' in prompt


def test_creation_gate_accepts_classified_blocking_but_start_does_not(git_repo):
    path = git_repo / 'docs/tasks/t001_alpha/spec.md'
    path.write_text(path.read_text().replace('外部行为：已核实', '外部行为：UNVERIFIED-BLOCKING，等待用户'))
    result = _task_cli(git_repo, 'preflight', 't001', '--creation')
    assert result.returncode == 0, result.stderr + result.stdout
    assert 'preflight=PASS' in result.stdout and 'UNVERIFIED-BLOCKING' in result.stdout
    readiness = _task_cli(git_repo, 'preflight', 't001', '--allow-backlog')
    assert readiness.returncode != 0
    _git(git_repo, 'add', '-A')
    _git(git_repo, 'commit', '-m', 'record classified blocker')
    result = _task_cli(git_repo, 'start', 't001')
    assert result.returncode != 0 and 'UNVERIFIED-BLOCKING' in result.stderr
    assert not _worktree(git_repo, 't001').exists()



def test_runtime_rejects_retired_blocked_status_instead_of_compatibility_fallback(git_repo):
    path = git_repo / 'docs/tasks/t001_alpha/task.md'
    fm, body = parse_front_matter(path)
    fm['status'] = 'blocked'
    write_front_matter(path, fm, body)
    result = _task_cli(git_repo, 'list')
    assert result.returncode != 0
    assert "status 非法（'blocked'）" in result.stderr


def _recovery(repo):
    before = _git(repo, 'status', '--porcelain').stdout
    result = _task_cli(repo, 'recovery', 't001')
    assert result.returncode == 0, result.stderr
    assert _git(repo, 'status', '--porcelain').stdout == before
    return json.loads(result.stdout)


def test_recovery_distinguishes_start_reserve_finish_and_commit_boundaries(git_repo):
    assert _task_cli(git_repo, 'start', 't001').returncode == 0
    assert _recovery(git_repo)['phase'] == 'started_without_attempt'
    identity = _reserve(git_repo, 't001')
    assert _recovery(git_repo)['phase'] == 'executing'
    w = _worktree(git_repo, 't001')
    base = _git(w, 'rev-parse', 'HEAD').stdout.strip()
    assert _task_cli(w, 'finish', 't001').returncode == 0
    archive = w / 'docs/archive/tasks/t001_alpha'
    (archive / 'handoff.json').write_text(json.dumps(_handoff('t001', 't001_alpha', identity, base)))
    state = _recovery(git_repo)
    assert state['phase'] == 'finished_uncommitted'
    assert state['execution_id'] == identity['execution_id']
    assert state['task_dir'] == str(archive)
    _git(w, 'add', '-A')
    _git(w, 'commit', '-m', 'execute task')
    assert _recovery(git_repo)['phase'] == 'committed_unreported'


def test_recovery_closes_same_identity_without_reexecution(git_repo):
    identity, _, head = _prepare_done(git_repo, 't001', 'alpha', terminal=False)
    assert _recovery(git_repo)['phase'] == 'committed_unreported'
    result = _task_cli(git_repo, 'attempt', 'terminal', 't001', *_identity_args(identity), '--status', 'completed')
    assert result.returncode == 0, result.stderr
    # Crash after terminal but before report must not ask for another terminal.
    assert _recovery(git_repo)['phase'] == 'committed_unreported'
    result = _task_cli(git_repo, 'attempt', 'report', 't001', *_identity_args(identity), '--status', 'done', '--sha', head)
    assert result.returncode == 0, result.stderr
    assert _recovery(git_repo)['phase'] == 'reported_uncleaned'
    _cleanup(git_repo, 't001', identity)
    state = _recovery(git_repo)
    assert state['phase'] == 'closed'
    assert state['execution_id'] == identity['execution_id']


def test_creation_cannot_be_used_for_active_or_strict_readiness(git_repo):
    result = _task_cli(git_repo, 'preflight', 't001', '--creation', '--require-verified')
    assert result.returncode != 0
    assert _task_cli(git_repo, 'start', 't001').returncode == 0
    result = _task_cli(_worktree(git_repo, 't001'), 'preflight', 't001', '--creation')
    assert result.returncode != 0


def test_limits_increase_both_budgets_without_state_transition(git_repo):
    assert _task_cli(git_repo, 'start', 't001').returncode == 0
    w = _worktree(git_repo, 't001')
    result = _task_cli(w, 'limits', 't001', '--review', '8', '--verify', '7', '--reason', '用户批准')
    assert result.returncode == 0, result.stderr
    fm, _ = parse_front_matter(w / 'docs/tasks/t001_alpha/task.md')
    assert fm['status'] == 'active'
    assert fm['review_limit'] == '8' and fm['verify_limit'] == '7'
    before = (w / 'docs/tasks/t001_alpha/task.md').read_bytes()
    result = _task_cli(w, 'limits', 't001', '--review', '8', '--reason', '不能不增')
    assert result.returncode != 0
    assert (w / 'docs/tasks/t001_alpha/task.md').read_bytes() == before


def test_blocked_attempt_keeps_task_active_and_new_attempt_resumes(git_repo):
    assert _task_cli(git_repo, 'start', 't001').returncode == 0
    first = _reserve(git_repo, 't001')
    for command, status in (('terminal', 'stopped'), ('report', 'blocked')):
        result = _task_cli(git_repo, 'attempt', command, 't001', *_identity_args(first), '--status', status)
        assert result.returncode == 0, result.stderr
    w = _worktree(git_repo, 't001')
    fm, _ = parse_front_matter(w / 'docs/tasks/t001_alpha/task.md')
    assert fm['status'] == 'active'
    assert _recovery(git_repo)['phase'] == 'retry_ready'
    second = _reserve(git_repo, 't001')
    assert second['attempt'] == first['attempt'] + 1


def test_native_single_merge_validates_before_commit(git_repo):
    identity, branch, branch_head = _prepare_done(git_repo, 't001', 'alpha')
    _cleanup(git_repo, 't001', identity)
    started = _task_cli(git_repo, 'integrate', 't001', *_identity_args(identity))
    assert started.returncode == 0, started.stderr
    assert '尚未 commit' in started.stdout
    assert _git(git_repo, 'rev-parse', '--verify', 'MERGE_HEAD', check=False).returncode == 0
    assert _git(git_repo, 'merge-base', '--is-ancestor', branch_head, 'main', check=False).returncode != 0
    finished = _task_cli(git_repo, 'integrate', 't001', *_identity_args(identity), '--continue')
    assert finished.returncode == 0, finished.stderr
    assert _git(git_repo, 'merge-base', '--is-ancestor', branch_head, 'main', check=False).returncode == 0
    assert not _git(git_repo, 'branch', '--list', branch).stdout.strip()
    integrated = [e for e in _read_ledger(git_repo) if e['event'] == 'integrated']
    assert len(integrated) == 1


def test_native_merge_can_abort_without_main_commit(git_repo):
    identity, branch, branch_head = _prepare_done(git_repo, 't001', 'alpha')
    _cleanup(git_repo, 't001', identity)
    before = _git(git_repo, 'rev-parse', 'HEAD').stdout.strip()
    assert _task_cli(git_repo, 'integrate', 't001', *_identity_args(identity)).returncode == 0
    assert _git(git_repo, 'merge', '--abort').returncode == 0
    assert _git(git_repo, 'rev-parse', 'HEAD').stdout.strip() == before
    assert _git(git_repo, 'branch', '--list', branch).stdout.strip()
    assert not [e for e in _read_ledger(git_repo) if e['event'] == 'integrated']


def test_native_chain_merge_commits_once_after_validation(git_repo):
    first, first_branch, first_head = _prepare_done(git_repo, 't001', 'alpha')
    _cleanup(git_repo, 't001', first)
    second, second_branch, second_head = _prepare_done(git_repo, 't002', 'beta', base=first_branch)
    _cleanup(git_repo, 't002', second)
    before = int(_git(git_repo, 'rev-list', '--merges', '--count', 'HEAD').stdout)
    prepared = _task_cli(git_repo, 'integrate-chain', 't002')
    assert prepared.returncode == 0, prepared.stderr
    assert _git(git_repo, 'rev-parse', '--verify', 'MERGE_HEAD', check=False).returncode == 0
    assert int(_git(git_repo, 'rev-list', '--merges', '--count', 'HEAD').stdout) == before
    assert not [e for e in _read_ledger(git_repo) if e['event'] == 'integrated']
    finished = _task_cli(git_repo, 'integrate-chain', 't002', '--continue')
    assert finished.returncode == 0, finished.stderr
    assert int(_git(git_repo, 'rev-list', '--merges', '--count', 'HEAD').stdout) == before + 1
    for head in (first_head, second_head):
        assert _git(git_repo, 'merge-base', '--is-ancestor', head, 'main', check=False).returncode == 0
    assert not _git(git_repo, 'branch', '--list', first_branch).stdout.strip()
    assert not _git(git_repo, 'branch', '--list', second_branch).stdout.strip()
    events = [e for e in _read_ledger(git_repo) if e['event'] == 'integrated']
    assert [(e['tid'], e['attempt'], e['execution_id']) for e in events] == [
        ('t001', first['attempt'], first['execution_id']),
        ('t002', second['attempt'], second['execution_id']),
    ]


def test_chain_continue_excludes_branch_integrated_by_older_merge(git_repo):
    first, first_branch, _ = _prepare_done(git_repo, 't001', 'alpha')
    _cleanup(git_repo, 't001', first)
    assert _task_cli(git_repo, 'integrate', 't001', *_identity_args(first)).returncode == 0
    kept = _task_cli(
        git_repo, 'integrate', 't001', *_identity_args(first),
        '--continue', '--keep-branch',
    )
    assert kept.returncode == 0, kept.stderr
    first_merge = [
        event['merge_sha'] for event in _read_ledger(git_repo)
        if event['event'] == 'integrated' and event['tid'] == 't001'
    ][0]

    second, second_branch, _ = _prepare_done(
        git_repo, 't002', 'beta', base=first_branch,
    )
    _cleanup(git_repo, 't002', second)
    assert _task_cli(git_repo, 'integrate-chain', 't002').returncode == 0
    finished = _task_cli(git_repo, 'integrate-chain', 't002', '--continue')
    assert finished.returncode == 0, finished.stderr
    events = [event for event in _read_ledger(git_repo) if event['event'] == 'integrated']
    assert [event['tid'] for event in events] == ['t001', 't002']
    assert events[0]['merge_sha'] == first_merge
    assert events[1]['merge_sha'] != first_merge
    assert _git(git_repo, 'branch', '--list', first_branch).stdout.strip() == first_branch
    assert not _git(git_repo, 'branch', '--list', second_branch).stdout.strip()


def test_native_chain_continue_recovers_after_merge_commit_before_ledger(git_repo):
    first, first_branch, _ = _prepare_done(git_repo, 't001', 'alpha')
    _cleanup(git_repo, 't001', first)
    second, second_branch, second_head = _prepare_done(git_repo, 't002', 'beta', base=first_branch)
    _cleanup(git_repo, 't002', second)
    assert _task_cli(git_repo, 'integrate-chain', 't002').returncode == 0
    # Simulate process loss after Git commit but before integrated events/branch cleanup.
    committed = _git(git_repo, 'commit', '--no-edit')
    assert committed.returncode == 0, committed.stderr
    assert not [e for e in _read_ledger(git_repo) if e['event'] == 'integrated']
    recovered = _task_cli(git_repo, 'integrate-chain', 't002', '--continue')
    assert recovered.returncode == 0, recovered.stderr
    assert not _git(git_repo, 'branch', '--list', first_branch).stdout.strip()
    assert not _git(git_repo, 'branch', '--list', second_branch).stdout.strip()
    assert _git(git_repo, 'merge-base', '--is-ancestor', second_head, 'main', check=False).returncode == 0
    assert len([e for e in _read_ledger(git_repo) if e['event'] == 'integrated']) == 2


def test_chain_continue_finishes_cleanup_after_integrated_ledger(git_repo):
    first, first_branch, first_head = _prepare_done(git_repo, 't001', 'alpha')
    _cleanup(git_repo, 't001', first)
    second, second_branch, second_head = _prepare_done(
        git_repo, 't002', 'beta', base=first_branch,
    )
    _cleanup(git_repo, 't002', second)
    assert _task_cli(git_repo, 'integrate-chain', 't002').returncode == 0
    finished = _task_cli(git_repo, 'integrate-chain', 't002', '--continue')
    assert finished.returncode == 0, finished.stderr
    merge_shas = {
        event['tid']: event['merge_sha']
        for event in _read_ledger(git_repo) if event['event'] == 'integrated'
    }
    assert merge_shas['t001'] == merge_shas['t002']

    _git(git_repo, 'branch', first_branch, first_head)
    _git(git_repo, 'branch', second_branch, second_head)
    recovered = _task_cli(git_repo, 'integrate-chain', 't002', '--continue')
    assert recovered.returncode == 0, recovered.stderr
    assert not _git(git_repo, 'branch', '--list', first_branch).stdout.strip()
    assert not _git(git_repo, 'branch', '--list', second_branch).stdout.strip()
    assert {
        event['tid']: event['merge_sha']
        for event in _read_ledger(git_repo) if event['event'] == 'integrated'
    } == merge_shas

    _git(git_repo, 'branch', first_branch, first_head)
    _git(git_repo, 'branch', second_branch, second_head)
    _git(git_repo, 'branch', '-d', '--', first_branch)
    recovered_tail = _task_cli(git_repo, 'integrate-chain', 't002', '--continue')
    assert recovered_tail.returncode == 0, recovered_tail.stderr
    assert not _git(git_repo, 'branch', '--list', first_branch).stdout.strip()
    assert not _git(git_repo, 'branch', '--list', second_branch).stdout.strip()
    assert {
        event['tid']: event['merge_sha']
        for event in _read_ledger(git_repo) if event['event'] == 'integrated'
    } == merge_shas


def test_native_merge_conflict_uses_git_state_and_continue(git_repo):
    shared = git_repo / 'shared.txt'
    shared.write_text('base\n')
    _git(git_repo, 'add', 'shared.txt')
    _git(git_repo, 'commit', '-m', 'add shared')

    def mutate(worktree):
        (worktree / 'shared.txt').write_text('from task\n')

    identity, branch, branch_head = _prepare_done(git_repo, 't001', 'alpha', mutate=mutate)
    _cleanup(git_repo, 't001', identity)
    shared.write_text('from main\n')
    _git(git_repo, 'add', 'shared.txt')
    _git(git_repo, 'commit', '-m', 'main edit')

    conflict = _task_cli(git_repo, 'integrate', 't001', *_identity_args(identity))
    assert conflict.returncode != 0
    assert _git(git_repo, 'rev-parse', '--verify', 'MERGE_HEAD', check=False).returncode == 0
    assert _git(git_repo, 'diff', '--name-only', '--diff-filter=U').stdout.strip() == 'shared.txt'
    shared.write_text('resolved\n')
    _git(git_repo, 'add', 'shared.txt')
    finished = _task_cli(git_repo, 'integrate', 't001', *_identity_args(identity), '--continue')
    assert finished.returncode == 0, finished.stderr
    assert _git(git_repo, 'merge-base', '--is-ancestor', branch_head, 'main', check=False).returncode == 0
    assert not _git(git_repo, 'branch', '--list', branch).stdout.strip()


def test_creation_gate_rejects_bare_unverified(git_repo):
    path = git_repo / 'docs/tasks/t001_alpha/spec.md'
    path.write_text(path.read_text().replace('外部行为：已核实', '外部行为：UNVERIFIED，待分类'))
    result = _task_cli(git_repo, 'preflight', 't001', '--creation')
    assert result.returncode != 0
    assert '裸 UNVERIFIED' in result.stdout


def test_recovery_rejects_wrong_handoff_identity(git_repo):
    assert _task_cli(git_repo, 'start', 't001').returncode == 0
    identity = _reserve(git_repo, 't001')
    worktree = _worktree(git_repo, 't001')
    base = _git(worktree, 'rev-parse', 'HEAD').stdout.strip()
    assert _task_cli(worktree, 'finish', 't001').returncode == 0
    handoff = _handoff('t001', 't001_alpha', identity, base)
    handoff['execution_id'] = 'wrong-execution-id'
    (worktree / 'docs/archive/tasks/t001_alpha/handoff.json').write_text(json.dumps(handoff))
    state = _recovery(git_repo)
    assert state['phase'] == 'needs_attention'
    assert 'identity' in state['action']


@pytest.mark.parametrize('option,field', [('--review', 'review_limit'), ('--verify', 'verify_limit')])
def test_limits_can_increase_one_budget(git_repo, option, field):
    assert _task_cli(git_repo, 'start', 't001').returncode == 0
    worktree = _worktree(git_repo, 't001')
    result = _task_cli(worktree, 'limits', 't001', option, '9', '--reason', '用户批准单项追加')
    assert result.returncode == 0, result.stderr
    fm, _ = parse_front_matter(worktree / 'docs/tasks/t001_alpha/task.md')
    assert fm[field] == '9'
    other = 'verify_limit' if field == 'review_limit' else 'review_limit'
    assert fm[other] == '5'


def test_merge_continue_rejects_unrelated_staged_path(git_repo):
    identity, _, _ = _prepare_done(git_repo, 't001', 'alpha')
    _cleanup(git_repo, 't001', identity)
    assert _task_cli(git_repo, 'integrate', 't001', *_identity_args(identity)).returncode == 0
    (git_repo / 'unrelated.txt').write_text('not part of task\n')
    _git(git_repo, 'add', 'unrelated.txt')
    result = _task_cli(git_repo, 'integrate', 't001', *_identity_args(identity), '--continue')
    assert result.returncode != 0
    assert '无关路径' in result.stderr and 'unrelated.txt' in result.stderr
    assert _git(git_repo, 'rev-parse', '--verify', 'MERGE_HEAD', check=False).returncode == 0


def test_merge_continue_rejects_modified_nonconflict_task_path(git_repo):
    identity, branch, _ = _prepare_done(git_repo, 't001', 'alpha')
    _cleanup(git_repo, 't001', identity)
    assert _task_cli(git_repo, 'integrate', 't001', *_identity_args(identity)).returncode == 0
    path = git_repo / f'docs/archive/tasks/{branch}/task.md'
    path.write_text(path.read_text() + '\n未经 review 的 merge 期改写\n')
    _git(git_repo, 'add', str(path.relative_to(git_repo)))
    result = _task_cli(git_repo, 'integrate', 't001', *_identity_args(identity), '--continue')
    assert result.returncode != 0
    assert '非冲突文件偏离 Git 自动合并结果' in result.stderr
    assert f'docs/archive/tasks/{branch}/task.md' in result.stderr
    assert _git(git_repo, 'rev-parse', '--verify', 'MERGE_HEAD', check=False).returncode == 0


def test_native_merge_commit_contains_derived_indexes(git_repo):
    identity, _, _ = _prepare_done(git_repo, 't001', 'alpha')
    _cleanup(git_repo, 't001', identity)
    assert _task_cli(git_repo, 'integrate', 't001', *_identity_args(identity)).returncode == 0
    assert _task_cli(git_repo, 'integrate', 't001', *_identity_args(identity), '--continue').returncode == 0
    names = _git(git_repo, 'show', '--format=', '--name-only', 'HEAD').stdout.splitlines()
    assert 'docs/tasks_index.json' in names
    assert 'docs/archive/tasks_index.json' in names


def test_two_independent_tasks_integrate_in_completion_order(git_repo):
    first, first_branch, _ = _prepare_done(git_repo, 't001', 'alpha')
    _cleanup(git_repo, 't001', first)
    second, second_branch, _ = _prepare_done(git_repo, 't002', 'beta')
    _cleanup(git_repo, 't002', second)
    for tid, identity in (('t002', second), ('t001', first)):
        assert _task_cli(git_repo, 'integrate', tid, *_identity_args(identity)).returncode == 0
        assert _task_cli(git_repo, 'integrate', tid, *_identity_args(identity), '--continue').returncode == 0
    assert not _git(git_repo, 'branch', '--list', first_branch).stdout.strip()
    assert not _git(git_repo, 'branch', '--list', second_branch).stdout.strip()
    assert [e['tid'] for e in _read_ledger(git_repo) if e['event'] == 'integrated'] == ['t002', 't001']


def test_integrate_is_idempotent_after_branch_cleanup(git_repo):
    identity, _, _ = _prepare_done(git_repo, 't001', 'alpha')
    _cleanup(git_repo, 't001', identity)
    assert _task_cli(git_repo, 'integrate', 't001', *_identity_args(identity)).returncode == 0
    assert _task_cli(git_repo, 'integrate', 't001', *_identity_args(identity), '--continue').returncode == 0
    repeated = _task_cli(git_repo, 'integrate', 't001', *_identity_args(identity), '--continue')
    assert repeated.returncode == 0, repeated.stderr
    assert '幂等' in repeated.stdout
    assert len([e for e in _read_ledger(git_repo) if e['event'] == 'integrated']) == 1


def test_native_chain_conflict_resolves_then_continues(git_repo):
    shared = git_repo / 'shared.txt'
    shared.write_text('base\n')
    _git(git_repo, 'add', 'shared.txt')
    _git(git_repo, 'commit', '-m', 'add shared')

    first, first_branch, _ = _prepare_done(
        git_repo, 't001', 'alpha',
        mutate=lambda worktree: (worktree / 'shared.txt').write_text('from chain\n'),
    )
    _cleanup(git_repo, 't001', first)
    second, second_branch, second_head = _prepare_done(
        git_repo, 't002', 'beta', base=first_branch,
    )
    _cleanup(git_repo, 't002', second)

    shared.write_text('from main\n')
    _git(git_repo, 'add', 'shared.txt')
    _git(git_repo, 'commit', '-m', 'main edit')

    conflict = _task_cli(git_repo, 'integrate-chain', 't002')
    assert conflict.returncode != 0
    assert 'shared.txt' in conflict.stderr
    assert _git(git_repo, 'rev-parse', '--verify', 'MERGE_HEAD', check=False).returncode == 0
    shared.write_text('resolved chain merge\n')
    _git(git_repo, 'add', 'shared.txt')
    finished = _task_cli(git_repo, 'integrate-chain', 't002', '--continue')
    assert finished.returncode == 0, finished.stderr
    assert _git(git_repo, 'merge-base', '--is-ancestor', second_head, 'main', check=False).returncode == 0
    assert not _git(git_repo, 'branch', '--list', first_branch).stdout.strip()
    assert not _git(git_repo, 'branch', '--list', second_branch).stdout.strip()
    assert len([e for e in _read_ledger(git_repo) if e['event'] == 'integrated']) == 2
