#!/usr/bin/env python3
"""check_review_status.py - 读 review 报告与处置表，输出 verdict、证据完整性与回归轮次（review 处置用）。

用法：
  python3 .repo_template/scripts/check_review_status.py --task-dir docs/tasks/t001_foo
  python3 .repo_template/scripts/check_review_status.py --task-dir docs/tasks/t001_foo --max-review-round 3

按 front matter 的 review_level 自动选报告文件：
  full   → review_code.md + review_test.md（两轴）
  single → review_general.md（一路）

输出（stdout，一行键值）：
  review_level=full|single
  code_verdict=PASS|FAIL|MISSING       # full 才有
  test_verdict=PASS|FAIL|MISSING       # full 才有
  general_verdict=PASS|FAIL|MISSING    # single 才有
  overall=PASS|FAIL|INCOMPLETE
  round=N               # 回归轮次：上轮 FAIL、修完重审才计；首轮不计
  max_review_round=N      # 来自 task.md review_limit；旧 task 默认 5
  max_verify_round=N      # 来自 task.md verify_limit
  next_action=finalize|fix_or_block|collect_reports|rerender_review|complete_disposition
  review_scope=ok|stale|missing|format_error  # 指纹比对状态；format_error=报告写了 reviewed_scope 但格式无法解析
"""

import argparse
import sys
from pathlib import Path

from repo_task.context import TaskDataError
from repo_task.monitoring import review_scope_fingerprint as monitoring_scope_fingerprint

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
from repo_task.review import (
    ReviewDataError, read, visible_markdown_lines, extract_verdicts, parse_front_matter, regression_rounds, extract_h2_lines, table_cells, is_separator_row, disposition_stats, disposed_findings, reported_findings, reviewed_scope, reviewed_scope_hint,
    VALID_REVIEW_LEVELS, evaluate_review, review_limits,
)

def current_scope_fingerprint(task_dir: Path, diff_anchor: str) -> str | None:
    """当前被审 diff 指纹；委托 monitoring.review_scope_fingerprint（单一真相源）。

    缺 diff_anchor 返回 None；git 失败返回 None 并打印 WARNING（F39），
    与「无 diff_anchor」区分。
    """
    if not diff_anchor:
        return None
    try:
        rel = task_dir.resolve().relative_to(REPO_ROOT.resolve())
    except ValueError:
        return None
    fingerprint = monitoring_scope_fingerprint(
        diff_anchor, rel.as_posix(), repo_root=REPO_ROOT
    )
    if not fingerprint:
        print(
            "WARNING: review scope 指纹无法计算（git 失败或 anchor 无效）；按不可验证处理",
            file=sys.stderr,
        )
        return None
    return fingerprint


def resolve_task_dir(value: str) -> Path:
    root = REPO_ROOT.resolve()
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as e:
        raise ReviewDataError(f"task directory must stay inside repository: {value}") from e
    if not candidate.is_dir():
        raise ReviewDataError(f"task directory does not exist: {value}")
    if not (candidate / "task.md").is_file():
        raise ReviewDataError(f"missing task.md in: {value}")
    return candidate


def main():
    p = argparse.ArgumentParser(
        description="读 review 报告与处置表输出 verdict / 回归轮次 / 撤回率",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--task-dir", required=True)
    p.add_argument("--max-review-round", type=int, help="只读兼容参数；不能提高已持久化上限")
    args = p.parse_args()
    try:
        if args.max_review_round is not None and args.max_review_round < 1:
            raise ReviewDataError("max-review-round must be at least 1")
        task_dir = resolve_task_dir(args.task_dir)
        fm = parse_front_matter(task_dir / "task.md")
        if fm.get("review_level", "full") not in VALID_REVIEW_LEVELS:
            raise ReviewDataError(f"review_level must be one of {sorted(VALID_REVIEW_LEVELS)}")
        limits = review_limits(fm)
        if args.max_review_round is not None and args.max_review_round > limits["review_limit"]:
            raise ReviewDataError("先经 task.py limits --review 持久化用户批准的上限")
        result = evaluate_review(task_dir, fm, current_scope_fingerprint(task_dir, fm.get("diff_anchor", "")))
    except (ReviewDataError, TaskDataError, OSError) as e:
        p.error(str(e))
    for key, value in result.items():
        if value != "":
            print(f"{key}={value}")
    print(f"max_review_round={args.max_review_round or limits['review_limit']}")
    print(f"max_verify_round={limits['verify_limit']}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
