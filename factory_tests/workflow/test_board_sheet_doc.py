"""board.js 文档请求竞态回归：node 运行 JS 用例集（P3-2）。"""

import shutil
import subprocess
from pathlib import Path

import pytest

CASES_JS = Path(__file__).resolve().parent / "test_board_sheet_doc_cases.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 运行 JS 用例")
def test_sheet_doc_request_identity_is_checked():
    result = subprocess.run(
        ["node", str(CASES_JS)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stderr + result.stdout
