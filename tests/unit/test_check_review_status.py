"""check_review_status.py 测试。"""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from check_review_status import (
    disposition_stats,
    extract_verdicts,
    regression_rounds,
)


# --- extract_verdicts ---

def test_extract_verdicts_finds_all(tmp_path):
    p = tmp_path / "review_code.md"
    p.write_text("verdict: FAIL\n...\nverdict: PASS\n", encoding="utf-8")
    assert extract_verdicts(p) == ["FAIL", "PASS"]


def test_extract_verdicts_empty_when_no_file(tmp_path):
    assert extract_verdicts(tmp_path / "missing.md") == []


def test_extract_verdicts_empty_when_no_match(tmp_path):
    p = tmp_path / "r.md"
    p.write_text("no verdict here\n", encoding="utf-8")
    assert extract_verdicts(p) == []


def test_extract_verdicts_strict_line_match(tmp_path):
    """带尾随注释的 verdict 行不被匹配（\\s*$ 限制）。"""
    p = tmp_path / "r.md"
    p.write_text("verdict: PASS # 注释\n", encoding="utf-8")
    assert extract_verdicts(p) == []


# --- regression_rounds ---

def test_regression_rounds_single_pass(tmp_path):
    p = tmp_path / "r.md"
    p.write_text("verdict: PASS\n", encoding="utf-8")
    assert regression_rounds(p) == 1


def test_regression_rounds_fail_then_pass(tmp_path):
    p = tmp_path / "r.md"
    p.write_text("verdict: FAIL\nverdict: PASS\n", encoding="utf-8")
    assert regression_rounds(p) == 2


def test_regression_rounds_multi_fail(tmp_path):
    p = tmp_path / "r.md"
    p.write_text("verdict: FAIL\nverdict: FAIL\nverdict: PASS\n", encoding="utf-8")
    assert regression_rounds(p) == 3


def test_regression_rounds_uses_round_headers(tmp_path):
    p = tmp_path / "r.md"
    p.write_text("## Round 3\nverdict: PASS\n", encoding="utf-8")
    assert regression_rounds(p) == 3


def test_regression_rounds_picks_max_across_reports(tmp_path):
    a = tmp_path / "code.md"
    a.write_text("verdict: FAIL\nverdict: PASS\n", encoding="utf-8")  # round=2
    b = tmp_path / "test.md"
    b.write_text("## Round 5\nverdict: PASS\n", encoding="utf-8")  # round=5
    assert regression_rounds(a, b) == 5


# --- disposition_stats ---

def test_disposition_stats_counts(tmp_path):
    p = tmp_path / "task.md"
    p.write_text(
        "---\ntid: t001\n---\n\n"
        "## Review 处置\n\n### Round 1\n\n"
        "| finding_id | severity | status | rationale | fix_ref |\n"
        "|------------|----------|--------|-----------|---------|\n"
        "| t001_code_f001 | critical | 已修 | x | f:1 |\n"
        "| t001_code_f002 | minor | 遗留 | y | f001 |\n"
        "| t001_test_f003 | important | 撤回 | z | - |\n",
        encoding="utf-8",
    )
    stats = disposition_stats(p)
    assert stats["已修"] == 1
    assert stats["遗留"] == 1
    assert stats["撤回"] == 1


def test_disposition_stats_skips_template_t000(tmp_path):
    p = tmp_path / "task.md"
    p.write_text(
        "---\ntid: t000\n---\n\n"
        "## Review 处置\n\n"
        "| finding_id | severity | status | rationale | fix_ref |\n"
        "|------------|----------|--------|-----------|---------|\n"
        "| t000_code_f001 | critical | 已修 | x | f:1 |\n"
        "| t001_test_f001 | minor | 遗留 | y | f001 |\n",
        encoding="utf-8",
    )
    stats = disposition_stats(p)
    # t000 模板行不计
    assert stats["已修"] == 0
    assert stats["遗留"] == 1


def test_disposition_stats_empty_when_no_table(tmp_path):
    p = tmp_path / "task.md"
    p.write_text("---\ntid: t001\n---\n无表\n", encoding="utf-8")
    stats = disposition_stats(p)
    assert sum(stats.values()) == 0
