"""一次性把主仓运行状态迁入 Git common dir。

迁移是文件级操作，不修改 Git index，也不产生 commit。目标目录固定在
``$GIT_COMMON_DIR/repo_template``；失败时按备份恢复旧布局。
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from repo_task.context import common_state_dir

from . import context as ctx
from .common import _git

LEDGER = "dispatch_ledger.jsonl"
AUDIT = "tasks_audit.log"
GOAL = "goal_queue.json"
STATUSLINE = "statusline_workdir.jsonl"
SYNC_STATE = "sync_state.json"
EXECUTION_CONTEXT = "execution_context.json"
LEGACY_RUNTIME = ("docs", "runtime")
LEGACY_SCRATCH = ".scratch"
TRANSACTION_PAIRS = (
    (".scratch/repo_sync_rollback", "sync_transaction"),
    (".scratch/task_graph_transaction", "graph_transaction"),
)
BACKUP_NAME = ".state-root-backup"


def _state_file_root(sync: ctx.SyncContext) -> Path:
    return common_state_dir(sync.consumer)


def _sources(sync: ctx.SyncContext) -> list[tuple[Path, Path]]:
    consumer = sync.consumer
    runtime = consumer.joinpath(*LEGACY_RUNTIME)
    pairs: list[tuple[Path, Path]] = []
    for name, target in (
        (LEDGER, "ledger.jsonl"),
        (AUDIT, AUDIT),
        (GOAL, GOAL),
        (STATUSLINE, STATUSLINE),
    ):
        source = runtime / name
        if source.exists():
            pairs.append((source, _state_file_root(sync) / target))
    legacy_state = consumer / ".repo_template" / SYNC_STATE
    if legacy_state.exists():
        pairs.append((legacy_state, _state_file_root(sync) / SYNC_STATE))
    for source_rel, target_name in TRANSACTION_PAIRS:
        source = consumer / source_rel
        if source.exists():
            pairs.append((source, _state_file_root(sync) / target_name))
    return pairs


def state_root_migration_status(sync: ctx.SyncContext) -> list[str]:
    findings = []
    runtime = sync.consumer.joinpath(*LEGACY_RUNTIME)
    if (runtime / EXECUTION_CONTEXT).exists():
        findings.append(
            f"{sync.rel(runtime / EXECUTION_CONTEXT)} 残留 worktree 执行上下文；"
            "先收尾所有 worktree 并人工处理，不能静默迁移"
        )
    for source, target in _sources(sync):
        findings.append(f"{sync.rel(source)} -> {sync.rel(target)}")
    return findings


def _registered_worktrees(sync: ctx.SyncContext) -> list[str]:
    result = _git(sync.consumer, "worktree", "list", "--porcelain")
    if result.returncode != 0:
        raise ctx.SyncError(f"无法检查 worktree：{result.stderr.strip()}")
    entries = []
    current = {}
    for line in result.stdout.splitlines() + [""]:
        if not line:
            if current.get("worktree"):
                entries.append(current)
            current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    primary = str(sync.consumer.resolve())
    extra = [item for item in entries if str(Path(item["worktree"]).resolve()) != primary]
    return [item.get("branch", item["worktree"]) for item in extra]


def _copy_verify(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        target.symlink_to(source.readlink())
    elif source.is_dir():
        shutil.copytree(source, target, symlinks=True)
    else:
        shutil.copy2(source, target)
    if source.is_file() and not source.is_symlink():
        if source.read_bytes() != target.read_bytes():
            raise ctx.SyncError(f"迁移校验失败：{source} 与 {target} 字节不一致")
    elif source.is_dir():
        for path in source.rglob("*"):
            if path.is_file():
                relative = path.relative_to(source)
                if path.read_bytes() != (target / relative).read_bytes():
                    raise ctx.SyncError(f"迁移校验失败：{path} 与 {(target / relative)} 字节不一致")


def _remove_tree(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def migrate_state_root(sync: ctx.SyncContext, *, write: bool = False) -> list[str]:
    if not (sync.consumer / ".git").exists():
        raise ctx.SyncError("state-root 迁移需要 Git 仓库")
    worktrees = _registered_worktrees(sync)
    if worktrees:
        raise ctx.SyncError(
            "仍有已登记 worktree，停止 state-root 迁移：\n- " + "\n- ".join(worktrees)
        )
    runtime = sync.consumer.joinpath(*LEGACY_RUNTIME)
    if (runtime / EXECUTION_CONTEXT).exists():
        raise ctx.SyncError(
            f"{sync.rel(runtime / EXECUTION_CONTEXT)} 残留 worktree 执行上下文；先收尾所有 worktree"
        )
    sources = _sources(sync)
    if not sources:
        return []
    target_root = _state_file_root(sync)
    conflicts = [
        str(target) for _source, target in sources
        if target.exists() and not (target.is_dir() and not any(target.iterdir()))
    ]
    if conflicts:
        raise ctx.SyncError(
            "目标 common 状态目录已有文件，拒绝覆盖：\n- " + "\n- ".join(conflicts)
        )
    reports = [f"{sync.rel(source)} -> {sync.rel(target)}" for source, target in sources]
    if not write:
        return reports

    backup_root = target_root / BACKUP_NAME
    moved: list[tuple[Path, Path]] = []
    created_root = not target_root.exists()
    try:
        target_root.mkdir(parents=True, exist_ok=True)
        _remove_tree(backup_root)
        backup_root.mkdir(parents=True)
        manifest = []
        for index, (source, target) in enumerate(sources):
            backup = backup_root / str(index)
            _copy_verify(source, backup)
            _copy_verify(source, target)
            manifest.append({"source": sync.rel(source), "target": sync.rel(target), "backup": str(index)})
            moved.append((source, target))
        (backup_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        for source, _target in moved:
            _remove_tree(source)
        for directory in (sync.consumer / LEGACY_SCRATCH / "repo_sync_rollback",
                          sync.consumer / LEGACY_SCRATCH / "task_graph_transaction"):
            if directory.exists() and not any(directory.iterdir()):
                directory.rmdir()
        runtime = sync.consumer.joinpath(*LEGACY_RUNTIME)
        if runtime.is_dir() and not any(runtime.iterdir()):
            runtime.rmdir()
        for source, target in moved:
            if source.exists():
                raise ctx.SyncError(f"旧路径未清空：{source}")
            if not target.exists():
                raise ctx.SyncError(f"新路径缺失：{target}")
    except Exception as error:
        for source, target in reversed(moved):
            try:
                if target.exists():
                    _remove_tree(target)
                if (backup_root / str(moved.index((source, target)))).exists():
                    _copy_verify(backup_root / str(moved.index((source, target))), source)
            except Exception as restore_error:  # noqa: BLE001
                raise ctx.SyncError(
                    f"state-root 迁移失败（{error}）；恢复 {source} 失败：{restore_error}；"
                    f"备份保留在 {backup_root}"
                ) from error
        if created_root and target_root.exists() and not any(target_root.iterdir()):
            target_root.rmdir()
        raise ctx.SyncError(f"state-root 迁移失败，已恢复旧布局：{error}") from error
    return reports
