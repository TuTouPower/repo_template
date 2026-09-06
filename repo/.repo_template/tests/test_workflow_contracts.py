"""Cross-stage workflow contracts, exercised in isolated real Git repositories."""
import json

import pytest

from test_dispatch_integration import (
    git_repo, _git, _task_cli, _prepare_done, _cleanup, _worktree,
    _reserve, _identity_args, _handoff,
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
    def mutate(w):
        rel = 'docs/tasks/t001_alpha'
        anchor = _git(w, 'rev-parse', 'HEAD').stdout.strip()
        scope = review_scope_fingerprint(anchor, rel, repo_root=w)
        for name in ('review_code.md', 'review_test.md'):
            (w / rel / name).write_text(f'reviewed_scope: {scope}\nverdict: PASS\n')
        (w / 'README.md').write_text('unreviewed finalization change\n')
    identity, _, _ = _prepare_done(git_repo, 't001', 'alpha', mutate=mutate)
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


def test_creation_gate_still_rejects_bare_unknown(git_repo):
    path = git_repo / 'docs/tasks/t001_alpha/spec.md'
    path.write_text(path.read_text().replace('外部行为：已核实', '外部行为：UNVERIFIED，未知'))
    result = _task_cli(git_repo, 'preflight', 't001', '--creation')
    assert result.returncode != 0
    assert '裸 UNVERIFIED' in result.stdout


def test_resume_persists_only_explicitly_increased_budget(git_repo):
    assert _task_cli(git_repo, 'start', 't001').returncode == 0
    w = _worktree(git_repo, 't001')
    assert _task_cli(w, 'block', 't001', '--reason', 'review').returncode == 0
    result = _task_cli(w, 'resume', 't001', '--review-limit', '8', '--reason', '用户批准追加三轮')
    assert result.returncode == 0, result.stderr
    fm, _ = parse_front_matter(w / 'docs/tasks/t001_alpha/task.md')
    assert fm['review_limit'] == '8'
    assert fm['verify_limit'] == '5'
    assert '用户批准追加三轮' in fm['note']
    assert _task_cli(w, 'block', 't001', '--reason', 'review').returncode == 0
    before = (w / 'docs/tasks/t001_alpha/task.md').read_bytes()
    for args in (('--review-limit', '5', '--reason', 'decrease'), ('--review-limit', '9')):
        result = _task_cli(w, 'resume', 't001', *args)
        assert result.returncode != 0
        assert (w / 'docs/tasks/t001_alpha/task.md').read_bytes() == before


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


def test_recovery_rejects_wrong_identity_in_finished_worktree(git_repo):
    assert _task_cli(git_repo, 'start', 't001').returncode == 0
    identity = _reserve(git_repo, 't001')
    w = _worktree(git_repo, 't001')
    base = _git(w, 'rev-parse', 'HEAD').stdout.strip()
    assert _task_cli(w, 'finish', 't001').returncode == 0
    handoff = _handoff('t001', 't001_alpha', identity, base)
    handoff['execution_id'] = 'wrong-execution'
    (w / 'docs/archive/tasks/t001_alpha/handoff.json').write_text(json.dumps(handoff))
    assert _recovery(git_repo)['phase'] == 'needs_attention'


def test_retry_recovery_retains_budget_between_resume_and_reserve(git_repo):
    assert _task_cli(git_repo, 'start', 't001').returncode == 0
    old = _reserve(git_repo, 't001')
    w = _worktree(git_repo, 't001')
    assert _task_cli(w, 'block', 't001', '--reason', 'review').returncode == 0
    for command, status in (('terminal', 'stopped'), ('report', 'blocked')):
        result = _task_cli(git_repo, 'attempt', command, 't001', *_identity_args(old), '--status', status)
        assert result.returncode == 0, result.stderr
    assert _recovery(git_repo)['phase'] == 'blocked'
    result = _task_cli(w, 'resume', 't001', '--review-limit', '8', '--reason', '用户加轮')
    assert result.returncode == 0, result.stderr
    assert _recovery(git_repo)['phase'] == 'retry_ready'
    new = _reserve(git_repo, 't001')
    assert new['attempt'] == old['attempt'] + 1
    assert _recovery(git_repo)['phase'] == 'executing'
    fm, _ = parse_front_matter(w / 'docs/tasks/t001_alpha/task.md')
    assert fm['review_limit'] == '8'


def test_creation_cannot_be_used_for_active_or_strict_readiness(git_repo):
    result = _task_cli(git_repo, 'preflight', 't001', '--creation', '--require-verified')
    assert result.returncode != 0
    assert _task_cli(git_repo, 'start', 't001').returncode == 0
    result = _task_cli(_worktree(git_repo, 't001'), 'preflight', 't001', '--creation')
    assert result.returncode != 0
