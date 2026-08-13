#!/usr/bin/env python3
"""render_review_prompts.py - 从 task.md front matter 渲染 code/test reviewer 完整 prompt。

提示词正文存于 docs/reviews/prompts/ 下三个 txt（code_prompt.txt / test_prompt.txt / share_prompt.txt）。

reviewer 不再自行去读 spec：契约区与上下文区正文直接注入 prompt，消除信息不对称。
契约区自 diff_anchor 后有变更时，prompt 末尾附「契约区 drift 警告」与 diff 供 reviewer 核对。

用法：
  python3 scripts/repo_template/render_review_prompts.py --task-dir docs/tasks/t001_my_slug
  python3 scripts/repo_template/render_review_prompts.py --task docs/tasks/t001_my_slug/task.md
  python3 scripts/repo_template/render_review_prompts.py --task-dir ... --out-dir .scratch/review_prompts

必填 front matter：tid, slug, diff_anchor
可选：spec_path（默认 <task_dir>/spec.md）、review_level
默认 stdout；--out-dir 时写入 code_review_prompt.md 与 test_review_prompt.md
"""

import argparse
import difflib
import hashlib
import re
import subprocess
import sys
from pathlib import Path

from repo_task.context import TaskDataError
from repo_task.documents import parse_front_matter as _parse_front_matter

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = REPO_ROOT / "docs/reviews/prompts"
PLACEHOLDER_RE = re.compile(
    r"\{(tid|slug|spec_path|task_dir|diff_anchor|review_level|contract_section|context_section|review_scope_fingerprint)\}"
)
CONTRACT_HEADING = "## 契约区"
CONTEXT_HEADING = "## 上下文区"
VALID_REVIEW_LEVELS = {"full", "single"}
H2_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$")
FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")


def parse_front_matter(task_path: Path) -> dict:
    """front matter 解析统一委托 repo_task.documents；格式非法以 sys.exit 终止。"""
    try:
        fm, _ = _parse_front_matter(task_path)
    except TaskDataError as error:
        sys.exit(f"{task_path}: {error}")
    return fm


def extract_section(spec_text: str, heading: str) -> str:
    """抽取精确二级小节正文，忽略 fenced code 内的伪标题。"""
    wanted = heading.removeprefix("## ").strip()
    section = []
    collecting = False
    fence_marker = None

    for line in spec_text.splitlines():
        fence = FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)[0]
            width = len(fence.group(1))
            if fence_marker is None:
                fence_marker = (marker, width)
            elif marker == fence_marker[0] and width >= fence_marker[1]:
                fence_marker = None
            if collecting:
                section.append(line)
            continue

        if fence_marker is None:
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
    """被审 diff 指纹：`git diff {diff_anchor}` 排除 task 流程文件后的内容摘要。

    reviewer 把本值写回报告 `reviewed_scope:`；checker 重算当前指纹比对。
    review 后改动代码/测试/spec 会改变指纹，PASS 随即失效（防「PASS 后继续改」）。
    """
    excludes = [
        f":(exclude){rel_task_dir}/task.md",
        f":(exclude){rel_task_dir}/review_code.md",
        f":(exclude){rel_task_dir}/review_test.md",
        f":(exclude){rel_task_dir}/review_general.md",
        f":(exclude){rel_task_dir}/handoff.json",
        # 只排除处置过程产生的具体文件/目录；行为文件（hooks/skills/review
        # prompts/blueprint/specs/guides/README 等）计入指纹，改之则 PASS 失效。
        ":(exclude)docs/pending", ":(exclude)docs/findings",
        ":(exclude)docs/archive", ":(exclude)docs/tasks_index.json",
        ":(exclude)docs/archive/tasks_index.json", ":(exclude)docs/spikes",
        ":(exclude).scratch",
    ]
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "diff", "--binary", diff_anchor, "--", ".", *excludes],
            capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return hashlib.sha1(result.stdout).hexdigest()[:16]


def apply_placeholders(template: str, values: dict) -> str:
    return PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)


def contract_drift_notice(spec_rel: str, diff_anchor: str, current_contract: str) -> str:
    """契约区相对 diff_anchor 有变更时返回追加给 reviewer 的警告块。

    无变更返回 ""。anchor 已由调用方校验；历史版本没有该 spec 或 git show
    暂时失败时打 stderr 警告并返回 ""，不阻断渲染。
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", f"{diff_anchor}:{spec_rel}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as e:
        print(f"WARNING: 契约区 drift 检查跳过（{e}）", file=sys.stderr)
        return ""
    if r.returncode != 0:
        print(
            f"WARNING: 契约区 drift 检查跳过"
            f"（git show {diff_anchor}:{spec_rel} 失败：{r.stderr.strip()}）",
            file=sys.stderr,
        )
        return ""
    anchored = extract_section(r.stdout, CONTRACT_HEADING)
    if anchored == current_contract:
        return ""
    diff = "\n".join(
        difflib.unified_diff(
            anchored.splitlines(),
            current_contract.splitlines(),
            fromfile=f"{diff_anchor} 契约区",
            tofile="当前契约区",
            lineterm="",
        )
    )
    return (
        "## 契约区 drift 警告\n\n"
        f"契约区自 diff_anchor（{diff_anchor}）以来有变更。"
        "请核对是否为经用户确认的需求变更；未经确认的 AC 变更按 blocking finding 处理。\n\n"
        f"```diff\n{diff}\n```"
    )


def render_review_prompts(
    task_dir: Path,
    task_path: Path | None = None,
) -> dict[str, str]:
    task_dir, rel_task_dir = resolve_repo_path(task_dir, label="task directory")
    task_path = task_path or task_dir / "task.md"
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

    fm = parse_front_matter(task_path)
    for key in ("tid", "slug", "diff_anchor"):
        if not fm.get(key):
            sys.exit(f"front matter requires tid, slug, diff_anchor (got {fm})")

    if not re.match(r"^t[0-9]+$", fm["tid"]):
        sys.exit(f"tid must be lowercase task id like t001 (got {fm['tid']!r})")

    level = fm.get("review_level") or "full"
    if level not in VALID_REVIEW_LEVELS:
        sys.exit(
            f"review_level must be one of {sorted(VALID_REVIEW_LEVELS)} "
            f"(got {level!r})"
        )
    diff_anchor = validate_diff_anchor(fm["diff_anchor"])

    spec_value = fm.get("spec_path") or f"{rel_task_dir}/spec.md"
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

    values = {
        "tid": fm["tid"],
        "slug": fm["slug"],
        "spec_path": spec_rel,
        "task_dir": rel_task_dir,
        "diff_anchor": diff_anchor,
        "review_level": level,
        "contract_section": contract,
        "context_section": context,
        "review_scope_fingerprint": review_scope_fingerprint(diff_anchor, rel_task_dir),
    }
    shared = template_paths["share"].read_text(encoding="utf-8")

    if level == "single":
        prompts = {
            "general_review_prompt.md": apply_placeholders(
                template_paths["general"].read_text(encoding="utf-8") + "\n" + shared,
                values,
            ),
        }
    else:
        prompts = {
            "code_review_prompt.md": apply_placeholders(
                template_paths["code"].read_text(encoding="utf-8") + "\n" + shared,
                values,
            ),
            "test_review_prompt.md": apply_placeholders(
                template_paths["test"].read_text(encoding="utf-8") + "\n" + shared,
                values,
            ),
        }

    drift = contract_drift_notice(spec_rel, diff_anchor, contract)
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
    parser.add_argument("--task", help="task.md 路径")
    parser.add_argument("--task-dir", help="task 目录（内含 task.md）")
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
        out_dir = Path(args.out_dir)
        if not out_dir.is_absolute():
            out_dir = REPO_ROOT / out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        for filename, prompt in prompts.items():
            path = out_dir / filename
            path.write_text(prompt, encoding="utf-8")
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
