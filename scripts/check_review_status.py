#!/usr/bin/env python3
"""check_review_status.py - 读 review 报告与处置表，输出 verdict、回归轮次与撤回率（Step 6 处置用）。

用法：
  python3 scripts/check_review_status.py --task-dir docs/tasks/t001_foo
  python3 scripts/check_review_status.py --task-dir docs/tasks/t001_foo --max-review-round 3

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
  max_review_round=N
  withdraw_rate=0.NN
  prompt_hint=...       # 撤回率超阈值时的下一轮 prompt 附加要求
"""

import argparse
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERDICT_RE = re.compile(r"^verdict:\s*(PASS|FAIL)\s*$", re.MULTILINE)
ROUND_HEADER_RE = re.compile(r"^##\s+Round\s+([0-9]+)", re.MULTILINE)
DISPOSITION_RE = re.compile(r"^\|\s*(t[0-9]+_(?:code|test|gen)_f[0-9]+)\s*\|(.+)$", re.MULTILINE)
STATUSES = ("已修", "遗留", "撤回")
WITHDRAW_THRESHOLD = 0.30


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def extract_verdicts(path: Path) -> list[str]:
    return VERDICT_RE.findall(read(path))


def parse_front_matter(path: Path) -> dict:
    fm = {}
    if not path.is_file():
        return fm
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return fm
    end = text.find("\n---", 3)
    if end == -1:
        return fm
    for line in text[3:end].splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, _, v = line.partition(":")
        fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm


def regression_rounds(*paths: Path) -> int:
    """回归轮次 = 任一报告中「FAIL 且其后还有 verdict」的最大计数 + 1。

    兼顾 reviewer 报告被重建的场景：若 verdict 序列无法判定（如只有本轮），
    回退到 `## Round N` 标题的最大编号。
    """
    best = 0
    max_header = 0
    for path in paths:
        verdicts = extract_verdicts(path)
        best = max(best, sum(1 for v in verdicts[:-1] if v == "FAIL"))
        text = read(path)
        headers = [int(n) for n in ROUND_HEADER_RE.findall(text)]
        if headers:
            max_header = max(max_header, max(headers))
    return max(best + 1, max_header, 1)


def disposition_stats(task_md: Path) -> dict[str, int]:
    """从 task.md 处置表统计各 status 条数；模板示例行（t000_）不计。"""
    stats = dict.fromkeys(STATUSES, 0)
    for finding_id, rest in DISPOSITION_RE.findall(read(task_md)):
        if finding_id.startswith("t000_"):
            continue
        cells = [c.strip() for c in rest.split("|")]
        for status in STATUSES:
            if status in cells:
                stats[status] += 1
                break
    return stats


def main():
    p = argparse.ArgumentParser(
        description="读 review 报告与处置表输出 verdict / 回归轮次 / 撤回率",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--task-dir", required=True)
    p.add_argument("--max-review-round", type=int, default=4)
    args = p.parse_args()

    task_dir = Path(args.task_dir)
    if not task_dir.is_absolute():
        task_dir = REPO_ROOT / task_dir

    fm = parse_front_matter(task_dir / "task.md")
    level = fm.get("review_level") or "full"

    if level == "single":
        general_f = task_dir / "review_general.md"
        general_verdicts = extract_verdicts(general_f)
        general_verdict = general_verdicts[-1] if general_verdicts else "MISSING"
        overall = "INCOMPLETE" if general_verdict == "MISSING" else general_verdict
        reports = (general_f,)
        print(f"review_level=single")
        print(f"general_verdict={general_verdict}")
    else:
        code_f = task_dir / "review_code.md"
        test_f = task_dir / "review_test.md"
        code_verdicts = extract_verdicts(code_f)
        test_verdicts = extract_verdicts(test_f)
        code_verdict = code_verdicts[-1] if code_verdicts else "MISSING"
        test_verdict = test_verdicts[-1] if test_verdicts else "MISSING"
        if code_verdict == "MISSING" or test_verdict == "MISSING":
            overall = "INCOMPLETE"
        elif code_verdict == "PASS" and test_verdict == "PASS":
            overall = "PASS"
        else:
            overall = "FAIL"
        reports = (code_f, test_f)
        print(f"review_level=full")
        print(f"code_verdict={code_verdict}")
        print(f"test_verdict={test_verdict}")

    stats = disposition_stats(task_dir / "task.md")
    total = sum(stats.values())
    rate = stats["撤回"] / total if total else 0.0

    print(f"overall={overall}")
    print(f"round={regression_rounds(*reports)}")
    print(f"max_review_round={args.max_review_round}")
    print(f"withdraw_rate={rate:.2f}")
    if rate > WITHDRAW_THRESHOLD:
        print(
            "prompt_hint=撤回率超阈值；下一轮 review prompt 须附上轮被撤回的 finding_id 与撤回理由，"
            "要求 reviewer 先复核判定边界再出新 finding"
        )
    else:
        print("prompt_hint=")


if __name__ == "__main__":
    main()
