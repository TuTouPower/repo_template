"""计划 7.3：消费仓从 988fd06 布局到当前布局的真实 Git 迁移链。

链路：固定当前来源 → 一次 apply 内置全部迁移 → hooks 下单 commit → accept。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

SCRIPTS = FACTORY_ROOT / "repo/.repo_template/scripts"
PRODUCT = FACTORY_ROOT / "repo"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SUPPORT = Path(__file__).resolve().parent.parent / "support"

for _path in (SCRIPTS, SUPPORT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from git_repo_support import get_controlled_git_env, install_fake_md_kx
from repo_task.context import common_state_dir
from repo_task.specs import load_specs
from template_sync import context as ctx
from template_sync.healthcheck import run_healthcheck
from template_sync.state_root_migration import state_root_migration_status

AUDIT_TEXT = "2026-10-01 rewind t001\n2026-10-02 finish t001\n"
LEGACY_HOOK = FIXTURES / "pre_commit_988fd06"


def _run(command: list[str], *, env: dict[str, str] | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=get_controlled_git_env(env),
        timeout=timeout,
    )


def _git(repo: Path, *args: str, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess:
    result = _run(["git", "-C", str(repo), *args], env=env)
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _name_status(consumer: Path, parent: str, child: str) -> list[tuple[str, ...]]:
    result = _git(consumer, "diff", "-M", "--name-status", f"{parent}..{child}")
    rows = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = tuple(line.split("\t"))
        assert len(parts) in (2, 3), line
        rows.append(parts)
    return rows


def _build_988fd06_consumer(tmp_path: Path) -> Path:
    root = tmp_path / "consumer"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "config", "gc.auto", "0")

    _write(root / ".gitignore", "__pycache__/\n*.py[cod]\n.scratch/\n")
    _write(root / ".md_kx.toml", 'table_mode = "compact"\n')

    hook = root / ".repo_template/hooks/pre-commit"
    hook.parent.mkdir(parents=True)
    shutil.copy2(LEGACY_HOOK, hook)
    hook.chmod(0o755)

    _write(
        root / ".repo_template/sync_state.json",
        json.dumps(
            {
                "template_source": {"kind": "path", "value": str(PRODUCT)},
                "last_synced_commit": "988fd06",
                "last_synced_at": "2026-11-06T00:00:00+08:00",
                "resolved_checkout": str(PRODUCT),
                "user_prompts": [],
            }
        )
        + "\n",
    )

    _write(root / "docs/archive/tasks/t001_alpha/task.md", "# t001 alpha\n\n实现 alpha。\n")
    _write(
        root / "docs/archive/tasks/t001_alpha/progress.md",
        '---\ntid: "t001"\nslug: "alpha"\ntitle: "alpha"\nstatus: "done"\n'
        'branch: ""\nworktree: ""\nreview_level: "full"\ndiff_anchor: ""\nnote: ""\n'
        "---\n# Task 进展\n\n## 实施笔记\n\n完成。\n",
    )
    _write(
        root / "docs/pending/todo/p001_fix_cli.md",
        "# p001 fix cli\n\n- 来源：用户提出\n- 内容：修复 CLI 退出码\n- 处理：未开\n",
    )
    _write(root / "docs/archive/tasks_audit.log", AUDIT_TEXT)
    _write(root / "docs/archive/handoff.md", "# 历史交接\n\n- 2026-09-01 旧交接\n")
    _write(
        root / "docs/handoff.md",
        "# 项目交接\n\n- 历史任务：docs/archive/tasks/t001_alpha\n"
        "- 待办：docs/pending/todo/p001_fix_cli.md\n",
    )

    _write(root / "docs/runtime/dispatch_ledger.jsonl", '{"event": "start"}\n')
    _write(root / "docs/runtime/goal_queue.json", '{"queue": ["t001"]}\n')
    _write(root / "docs/runtime/statusline_workdir.jsonl", "{}\n")
    _write(
        root / ".scratch/repo_sync_rollback/transaction.json",
        '{"version": 1, "status": "accepted", "paths": {}}\n',
    )
    _write(
        root / ".scratch/task_graph_transaction/transaction.json",
        '{"version": 1, "status": "accepted", "paths": {}}\n',
    )

    _write(
        root / "docs/specs_index.md",
        "# 当前生效 spec 清单\n\n|slug|task 清单|最后更新时间|\n|---|---|---|\n"
        "|feature|t001|2026-01-01|\n|old|t002|2025-01-01|\n",
    )
    _write(root / "docs/specs/feature.md", "# Feature\n\n需求。\n")
    _write(root / "docs/archive/specs/old.md", "# Old\n\n历史需求。\n")

    for index in range(1, 8):
        directory = "docs/reviews" if index <= 5 else "docs/archive/reviews"
        _write(
            root / f"{directory}/review_2026010{index}_120000/review_general.md",
            "PASS\n",
        )

    _write(
        root / "docs/blueprint/decisions.md",
        "# 决策记录（ADR）\n\n## 001 选择 SQLite（2026-01-02）\n\n"
        "- 背景：需要本地存储。\n- 结论：使用 SQLite。\n\n"
        "## 002 替代方案（2026-01-03）\n\n- 替代：001\n",
    )
    _write(
        root / "docs/blueprint/testing.md",
        "# 测试\n\n## verification_policy\n\nproject_configured\n\n"
        "## verification_notes\n\n迁移链用例。\n\n## doctor_cmd\n\n无\n\n"
        "## test_cmd\n\n无\n\n## blackbox_verify\n\n无\n",
    )
    _write(
        root / "AGENTS.md",
        "# 示例消费仓\n\n这是消费项目介绍。\n\n## 目录与读写规则\n\n"
        "|路径|用途|写权归属|\n|---|---|---|\n"
        "|`docs/specs_index.md`|旧 spec 清单|模板|\n"
        "|`docs/tasks/{tid}_{slug}/`|task 工作区|模板|\n"
        "|`src/`|源码|项目|\n\n"
        "## 开发原则\n\n- 旧原则：保持现有实现。\n",
    )

    _git(root, "config", "core.hooksPath", ".repo_template/hooks")
    _git(root, "add", "-A")
    _git(root, "commit", "--no-verify", "-m", "chore: 初始化 988fd06 布局")
    return root


def test_consumer_migration_chain_from_988fd06_layout(tmp_path: Path, monkeypatch) -> None:
    """真实历史来源 + 当前产物，一次 apply 后只提交一次，hooks 不改写。"""
    consumer = _build_988fd06_consumer(tmp_path)
    source = tmp_path / "source"
    result = _run(["git", "clone", "--shared", str(FACTORY_ROOT), str(source)])
    assert result.returncode == 0, result.stderr
    shutil.rmtree(source / "repo")
    shutil.copytree(PRODUCT, source / "repo", symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
    # 固定来源：clone 的 HEAD 已含当前产物，pin 取 source 的 HEAD。无产物差异时
    # 工作区与 HEAD 一致（同步器按 --checkout 读工作区），直接复用 HEAD，避免对干净
    # 树做空提交触发 "nothing to commit" 失败；仅当开发工作区有待审产物改动时，才把
    # copytree 快照落成快照提交，使来源重新干净并通过 apply 的 dirty 来源校验。
    if _git(source, "status", "--porcelain").stdout.strip():
        _git(source, "add", "-A")
        _git(source, "commit", "-m", "test: current template snapshot")
    pin = _git(source, "rev-parse", "HEAD").stdout.strip()
    fake_bin = install_fake_md_kx(tmp_path, monkeypatch)
    before = _git(consumer, "rev-parse", "HEAD").stdout.strip()
    command = [sys.executable, str(source / "repo/.repo_template/scripts/repo_sync.py"),
               "--consumer", str(consumer), "--source", str(source), "--checkout", str(source)]
    apply = _run([*command, "apply", "--expected-commit", pin, "--skip-tests"])
    assert apply.returncode == 0, apply.stdout + apply.stderr
    assert _git(consumer, "rev-parse", "HEAD").stdout.strip() == before
    state = common_state_dir(consumer)
    journal = json.loads((state / "sync_transaction/transaction.json").read_text())
    assert journal["status"] == "applied"
    assert (state / "tasks_audit.log").read_text() == AUDIT_TEXT
    assert (state / "ledger.jsonl").read_text() == '{"event": "start"}\n'
    assert not (consumer / "docs/runtime").exists()
    for rel in (".scratch/repo_sync_rollback", ".scratch/task_graph_transaction"):
        assert not (consumer / rel).exists()
        assert (state / "sync_transaction" / rel / "transaction.json").exists()
    assert (consumer / "docs/tasks/t001_alpha/progress.md").is_file()
    assert (consumer / "docs/pending/p001_fix_cli.md").is_file()
    assert not (consumer / "docs/archive/tasks").exists()
    assert not (consumer / "docs/archive/handoff.md").exists()
    agents = (consumer / "AGENTS.md").read_text()
    assert "这是消费项目介绍。" in agents and "<!-- repo-template:begin -->" in agents
    specs = {item["slug"]: item for item in load_specs(consumer)}
    assert specs["feature"]["tasks"] == ["t001"]
    assert specs["old"]["status"] == "retired"
    assert sorted(path.name for path in (consumer / "docs/reviews").glob("review_*")) == [
        f"review_2026010{index}_120000" for index in range(3, 8)]
    assert sorted(path.name for path in (consumer / "docs/blueprint/decisions").glob("*.md")) == [
        "20260102_sqlite.md", "20260103_entry.md"]
    _git(consumer, "add", "-A")
    tree = _git(consumer, "write-tree").stdout
    _git(consumer, "commit", "-m", "chore: sync template and migrate legacy layout")
    assert _git(consumer, "rev-parse", "HEAD^{tree}").stdout == tree
    assert _git(consumer, "rev-list", "--count", before + "..HEAD").stdout.strip() == "1"
    assert not _git(consumer, "status", "--porcelain").stdout
    # 全量契约由工厂门禁执行；链路测试隔离契约子进程，避免嵌套整套测试超时。
    import argparse
    from template_sync.transaction import cmd_transaction
    monkeypatch.setattr("template_sync.transaction._run_tests", lambda root: True)
    assert cmd_transaction(argparse.Namespace(sync=ctx.SyncContext.for_consumer(consumer), action="accept")) == 0
    assert json.loads((state / "sync_transaction/transaction.json").read_text())["status"] == "accepted"
    assert run_healthcheck(ctx.SyncContext.for_consumer(consumer)) == []
