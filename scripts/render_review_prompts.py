#!/usr/bin/env python3
"""render_review_prompts.py - 从 task.md front matter 渲染 code/test reviewer 完整 prompt。

提示词正文存于 docs/templates/review/ 下三个 txt（code_prompt.txt / test_prompt.txt / share_prompt.txt）。

用法：
  python3 scripts/render_review_prompts.py --task-dir docs/tasks/t001_my_slug
  python3 scripts/render_review_prompts.py --task docs/tasks/t001_my_slug/task.md
  python3 scripts/render_review_prompts.py --task-dir ... --out-dir .scratch/review_prompts

必填 front matter：tid, slug, diff_anchor
可选：spec_path（默认 <task_dir>/spec.md）
默认 stdout；--out-dir 时写入 code_review_prompt.md 与 test_review_prompt.md
"""

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "docs/templates/review"
PLACEHOLDER_RE = re.compile(r"\{(tid|slug|spec_path|task_dir|diff_anchor)\}")


def parse_front_matter(task_path: Path) -> dict:
    text = task_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        sys.exit(f"{task_path}: must start with YAML front matter (---)")
    end = text.find("\n---", 3)
    if end == -1:
        sys.exit(f"{task_path}: front matter not terminated")
    fm_text = text[3:end]
    fm = {}
    for line in fm_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        fm[key] = val
    return fm


def apply_placeholders(template: str, values: dict) -> str:
    def repl(m):
        return values[m.group(1)]
    return PLACEHOLDER_RE.sub(repl, template)


def main():
    p = argparse.ArgumentParser(
        description="渲染 code/test reviewer prompt（唯一入口）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--task", help="task.md 路径")
    p.add_argument("--task-dir", help="task 目录（内含 task.md）")
    p.add_argument("--out-dir", help="输出目录；不填则 stdout")
    args = p.parse_args()

    if args.task and args.task_dir:
        sys.exit("use only one of --task or --task-dir")
    if not args.task and not args.task_dir:
        p.print_help()
        sys.exit(1)

    if args.task_dir:
        task_dir = Path(args.task_dir)
        if not task_dir.is_absolute():
            task_dir = REPO_ROOT / task_dir
        task_path = task_dir / "task.md"
    else:
        task_path = Path(args.task)
        if not task_path.is_absolute():
            task_path = REPO_ROOT / task_path
        task_dir = task_path.parent

    if not task_path.is_file():
        sys.exit(f"missing task file: {task_path}")

    code_prompt = TEMPLATES_DIR / "code_prompt.txt"
    test_prompt = TEMPLATES_DIR / "test_prompt.txt"
    share_prompt = TEMPLATES_DIR / "share_prompt.txt"
    for pth in (code_prompt, test_prompt, share_prompt):
        if not pth.is_file():
            sys.exit(f"missing prompt template: {pth}")

    fm = parse_front_matter(task_path)
    for k in ("tid", "slug", "diff_anchor"):
        if not fm.get(k):
            sys.exit(f"front matter requires tid, slug, diff_anchor (got {fm})")

    if not re.match(r"^t[0-9]+$", fm["tid"]):
        sys.exit(f"tid must be lowercase task id like t001 (got {fm['tid']!r})")

    try:
        rel_task_dir = str(task_dir.relative_to(REPO_ROOT))
    except ValueError:
        rel_task_dir = str(task_dir)

    spec_path = fm.get("spec_path") or f"{rel_task_dir}/spec.md"

    values = {
        "tid": fm["tid"],
        "slug": fm["slug"],
        "spec_path": spec_path,
        "task_dir": rel_task_dir,
        "diff_anchor": fm["diff_anchor"],
    }

    def render(axis: str) -> str:
        tmpl = code_prompt if axis == "code" else test_prompt
        body = tmpl.read_text(encoding="utf-8") + "\n" + share_prompt.read_text(encoding="utf-8")
        return apply_placeholders(body, values)

    if args.out_dir:
        out_dir = Path(args.out_dir)
        if not out_dir.is_absolute():
            out_dir = REPO_ROOT / out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "code_review_prompt.md").write_text(render("code"), encoding="utf-8")
        (out_dir / "test_review_prompt.md").write_text(render("test"), encoding="utf-8")
        print(f"wrote {out_dir}/code_review_prompt.md", file=sys.stderr)
        print(f"wrote {out_dir}/test_review_prompt.md", file=sys.stderr)
    else:
        print("===== code_review_prompt =====")
        print(render("code"))
        print("===== test_review_prompt =====")
        print(render("test"))


if __name__ == "__main__":
    main()
