"""chain_plan.js 行为级回归：node 运行 JS 用例集。"""
import shutil
import subprocess
from pathlib import Path

import pytest

CASES_JS = Path(__file__).resolve().parent / "test_chain_plan_cases.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 运行 JS 用例")
def test_chain_plan_batch_plan_cases():
    result = subprocess.run(
        ["node", str(CASES_JS)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stderr
