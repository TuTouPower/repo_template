"""task 创建文档结构校验。"""
from pathlib import Path
import sys

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "repo_template"
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task.documents import parse_front_matter, validate_task_documents


SPEC_TEMPLATE = (REPO_ROOT / "docs/tasks/task_template/spec.md").read_text(encoding="utf-8")
_, TASK_BODY_TEMPLATE = parse_front_matter(REPO_ROOT / "docs/tasks/task_template/task.md")


def _filled_spec() -> str:
    replacements = {
        "{为什么需要此变更。}": "测试背景。",
        "{本 task 包含什么。}": "测试范围。",
        "{明确不做什么。}": "无。",
        "{可独立验证的行为结果。}": "可验证行为。",
        "- AC-001：{不可测原因与替代验证方式}": "- 全部 AC 可自动测试",
        "- {分支或场景}：{不测原因}": "- 无",
        "- {内容}": "- 按项目默认",
        "- {契约}：{分类标记}，{待验证方式}": "- 无",
        "- 风险：{可能失败的地方}": "- 风险：无",
        "- 回退：{失败后如何恢复}": "- 回退：无",
        "- {前置依赖、平台、安全或兼容性约束；无则写「无」。}": "- 无",
        "- `{文件路径}`：{具体条目；无则写「无」}": "- 无",
        "- 来源：{pNNN / finding_id / 原 tid}（核实日期与结论；无外部来源写「无」）": "- 来源：无",
    }
    text = SPEC_TEMPLATE
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def test_template_documents_pass_with_placeholders_allowed():
    problems, warnings = validate_task_documents(
        SPEC_TEMPLATE,
        TASK_BODY_TEMPLATE,
        allow_template_placeholders=True,
    )

    assert problems == []
    assert warnings == []


def test_filled_documents_pass():
    problems, warnings = validate_task_documents(_filled_spec(), TASK_BODY_TEMPLATE)

    assert problems == []
    assert warnings == []


def test_acceptance_missing_ac_id_fails():
    spec = _filled_spec().replace(
        "- [ ] AC-001：可验证行为。",
        "- [ ] 可验证行为。",
        1,
    )

    problems, _ = validate_task_documents(spec, TASK_BODY_TEMPLATE)

    assert any("缺 AC 编号" in problem for problem in problems)


def test_acceptance_duplicate_ac_id_fails():
    spec = _filled_spec().replace(
        "- [ ] AC-001：可验证行为。",
        "- [ ] AC-001：可验证行为。\n- [ ] AC-001：重复编号。",
        1,
    )

    problems, _ = validate_task_documents(spec, TASK_BODY_TEMPLATE)

    assert any("AC 编号重复" in problem for problem in problems)


def test_acceptance_deploy_ac_id_passes():
    spec = _filled_spec().replace(
        "- [ ] AC-001：可验证行为。",
        "- [ ] [deploy] AC-001：需真实部署验证。",
        1,
    )

    problems, _ = validate_task_documents(spec, TASK_BODY_TEMPLATE)

    assert problems == []


@pytest.mark.parametrize(
    "old,new,expected",
    [
        ("### 范围", "#### 范围", "### 范围"),
        ("### 测试策略", "### 测试方案", "### 测试策略"),
        ("## 上下文区", "## 其它", "## 上下文区"),
    ],
)
def test_missing_or_wrong_level_heading_fails(old, new, expected):
    problems, _ = validate_task_documents(
        _filled_spec().replace(old, new, 1),
        TASK_BODY_TEMPLATE,
    )

    assert any(expected in problem for problem in problems)


def test_heading_order_change_fails():
    spec = _filled_spec()
    scope = spec.index("### 范围")
    non_scope = spec.index("### 非范围")
    acceptance = spec.index("### 验收标准")
    scope_block = spec[scope:non_scope]
    non_scope_block = spec[non_scope:acceptance]
    spec = spec[:scope] + non_scope_block + scope_block + spec[acceptance:]

    problems, _ = validate_task_documents(spec, TASK_BODY_TEMPLATE)

    assert any("顺序错误" in problem for problem in problems)


def test_missing_fixed_guidance_fails():
    # 删掉带 `<!-- 规范 -->` 标记的就近规范块，门禁应失败
    spec = _filled_spec().replace(
        "<!-- 规范（门禁必留，不得删除） -->\n"
        "只写用户或调用方可观察行为，每条可独立验证。普通版本号、底层库和目录结构不作为验收标准；需要长期约束后续工作的技术选择写入 `docs/blueprint/decisions.md`。\n"
        "<!-- /规范 -->\n",
        "",
        1,
    )

    problems, _ = validate_task_documents(spec, TASK_BODY_TEMPLATE)

    assert any("规范块" in problem for problem in problems)


def test_template_placeholder_fails_after_creation():
    problems, _ = validate_task_documents(SPEC_TEMPLATE, TASK_BODY_TEMPLATE)

    assert any("模板占位符" in problem for problem in problems)


def test_empty_implementation_note_fails():
    task_body = TASK_BODY_TEMPLATE.replace("\n无\n\n## Review 处置", "\n## Review 处置", 1)

    problems, _ = validate_task_documents(_filled_spec(), task_body)

    assert any("实施笔记为空" in problem for problem in problems)


def test_actual_implementation_note_can_replace_default_none():
    task_body = TASK_BODY_TEMPLATE.replace(
        "\n无\n\n## Review 处置",
        "\n- 已完成结构检查。\n\n## Review 处置",
        1,
    )

    problems, _ = validate_task_documents(_filled_spec(), task_body)

    assert problems == []
