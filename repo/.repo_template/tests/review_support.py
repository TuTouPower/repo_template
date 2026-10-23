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


def configure_project_verification(repo):
    """Fixtures explicitly choose gates; they do not inherit template defaults."""
    path = repo / 'docs/blueprint/testing.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('## verification_policy\n\nproject_configured\n\n'
                    '## verification_notes\n\n隔离 Git 契约场景，业务门禁不适用；断言由外层 pytest 执行。\n\n'
                    '## doctor_cmd\n\n无\n\n## test_cmd\n\n无\n\n## blackbox_verify\n\n无\n', encoding='utf-8')
    index = repo / 'docs/specs_index.md'
    index.write_text('# 当前生效 spec 清单\n\n|slug|task 清单|最后更新时间|\n|---|---|---|\n', encoding='utf-8')
    for task in (repo / 'docs/tasks').glob('t*/task.md'):
        task.write_text(task.read_text(encoding='utf-8') + '\n- specs_index: not_applicable: 隔离工作流测试不修改长期需求规格。\n', encoding='utf-8')
