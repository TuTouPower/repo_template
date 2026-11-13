"""goal-snapshot 与 retired-docs 同步期迁移测试。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from factory_paths import FACTORY_ROOT
from git_repo_support import install_fake_md_kx, state_file
from migration_support import exercise_migrations, findings
from repo_task.context import common_state_dir
from repo_task.documents import parse_front_matter, write_front_matter
from repo_task.file_transaction import SYNC_TRANSACTION_DIR, open_transactions, state_transaction
from template_sync import context as ctx
from template_sync.apply import cmd_apply
from template_sync.goal_snapshot_migration import goal_snapshot_migration_status, migrate_goal_snapshot
from template_sync.retired_docs_migration import retired_docs_migration_status
from template_sync.status_plan import cmd_plan, cmd_status
from template_sync.transaction import cmd_transaction
from test_repo_sync import _commit_consumer_baseline, _git, env


def _write_task(repo: Path, tid: str, slug: str, *, status: str = "backlog", depends_on: str = "") -> None:
    task_dir = repo / f"docs/tasks/{tid}_{slug}"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "task.md").write_text(f"# {tid} {slug}\n\n- [ ] AC-001 行为\n", encoding="utf-8")
    fm = {
        "tid": tid,
        "slug": slug,
        "title": slug,
        "status": status,
        "branch": "",
        "worktree": "",
        "review_level": "full",
        "diff_anchor": "",
        "note": "",
    }
    if depends_on:
        fm["depends_on"] = depends_on
    write_front_matter(task_dir / "progress.md", fm, "# Task 进展\n\n## 实施笔记\n\n无。\n")


def _init_contract_ready_consumer(env_dict: dict) -> Path:
    consumer = env_dict["consumer"]
    (consumer / ".repo_template/entity_schema.json").parent.mkdir(parents=True, exist_ok=True)
    (consumer / ".repo_template/entity_schema.json").write_text('{"version": 1}\n', encoding="utf-8")
    testing = consumer / "docs/blueprint/testing.md"
    testing.parent.mkdir(parents=True, exist_ok=True)
    testing.write_text(
        "# 测试\n\n## verification_policy\n\nproject_configured\n\n"
        "## verification_notes\n\n同步迁移测试。\n\n## doctor_cmd\n\n无\n\n"
        "## test_cmd\n\n无\n\n## blackbox_verify\n\n无\n",
        encoding="utf-8",
    )
    return consumer


# ---------- goal-snapshot ----------


def test_goal_snapshot_upgrades_v1_without_edges_including_done_tasks(env):
    consumer = _init_contract_ready_consumer(env)
    _write_task(consumer, "t001", "alpha", status="done")
    _write_task(consumer, "t002", "beta", status="done", depends_on="t001")
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "done tasks")

    snap = state_file(consumer, "goal_queue.json")
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text(
        json.dumps({"version": 1, "created_at": "2026-09-17T08:03:47+08:00", "queue": ["t001", "t002"]}),
        encoding="utf-8",
    )

    sync = env["sync"]
    assert goal_snapshot_migration_status(sync)
    reports = exercise_migrations(argparse.Namespace(sync=sync, kind="goal-snapshot", write=True))
    assert reports and "version 2" in reports[0]
    assert goal_snapshot_migration_status(sync) == []

    migrated = json.loads(snap.read_text(encoding="utf-8"))
    assert migrated == {
        "version": 2,
        "created_at": "2026-09-17T08:03:47+08:00",
        "queue": ["t001", "t002"],
        "edges": [{"type": "dep", "from": "t001", "to": "t002"}],
    }


def test_goal_snapshot_preserves_existing_edges_on_v1_to_v2(env):
    consumer = _init_contract_ready_consumer(env)
    _write_task(consumer, "t001", "alpha")
    _write_task(consumer, "t002", "beta")  # 主干当前无 depends_on，但历史快照已冻结边
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "tasks")

    frozen_edges = [{"type": "dep", "from": "t001", "to": "t002"}]
    snap = state_file(consumer, "goal_queue.json")
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text(
        json.dumps({
            "version": 1,
            "created_at": "2026-11-05T10:00:00+08:00",
            "queue": ["t001", "t002"],
            "edges": frozen_edges,
        }),
        encoding="utf-8",
    )

    sync = env["sync"]
    exercise_migrations(argparse.Namespace(sync=sync, kind="goal-snapshot", write=True))
    migrated = json.loads(snap.read_text(encoding="utf-8"))
    assert migrated["version"] == 2
    assert migrated["edges"] == frozen_edges


def test_goal_snapshot_blocks_on_missing_tid_and_corrupt_json(env):
    consumer = _init_contract_ready_consumer(env)
    _write_task(consumer, "t001", "alpha")
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "t001")
    snap = state_file(consumer, "goal_queue.json")
    snap.parent.mkdir(parents=True, exist_ok=True)
    sync = env["sync"]

    snap.write_text(json.dumps({"version": 1, "queue": ["t001", "t999"]}), encoding="utf-8")
    assert goal_snapshot_migration_status(sync)
    with pytest.raises(ctx.SyncError, match="t999"):
        migrate_goal_snapshot(sync, env["src"], write=False)

    snap.write_text("{broken", encoding="utf-8")
    assert goal_snapshot_migration_status(sync)
    with pytest.raises(ctx.SyncError, match="损坏"):
        migrate_goal_snapshot(sync, env["src"], write=False)


def test_goal_snapshot_only_migration_persists_when_workspace_matches_head(env, tmp_path, monkeypatch):
    """即使工作区无交付差异，goal_queue.json 迁移也不得被 no_op 回滚。"""
    install_fake_md_kx(tmp_path, monkeypatch)
    consumer = _init_contract_ready_consumer(env)
    _write_task(consumer, "t001", "alpha")
    _commit_consumer_baseline(consumer)

    from template_sync import transaction as tx_mod
    monkeypatch.setattr(tx_mod, "_run_tests", lambda _root: True)
    assert cmd_apply(argparse.Namespace(sync=env["sync"], skip_tests=True, decisions={})) == 0
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "sync baseline")
    assert cmd_transaction(argparse.Namespace(sync=env["sync"], action="accept")) == 0

    snap = state_file(consumer, "goal_queue.json")
    snap.write_text(
        json.dumps({"version": 1, "created_at": "2026-09-17T08:03:47+08:00", "queue": ["t001"]}),
        encoding="utf-8",
    )
    assert cmd_apply(argparse.Namespace(sync=env["sync"], skip_tests=True, decisions={})) == 0
    assert json.loads(snap.read_text(encoding="utf-8"))["version"] == 2
    assert open_transactions(consumer) == []


def test_goal_snapshot_rolls_back_on_apply_failure(env, tmp_path, monkeypatch):
    install_fake_md_kx(tmp_path, monkeypatch)
    consumer = _init_contract_ready_consumer(env)
    _write_task(consumer, "t001", "alpha")
    _commit_consumer_baseline(consumer)

    snap = state_file(consumer, "goal_queue.json")
    snap.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps({"version": 1, "created_at": "2026-09-17T08:03:47+08:00", "queue": ["t001"]})
    snap.write_text(raw, encoding="utf-8")

    from template_sync import apply as apply_mod
    monkeypatch.setattr(apply_mod, "_apply_sync_write", lambda *a, **kw: (_ for _ in ()).throw(OSError("boom")))
    assert cmd_apply(argparse.Namespace(sync=env["sync"], skip_tests=True, decisions={})) == 1
    assert snap.read_text(encoding="utf-8") == raw


def test_goal_snapshot_and_state_root_combined_preview_and_apply(env, tmp_path, monkeypatch, capsys):
    install_fake_md_kx(tmp_path, monkeypatch)
    consumer = _init_contract_ready_consumer(env)
    _write_task(consumer, "t001", "alpha")
    legacy = consumer / "docs/runtime/goal_queue.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text('{"queue": ["t001"]}\n', encoding="utf-8")
    _commit_consumer_baseline(consumer)

    sync = env["sync"]
    assert cmd_status(argparse.Namespace(sync=sync, fetch=False)) == 0
    out = capsys.readouterr().out
    assert "state-root" in out and "goal-snapshot" in out

    assert cmd_apply(argparse.Namespace(sync=sync, skip_tests=True, decisions={})) == 0
    assert not legacy.exists()
    migrated = json.loads(state_file(consumer, "goal_queue.json").read_text(encoding="utf-8"))
    assert migrated == {"version": 2, "created_at": "", "queue": ["t001"], "edges": []}
    assert goal_snapshot_migration_status(sync) == []


def test_retired_docs_blocks_symlink(env, tmp_path, monkeypatch, capsys):
    install_fake_md_kx(tmp_path, monkeypatch)
    consumer = _init_contract_ready_consumer(env)
    target = consumer / "README.md"
    target.write_text("# consumer\n", encoding="utf-8")
    link = consumer / "docs/pending/README.md"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to("../../README.md")
    assert target.is_file()
    _commit_consumer_baseline(consumer)

    assert cmd_status(argparse.Namespace(sync=env["sync"], fetch=False)) == 0
    assert "BLOCKED" in capsys.readouterr().out
    assert cmd_apply(argparse.Namespace(sync=env["sync"], skip_tests=True, decisions={})) == 1
    assert link.is_symlink()


# ---------- retired-docs ----------


PURE_PENDING_README = """# 待办与不办总账

项目里「已知、还欠着」的事只在本目录登记：未修 bug、review 遗留、技术债、该做未做的需求，以及用户已确认暂搁的事项。

持续口述登记用 skill `pending-record`（澄清后派子代理落盘；bug 走 `task-bug` 分析再记）。从总账捞成 task 用 `task-from-pending`。

一条目一文件，文件名 `pNNN_{slug}.md`；三态由所在目录表达：

|目录|语义|
|---|---|
|`docs/pending/todo/`|未闭环、待启动|
|`docs/pending/parked/`|用户显式确认暂搁（不办）；不等于闭环|
|`docs/archive/pending/`|已闭环|

`pNNN` 全局递增，跨三个目录共享一条序列，历史编号不复用。已验证的技术发现不属于待办，写 `docs/findings/`。

## 命令

```bash
.repo_template/scripts/pending.py new --slug cli_exit_code            # 建普通条目
.repo_template/scripts/pending.py new --slug crash_on_empty --kind bug # 建 bug 条目
.repo_template/scripts/pending.py list --state all                     # 列举
.repo_template/scripts/pending.py archive p047 --fix-ref t012 --write  # 闭环
.repo_template/scripts/pending.py park p047 --reason "等外部依赖" --write
.repo_template/scripts/pending.py revive p047 --write                  # parked → todo
```

`new` 在 git 公共目录的排他锁内完成「扫描取号 → 建文件」，并发 worker 不会撞号；禁止手工创建条目文件。迁移一律走命令，命令默认 dry-run，加 `--write` 落盘。

## 字段

两种模板，按条目性质选一种。`- 处理` 未闭环写「未开」，闭环写 `{tid}` 或外部动作说明，暂搁写「不办」。

- 普通（需求 / 遗留 / 技术债）：`- 来源` / `- 内容` / `- 处理`。`- 来源` 写清出处：finding_id、原 tid、用户提出，或技术债自查。
- bug：`- 现象` / `- 影响` / `- 根因` / `- 测试缺口` / `- 线索` / `- 处理`。bug 由 `task-bug` 或 `pending-record`（派子代理跑 task-bug 第 1–6 步：含同类位点扫描与合并）登记并完成根因与补测分析。`- 根因` 写共享机制与已确认同类位点清单（或「已扫无同类」）；`- 影响` / `- 测试缺口` 覆盖合并后的位点并集。

`parked/` 条目追加 `- 暂搁：YYYY-MM-DD 决定不办的理由`，写清为什么现在不动（风险可控、排期靠后、等外部依赖等）。复活时 `revive` 会删该字段并把 `- 处理` 改回「未开」，保留原编号。
"""

PURE_FINDINGS_README = """# 发现总账

已被验证的技术发现与踩坑记录，跨 task 复用。一条目一文件，文件名 `dNNN_{slug}.md`。

- spike 结论收尾时抽一条到这里；spike 报告全文留在 `docs/archive/spikes/`。
- 日常发现（工具行为、平台差异、依赖坑、性能特征、被证伪的假设）随时追加。
- 只记**已验证**的事实与证据来源；推测和待确认的写进对应 task 的 spec 上下文区：只有用户或外部环境能核实的标 `UNVERIFIED-BLOCKING`，agent 可实验核实的标 `UNVERIFIED-SPIKE`。
- 发现是长期资产，不存在「闭环」，因此不迁 archive。失效时保留条目，改写 `- 现状` 为「YYYY-MM-DD 失效：原因」。
- `dNNN` 全局递增不复用。

## 命令

```bash
python3 .repo_template/scripts/findings.py new --slug uv_lock_platform_marker
python3 .repo_template/scripts/findings.py list
```

`new` 在 git 公共目录的排他锁内完成「扫描取号 → 建文件」，并发 worker 不会撞号；禁止手工创建条目文件。

## 字段

`- 来源` / `- 结论` / `- 证据` / `- 影响` / `- 现状`。`- 来源` 写 `sNNN` spike、`tNNN` task、或「日常」。新条目现状写「有效」。
"""


def test_retired_docs_deletes_pure_obsolete_readmes_in_apply(env, tmp_path, monkeypatch, capsys):
    install_fake_md_kx(tmp_path, monkeypatch)
    consumer = _init_contract_ready_consumer(env)
    pending_readme = consumer / "docs/pending/README.md"
    findings_readme = consumer / "docs/findings/README.md"
    pending_readme.parent.mkdir(parents=True, exist_ok=True)
    findings_readme.parent.mkdir(parents=True, exist_ok=True)
    pending_readme.write_text(PURE_PENDING_README, encoding="utf-8")
    findings_readme.write_text(PURE_FINDINGS_README, encoding="utf-8")
    _commit_consumer_baseline(consumer)

    sync = env["sync"]
    assert cmd_status(argparse.Namespace(sync=sync, fetch=False)) == 0
    status_out = capsys.readouterr().out
    assert "retired-docs" in status_out
    assert "BLOCKED" not in status_out

    assert cmd_plan(argparse.Namespace(sync=sync, fetch=False)) == 0
    assert "retired-docs" in capsys.readouterr().out

    assert cmd_apply(argparse.Namespace(sync=sync, skip_tests=True, decisions={})) == 0
    assert not pending_readme.exists()
    assert not findings_readme.exists()
    assert retired_docs_migration_status(sync) == []


def test_retired_docs_blocks_when_consumer_customized_obsolete_readme(env, tmp_path, monkeypatch, capsys):
    install_fake_md_kx(tmp_path, monkeypatch)
    consumer = _init_contract_ready_consumer(env)
    findings_readme = consumer / "docs/findings/README.md"
    findings_readme.parent.mkdir(parents=True, exist_ok=True)
    custom = PURE_FINDINGS_README + "\n**历史条目**保留项目原字段约定。\n"
    findings_readme.write_text(custom, encoding="utf-8")
    _commit_consumer_baseline(consumer)

    sync = env["sync"]
    assert cmd_status(argparse.Namespace(sync=sync, fetch=False)) == 0
    out = capsys.readouterr().out
    assert "BLOCKED" in out and "保留项目原字段约定" in out
    assert cmd_apply(argparse.Namespace(sync=sync, skip_tests=True, decisions={})) == 1
    err = capsys.readouterr().err
    assert "保留项目原字段约定" in err
    assert findings_readme.read_text(encoding="utf-8") == custom


def test_retired_docs_ignores_clean_consumer_readme(env):
    consumer = _init_contract_ready_consumer(env)
    pending_readme = consumer / "docs/pending/README.md"
    pending_readme.parent.mkdir(parents=True, exist_ok=True)
    pending_readme.write_text("# 项目待办说明\n\n仅记录本仓库业务待办范围。\n", encoding="utf-8")
    assert retired_docs_migration_status(env["sync"]) == []


def test_retired_docs_rolls_back_deleted_readme_on_apply_failure(env, tmp_path, monkeypatch):
    install_fake_md_kx(tmp_path, monkeypatch)
    consumer = _init_contract_ready_consumer(env)
    pending_readme = consumer / "docs/pending/README.md"
    pending_readme.parent.mkdir(parents=True, exist_ok=True)
    pending_readme.write_text(PURE_PENDING_README, encoding="utf-8")
    _commit_consumer_baseline(consumer)

    from template_sync import apply as apply_mod
    monkeypatch.setattr(apply_mod, "_apply_sync_write", lambda *a, **kw: (_ for _ in ()).throw(OSError("boom")))
    assert cmd_apply(argparse.Namespace(sync=env["sync"], skip_tests=True, decisions={})) == 1
    assert pending_readme.read_text(encoding="utf-8") == PURE_PENDING_README
