#!/usr/bin/env python3
"""check_review_status.py - 读 task 目录下 review 报告，输出两路 verdict 与当前轮次（供 step 6 处置用）。

用法：
  python3 scripts/check_review_status.py --task-dir docs/tasks/t001_foo
  python3 scripts/check_review_status.py --task-dir docs/tasks/t001_foo --max-review-round 3

输出（stdout，一行键值，便于脚本/人读）：
  code_verdict=PASS|FAIL|MISSING
  test_verdict=PASS|FAIL|MISSING
  overall=PASS|FAIL|INCOMPLETE
  round=N               # 取两份报告 front 字段 round 的最大值；缺省时按 ## Round 小节推断，至少 1
  max_review_round=N    # 当前双审上限（默认 2；blocked 后用户加轮则由调用方传入新值）
"""

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERDICT_RE = re.compile(r"^verdict:\s*(PASS|FAIL)\b", re.MULTILINE)
ROUND_FIELD_RE = re.compile(r"^-\s*round:\s*([0-9]+)", re.MULTILINE)
ROUND_HEADER_RE = re.compile(r"^##\s+Round\s+([0-9]+)", re.MULTILINE)


def extract_verdict(path: Path) -> str:
    if not path.is_file():
        return "MISSING"
    text = path.read_text(encoding="utf-8")
    matches = VERDICT_RE.findall(text)
    if not matches:
        return "MISSING"
    return matches[-1]


def extract_round(path: Path) -> int:
    if not path.is_file():
        return 0
    text = path.read_text(encoding="utf-8")
    m = ROUND_FIELD_RE.findall(text)
    if m:
        return int(m[-1])
    headers = ROUND_HEADER_RE.findall(text)
    if headers:
        return len(headers)
    if VERDICT_RE.search(text):
        return 1
    return 0


def main():
    p = argparse.ArgumentParser(
        description="读 review 报告输出 verdict/round（step 6 处置用）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--task-dir", required=True)
    p.add_argument("--max-review-round", type=int, default=2)
    args = p.parse_args()

    task_dir = Path(args.task_dir)
    if not task_dir.is_absolute():
        task_dir = REPO_ROOT / task_dir

    code_f = task_dir / "review_code.md"
    test_f = task_dir / "review_test.md"

    code_verdict = extract_verdict(code_f)
    test_verdict = extract_verdict(test_f)
    round_val = max(extract_round(code_f), extract_round(test_f), 1)

    if code_verdict == "MISSING" or test_verdict == "MISSING":
        overall = "INCOMPLETE"
    elif code_verdict == "PASS" and test_verdict == "PASS":
        overall = "PASS"
    else:
        overall = "FAIL"

    print(f"code_verdict={code_verdict}")
    print(f"test_verdict={test_verdict}")
    print(f"overall={overall}")
    print(f"round={round_val}")
    print(f"max_review_round={args.max_review_round}")


if __name__ == "__main__":
    main()
