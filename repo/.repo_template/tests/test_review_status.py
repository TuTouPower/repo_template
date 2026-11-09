"""task.py review-status 门禁测试。"""
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task import review_status as crs
from repo_task.context import RepoContext
from repo_task.review import (
    AC_ROW_RE,
    ReviewDataError,
    disposition_stats,
    extract_verdicts,
    parse_front_matter,
    regression_rounds,
    reported_findings,
    reviewed_scope,
    reviewed_scope_hint,
)
from review_support import ac_review_section

pytestmark = pytest.mark.contract


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


def test_extract_verdicts_info_string_fence_is_content_not_close(tmp_path):
    """带 info string 的同宽围栏行是内容：不提前闭合外层 code block。"""
    p = tmp_path / "r.md"
    p.write_text(
        "```markdown\n```python\nreviewed_scope: ffffffffffffffff\nverdict: FAIL\n```\n"
        "reviewed_scope: 0123456789abcdef\nverdict: PASS\n",
        encoding="utf-8",
    )
    assert extract_verdicts(p) == ["PASS"]
    assert reviewed_scope(p) == "0123456789abcdef"


def test_reviewed_scope_ignores_fenced_example(tmp_path):
    """fenced 示例中的 reviewed_scope 不算声明；取可见行最后一条。"""
    p = tmp_path / "r.md"
    p.write_text(
        "reviewed_scope: 0123456789abcdef\n"
        "verdict: PASS\n\n"
        "```markdown\nreviewed_scope: ffffffffffffffff\n```\n",
        encoding="utf-8",
    )
    assert reviewed_scope(p) == "0123456789abcdef"


def test_reviewed_scope_hint_ignores_fenced_only_mention(tmp_path):
    p = tmp_path / "r.md"
    p.write_text(
        "```markdown\nreviewed_scope: not-hex\n```\nverdict: PASS\n",
        encoding="utf-8",
    )
    assert reviewed_scope(p) is None
    assert reviewed_scope_hint(p) is False


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


def test_main_rejects_invalid_review_level(tmp_path, monkeypatch, capsys):
    """progress.md 中 review_level 非法时，review-status 拦截并以非零码退出。"""
    monkeypatch.chdir(tmp_path)
    task_dir = tmp_path / "docs" / "tasks" / "t001_x"
    task_dir.mkdir(parents=True)
    (task_dir / "progress.md").write_text(
        "---\ntid: t001\nreview_level: typo\n---\n## Review 处置\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys, "argv", ["task.py", "--task-dir", str(task_dir)]
    )
    with pytest.raises(SystemExit) as exc:
        crs.main()
    assert exc.value.code == 1
    assert "review_level must be one of" in capsys.readouterr().err


def test_main_rejects_missing_task_directory(tmp_path, monkeypatch, capsys):
    """--task-dir 指向不存在的目录时，review-status 拦截并以非零码退出。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["task.py", "--task-dir", "docs/tasks/missing"],
    )
    with pytest.raises(SystemExit) as exc:
        crs.main()
    assert exc.value.code == 1
    assert "task directory does not exist" in capsys.readouterr().err


# --- DS L283：resolve_task_dir 仓库包含性防护的越界场景 ---
def test_main_rejects_relative_task_dir_escaping_repository(tmp_path, monkeypatch, capsys):
    """--task-dir ../../etc：resolve 后越出仓库根，明确拒绝。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs" / "tasks").mkdir(parents=True)
    monkeypatch.setattr(sys, "argv", ["task.py", "--task-dir", "../../etc"])
    with pytest.raises(SystemExit) as exc:
        crs.main()
    assert exc.value.code == 1
    assert "inside repository" in capsys.readouterr().err


def test_resolve_task_dir_rejects_absolute_outside_path(tmp_path):
    """仓外绝对路径即便含 progress.md 也不得被解析为 task 目录。"""
    repo = RepoContext.for_root(tmp_path / "repo")
    (tmp_path / "repo" / "docs" / "tasks").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "progress.md").write_text("---\ntid: t001\n---\n", encoding="utf-8")
    with pytest.raises(ReviewDataError, match="inside repository"):
        crs.resolve_task_dir(str(outside), repo=repo)


def test_resolve_task_dir_rejects_internal_symlink_to_external_dir(tmp_path):
    """repo 内软链指向仓外目录：resolve 后越界，拒绝。"""
    repo = RepoContext.for_root(tmp_path / "repo")
    task_root = tmp_path / "repo" / "docs" / "tasks"
    task_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "progress.md").write_text("---\ntid: t001\n---\n", encoding="utf-8")
    (task_root / "t001_link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ReviewDataError, match="inside repository"):
        crs.resolve_task_dir("docs/tasks/t001_link", repo=repo)


def test_current_scope_fingerprint_returns_none_for_outside_task_dir(tmp_path):
    """越界 task 目录的指纹不可验证，返回 None 而非伪造指纹。"""
    repo = RepoContext.for_root(tmp_path / "repo")
    (tmp_path / "repo" / "docs" / "tasks").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    assert crs.current_scope_fingerprint(outside, "abc", repo=repo) is None


def test_main_reports_missing_disposition(tmp_path, monkeypatch, capsys):
    """报告含但处置表未处置的 finding → overall=INCOMPLETE 并列出漏记。"""
    task_dir = tmp_path / "docs" / "tasks" / "t001_x"
    task_dir.mkdir(parents=True)
    (task_dir / "progress.md").write_text(
        "---\ntid: t001\nreview_level: single\n---\n## Review 处置\n\n"
        "| finding_id | severity | status | rationale | fix_ref |\n"
        "|---|---|---|---|---|\n"
        "| t001_gen_f001 | minor | 已修 | x | f:1 |\n",
        encoding="utf-8",
    )
    (task_dir / "task.md").write_text(SPEC_TICKED, encoding="utf-8")
    (task_dir / "review_general.md").write_text(
        "verdict: PASS\n\n## 结论\n\n" + ac_review_section(SPEC_TICKED)
        + "| finding_id | severity | 说明 |\n|---|---|---|\n"
        "| t001_gen_f001 | minor | a |\n"
        "| t001_gen_f002 | minor | b |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys, "argv", ["task.py", "--task-dir", str(task_dir)]
    )
    crs.main()
    out = capsys.readouterr().out
    assert "overall=INCOMPLETE" in out
    assert "missing_disposition=t001_gen_f002" in out


def test_main_reports_missing_disposition_for_heading_findings(tmp_path, monkeypatch, capsys):
    """prompt 模板标题格式 finding 未处置 → 与表行格式同样进 missing_disposition。"""
    task_dir = _scope_task_dir(tmp_path, monkeypatch, f"reviewed_scope: {SCOPE}\n")
    report = task_dir / "review_general.md"
    report.write_text(
        report.read_text()
        + "\n## Findings\n\n"
        + "### t001_gen_f001 - 标题一\n\n- 严重度：important\n\n"
        + "### t001_gen_f002 - 标题二\n\n- 严重度：minor\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(crs, "current_scope_fingerprint", lambda *args, **kwargs: SCOPE)
    monkeypatch.setattr(
        sys, "argv", ["task.py", "--task-dir", str(task_dir)]
    )
    crs.main()
    out = capsys.readouterr().out
    assert "overall=INCOMPLETE" in out
    assert "missing_disposition=t001_gen_f001,t001_gen_f002" in out
    assert "next_action=complete_disposition" in out


def test_disposition_legacy_requires_fix_ref(tmp_path):
    """status=遗留 缺 fix_ref（- 或空）→ 拒绝。"""
    p = tmp_path / "task.md"
    p.write_text(
        "---\ntid: t001\n---\n\n## Review 处置\n\n"
        "| finding_id | severity | status | rationale | fix_ref |\n"
        "|---|---|---|---|---|\n"
        "| t001_code_f001 | critical | 遗留 | x | - |\n",
        encoding="utf-8",
    )
    with pytest.raises(ReviewDataError, match="fix_ref"):
        disposition_stats(p)


def test_disposition_legacy_rejects_free_text_fix_ref(tmp_path):
    """遗留 fix_ref 填 TODO/later 等任意文本 → 拒绝（须 pNNN 或 tNNN）。"""
    p = tmp_path / "task.md"
    p.write_text(
        "---\ntid: t001\n---\n\n## Review 处置\n\n"
        "| finding_id | severity | status | rationale | fix_ref |\n"
        "|---|---|---|---|---|\n"
        "| t001_code_f001 | minor | 遗留 | x | TODO |\n",
        encoding="utf-8",
    )
    with pytest.raises(ReviewDataError, match="fix_ref 非法"):
        disposition_stats(p)


def test_disposition_legacy_requires_fix_ref_column(tmp_path):
    """处置表有遗留行但缺 fix_ref 列 → 拒绝。"""
    p = tmp_path / "task.md"
    p.write_text(
        "---\ntid: t001\n---\n\n## Review 处置\n\n"
        "| finding_id | status | rationale |\n|---|---|---|\n"
        "| t001_code_f001 | 遗留 | x |\n",
        encoding="utf-8",
    )
    with pytest.raises(ReviewDataError, match="fix_ref 列"):
        disposition_stats(p)


def test_main_keeps_fail_when_disposition_missing(tmp_path, monkeypatch, capsys):
    """首轮 FAIL（报告含 blocker、处置表未填）→ overall 保留 FAIL，不降 INCOMPLETE。"""
    task_dir = tmp_path / "docs" / "tasks" / "t001_x"
    task_dir.mkdir(parents=True)
    (task_dir / "progress.md").write_text(
        "---\ntid: t001\nreview_level: single\ndiff_anchor: "
        + "0" * 40 + "\n---\n## Review 处置\n",
        encoding="utf-8",
    )
    (task_dir / "review_general.md").write_text(
        "verdict: FAIL\n\n| finding_id | severity | 说明 |\n|---|---|---|\n"
        "| t001_gen_f001 | critical | x |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        crs, "current_scope_fingerprint", lambda *args, **kwargs: SCOPE
    )
    monkeypatch.setattr(
        sys, "argv", ["task.py", "--task-dir", str(task_dir)]
    )
    crs.main()
    out = capsys.readouterr().out
    assert "overall=FAIL" in out
    assert "missing_disposition=t001_gen_f001" in out


def test_reported_findings_ignores_foreign_task(tmp_path):
    """报告引用其它 task finding（t099_...）→ 不进入本 task 的 missing 集合。"""
    p = tmp_path / "review_general.md"
    p.write_text(
        "verdict: PASS\n\n"
        "| finding_id | 说明 |\n|---|---|\n"
        "| t001_gen_f001 | 本 task |\n"
        "| t099_code_f001 | 历史引用 |\n",
        encoding="utf-8",
    )
    assert reported_findings("t001", p) == {"t001_gen_f001"}


def test_reported_findings_accepts_heading_format(tmp_path):
    """prompt 模板标题格式 finding 与表行同等进入集合；代码块/引用块/正文提及不算。"""
    p = tmp_path / "review_general.md"
    p.write_text(
        "# Task review t001\n\n"
        "## Findings\n\n"
        "### t001_gen_f001 - 标题一\n\n- 严重度：important\n\n"
        "#### t001_gen_f002 - 二级标题\n\n"
        "```markdown\n### t001_gen_f003 - 代码块示例\n```\n\n"
        "> ### t001_gen_f004 - 引用块\n\n"
        "### t001_gen_f005\n\n"
        "### t099_code_f001 - 历史 task 标题\n\n"
        "正文提及 t001_gen_f006 不计。\n\n"
        "| finding_id | severity |\n|---|---|\n"
        "| t001_gen_f007 | minor |\n",
        encoding="utf-8",
    )
    assert reported_findings("t001", p) == {
        "t001_gen_f001", "t001_gen_f002", "t001_gen_f005", "t001_gen_f007",
    }


SCOPE = "abcdef0123456789"
SPEC_TICKED = (
    "# Spec\n\n## 契约区\n\n### 验收标准\n\n"
    "- [x] AC-001：甲\n- [x] [deploy] AC-002：乙\n"
)


def _scope_task_dir(tmp_path, monkeypatch, scope_line):
    task_dir = tmp_path / "docs/tasks/t001_x"
    task_dir.mkdir(parents=True)
    (task_dir / "progress.md").write_text(
        "---\ntid: t001\nreview_level: single\ndiff_anchor: "
        + "0" * 40 + "\n---\n## Review 处置\n",
        encoding="utf-8",
    )
    (task_dir / "task.md").write_text(SPEC_TICKED, encoding="utf-8")
    (task_dir / "review_general.md").write_text(
        "verdict: PASS\n\n" + ac_review_section(SPEC_TICKED) + scope_line,
        encoding="utf-8",
    )
    return task_dir


def test_main_pass_requires_matching_scope(tmp_path, monkeypatch, capsys):
    """PASS + 报告指纹与当前一致 → overall=PASS, review_scope=ok。"""
    task_dir = _scope_task_dir(
        tmp_path, monkeypatch, f"reviewed_scope: {SCOPE}\n"
    )
    monkeypatch.setattr(
        crs, "current_scope_fingerprint", lambda *args, **kwargs: SCOPE
    )
    monkeypatch.setattr(
        sys, "argv", ["task.py", "--task-dir", str(task_dir)]
    )
    crs.main()
    out = capsys.readouterr().out
    assert "overall=PASS" in out
    assert "review_scope=ok" in out


def test_main_pass_stale_scope_fails(tmp_path, monkeypatch, capsys):
    """PASS 后 diff 变（指纹不等）→ overall=INCOMPLETE, review_scope=stale。"""
    task_dir = _scope_task_dir(
        tmp_path, monkeypatch, f"reviewed_scope: {SCOPE}\n"
    )
    monkeypatch.setattr(
        crs, "current_scope_fingerprint", lambda *args, **kwargs: "0" * 16
    )
    monkeypatch.setattr(
        sys, "argv", ["task.py", "--task-dir", str(task_dir)]
    )
    crs.main()
    out = capsys.readouterr().out
    assert "overall=INCOMPLETE" in out
    assert "review_scope=stale" in out


def test_main_pass_missing_scope_fails(tmp_path, monkeypatch, capsys):
    """报告缺 reviewed_scope → overall=INCOMPLETE, review_scope=missing。"""
    task_dir = _scope_task_dir(tmp_path, monkeypatch, "verdict: PASS\n")
    monkeypatch.setattr(
        crs, "current_scope_fingerprint", lambda *args, **kwargs: SCOPE
    )
    monkeypatch.setattr(
        sys, "argv", ["task.py", "--task-dir", str(task_dir)]
    )
    crs.main()
    out = capsys.readouterr().out
    assert "overall=INCOMPLETE" in out
    assert "review_scope=missing" in out


def test_main_scope_tolerates_backtick_wrapped(tmp_path, monkeypatch, capsys):
    """指纹行被反引号整行包裹（历史误抄）→ 宽容解析自愈为 ok，不再误报。"""
    task_dir = _scope_task_dir(
        tmp_path, monkeypatch, f"`reviewed_scope: {SCOPE}`\n"
    )
    monkeypatch.setattr(
        crs, "current_scope_fingerprint", lambda *args, **kwargs: SCOPE
    )
    monkeypatch.setattr(
        sys, "argv", ["task.py", "--task-dir", str(task_dir)]
    )
    crs.main()
    out = capsys.readouterr().out
    assert "overall=PASS" in out
    assert "review_scope=ok" in out


@pytest.mark.parametrize("prefix", ["**", "- "])
def test_main_scope_tolerates_bold_and_list_prefix(
    prefix, tmp_path, monkeypatch, capsys
):
    """加粗或列表前缀修饰 → 宽容解析自愈。"""
    task_dir = _scope_task_dir(
        tmp_path, monkeypatch, f"{prefix}reviewed_scope: {SCOPE}\n"
    )
    monkeypatch.setattr(
        crs, "current_scope_fingerprint", lambda *args, **kwargs: SCOPE
    )
    monkeypatch.setattr(
        sys, "argv", ["task.py", "--task-dir", str(task_dir)]
    )
    crs.main()
    out = capsys.readouterr().out
    assert "review_scope=ok" in out


def test_main_scope_reports_format_error(tmp_path, monkeypatch, capsys):
    """报告含 reviewed_scope 但格式无法解析 → format_error，区分于 missing。"""
    task_dir = _scope_task_dir(
        tmp_path, monkeypatch, "reviewed_scope: not-hex\n"
    )
    monkeypatch.setattr(
        crs, "current_scope_fingerprint", lambda *args, **kwargs: SCOPE
    )
    monkeypatch.setattr(
        sys, "argv", ["task.py", "--task-dir", str(task_dir)]
    )
    crs.main()
    out = capsys.readouterr().out
    assert "overall=INCOMPLETE" in out
    assert "review_scope=format_error" in out


def test_checker_reads_persisted_budget_and_refuses_implicit_raise(tmp_path, monkeypatch, capsys):
    task_dir = _scope_task_dir(tmp_path, monkeypatch, f'reviewed_scope: {SCOPE}\n')
    path = task_dir / 'progress.md'
    path.write_text(path.read_text().replace('review_level: single', 'review_level: single\nreview_limit: 8\nverify_limit: 7'))
    monkeypatch.setattr(crs, 'current_scope_fingerprint', lambda *args, **kwargs: SCOPE)
    monkeypatch.setattr(sys, 'argv', ['task.py', '--task-dir', str(task_dir)])
    crs.main()
    out = capsys.readouterr().out
    assert 'max_review_round=8' in out
    assert 'max_verify_round=7' in out
    assert 'next_action=finalize' in out


@pytest.mark.parametrize('defect,action', [
    ('missing_report', 'collect_reports'), ('stale', 'rerender_review'),
    ('missing_disposition', 'complete_disposition'),
])
def test_incomplete_has_explicit_recovery_action(tmp_path, monkeypatch, capsys, defect, action):
    task_dir = _scope_task_dir(tmp_path, monkeypatch, f'reviewed_scope: {SCOPE}\n')
    report = task_dir / 'review_general.md'
    if defect == 'missing_report':
        report.unlink()
    elif defect == 'missing_disposition':
        report.write_text(report.read_text() + '\n|t001_gen_f001|important|bug|\n')
    monkeypatch.setattr(crs, 'current_scope_fingerprint', lambda *args, **kwargs: '0' * 16 if defect == 'stale' else SCOPE)
    monkeypatch.setattr(sys, 'argv', ['task.py', '--task-dir', str(task_dir)])
    crs.main()
    out = capsys.readouterr().out
    assert 'overall=INCOMPLETE' in out
    assert f'next_action={action}' in out


# --- AC 复验门禁（J3）：最后一轮 AC 小节 × task.md 的 AC 编号与打勾状态 ---

SPEC_PARTLY_TICKED = SPEC_TICKED.replace("- [x] [deploy] AC-002", "- [ ] [deploy] AC-002")
ROW_1 = "- AC-001：TICK-VALID，re_verified。重跑命令，断言真实。"
ROW_2 = "- AC-002：TICK-VALID，trust_prior。依赖部署态证据。"


def _report(rows, verdict="PASS", round_header="## Round 1", heading="### AC 复验方式"):
    body = "\n".join(rows)
    return (
        f"{round_header}\nreviewed_scope: {SCOPE}\n\n{heading}\n\n{body}\n\n"
        f"coverage = 1 / 2\n\nverdict: {verdict}\n"
    )


def _ac_task(tmp_path, monkeypatch, reports, spec=SPEC_TICKED, level="single"):
    """reports 为单个文本（single / 两路同文）或 {code, test} 字典；spec=None 表示不写 task.md。"""
    task_dir = tmp_path / "docs/tasks/t001_x"
    task_dir.mkdir(parents=True)
    (task_dir / "progress.md").write_text(
        f"---\ntid: t001\nreview_level: {level}\ndiff_anchor: " + "0" * 40
        + "\n---\n## Review 处置\n",
        encoding="utf-8",
    )
    if spec is not None:
        (task_dir / "task.md").write_text(spec, encoding="utf-8")
    if level == "single":
        (task_dir / "review_general.md").write_text(reports, encoding="utf-8")
    else:
        if isinstance(reports, str):
            reports = {"code": reports, "test": reports}
        for name, text in reports.items():
            (task_dir / f"review_{name}.md").write_text(text, encoding="utf-8")
    monkeypatch.setattr(crs, "current_scope_fingerprint", lambda *args, **kwargs: SCOPE)
    return task_dir


def _check(task_dir, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["task.py", "--task-dir", str(task_dir)])
    crs.main()
    return dict(
        line.split("=", 1) for line in capsys.readouterr().out.splitlines() if "=" in line
    )


def test_ac_gate_all_tick_valid_passes(tmp_path, monkeypatch, capsys):
    task_dir = _ac_task(tmp_path, monkeypatch, _report([ROW_1, ROW_2]))
    out = _check(task_dir, monkeypatch, capsys)
    assert out["overall"] == "PASS"
    assert out["ac_review"] == "ok"
    assert out["next_action"] == "finalize"


def test_ac_gate_tick_void_fails_even_when_verdict_is_pass(tmp_path, monkeypatch, capsys):
    void = "- AC-002：TICK-VOID，trust_prior。无部署证据。"
    task_dir = _ac_task(tmp_path, monkeypatch, _report([ROW_1, void], verdict="PASS"))
    out = _check(task_dir, monkeypatch, capsys)
    assert out["general_verdict"] == "PASS"
    assert out["overall"] == "FAIL"
    assert out["ac_review"] == "void"
    assert out["next_action"] == "fix_or_block"
    assert "AC-002" in out["ac_review_detail"]


def test_ac_gate_tick_void_with_fail_verdict_stays_fail(tmp_path, monkeypatch, capsys):
    void = "- AC-001：TICK-VOID，re_verified。实现缺失。"
    task_dir = _ac_task(tmp_path, monkeypatch, _report([void, ROW_2], verdict="FAIL"))
    out = _check(task_dir, monkeypatch, capsys)
    assert out["overall"] == "FAIL"
    assert out["ac_review"] == "void"


def test_ac_gate_void_wins_over_stale_scope(tmp_path, monkeypatch, capsys):
    """VOID 等同报告 FAIL：不因 scope 过期降为 INCOMPLETE（与 verdict FAIL 一致）。"""
    void = "- AC-001：TICK-VOID，re_verified。实现缺失。"
    task_dir = _ac_task(tmp_path, monkeypatch, _report([void, ROW_2]))
    monkeypatch.setattr(crs, "current_scope_fingerprint", lambda *args, **kwargs: "0" * 16)
    out = _check(task_dir, monkeypatch, capsys)
    assert out["overall"] == "FAIL"
    assert out["review_scope"] == "stale"


@pytest.mark.parametrize("rows,needle", [
    ([ROW_1], "AC-002"),                                              # 缺行
    ([ROW_1, ROW_2, "- AC-009：TICK-VALID，re_verified。多出。"], "AC-009"),  # 多出未知 AC
    ([ROW_1, ROW_1, ROW_2], "AC-001"),                                # 重复
    ([ROW_1, "- AC-002：已复验，通过。"], "AC-002"),                  # 格式无法解析
    ([ROW_1, "- AC-002：TICK-OK，re_verified。"], "AC-002"),          # 未知标签
    ([ROW_1, "- AC-002：TICK-VALID，guessed。"], "AC-002"),           # 未知复验方式
    ([], "AC-001"),                                                   # 小节空
])
def test_ac_gate_structural_defects_are_incomplete(tmp_path, monkeypatch, capsys, rows, needle):
    task_dir = _ac_task(tmp_path, monkeypatch, _report(rows))
    out = _check(task_dir, monkeypatch, capsys)
    assert out["overall"] == "INCOMPLETE"
    assert out["ac_review"] == "incomplete"
    assert out["next_action"] == "complete_ac_review"
    assert needle in out["ac_review_detail"]


def test_ac_gate_missing_section_is_incomplete(tmp_path, monkeypatch, capsys):
    task_dir = _ac_task(tmp_path, monkeypatch, f"reviewed_scope: {SCOPE}\n\nverdict: PASS\n")
    out = _check(task_dir, monkeypatch, capsys)
    assert out["overall"] == "INCOMPLETE"
    assert out["next_action"] == "complete_ac_review"
    assert "AC 复验方式" in out["ac_review_detail"]


def test_ac_gate_unticked_task_ac_cannot_pass(tmp_path, monkeypatch, capsys):
    """task.md 仍有未打勾 AC：报告写 TICK-VALID 自相矛盾，不得 PASS。"""
    task_dir = _ac_task(tmp_path, monkeypatch, _report([ROW_1, ROW_2]), spec=SPEC_PARTLY_TICKED)
    out = _check(task_dir, monkeypatch, capsys)
    assert out["overall"] == "INCOMPLETE"
    assert out["next_action"] == "complete_ac_review"
    assert "AC-002" in out["ac_review_detail"]


def test_ac_gate_unticked_task_ac_marked_void_fails(tmp_path, monkeypatch, capsys):
    void = "- AC-002：TICK-VOID，trust_prior。task.md 未打勾。"
    task_dir = _ac_task(tmp_path, monkeypatch, _report([ROW_1, void]), spec=SPEC_PARTLY_TICKED)
    out = _check(task_dir, monkeypatch, capsys)
    assert out["overall"] == "FAIL"
    assert out["ac_review"] == "void"


def test_ac_gate_reads_only_last_round(tmp_path, monkeypatch, capsys):
    void = "- AC-001：TICK-VOID，re_verified。实现缺失。"
    first = _report([void, ROW_2], verdict="FAIL")
    second = _report([ROW_1, ROW_2], round_header="## Round 2 (2026-11-03T10:00:00+08:00)")
    task_dir = _ac_task(tmp_path, monkeypatch, first + "\n" + second)
    out = _check(task_dir, monkeypatch, capsys)
    assert out["overall"] == "PASS"
    assert out["ac_review"] == "ok"


def test_ac_gate_last_round_must_relist_all_acs(tmp_path, monkeypatch, capsys):
    """Round 1 完整合格也不能代替 Round 2：最后一轮无 AC 小节 → INCOMPLETE。"""
    first = _report([ROW_1, ROW_2])
    second = "## Round 2 (2026-11-03T10:00:00+08:00)\nreviewed_scope: " + SCOPE + "\n\nverdict: PASS\n"
    task_dir = _ac_task(tmp_path, monkeypatch, first + "\n" + second)
    out = _check(task_dir, monkeypatch, capsys)
    assert out["overall"] == "INCOMPLETE"
    assert out["next_action"] == "complete_ac_review"


def test_ac_gate_last_round_void_overrides_earlier_valid(tmp_path, monkeypatch, capsys):
    void = "- AC-002：TICK-VOID，re_verified。回归后测试假绿。"
    first = _report([ROW_1, ROW_2])
    second = _report([ROW_1, void], round_header="## Round 2 (2026-11-03T10:00:00+08:00)")
    task_dir = _ac_task(tmp_path, monkeypatch, first + "\n" + second)
    out = _check(task_dir, monkeypatch, capsys)
    assert out["overall"] == "FAIL"


@pytest.mark.parametrize("row1,row2", [
    ("- AC-001: TICK-VALID, re_verified. 证据", "- AC-002: TICK-VALID, trust_prior. 证据"),
    ("- AC-001：TICK-VALID，re_verified。证据", "- AC-002: TICK-VALID，trust_prior. 证据"),
    ("- AC-001 ：TICK-VALID ， re_verified。证据", "- AC-002:TICK-VALID,trust_prior"),
    ("* AC-001：TICK-VALID，re_verified。证据", "- **AC-002**：`TICK-VALID`，trust_prior。证据"),
])
def test_ac_gate_accepts_fullwidth_and_halfwidth_punctuation(
    tmp_path, monkeypatch, capsys, row1, row2
):
    task_dir = _ac_task(tmp_path, monkeypatch, _report([row1, row2]))
    out = _check(task_dir, monkeypatch, capsys)
    assert out["overall"] == "PASS"
    assert out["ac_review"] == "ok"


def test_ac_gate_ignores_rows_inside_code_blocks(tmp_path, monkeypatch, capsys):
    """代码块里的示例行不计入：既不能补全缺行，也不产生多出 AC。"""
    report = (
        f"## Round 1\nreviewed_scope: {SCOPE}\n\n### AC 复验方式\n\n"
        f"{ROW_1}\n\n```markdown\n{ROW_2}\n- AC-009：TICK-VOID，re_verified。示例\n```\n\n"
        "verdict: PASS\n"
    )
    task_dir = _ac_task(tmp_path, monkeypatch, report)
    out = _check(task_dir, monkeypatch, capsys)
    assert out["overall"] == "INCOMPLETE"
    assert "AC-002" in out["ac_review_detail"]
    assert "AC-009" not in out["ac_review_detail"]


@pytest.mark.parametrize("heading", ["## AC 复验方式", "### AC 复验方式", "#### AC 复验方式（逐条）"])
def test_ac_gate_accepts_section_heading_levels(tmp_path, monkeypatch, capsys, heading):
    task_dir = _ac_task(tmp_path, monkeypatch, _report([ROW_1, ROW_2], heading=heading))
    assert _check(task_dir, monkeypatch, capsys)["overall"] == "PASS"


def test_ac_gate_section_ends_at_next_heading(tmp_path, monkeypatch, capsys):
    """小节之后其它标题下的 AC 行不计入本小节。"""
    report = _report([ROW_1, ROW_2]).replace(
        "coverage = 1 / 2", "### 其它\n\n- AC-009：随笔，不属于 AC 小节"
    )
    task_dir = _ac_task(tmp_path, monkeypatch, report)
    assert _check(task_dir, monkeypatch, capsys)["overall"] == "PASS"


def test_ac_gate_full_level_checks_both_reports(tmp_path, monkeypatch, capsys):
    void = "- AC-002：TICK-VOID，re_verified。无有效测试。"
    ok = _report([ROW_1, ROW_2])
    task_dir = _ac_task(
        tmp_path, monkeypatch, {"code": ok, "test": _report([ROW_1, void])}, level="full",
    )
    out = _check(task_dir, monkeypatch, capsys)
    assert out["overall"] == "FAIL"
    assert "review_test.md" in out["ac_review_detail"]


def test_ac_gate_full_level_missing_section_in_one_report(tmp_path, monkeypatch, capsys):
    """full 级 code 与 test 报告都须含 AC 小节：任一缺失 → INCOMPLETE。"""
    bare = f"reviewed_scope: {SCOPE}\n\nverdict: PASS\n"
    task_dir = _ac_task(
        tmp_path, monkeypatch, {"code": _report([ROW_1, ROW_2]), "test": bare}, level="full",
    )
    out = _check(task_dir, monkeypatch, capsys)
    assert out["overall"] == "INCOMPLETE"
    assert out["next_action"] == "complete_ac_review"
    assert "review_test.md" in out["ac_review_detail"]


def test_ac_gate_full_level_all_valid_passes(tmp_path, monkeypatch, capsys):
    task_dir = _ac_task(tmp_path, monkeypatch, _report([ROW_1, ROW_2]), level="full")
    assert _check(task_dir, monkeypatch, capsys)["overall"] == "PASS"


def test_ac_gate_without_task_md_still_rejects_void_and_missing_rows(tmp_path, monkeypatch, capsys):
    """checker 单独运行且目录缺 task.md 时无法比对 AC 集合，但 VOID 与缺小节仍拦截。"""
    task_dir = _ac_task(tmp_path, monkeypatch, _report([ROW_1, ROW_2]), spec=None)
    out = _check(task_dir, monkeypatch, capsys)
    assert out["overall"] == "PASS"
    assert out["ac_review"] == "no_spec"
    (task_dir / "review_general.md").write_text(
        _report([ROW_1, "- AC-002：TICK-VOID，trust_prior。无证据。"]), encoding="utf-8"
    )
    assert _check(task_dir, monkeypatch, capsys)["overall"] == "FAIL"
    (task_dir / "review_general.md").write_text(_report([]), encoding="utf-8")
    assert _check(task_dir, monkeypatch, capsys)["overall"] == "INCOMPLETE"


def test_ac_gate_rejects_second_valid_report_when_other_is_missing(tmp_path, monkeypatch, capsys):
    """缺一份报告仍优先 collect_reports，AC 问题不抢占。"""
    task_dir = _ac_task(
        tmp_path, monkeypatch, {"code": _report([ROW_1])}, level="full",
    )
    out = _check(task_dir, monkeypatch, capsys)
    assert out["overall"] == "INCOMPLETE"
    assert out["next_action"] == "collect_reports"


@pytest.mark.parametrize("name", ["code_prompt.txt", "test_prompt.txt", "general_prompt.txt"])
def test_prompt_output_examples_parse_with_the_gate(name):
    """prompt 输出格式里的 AC 示例行必须能被门禁解析，防模板与 AC_ROW_RE 漂移。"""
    text = (SCRIPTS_DIR.parent / "docs/review_prompts" / name).read_text(encoding="utf-8")
    examples = [line for line in text.splitlines() if line.startswith("- AC-00")]
    assert [line.split("：")[1].split("，")[0] for line in examples] == ["TICK-VALID", "TICK-VOID"]
    for line in examples:
        assert AC_ROW_RE.match(line), line


@pytest.mark.parametrize("name", ["code_prompt.txt", "test_prompt.txt", "general_prompt.txt"])
def test_prompt_output_format_forbids_absolute_repo_root_and_home_paths(name):
    """输出格式模板不得写入 {repo_root} 或 {task_dir}，且须明确禁止开发者家目录绝对路径（Issue #89）。"""
    text = (SCRIPTS_DIR.parent / "docs/review_prompts" / name).read_text(encoding="utf-8")
    assert "- target：`git diff {diff_anchor}`" in text
    assert "- target：`git -C '{repo_root}' diff {diff_anchor}`" not in text
    assert "开发者家目录绝对路径" in text


def test_share_prompt_declares_privacy_gate():
    text = (SCRIPTS_DIR.parent / "docs/review_prompts/share_prompt.txt").read_text(encoding="utf-8")
    assert "路径隐私边界" in text
    assert "sanitize_review_paths" in text


@pytest.mark.parametrize(
    "leak_fragment",
    [
        "/".join(("", "Users", "alice", "worktree")),
        "/".join(("", "home", "bob", "worktree")),
        "C:\\" + "\\".join(("Users", "carol", "worktree")) + "\\",
        "C:" + "/".join(("", "Users", "dave", "worktree")),
        "/".join(("", "root", "work", "repo")),
    ],
)
def test_privacy_gate_blocks_developer_home_paths_in_review_reports(
    tmp_path, monkeypatch, capsys, leak_fragment
):
    report = f"- target：`git -C '{leak_fragment}' diff abc`\n\n" + _report([ROW_1, ROW_2])
    task_dir = _ac_task(tmp_path, monkeypatch, report)
    out = _check(task_dir, monkeypatch, capsys)
    assert out["overall"] == "INCOMPLETE"
    assert out["privacy_review"] == "leak"
    assert "review_general.md:1:" in out["privacy_review_detail"]
    assert out["next_action"] == "sanitize_review_paths"


def test_privacy_gate_allows_windows_system_profiles_only(tmp_path, monkeypatch, capsys):
    """A08：仅 Windows 系统配置目录（Public 等）免检；容器用户名不再全局豁免。"""
    public_win = "C:\\" + "\\".join(("Users", "Public", "shared"))
    report = (
        f"- target：`git diff abc`\n- 证据：`{public_win}`\n\n"
        + _report([ROW_1, ROW_2])
    )
    task_dir = _ac_task(tmp_path, monkeypatch, report)
    out = _check(task_dir, monkeypatch, capsys)
    assert out["overall"] == "PASS"
    assert out["privacy_review"] == "ok"
    assert out["next_action"] == "finalize"


@pytest.mark.parametrize(
    "container_path",
    [
        "/".join(("", "home", "cbrowser", "app")),
        "/".join(("", "home", "runner", "work")),
        "/".join(("", "root", ".config", "google-chrome", "Default")),
    ],
)
def test_privacy_gate_no_longer_exempts_container_usernames(
    tmp_path, monkeypatch, capsys, container_path
):
    """A08：取消仅凭用户名认定容器路径的全局放行。"""
    report = f"- target：`git diff abc`\n- 证据：`{container_path}`\n\n" + _report([ROW_1, ROW_2])
    task_dir = _ac_task(tmp_path, monkeypatch, report)
    out = _check(task_dir, monkeypatch, capsys)
    assert out["overall"] == "INCOMPLETE"
    assert out["privacy_review"] == "leak"
    assert out["next_action"] == "sanitize_review_paths"


@pytest.mark.parametrize(
    "boundary_path",
    [
        "/".join(("", "Users", "alice")),                      # 裸家目录（无尾随分隔符）
        "/".join(("", "home", "bob")),                         # 裸 Linux 家目录
        "C:" + "/".join(("", "Users", "carol")),               # Windows 正斜杠裸家目录
        "C:\\" + "\\".join(("Users", "dave")),                 # Windows 反斜杠裸家目录
        "/".join(("", "root")),                                # 裸 root 家目录
        "/".join(("", "Users", "alice", "proj")),              # 子路径
    ],
)
def test_privacy_gate_detects_bare_and_subpath_home_tokens(
    tmp_path, monkeypatch, capsys, boundary_path
):
    """A08：裸家目录、子路径、单双斜杠形式均得到确定结果。"""
    report = f"- 证据：`{boundary_path}`。\n\n" + _report([ROW_1, ROW_2])
    task_dir = _ac_task(tmp_path, monkeypatch, report)
    out = _check(task_dir, monkeypatch, capsys)
    assert out["privacy_review"] == "leak"
    assert out["next_action"] == "sanitize_review_paths"


def test_review_parse_front_matter_raises_on_missing_or_corrupt_file(tmp_path):
    """A05：review.parse_front_matter 不再把缺失或损坏元数据降级为 {}。"""
    with pytest.raises(ReviewDataError):
        parse_front_matter(tmp_path / "missing.md")
    bad = tmp_path / "corrupt.md"
    bad.write_text("---\ntid: t001\ntid: t001\n---\n", encoding="utf-8")
    with pytest.raises(ReviewDataError, match="front matter 重复键"):
        parse_front_matter(bad)
