"""healthcheck 与 hooks 接入片段。"""

from __future__ import annotations

import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

sys.path.insert(0, str(FACTORY_ROOT / "repo/.repo_template/scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "support"))

from git_repo_support import install_fake_md_kx, state_file
from template_sync import assets
from template_sync import context as ctx
from template_sync.healthcheck import run_healthcheck
from template_sync.hooks import cmd_install_hooks


HOOKS_SRC = FACTORY_ROOT / "repo/.repo_template/hooks"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


def _restore_hooks(sync: ctx.SyncContext) -> set:
    """按诊断提示执行恢复动作：apply 硬同步对 hooks 树做的事。

    从固定模板产物 HOOKS_SRC 树对树刷新消费仓 .repo_template/hooks，
    补回 pre-commit / commit-msg 入口文件与 pre-merge-commit 软链。
    """
    changed: set = set()
    assets.sync_dir(HOOKS_SRC, sync.consumer / ".repo_template/hooks", changed, sync)
    return changed


def _entry_issue(issues, name):
    return next(issue for issue in issues if f".repo_template/hooks/{name}" in issue)


def _assert_asset_sync_hint(issue):
    """入口损坏提示必须指向固定来源模板资产同步，且不含 install-hooks 修复建议。"""
    assert "repo_sync.py apply" in issue
    assert "同步" in issue
    assert "固定" in issue
    assert "sync.md" in issue
    assert "install-hooks 只设置 core.hooksPath" in issue
    assert "不复制 hook 文件" in issue
    assert "不能修复入口文件" in issue
    assert "运行 repo_sync.py install-hooks" not in issue
    assert "install-hooks 重新同步" not in issue


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


def test_missing_commit_msg_points_to_asset_sync_not_install_hooks(
    consumer, tmp_path, monkeypatch
):
    bin_dir = install_fake_md_kx(tmp_path)
    monkeypatch.setenv("PATH", str(bin_dir) + ":" + str(Path("/usr/bin")))
    sync = ctx.SyncContext.for_consumer(consumer)
    (consumer / ".repo_template/hooks/commit-msg").unlink()
    issues = run_healthcheck(sync)
    entry = _entry_issue(issues, "commit-msg")
    assert "缺失或非文件" in entry
    _assert_asset_sync_hint(entry)
    assert any(
        "未设置 core.hooksPath" in issue and "install-hooks" in issue for issue in issues
    )


def test_install_hooks_cannot_repair_missing_commit_msg(consumer, tmp_path, monkeypatch):
    bin_dir = install_fake_md_kx(tmp_path)
    monkeypatch.setenv("PATH", str(bin_dir) + ":" + str(Path("/usr/bin")))
    sync = ctx.SyncContext.for_consumer(consumer)
    hook = consumer / ".repo_template/hooks/commit-msg"
    hook.unlink()
    assert cmd_install_hooks(Namespace(sync=sync)) == 0
    assert not hook.exists()
    assert any("commit-msg" in issue for issue in run_healthcheck(sync))


def test_asset_sync_closes_missing_commit_msg_loop(consumer, tmp_path, monkeypatch):
    bin_dir = install_fake_md_kx(tmp_path)
    monkeypatch.setenv("PATH", str(bin_dir) + ":" + str(Path("/usr/bin")))
    sync = ctx.SyncContext.for_consumer(consumer)
    hook = consumer / ".repo_template/hooks/commit-msg"
    hook.unlink()
    assert any("commit-msg" in issue for issue in run_healthcheck(sync))
    _restore_hooks(sync)
    assert hook.is_file() and os.access(hook, os.X_OK)
    assert not any(
        ".repo_template/hooks/commit-msg" in issue for issue in run_healthcheck(sync)
    )


def test_asset_sync_closes_dangling_pre_merge_commit_loop(consumer, tmp_path, monkeypatch):
    bin_dir = install_fake_md_kx(tmp_path)
    monkeypatch.setenv("PATH", str(bin_dir) + ":" + str(Path("/usr/bin")))
    sync = ctx.SyncContext.for_consumer(consumer)
    link = consumer / ".repo_template/hooks/pre-merge-commit"
    link.unlink()
    link.symlink_to("missing-hook")
    entry = _entry_issue(run_healthcheck(sync), "pre-merge-commit")
    assert "软链悬空" in entry
    _assert_asset_sync_hint(entry)
    _restore_hooks(sync)
    assert link.is_symlink() and link.readlink() == Path("pre-commit")
    assert link.resolve().is_file()
    assert not any(
        ".repo_template/hooks/pre-merge-commit" in issue for issue in run_healthcheck(sync)
    )


def test_asset_sync_closes_non_executable_hook_loop(consumer, tmp_path, monkeypatch):
    bin_dir = install_fake_md_kx(tmp_path)
    monkeypatch.setenv("PATH", str(bin_dir) + ":" + str(Path("/usr/bin")))
    sync = ctx.SyncContext.for_consumer(consumer)
    hook = consumer / ".repo_template/hooks/commit-msg"
    hook.chmod(0o644)
    entry = _entry_issue(run_healthcheck(sync), "commit-msg")
    assert "不可执行" in entry
    _assert_asset_sync_hint(entry)
    _restore_hooks(sync)
    assert hook.is_file() and os.access(hook, os.X_OK)
    assert not any("不可执行" in issue for issue in run_healthcheck(sync))


def test_snippet_branch_points_to_asset_sync_and_closes_loop(
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
    entry = next(issue for issue in issues if "hooks 接入片段齐全" in issue)
    assert "commit-msg" in entry
    _assert_asset_sync_hint(entry)
    _restore_hooks(sync)
    assert run_healthcheck(sync) == []
