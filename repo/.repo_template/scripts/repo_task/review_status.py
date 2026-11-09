"""review 状态门禁：读 review 报告与处置表，输出 verdict、证据完整性与回归轮次。

由 `task.py review-status --task-dir <task_dir>` 调用，与 `finish` / `integrate`
共用同一 `repo_task.review` 评估器与持久化 limits。

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
  max_review_round=N      # 来自 progress.md review_limit
  max_verify_round=N      # 来自 progress.md verify_limit
  next_action=finalize|fix_or_block|collect_reports|rerender_review|complete_ac_review|sanitize_review_paths|complete_disposition
  review_scope=ok|stale|missing|format_error  # 指纹比对状态
  ac_review=ok|void|incomplete|no_spec        # 每份报告最后一轮「AC 复验方式」小节门禁状态
  ac_review_detail=...                        # void / incomplete 的具体原因（逐份报告）
  privacy_review=ok|leak                      # 报告是否泄漏开发者家目录绝对路径
  privacy_review_detail=...                   # leak 时的具体文件、行号与路径片段

AC 复验门禁：每份应有报告最后一轮须逐条列出 task.md 全部 AC（格式见 share_prompt.txt）。
  - 全部 TICK-VALID、行集合与 task.md 的 AC 编号一致、task.md 全部已打勾 → 不影响 verdict
  - 任一行 TICK-VOID → overall=FAIL（即使 verdict 写 PASS）
  - 缺小节 / 缺行 / 多出未知 AC / 重复 / 格式无法解析 / task.md 有未打勾 AC 标了 TICK-VALID
    → PASS 降为 INCOMPLETE，next_action=complete_ac_review
  - no_spec：目录里没有 task.md，无法比对 AC 集合，只拦 TICK-VOID、缺小节与格式错误
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from repo_task import context as ctx
from repo_task.context import PROGRESS_FILE, RepoContext, TaskDataError
from repo_task.monitoring import review_scope_fingerprint as monitoring_scope_fingerprint
from repo_task.review import (
    ReviewDataError,
    VALID_REVIEW_LEVELS,
    evaluate_review,
    parse_front_matter,
    review_limits,
)

def current_scope_fingerprint(
    task_dir: Path, diff_anchor: str, *, repo: ctx.RepoContext,
) -> str | None:
    """当前被审 diff 指纹；缺 diff_anchor 或目录越界返回 None，git 失败 fail-closed 抛错。"""
    if not diff_anchor:
        return None
    try:
        rel = task_dir.resolve().relative_to(repo.root.resolve())
    except ValueError:
        return None
    return monitoring_scope_fingerprint(
        diff_anchor, rel.as_posix(), repo_root=repo.root
    )


def resolve_task_dir(value: str, *, repo: ctx.RepoContext) -> Path:
    root = repo.root.resolve()
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
    if not (candidate / PROGRESS_FILE).is_file():
        raise ReviewDataError(f"missing {PROGRESS_FILE} in: {value}")
    return candidate


def run_review_status(task_dir: str, *, repo: ctx.RepoContext) -> tuple[dict, dict[str, int]]:
    """评估指定 task 目录，返回 (评估结果, 持久化 limits)。"""
    resolved = resolve_task_dir(task_dir, repo=repo)
    fm = parse_front_matter(resolved / PROGRESS_FILE)
    if fm.get("review_level", "full") not in VALID_REVIEW_LEVELS:
        raise ReviewDataError(f"review_level must be one of {sorted(VALID_REVIEW_LEVELS)}")
    limits = review_limits(fm)
    scope = current_scope_fingerprint(resolved, fm.get("diff_anchor", ""), repo=repo)
    return evaluate_review(resolved, fm, scope), limits


def cmd_review_status(args: argparse.Namespace) -> None:
    repo = getattr(args, "repo", None)
    if repo is None:
        repo = ctx.RepoContext.from_code_location(Path(__file__))
    try:
        result, limits = run_review_status(args.task_dir, repo=repo)
    except (ReviewDataError, TaskDataError, OSError) as error:
        print(f"review-status=FAIL：{error}", file=sys.stderr)
        sys.exit(1)
    for key, value in result.items():
        if value != "":
            print(f"{key}={value}")
    print(f"max_review_round={limits['review_limit']}")
    print(f"max_verify_round={limits['verify_limit']}")


def main(argv: list[str] | None = None, *, repo: ctx.RepoContext | None = None) -> None:
    parser = argparse.ArgumentParser(description="review 状态门禁（与 finish/integrate 同源）")
    parser.add_argument("--task-dir", required=True)
    args = parser.parse_args(argv)
    if repo is None:
        cand = Path(args.task_dir)
        if not cand.is_absolute():
            cand = Path.cwd() / cand
        cand = cand.resolve()
        root = None
        for p in (cand, *cand.parents):
            if (p / "docs" / "tasks").is_dir():
                root = p
                break
        repo = ctx.RepoContext.for_root(root or Path.cwd().resolve())
    args.repo = repo
    cmd_review_status(args)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
