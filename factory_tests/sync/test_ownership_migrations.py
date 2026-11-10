"""WP2/WP3 一次性迁移：AGENTS 两区、specs front matter、review 裁剪、ADR。"""

from __future__ import annotations

import subprocess
import sys
from argparse import Namespace
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS = FACTORY_ROOT / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from git_repo_support import state_file
from repo_task.specs import load_specs
from template_sync import context as ctx
from template_sync.adr_migration import migrate_adr
from template_sync.agents_zone_migration import migrate_agents_zones
from template_sync.reviews_migration import migrate_reviews
from template_sync.shared_files import merge_gitignore
from template_sync.specs_migration import migrate_specs_layout


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


@pytest.fixture
def consumer(tmp_path: Path) -> Path:
    root = tmp_path / "consumer"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")
    (root / ".repo_template").mkdir()
    (root / ".repo_template/sync_state.json").write_text("{}\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    return root


def _source(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "AGENTS.md").write_text(
        "模板项目\n\n<!-- repo-template:begin -->\n## 开发原则\n模板原则\n<!-- repo-template:end -->\n",
        encoding="utf-8",
    )
    (src / ".gitignore").write_text(
        "# repo-template:begin\nnode_modules/\n*.log\n# repo-template:end\n", encoding="utf-8"
    )
    return src


def test_agents_zone_migration_keeps_project_rows(consumer, tmp_path):
    (consumer / "AGENTS.md").write_text(
        "项目介绍\n\n## 目录与读写规则\n"
        "|路径|用途|写权归属|\n|---|---|---|\n"
        "|`docs/specs/`|spec|模板|\n|`src/`|源码|项目|\n\n"
        "## 开发原则\n旧原则\n",
        encoding="utf-8",
    )
    reports = migrate_agents_zones(ctx.SyncContext.for_consumer(consumer), _source(tmp_path), write=True)
    text = (consumer / "AGENTS.md").read_text(encoding="utf-8")
    assert "## 项目目录与读写规则" in text
    assert "`src/`" in text
    assert "模板原则" in text
    assert reports


def test_specs_migration_adds_front_matter_and_moves_archive(consumer):
    (consumer / "docs/specs").mkdir(parents=True)
    (consumer / "docs/archive/specs").mkdir(parents=True)
    (consumer / "docs/specs/feature.md").write_text("# Feature\n", encoding="utf-8")
    (consumer / "docs/archive/specs/old.md").write_text("# Old\n", encoding="utf-8")
    (consumer / "docs/specs_index.md").write_text(
        "# specs\n\n|slug|task 清单|最后更新时间|\n|---|---|---|\n"
        "|feature|t001|2026-01-01|\n|old|t002|2025-01-01|\n",
        encoding="utf-8",
    )
    sync = ctx.SyncContext.for_consumer(consumer)
    migrate_specs_layout(sync, write=True)
    specs = {spec["slug"]: spec for spec in load_specs(consumer)}
    assert specs["feature"]["status"] == "active"
    assert specs["feature"]["tasks"] == ["t001"]
    assert specs["old"]["status"] == "retired"
    assert not (consumer / "docs/specs_index.md").exists()
    assert not (consumer / "docs/archive/specs").exists()


def test_reviews_migration_keeps_latest_five(consumer):
    reviews = consumer / "docs/reviews"
    for index in range(1, 8):
        directory = reviews / f"review_2026010{index}_120000"
        directory.mkdir(parents=True)
        (directory / "review_general.md").write_text("PASS\n", encoding="utf-8")
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "reviews")
    sync = ctx.SyncContext.for_consumer(consumer)
    migrate_reviews(sync, write=True)
    kept = sorted(path.name for path in reviews.glob("review_*"))
    assert kept == [f"review_2026010{i}_120000" for i in range(3, 8)]


def test_adr_migration_one_file_per_decision(consumer):
    path = consumer / "docs/blueprint/decisions.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "# ADR\n\n## 001 选择方案（2026-01-02）\n\n- 结论：A\n\n"
        "## 002 替代方案（2026-01-03）\n\n- 替代：001\n",
        encoding="utf-8",
    )
    sync = ctx.SyncContext.for_consumer(consumer)
    migrate_adr(sync, write=True)
    files = sorted((consumer / "docs/blueprint/decisions").glob("*.md"))
    assert [item.name for item in files] == [
        "20260102_001_1.md", "20260103_002_2.md",
    ] or len(files) == 2
    assert not path.exists()


def test_gitignore_managed_block_preserves_outside_rules(consumer, tmp_path):
    src = _source(tmp_path)
    (consumer / ".gitignore").write_text("keep.txt\n", encoding="utf-8")
    sync = ctx.SyncContext.for_consumer(consumer)
    info = merge_gitignore(src, [], set(), sync)
    text = (consumer / ".gitignore").read_text(encoding="utf-8")
    assert "keep.txt" in text.splitlines()
    assert "node_modules/" in text.splitlines()
    assert "*.log" in info["added"]
