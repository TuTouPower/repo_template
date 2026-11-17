"""工厂全量门禁须运行于契约版本；模板产物树的版本声明一致性校验。

消费仓 README 由项目定制，不参与版本校验（见 Issue #97 P1）。
"""

import sys
from pathlib import Path

from _runtime import MIN_PYTHON, PYTHON_VERSION

FACTORY_ROOT = Path(__file__).resolve().parents[2]


def test_factory_gate_runs_on_contract_python():
    assert sys.version_info[:3] >= MIN_PYTHON, (
        f"工厂全量门禁需在 Python {PYTHON_VERSION}+ 运行；当前 {sys.version.split()[0]}"
    )


def test_template_version_declarations_match_contract():
    """模板产物树内的现行版本声明必须与 PYTHON_VERSION 一致。"""
    for doc in (
        FACTORY_ROOT / "repo/README.md",
        FACTORY_ROOT / "repo/.repo_template/docs/usage.md",
        FACTORY_ROOT / "repo/docs/blueprint/testing.md",
    ):
        text = doc.read_text(encoding="utf-8")
        assert PYTHON_VERSION in text, f"{doc} 未声明 {PYTHON_VERSION}"
        assert "Python 3.9" not in text, f"{doc} 仍声明 3.9 为最低版本"
