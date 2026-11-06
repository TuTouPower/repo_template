"""验证策略门禁契约：拒绝多个可执行代码块，保留非执行示例。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task.validation import ValidationError, parse_policy

pytestmark = pytest.mark.contract

_GATE_SECTIONS = ("doctor_cmd", "test_cmd", "blackbox_verify")


def _policy_text(*, doctor="无", test="无", blackbox="无"):
    parts = [
        "# 测试", "",
        "## verification_policy", "project_configured", "",
        "## verification_notes", "门禁为隔离测试替身；断言由外层 pytest 执行。", "",
    ]
    parts += [
        "## doctor_cmd", doctor, "",
        "## test_cmd", test, "",
        "## blackbox_verify", blackbox, "",
    ]
    return "\n".join(parts)


def test_gate_sections_reject_multiple_bash_blocks():
    two_blocks = "```bash\necho first\n```\n\n```bash\necho second\n```"
    param_by_section = {
        "doctor_cmd": "doctor",
        "test_cmd": "test",
        "blackbox_verify": "blackbox",
    }
    for name in _GATE_SECTIONS:
        with pytest.raises(ValidationError, match="只允许一个 bash/sh 代码块"):
            parse_policy(_policy_text(**{param_by_section[name]: two_blocks}))


def test_gate_section_allows_single_bash_block_with_non_bash_example():
    doctor = "```text\n这是示例说明，不会执行\n```\n\n```bash\necho doctor\n```"
    commands = parse_policy(_policy_text(doctor=doctor))
    assert commands["doctor_cmd"] == "echo doctor"
