"""Codex 复审 P2 逐项回归（R01–R07）：每项先以负例锁定缺陷，再改生产代码。"""

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
    validate_current_entities,
)


def _git(repo: Path, *args: str, check: bool = True, env: dict[str, str] | None = None):
    base = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    base.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
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
# R01：无条件替换旧目录前缀会改写无关外站 URL；没有实体迁移也会触发。
# ---------------------------------------------------------------------------


def test_r01_external_url_untouched_when_no_migration_sources(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    readme = (
        "[upstream](https://example.com/docs/archive/tasks/guide.md)\n\n"
        "Similar bare prefix: docs/archive/tasks/legacy-note.md\n"
    )
    (root / "README.md").write_text(readme, encoding="utf-8")
    _commit_all(root, "readme with external url")

    status = entity_storage_migration_status(_sync(root))
    assert not any("更新普通文档精确路径引用" in s for s in status), status
    migrate_entity_storage(_sync(root), write=True)
    assert (root / "README.md").read_text(encoding="utf-8") == readme


def test_r01_real_source_reference_rewritten_but_url_not(tmp_path: Path) -> None:
    root = _init_consumer(tmp_path)
    _pending(root, "docs/pending/todo/p001_x.md", "p001", "x")
    (root / "docs/handoff.md").write_text(
        "- 待办：docs/pending/todo/p001_x.md\n", encoding="utf-8"
    )
    (root / "README.md").write_text(
        "https://example.com/docs/pending/todo/guide.md\n", encoding="utf-8"
    )
    _commit_all(root, "legacy pending with outside refs")

    migrate_entity_storage(_sync(root), write=True)
    assert "docs/pending/p001_x.md" in (root / "docs/handoff.md").read_text(encoding="utf-8")
    assert "https://example.com/docs/pending/todo/guide.md" in (
        root / "README.md"
    ).read_text(encoding="utf-8")
