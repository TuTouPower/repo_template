"""计划 7.3：消费仓从 988fd06 布局到当前布局的真实 Git 迁移链。

链路：entity-storage -> 旧 hook 下 ``--no-verify`` 提交并核对重命名摘要 ->
state-root（docs/runtime、旧 sync_state、.scratch 事务迁入 Git common dir）->
S5/S6（agents-zones、specs-layout、reviews、adr）-> 新 hook 提交 -> healthcheck。
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


def _migrate(consumer: Path, kind: str) -> subprocess.CompletedProcess:
    result = _run(
        [
            sys.executable,
            str(SCRIPTS / "repo_sync.py"),
            "--consumer",
            str(consumer),
            "migrate",
            kind,
            "--write",
        ]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


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
    consumer = _build_988fd06_consumer(tmp_path)
    old_hook = consumer / ".repo_template/hooks/pre-commit"
    old_hook_text = old_hook.read_text(encoding="utf-8")
    assert old_hook_text == LEGACY_HOOK.read_text(encoding="utf-8")
    assert "--diff-filter=ACMR" in old_hook_text
    assert "scan_privacy_changes" not in old_hook_text

    _migrate(consumer, "entity-storage")
    assert (consumer / "docs/tasks/t001_alpha/progress.md").is_file()
    assert (consumer / "docs/pending/p001_fix_cli.md").is_file()
    assert (consumer / "docs/runtime/tasks_audit.log").read_text(encoding="utf-8") == AUDIT_TEXT
    assert not (consumer / "docs/archive/tasks_audit.log").exists()
    assert (consumer / ".scratch/repo_sync_rollback/transaction.json").is_file()
    assert (consumer / ".scratch/task_graph_transaction/transaction.json").is_file()
    assert not (common_state_dir(consumer) / "sync_transaction").exists()

    _git(consumer, "add", "-A")
    _git(consumer, "commit", "--no-verify", "-m", "chore(migration): entity storage")
    rows = _name_status(consumer, "HEAD~1", "HEAD")
    pending_rows = [row for row in rows if any("p001_fix_cli.md" in cell for cell in row)]
    assert len(pending_rows) == 1
    assert pending_rows[0][0].startswith("R")
    assert set(rows) - set(pending_rows) == {
        ("A", ".repo_template/entity_schema.json"),
        ("D", "docs/archive/handoff.md"),
        ("M", "docs/handoff.md"),
        ("R100", "docs/archive/tasks_audit.log", "docs/runtime/tasks_audit.log"),
        ("R100", "docs/archive/tasks/t001_alpha/progress.md", "docs/tasks/t001_alpha/progress.md"),
        ("R100", "docs/archive/tasks/t001_alpha/task.md", "docs/tasks/t001_alpha/task.md"),
    }
    handoff_text = (consumer / "docs/handoff.md").read_text(encoding="utf-8")
    assert "docs/archive/tasks/" not in handoff_text
    assert "docs/pending/todo/" not in handoff_text

    sync = ctx.SyncContext.for_consumer(consumer)
    assert state_root_migration_status(sync)
    _migrate(consumer, "state-root")
    state = common_state_dir(consumer)
    assert (state / "ledger.jsonl").read_text(encoding="utf-8") == '{"event": "start"}\n'
    assert (state / "tasks_audit.log").read_text(encoding="utf-8") == AUDIT_TEXT
    assert (state / "goal_queue.json").is_file()
    assert (state / "statusline_workdir.jsonl").is_file()
    assert (state / "sync_transaction/transaction.json").is_file()
    assert (state / "graph_transaction/transaction.json").is_file()
    assert json.loads((state / "sync_state.json").read_text(encoding="utf-8"))["template_source"][
        "value"
    ] == str(PRODUCT)
    assert not (consumer / "docs/runtime").exists()
    assert not (consumer / ".repo_template/sync_state.json").exists()
    assert not (consumer / ".scratch/repo_sync_rollback").exists()
    assert not (consumer / ".scratch/task_graph_transaction").exists()
    assert state_root_migration_status(sync) == []

    for kind in ("agents-zones", "specs-layout", "reviews", "adr"):
        _migrate(consumer, kind)

    agents = (consumer / "AGENTS.md").read_text(encoding="utf-8")
    assert "这是消费项目介绍。" in agents
    assert "## 项目目录与读写规则" in agents
    assert "|`docs/specs_index.md`|旧 spec 清单|模板|" in agents
    assert "<!-- repo-template:begin -->" in agents
    assert "docs/specs/<slug>.md" in agents
    assert "src/` `tests/` `assets/`" in agents

    specs = {spec["slug"]: spec for spec in load_specs(consumer)}
    assert specs["feature"]["status"] == "active"
    assert specs["feature"]["tasks"] == ["t001"]
    assert specs["old"]["status"] == "retired"
    assert specs["old"]["tasks"] == ["t002"]
    assert not (consumer / "docs/specs_index.md").exists()
    assert not (consumer / "docs/archive/specs").exists()

    kept = sorted(path.name for path in (consumer / "docs/reviews").glob("review_*"))
    assert kept == [f"review_2026010{index}_120000" for index in range(3, 8)]
    assert not (consumer / "docs/archive/reviews").exists()

    decisions = sorted(path.name for path in (consumer / "docs/blueprint/decisions").glob("*.md"))
    assert decisions == ["20260102_sqlite.md", "20260103_entry.md"]
    assert not (consumer / "docs/blueprint/decisions.md").exists()
    assert "supersedes: [20260102_sqlite.md]" in (
        consumer / "docs/blueprint/decisions/20260103_entry.md"
    ).read_text(encoding="utf-8")

    for relative in ("scripts", "hooks", "docs/task_template"):
        source = PRODUCT / ".repo_template" / relative
        target = consumer / ".repo_template" / relative
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(
            source,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        )
    _git(consumer, "add", "-A")
    fake_bin = install_fake_md_kx(tmp_path)
    commit = _git(
        consumer,
        "commit",
        "-m",
        "refactor(specs): 迁移 specs/review/ADR 布局",
        env={"PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", "")},
        check=False,
    )
    assert commit.returncode == 0, commit.stdout + commit.stderr
    assert _git(consumer, "status", "--porcelain").stdout.strip() == ""

    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    sync = ctx.SyncContext.for_consumer(consumer)
    assert run_healthcheck(sync) == [
        "缺 .agents/skills；运行 repo_sync.py link-skills",
        "缺 .claude/skills；运行 repo_sync.py link-skills",
        "缺 md_kx；安装 md-kx 或修正 PATH",
    ]
    for relative in (".agents/skills", ".claude/skills"):
        (consumer / relative).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + "/usr/bin:/bin")
    assert run_healthcheck(sync) == []
