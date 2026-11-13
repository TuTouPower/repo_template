"""同步期 goal_queue.json 快照升版迁移（仅在同步事务内运行，运行期保持零兼容）。"""

from __future__ import annotations

import json
from pathlib import Path

from repo_task import context as repo_ctx
from repo_task.documents import atomic_write_text
from repo_task.goal import SNAPSHOT_VERSION, _parse_snapshot, _queue_edges
from repo_task.store import scan_tasks

from . import context as ctx
from .entity_storage_migration import entity_storage_migration_status
from .workflow_migration import workflow_migration_status

GOAL_FILENAME = "goal_queue.json"
LEGACY_GOAL_REL = Path("docs/runtime") / GOAL_FILENAME


def _present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _common_goal_path(sync: ctx.SyncContext) -> Path:
    return ctx.common_state_dir(sync.consumer) / GOAL_FILENAME


def _legacy_goal_path(sync: ctx.SyncContext) -> Path:
    return sync.consumer / LEGACY_GOAL_REL


def _pending_source(sync: ctx.SyncContext) -> Path | None:
    common = _common_goal_path(sync)
    if _present(common):
        return common
    legacy = _legacy_goal_path(sync)
    if _present(legacy):
        return legacy
    return None


def _validate_edges(value: object, queue: list[str], label: str) -> list[dict]:
    if not isinstance(value, list):
        raise ctx.SyncError(f"{label} edges 须为数组；请核对或删除快照后重跑同步")
    members = set(queue)
    edges = []
    for edge in value:
        if not isinstance(edge, dict):
            raise ctx.SyncError(f"{label} edges 成员须为对象；请核对或删除快照后重跑同步")
        kind = edge.get("type")
        src = edge.get("from")
        dst = edge.get("to")
        if kind not in ("dep", "conflict"):
            raise ctx.SyncError(f"{label} edges.type 非法（{kind!r}）")
        if not all(isinstance(tid, str) and repo_ctx.TID_RE.fullmatch(tid) for tid in (src, dst)):
            raise ctx.SyncError(f"{label} edges.from/to 非法")
        if src not in members or dst not in members:
            raise ctx.SyncError(f"{label} edges 引用队列外成员：{src} -> {dst}")
        edges.append({"type": kind, "from": src, "to": dst})
    return edges


def _inspect_snapshot(path: Path, label: str) -> tuple[str, dict]:
    if path.is_symlink() or not path.is_file():
        raise ctx.SyncError(f"{label} 不是普通状态文件；请人工核对后处理")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as error:
        raise ctx.SyncError(f"{label} 损坏或无法解析（{error}）；请人工核对或删除后重试") from error
    if not isinstance(data, dict):
        raise ctx.SyncError(f"{label} 顶层须为 JSON 对象；请人工核对或删除后重试")
    version = data.get("version")
    if version is not None and (type(version) is not int or version not in (1, SNAPSHOT_VERSION)):
        raise ctx.SyncError(f"{label} 版本不受支持（version={version!r}）；请核对或删除后重试")
    created_at = data.get("created_at")
    if created_at is not None and not isinstance(created_at, str):
        raise ctx.SyncError(f"{label} created_at 类型非法（{created_at!r}）")
    queue = data.get("queue")
    if (
        not isinstance(queue, list)
        or not queue
        or not all(isinstance(tid, str) and repo_ctx.TID_RE.fullmatch(tid) for tid in queue)
        or len(set(queue)) != len(queue)
    ):
        raise ctx.SyncError(f"{label} queue 非法或为空；请核对或删除后重跑同步")

    info = {
        "raw_version": version,
        "created_at": created_at or "",
        "queue": queue,
    }
    has_edges = "edges" in data and data.get("edges") is not None
    if version == SNAPSHOT_VERSION:
        info["edges"] = _validate_edges(data.get("edges"), queue, label)
        return "current", info
    if has_edges:
        info["edges"] = _validate_edges(data.get("edges"), queue, label)
        return "upgrade_version_only", info
    return "compute_edges", info


def _tasks_migration_pending(sync: ctx.SyncContext, src: Path | None) -> bool:
    if entity_storage_migration_status(sync):
        return True
    if src is not None:
        migrations, _, _ = workflow_migration_status(src, sync=sync)
        if migrations:
            return True
    return False


def _missing_tasks(queue: list[str], tasks: list[dict]) -> list[str]:
    known = {task["tid"] for task in tasks}
    return [tid for tid in queue if tid not in known]


def goal_snapshot_migration_status(sync: ctx.SyncContext) -> list[str]:
    """检测旧版或异常 goal_queue.json；与 migrate_goal_snapshot 同口径。"""
    source = _pending_source(sync)
    if source is None:
        return []
    label = sync.rel(source)
    try:
        mode, info = _inspect_snapshot(source, label)
    except ctx.SyncError as error:
        return [f"{label} 存在异常：{error}"]
    if mode == "current":
        return []
    ver_desc = "未声明版本" if info["raw_version"] is None else f"version={info['raw_version']}"
    detail = "补齐冻结边并升版" if mode == "compute_edges" else "保留冻结边并升版"
    return [f"{label}（{ver_desc}, {len(info['queue'])} 个 tid）待{detail}为 version {SNAPSHOT_VERSION}"]


def migrate_goal_snapshot(
    sync: ctx.SyncContext, src: Path | None = None, *, write: bool = False
) -> list[str]:
    """预检或在同步事务内将旧版 goal_queue.json 升版为现行版本。"""
    if not write:
        source = _pending_source(sync)
        if source is None:
            return []
        label = sync.rel(source)
        mode, info = _inspect_snapshot(source, label)
        if mode == "current":
            return []
        if mode == "compute_edges" and not _tasks_migration_pending(sync, src):
            tasks = scan_tasks(repo=repo_ctx.RepoContext.for_root(sync.consumer))
            missing = _missing_tasks(info["queue"], tasks)
            if missing:
                raise ctx.SyncError(
                    f"{label} 引用了主干不存在的 task（{', '.join(missing)}）；"
                    f"原队列（{info['created_at'] or '未知'}）：{', '.join(info['queue'])}。"
                    "请删除或重置快照后重跑同步"
                )
        ver_desc = "未声明版本" if info["raw_version"] is None else f"version={info['raw_version']}"
        return [f"{label}（{ver_desc}）→ version {SNAPSHOT_VERSION}"]

    common = _common_goal_path(sync)
    if not _present(common):
        legacy = _legacy_goal_path(sync)
        if _present(legacy):
            raise ctx.SyncError(f"{sync.rel(legacy)} 须先经 state-root 迁入 common 状态目录")
        return []
    label = sync.rel(common)
    mode, info = _inspect_snapshot(common, label)
    if mode == "current":
        return []
    if mode == "compute_edges":
        repo = repo_ctx.RepoContext.for_root(sync.consumer)
        tasks = scan_tasks(repo=repo)
        missing = _missing_tasks(info["queue"], tasks)
        if missing:
            raise ctx.SyncError(
                f"{label} 引用了主干不存在的 task（{', '.join(missing)}）；"
                f"原队列（{info['created_at'] or '未知'}）：{', '.join(info['queue'])}。"
                "请删除或重置快照后重跑同步"
            )
        edges = _queue_edges(info["queue"], tasks)
    else:
        repo = repo_ctx.RepoContext.for_root(sync.consumer)
        edges = info["edges"]

    upgraded = {
        "version": SNAPSHOT_VERSION,
        "created_at": info["created_at"],
        "queue": info["queue"],
        "edges": edges,
    }
    _parse_snapshot(upgraded, repo)
    atomic_write_text(common, json.dumps(upgraded, ensure_ascii=False, indent=2) + "\n")
    return [f"{label} 已升版为 version {SNAPSHOT_VERSION}（{len(info['queue'])} 个 tid，{len(edges)} 条边）"]
