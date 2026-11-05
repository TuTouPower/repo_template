"""specs_index 契约：严格 schema 与 finish 闭环。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task.finalization import check_finalization
from repo_task.specs_index import parse_specs_index, specs_index_errors

pytestmark = pytest.mark.contract

HEADER = "|slug|task 清单|最后更新时间|\n|---|---|---|\n"


def _root(tmp_path, index_text=HEADER, specs=(), note=None):
    root = tmp_path / "repo"
    (root / "docs/specs").mkdir(parents=True)
    if index_text is not None:
        (root / "docs/specs_index.md").write_text(index_text, encoding="utf-8")
    for slug in specs:
        (root / "docs/specs" / f"{slug}.md").write_text("# spec\n", encoding="utf-8")
    task_dir = root / "docs/tasks/t001_alpha"
    task_dir.mkdir(parents=True)
    progress = "# Task 进展\n"
    if note:
        progress += note + "\n"
    (task_dir / "progress.md").write_text(progress, encoding="utf-8")
    (task_dir / "task.md").write_text("# Task spec\n", encoding="utf-8")
    return root, task_dir


@pytest.mark.parametrize("index_text", [
    HEADER + "|feature|t001|2026-10-24|\n",
    "| slug | task 清单 | 最后更新时间 |\n| :--- | ---: | :---: |\n| feature | t001 | 2026-10-24 |\n",
    HEADER + "|feature|t001, t002 ,baseline, t010|2026-10-24|\n",
    HEADER,
])
def test_valid_specs_index_has_no_errors(tmp_path, index_text):
    root, _ = _root(tmp_path, index_text, specs=("feature",))
    assert specs_index_errors(root) == []


def test_parse_returns_slug_to_token_mapping(tmp_path):
    root, _ = _root(
        tmp_path,
        HEADER + "|feature|t001, t002 ,baseline|2026-10-24|\n",
        specs=("feature",),
    )
    assert parse_specs_index(root) == {"feature": ["t001", "t002", "baseline"]}


def test_empty_table_parses_to_empty_mapping(tmp_path):
    root, _ = _root(tmp_path)
    assert parse_specs_index(root) == {}


@pytest.mark.parametrize("index_text,specs,keyword", [
    (HEADER + "|feature|t001|2026-10-24|\n\n|slug|task 清单|最后更新时间|\n|---|---|---|\n|other|t002|2026-10-24|\n",
     ("feature", "other"), "多余的表"),
    ("|slug|验证方式|task 清单|最后固化时间|\n|---|---|---|---|\n|feature|API|t001|2026-10-24|\n",
     ("feature",), "3 列"),
    ("|slug|任务|时间|\n|---|---|---|\n|feature|t001|2026-10-24|\n",
     ("feature",), "列表头须为"),
    (HEADER + "|add-account|t001|2026-10-24|\n",
     ("add-account",), "slug 须匹配"),
    (HEADER + "|feature|t001|2026-10-24|\n|feature|t002|2026-10-24|\n",
     ("feature",), "slug 重复"),
    (HEADER + "|feature|t001|2026-10-24|\n",
     (), "不存在的 spec"),
    (HEADER + "|feature|-|2026-10-24|\n",
     ("feature",), "task 清单须为"),
    (HEADER + "|feature|t001，t002|2026-10-24|\n",
     ("feature",), "task 清单须为"),
    (HEADER + "|feature|t001|2026-13-45|\n",
     ("feature",), "非法日期"),
    (HEADER + "|feature|t001|2026-10-24T00:00:00+08:00|\n",
     ("feature",), "YYYY-MM-DD"),
    (HEADER + "|`feature`|t001|2026-10-24|\n",
     ("feature",), "反引号"),
    ("|slug|task 清单|最后更新时间|\n|===|===|===|\n|feature|t001|2026-10-24|\n",
     ("feature",), "分隔符"),
    (HEADER + "|feature|t001, t001|2026-10-24|\n",
     ("feature",), "token 重复"),
    (HEADER + "|feature|baseline, baseline|2026-10-24|\n",
     ("feature",), "token 重复"),
    ("只有说明文字\n", ("feature",), "缺 spec 清单表"),
])
def test_invalid_specs_index_reports_violation(tmp_path, index_text, specs, keyword):
    root, _ = _root(tmp_path, index_text, specs=specs)
    errors = specs_index_errors(root)
    assert errors
    assert any(keyword in error for error in errors)


def test_missing_index_file_is_a_violation(tmp_path):
    root, _ = _root(tmp_path, index_text=None)
    errors = specs_index_errors(root)
    assert any("缺 docs/specs_index.md" in error for error in errors)


def test_spec_table_after_leading_explanatory_table_is_fully_validated(tmp_path):
    text = (
        "## 验证方式分类\n\n"
        "|方式|含义|验证手段|\n|---|---|---|\n|**API**|数据层|curl|\n\n"
        "## spec 清单\n\n"
        + HEADER
        + "|add-account-catalog|-|2026-13-45|\n"
        + "|feature|t001，t002|2026-10-24|\n"
        + "|feature|t003|2026-10-24|\n"
    )
    root, _ = _root(tmp_path, text, specs=("feature",))
    errors = specs_index_errors(root)
    assert sum(1 for error in errors if "多余的表" in error) == 1
    assert any("第 3–5 行是多余的表" in error for error in errors)
    assert not any("表外出现" in error for error in errors)
    assert any("slug 须匹配" in error for error in errors)
    assert any("task 清单须为" in error for error in errors)
    assert any("slug 重复" in error for error in errors)
    assert any("非法日期" in error for error in errors)


def test_wide_table_reports_header_once_and_validates_first_column(tmp_path):
    text = (
        "|slug|验证方式|task 清单|最后固化时间|\n"
        "|---|---|---|---|\n"
        "|add-account-catalog|API|t001|2026-10-24|\n"
        "|feature|API|t001|2026-10-24|\n"
    )
    root, _ = _root(tmp_path, text, specs=("feature",))
    errors = specs_index_errors(root)
    assert sum(1 for error in errors if "表头须为 3 列" in error) == 1
    assert not any("数据行须为 3 列" in error for error in errors)
    assert not any("数据行列数须与表头一致" in error for error in errors)
    assert any("slug 须匹配" in error for error in errors)


def test_row_column_count_compared_to_header(tmp_path):
    root, _ = _root(
        tmp_path, HEADER + "|feature|t001|2026-10-24|extra|\n",
        specs=("feature",),
    )
    errors = specs_index_errors(root)
    assert any("数据行列数须与表头一致" in error for error in errors)
    assert not any("数据行须为 3 列" in error for error in errors)


def test_fenced_code_and_html_comments_are_not_tables(tmp_path):
    text = (
        "说明\n\n```\n|fake|t001|2026-10-24|\n```\n\n"
        "<!--\n|hidden|t001|2026-10-24|\n-->\n\n"
        + HEADER + "|feature|t001|2026-10-24|\n"
    )
    root, _ = _root(tmp_path, text, specs=("feature",))
    assert specs_index_errors(root) == []


def test_multiple_violations_are_reported_once_with_line_numbers(tmp_path):
    text = (
        HEADER
        + "|bad-slug|-|2026-13-45|\n"
        + "|feature|t001，t002|not-a-date|\n"
        + "|feature|t003|2026-10-24|\n"
    )
    root, _ = _root(tmp_path, text, specs=("feature",))
    errors = specs_index_errors(root)
    assert len(errors) >= 6
    assert all(re.search(r"docs/specs_index\.md:\d+:", error) for error in errors)
    assert any("docs/specs_index.md:4:" in error for error in errors)
    with pytest.raises(ValueError, match="契约违规"):
        parse_specs_index(root)


def test_current_tid_in_index_passes_without_exemption(tmp_path):
    root, task_dir = _root(
        tmp_path, HEADER + "|feature|t001|2026-10-24|\n", specs=("feature",)
    )
    check_finalization(root, "t001", task_dir)


def test_not_applicable_exemption_passes(tmp_path):
    root, task_dir = _root(
        tmp_path, HEADER, specs=(),
        note="- specs_index: not_applicable: 无长期 spec 变更",
    )
    check_finalization(root, "t001", task_dir)


def test_missing_inclusion_and_exemption_fails(tmp_path):
    root, task_dir = _root(tmp_path, HEADER, specs=())
    with pytest.raises(ValueError, match="未进入 specs_index"):
        check_finalization(root, "t001", task_dir)


def test_historical_violation_fails_current_finish(tmp_path):
    root, task_dir = _root(
        tmp_path,
        HEADER + "|feature|t999|2026-10-24|\n",
        specs=(),
        note="- specs_index: not_applicable: 无长期 spec 变更",
    )
    with pytest.raises(ValueError, match="不存在的 spec"):
        check_finalization(root, "t001", task_dir)
