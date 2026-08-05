"""repo_task package layout, façade compatibility, direct CLI, and repository fingerprints."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "repo_template"
sys.path.insert(0, str(SCRIPTS_DIR))

import task
from repo_task import monitoring, scheduling


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def _fingerprint_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def test_facade_reexports_canonical_functions():
    assert task.compute_schedule is scheduling.compute_schedule
    assert task.repository_fingerprint is monitoring.repository_fingerprint
    assert 100 <= len((SCRIPTS_DIR / "task.py").read_text(encoding="utf-8").splitlines()) <= 250


def test_direct_cli_from_external_cwd_and_copied_toolchain(tmp_path):
    external = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "task.py"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert external.returncode == 0, external.stderr
    assert "observe" in external.stdout and "reconcile" in external.stdout

    copied = tmp_path / "copied" / "scripts" / "repo_template"
    copied.mkdir(parents=True)
    shutil.copy2(SCRIPTS_DIR / "task.py", copied / "task.py")
    shutil.copytree(SCRIPTS_DIR / "repo_task", copied / "repo_task")
    result = subprocess.run(
        [sys.executable, str(copied / "task.py"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stderr
    assert "observe" in result.stdout


def test_submodules_do_not_import_facade():
    for path in (SCRIPTS_DIR / "repo_task").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "import task" not in text
        assert "from task import" not in text


def test_fingerprint_tracks_head_staged_unstaged_delete_binary_and_mode(tmp_path):
    repo = _fingerprint_repo(tmp_path)
    tracked = repo / "tracked.txt"
    clean = monitoring.repository_fingerprint(repo)["fingerprint"]

    os.utime(tracked, None)
    assert monitoring.repository_fingerprint(repo)["fingerprint"] == clean
    ignored = repo / "ignored" / "cache.bin"
    ignored.parent.mkdir()
    ignored.write_bytes(b"ignored\x00bytes")
    assert monitoring.repository_fingerprint(repo)["fingerprint"] == clean

    tracked.write_text("unstaged\n", encoding="utf-8")
    unstaged = monitoring.repository_fingerprint(repo)["fingerprint"]
    assert unstaged != clean
    _git(repo, "add", "tracked.txt")
    staged = monitoring.repository_fingerprint(repo)["fingerprint"]
    assert staged != clean and staged != unstaged

    _git(repo, "reset", "--hard", "HEAD")
    tracked.unlink()
    assert monitoring.repository_fingerprint(repo)["fingerprint"] != clean
    _git(repo, "reset", "--hard", "HEAD")

    binary = repo / "binary.dat"
    binary.write_bytes(b"\x00\xffone")
    binary_one = monitoring.repository_fingerprint(repo)["fingerprint"]
    binary.write_bytes(b"\x00\xfftwo")
    binary_two = monitoring.repository_fingerprint(repo)["fingerprint"]
    assert binary_one != clean and binary_two != binary_one
    binary.chmod(0o755)
    assert monitoring.repository_fingerprint(repo)["fingerprint"] != binary_two

    _git(repo, "add", "binary.dat")
    _git(repo, "commit", "-m", "binary")
    assert monitoring.repository_fingerprint(repo)["fingerprint"] != clean


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="platform lacks symlink support")
def test_fingerprint_symlink_hashes_target_not_target_contents(tmp_path):
    repo = _fingerprint_repo(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("one", encoding="utf-8")
    link = repo / "link"
    link.symlink_to(outside)

    first = monitoring.repository_fingerprint(repo)["fingerprint"]
    outside.write_text("two", encoding="utf-8")
    assert monitoring.repository_fingerprint(repo)["fingerprint"] == first

    other = tmp_path / "other.txt"
    other.write_text("two", encoding="utf-8")
    link.unlink()
    link.symlink_to(other)
    assert monitoring.repository_fingerprint(repo)["fingerprint"] != first
