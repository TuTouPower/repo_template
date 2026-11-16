"""工厂全量门禁必须运行在模板工具链契约版本上。"""

import sys

from _runtime import MIN_PYTHON, PYTHON_VERSION


def test_factory_gate_runs_on_contract_python():
    assert sys.version_info[:3] >= MIN_PYTHON, (
        f"工厂全量门禁需在 Python {PYTHON_VERSION}+ 运行；当前 {sys.version.split()[0]}"
    )
