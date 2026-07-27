#!/usr/bin/env python3
"""render_review_prompts.py - 从 task.md front matter 渲染 code/test reviewer 完整 prompt。

提示词正文存于 docs/reviews/prompts/ 下三个 txt（code_prompt.txt / test_prompt.txt / share_prompt.txt）。

reviewer 不再自行去读 spec：契约区与上下文区正文直接注入 prompt，消除信息不对称。
契约区自 diff_anchor 后有变更时，prompt 末尾附「契约区 drift 警告」与 diff 供 reviewer 核对。

用法：
  python3 scripts/render_review_prompts.py --task-dir docs/tasks/t001_my_slug
  python3 scripts/render_review_prompts.py --task docs/tasks/t001_my_slug/task.md
  python3 scripts/render_review_prompts.py --task-dir ... --out-dir .scratch/review_prompts

必填 front matter：tid, slug, diff_anchor
可选：spec_path（默认 <task_dir>/spec.md）、review_level
默认 stdout；--out-dir 时写入 code_review_prompt.md 与 test_review_prompt.md
"""

import argparse
import difflib
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "docs/reviews/prompts"
PLACEHOLDER_RE = re.compile(
    r"\{(tid|slug|spec_path|task_dir|diff_anchor|review_level|contract_section|context_section)\}"
)
CONTRACT_HEADING = "## 契约区"
CONTEXT_HEADING = "## 上下文区"


def parse_front_matter(task_path: Path) -> dict:
    """简化版 front matter 解析（task.py / check_review_status.py 各有副本，改规则需三处同步）。"""
    text = task_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        sys.exit(f"{task_path}: must start with YAML front matter (---)")
    end = text.find("\n---", 3)
    if end == -1:
        sys.exit(f"{task_path}: front matter not terminated")
    fm = {}
    for line in text[3:end].splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        val = val.strip()
        if val and val[0] not in ("\"", "'"):
            val = val.split(" #", 1)[0].rstrip()
        fm[key.strip()] = val.strip('"').strip("'")
    return fm


def extract_section(spec_text: str, heading: str) -> str:
    """抽取 spec 中某个二级小节正文（不含标题行），到下一个二级标题为止。"""
    start = spec_text.find(heading)
    if start == -1:
        return ""
    rest = spec_text[start + len(heading):]
    end = rest.find("\n## ")
    section = rest if end == -1 else rest[:end]
    return section.strip()


def apply_placeholders(template: str, values: dict) -> str:
    return PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)


def contract_drift_notice(spec_rel: str, diff_anchor: str, current_contract: str) -> str:
    """契约区相对 diff_anchor 有变更时返回追加给 reviewer 的警告块。

    无变更返回 ""。无法判定（非 git 仓库、anchor 失效等）时打 stderr 警告并返回 ""——
    drift 检测是 advisory，不阻断渲染。
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", f"{diff_anchor}:{spec_rel}"],
            capture_output=True, text=True, timeout=15,
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
    if not task_dir.is_absolute():
        task_dir = REPO_ROOT / task_dir
    task_path = task_path or task_dir / "task.md"
    if not task_path.is_absolute():
        task_path = REPO_ROOT / task_path

    if not task_path.is_file():
        sys.exit(f"missing task file: {task_path}")

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

    try:
        rel_task_dir = str(task_dir.relative_to(REPO_ROOT))
    except ValueError:
        rel_task_dir = str(task_dir)

    spec_rel = fm.get("spec_path") or f"{rel_task_dir}/spec.md"
    spec_abs = REPO_ROOT / spec_rel
    if not spec_abs.is_file():
        sys.exit(f"missing spec: {spec_rel}")
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
        "diff_anchor": fm["diff_anchor"],
        "review_level": fm.get("review_level") or "full",
        "contract_section": contract,
        "context_section": context,
    }
    shared = template_paths["share"].read_text(encoding="utf-8")
    level = fm.get("review_level") or "full"

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

    drift = contract_drift_notice(spec_rel, fm["diff_anchor"], contract)
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
    main()
