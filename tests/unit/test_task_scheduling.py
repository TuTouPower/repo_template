"""task.py 调度输入与图算法纯函数。"""
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from task import TaskDataError, _dependency_cycle, parse_done_tids


@pytest.mark.parametrize(
    "raw,expected",
    [
        (["t11"], ["t011"]),
        (["t012"], ["t012"]),
        (["13"], ["t013"]),
        (["t0015"], ["t015"]),
        (["T14"], ["t014"]),
        (["T00025"], ["t025"]),
        (["t1000"], ["t1000"]),
        (["t11,t012", "13,T14"], ["t011", "t012", "t013", "t014"]),
    ],
)
def test_parse_done_tids_accepts_human_formats(raw, expected):
    known = ["t011", "t012", "t013", "t014", "t015", "t025", "t1000"]

    assert parse_done_tids(raw, known) == expected


@pytest.mark.parametrize("raw", [["x12"], ["t0"], ["0"], ["t999"]])
def test_parse_done_tids_rejects_invalid_or_missing(raw):
    with pytest.raises(TaskDataError, match="invalid_done"):
        parse_done_tids(raw, ["t012"])


def test_parse_done_tids_rejects_numeric_ambiguity():
    with pytest.raises(TaskDataError, match="匹配不唯一"):
        parse_done_tids(["1"], ["t1", "t001"])


def test_dependency_cycle_returns_stable_path():
    dependencies = {
        "t001": ["t003"],
        "t002": [],
        "t003": ["t005"],
        "t005": ["t001"],
    }

    assert _dependency_cycle(dependencies) == ["t001", "t003", "t005", "t001"]
