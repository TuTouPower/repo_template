"""Build explicit PASS evidence for successful real-Git workflow fixtures."""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task.documents import extract_ac_statuses, parse_front_matter
from repo_task.monitoring import review_scope_fingerprint

TASK_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "docs" / "task_template"

_SPEC_REPLACEMENTS = {
    "{为什么需要此变更。}": "测试背景。",
    "{本 task 包含什么。}": "测试范围。",
    "{明确不做什么。}": "无。",
    "{可独立验证的行为结果。}": "可验证行为。",
    "- AC-001：{不可测原因与替代验证方式}": "- 全部 AC 可自动测试",
    "- {分支或场景}：{不测原因}": "- 无",
    "- {内容}": "- 按项目默认",
    "- 风险：{可能失败的地方}": "- 风险：无",
    "- 回退：{失败后如何恢复}": "- 回退：无",
    "- {前置依赖、平台、安全或兼容性约束；无则写「无」。}": "- 无",
    "- `{文件路径}`：{具体条目；无则写「无」}": "- 无",
    "- 来源：{pNNN / finding_id / 原 tid}（核实日期与结论；无外部来源写「无」）": "- 来源：无",
}


def fill_spec_text(text: str, *, contract_item: str = "外部行为：已核实") -> str:
    """唯一权威的 spec 模板填充；contract_item 控制未知契约行。"""
    replacements = dict(_SPEC_REPLACEMENTS)
    replacements["- {契约}：{分类标记}，{待验证方式}"] = f"- {contract_item}"
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def valid_spec(contract_item: str = "外部行为：已核实") -> str:
    return fill_spec_text(
        (TASK_TEMPLATE_DIR / "task.md").read_text(encoding="utf-8"),
        contract_item=contract_item,
    )


def ac_review_section(spec_text, tick='TICK-VALID', mode='re_verified'):
    """按 task.md 的 AC 编号生成「AC 复验方式」小节（每条 AC 一行，格式同 share_prompt.txt）。"""
    rows = ''.join(
        f'- {ac_id}：{tick}，{mode}。测试夹具。\n' for ac_id, _ in extract_ac_statuses(spec_text)
    )
    return f'### AC 复验方式\n\n{rows}\n'


def pass_report(scope, spec_text, round_no=1, tick='TICK-VALID'):
    """含 AC 复验小节的 PASS review 报告正文，供 review 门禁测试夹具使用。"""
    return (f'## Round {round_no}\nreviewed_scope: {scope}\n\n'
            f'{ac_review_section(spec_text, tick=tick)}verdict: PASS\n')


def ensure_review_evidence(worktree, task_dir, anchor):
    fm, _ = parse_front_matter(task_dir / 'progress.md')
    spec = task_dir / 'task.md'
    spec_text = spec.read_text(encoding='utf-8') if spec.is_file() else ''
    names = ('general',) if fm.get('review_level') == 'single' else ('code', 'test')
    scope = review_scope_fingerprint(anchor, task_dir.relative_to(worktree).as_posix(), repo_root=worktree)
    assert scope, 'fixture must have a verifiable content scope'
    for name in names:
        report = task_dir / f'review_{name}.md'
        # A caller can deliberately provide stale/failed evidence; never overwrite it.
        if not report.exists():
            report.write_text(pass_report(scope, spec_text), encoding='utf-8')


def configure_project_verification(repo):
    """Fixtures explicitly choose gates; they do not inherit template defaults."""
    path = repo / 'docs/blueprint/testing.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('## verification_policy\n\nproject_configured\n\n'
                    '## verification_notes\n\n隔离 Git 契约场景，业务门禁不适用；断言由外层 pytest 执行。\n\n'
                    '## doctor_cmd\n\n无\n\n## test_cmd\n\n无\n\n## blackbox_verify\n\n无\n', encoding='utf-8')
    index = repo / 'docs/specs_index.md'
    index.write_text('# 当前生效 spec 清单\n\n|slug|task 清单|最后更新时间|\n|---|---|---|\n', encoding='utf-8')
    for task in (repo / 'docs/tasks').glob('t*/progress.md'):
        task.write_text(task.read_text(encoding='utf-8') + '\n- specs_index: not_applicable: 隔离工作流测试不修改长期需求规格。\n', encoding='utf-8')
