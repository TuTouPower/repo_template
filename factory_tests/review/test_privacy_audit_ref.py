"""B4：privacy-audit 的排除项按 --ref tree 计算，不读当前工作区。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from git_repo_support import (
    build_full_product_tree_repo,
    copy_seed_repo,
    get_controlled_git_env,
    git_run,
)
from repo_task.documents import dump_front_matter, parse_front_matter, write_front_matter
from repo_task.specs import render_spec_text


def _home(*parts: str) -> str:
    return "/".join(("", *parts))


def _audit(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(root / ".repo_template/scripts/task.py"),
            "privacy-audit",
            *args,
        ],
        cwd=str(root),
        env=get_controlled_git_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


@pytest.fixture(scope="module")
def full_tree_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_full_product_tree_repo(tmp_path_factory.mktemp("privacy_audit_ref") / "repo")


@pytest.fixture
def audit_repo(full_tree_repo: Path, tmp_path: Path) -> Path:
    return copy_seed_repo(full_tree_repo, tmp_path / "repo")


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


def test_ref_terminal_task_excluded_even_when_worktree_is_active(audit_repo: Path) -> None:
    token = _home("Users", "auditref")
    _write_task(audit_repo, "t002", "done_task", "done", token)
    git_run(audit_repo, "add", "-A")
    git_run(audit_repo, "commit", "-m", "done task fixture")

    progress = audit_repo / "docs/tasks/t002_done_task/progress.md"
    fm, body = parse_front_matter(progress)
    fm["status"] = "active"
    write_front_matter(progress, fm, body)

    result = _audit(audit_repo, "--ref", "HEAD")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "privacy-audit=PASS" in result.stdout


def test_ref_retired_spec_excluded_even_when_worktree_is_active(audit_repo: Path) -> None:
    token = _home("Users", "auditrefspec")
    spec = audit_repo / "docs/specs/retired_feature.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(
        render_spec_text(
            "retired_feature", "retired", ["baseline"],
            "\n# Retired\n\n" + token + "/old\n",
        ),
        encoding="utf-8",
    )
    git_run(audit_repo, "add", "-A")
    git_run(audit_repo, "commit", "-m", "retired spec fixture")

    spec.write_text(
        spec.read_text(encoding="utf-8").replace("status: retired", "status: active"),
        encoding="utf-8",
    )

    result = _audit(audit_repo, "--ref", "HEAD")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "privacy-audit=PASS" in result.stdout
