"""硬同步与镜像：树对树 / 单文件 / skill 整目录覆盖及差异扫描。"""

from __future__ import annotations

import shutil
from pathlib import Path

from . import context as ctx


def _is_noise(name: str) -> bool:
    return name in ctx.NOISE_NAMES or name in ctx.PROTECT_NAMES or name.endswith(".pyc")


def _file_differs(a: Path, b: Path) -> bool:
    if a.is_symlink() or b.is_symlink():
        return a.resolve() != b.resolve()
    if a.stat().st_size != b.stat().st_size:
        return True
    return a.read_bytes() != b.read_bytes()


def _reject_unsafe_target(dst: Path, reason: str) -> None:
    """拒绝目录与软链目标：copy2 会跟随文件软链写仓外，也会把文件写进同名目录。"""
    if dst.is_symlink():
        raise ctx.SyncError(
            f"{ctx._rel(dst)} 是软链：{reason}。同步不得经软链写到仓外；请人工处理后重跑 apply"
        )
    if dst.is_dir():
        raise ctx.SyncError(f"{ctx._rel(dst)} 是目录：{reason}。请人工处理该目录后重跑 apply")


def sync_dir(src_dir: Path, dst_dir: Path, changed: set[Path]) -> None:
    """树对树同步：src 全量复制（含覆盖），dst 中 src 没有的非噪声条目删除。"""
    if dst_dir.is_symlink():
        raise ctx.SyncError(
            f"{ctx._rel(dst_dir)} 是目录软链，拒绝向软链目标执行树对树同步以防越界。请人工处理后重跑 apply"
        )
    if not dst_dir.exists():
        dst_dir.mkdir(parents=True)
    elif not dst_dir.is_dir():
        raise ctx.SyncError(
            f"{ctx._rel(dst_dir)} 是文件，模板源是目录；类型不符，停止。"
            "请人工处理该路径后重跑 apply"
        )
    for entry in src_dir.iterdir():
        if _is_noise(entry.name):
            continue
        target = dst_dir / entry.name
        if target.is_symlink() and entry.is_dir() and not entry.is_symlink():
            raise ctx.SyncError(f"{ctx._rel(target)} 是目录软链，模板源是真实目录，拒绝覆盖；停止")
        if entry.is_dir() and not entry.is_symlink():
            sync_dir(entry, target, changed)
        elif entry.is_symlink():
            _reject_unsafe_target(target, "模板源是文件/软链，拒绝写入目录内部")
            if not target.exists() or _file_differs(entry, target):
                shutil.copy2(entry, target, follow_symlinks=False)
                changed.add(target)
        else:
            _reject_unsafe_target(target, "模板源是文件，拒绝写入目录内部")
            if not target.exists() or _file_differs(entry, target):
                shutil.copy2(entry, target)
                changed.add(target)
    for entry in dst_dir.iterdir():
        if _is_noise(entry.name):
            continue
        if not (src_dir / entry.name).exists():
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            changed.add(entry)


def sync_file(src: Path, dst: Path, changed: set[Path]) -> None:
    """单文件：src 有 → 拷贝（相同则不动）；src 无 → 删 dst。"""
    if not src.exists():
        if dst.exists():
            _reject_unsafe_target(dst, "模板已无此文件，拒绝删除同名目录")
            dst.unlink()
            changed.add(dst)
        return
    _reject_unsafe_target(dst, "模板源是文件，拒绝写入目录内部")
    if not dst.exists() or _file_differs(src, dst):
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        changed.add(dst)


def _dir_has_diff(src_dir: Path, dst_dir: Path) -> bool:
    """目录树是否存在实质差异（非噪声）。"""
    if not src_dir.exists() and not dst_dir.exists():
        return False
    if src_dir.exists() != dst_dir.exists():
        return True
    src_files = {p.relative_to(src_dir) for p in src_dir.rglob("*")
                 if p.is_file() and not any(_is_noise(part) for part in p.relative_to(src_dir).parts)}
    dst_files = {p.relative_to(dst_dir) for p in dst_dir.rglob("*")
                 if p.is_file() and not any(_is_noise(part) for part in p.relative_to(dst_dir).parts)}
    if src_files != dst_files:
        return True
    for rel in src_files:
        if _file_differs(src_dir / rel, dst_dir / rel):
            return True
    return False


def hard_sync_status(src: Path) -> list[dict]:
    """硬同步清单状态：write / delete / same。"""
    out: list[dict] = []
    for rel in ctx.HARD_SYNC_DIRS:
        s, d = src / rel, ctx.CONSUMER / rel
        if not s.exists() and not d.exists():
            continue
        if not s.exists():
            out.append({"rel": rel + "/", "state": "delete"})
        elif not d.exists() or _dir_has_diff(s, d):
            out.append({"rel": rel + "/", "state": "write"})
        else:
            out.append({"rel": rel + "/", "state": "same"})
    for rel in ctx.HARD_SYNC_FILES:
        s, d = src / rel, ctx.CONSUMER / rel
        if not s.exists() and not d.exists():
            continue
        if not s.exists():
            out.append({"rel": rel, "state": "delete"})
        elif not d.exists() or _file_differs(s, d):
            out.append({"rel": rel, "state": "write"})
        else:
            out.append({"rel": rel, "state": "same"})
    return out


def retired_template_warnings() -> list[str]:
    """已下线模板产物残留提醒：只读检查，不写盘。"""
    warnings = []
    for rel in ctx.RETIRED_TEMPLATE_PATHS:
        if (ctx.CONSUMER / rel).exists():
            warnings.append(
                f"{rel} 为已下线模板产物（b3e5c8f 起不再分发），"
                "确认无消费定制后手动删除，否则消费仓 format 门禁持续报红"
            )
    return warnings
