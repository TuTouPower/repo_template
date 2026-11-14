"""开发铁律整合回归：按审阅 ①-⑥ 顺序的根因级修复负例。

每项先锁定缺陷或行为契约，再改生产代码。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS = FACTORY_ROOT / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS))

from template_sync import context as ctx
from template_sync.entity_storage_migration import (
    MigrationError,
    build_migration_plan,
    entity_storage_migration_status,
    migrate_entity_storage,
)


def _git(repo: Path, *args: str, check: bool = True, env: dict[str, str] | None = None):
    base = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    base.update({
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_TERMINAL_PROMPT": "0",
    })
    if env:
        base.update(env)
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=check, env=base,
    )


def _init_consumer(tmp_path: Path) -> Path:
    root = tmp_path / "consumer"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    (root / ".repo_template").mkdir()
    (root / ".repo_template/entity_schema.json").write_text('{"version": 1}\n', encoding="utf-8")
    for d in ("docs/findings", "docs/pending", "docs/spikes", "docs/tasks"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / ".gitignore").write_text(".scratch/\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    return root


def _commit_all(root: Path, msg: str, *, at: str | None = None) -> None:
    env = None
    if at:
        env = {"GIT_AUTHOR_DATE": at, "GIT_COMMITTER_DATE": at}
    _git(root, "add", "-A")
    _git(root, "commit", "-m", msg, env=env)


def _finding(root: Path, name: str, eid: str, extra: str = "") -> None:
    (root / "docs/findings" / name).write_text(
        f"# {eid} {name[:-3]}\n\n- 结论：fact\n- 证据：data\n- 影响：无\n- 现状：有效\n{extra}",
        encoding="utf-8",
    )


def _pending(root: Path, rel: str, eid: str, slug: str, extra: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\npid: "{eid}"\nslug: "{slug}"\nstatus: "todo"\n---\n# {eid} {slug}\n{extra}',
        encoding="utf-8",
    )


def _sync(root: Path) -> ctx.SyncContext:
    return ctx.SyncContext.for_consumer(root)


# ---------------------------------------------------------------------------
# ① 统一「计划接管路径」派生：task 归档目录整体搬迁，内部普通 .md 不得被按旧路径回写
# ---------------------------------------------------------------------------


def _archived_task(root: Path, tid: str = "t001", slug: str = "alpha") -> Path:
    task_dir = root / "docs/archive/tasks" / f"{tid}_{slug}"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "progress.md").write_text(
        f'---\ntid: "{tid}"\nslug: "{slug}"\ntitle: "{slug}"\nstatus: "done"\n---\n'
        "## 实施笔记\n\n完成。\n",
        encoding="utf-8",
    )
    (task_dir / "task.md").write_text(f"# {tid} {slug}\n", encoding="utf-8")
    return task_dir


def test_1_archived_task_extra_md_reference_does_not_break_migration(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    task_dir = _archived_task(root)
    (task_dir / "notes.md").write_text(
        "- 依据 docs/pending/todo/p001_fix.md\n", encoding="utf-8"
    )
    _pending(root, "docs/pending/todo/p001_fix.md", "p001", "fix")
    _commit_all(root, "archived task with notes.md")

    migrate_entity_storage(_sync(root), write=True)

    notes = root / "docs/tasks/t001_alpha/notes.md"
    assert notes.is_file(), "notes.md must move with the task dir"
    assert "docs/pending/p001_fix.md" in notes.read_text(encoding="utf-8")
    assert not (root / "docs/archive/tasks").exists()
    assert entity_storage_migration_status(_sync(root)) == []


# ---------------------------------------------------------------------------
# ② 引用引擎围栏感知 + ledger 来源按真实位置(history_path)解析相对链接
# ---------------------------------------------------------------------------


def test_2_fenced_code_paths_are_not_rewritten(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    _finding(root, "d001_a.md", "d001")
    _finding(root, "d001_b.md", "d001")
    (root / "docs/guide.md").write_text(
        "见 docs/findings/d001_b.md\n\n"
        "```text\n"
        "docs/findings/d001_b.md\n"
        "```\n\n"
        "再见 docs/findings/d001_b.md\n",
        encoding="utf-8",
    )
    _commit_all(root, "guide with fenced example")

    migrate_entity_storage(_sync(root), write=True)
    text = (root / "docs/guide.md").read_text(encoding="utf-8")
    assert text.count("docs/findings/d002_b.md") == 2, text
    assert "```text\ndocs/findings/d001_b.md\n```" in text, text


def test_2_ledger_relative_link_resolves_from_ledger_position(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    _pending(root, "docs/pending/p001_fix.md", "p001", "fix")
    (root / "docs/findings.md").write_text(
        "# 发现\n\n## d010 link\n\n- 现状：有效\n\n[ref](pending/p001_fix.md)\n",
        encoding="utf-8",
    )
    _commit_all(root, "ledger with relative link")

    plan = build_migration_plan(_sync(root))
    ledger = [f for f in plan.findings if f.from_ledger][0]
    assert "../pending/p001_fix.md" in ledger.content, ledger.content

    migrate_entity_storage(_sync(root), write=True)
    final = next(p for p in (root / "docs/findings").glob("d010_*.md"))
    assert "../pending/p001_fix.md" in final.read_text(encoding="utf-8")
    assert (final.parent / "../pending/p001_fix.md").resolve().is_file()
