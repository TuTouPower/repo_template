"""套件级约束：本测试依赖进程级 monkeypatch，禁止 pytest-xdist 并行。"""

import os

import pytest


def pytest_configure(config):
    if os.environ.get("PYTEST_XDIST_WORKER"):
        raise pytest.UsageError(
            "repo/.repo_template/tests 依赖进程级 monkeypatch，必须串行执行；"
            "请移除 pytest-xdist 的 -n/--dist 参数"
        )
