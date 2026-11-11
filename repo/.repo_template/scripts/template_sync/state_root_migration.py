"""一次性把主仓运行状态迁入 Git common dir。

迁移是文件级操作，不修改 Git index，也不产生 commit。目标目录固定在
``$GIT_COMMON_DIR/repo_template``；在标准 FileTransaction 内执行，旧路径作为
消费仓资产纳入 ``paths``，新路径作为 common 状态目录资产纳入 ``state_paths``。
崩溃恢复统一走 ``transaction status/rollback``，不再有独立状态文件、备份目录
或手写恢复顺序。作为 ``migrate`` 的独立 step 顺序执行，不与 ``_transactional``
套叠，避免同一 ``sync_transaction`` 目录出现嵌套事务。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from repo_task.context import common_state_dir
from repo_task.file_transaction import SYNC_TRANSACTION_DIR, state_transaction

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
KNOWN_RUNTIME_NAMES = frozenset(
    {LEDGER, LEDGER_LOCK, AUDIT, GOAL, STATUSLINE, EXECUTION_CONTEXT}
)


def _state_file_root(sync: ctx.SyncContext) -> Path:
    return common_state_dir(sync.consumer)


def _present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


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
    return pairs


def state_root_migration_status(sync: ctx.SyncContext) -> list[str]:
    findings = []
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


def migrate_state_root(sync: ctx.SyncContext, *, write: bool = False) -> list[str]:
    if not (sync.consumer / ".git").exists():
        raise ctx.SyncError("state-root 迁移需要 Git 仓库")
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

    paths = [source.relative_to(sync.consumer).as_posix() for source, _target in sources]
    paths.extend(lock.relative_to(sync.consumer).as_posix() for lock in locks)
    state_root = common_state_dir(sync.consumer)
    state_paths = [target.relative_to(state_root).as_posix() for _source, target in sources]
    transaction = state_transaction(sync.consumer, SYNC_TRANSACTION_DIR)
    try:
        transaction.begin(paths, state_paths=state_paths)
    except ValueError as error:
        raise ctx.SyncError(f"state-root 事务无法启动：{error}") from error
    try:
        for source, target in sources:
            _copy_verify(source, target)
            source.unlink()
        for lock in locks:
            lock.unlink()
        if runtime.is_dir() and not any(runtime.iterdir()):
            runtime.rmdir()
        for source, target in sources:
            if _present(source):
                raise ctx.SyncError(f"旧路径未清空：{source}")
            if not _present(target):
                raise ctx.SyncError(f"新路径缺失：{target}")
        transaction.applied(verified=True)
        transaction.accept()
    except Exception as error:
        try:
            transaction.rollback(automatic=True)
        except Exception as rollback_error:
            raise ctx.SyncError(
                f"state-root 迁移失败（{error}）；自动回滚失败：{rollback_error}；"
                f"备份保留在 {sync.rel(transaction.backup)}，请用 transaction rollback 恢复"
            ) from error
        raise ctx.SyncError(f"state-root 迁移失败，已回滚：{error}") from error
    return reports
