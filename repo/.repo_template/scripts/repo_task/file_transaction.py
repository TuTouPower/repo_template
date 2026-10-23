"""Durable before-image transaction for repository-owned file sets.

Snapshots precede every asset mutation. Backups are copied on restore, never
consumed, and a failed restore remains visible as an unfinished transaction.
"""

import hashlib
import json
import shutil
from pathlib import Path

from .documents import atomic_write_text


def _remove(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        target.symlink_to(source.readlink())
    elif source.is_dir():
        shutil.copytree(source, target, symlinks=True)
    else:
        shutil.copy2(source, target)


def fingerprint(root, paths):
    digest = hashlib.sha256()
    for name in paths:
        base = root / name
        items = [base]
        if base.is_dir() and not base.is_symlink():
            items.extend(sorted(base.rglob("*")))
        for path in items:
            if any(part in {"__pycache__", ".pytest_cache"} or part.endswith(".pyc")
                   for part in path.relative_to(root).parts):
                continue
            digest.update(path.relative_to(root).as_posix().encode())
            if path.is_symlink():
                digest.update(b"link" + str(path.readlink()).encode())
            elif path.is_file():
                digest.update(b"file" + str(path.stat().st_mode).encode() + path.read_bytes())
            elif path.is_dir():
                digest.update(b"dir")
            else:
                digest.update(b"absent")
    return digest.hexdigest()


class FileTransaction:
    def __init__(self, root: Path, directory: str):
        self.root = root
        self.backup = root / directory
        self.journal = self.backup / "transaction.json"

    def read(self):
        return json.loads(self.journal.read_text(encoding="utf-8")) if self.journal.exists() else None

    def save(self, data):
        atomic_write_text(self.journal, json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def begin(self, paths):
        old = self.read()
        if old and old["status"] in {"applying", "restore_failed", "applied"}:
            raise ValueError("存在未结束文件事务；先 transaction status，再 rollback 或 accept")
        if self.backup.exists():
            shutil.rmtree(self.backup)
        data = {"version": 1, "status": "preparing", "paths": {}, "absent_parents": []}
        self.save(data)
        for name in paths:
            path = self.root / name
            if path.is_symlink():
                raise ValueError(f"事务资产根是软链，不能可靠恢复其目标：{name}")
            # Never follow a symlink ancestor out of the consumer during restore.
            for parent in path.parents:
                if parent == self.root:
                    break
                if parent.is_symlink():
                    raise ValueError(f"事务路径父目录是软链：{name}")
                if not parent.exists() and str(parent.relative_to(self.root)) not in data["absent_parents"]:
                    data["absent_parents"].append(parent.relative_to(self.root).as_posix())
            exists = path.exists() or path.is_symlink()
            if exists:
                _copy(path, self.backup / name)
            data["paths"][name] = exists
        data["status"] = "applying"
        self.save(data)

    def applied(self, *, verified=False):
        data = self.read()
        data["status"] = "applied"
        data["gates_verified"] = verified
        data["after"] = fingerprint(self.root, data["paths"])
        self.save(data)

    def rollback(self, *, confirm_restore=False, automatic=False):
        data = self.read()
        if not data or data["status"] not in {"applying", "applied", "restore_failed", "rolled_back"}:
            raise ValueError("没有可恢复文件事务")
        if data["status"] == "rolled_back":
            return
        if not automatic and not confirm_restore:
            if data["status"] != "applied" or data.get("after") != fingerprint(self.root, data["paths"]):
                raise ValueError("现场发生变化或事务被中断；先备份新增改动，再用 --confirm-restore 明确恢复基线")
        try:
            for name, existed in data["paths"].items():
                path = self.root / name
                _remove(path)
                if existed:
                    _copy(self.backup / name, path)
            for name in sorted(data.get("absent_parents", []), key=lambda name: name.count("/"), reverse=True):
                path = self.root / name
                if path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
        except OSError:
            data["status"] = "restore_failed"
            self.save(data)
            raise
        data["status"] = "rolled_back"
        self.save(data)

    def accept(self, *, verified=False):
        data = self.read()
        if not data or data["status"] != "applied":
            raise ValueError("只有 applied 事务可以 accept")
        current = fingerprint(self.root, data["paths"])
        if data.get("after") != current and not verified:
            raise ValueError("apply 后资产发生变化；先验证并处理事务，不能静默 accept")
        data["after"] = current
        data["status"] = "accepted"
        self.save(data)
