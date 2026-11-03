"""工厂集成测试套件约束：串行执行，并把产物工具链加入 sys.path。

本目录只在模板工厂运行，不随 repo/ 产物分发。测试经 sys.path 导入
repo/.repo_template/scripts 的脚本模块与 repo/.repo_template/tests 的 review_support。
"""

import os
import sys
from pathlib import Path

import pytest

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "repo" / ".repo_template"

for _path in (TEMPLATE_DIR / "scripts", TEMPLATE_DIR / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


def pytest_configure(config):
    if os.environ.get("PYTEST_XDIST_WORKER"):
        raise pytest.UsageError(
            "factory_tests 依赖进程级 monkeypatch，必须串行执行；"
            "请移除 pytest-xdist 的 -n/--dist 参数"
        )
