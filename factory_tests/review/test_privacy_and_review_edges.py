"""WP3 边界：review 保留与 spec 迁移边界。"""

import os
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

from git_repo_support import (
    get_controlled_git_env,
    git_run,
    install_fake_md_kx,
)
from repo_task.specs import render_spec_text
from template_sync import context as sync_context
from template_sync.specs_migration import migrate_specs_layout

HOOK = FACTORY_ROOT / "repo/.repo_template/hooks/pre-commit"


def _write_review_dir(root: Path, day: int) -> Path:
    directory = root / "docs/reviews" / f"review_202601{day:02d}_120000"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "review_general.md").write_text("PASS\n", encoding="utf-8")
    return directory


@pytest.fixture
def review_repo(tmp_path: Path) -> Path:
    root = tmp_path / "review_repo"
    root.mkdir()
    git_run(root, "init", "-b", "main")
    git_run(root, "config", "user.email", "test@example.com")
    git_run(root, "config", "user.name", "test")
    (root / ".repo_template").mkdir()
    (root / ".repo_template/entity_schema.json").write_text('{"version": 1}\n', encoding="utf-8")
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    for day in range(1, 6):
        _write_review_dir(root, day)
    git_run(root, "add", "-A")
    git_run(root, "commit", "-m", "init")
    return root


def _run_hook(root: Path, bin_dir: Path) -> subprocess.CompletedProcess:
    env = get_controlled_git_env()
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        [sys.executable, str(HOOK)], cwd=str(root), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )


def test_appending_file_to_existing_review_dir_is_not_new(review_repo: Path, tmp_path: Path):
    target = review_repo / "docs/reviews/review_20260101_120000"
    (target / "review_extra.md").write_text("PASS\n", encoding="utf-8")
    git_run(review_repo, "add", "-A")

    result = _run_hook(review_repo, install_fake_md_kx(tmp_path))

    assert result.returncode == 0, result.stderr
    assert "超过 5 个" not in result.stderr


def test_referenced_old_review_is_still_suggested_for_deletion(review_repo: Path, tmp_path: Path):
    """612c0f1 废除引用/未跟踪保留后，被引用旧目录也列入删除建议，数量检查仍阻断。

    旧用例 test_referenced_old_review_is_not_suggested_for_deletion 固定「被引用
    不删除」语义，随契约废除整体删除；本用例另立，覆盖新集合语义。
    """
    _write_review_dir(review_repo, 6)
    (review_repo / "docs/handoff.md").write_text(
        "见 docs/reviews/review_20260101_120000\n", encoding="utf-8",
    )
    git_run(review_repo, "add", "-A")
    git_run(review_repo, "commit", "-m", "six reviews and handoff")
    _write_review_dir(review_repo, 7)
    git_run(review_repo, "add", "-A")

    result = _run_hook(review_repo, install_fake_md_kx(tmp_path))

    assert result.returncode == 1
    assert "超过 5 个" in result.stderr
    assert "git rm -r -q -- docs/reviews/review_20260101_120000" in result.stderr
    assert "git rm -r -q -- docs/reviews/review_20260102_120000" in result.stderr
    assert "被引用、不能删除" not in result.stderr
    assert "先处理引用" not in result.stderr


def test_merge_head_in_linked_worktree_skips_review_retention(review_repo: Path, tmp_path: Path):
    git_run(review_repo, "checkout", "-b", "feature")
    (review_repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    git_run(review_repo, "add", "feature.txt")
    git_run(review_repo, "commit", "-m", "feature")
    git_run(review_repo, "checkout", "main")
    linked = tmp_path / "linked"
    git_run(review_repo, "worktree", "add", "--detach", str(linked), "main")
    try:
        merged = git_run(linked, "merge", "--no-commit", "--no-ff", "feature", check=False)
        assert merged.returncode == 0, merged.stdout + merged.stderr
        merge_path = Path(git_run(linked, "rev-parse", "--git-path", "MERGE_HEAD").stdout.strip())
        if not merge_path.is_absolute():
            merge_path = linked / merge_path
        assert merge_path.is_file()

        _write_review_dir(linked, 6)
        git_run(linked, "add", "-A")
        bin_dir = install_fake_md_kx(tmp_path)
        result = _run_hook(linked, bin_dir)
        assert result.returncode == 0, result.stderr
        assert "超过 5 个" not in result.stderr

        merge_path.unlink()
        result = _run_hook(linked, bin_dir)
        assert result.returncode == 1
        assert "超过 5 个" in result.stderr
    finally:
        git_run(review_repo, "worktree", "remove", "--force", str(linked), check=False)


def test_specs_migration_blocks_on_same_name_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)
    root = tmp_path / "consumer"
    (root / "docs/specs").mkdir(parents=True)
    (root / "docs/archive/specs").mkdir(parents=True)
    git_run(root, "init", "-b", "main")
    active_text = render_spec_text("feature", "active", ["baseline"], "\n# Feature\n")
    (root / "docs/specs/feature.md").write_text(active_text, encoding="utf-8")
    (root / "docs/archive/specs/feature.md").write_text("# Archived\n", encoding="utf-8")

    with pytest.raises(sync_context.SyncError, match="同名 spec 冲突"):
        migrate_specs_layout(sync_context.SyncContext.for_consumer(root), write=True)

    assert (root / "docs/specs/feature.md").is_file()
    archived = root / "docs/archive/specs/feature.md"
    assert archived.read_text(encoding="utf-8") == "# Archived\n"
