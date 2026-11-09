"""test_entity_storage_migration.py - 实体存储一次性迁移端到端测试。

覆盖：
1. dry-run：默认执行不改写任何文件，输出详细计划；
2. --write 真实写入：
   - docs/archive/tasks/tNNN_slug/ -> docs/tasks/tNNN_slug/ (校验状态字段为 done/dropped；迁移不复验 handoff/实现血缘，使用时核验)
   - docs/tasks/tNNN_slug/ 原位保留
   - docs/pending/todo/pNNN.md -> docs/pending/pNNN.md (status: todo)
   - docs/pending/parked/pNNN.md -> docs/pending/pNNN.md (status: parked, 保留 reason)
   - docs/archive/pending/pNNN.md -> docs/pending/pNNN.md (status: closed, 保留 resolution 与 reason)
   - docs/archive/spikes/sNNN/ -> docs/spikes/sNNN/ (status: completed，校验报告与 findings 闭环证据)
   - docs/archive/tasks_audit.log -> docs/runtime/tasks_audit.log (保持原始字节)
   - 移除旧 docs/archive/handoff.md
   - 更新 docs/handoff.md 中的引用并清理空旧目录（含仅剩占位文件的旧目录）
3. 重跑零写入（幂等性）
4. 阻断条件：
   - 同号多处、目标占用
   - 缺少闭环依据（缺少 resolution 或 reason，无兜底）
   - 同字节 audit.log 存在时严格阻断，绝不擅自删除
   - 身份与文件名不符（声明 pid/slug 与文件名不一致，拒绝静默改写）
   - 归档 spike 报告缺少有效 findings 关联（dNNN）
   - 目录来源与 Front Matter 状态矛盾（归档 pending 声明 todo，todo pending 声明 closed，归档 spike 声明 active）
   - 工作区存在未暂存或已暂存修改（仅 entity-storage 生效）
   - 清理旧目录非空直接阻断报错
5. 事务保护与失败回滚（逐项事务，已完成步骤保留）
6. apply 门禁阻断与提示
7. 迁移后依赖实现 (dependency implementations) 统一在使用时查验（迁移不复验血缘）
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task.context import RepoContext, TaskDataError
from repo_task.documents import parse_front_matter_text
from repo_task.integration import _dependency_implementations
from repo_task.spikes import validate_spike_metadata
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


def _docs_snapshot(root: Path) -> dict[str, bytes]:
    """docs/ 全树相对路径 -> 字节内容，用于回滚后整体比对。"""
    docs = root / "docs"
    return {
        path.relative_to(docs).as_posix(): path.read_bytes()
        for path in sorted(docs.rglob("*")) if path.is_file()
    }


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

    # 初始提交（.scratch/ 备份不得进入 Git）
    (root / ".gitignore").write_text(".scratch/\n", encoding="utf-8")
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

    # 工作区清洁：.scratch/ 事务备份被忽略，本次落盘只涉及 docs/ 与 schema 标记
    status_lines = _git(repo, "status", "--porcelain", "-uall").stdout.splitlines()
    assert not any(".scratch" in line for line in status_lines)
    changed_paths = [line[3:] for line in status_lines]
    assert changed_paths
    assert all(
        path.startswith("docs/") or path == ".repo_template/entity_schema.json"
        for path in changed_paths
    ), changed_paths

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


def test_entity_storage_migration_preserves_tampered_handoff_bytes_and_defers_verification_to_use(repo):
    """信任边界（D3-T7）：迁移只做存储转换与完整性校验，不复验历史实现关联。
    篡改 base_sha 的 handoff 不再阻断迁移且字节原样保留；被用作依赖时仍被业务门禁拒绝。"""
    _setup_legacy_repo(repo)
    t1_handoff = repo / "docs/archive/tasks/t001_alpha/handoff.json"
    data = json.loads(t1_handoff.read_text(encoding="utf-8"))
    data["base_sha"] = "0" * 40
    tampered_bytes = json.dumps(data).encode("utf-8")
    t1_handoff.write_bytes(tampered_bytes)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "tamper t001 handoff base_sha")
    sync = ctx.SyncContext.for_consumer(repo)

    # 历史证据缺失/损坏不再阻断迁移
    migrate_entity_storage(sync=sync, write=True)

    # 证据保真：handoff.json 字节原样保留，不补造字段、不重新签发
    moved = repo / "docs/tasks/t001_alpha/handoff.json"
    assert moved.read_bytes() == tampered_bytes

    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: migrate entity storage and tampered handoff")
    # 用作依赖时仍被业务门禁拒绝（无 override、无历史格式兼容分支）
    with pytest.raises(TaskDataError, match=r"handoff\.base_sha"):
        _dependency_implementations("t001", repo=RepoContext.for_root(repo))


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


def test_entity_storage_migration_status_reports_flat_active_spike_without_front_matter(repo):
    """R04：扁平布局的 active spike 完全没有 Front Matter 时，status 必须报待迁移。"""
    schema = repo / ".repo_template/entity_schema.json"
    schema.parent.mkdir(parents=True)
    schema.write_text('{"version": 1}\n', encoding="utf-8")

    report = repo / "docs/spikes/s001_network/report.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Spike s001\n\n调研网络驱动。\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: flat active spike without front matter")
    sync = ctx.SyncContext.for_consumer(repo)

    problems = entity_storage_migration_status(sync=sync)
    assert any(
        "docs/spikes/s001_network/report.md" in item and "front matter" in item.lower()
        for item in problems
    ), problems


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


def test_entity_storage_migration_blocks_and_rolls_back_on_non_empty_dir_clean(repo, monkeypatch):
    """严格清理空目录：若清理非空直接阻断报错并触发事务完整回滚。"""
    _setup_legacy_repo(repo)
    sync = ctx.SyncContext.for_consumer(repo)

    # 预检合法通过；在搬移阶段向 docs/archive/tasks 留下残留文件触发清理空目录报错
    real_move = sys.modules["template_sync.entity_storage_migration"].shutil.move

    def move_with_residual(src, dst):
        res = real_move(src, dst)
        residual = repo / "docs/archive/tasks/residual_unknown.txt"
        residual.write_text("should block clean", encoding="utf-8")
        return res

    monkeypatch.setattr("template_sync.entity_storage_migration.shutil.move", move_with_residual)

    with pytest.raises(MigrationError, match="清理旧目录失败.*仍包含非空残留文件"):
        migrate_entity_storage(sync=sync, write=True)

    # 回滚后验证现场恢复：t001 仍在旧归档目录中
    assert (repo / "docs/archive/tasks/t001_alpha").is_dir()
    assert not (repo / "docs/tasks/t001_alpha").exists()


def test_entity_storage_migration_rollback_on_failure(repo, monkeypatch):
    _setup_legacy_repo(repo)
    sync = ctx.SyncContext.for_consumer(repo)
    before = _docs_snapshot(repo)

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

    # 回滚后对 docs/ 全树整体比对：t001/p001~p003/s001/audit.log/handoff/目标目录
    # 全部纳入，杜绝只核对部分文件而漏掉残留。
    assert _docs_snapshot(repo) == before


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
    ret = cmd_apply(args)
    assert ret == 1
    err = capsys.readouterr().err
    assert "检测到旧实体存储布局" in err
    assert "migrate entity-storage --write" in err


def test_entity_storage_migration_blocks_on_unknown_residual_files(repo):
    """散落未知文件检查：在 status 与 build_plan 中拦截非规范文件（非 p*.md、非规范 task/spike 目录）。"""
    _setup_legacy_repo(repo)
    # 在 docs/archive/tasks 留下非规范文件
    bad_file = repo / "docs/archive/tasks/unknown_file.txt"
    bad_file.write_text("unknown", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add unknown file to archive tasks")
    sync = ctx.SyncContext.for_consumer(repo)

    status_problems = entity_storage_migration_status(sync=sync)
    assert any("存在非规范任务项或未知散落文件" in p for p in status_problems)

    with pytest.raises(MigrationError, match="存在非规范任务项或未知散落文件"):
        migrate_entity_storage(sync=sync, write=False)

    # 清理并测试 docs/pending/todo 中的未知文件
    bad_file.unlink()
    bad_pending = repo / "docs/pending/todo/notes.txt"
    bad_pending.write_text("not a pending", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add unknown file to pending todo")

    status_problems = entity_storage_migration_status(sync=sync)
    assert any("存在非规范待办项或未知散落文件" in p for p in status_problems)

    with pytest.raises(MigrationError, match="存在非规范待办项或未知散落文件"):
        migrate_entity_storage(sync=sync, write=False)


def test_entity_storage_migration_blocks_on_nonexistent_finding_ref(repo):
    """Spike 证据真实性：校验 Spike 正文中的 dNNN 引用时，引用的 finding 文件必须真实存在。"""
    _setup_legacy_repo(repo)
    s1_rep = repo / "docs/archive/spikes/s001_kv_store/report.md"
    s1_rep.write_text(
        "# Spike s001: kv_store\n\n## 结论\n采纳 SQLite方案。\n\n## 关联 findings\n- `d999`\n\n## 采纳决定\n直接进入后续任务。\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "reference nonexistent finding d999")
    sync = ctx.SyncContext.for_consumer(repo)

    with pytest.raises(MigrationError, match="引用的 finding d999 在 docs/findings/ 中不存在真实文件"):
        migrate_entity_storage(sync=sync, write=False)


def test_entity_storage_migration_preserves_superseded_spike_metadata(repo):
    """Spike 状态保真：迁移归档 spike 时，若原 report 声明为 status: superseded，保留其原有替代关系。"""
    _setup_legacy_repo(repo)
    s1_rep = repo / "docs/archive/spikes/s001_kv_store/report.md"
    s1_rep.write_text(
        "---\n"
        "sid: \"s001\"\n"
        "slug: \"kv_store\"\n"
        "status: \"superseded\"\n"
        "superseded_by: \"s002\"\n"
        "reason: \"方案被 s002 替代\"\n"
        "---\n"
        "# Spike s001\n\n"
        "## 背景\n旧方案。\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "mark s001 as superseded")
    sync = ctx.SyncContext.for_consumer(repo)

    migrate_entity_storage(sync=sync, write=True)

    target_rep = repo / "docs/spikes/s001_kv_store/report.md"
    assert target_rep.is_file()
    text = target_rep.read_text(encoding="utf-8")
    assert 'status: "superseded"' in text
    assert 'superseded_by: "s002"' in text
    assert 'reason: "方案被 s002 替代"' in text


def test_entity_storage_migration_preserves_pending_body_indentation(repo):
    """Pending 排版保真：正文中的缩进排版（代码块、列表缩进等）严格保留，不被无脑 strip 破坏。"""
    _setup_legacy_repo(repo)
    p1 = repo / "docs/pending/todo/p001_fix_cli.md"
    indented_body = (
        "# p001 fix cli\n\n"
        "- 现象：\n"
        "    - 第一层缩进\n"
        "        - 第二层缩进\n\n"
        "```python\n"
        "def foo():\n"
        "    return 42\n"
        "```\n"
    )
    p1.write_text(indented_body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add indented pending")
    sync = ctx.SyncContext.for_consumer(repo)

    migrate_entity_storage(sync=sync, write=True)

    target_p1 = repo / "docs/pending/p001_fix_cli.md"
    assert target_p1.is_file()
    text = target_p1.read_text(encoding="utf-8")
    assert "    - 第一层缩进\n        - 第二层缩进" in text
    assert "def foo():\n    return 42" in text


def test_entity_storage_migration_cleans_template_gitkeep(repo):
    """P1-7：模板自带 .gitkeep 占位文件不得阻断清理，也不得残留。"""
    _setup_legacy_repo(repo)
    for rel in (
        "docs/archive/tasks", "docs/archive/pending", "docs/archive/spikes",
        "docs/pending/todo", "docs/pending/parked",
    ):
        directory = repo / rel
        if directory.is_dir():
            (directory / ".gitkeep").write_text("", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: add template gitkeep placeholders")
    sync = ctx.SyncContext.for_consumer(repo)

    assert migrate_entity_storage(sync=sync, write=True)
    assert not (repo / "docs/archive/tasks").exists()
    assert not (repo / "docs/pending/todo").exists()
    assert not (repo / "docs/pending/parked").exists()
    assert not (repo / "docs/archive/pending").exists()
    assert not (repo / "docs/archive/spikes").exists()
    assert entity_storage_migration_status(sync=sync) == []


def test_entity_storage_migration_escapes_superseded_reason(repo):
    """DS L292：superseded reason 含引号/反斜杠时写出合法 YAML，往返不丢字符。"""
    _setup_legacy_repo(repo)
    tricky = '方案含 "双引号" 与 \\ 反斜杠'
    escaped = tricky.replace("\\", "\\\\").replace('"', '\\"')
    s1_rep = repo / "docs/archive/spikes/s001_kv_store/report.md"
    s1_rep.write_text(
        "---\n"
        'sid: "s001"\n'
        'slug: "kv_store"\n'
        'status: "superseded"\n'
        'superseded_by: "s002"\n'
        f'reason: "{escaped}"\n'
        "---\n"
        "# Spike s001\n\n## 背景\n旧方案，关联 d001。\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: tricky superseded reason")
    sync = ctx.SyncContext.for_consumer(repo)

    migrate_entity_storage(sync=sync, write=True)

    target = repo / "docs/spikes/s001_kv_store/report.md"
    text = target.read_text(encoding="utf-8")
    fm, _ = parse_front_matter_text(text, source="report.md")
    assert fm["reason"] == tricky
    validate_spike_metadata(fm, expected_sid="s001", expected_slug="kv_store")


def test_entity_storage_migration_blocks_unknown_spike_front_matter_field(repo):
    """DS L239：归档 spike 的未知字段由统一校验阻断，不再白名单重建静默丢弃。"""
    _setup_legacy_repo(repo)
    s1_rep = repo / "docs/archive/spikes/s001_kv_store/report.md"
    s1_rep.write_text(
        "---\n"
        'sid: "s001"\n'
        'slug: "kv_store"\n'
        'status: "completed"\n'
        'custom_field: "leak"\n'
        "---\n"
        "# Spike s001\n\n## 结论\n采纳 d001。\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: unknown spike field")
    sync = ctx.SyncContext.for_consumer(repo)

    with pytest.raises(MigrationError, match="Front Matter 非法"):
        migrate_entity_storage(sync=sync, write=True)


def test_entity_storage_migration_preserves_pending_fix_ref(repo):
    """DS L240：正文同时含 reason= 与 fix_ref= 时，fix_ref 不再静默丢失。"""
    _setup_legacy_repo(repo)
    p3 = repo / "docs/archive/pending/p003_closed_item.md"
    p3.write_text(
        "# p003\n\n- 处理：resolution=resolved；reason=已修复并验证；fix_ref=t042\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: closed pending with fix_ref")
    sync = ctx.SyncContext.for_consumer(repo)

    migrate_entity_storage(sync=sync, write=True)

    target = repo / "docs/pending/p003_closed_item.md"
    fm, _ = parse_front_matter_text(target.read_text(encoding="utf-8"), source="p003")
    assert fm["fix_ref"] == "t042"
    assert fm["reason"] == "已修复并验证"
    assert fm["resolution"] == "resolved"


def test_entity_storage_migration_blocks_unknown_pending_front_matter_field(repo):
    """DS L240：旧 pending 的未知 Front Matter 字段阻断，而非白名单重建丢弃。"""
    _setup_legacy_repo(repo)
    p1 = repo / "docs/pending/todo/p001_fix_cli.md"
    p1.write_text(
        "---\n"
        'pid: "p001"\n'
        'slug: "fix_cli"\n'
        'status: "todo"\n'
        'custom: "leak"\n'
        "---\n# p001\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: unknown pending field")
    sync = ctx.SyncContext.for_consumer(repo)

    with pytest.raises(MigrationError, match="未知 Front Matter 字段"):
        migrate_entity_storage(sync=sync, write=True)


def test_entity_storage_migration_wraps_invalid_pending_status_as_migration_error(repo):
    """DS L237：扁平 pending 非法 status 由 build_migration_plan 包装为 MigrationError，不留 traceback。"""
    _setup_legacy_repo(repo)
    bad = repo / "docs/pending/p009_bad.md"
    bad.write_text(
        "---\n"
        'pid: "p009"\n'
        'slug: "bad"\n'
        'status: "nonsense"\n'
        "---\n# p009\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: invalid pending status")
    sync = ctx.SyncContext.for_consumer(repo)

    with pytest.raises(MigrationError, match="非法 Front Matter"):
        migrate_entity_storage(sync=sync, write=True)


def test_entity_storage_migration_removes_stale_derived_index(repo):
    """DS L231：已跟踪的陈旧 tasks_index.json 由迁移显式删除/报告。"""
    _setup_legacy_repo(repo)
    for rel in ("docs/tasks_index.json", "docs/archive/tasks_index.json"):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[]\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: stale derived index")
    sync = ctx.SyncContext.for_consumer(repo)

    assert any("tasks_index" in item for item in entity_storage_migration_status(sync=sync))
    migrate_entity_storage(sync=sync, write=True)
    assert not (repo / "docs/tasks_index.json").exists()
    assert not (repo / "docs/archive/tasks_index.json").exists()
    assert entity_storage_migration_status(sync=sync) == []


def test_entity_storage_migration_rejects_symlink_entity(repo, tmp_path):
    """P1-10：实体目录为软链时拒绝迁移，绝不沿软链写/删仓外文件。"""
    _setup_legacy_repo(repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = repo / "docs/spikes/s002_network"
    shutil.rmtree(link)
    link.symlink_to(outside, target_is_directory=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: symlink spike entity")
    sync = ctx.SyncContext.for_consumer(repo)

    with pytest.raises(MigrationError, match="软链"):
        migrate_entity_storage(sync=sync, write=True)


def test_entity_storage_migration_fails_closed_on_worktree_query_error(repo, monkeypatch):
    """DS L242：git worktree 查询失败即抛 MigrationError，不再 fail-open 放行。"""
    _setup_legacy_repo(repo)
    sync = ctx.SyncContext.for_consumer(repo)
    mod = sys.modules["template_sync.entity_storage_migration"]
    real_git = mod._git

    def flaky_git(root, *args):
        if args and args[0] == "worktree":
            return subprocess.CompletedProcess([], 1, "", "simulated worktree failure")
        return real_git(root, *args)

    monkeypatch.setattr(mod, "_git", flaky_git)
    with pytest.raises(MigrationError, match="无法查询 git worktree"):
        migrate_entity_storage(sync=sync, write=True)
