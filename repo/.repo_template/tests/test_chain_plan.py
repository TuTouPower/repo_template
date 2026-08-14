"""chain_plan.js 展示辅助回归：node 运行 JS 用例集。

批计划算法权威与用例在 test_plan.py（repo_task.plan.compute_batch_plan）。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

CASES_JS = Path(__file__).resolve().parent / "test_chain_plan_cases.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 运行 JS 用例")
def test_chain_plan_display_helpers():
    result = subprocess.run(
        ["node", str(CASES_JS)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stderr + result.stdout
