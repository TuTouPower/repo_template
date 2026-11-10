"""WP3 边界：隐私 rename-aware、全量审计排除项与 review 保留边界。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

from git_repo_support import (
    build_full_product_tree_repo,
    get_controlled_git_env,
    git_run,
    install_fake_md_kx,
)
from repo_task import privacy
from repo_task.documents import dump_front_matter
from repo_task.specs import render_spec_text
from template_sync import context as sync_context
from template_sync.specs_migration import migrate_specs_layout

HOOK = FACTORY_ROOT / "repo/.repo_template/hooks/pre-commit"


def _home(*parts: str) -> str:
    return "/".join(("", *parts))


@pytest.fixture
def privacy_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in get_controlled_git_env().items():
        if key.startswith("GIT_"):
            monkeypatch.setenv(key, value)
    root = tmp_path / "privacy_repo"
    root.mkdir()
    git_run(root, "init", "-b", "main")
    git_run(root, "config", "user.email", "test@example.com")
    git_run(root, "config", "user.name", "test")
    (root / "init.txt").write_text("init\n", encoding="utf-8")
    git_run(root, "add", "-A")
    git_run(root, "commit", "-m", "init")
    return root


def test_pure_rename_ignores_historical_leak(privacy_repo: Path):
    token = _home("Users", "renameuser")
    (privacy_repo / "old.md").write_text("clean\n" + token + "/old\n", encoding="utf-8")
    git_run(privacy_repo, "add", "old.md")
    git_run(privacy_repo, "commit", "-m", "historical leak")
    git_run(privacy_repo, "mv", "old.md", "new.md")

    assert privacy.scan_privacy_changes(privacy_repo) == []


def test_rename_with_added_front_matter_reports_only_added_lines(privacy_repo: Path):
    old_token = _home("Users", "historical")
    new_token = _home("Users", "frontmatter")
    body_lines = ["# Title", ""] + [f"body line {index:02d}" for index in range(40)]
    body_lines.append("body " + old_token + "/old")
    body = "\n".join(body_lines) + "\n"
    (privacy_repo / "old.md").write_text(body, encoding="utf-8")
    git_run(privacy_repo, "add", "old.md")
    git_run(privacy_repo, "commit", "-m", "historical body")
    git_run(privacy_repo, "mv", "old.md", "new.md")
    (privacy_repo / "new.md").write_text(
        "---\ntitle: demo\nnote: " + new_token + "/proj\n---\n" + body,
        encoding="utf-8",
    )
    git_run(privacy_repo, "add", "new.md")

    assert privacy.scan_privacy_changes(privacy_repo) == [f"new.md:3: {new_token}"]


def test_low_similarity_rename_is_scanned_as_new_file(privacy_repo: Path):
    token = _home("Users", "rewriteuser")
    old_lines = [f"alpha section {index:02d}" for index in range(40)] + [
        "historical " + token + "/old",
    ]
    new_lines = [f"beta rewrite {index:02d}" for index in range(40)] + [
        "historical " + token + "/old",
        "tail",
    ]
    (privacy_repo / "old.md").write_text("\n".join(old_lines) + "\n", encoding="utf-8")
    git_run(privacy_repo, "add", "old.md")
    git_run(privacy_repo, "commit", "-m", "old content")
    git_run(privacy_repo, "mv", "old.md", "new.md")
    (privacy_repo / "new.md").write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    git_run(privacy_repo, "add", "new.md")

    status = git_run(privacy_repo, "diff", "--cached", "--name-status", "-M").stdout
    assert not any(line.startswith("R") for line in status.splitlines())
    assert privacy.scan_privacy_changes(privacy_repo) == [f"new.md:41: {token}"]


def _write_task(root: Path, tid: str, slug: str, status: str, token: str) -> None:
    directory = root / "docs/tasks" / f"{tid}_{slug}"
    directory.mkdir(parents=True)
    fm = {
        "tid": tid, "slug": slug, "title": slug, "status": status,
        "branch": "", "worktree": "", "review_level": "full",
        "diff_anchor": "", "note": "",
    }
    (directory / "progress.md").write_text(
        dump_front_matter(fm) + "\n# Task\n", encoding="utf-8",
    )
    (directory / "notes.md").write_text("clean\n" + token + "/proj\n", encoding="utf-8")


def test_full_audit_excludes_terminal_tasks_and_retired_specs_but_scans_src(tmp_path: Path):
    root = build_full_product_tree_repo(tmp_path / "full_tree")
    token = _home("Users", "audituser")
    _write_task(root, "t002", "done_task", "done", token)
    _write_task(root, "t003", "dropped_task", "dropped", token)
    (root / "docs/specs").mkdir(exist_ok=True)
    (root / "docs/specs/retired_feature.md").write_text(
        render_spec_text(
            "retired_feature", "retired", ["baseline"],
            "\n# Retired\n\n" + token + "/old\n",
        ),
        encoding="utf-8",
    )
    (root / "src").mkdir(exist_ok=True)
    (root / "src/app.txt").write_text("clean\n" + token + "/app\n", encoding="utf-8")
    git_run(root, "add", "-A")
    git_run(root, "commit", "-m", "audit fixtures")

    result = subprocess.run(
        [
            sys.executable,
            str(root / ".repo_template/scripts/task.py"),
            "privacy-audit", "--ref", "HEAD",
        ],
        cwd=str(root), env=get_controlled_git_env(),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.splitlines() == [f"src/app.txt:2: {token}"]


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


def test_referenced_old_review_is_not_suggested_for_deletion(review_repo: Path, tmp_path: Path):
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
    assert "git rm -r -q -- docs/reviews/review_20260101_120000" not in result.stderr
    assert "docs/reviews/review_20260101_120000（docs/handoff.md）" in result.stderr
    assert "git rm -r -q -- docs/reviews/review_20260102_120000" in result.stderr


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
