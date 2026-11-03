"""C/J1/A3：skill 与文档与新编排接口一致（run-begin / run-close / finish / 身份默认解析）。"""

from pathlib import Path

PRODUCT = Path(__file__).resolve().parents[1] / "repo"
SKILLS = PRODUCT / ".repo_template" / "skills"
DOCS = PRODUCT / ".repo_template" / "docs"


def _text(path):
    return path.read_text(encoding="utf-8")


def test_task_run_main_path_uses_run_begin_and_run_close_only():
    skill = _text(SKILLS / "task-run" / "SKILL.md")
    assert "run-begin {tid} --owner-id {owner_id}" in skill
    assert "run-close {tid} --owner-id {owner_id} --result done" in skill
    assert "--result blocked" in skill and "--result failed" in skill
    assert "--executor" not in skill
    # 原语子命令只出现在恢复语境，不再是逐步调用清单
    assert "attempt reserve --executor" not in skill
    assert "recovery.md" in skill and "只在恢复时使用" in skill


def test_task_work_finishes_with_message_file_and_never_hand_writes_handoff():
    skill = _text(SKILLS / "task-work" / "SKILL.md")
    assert "task.py finish {tid} --owner-id {owner_id} --message-file {path}" in skill
    assert "ac_evidence" not in skill
    assert "不再包含" not in skill
    assert "不手写 handoff" in skill
    assert "`- 测试：`、`- 黑盒：`、`- review：`" in skill
    assert "待执行时填写" in skill


def test_task_work_review_section_withdraws_all_ac_ticks_after_fail():
    skill = _text(SKILLS / "task-work" / "SKILL.md")
    assert "review 判 FAIL 且随后要修改交付内容时，先把 `task.md` 全部 AC 撤回为 `- [ ]`" in skill
    assert "修复后重跑测试与黑盒，逐条重新打勾，再派下一轮 review" in skill
    assert "只补报告或处置表、不改交付内容的 INCOMPLETE 不撤回" in skill
    assert "reviewer 不改 `task.md`" in skill


def test_task_integrate_does_not_require_explicit_identity():
    skill = _text(SKILLS / "task-integrate" / "SKILL.md")
    assert "`integrate {tid}`" in skill
    assert "--attempt" not in skill and "--execution-id" not in skill


def test_docs_describe_primitives_as_recovery_interface_and_finish_rollback():
    recovery = _text(DOCS / "recovery.md")
    assert "恢复用低层接口" in recovery
    assert "finish 回滚语义" in recovery
    assert "finish_interrupted" in recovery
    usage = _text(DOCS / "usage.md")
    assert "run-begin" in usage and "run-close" in usage
    assert "机器生成" in usage and "不手写" in usage
    architecture = _text(DOCS / "architecture.md")
    assert "run-begin(start → reserve → claim)" in architecture
    assert "低层原语" in architecture


def test_agents_md_states_handoff_is_generated_by_finish():
    agents = _text(PRODUCT / "AGENTS.md")
    assert "执行回执由 `finish` 生成，不手写" in agents
    assert "执行回执由实施侧在 `finish` 前写入" not in agents
