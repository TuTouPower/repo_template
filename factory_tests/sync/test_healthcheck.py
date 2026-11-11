"""healthcheck 与 hooks 接入片段。"""

from __future__ import annotations

import subprocess
import sys
from argparse import Namespace
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

sys.path.insert(0, str(FACTORY_ROOT / "repo/.repo_template/scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "support"))

from git_repo_support import install_fake_md_kx, state_file
from template_sync import context as ctx
from template_sync.healthcheck import run_healthcheck
from template_sync.hooks import cmd_install_hooks


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


@pytest.fixture
def consumer(tmp_path: Path) -> Path:
    root = tmp_path / "consumer"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")
    hooks = root / ".repo_template/hooks"
    hooks.mkdir(parents=True)
    for name in ("pre-commit", "commit-msg"):
        hook = hooks / name
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        hook.chmod(0o755)
    (hooks / "pre-merge-commit").symlink_to("pre-commit")
    template = root / ".repo_template/docs/task_template/task.md"
    template.parent.mkdir(parents=True)
    template.write_text("- 关联 spec：{spec 路径；无关联写“无”}\n", encoding="utf-8")
    skill = root / ".repo_template/skills/task-run"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\ndescription: run\n---\n", encoding="utf-8")
    for rel in (".agents/skills", ".claude/skills"):
        host = root / rel
        host.mkdir(parents=True)
        (host / "task-run").symlink_to("../../.repo_template/skills/task-run")
    policy = root / "docs/blueprint/testing.md"
    policy.parent.mkdir(parents=True)
    policy.write_text(
        "## verification_policy\n\nproject_configured\n\n"
        "## verification_notes\n\n测试。\n\n## doctor_cmd\n\n无\n\n"
        "## test_cmd\n\n无\n\n## blackbox_verify\n\n无\n",
        encoding="utf-8",
    )
    state_file(root, "sync_state.json").write_text(
        '{"template_source": {"kind": "path", "value": "x"}}\n', encoding="utf-8"
    )
    return root


def test_healthcheck_passes_and_install_hooks_does_not_overwrite(consumer, tmp_path, monkeypatch):
    bin_dir = install_fake_md_kx(tmp_path)
    monkeypatch.setenv("PATH", str(bin_dir) + ":" + str(Path("/usr/bin")))
    sync = ctx.SyncContext.for_consumer(consumer)
    assert cmd_install_hooks(Namespace(sync=sync)) == 0
    assert run_healthcheck(sync) == []
    _git(consumer, "config", "core.hooksPath", ".husky/_")
    (consumer / ".husky").mkdir()
    (consumer / ".husky/pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")
    issues = run_healthcheck(sync)
    assert any("hooks 未接入" in issue for issue in issues)
    (consumer / ".husky/pre-commit").write_text(
        "#!/bin/sh\npython3 .repo_template/hooks/pre-commit\n", encoding="utf-8"
    )
    (consumer / ".husky/commit-msg").write_text(
        "#!/bin/sh\npython3 .repo_template/hooks/commit-msg \"$1\"\n", encoding="utf-8"
    )
    assert run_healthcheck(sync) == []


def test_healthcheck_without_hookspath_prompts_install_hooks(consumer, tmp_path, monkeypatch):
    bin_dir = install_fake_md_kx(tmp_path)
    monkeypatch.setenv("PATH", str(bin_dir) + ":" + str(Path("/usr/bin")))
    (consumer / ".husky").mkdir()
    (consumer / ".husky/pre-commit").write_text(
        "#!/bin/sh\npython3 .repo_template/hooks/pre-commit\n", encoding="utf-8"
    )
    issues = run_healthcheck(ctx.SyncContext.for_consumer(consumer))
    assert any("install-hooks" in issue for issue in issues)
    assert all(issue.strip() for issue in issues)


def test_install_hooks_foreign_path_reports_not_installed_and_lefthook_commit_msg(
    tmp_path, capsys
):
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _git(consumer, "init", "-b", "main")
    _git(consumer, "config", "core.hooksPath", ".git/hooks")
    hook = consumer / ".repo_template/hooks/pre-commit"
    hook.parent.mkdir(parents=True)
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    hook.chmod(0o755)
    sync = ctx.SyncContext.for_consumer(consumer)

    assert cmd_install_hooks(Namespace(sync=sync)) == 2
    err = capsys.readouterr().err
    assert "未安装，需人工接入" in err
    assert "run: python3 .repo_template/hooks/commit-msg {1}" in err
    assert _git(consumer, "config", "--get", "core.hooksPath").stdout.strip() == ".git/hooks"


def test_healthcheck_requires_commit_msg_hook(consumer, tmp_path, monkeypatch):
    bin_dir = install_fake_md_kx(tmp_path)
    monkeypatch.setenv("PATH", str(bin_dir) + ":" + str(Path("/usr/bin")))
    sync = ctx.SyncContext.for_consumer(consumer)
    _git(consumer, "config", "core.hooksPath", ".husky/_")
    (consumer / ".husky").mkdir()
    (consumer / ".husky/pre-commit").write_text(
        "#!/bin/sh\npython3 .repo_template/hooks/pre-commit\n", encoding="utf-8"
    )
    issues = run_healthcheck(sync)
    assert any("commit-msg" in issue for issue in issues)


def test_healthcheck_reports_missing_commit_msg_script(consumer, tmp_path, monkeypatch):
    bin_dir = install_fake_md_kx(tmp_path)
    monkeypatch.setenv("PATH", str(bin_dir) + ":" + str(Path("/usr/bin")))
    sync = ctx.SyncContext.for_consumer(consumer)
    (consumer / ".repo_template/hooks/commit-msg").unlink()
    issues = run_healthcheck(sync)
    assert any("commit-msg" in issue for issue in issues)


def test_healthcheck_reports_dangling_pre_merge_commit_symlink(consumer, tmp_path, monkeypatch):
    bin_dir = install_fake_md_kx(tmp_path)
    monkeypatch.setenv("PATH", str(bin_dir) + ":" + str(Path("/usr/bin")))
    sync = ctx.SyncContext.for_consumer(consumer)
    link = consumer / ".repo_template/hooks/pre-merge-commit"
    link.unlink()
    link.symlink_to("missing-hook")
    issues = run_healthcheck(sync)
    assert any("pre-merge-commit" in issue for issue in issues)


def test_healthcheck_reports_dangling_skill_links(consumer, tmp_path, monkeypatch):
    bin_dir = install_fake_md_kx(tmp_path)
    monkeypatch.setenv("PATH", str(bin_dir) + ":" + str(Path("/usr/bin")))
    sync = ctx.SyncContext.for_consumer(consumer)
    for rel in (".agents/skills/task-run", ".claude/skills/task-run"):
        link = consumer / rel
        link.unlink()
        link.symlink_to("../../.repo_template/skills/missing")
    issues = run_healthcheck(sync)
    assert any(".agents/skills/task-run" in issue for issue in issues)
    assert any(".claude/skills/task-run" in issue for issue in issues)


def test_healthcheck_reports_missing_real_commit_msg_when_snippets_present(
    consumer, tmp_path, monkeypatch
):
    bin_dir = install_fake_md_kx(tmp_path)
    monkeypatch.setenv("PATH", str(bin_dir) + ":" + str(Path("/usr/bin")))
    sync = ctx.SyncContext.for_consumer(consumer)
    _git(consumer, "config", "core.hooksPath", ".husky/_")
    (consumer / ".husky").mkdir()
    (consumer / ".husky/pre-commit").write_text(
        "#!/bin/sh\npython3 .repo_template/hooks/pre-commit\n", encoding="utf-8"
    )
    (consumer / ".husky/commit-msg").write_text(
        "#!/bin/sh\npython3 .repo_template/hooks/commit-msg \"$1\"\n", encoding="utf-8"
    )
    (consumer / ".repo_template/hooks/commit-msg").unlink()
    issues = run_healthcheck(sync)
    assert any("commit-msg" in issue for issue in issues)
