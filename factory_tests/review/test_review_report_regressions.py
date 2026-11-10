"""Behavioral regressions for finish, immutable sync, locking and Python 3.9."""

import io
import json
import os
import subprocess
import sys
import tarfile
from argparse import Namespace
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

from test_dispatch_integration import git_repo, _git, _task_cli, _reserve, _identity_args, _worktree
from task_flow_support import finish_cli, prepare_finish
from test_repo_sync import env
from repo_task.finalization import check_finalization, validate_spike
from repo_task.locks import CONTROL_LOCK_NAME, file_lock, git_common_lock
import repo_task.locks as locks
from template_sync import context as ctx
from template_sync.apply import cmd_apply
from template_sync.gates import _run_tests
from export_template import _extract_product
from git_repo_support import state_file, worktree_state_file

SCRIPTS = FACTORY_ROOT / "repo/.repo_template/scripts"


@pytest.mark.parametrize("snippet", [
    "普通变量 d1、s1、s2，及内联代码 `s001 = socket()`。",
    "```python\nd1 = date()\ns001 = socket()\ns999 = socket()\n```",
    "~~~~python\ns001 = socket()\n```\ns999 = socket()\n~~~~",
    "<!-- s999 -->\n`d001 = date()`",
])
def test_finish_ignores_non_references_and_fenced_examples(git_repo, snippet):
    begun = _task_cli(git_repo, "run-begin", "t001", "--owner-id", "owner-a")
    assert begun.returncode == 0, begun.stderr
    worktree = _worktree(git_repo, "t001")
    spec = worktree / "docs/tasks/t001_alpha/task.md"
    spec.write_text(spec.read_text() + "\n" + snippet + "\n", encoding="utf-8")
    message = prepare_finish(worktree, "t001", "alpha")
    result = finish_cli(worktree, "t001", "owner-a", message)
    assert result.returncode == 0, result.stdout + result.stderr


def _spike(root, decision="- 决定：是", followup="- 后续 task：t001"):
    directory = root / "docs/spikes/s001_probe"
    directory.mkdir(parents=True)
    finding = root / "docs/findings/d001_verified.md"
    finding.parent.mkdir(parents=True)
    finding.write_text("# Verified\n", encoding="utf-8")
    report = directory / "report.md"
    report.write_text(
        "---\nsid: \"s001\"\nslug: \"probe\"\nstatus: \"completed\"\n---\n"
        "## 结论\n已验证。\n## 是否采纳\n" + decision + "\n" + followup +
        "\n## 关联 findings\n- `d001`\n\n```python\nd1 = date()\nd999 = date()\n```\n",
        encoding="utf-8",
    )
    return directory


@pytest.mark.parametrize("decision", ["- 决定：是", "- 决定： 是", "- 决定: 是", "- 决定 :\t否", "* 决定: 否"])
@pytest.mark.parametrize("followup", ["- 后续 task： t001", "- 后续 task: `t001`", "- 无需后续理由: 已验证无需修改"])
def test_spike_accepts_markdown_field_spacing_without_scanning_code(tmp_path, decision, followup):
    directory = _spike(tmp_path, decision, followup)
    validate_spike(tmp_path, directory)


def test_missing_real_finding_and_spike_references_still_block(tmp_path):
    directory = _spike(tmp_path)
    report = directory / "report.md"
    report.write_text(report.read_text().replace("`d001`", "`d003`"), encoding="utf-8")
    with pytest.raises(ValueError, match="findings"):
        validate_spike(tmp_path, directory)


def test_finish_does_not_require_unrelated_historical_task_directories(git_repo):
    begun = _task_cli(git_repo, "run-begin", "t001", "--owner-id", "owner-a")
    assert begun.returncode == 0, begun.stderr
    worktree = _worktree(git_repo, "t001")

    def write_specs(root):
        (root / "docs/specs").mkdir()
        (root / "docs/specs/feature.md").write_text(
            "---\nslug: feature\nstatus: active\ntasks: [t001, t998]\n---\n\n# Current feature\n",
            encoding="utf-8",
        )
        (root / "docs/specs/old.md").write_text(
            "---\nslug: old\nstatus: retired\ntasks: [t999]\n---\n\n# Historical feature\n",
            encoding="utf-8",
        )

    message = prepare_finish(worktree, "t001", "alpha", mutate=write_specs)
    result = finish_cli(worktree, "t001", "owner-a", message)
    assert result.returncode == 0, result.stdout + result.stderr


def test_historical_missing_spec_does_not_block_current_finish(git_repo):
    begun = _task_cli(git_repo, "run-begin", "t001", "--owner-id", "owner-a")
    assert begun.returncode == 0, begun.stderr
    worktree = _worktree(git_repo, "t001")

    def write_specs(root):
        (root / "docs/specs").mkdir()
        (root / "docs/specs/feature.md").write_text(
            "---\nslug: feature\nstatus: active\ntasks: [t001]\n---\n\n# Current feature\n",
            encoding="utf-8",
        )

    message = prepare_finish(worktree, "t001", "alpha", mutate=write_specs)
    result = finish_cli(worktree, "t001", "owner-a", message)
    assert result.returncode == 0, result.stdout + result.stderr


def test_current_task_spec_reference_remains_a_hard_gate(git_repo):
    directory = git_repo / "docs/tasks/t001_alpha"
    progress = directory / "progress.md"
    progress.write_text(
        "\n".join(
            line for line in progress.read_text(encoding="utf-8").splitlines()
            if not line.startswith("- specs_index: not_applicable:")
        ) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="未进入任何 active spec"):
        check_finalization(git_repo, "t001", directory)


def test_control_lock_is_reentrant_in_one_thread_without_child_hang(git_repo):
    code = ("import sys\nfrom pathlib import Path\n"
            f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
            "from repo_task.locks import git_common_lock, CONTROL_LOCK_NAME\n"
            f"with git_common_lock(Path({str(git_repo)!r}), CONTROL_LOCK_NAME):\n"
            f"    with git_common_lock(Path({str(git_repo)!r}), CONTROL_LOCK_NAME):\n"
            "        pass\n")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(os.name == "nt", reason="The child probe uses POSIX nonblocking flock")
def test_nested_exception_does_not_release_the_outer_os_lock(git_repo):
    path = locks.git_common_dir(git_repo) / CONTROL_LOCK_NAME
    probe = ("import fcntl,sys\n"
             "with open(sys.argv[1], 'a+') as stream:\n"
             "    try:\n"
             "        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
             "    except BlockingIOError:\n"
             "        sys.exit(3)\n")
    with git_common_lock(git_repo, CONTROL_LOCK_NAME):
        with pytest.raises(RuntimeError):
            with git_common_lock(git_repo, CONTROL_LOCK_NAME):
                raise RuntimeError("nested failure")
        result = subprocess.run([sys.executable, "-c", probe, str(path)], timeout=5)
        assert result.returncode == 3
    result = subprocess.run([sys.executable, "-c", probe, str(path)], timeout=5)
    assert result.returncode == 0


@pytest.mark.skipif(os.name == "nt", reason="Windows publishes an initialized private inode before opening the shared lock")
def test_shared_lock_sentinel_is_written_only_after_os_lock(tmp_path, monkeypatch):
    observed = []
    original = locks.lock_fh
    def check(fh, **kwargs):
        observed.append(os.fstat(fh.fileno()).st_size)
        original(fh, **kwargs)
    monkeypatch.setattr(locks, "lock_fh", check)
    path = tmp_path / "shared.lock"
    with file_lock(path):
        assert path.read_bytes() == b"\0"
    assert observed == [0]


def test_sync_validation_child_fails_fast_instead_of_waiting_for_parent_lock(git_repo, monkeypatch):
    command = [sys.executable, ".repo_template/scripts/task.py", "add", "--title", "child", "--slug", "child"]
    monkeypatch.setattr(ctx, "CONSUMER_TEST_COMMAND", tuple(command))
    monkeypatch.setattr(ctx, "CONSUMER_TEST_TIMEOUT", 5)
    with git_common_lock(git_repo, CONTROL_LOCK_NAME):
        assert not _run_tests(git_repo)
    assert not list((git_repo / "docs/tasks").glob("t004_*"))


def test_sync_validation_can_test_mutating_cli_in_an_isolated_repository(git_repo, monkeypatch):
    isolated = git_repo.parent / "isolated"
    _git(git_repo, "clone", str(git_repo), str(isolated))
    command = (sys.executable, str(isolated / ".repo_template/scripts/task.py"),
               "add", "--title", "isolated", "--slug", "isolated")
    monkeypatch.setattr(ctx, "CONSUMER_TEST_COMMAND", command)
    monkeypatch.setattr(ctx, "CONSUMER_TEST_TIMEOUT", 10)
    with git_common_lock(git_repo, CONTROL_LOCK_NAME):
        assert _run_tests(git_repo)
    assert list((isolated / "docs/tasks").glob("t004_isolated"))
    assert not list((git_repo / "docs/tasks").glob("t004_*"))


def test_ledger_rollback_cannot_overwrite_a_newer_execution_receipt(git_repo):
    assert _task_cli(git_repo, "start", "t001").returncode == 0
    first = _reserve(git_repo, "t001")
    for identity in (first,):
        assert _task_cli(git_repo, "attempt", "terminal", "t001", *_identity_args(identity), "--status", "stopped").returncode == 0
        assert _task_cli(git_repo, "attempt", "report", "t001", *_identity_args(identity), "--status", "blocked", "--reason", "external condition").returncode == 0
    ledger = state_file(git_repo, "ledger.jsonl")
    old_ledger = ledger.read_bytes()
    second = _reserve(git_repo, "t001")
    assert second["attempt"] > first["attempt"]
    receipt = worktree_state_file(_worktree(git_repo, "t001"), "repo-task-execution.json")
    newer_receipt = receipt.read_bytes()
    ledger.write_bytes(old_ledger)
    result = _task_cli(git_repo, "attempt", "reserve", "t001")
    assert result.returncode != 0 and "identity 不一致" in result.stderr
    assert ledger.read_bytes() == old_ledger
    assert receipt.read_bytes() == newer_receipt
    state = _task_cli(git_repo, "recovery", "t001")
    assert json.loads(state.stdout)["phase"] == "runtime_mismatch"


def _archive(members):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for member, data in members:
            archive.addfile(member, io.BytesIO(data) if data is not None else None)
    stream.seek(0)
    return tarfile.open(fileobj=stream)


@pytest.mark.parametrize("kind", ["traversal", "absolute", "nested_drive", "symlink_parent", "hardlink", "device"])
def test_python39_extractor_rejects_unsafe_archive_members_before_any_write(tmp_path, kind):
    name = {"traversal": "../escaped", "absolute": "/escaped", "nested_drive": "dir/C:escaped"}.get(kind, "entry")
    member = tarfile.TarInfo(name)
    member.size = 1
    members = [(member, b"x")]
    if kind == "symlink_parent":
        member.type = tarfile.SYMTYPE
        member.linkname = "../outside"
        child = tarfile.TarInfo("entry/file")
        child.size = 1
        members = [(member, None), (child, b"x")]
    elif kind in {"hardlink", "device"}:
        member.type = tarfile.LNKTYPE if kind == "hardlink" else tarfile.CHRTYPE
        member.linkname = "../outside"
        members = [(member, None)]
    target = tmp_path / "output"
    target.mkdir()
    with _archive(members) as archive, pytest.raises(ValueError):
        _extract_product(archive, target)
    assert list(target.iterdir()) == []


def test_python39_extractor_preserves_regular_executable_and_internal_symlink(tmp_path):
    member = tarfile.TarInfo("bin/run")
    member.mode = 0o755
    member.size = 4
    link = tarfile.TarInfo("entry")
    link.type = tarfile.SYMTYPE
    link.linkname = "bin/run"
    with _archive([(member, b"test"), (link, None)]) as archive:
        _extract_product(archive, tmp_path)
    assert (tmp_path / "entry").is_symlink()
    assert (tmp_path / "entry").read_bytes() == b"test"
    assert (tmp_path / "bin/run").stat().st_mode & 0o111
