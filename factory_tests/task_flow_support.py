"""finish 相关 fixture 辅助（工厂集成测试专用）。

`finish` 现在是原子收尾：严格 preflight、review 门禁、干净暂存区校验通过后才写入，
并生成 handoff.json 与执行 commit。多数测试需要构造异常现场（非法 handoff、stale
review、未提交的归档），因此提供：

- mark_done：只做「原位写 status: done」，不做前置门禁，也不生成 handoff 或提交；
  构造异常现场用。
- prepare_finish：把 worktree 布置到可通过 finish 全部前置检查的状态，供真实收尾测试用。
"""

import subprocess
import sys
from pathlib import Path

from review_support import ensure_review_evidence
from repo_task.documents import parse_front_matter

_MARK_DONE = (
    "import argparse, sys; sys.path.insert(0, '.repo_template/scripts'); "
    "from repo_task import lifecycle; "
    "lifecycle._close_task(argparse.Namespace(tid=sys.argv[1]), 'done', None)"
)


def _run(args, cwd):
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=300,
    )


def mark_done(worktree, tid):
    """在 worktree 内把 active task 原位写成 done（目录留在 docs/tasks/，不提交）；断言成功。"""
    result = _run([sys.executable, "-c", _MARK_DONE, tid], worktree)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def finish_cli(worktree, tid, owner, message_file, *extra):
    return _run(
        [sys.executable, str(Path(worktree) / ".repo_template/scripts/task.py"), "finish", tid,
         "--owner-id", owner, "--message-file", str(message_file), *extra],
        worktree,
    )


def git_in(path, *args, check=True):
    result = _run(["git", "-C", str(path), *args], None)
    if check:
        assert result.returncode == 0, result.stderr
    return result


def fill_summary(note_path, *, tests="pytest -q 通过", blackbox="无黑盒", review="PASS"):
    text = note_path.read_text(encoding="utf-8")
    for label, value in (("测试", tests), ("黑盒", blackbox), ("review", review)):
        text = text.replace(f"- {label}：待执行时填写", f"- {label}：{value}")
    note_path.write_text(text, encoding="utf-8")


def prepare_finish(
    worktree, tid, slug, *, mutate=None, tick_ac=True, summary=True, evidence=True,
    stage=True, message="feat: 完成 task\n",
):
    """布置 worktree，使 finish 的全部前置检查通过；返回 commit message 文件路径。

    顺序固定：改交付内容 → 暂存 → 打勾与验证摘要（进 task 目录，不影响暂存检查）→
    以最终内容生成 PASS 证据。message 文件放在仓外，不制造未跟踪文件。
    """
    worktree = Path(worktree)
    task_dir = worktree / "docs" / "tasks" / f"{tid}_{slug}"
    if mutate:
        mutate(worktree)
    if stage:
        git_in(worktree, "add", "-A", "--", ".", f":(exclude)docs/tasks/{tid}_{slug}")
    if tick_ac:
        spec = task_dir / "task.md"
        spec.write_text(spec.read_text(encoding="utf-8").replace("- [ ] AC-001", "- [x] AC-001"),
                        encoding="utf-8")
    if summary:
        fill_summary(task_dir / "note.md")
    if evidence:
        for name in ("review_code.md", "review_test.md", "review_general.md"):
            (task_dir / name).unlink(missing_ok=True)  # 内容变化后旧证据必然 stale，重新生成
        fm, _ = parse_front_matter(task_dir / "note.md")
        ensure_review_evidence(worktree, task_dir, fm["diff_anchor"])
    message_file = worktree.parent / f"{worktree.name}_commit_message.txt"
    message_file.write_text(message, encoding="utf-8")
    return message_file
