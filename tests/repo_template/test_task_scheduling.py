"""task.py 调度图算法纯函数。"""
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "repo_template"
sys.path.insert(0, str(SCRIPTS_DIR))

from task import TaskDataError, _dependency_cycle


def test_dependency_cycle_returns_stable_path():
    dependencies = {
        "t001": ["t003"],
        "t002": [],
        "t003": ["t005"],
        "t005": ["t001"],
    }

    assert _dependency_cycle(dependencies) == ["t001", "t003", "t005", "t001"]
