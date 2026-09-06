"""Build explicit PASS evidence for successful real-Git workflow fixtures."""
from repo_task.documents import parse_front_matter
from repo_task.monitoring import review_scope_fingerprint


def ensure_review_evidence(worktree, task_dir, anchor):
    fm, _ = parse_front_matter(task_dir / 'task.md')
    names = ('general',) if fm.get('review_level') == 'single' else ('code', 'test')
    scope = review_scope_fingerprint(anchor, task_dir.relative_to(worktree).as_posix(), repo_root=worktree)
    assert scope, 'fixture must have a verifiable content scope'
    for name in names:
        report = task_dir / f'review_{name}.md'
        # A caller can deliberately provide stale/failed evidence; never overwrite it.
        if not report.exists():
            report.write_text(f'## Round 1\nreviewed_scope: {scope}\nverdict: PASS\n', encoding='utf-8')
