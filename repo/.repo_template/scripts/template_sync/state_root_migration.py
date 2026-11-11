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
LEDGER_LOCK = "dispatch_ledger.lock"
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
STATE_FILE = "state_root_migration.json"
IN_PROGRESS = "in_progress"
RESTORE_FAILED = "restore_failed"
PENDING_STATUSES = frozenset({IN_PROGRESS, RESTORE_FAILED})
KNOWN_RUNTIME_NAMES = frozenset(
    {LEDGER, LEDGER_LOCK, AUDIT, GOAL, STATUSLINE, EXECUTION_CONTEXT}
)


def _state_file_root(sync: ctx.SyncContext) -> Path:
    return common_state_dir(sync.consumer)


def _present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _migration_state_file(sync: ctx.SyncContext) -> Path:
    return _state_file_root(sync) / STATE_FILE


def _write_migration_state(
    state_file: Path, status: str, backup_root: Path, sources: list[tuple[Path, Path]]
) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "backup": str(backup_root),
        "sources": [str(source) for source, _target in sources],
    }
    state_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _unfinished_restore(sync: ctx.SyncContext) -> tuple[str, Path] | None:
    state_file = _migration_state_file(sync)
    if not _present(state_file):
        return None
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"status": RESTORE_FAILED}
    if data.get("status") not in PENDING_STATUSES:
        return None
    backup = data.get("backup") or str(_state_file_root(sync) / BACKUP_NAME)
    return str(backup), state_file


def _restore_pending_message(backup: str, state_file: Path) -> str:
    return (
        f"上次 state-root 恢复未完成，禁止创建新迁移；备份保留在 {backup}，"
        f"请先按报告人工恢复，再删除状态文件 {state_file} 重试"
    )


def _statusline_source(sync: ctx.SyncContext) -> Path | None:
    runtime = sync.consumer.joinpath(*LEGACY_RUNTIME) / STATUSLINE
    scratch = sync.consumer / LEGACY_SCRATCH / STATUSLINE
    if _present(runtime) and _present(scratch):
        raise ctx.SyncError(
            f"{sync.rel(runtime)} 与 {sync.rel(scratch)} 同时存在，"
            "无法判断旧 statusline 来源；停止迁移，人工保留一个后再执行"
        )
    if _present(scratch):
        return scratch
    if _present(runtime):
        return runtime
    return None


def _unknown_runtime_entries(sync: ctx.SyncContext) -> list[Path]:
    runtime = sync.consumer.joinpath(*LEGACY_RUNTIME)
    if not runtime.is_dir():
        return []
    unknown = []
    for entry in sorted(runtime.iterdir(), key=lambda path: path.name):
        if entry.name not in KNOWN_RUNTIME_NAMES:
            unknown.append(entry)
        elif entry.name == LEDGER_LOCK and not (entry.is_file() or entry.is_symlink()):
            unknown.append(entry)
    return unknown


def _locks(sync: ctx.SyncContext) -> list[Path]:
    lock = sync.consumer.joinpath(*LEGACY_RUNTIME) / LEDGER_LOCK
    return [lock] if lock.is_file() or lock.is_symlink() else []


def _sources(sync: ctx.SyncContext) -> list[tuple[Path, Path]]:
    consumer = sync.consumer
    runtime = consumer.joinpath(*LEGACY_RUNTIME)
    pairs: list[tuple[Path, Path]] = []
    for name, target in (
        (LEDGER, "ledger.jsonl"),
        (AUDIT, AUDIT),
        (GOAL, GOAL),
    ):
        source = runtime / name
        if _present(source):
            pairs.append((source, _state_file_root(sync) / target))
    statusline = _statusline_source(sync)
    if statusline is not None:
        pairs.append((statusline, _state_file_root(sync) / STATUSLINE))
    legacy_state = consumer / ".repo_template" / SYNC_STATE
    if _present(legacy_state):
        pairs.append((legacy_state, _state_file_root(sync) / SYNC_STATE))
    for source_rel, target_name in TRANSACTION_PAIRS:
        source = consumer / source_rel
        if _present(source):
            pairs.append((source, _state_file_root(sync) / target_name))
    return pairs


def state_root_migration_status(sync: ctx.SyncContext) -> list[str]:
    findings = []
    pending = _unfinished_restore(sync)
    if pending is not None:
        backup, state_file = pending
        findings.append(_restore_pending_message(backup, state_file))
    runtime = sync.consumer.joinpath(*LEGACY_RUNTIME)
    if _present(runtime / EXECUTION_CONTEXT):
        findings.append(
            f"{sync.rel(runtime / EXECUTION_CONTEXT)} 残留 worktree 执行上下文；"
            "先收尾所有 worktree 并人工处理，不能静默迁移"
        )
    for path in _unknown_runtime_entries(sync):
        findings.append(
            f"{sync.rel(path)} 为未知残留；写入前需人工处理，state-root 迁移会停止"
        )
    try:
        sources = _sources(sync)
    except ctx.SyncError as error:
        findings.append(str(error))
        sources = []
    for source, target in sources:
        findings.append(f"{sync.rel(source)} -> {sync.rel(target)}")
    for lock in _locks(sync):
        findings.append(f"清理 {sync.rel(lock)}")
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


def _content_equal(left: Path, right: Path) -> bool:
    if left.is_symlink() or right.is_symlink():
        return left.is_symlink() and right.is_symlink() and left.readlink() == right.readlink()
    if left.is_file() or right.is_file():
        return left.is_file() and right.is_file() and left.read_bytes() == right.read_bytes()
    if left.is_dir() and right.is_dir():
        left_items = sorted(path.relative_to(left).as_posix() for path in left.rglob("*"))
        right_items = sorted(path.relative_to(right).as_posix() for path in right.rglob("*"))
        if left_items != right_items:
            return False
        return all(_content_equal(left / item, right / item) for item in left_items)
    return False


def _restore_source(backup: Path, source: Path) -> None:
    if _present(source):
        if _content_equal(backup, source):
            return
        _remove_tree(source)
    _copy_verify(backup, source)


def _prepare_target(target: Path) -> None:
    if not _present(target):
        return
    if target.is_dir() and not target.is_symlink() and not any(target.iterdir()):
        target.rmdir()
        return
    raise ctx.SyncError(f"迁移目标已存在，拒绝覆盖：{target}")


def _rollback(
    copied: list[tuple[Path, Path, Path]], current_target: Path | None
) -> list[str]:
    failures = []
    targets = ([current_target] if current_target is not None else [])
    targets += [target for _source, target, _backup in reversed(copied)]
    for target in targets:
        try:
            if _present(target):
                _remove_tree(target)
        except Exception as error:  # noqa: BLE001 - 逐项收集，恢复必须继续
            failures.append(f"清理目标 {target} 失败：{error}")
    for source, _target, backup in reversed(copied):
        try:
            _restore_source(backup, source)
        except Exception as error:  # noqa: BLE001 - 逐项收集，恢复必须继续
            failures.append(f"恢复 {source} 失败：{error}")
    return failures


def migrate_state_root(sync: ctx.SyncContext, *, write: bool = False) -> list[str]:
    if not (sync.consumer / ".git").exists():
        raise ctx.SyncError("state-root 迁移需要 Git 仓库")
    pending = _unfinished_restore(sync)
    if pending is not None:
        backup, state_file = pending
        raise ctx.SyncError(_restore_pending_message(backup, state_file))
    worktrees = _registered_worktrees(sync)
    if worktrees:
        raise ctx.SyncError(
            "仍有已登记 worktree，停止 state-root 迁移：\n- " + "\n- ".join(worktrees)
        )
    runtime = sync.consumer.joinpath(*LEGACY_RUNTIME)
    if _present(runtime / EXECUTION_CONTEXT):
        raise ctx.SyncError(
            f"{sync.rel(runtime / EXECUTION_CONTEXT)} 残留 worktree 执行上下文；先收尾所有 worktree"
        )
    unknown = _unknown_runtime_entries(sync)
    if unknown:
        raise ctx.SyncError(
            "docs/runtime 存在未知残留，停止 state-root 迁移：\n- "
            + "\n- ".join(sync.rel(path) for path in unknown)
        )
    sources = _sources(sync)
    locks = _locks(sync)
    if not sources and not locks:
        return []
    target_root = _state_file_root(sync)
    conflicts = [
        str(target) for _source, target in sources
        if _present(target) and not (target.is_dir() and not target.is_symlink() and not any(target.iterdir()))
    ]
    if conflicts:
        raise ctx.SyncError(
            "目标 common 状态目录已有文件，拒绝覆盖：\n- " + "\n- ".join(conflicts)
        )
    reports = [f"{sync.rel(source)} -> {sync.rel(target)}" for source, target in sources]
    reports.extend(f"清理 {sync.rel(lock)}" for lock in locks)
    if not write:
        return reports

    backup_root = target_root / BACKUP_NAME
    state_file = _migration_state_file(sync)
    created_root = not target_root.exists()
    _write_migration_state(state_file, IN_PROGRESS, backup_root, sources)
    copied: list[tuple[Path, Path, Path]] = []
    current_target: Path | None = None
    try:
        target_root.mkdir(parents=True, exist_ok=True)
        _remove_tree(backup_root)
        backup_root.mkdir(parents=True)
        manifest = []
        for index, (source, target) in enumerate(sources):
            backup = backup_root / str(index)
            _copy_verify(source, backup)
            _prepare_target(target)
            current_target = target
            _copy_verify(source, target)
            copied.append((source, target, backup))
            current_target = None
            manifest.append({"source": sync.rel(source), "target": sync.rel(target), "backup": str(index)})
        (backup_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        for source, _target, _backup in copied:
            _remove_tree(source)
        for directory in (sync.consumer / LEGACY_SCRATCH / "repo_sync_rollback",
                          sync.consumer / LEGACY_SCRATCH / "task_graph_transaction"):
            if directory.exists() and not any(directory.iterdir()):
                directory.rmdir()
        for source, target, _backup in copied:
            if _present(source):
                raise ctx.SyncError(f"旧路径未清空：{source}")
            if not _present(target):
                raise ctx.SyncError(f"新路径缺失：{target}")
        for lock in locks:
            _remove_tree(lock)
        runtime = sync.consumer.joinpath(*LEGACY_RUNTIME)
        if runtime.is_dir() and not any(runtime.iterdir()):
            runtime.rmdir()
    except Exception as error:
        failures = _rollback(copied, current_target)
        if failures:
            _write_migration_state(state_file, RESTORE_FAILED, backup_root, sources)
            raise ctx.SyncError(
                f"state-root 迁移失败（{error}）；恢复旧布局未完成：\n- "
                + "\n- ".join(failures)
                + f"\n备份保留在 {backup_root}，状态文件 {state_file}，"
                + f"请按 {backup_root}/manifest.json 人工恢复，完成后删除状态文件 {state_file} 再重试"
            ) from error
        _remove_tree(state_file)
        if created_root and target_root.exists() and not any(target_root.iterdir()):
            target_root.rmdir()
        raise ctx.SyncError(f"state-root 迁移失败，已恢复旧布局：{error}") from error
    else:
        _remove_tree(backup_root)
        _remove_tree(state_file)
    return reports
