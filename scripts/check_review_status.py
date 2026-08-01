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
  max_review_round=N      # 默认 5，与 task-run 的 max_review_round 默认一致
  withdraw_rate=0.NN
  prompt_hint=...       # 撤回率超阈值时的下一轮 prompt 附加要求
"""

import argparse
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERDICT_RE = re.compile(r"^verdict:\s*(PASS|FAIL)\s*$", re.MULTILINE)
ROUND_HEADER_RE = re.compile(r"^##\s+Round\s+([0-9]+)(?:\s|$)", re.MULTILINE)
H2_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$")
FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
FINDING_RE = re.compile(r"^(t[0-9]+)_(?:code|test|gen)_f[0-9]+$")
STATUSES = ("已修", "遗留", "撤回")
VALID_REVIEW_LEVELS = {"full", "single"}
WITHDRAW_THRESHOLD = 0.30


class ReviewDataError(ValueError):
    """review/task 文档结构不合法。"""


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def visible_markdown_lines(text: str) -> list[str]:
    """移除 fenced code 与 blockquote，保留可参与结构解析的正文行。"""
    lines = []
    fence_marker = None
    for line in text.splitlines():
        fence = FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)[0]
            width = len(fence.group(1))
            if fence_marker is None:
                fence_marker = (marker, width)
            elif marker == fence_marker[0] and width >= fence_marker[1]:
                fence_marker = None
            continue
        if fence_marker is not None or line.lstrip().startswith(">"):
            continue
        lines.append(line)
    return lines


def extract_verdicts(path: Path) -> list[str]:
    text = "\n".join(visible_markdown_lines(read(path)))
    return VERDICT_RE.findall(text)


def parse_front_matter(path: Path) -> dict:
    """简化版 front matter 解析（task.py / render_review_prompts.py 各有副本，改规则需三处同步）。"""
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
        v = v.strip()
        if v and v[0] not in ("\"", "'"):
            v = v.split(" #", 1)[0].rstrip()
        fm[k.strip()] = v.strip('"').strip("'")
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
        text = "\n".join(visible_markdown_lines(read(path)))
        headers = [int(n) for n in ROUND_HEADER_RE.findall(text)]
        if headers:
            max_header = max(max_header, max(headers))
    return max(best + 1, max_header, 1)


def extract_h2_lines(text: str, heading: str) -> list[str]:
    """返回精确 H2 小节正文，忽略 fenced code 与引用块。"""
    wanted = heading.removeprefix("## ").strip()
    section = []
    collecting = False
    for line in visible_markdown_lines(text):
        match = H2_RE.fullmatch(line)
        if match:
            if collecting:
                break
            collecting = match.group(1).strip() == wanted
            continue
        if collecting:
            section.append(line)
    return section


def table_cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None

    cells = []
    cell = []
    code_fence = ""
    index = 1
    while index < len(stripped) - 1:
        char = stripped[index]
        if char == "\\" and index + 1 < len(stripped) - 1:
            cell.append(stripped[index + 1])
            index += 2
            continue
        if char == "`":
            end = index
            while end < len(stripped) and stripped[end] == "`":
                end += 1
            marker = stripped[index:end]
            if not code_fence:
                code_fence = marker
            elif marker == code_fence:
                code_fence = ""
            cell.append(marker)
            index = end
            continue
        if char == "|" and not code_fence:
            cells.append("".join(cell).strip())
            cell = []
        else:
            cell.append(char)
        index += 1
    cells.append("".join(cell).strip())
    return cells


def is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def disposition_stats(task_md: Path) -> dict[str, int]:
    """从 Review 处置表 status 列统计；结构错误直接拒绝。"""
    stats = dict.fromkeys(STATUSES, 0)
    fm = parse_front_matter(task_md)
    task_tid = fm.get("tid", "")
    if not re.fullmatch(r"t[0-9]+", task_tid):
        raise ReviewDataError(f"{task_md}: missing or invalid front matter tid")

    lines = extract_h2_lines(read(task_md), "## Review 处置")
    seen = set()
    index = 0
    while index < len(lines):
        header = table_cells(lines[index])
        if not header or "finding_id" not in header or "status" not in header:
            index += 1
            continue
        if index + 1 >= len(lines):
            raise ReviewDataError("Review 处置表缺 separator row")
        separator = table_cells(lines[index + 1])
        if not separator or len(separator) != len(header) or not is_separator_row(separator):
            raise ReviewDataError("Review 处置表 separator row 非法")

        finding_col = header.index("finding_id")
        status_col = header.index("status")
        index += 2
        while index < len(lines):
            cells = table_cells(lines[index])
            if cells is None:
                break
            if len(cells) != len(header):
                raise ReviewDataError("Review 处置表数据列数与表头不一致")
            finding_id = cells[finding_col]
            status = cells[status_col]
            match = FINDING_RE.fullmatch(finding_id)
            if not match:
                raise ReviewDataError(f"finding_id 非法：{finding_id!r}")
            if finding_id.startswith("t000_"):
                index += 1
                continue
            if match.group(1) != task_tid:
                raise ReviewDataError(
                    f"finding_id {finding_id!r} 不属于 task {task_tid}"
                )
            if finding_id in seen:
                raise ReviewDataError(f"finding_id 重复：{finding_id}")
            if status not in STATUSES:
                raise ReviewDataError(f"status 非法：{status!r}")
            seen.add(finding_id)
            stats[status] += 1
            index += 1
    return stats


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
    p.add_argument("--max-review-round", type=int, default=5)
    args = p.parse_args()

    try:
        if args.max_review_round < 1:
            raise ReviewDataError("max-review-round must be at least 1")
        task_dir = resolve_task_dir(args.task_dir)
        fm = parse_front_matter(task_dir / "task.md")
        level = fm.get("review_level") or "full"
        if level not in VALID_REVIEW_LEVELS:
            raise ReviewDataError(
                f"review_level must be one of {sorted(VALID_REVIEW_LEVELS)} "
                f"(got {level!r})"
            )
        stats = disposition_stats(task_dir / "task.md")
    except ReviewDataError as e:
        p.error(str(e))

    if level == "single":
        general_f = task_dir / "review_general.md"
        general_verdicts = extract_verdicts(general_f)
        general_verdict = general_verdicts[-1] if general_verdicts else "MISSING"
        overall = "INCOMPLETE" if general_verdict == "MISSING" else general_verdict
        reports = (general_f,)
        print("review_level=single")
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
        print("review_level=full")
        print(f"code_verdict={code_verdict}")
        print(f"test_verdict={test_verdict}")

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
