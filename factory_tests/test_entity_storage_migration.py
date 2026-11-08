"""test_entity_storage_migration.py - 实体存储一次性迁移端到端测试。

覆盖：
1. dry-run：默认执行不改写任何文件，输出详细计划；
2. --write 真实写入：
   - docs/archive/tasks/tNNN_slug/ -> docs/tasks/tNNN_slug/ (done 需 handoff.json 与实现血缘)
   - docs/tasks/tNNN_slug/ 原位保留
   - docs/pending/todo/pNNN.md -> docs/pending/pNNN.md (status: todo)
   - docs/pending/parked/pNNN.md -> docs/pending/pNNN.md (status: parked, 保留 reason)
   - docs/archive/pending/pNNN.md -> docs/pending/pNNN.md (status: closed, 保留 resolution 与 reason)
   - docs/archive/spikes/sNNN/ -> docs/spikes/sNNN/ (status: completed, 校验闭环证据与 findings 关联)
   - docs/archive/tasks_audit.log -> docs/runtime/tasks_audit.log (保持原始字节)
   - 移除旧 docs/archive/handoff.md
   - 更新 docs/handoff.md 中的引用并清理空旧目录
3. 重跑零写入（幂等性）
4. 阻断条件：
   - 同号多处、目标占用
   - 缺少闭环依据（缺少 resolution 或 reason，无兜底）
   - 同字节 audit.log 存在时严格阻断，绝不擅自删除
   - done 任务缺少合法实现 commit 或血缘不合
   - 归档 spike 报告缺少有效 findings 关联（dNNN）
   - 目录来源与 Front Matter 状态矛盾（归档 pending 声明 todo，todo pending 声明 closed，归档 spike 声明 active）
   - 工作区存在未暂存或已暂存修改
   - 清理旧目录非空直接阻断报错
5. 事务保护与失败回滚
6. apply 门禁阻断与提示
7. 迁移后依赖实现 (dependency implementations) 统一查验
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task.context import RepoContext
from repo_task.integration import _dependency_implementations
from template_sync import context as ctx
from template_sync.apply import cmd_apply
from template_sync.entity_storage_migration import (
    MigrationError,
    entity_storage_migration_status,
    migrate_entity_storage,
)
from template_sync.migrate import cmd_migrate


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "consumer"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Tester")

    # 创建基本目录
    (root / "docs/tasks").mkdir(parents=True)
    (root / "docs/pending").mkdir(parents=True)
    (root / "docs/spikes").mkdir(parents=True)
    (root / "docs/findings").mkdir(parents=True)
    (root / "docs/runtime").mkdir(parents=True)
    (root / "docs/archive/reviews").mkdir(parents=True)

    # 初始提交
    (root / "README.md").write_text("# Repo\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: initial commit")
    return root


def _setup_legacy_repo(repo: Path) -> dict[str, str]:
    """在 repo 中构造包含旧 Task、Pending、Spike、Audit、Handoff 归档的场景。"""
    # 1. Active task: docs/tasks/t002_beta
    t2 = repo / "docs/tasks/t002_beta"
    t2.mkdir(parents=True)
    (t2 / "task.md").write_text("# t002 beta\n", encoding="utf-8")
    (t2 / "progress.md").write_text(
        "---\ntid: \"t002\"\nslug: \"beta\"\nstatus: \"active\"\n---\n# Task 进展\n",
        encoding="utf-8",
    )

    # 2. Archived task (done): docs/archive/tasks/t001_alpha
    t1 = repo / "docs/archive/tasks/t001_alpha"
    t1.mkdir(parents=True)
    (t1 / "task.md").write_text("# t001 alpha\n", encoding="utf-8")
    (t1 / "progress.md").write_text(
        "---\ntid: \"t001\"\nslug: \"alpha\"\nstatus: \"done\"\n---\n# Task 进展\n",
        encoding="utf-8",
    )
    # 创建真实的实现 commit 与 handoff.json，以便后续测试依赖实现统一查找
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    handoff_data = {
        "tid": "t001",
        "attempt": 1,
        "execution_id": "exec_t001",
        "status": "done",
        "branch": "t001_alpha",
        "base_sha": head_before,
        "spec_source_sha": head_before,
        "tests": "pytest passed",
        "blackbox": "passed",
        "review": "PASS",
        "review_scope": "docs/tasks/t001_alpha/task.md",
        "pending": [],
        "findings": [],
    }
    (t1 / "handoff.json").write_text(json.dumps(handoff_data, indent=2) + "\n", encoding="utf-8")
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src/alpha.py").write_text("# alpha implementation\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feat(t001): implement alpha")
    impl_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    # 3. Pending todo: docs/pending/todo/p001_fix_cli.md
    p1 = repo / "docs/pending/todo/p001_fix_cli.md"
    p1.parent.mkdir(parents=True, exist_ok=True)
    p1.write_text("# p001 fix cli\n\n- 来源：用户提出\n- 内容：修复 cli 退出码\n- 处理：未开\n", encoding="utf-8")

    # 4. Pending parked: docs/pending/parked/p002_wait_dep.md
    p2 = repo / "docs/pending/parked/p002_wait_dep.md"
    p2.parent.mkdir(parents=True, exist_ok=True)
    p2.write_text(
        "# p002 wait dep\n\n- 来源：架构评审\n- 内容：升级网络库\n- 暂搁：2026-10-01 等待上游稳定\n- 处理：不办\n",
        encoding="utf-8",
    )

    # 5. Pending closed: docs/archive/pending/p003_closed_item.md
    p3 = repo / "docs/archive/pending/p003_closed_item.md"
    p3.parent.mkdir(parents=True, exist_ok=True)
    p3.write_text(
        "# p003 closed item\n\n- 来源：自查\n- 内容：历史问题\n- 处理：t001；resolution=resolved；reason=已在 t001 修复并验证通过\n",
        encoding="utf-8",
    )

    # 6. Archived spike: docs/archive/spikes/s001_kv_store (包含 d001 关联)
    s1 = repo / "docs/archive/spikes/s001_kv_store"
    s1.mkdir(parents=True, exist_ok=True)
    (s1 / "report.md").write_text(
        "# Spike s001: kv_store\n\n## 背景\n测试选型。\n\n## 结论\n采纳 SQLite方案。\n\n## 关联 findings\n- `d001`\n\n## 采纳决定\n直接进入后续任务。\n",
        encoding="utf-8",
    )
    f1 = repo / "docs/findings/d001_kv_bench.md"
    f1.parent.mkdir(parents=True, exist_ok=True)
    f1.write_text("# d001 kv bench\n\n- 结论：SQLite 满足需求\n", encoding="utf-8")

    # 7. Active spike: docs/spikes/s002_network
    s2 = repo / "docs/spikes/s002_network"
    s2.mkdir(parents=True, exist_ok=True)
    (s2 / "report.md").write_text(
        "---\nsid: \"s002\"\nslug: \"network\"\nstatus: \"active\"\n---\n# Spike s002\n\n调研网络驱动。\n",
        encoding="utf-8",
    )

    # 8. tasks_audit.log
    old_audit = repo / "docs/archive/tasks_audit.log"
    old_audit.parent.mkdir(parents=True, exist_ok=True)
    old_audit.write_text("2026-10-01 rewind t001\n2026-10-02 finish t001\n", encoding="utf-8")

    # 9. docs/archive/handoff.md (提交到 git)
    old_handoff = repo / "docs/archive/handoff.md"
    old_handoff.write_text("# 历史交接\n\n## 2026-09-01 旧交接\n- note: foo\n", encoding="utf-8")

    # 10. docs/handoff.md (当前交接，含有对旧路径的引用)
    handoff_doc = repo / "docs/handoff.md"
    handoff_doc.write_text(
        "# 项目交接\n\n- 参考历史任务 [t001](docs/archive/tasks/t001_alpha)\n- 待办见 [p001](docs/pending/todo/p001_fix_cli.md)\n",
        encoding="utf-8",
    )

    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: setup legacy layout")

    return {"impl_sha": impl_sha, "base_sha": head_before}


def test_entity_storage_migration_dry_run(repo, monkeypatch, capsys):
    _setup_legacy_repo(repo)
    sync = ctx.SyncContext.for_consumer(repo)

    # dry-run
    args = argparse.Namespace(kind="entity-storage", write=False, sync=sync)
    res = cmd_migrate(args)
    assert res == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "Task t001" in out
    assert "Pending p001" in out

    # 验证文件完全未动
    assert (repo / "docs/archive/tasks/t001_alpha").is_dir()
    assert not (repo / "docs/tasks/t001_alpha").exists()
    assert (repo / "docs/pending/todo/p001_fix_cli.md").is_file()
    assert not (repo / "docs/pending/p001_fix_cli.md").exists()
    assert (repo / "docs/archive/handoff.md").is_file()


def test_entity_storage_migration_write_e2e(repo, capsys):
    shas = _setup_legacy_repo(repo)
    sync = ctx.SyncContext.for_consumer(repo)

    # 显式落盘迁移
    args = argparse.Namespace(kind="entity-storage", write=True, sync=sync)
    res = cmd_migrate(args)
    assert res == 0
    out = capsys.readouterr().out
    assert "落盘完成" in out

    # 1. Tasks
    t1_target = repo / "docs/tasks/t001_alpha"
    assert t1_target.is_dir()
    assert not (repo / "docs/archive/tasks").exists()
    prog_text = (t1_target / "progress.md").read_text(encoding="utf-8")
    assert "status: \"done\"" in prog_text

    # 2. Pendings
    p1 = repo / "docs/pending/p001_fix_cli.md"
    assert p1.is_file()
    p1_text = p1.read_text(encoding="utf-8")
    assert "status: \"todo\"" in p1_text
    assert "pid: \"p001\"" in p1_text

    # 验证 p002 保留 reason
    p2 = repo / "docs/pending/p002_wait_dep.md"
    assert p2.is_file()
    p2_text = p2.read_text(encoding="utf-8")
    assert "status: \"parked\"" in p2_text
    assert "reason: \"2026-10-01 等待上游稳定\"" in p2_text

    # 验证 p003 保留 resolution 与 reason
    p3 = repo / "docs/pending/p003_closed_item.md"
    assert p3.is_file()
    p3_text = p3.read_text(encoding="utf-8")
    assert "status: \"closed\"" in p3_text
    assert "resolution: \"resolved\"" in p3_text
    assert "reason: \"已在 t001 修复并验证通过\"" in p3_text

    # 旧 pending 目录已被严格 rmdir 清理
    assert not (repo / "docs/pending/todo").exists()
    assert not (repo / "docs/pending/parked").exists()
    assert not (repo / "docs/archive/pending").exists()

    # 3. Spikes
    s1 = repo / "docs/spikes/s001_kv_store"
    assert s1.is_dir()
    s1_text = (s1 / "report.md").read_text(encoding="utf-8")
    assert "status: \"completed\"" in s1_text
    assert not (repo / "docs/archive/spikes").exists()

    # 4. tasks_audit.log
    audit = repo / "docs/runtime/tasks_audit.log"
    assert audit.is_file()
    assert "finish t001" in audit.read_text(encoding="utf-8")
    assert not (repo / "docs/archive/tasks_audit.log").exists()

    # 5. handoff.md
    assert not (repo / "docs/archive/handoff.md").exists()
    handoff_text = (repo / "docs/handoff.md").read_text(encoding="utf-8")
    assert "docs/archive/tasks/" not in handoff_text
    assert "docs/tasks/t001_alpha" in handoff_text

    # 6. migration status 查验完全为当前布局
    assert entity_storage_migration_status(sync=sync) == []

    # 7. 查验依赖实现定位（方案第 257–269 行）
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: migrate entity storage")
    task_repo = RepoContext.for_root(repo)
    impl_candidates = _dependency_implementations("t001", repo=task_repo)
    assert impl_candidates == [shas["impl_sha"]]


def test_entity_storage_migration_idempotent_zero_write(repo, capsys):
    _setup_legacy_repo(repo)
    sync = ctx.SyncContext.for_consumer(repo)

    # 第一次迁移
    cmd_migrate(argparse.Namespace(kind="entity-storage", write=True, sync=sync))
    capsys.readouterr()

    # 保持工作区干净后，第二次迁移（幂等性，零写入）
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: commit migration")
    res = cmd_migrate(argparse.Namespace(kind="entity-storage", write=True, sync=sync))
    assert res == 0
    out = capsys.readouterr().out
    assert "已是当前格式，无需迁移" in out


def test_entity_storage_migration_blocks_on_duplicate_tid(repo):
    _setup_legacy_repo(repo)
    dup_t1 = repo / "docs/tasks/t001_alpha"
    dup_t1.mkdir(parents=True)
    (dup_t1 / "progress.md").write_text("---\ntid: \"t001\"\nslug: \"alpha\"\nstatus: \"active\"\n---\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: add duplicate tid")
    sync = ctx.SyncContext.for_consumer(repo)

    with pytest.raises(MigrationError, match="同号多处.*t001"):
        migrate_entity_storage(sync=sync, write=False)


def test_entity_storage_migration_blocks_on_duplicate_pid(repo):
    _setup_legacy_repo(repo)
    dup_p1 = repo / "docs/archive/pending/p001_duplicate.md"
    dup_p1.write_text("# p001 duplicate\n\n- 处理：resolution=resolved；reason=done\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: add duplicate pid")
    sync = ctx.SyncContext.for_consumer(repo)

    with pytest.raises(MigrationError, match="同号多处.*p001"):
        migrate_entity_storage(sync=sync, write=False)


def test_entity_storage_migration_blocks_on_missing_closure_reason(repo):
    _setup_legacy_repo(repo)
    p3 = repo / "docs/archive/pending/p003_closed_item.md"
    p3.write_text("# p003 closed item\n\n- 处理：未开\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: tamper p003 reason")
    sync = ctx.SyncContext.for_consumer(repo)

    with pytest.raises(MigrationError, match="缺少有效闭环结论|缺少闭环依据"):
        migrate_entity_storage(sync=sync, write=False)


def test_entity_storage_migration_blocks_on_missing_resolution_no_default(repo):
    """根除默认兜底：旧 pending 若仅有 reason 而缺失 resolution，绝不自动 fallback 为 resolved，必须阻断。"""
    _setup_legacy_repo(repo)
    p3 = repo / "docs/archive/pending/p003_closed_item.md"
    p3.write_text(
        "# p003 closed item\n\n- 处理：reason=已修复并验证，但未给 resolution 字段\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: remove resolution from p003")
    sync = ctx.SyncContext.for_consumer(repo)

    with pytest.raises(MigrationError, match="缺少有效闭环结论"):
        migrate_entity_storage(sync=sync, write=False)


def test_entity_storage_migration_blocks_on_identical_audit_log_existing(repo):
    """方案契约第 253 行：新旧 audit.log 同字节也绝不擅自删除，阻断并要求人工介入。"""
    _setup_legacy_repo(repo)
    new_audit = repo / "docs/runtime/tasks_audit.log"
    old_audit = repo / "docs/archive/tasks_audit.log"
    new_audit.parent.mkdir(parents=True, exist_ok=True)
    new_audit.write_bytes(old_audit.read_bytes())
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: create identical runtime audit log")
    sync = ctx.SyncContext.for_consumer(repo)

    with pytest.raises(MigrationError, match="目标占用.*禁止擅自覆盖或删除旧归档日志"):
        migrate_entity_storage(sync=sync, write=False)


def test_entity_storage_migration_blocks_on_tampered_task_lineage(repo):
    """迁移预检中自动调用实现提交与血缘复验门禁：篡改 base_sha 导致无法匹配实现提交时直接阻断。"""
    _setup_legacy_repo(repo)
    t1_handoff = repo / "docs/archive/tasks/t001_alpha/handoff.json"
    data = json.loads(t1_handoff.read_text(encoding="utf-8"))
    data["base_sha"] = "0" * 40
    t1_handoff.write_text(json.dumps(data), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "tamper t001 handoff base_sha")
    sync = ctx.SyncContext.for_consumer(repo)

    with pytest.raises(MigrationError, match="无法解析为 commit|复验失败|血缘不合法"):
        migrate_entity_storage(sync=sync, write=False)


def test_entity_storage_migration_blocks_on_spike_missing_findings_ref(repo):
    """严格校验 spike 报告必须包含有效 findings 关联（如 dNNN 引用）。"""
    _setup_legacy_repo(repo)
    s1_rep = repo / "docs/archive/spikes/s001_kv_store/report.md"
    s1_rep.write_text(
        "# Spike s001: kv_store\n\n## 结论\n采纳 SQLite方案。\n\n## 采纳决定\n直接进入后续任务。\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "remove findings ref from s001 report")
    sync = ctx.SyncContext.for_consumer(repo)

    with pytest.raises(MigrationError, match="缺少有效 findings 关联"):
        migrate_entity_storage(sync=sync, write=False)


def test_entity_storage_migration_blocks_on_pending_status_conflict(repo):
    """目录来源与 Front Matter 状态一致性检查：归档待办声明为 todo 时直接阻断。"""
    _setup_legacy_repo(repo)
    p3 = repo / "docs/archive/pending/p003_closed_item.md"
    p3.write_text(
        "---\npid: \"p003\"\nslug: \"closed_item\"\nstatus: \"todo\"\n---\n# p003\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "tamper p003 status to todo in archive")
    sync = ctx.SyncContext.for_consumer(repo)

    with pytest.raises(MigrationError, match="状态与所在目录矛盾"):
        migrate_entity_storage(sync=sync, write=False)


def test_entity_storage_migration_blocks_on_spike_status_conflict(repo):
    """目录来源与 Front Matter 状态一致性检查：归档 spike 声明为 active 时直接阻断。"""
    _setup_legacy_repo(repo)
    s1_rep = repo / "docs/archive/spikes/s001_kv_store/report.md"
    s1_rep.write_text(
        "---\nsid: \"s001\"\nslug: \"kv_store\"\nstatus: \"active\"\n---\n# Spike s001\n\n## 结论\n采纳方案 d001。\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "tamper s001 status to active in archive")
    sync = ctx.SyncContext.for_consumer(repo)

    with pytest.raises(MigrationError, match="状态与目录矛盾"):
        migrate_entity_storage(sync=sync, write=False)


def test_entity_storage_migration_blocks_on_dirty_working_tree(repo):
    """迁移前必须检查工作区干净（staged 和 unstaged 均无修改）。"""
    _setup_legacy_repo(repo)
    sync = ctx.SyncContext.for_consumer(repo)

    # 1. 测试 unstaged 修改阻断
    (repo / "README.md").write_text("# Dirty\n", encoding="utf-8")
    with pytest.raises(MigrationError, match="工作区必须干净"):
        migrate_entity_storage(sync=sync, write=False)

    # 2. 测试 staged 修改阻断
    _git(repo, "add", "README.md")
    with pytest.raises(MigrationError, match="工作区必须干净"):
        migrate_entity_storage(sync=sync, write=False)


def test_entity_storage_migration_blocks_and_rolls_back_on_non_empty_dir_clean(repo):
    """严格清理空目录：若清理非空直接阻断报错并触发事务完整回滚。"""
    _setup_legacy_repo(repo)
    # 在 docs/archive/tasks 留下未预期的文件
    residual = repo / "docs/archive/tasks/residual_unknown.txt"
    residual.write_text("should block clean", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add residual to archive tasks")
    sync = ctx.SyncContext.for_consumer(repo)

    with pytest.raises(MigrationError, match="清理旧目录失败.*仍包含非空残留文件"):
        migrate_entity_storage(sync=sync, write=True)

    # 回滚后验证现场恢复：t001 仍在旧归档目录中
    assert (repo / "docs/archive/tasks/t001_alpha").is_dir()
    assert not (repo / "docs/tasks/t001_alpha").exists()


def test_entity_storage_migration_rollback_on_failure(repo, monkeypatch):
    _setup_legacy_repo(repo)
    sync = ctx.SyncContext.for_consumer(repo)

    real_atomic_write = sys.modules["template_sync.entity_storage_migration"].atomic_write_text
    calls = {"count": 0}

    def flaky_write(path, text):
        calls["count"] += 1
        if calls["count"] >= 3:
            raise OSError("simulated disk error during migration")
        return real_atomic_write(path, text)

    monkeypatch.setattr("template_sync.entity_storage_migration.atomic_write_text", flaky_write)

    with pytest.raises(MigrationError, match="已自动回滚"):
        migrate_entity_storage(sync=sync, write=True)

    # 回滚后核对现场完整性：原归档任务仍存在
    assert (repo / "docs/archive/tasks/t001_alpha").is_dir()
    assert not (repo / "docs/tasks/t001_alpha").exists()
    assert (repo / "docs/pending/todo/p001_fix_cli.md").is_file()
    assert (repo / "docs/archive/handoff.md").is_file()


def test_apply_blocks_on_legacy_entity_storage(repo, capsys):
    _setup_legacy_repo(repo)
    sync = ctx.SyncContext.for_consumer(repo)

    # 模拟外部模板源
    src_dir = repo.parent / "template_src"
    src_dir.mkdir()
    _git(src_dir, "init", "-b", "main")
    (src_dir / ".repo_template").mkdir()
    (src_dir / ".repo_template/scripts").mkdir()
    (src_dir / ".repo_template/scripts/task.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    (src_dir / "README.md").write_text("# Template\n", encoding="utf-8")
    _git(src_dir, "add", "-A")
    _git(src_dir, "commit", "-m", "init template")

    # 在消费仓记录同步状态
    state_file = repo / ".repo_template/sync_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({
        "template_source": {"kind": "path", "value": str(src_dir)},
        "last_synced_commit": None,
        "last_synced_at": None,
        "user_prompts": [],
    }), encoding="utf-8")

    args = argparse.Namespace(sync=sync, allow_dirty_source=True, skip_tests=True)
    ret = cmd_apply(args, sync=sync)
    assert ret == 1
    err = capsys.readouterr().err
    assert "检测到旧实体存储布局" in err
    assert "migrate entity-storage --write" in err
