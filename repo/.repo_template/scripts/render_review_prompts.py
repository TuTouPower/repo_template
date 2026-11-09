#!/usr/bin/env python3
"""render_review_prompts.py - 从 progress.md front matter 渲染 full/single reviewer 完整 prompt。

提示词正文存于 .repo_template/docs/review_prompts/ 下四个 txt（code_prompt.txt / test_prompt.txt / share_prompt.txt / general_prompt.txt）。
review_level=full 使用 code + test；review_level=single 使用 general。

reviewer 不再自行去读 task：契约区与上下文区正文直接注入 prompt，消除信息不对称。
契约区自 diff_anchor 后有变更时，prompt 末尾附「契约区 drift 警告」与 diff 供 reviewer 核对。

用法：
  python3 .repo_template/scripts/render_review_prompts.py --task-dir docs/tasks/t001_my_slug
  python3 .repo_template/scripts/render_review_prompts.py --progress docs/tasks/t001_my_slug/progress.md
  python3 .repo_template/scripts/render_review_prompts.py --task-dir ... --out-dir .scratch/review_prompts

 必填 front matter：tid, slug, diff_anchor
 可选：spec_path（默认 <task_dir>/task.md）、review_level
 默认 stdout；--out-dir 时 full 写入 code_review_prompt.md 与 test_review_prompt.md，single 写入 general_review_prompt.md
 --out-dir 必须在仓库内且不得落在 docs/tasks/ 下（prompt 内含 task_dir/repo_root
 绝对路径，落进 task 目录会被 finish 隐私扫描误阻断；请用 .scratch/review_prompts）
"""

from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
from pathlib import Path

from repo_task.context import FenceState, PROGRESS_FILE, RepoContext, TaskDataError
from repo_task.documents import (
    atomic_write_text,
    parse_front_matter as _parse_front_matter,
)
from repo_task.monitoring import (
    REVIEW_PROMPT_OUTPUT_FILES,
    review_scope_fingerprint as monitoring_scope_fingerprint,
)
from repo_task.store import resolve_baseline_spec_text

TOOLKIT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = TOOLKIT_ROOT.parent
TEMPLATES_DIR = TOOLKIT_ROOT / "docs/review_prompts"
PLACEHOLDER_RE = re.compile(
    r"\{(tid|slug|spec_path|task_dir_rel|task_dir|repo_root|diff_anchor|review_level|contract_section|context_section|review_scope_fingerprint)\}"
)
CONTRACT_HEADING = "## 契约区"
CONTEXT_HEADING = "## 上下文区"
VALID_REVIEW_LEVELS = {"full", "single"}
H2_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$")


def parse_front_matter(task_path: Path) -> dict:
    """front matter 解析统一委托 repo_task.documents；格式非法以 sys.exit 终止。"""
    try:
        fm, _ = _parse_front_matter(task_path)
    except TaskDataError as error:
        sys.exit(f"{task_path}: {error}")
    return fm


def extract_section(spec_text: str, heading: str) -> str:
    """抽取精确二级小节正文，忽略 fenced code 内的伪标题（fence 规则委托 ctx.FenceState）。"""
    wanted = heading.removeprefix("## ").strip()
    section = []
    collecting = False
    fence = FenceState()

    for line in spec_text.splitlines():
        if fence.feed(line):
            # 围栏行本身仍属章节正文，只是其中的伪标题不参与小节切换。
            if collecting:
                section.append(line)
            continue
        match = H2_RE.fullmatch(line)
        if match:
            if collecting:
                break
            collecting = match.group(1).strip() == wanted
            continue
        if collecting:
            section.append(line)

    return "\n".join(section).strip()


def resolve_repo_path(path: Path, *, label: str, require_file: bool = False) -> tuple[Path, str]:
    """解析仓库内路径，返回绝对路径与 POSIX 仓库相对路径。"""
    root = REPO_ROOT.resolve()
    candidate = path if path.is_absolute() else root / path
    candidate = candidate.resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        sys.exit(f"{label} must stay inside repository: {path}")
    if require_file and not candidate.is_file():
        sys.exit(f"missing {label}: {relative.as_posix()}")
    return candidate, relative.as_posix()


def resolve_out_dir(raw: str) -> Path:
    """解析 --out-dir 并统一校验输出落点，返回绝对路径。

    prompt 内含 task_dir/repo_root 本机绝对路径：落进 docs/tasks/ 会被 finish
    隐私扫描误阻断（DS L221），故该落点直接拒绝并给出可执行替代。
    """
    out_dir = Path(raw)
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    root = REPO_ROOT.resolve()
    resolved = out_dir.resolve()
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        sys.exit(
            f"--out-dir 必须在仓库内（收到 {raw!r}）；拒绝越界写入"
        )
    if rel.parts[:2] == ("docs", "tasks"):
        sys.exit(
            "--out-dir 不得落在 docs/tasks/ 下（prompt 内含 task_dir/repo_root 绝对路径，"
            "会被 finish 隐私扫描误阻断）；请改用 --out-dir .scratch/review_prompts "
            "或保持默认 stdout 输出"
        )
    return resolved


def validate_diff_anchor(diff_anchor: str) -> str:
    """校验 revision 并返回完整 commit SHA。"""
    try:
        result = subprocess.run(
            [
                "git", "-C", str(REPO_ROOT), "rev-parse", "--verify",
                "--end-of-options", f"{diff_anchor}^{{commit}}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8", errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as e:
        sys.exit(f"diff_anchor validation failed: {e}")
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown revision"
        sys.exit(f"invalid diff_anchor {diff_anchor!r}: {detail}")
    return result.stdout.strip()


def review_scope_fingerprint(diff_anchor: str, rel_task_dir: str) -> str:
    """被审 diff 指纹：委托 monitoring.review_scope_fingerprint（单一真相源）。

    reviewer 把本值写回报告 `reviewed_scope:`；checker 重算当前指纹比对。
    review 后改动代码/测试/spec 会改变指纹，PASS 随即失效（防「PASS 后继续改」）。
    空指纹（git 失败/anchor 无效）显式失败，绝不注入空的 reviewed_scope。
    """
    fingerprint = monitoring_scope_fingerprint(diff_anchor, rel_task_dir, repo_root=REPO_ROOT)
    if not fingerprint:
        sys.exit(
            f"review scope 指纹无法计算（{diff_anchor}:{rel_task_dir}）："
            "拒绝把空 reviewed_scope 注入 prompt"
        )
    return fingerprint


def apply_placeholders(template: str, values: dict) -> str:
    return PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)


def validate_output_format_template(template: str, *, source: Path) -> None:
    """锁死模板 `## 输出格式` 章节：禁止包含本机绝对路径占位符 `{repo_root}` 或 `{task_dir}`。"""
    section = extract_section(template, "## 输出格式")
    for forbidden in ("{repo_root}", "{task_dir}"):
        if forbidden in section:
            sys.exit(
                f"{source}: `## 输出格式` 不得引用本机绝对路径占位符 {forbidden}；"
                "请改用仓库相对路径（如 `git diff {diff_anchor}`、`{task_dir_rel}`）"
            )


def _normalize_checkboxes(text: str) -> str:
    """归一化 markdown checkbox，将 - [x] / - [X] 替换为 - [ ]，以便仅对比契约语义漂移。"""
    return re.sub(r"^(\s*-\s*)\[[xX]\]", r"\1[ ]", text, flags=re.MULTILINE)


def contract_drift_notice(
    spec_rel: str, diff_anchor: str, current_contract: str, *, tid: str = "",
    spec_source_sha: str = "",
) -> str:
    """契约区相对基线有变更时返回追加给 reviewer 的警告块（A04/C90）。

    基线读取统一委托 repo_task.store.resolve_baseline_spec_text（从 spec_source_sha 读取）。
    忽略仅 checkbox 勾选状态（[ ] -> [x]）的变化。
    基线读取或 Git 失败时直接 sys.exit 阻断渲染，不降级为无 drift；
    基线 spec 真实无「## 契约区」小节时返回明确业务提示块。
    """
    if not spec_source_sha:
        sys.exit(f"契约区 drift 基线读取失败（{spec_rel}）：缺 spec_source_sha")
    try:
        anchored_text, source_sha = resolve_baseline_spec_text(
            tid, spec_rel, spec_source_sha, root=REPO_ROOT, repo=RepoContext.for_root(REPO_ROOT),
        )
    except (OSError, subprocess.SubprocessError, TaskDataError) as e:
        sys.exit(f"契约区 drift 基线读取失败（{spec_source_sha[:12]}:{spec_rel}）：{e}")
    anchored = extract_section(anchored_text, CONTRACT_HEADING)
    if not anchored:
        return (
            "## 契约区 drift 警告\n\n"
            f"基线（{source_sha[:12]}:{spec_rel}）无「{CONTRACT_HEADING}」小节。"
            "请核对当前契约区是否为经用户确认的完整 AC 定义。"
        )
    if _normalize_checkboxes(anchored) == _normalize_checkboxes(current_contract):
        return ""
    diff = "\n".join(
        difflib.unified_diff(
            anchored.splitlines(),
            current_contract.splitlines(),
            fromfile=f"{source_sha[:12]} 契约区",
            tofile="当前契约区",
            lineterm="",
        )
    )
    return (
        "## 契约区 drift 警告\n\n"
        f"契约区自规格基准（{source_sha[:12]}）以来有变更。"
        "请核对是否为经用户确认的需求变更；未经确认的 AC 变更按 blocking finding 处理。\n\n"
        f"```diff\n{diff}\n```"
    )


def render_review_prompts(
    task_dir: Path,
    task_path: Path | None = None,
) -> dict[str, str]:
    task_dir, rel_task_dir = resolve_repo_path(task_dir, label="task directory")
    task_path = task_path or task_dir / PROGRESS_FILE
    task_path, _ = resolve_repo_path(task_path, label="task file", require_file=True)

    template_paths = {
        "code": TEMPLATES_DIR / "code_prompt.txt",
        "test": TEMPLATES_DIR / "test_prompt.txt",
        "general": TEMPLATES_DIR / "general_prompt.txt",
        "share": TEMPLATES_DIR / "share_prompt.txt",
    }
    for path in template_paths.values():
        if not path.is_file():
            sys.exit(f"missing prompt template: {path}")
        validate_output_format_template(path.read_text(encoding="utf-8"), source=path)

    fm = parse_front_matter(task_path)
    for key in ("tid", "slug", "diff_anchor", "spec_source_sha"):
        if not fm.get(key):
            sys.exit(f"front matter requires tid, slug, diff_anchor, spec_source_sha (got {fm})")

    if not re.match(r"^t[0-9]+$", fm["tid"]):
        sys.exit(f"tid must be lowercase task id like t001 (got {fm['tid']!r})")

    level = fm.get("review_level") or "full"
    if level not in VALID_REVIEW_LEVELS:
        sys.exit(
            f"review_level must be one of {sorted(VALID_REVIEW_LEVELS)} "
            f"(got {level!r})"
        )
    diff_anchor = validate_diff_anchor(fm["diff_anchor"])

    spec_value = fm.get("spec_path") or f"{rel_task_dir}/task.md"
    if "\\" in spec_value:
        sys.exit(f"spec_path must use POSIX separators: {spec_value!r}")
    spec_input = Path(spec_value)
    if spec_input.is_absolute():
        sys.exit(f"spec_path must be repository-relative: {spec_value!r}")
    spec_abs, spec_rel = resolve_repo_path(
        spec_input, label="spec", require_file=True
    )
    spec_text = spec_abs.read_text(encoding="utf-8")

    contract = extract_section(spec_text, CONTRACT_HEADING)
    if not contract:
        sys.exit(f"{spec_rel}: 缺「{CONTRACT_HEADING}」小节；reviewer 无 AC 锚点，拒绝渲染")
    context = extract_section(spec_text, CONTEXT_HEADING) or "（spec 未填上下文区）"

    # 先做契约区基线校验（失败时给出可定位的基线错误），再算被审指纹。
    drift = contract_drift_notice(
        spec_rel, diff_anchor, contract, tid=fm["tid"], spec_source_sha=fm["spec_source_sha"]
    )
    fingerprint = review_scope_fingerprint(diff_anchor, rel_task_dir)

    # task_dir 注入绝对路径（reviewer 写文件落点与 cwd 无关）；
    # task_dir_rel 仓库内相对路径（模板在输出指令行展示，供报告引用与相对 git 路径）。
    values = {
        "tid": fm["tid"],
        "slug": fm["slug"],
        "spec_path": spec_rel,
        "task_dir": task_dir.as_posix(),
        "task_dir_rel": rel_task_dir,
        "repo_root": str(REPO_ROOT.resolve()),
        "diff_anchor": diff_anchor,
        "review_level": level,
        "contract_section": contract,
        "context_section": context,
        "review_scope_fingerprint": fingerprint,
    }
    shared = template_paths["share"].read_text(encoding="utf-8")

    if level == "single":
        (general_name,) = REVIEW_PROMPT_OUTPUT_FILES["single"]
        prompts = {
            general_name: apply_placeholders(
                template_paths["general"].read_text(encoding="utf-8") + "\n" + shared,
                values,
            ),
        }
    else:
        code_name, test_name = REVIEW_PROMPT_OUTPUT_FILES["full"]
        prompts = {
            code_name: apply_placeholders(
                template_paths["code"].read_text(encoding="utf-8") + "\n" + shared,
                values,
            ),
            test_name: apply_placeholders(
                template_paths["test"].read_text(encoding="utf-8") + "\n" + shared,
                values,
            ),
        }

    if drift:
        prompts = {
            name: f"{content.rstrip()}\n\n{drift}\n" for name, content in prompts.items()
        }
    return prompts


def main():
    parser = argparse.ArgumentParser(
        description="渲染 code/test reviewer prompt（唯一入口）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--task", "--progress", dest="task", help=f"{PROGRESS_FILE} 路径")
    parser.add_argument("--task-dir", help=f"task 目录（内含 {PROGRESS_FILE}）")
    parser.add_argument("--out-dir", help="输出目录；不填则 stdout")
    args = parser.parse_args()

    if args.task and args.task_dir:
        sys.exit("use only one of --task or --task-dir")
    if not args.task and not args.task_dir:
        parser.print_help()
        sys.exit(1)

    if args.task_dir:
        task_dir = Path(args.task_dir)
        task_path = None
    else:
        task_path = Path(args.task)
        if not task_path.is_absolute():
            task_path = REPO_ROOT / task_path
        task_dir = task_path.parent

    prompts = render_review_prompts(task_dir, task_path=task_path)

    if args.out_dir:
        out_dir = resolve_out_dir(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for filename, prompt in prompts.items():
            path = out_dir / filename
            atomic_write_text(path, prompt)
            print(f"wrote {path}", file=sys.stderr)
    else:
        for filename, prompt in prompts.items():
            print(f"===== {filename.removesuffix('.md')} =====")
            print(prompt)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
