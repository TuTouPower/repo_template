"""check_review_status.py 测试。"""
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import check_review_status as crs
from check_review_status import (
    ReviewDataError,
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


def test_extract_verdicts_ignores_fenced_and_quoted_examples(tmp_path):
    p = tmp_path / "r.md"
    p.write_text(
        "```markdown\nverdict: FAIL\n```\n"
        "> verdict: FAIL\n"
        "verdict: PASS\n",
        encoding="utf-8",
    )
    assert extract_verdicts(p) == ["PASS"]


def test_extract_verdicts_ignores_shorter_fence_inside_block(tmp_path):
    p = tmp_path / "r.md"
    p.write_text(
        "````markdown\nverdict: PASS\n```\nverdict: PASS\n````\n"
        "verdict: FAIL\n",
        encoding="utf-8",
    )
    assert extract_verdicts(p) == ["FAIL"]


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


def test_regression_rounds_ignores_fenced_round_header(tmp_path):
    p = tmp_path / "r.md"
    p.write_text(
        "```markdown\n## Round 99\nverdict: FAIL\n```\n"
        "## Round 2\nverdict: PASS\n",
        encoding="utf-8",
    )
    assert regression_rounds(p) == 2


# --- disposition_stats ---

def test_disposition_stats_counts(tmp_path):
    p = tmp_path / "task.md"
    p.write_text(
        "---\ntid: t001\n---\n\n"
        "## Review 处置\n\n### Round 1\n\n"
        "| finding_id | severity | status | rationale | fix_ref |\n"
        "|------------|----------|--------|-----------|---------|\n"
        "| t001_code_f001 | critical | 已修 | x | f:1 |\n"
        "| t001_code_f002 | minor | 遗留 | y | p001 |\n"
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
        "---\ntid: t001\n---\n\n"
        "## Review 处置\n\n"
        "| finding_id | severity | status | rationale | fix_ref |\n"
        "|------------|----------|--------|-----------|---------|\n"
        "| t000_code_f001 | critical | 已修 | x | f:1 |\n"
        "| t001_test_f001 | minor | 遗留 | y | p001 |\n",
        encoding="utf-8",
    )
    stats = disposition_stats(p)
    assert stats["已修"] == 0
    assert stats["遗留"] == 1


def test_disposition_stats_empty_when_no_table(tmp_path):
    p = tmp_path / "task.md"
    p.write_text("---\ntid: t001\n---\n无表\n", encoding="utf-8")
    stats = disposition_stats(p)
    assert sum(stats.values()) == 0


def test_disposition_stats_ignores_tables_outside_section(tmp_path):
    p = tmp_path / "task.md"
    p.write_text(
        "---\ntid: t001\n---\n\n"
        "## 其它\n"
        "| finding_id | status |\n|---|---|\n| t001_code_f001 | 撤回 |\n",
        encoding="utf-8",
    )
    assert sum(disposition_stats(p).values()) == 0


def test_disposition_stats_uses_header_columns(tmp_path):
    p = tmp_path / "task.md"
    p.write_text(
        "---\ntid: t001\n---\n\n## Review 处置\n"
        "| status | rationale | finding_id |\n"
        "|---|---|---|\n"
        "| 撤回 | x | t001_gen_f001 |\n",
        encoding="utf-8",
    )
    assert disposition_stats(p)["撤回"] == 1


def test_disposition_stats_accepts_escaped_and_code_span_pipes(tmp_path):
    p = tmp_path / "task.md"
    p.write_text(
        "---\ntid: t001\n---\n\n## Review 处置\n"
        "| finding_id | status | rationale |\n"
        "|---|---|---|\n"
        "| t001_gen_f001 | 已修 | `a|b` 和 a\\|b |\n",
        encoding="utf-8",
    )
    assert disposition_stats(p)["已修"] == 1


def test_disposition_stats_rejects_status_in_wrong_column(tmp_path):
    p = tmp_path / "task.md"
    p.write_text(
        "---\ntid: t001\n---\n\n## Review 处置\n"
        "| finding_id | status | rationale |\n"
        "|---|---|---|\n"
        "| t001_code_f001 | 待定 | 撤回 |\n",
        encoding="utf-8",
    )
    with pytest.raises(ReviewDataError, match="status 非法"):
        disposition_stats(p)


def test_disposition_stats_rejects_foreign_or_duplicate_finding(tmp_path):
    foreign = tmp_path / "foreign.md"
    foreign.write_text(
        "---\ntid: t001\n---\n\n## Review 处置\n"
        "| finding_id | status |\n|---|---|\n| t999_code_f001 | 已修 |\n",
        encoding="utf-8",
    )
    with pytest.raises(ReviewDataError, match="不属于"):
        disposition_stats(foreign)

    duplicate = tmp_path / "duplicate.md"
    duplicate.write_text(
        "---\ntid: t001\n---\n\n## Review 处置\n"
        "| finding_id | status |\n|---|---|\n"
        "| t001_code_f001 | 已修 |\n| t001_code_f001 | 撤回 |\n",
        encoding="utf-8",
    )
    with pytest.raises(ReviewDataError, match="重复"):
        disposition_stats(duplicate)


def test_disposition_stats_ignores_fenced_table(tmp_path):
    p = tmp_path / "task.md"
    p.write_text(
        "---\ntid: t001\n---\n\n## Review 处置\n"
        "```markdown\n| finding_id | status |\n|---|---|\n"
        "| t001_code_f001 | 撤回 |\n```\n",
        encoding="utf-8",
    )
    assert sum(disposition_stats(p).values()) == 0


def test_main_rejects_invalid_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(crs, "REPO_ROOT", tmp_path)
    task_dir = tmp_path / "docs" / "tasks" / "t001_x"
    task_dir.mkdir(parents=True)
    (task_dir / "task.md").write_text(
        "---\ntid: t001\nreview_level: typo\n---\n## Review 处置\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys, "argv", ["check_review_status.py", "--task-dir", str(task_dir)]
    )
    with pytest.raises(SystemExit) as exc:
        crs.main()
    assert exc.value.code == 2

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_review_status.py",
            "--task-dir",
            str(task_dir),
            "--max-review-round",
            "0",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        crs.main()
    assert exc.value.code == 2


def test_main_rejects_missing_task_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(crs, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["check_review_status.py", "--task-dir", "docs/tasks/missing"],
    )
    with pytest.raises(SystemExit) as exc:
        crs.main()
    assert exc.value.code == 2
