#!/usr/bin/env python3
"""render_review_prompts.py - 从 task 的权威 worktree 渲染 full/single reviewer prompt。

提示词模板从 task 锁定的 spec_source_sha（Git commit）读取，规则与 spec 基线同源；
与 cwd、入口脚本位置、主仓当前模板内容无关。

review_level=full 使用 code + test；review_level=single 使用 general。

reviewer 不再自行去读 task：契约区与上下文区正文直接注入 prompt，消除信息不对称。
契约区自 spec 锁定来源后有变更时，prompt 末尾附「契约区 drift 警告」与 diff 供 reviewer 核对。

用法（唯一入口以 --tid 定位）：
  python3 .repo_template/scripts/render_review_prompts.py --tid t001
  python3 .repo_template/scripts/render_review_prompts.py --tid t001 --out-dir .scratch/review_prompts
  python3 .repo_template/scripts/render_review_prompts.py --tid t001 --root /path/to/repo

准入：active（执行期）或 done（review_repair 补审）且存在登记 worktree。
backlog 主仓副本是滞后快照，明确拒绝。
--out-dir 必须在仓库内且不得落在 docs/tasks/ 下（prompt 内含 task_dir/repo_root
绝对路径，落进 task 目录会被一并提交；请用 .scratch/review_prompts）
"""

from _runtime import require_python, run

require_python()

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
from repo_task.git_ops import _git
from repo_task.monitoring import (
    REVIEW_PROMPT_OUTPUT_FILES,
    review_scope_fingerprint as monitoring_scope_fingerprint,
)
from repo_task.store import (
    load_review_templates,
    resolve_baseline_spec_text,
    resolve_task_location,
)
from repo_task.specs import load_spec

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


def resolve_repo_path(path: Path, *, repo: RepoContext, label: str, require_file: bool = False) -> tuple[Path, str]:
    """解析仓库内路径，返回绝对路径与 POSIX 仓库相对路径；根由注入的 repo 决定。"""
    root = repo.root.resolve()
    candidate = path if path.is_absolute() else root / path
    candidate = candidate.resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        sys.exit(f"{label} must stay inside repository: {path}")
    if require_file and not candidate.is_file():
        sys.exit(f"missing {label}: {relative.as_posix()}")
    return candidate, relative.as_posix()


def resolve_out_dir(raw: str, *, repo: RepoContext) -> Path:
    """解析 --out-dir 并统一校验输出落点，返回绝对路径。

    prompt 内含 task_dir/repo_root 本机绝对路径：落进 docs/tasks/ 会被一并提交，
    故该落点直接拒绝并给出可执行替代。
    """
    out_dir = Path(raw)
    if not out_dir.is_absolute():
        out_dir = repo.root / out_dir
    root = repo.root.resolve()
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
            "会被一并提交）；请改用 --out-dir .scratch/review_prompts "
            "或保持默认 stdout 输出"
        )
    return resolved


def validate_diff_anchor(diff_anchor: str, *, repo: RepoContext) -> str:
    """校验 revision 并返回完整 commit SHA（git 调用绑定注入的权威 repo）。"""
    try:
        result = _git(
            ["rev-parse", "--verify", "--end-of-options", f"{diff_anchor}^{{commit}}"],
            repo=repo, timeout=15,
        )
    except TaskDataError as error:
        sys.exit(f"diff_anchor validation failed: {error}")
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown revision"
        sys.exit(f"invalid diff_anchor {diff_anchor!r}: {detail}")
    return result.stdout.strip()


def review_scope_fingerprint(diff_anchor: str, rel_task_dir: str, *, repo: RepoContext) -> str:
    """被审 diff 指纹：委托 monitoring.review_scope_fingerprint（单一真相源）。

    reviewer 把本值写回报告 `reviewed_scope:`；checker 重算当前指纹比对。
    review 后改动代码/测试/spec 会改变指纹，PASS 随即失效（防「PASS 后继续改」）。
    空指纹（git 失败/anchor 无效）显式失败，绝不注入空的 reviewed_scope。
    """
    fingerprint = monitoring_scope_fingerprint(diff_anchor, rel_task_dir, repo_root=repo.root)
    if not fingerprint:
        sys.exit(
            f"review scope 指纹无法计算（{diff_anchor}:{rel_task_dir}）："
            "拒绝把空 reviewed_scope 注入 prompt"
        )
    return fingerprint


def apply_placeholders(template: str, values: dict) -> str:
    return PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)


def validate_output_format_template(template: str, *, source: str | Path) -> None:
    """锁死模板 `## 输出格式` 章节：禁止包含本机绝对路径占位符 `{repo_root}` 或 `{task_dir}`。"""
    section = extract_section(template, "## 输出格式")
    for forbidden in ("{repo_root}", "{task_dir}"):
        if forbidden in section:
            sys.exit(
                f"{source}: `## 输出格式` 不得引用本机绝对路径占位符 {forbidden}；"
                "请改用仓库相对路径（如 `git diff {diff_anchor}`、`{task_dir_rel}`）"
            )


def linked_spec_summary(task_dir: Path, *, repo: RepoContext) -> str:
    task_md = task_dir / "task.md"
    if not task_md.is_file():
        return ""
    linked = None
    for line in task_md.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            stripped = stripped[2:]
        if stripped.startswith("关联 spec："):
            linked = stripped.split("：", 1)[1].strip()
            break
    if not linked or linked == "无":
        return ""
    path = repo.root / linked
    try:
        spec = load_spec(path, root=repo.root)
    except TaskDataError as error:
        sys.exit(f"关联 spec 无法读取：{error}")
    tasks = ", ".join(spec["tasks"])
    return (
        "## 关联 spec\n\n"
        f"- 路径：`{linked}`\n"
        f"- 标题：{spec['title']}\n"
        f"- 状态：{spec['status']}；tasks：{tasks}\n"
        "请核对实现是否破坏该 spec 描述的既有行为；需要时直接读取路径。\n"
    )


def _normalize_checkboxes(text: str) -> str:
    """归一化 markdown checkbox，将 - [x] / - [X] 替换为 - [ ]，以便仅对比契约语义漂移。"""
    return re.sub(r"^(\s*-\s*)\[[xX]\]", r"\1[ ]", text, flags=re.MULTILINE)


def contract_drift_notice(
    spec_rel: str, diff_anchor: str, current_contract: str, *, tid: str = "",
    spec_source_sha: str = "", repo: RepoContext,
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
            tid, spec_rel, spec_source_sha, repo=repo,
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
    task_dir: Path, *, repo: RepoContext, templates: dict[str, str],
    task_path: Path | None = None,
) -> dict[str, str]:
    task_dir, rel_task_dir = resolve_repo_path(task_dir, repo=repo, label="task directory")
    task_path = task_path or task_dir / PROGRESS_FILE
    task_path, _ = resolve_repo_path(task_path, repo=repo, label="task file", require_file=True)

    required_templates = {"code_prompt.txt", "test_prompt.txt", "general_prompt.txt", "share_prompt.txt"}
    missing_templates = required_templates - templates.keys()
    if missing_templates:
        sys.exit(f"missing prompt template: {', '.join(sorted(missing_templates))}")
    for name, template in templates.items():
        validate_output_format_template(template, source=f"<locked-template:{name}>")

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
    diff_anchor = validate_diff_anchor(fm["diff_anchor"], repo=repo)

    spec_value = fm.get("spec_path") or f"{rel_task_dir}/task.md"
    if "\\" in spec_value:
        sys.exit(f"spec_path must use POSIX separators: {spec_value!r}")
    spec_input = Path(spec_value)
    if spec_input.is_absolute():
        sys.exit(f"spec_path must be repository-relative: {spec_value!r}")
    spec_abs, spec_rel = resolve_repo_path(
        spec_input, repo=repo, label="spec", require_file=True
    )
    spec_text = spec_abs.read_text(encoding="utf-8")

    contract = extract_section(spec_text, CONTRACT_HEADING)
    if not contract:
        sys.exit(f"{spec_rel}: 缺「{CONTRACT_HEADING}」小节；reviewer 无 AC 锚点，拒绝渲染")
    context = extract_section(spec_text, CONTEXT_HEADING) or "（spec 未填上下文区）"

    # 先做契约区基线校验（失败时给出可定位的基线错误），再算被审指纹。
    drift = contract_drift_notice(
        spec_rel, diff_anchor, contract, tid=fm["tid"], spec_source_sha=fm["spec_source_sha"], repo=repo
    )
    fingerprint = review_scope_fingerprint(diff_anchor, rel_task_dir, repo=repo)

    # task_dir 注入绝对路径（reviewer 写文件落点与 cwd 无关）；
    # task_dir_rel 仓库内相对路径（模板在输出指令行展示，供报告引用与相对 git 路径）。
    values = {
        "tid": fm["tid"],
        "slug": fm["slug"],
        "spec_path": spec_rel,
        "task_dir": task_dir.as_posix(),
        "task_dir_rel": rel_task_dir,
        "repo_root": str(repo.root.resolve()),
        "diff_anchor": diff_anchor,
        "review_level": level,
        "contract_section": contract,
        "context_section": context,
        "review_scope_fingerprint": fingerprint,
    }
    shared = templates["share_prompt.txt"]

    if level == "single":
        (general_name,) = REVIEW_PROMPT_OUTPUT_FILES["single"]
        prompts = {
            general_name: apply_placeholders(
                templates["general_prompt.txt"] + "\n" + shared,
                values,
            ),
        }
    else:
        code_name, test_name = REVIEW_PROMPT_OUTPUT_FILES["full"]
        prompts = {
            code_name: apply_placeholders(
                templates["code_prompt.txt"] + "\n" + shared,
                values,
            ),
            test_name: apply_placeholders(
                templates["test_prompt.txt"] + "\n" + shared,
                values,
            ),
        }

    linked = linked_spec_summary(task_dir, repo=repo)
    extras = "\n".join(part for part in (linked, drift) if part)
    if extras:
        prompts = {
            name: f"{content.rstrip()}\n\n{extras}\n" for name, content in prompts.items()
        }
    return prompts


def main():
    parser = argparse.ArgumentParser(
        description="渲染 active task 的 code/test reviewer prompt（唯一入口）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--tid", required=True, help="task id（如 t001）；位置由 tid 唯一决定")
    parser.add_argument("--root", help="仓库根；默认由入口脚本 .repo_template 祖先定位")
    parser.add_argument("--out-dir", help="输出目录；不填则 stdout")
    args = parser.parse_args()

    repo = (
        RepoContext.for_root(Path(args.root))
        if args.root else RepoContext.from_code_location(Path(__file__))
    )
    try:
        location = resolve_task_location(args.tid, repo=repo)
        if location.worktree_root is None:
            raise TaskDataError(
                f"{args.tid} 无登记 worktree；主仓副本是滞后快照，拒绝渲染"
            )
        if location.status not in ("active", "done"):
            raise TaskDataError(
                f"{args.tid} 权威状态为 {location.status!r}；"
                "渲染只服务 active（执行期）与 done（review_repair 补审）"
            )
        templates = load_review_templates(location.spec_source_sha, repo=repo)
        prompts = render_review_prompts(
            location.task_dir, repo=location.repo, templates=templates
        )
    except TaskDataError as error:
        sys.exit(f"render=FAIL：{error}")

    if args.out_dir:
        out_dir = resolve_out_dir(args.out_dir, repo=location.repo)
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
    run(main)
