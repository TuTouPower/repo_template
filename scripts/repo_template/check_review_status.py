#!/usr/bin/env python3
"""check_review_status.py - 读 review 报告与处置表，输出 verdict、回归轮次与撤回率（Step 6 处置用）。

用法：
  python3 scripts/repo_template/check_review_status.py --task-dir docs/tasks/t001_foo
  python3 scripts/repo_template/check_review_status.py --task-dir docs/tasks/t001_foo --max-review-round 3

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
  review_scope=ok|stale|missing|format_error  # 指纹比对状态；format_error=报告写了 reviewed_scope 但格式无法解析
  withdraw_rate=0.NN
  prompt_hint=...       # 撤回率超阈值时的下一轮 prompt 附加要求
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

from repo_task.context import TaskDataError
from repo_task.documents import parse_front_matter as _parse_front_matter
from repo_task.monitoring import review_scope_fingerprint as monitoring_scope_fingerprint

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VERDICT_RE = re.compile(r"^verdict:\s*(PASS|FAIL)\s*$", re.MULTILINE)
# 行首容忍照抄环节常见的轻量 markdown 修饰（反引号 / 加粗 / 列表前缀），
# 避免 reviewer 误带修饰导致行首锚定失败而误报 missing/stale。
REVIEW_SCOPE_RE = re.compile(
    r"^[ \t]*(?:`+\s*|\*+[ \t]*|[*-][ \t]+)*reviewed_scope\s*:\s*([0-9a-f]{16})",
    re.MULTILINE,
)
# 宽容解析仍失败但文本提及 reviewed_scope → 判定格式错误（format_error），区分于缺失。
REVIEW_SCOPE_HINT_RE = re.compile(r"reviewed_scope", re.IGNORECASE)
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
    """front matter 解析统一委托 repo_task.documents；缺失或非法返回 {}（宽容语义）。"""
    try:
        fm, _ = _parse_front_matter(path)
    except (OSError, TaskDataError):
        return {}
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


def _parse_disposition_rows(task_md: Path) -> tuple[dict[str, int], set[str]]:
    """扫描 Review 处置表，返回 (status 统计, 已处置 finding 集合)。结构错误直接拒绝。"""
    stats = dict.fromkeys(STATUSES, 0)
    seen = set()
    fm = parse_front_matter(task_md)
    task_tid = fm.get("tid", "")
    if not re.fullmatch(r"t[0-9]+", task_tid):
        raise ReviewDataError(f"{task_md}: missing or invalid front matter tid")

    lines = extract_h2_lines(read(task_md), "## Review 处置")
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
        fix_col = header.index("fix_ref") if "fix_ref" in header else None
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
                raise ReviewDataError(
                    f"finding_id 非法：{finding_id!r}。"
                    f"格式：full 级 {task_tid}_code_fNNN / {task_tid}_test_fNNN，"
                    f"single 级 {task_tid}_gen_fNNN（不是 _general_）；参考 conventions.md"
                )
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
            if status == "遗留":
                if fix_col is None:
                    raise ReviewDataError(
                        f"status=遗留 的 {finding_id} 需要 fix_ref 列；处置表缺该列"
                    )
                fix_value = cells[fix_col].strip()
                if not re.fullmatch(r"(?:p[0-9]+|t[0-9]+)", fix_value):
                    raise ReviewDataError(
                        f"status=遗留 的 {finding_id} fix_ref 非法：{fix_value!r}"
                        "（须 pNNN 或 follow-up tid）"
                    )
            seen.add(finding_id)
            stats[status] += 1
            index += 1
    return stats, seen


def disposition_stats(task_md: Path) -> dict[str, int]:
    stats, _ = _parse_disposition_rows(task_md)
    return stats


def disposed_findings(task_md: Path) -> set[str]:
    _, seen = _parse_disposition_rows(task_md)
    return seen


def reported_findings(task_tid: str, *paths: Path) -> set[str]:
    """从 review 报告提取本 task 的结构化 finding（表格行首精确匹配当前 tid）。

    只取「行首即 finding_id」的表行，不扫描正文/代码块/其它 task 引用——
    否则引用历史 finding（如 t099_code_f001）会被误要求处置，而处置表又拒绝
    非当前 tid，造成无法闭环。
    """
    findings = set()
    pattern = re.compile(
        rf"^\|?\s*({re.escape(task_tid)}_(?:code|test|gen)_f[0-9]+)(?=$|[\s|])"
    )
    for path in paths:
        for line in visible_markdown_lines(read(path)):
            stripped = line.strip()
            match = pattern.search(stripped)
            if match and not stripped.startswith("t000_"):
                findings.add(match.group(1))
    return findings


def reviewed_scope(path: Path) -> str | None:
    """报告最后一条 `reviewed_scope:` 指纹（对应最后 verdict 的被审 diff）。"""
    scopes = REVIEW_SCOPE_RE.findall(read(path))
    return scopes[-1] if scopes else None


def reviewed_scope_hint(path: Path) -> bool:
    """报告提及 reviewed_scope 但宽容正则提取不到 → 写了但格式错。"""
    return REVIEW_SCOPE_HINT_RE.search(read(path)) is not None


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

    disposed = disposed_findings(task_dir / "task.md")
    reported = reported_findings(fm.get("tid", ""), *reports)
    missing = sorted(
        (finding for finding in reported if finding not in disposed),
        key=lambda f: (f.split("_")[0], int(f.rsplit("_f", 1)[1])),
    )
    # 首轮 FAIL 时处置表尚未填，missing 必然非空——FAIL 是更严格的信息，
    # 保留 FAIL（task-work 只有 PASS/FAIL 分支）；仅 PASS 才降级为 INCOMPLETE。
    if missing and overall == "PASS":
        overall = "INCOMPLETE"

    # PASS 有效性锚点：报告须回写被审 diff 指纹且与当前一致（review 后改动 → stale）
    current_scope = current_scope_fingerprint(task_dir, fm.get("diff_anchor", ""))
    scopes = [reviewed_scope(report) for report in reports]
    scope_ok = (
        current_scope is not None
        and all(scope is not None for scope in scopes)
        and all(scope == current_scope for scope in scopes)
    )
    if not scope_ok and overall == "PASS":
        overall = "INCOMPLETE"
    if current_scope is None or all(scope is None for scope in scopes):
        if any(reviewed_scope_hint(report) for report in reports):
            scope_status = "format_error"
        else:
            scope_status = "missing"
    elif not scope_ok:
        scope_status = "stale"
    else:
        scope_status = "ok"

    total = sum(stats.values())
    rate = stats["撤回"] / total if total else 0.0

    print(f"overall={overall}")
    print(f"review_scope={scope_status}")
    if missing:
        print(f"missing_disposition={','.join(missing)}")
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
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
