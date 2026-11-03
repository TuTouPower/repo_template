"""Shared review evidence parser for the worktree checker and committed handoff gate."""

from __future__ import annotations
import re
from pathlib import Path

from .context import PROGRESS_FILE, TaskDataError
from .documents import extract_ac_statuses, parse_front_matter as _parse_front_matter

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
# 闭栏须为裸围栏（无 info string）；fence 内带 info 的围栏行是内容，不是提前闭栏。
FENCE_CLOSE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*$")
FINDING_RE = re.compile(r"^(t[0-9]+)_(?:code|test|gen)_f[0-9]+$")
# AC 复验行（J2/J3）：`- AC-NNN：TICK-VALID|TICK-VOID，re_verified|trust_prior。<证据>`。
# 冒号、逗号接受全角与半角；允许 AC 编号与标签带反引号 / 加粗修饰；证据文字不解析。
_AC_MARK = r"[`*]*"
AC_ROW_RE = re.compile(
    rf"^[ \t]*[-*][ \t]+{_AC_MARK}(AC-[0-9]{{3}}){_AC_MARK}[ \t]*[：:][ \t]*{_AC_MARK}"
    rf"(TICK-VALID|TICK-VOID){_AC_MARK}[ \t]*[，,][ \t]*{_AC_MARK}(re_verified|trust_prior){_AC_MARK}"
    r"(?=$|[\s。.；;：:，,])",
    re.IGNORECASE,
)
# 小节内以 AC 编号起头却不符合 AC_ROW_RE 的列表行 → 格式无法解析。
AC_ROW_CANDIDATE_RE = re.compile(rf"^[ \t]*[-*][ \t]+{_AC_MARK}AC-[0-9]{{3}}", re.IGNORECASE)
MD_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*$")
AC_SECTION_TITLE_RE = re.compile(r"^[`*]*AC[ \t]*复验方式")
STATUSES = ("已修", "遗留", "撤回")
VALID_REVIEW_LEVELS = {"full", "single"}


class ReviewDataError(ValueError):
    """review/task 文档结构不合法。"""


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def visible_markdown_lines(text: str) -> list[str]:
    """移除 fenced code 与 blockquote，保留可参与结构解析的正文行。

    围栏规则与 repo_task.documents 一致：开栏可带 info string，闭栏须为裸围栏
    且宽度 >= 开栏；fence 内带 info string 的围栏行是内容，不提前闭合代码块。
    """
    lines = []
    fence_marker = None
    for line in text.splitlines():
        if fence_marker is not None:
            close = FENCE_CLOSE_RE.match(line)
            if (
                close
                and close.group(1)[0] == fence_marker[0]
                and len(close.group(1)) >= fence_marker[1]
            ):
                fence_marker = None
            continue
        fence = FENCE_RE.match(line)
        if fence:
            fence_marker = (fence.group(1)[0], len(fence.group(1)))
            continue
        if line.lstrip().startswith(">"):
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
                    f"single 级 {task_tid}_gen_fNNN（不是 _general_）；参考 .repo_template/docs/usage.md"
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
    """从 review 报告提取本 task 的结构化 finding（表行或标题行精确匹配当前 tid）。

    取「行首即 finding_id」的表行，以及 prompt 模板输出格式的
    `### {tid}_{code|test|gen}_fNNN - 标题` 标题行；不扫描正文中段/代码块/
    其它 task 引用——否则引用历史 finding（如 t099_code_f001）会被误要求处置，
    而处置表又拒绝非当前 tid，造成无法闭环。
    """
    findings = set()
    escaped = re.escape(task_tid)
    patterns = (
        re.compile(rf"^\|?\s*({escaped}_(?:code|test|gen)_f[0-9]+)(?=$|[\s|])"),
        re.compile(rf"^#{{1,6}}[ \t]+({escaped}_(?:code|test|gen)_f[0-9]+)(?=$|[\s|:：-])"),
    )
    for path in paths:
        for line in visible_markdown_lines(read(path)):
            stripped = line.strip()
            for pattern in patterns:
                match = pattern.search(stripped)
                if match and not match.group(1).startswith("t000_"):
                    findings.add(match.group(1))
                    break
    return findings


def reviewed_scope(path: Path) -> str | None:
    """报告可见正文最后一条 `reviewed_scope:` 指纹（对应最后 verdict 的被审 diff）。

    只在可见行上扫描（与 verdict/文档校验一致）；fenced 示例里的 scope 不算声明。
    """
    text = "\n".join(visible_markdown_lines(read(path)))
    scopes = REVIEW_SCOPE_RE.findall(text)
    return scopes[-1] if scopes else None


def reviewed_scope_hint(path: Path) -> bool:
    """报告可见正文提及 reviewed_scope 但宽容正则提取不到 → 写了但格式错。"""
    text = "\n".join(visible_markdown_lines(read(path)))
    return REVIEW_SCOPE_HINT_RE.search(text) is not None



def parse_ac_review(path: Path) -> tuple[bool, list[tuple[str, str, str]], list[str]]:
    """解析报告**最后一轮**（最后一个 `## Round N` 之后，无则全文）的「AC 复验方式」小节。

    返回 (是否找到小节, [(AC-NNN, TICK-VALID|TICK-VOID, re_verified|trust_prior)], 无法解析的候选行)。
    只扫可见行：fenced 示例与引用块不计入。小节为标题以「AC 复验方式」起头的 H2–H6，
    止于下一个同级或更高级标题；`## Round N` 同样截断小节。
    """
    lines = visible_markdown_lines(read(path))
    start = 0
    for index, line in enumerate(lines):
        if ROUND_HEADER_RE.match(line):
            start = index
    found = False
    rows: list[tuple[str, str, str]] = []
    bad: list[str] = []
    depth = 0
    for line in lines[start:]:
        heading = MD_HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            if depth and level <= depth:
                depth = 0
            if level >= 2 and AC_SECTION_TITLE_RE.match(heading.group(2)):
                depth, found = level, True
            continue
        if not depth:
            continue
        match = AC_ROW_RE.match(line)
        if match:
            rows.append((match.group(1).upper(), match.group(2).upper(), match.group(3).lower()))
        elif AC_ROW_CANDIDATE_RE.match(line):
            bad.append(line.strip()[:60])
    return found, rows, bad


def check_ac_review(path: Path, expected: dict[str, bool] | None) -> tuple[str, list[str]]:
    """核对单份报告的 AC 复验行；返回 (void|incomplete|ok, 问题清单)。

    expected 为 task.md 的 {AC 编号: 是否已打勾}；None 表示拿不到 task.md，
    只能核对报告自身（有小节、有行、无重复、无无法解析行、无 TICK-VOID）。
    任一 TICK-VOID → void（等同该报告 FAIL）；其余结构问题 → incomplete。
    """
    name = path.name
    found, rows, bad = parse_ac_review(path)
    ids = [row[0] for row in rows]
    voided = sorted({row[0] for row in rows if row[1] == "TICK-VOID"})
    issues = []
    if not found:
        issues.append(f"{name} 最后一轮缺「AC 复验方式」小节（须逐条列出全部 AC）")
    issues.extend(f"{name} 无法解析的 AC 行：{line}" for line in bad)
    duplicated = sorted({ac_id for ac_id in ids if ids.count(ac_id) > 1})
    if duplicated:
        issues.append(f"{name} AC 行重复：{','.join(duplicated)}")
    if expected is None:
        if found and not rows and not bad:
            issues.append(f"{name} 「AC 复验方式」小节没有 AC 行")
    elif found:
        missing = sorted(set(expected) - set(ids))
        extra = sorted(set(ids) - set(expected))
        unticked = sorted(
            ac_id for ac_id, ticked in expected.items()
            if not ticked and ac_id in ids and ac_id not in voided
        )
        if missing:
            issues.append(f"{name} 缺 AC 行：{','.join(missing)}")
        if extra:
            issues.append(f"{name} 多出 task.md 没有的 AC：{','.join(extra)}")
        if unticked:
            issues.append(f"{name} task.md 未打勾的 AC 须标 TICK-VOID：{','.join(unticked)}")
    if voided:
        return "void", [f"{name} TICK-VOID：{','.join(voided)}"] + issues
    return ("incomplete" if issues else "ok"), issues


def review_limits(fm: dict) -> dict[str, int]:
    limits = {}
    for name in ("review_limit", "verify_limit"):
        value = str(fm.get(name, "5"))
        if not re.fullmatch(r"[1-9][0-9]*", value):
            raise TaskDataError(f"{name} 必须是正整数，收到 {value!r}")
        limits[name] = int(value)
    return limits


def evaluate_review(
    task_dir: Path, fm: dict, current_scope: str | None, spec_text: str | None = None
) -> dict:
    """Return all business/evidence states; this function never writes or runs Git.

    spec_text 缺省读 task_dir/task.md，用于核对报告 AC 行与 task.md 的 AC 编号和打勾状态。
    两者都拿不到时 ac_review=no_spec：不比对 AC 集合，但 TICK-VOID、缺小节与格式错误仍拦截。
    """
    level = fm.get("review_level") or "full"
    if level not in VALID_REVIEW_LEVELS:
        raise ReviewDataError(f"review_level 非法：{level!r}")
    names = ("general",) if level == "single" else ("code", "test")
    reports = tuple(task_dir / f"review_{name}.md" for name in names)
    result = {"review_level": level}
    verdicts = []
    for name, report in zip(names, reports):
        values = extract_verdicts(report)
        verdict = values[-1] if values else "MISSING"
        verdicts.append(verdict)
        result[f"{name}_verdict"] = verdict
    if spec_text is None and (task_dir / "task.md").is_file():
        spec_text = read(task_dir / "task.md")
    expected = None if spec_text is None else dict(extract_ac_statuses(spec_text))
    ac_states, ac_issues, effective = [], [], []
    for report, verdict in zip(reports, verdicts):
        if verdict == "MISSING":
            effective.append(verdict)
            continue
        state, issues = check_ac_review(report, expected)
        ac_states.append(state)
        ac_issues.extend(issues)
        # TICK-VOID 等同该报告 FAIL：reviewer 写了 PASS 也不能放行。
        effective.append("FAIL" if state == "void" else verdict)
    ac_state = ("" if not ac_states else
                "void" if "void" in ac_states else
                "incomplete" if "incomplete" in ac_states else
                "no_spec" if expected is None else "ok")
    overall = ("INCOMPLETE" if "MISSING" in effective else
               "FAIL" if "FAIL" in effective else "PASS")
    stats, disposed = _parse_disposition_rows(task_dir / PROGRESS_FILE)
    reported = reported_findings(fm.get("tid", ""), *reports)
    missing = sorted(reported - disposed)
    scopes = [reviewed_scope(report) for report in reports]
    if any(scope is None and reviewed_scope_hint(report) for scope, report in zip(scopes, reports)):
        scope_status = "format_error"
    elif current_scope is None or any(scope is None for scope in scopes):
        scope_status = "missing"
    elif any(scope != current_scope for scope in scopes):
        scope_status = "stale"
    else:
        scope_status = "ok"
    if overall == "PASS" and (missing or scope_status != "ok" or ac_state == "incomplete"):
        overall = "INCOMPLETE"
    result.update(
        overall=overall, review_scope=scope_status, ac_review=ac_state,
        ac_review_detail="; ".join(ac_issues),
        missing_disposition=",".join(missing), round=regression_rounds(*reports),
    )
    # Evidence repair does not itself spend a business regression round.
    if overall == "INCOMPLETE":
        result["next_action"] = (
            "collect_reports" if "MISSING" in verdicts else
            "rerender_review" if scope_status != "ok" else
            "complete_ac_review" if ac_state == "incomplete" else "complete_disposition"
        )
    else:
        result["next_action"] = "finalize" if overall == "PASS" else "fix_or_block"
    return result
