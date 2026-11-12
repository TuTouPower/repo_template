"""Durable before-image transaction for repository-owned file sets.

Snapshots precede every asset mutation. Backups are copied on restore, never
consumed, and a failed restore remains visible as an unfinished transaction.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from .documents import atomic_write_text

SYNC_TRANSACTION_DIR = "sync_transaction"
GRAPH_TRANSACTION_DIR = "graph_transaction"
TRANSACTION_DIRS = (SYNC_TRANSACTION_DIR, GRAPH_TRANSACTION_DIR)
OPEN_STATUSES = frozenset({"preparing", "applying", "applied", "restore_failed"})
FINISHED_STATUSES = frozenset({"accepted", "rolled_back", "cancelled", "no_op"})


def open_transactions(root) -> list[tuple[str, str]]:
    """返回 Git common 状态目录内未结束或记录损坏的事务。

    仅检查 ``TRANSACTION_DIRS``；事务目录不存在或为空视为无事务。目录非空但
    缺少 journal，说明备份已成为无主残留，按 ``corrupt`` 带诊断返回；记录无法
    解析或不是对象时同样为 ``corrupt``。
    """
    from .context import common_state_dir

    root = Path(root)
    findings: list[tuple[str, str]] = []
    for directory in TRANSACTION_DIRS:
        transaction_dir = common_state_dir(root) / directory
        journal = transaction_dir / "transaction.json"
        if not journal.is_file():
            if transaction_dir.is_dir():
                try:
                    non_empty = any(transaction_dir.iterdir())
                except OSError:
                    non_empty = True
                if non_empty:
                    findings.append((directory, "corrupt"))
            continue
        try:
            data = json.loads(journal.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            findings.append((directory, "corrupt"))
            continue
        status = data.get("status") if isinstance(data, dict) else None
        if status in OPEN_STATUSES:
            findings.append((directory, status))
        elif status not in FINISHED_STATUSES:
            findings.append((directory, "corrupt"))
    return findings


def state_transaction(root: Path, directory: str) -> "FileTransaction":
    """事务备份落在 Git common 状态目录；资产路径仍相对仓库根。"""
    from .context import common_state_dir

    return FileTransaction(root, directory, backup_root=common_state_dir(root))


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


def _snapshot_asset(base, name, backup, absent_parents=None):
    path = base / name
    if path.is_symlink():
        raise ValueError(f"事务资产根是软链，不能可靠恢复其目标：{name}")
    # Never follow a symlink ancestor out of the consumer during restore.
    for parent in path.parents:
        if parent == base:
            break
        if parent.is_symlink():
            raise ValueError(f"事务路径父目录是软链：{name}")
        if absent_parents is not None:
            rel = str(parent.relative_to(base))
            if not parent.exists() and rel not in absent_parents:
                absent_parents.append(rel)
    exists = path.exists() or path.is_symlink()
    if exists:
        _copy(path, backup)
    return exists


def fingerprint(root, paths, state_paths=(), *, state_base=None):
    digest = hashlib.sha256()

    def feed(logical, path):
        digest.update(logical.encode())
        if path.is_symlink():
            digest.update(b"link" + str(path.readlink()).encode())
        elif path.is_file():
            digest.update(b"file" + str(path.stat().st_mode).encode() + path.read_bytes())
        elif path.is_dir():
            digest.update(b"dir")
        else:
            digest.update(b"absent")

    for name in paths:
        base = root / name
        items = [base]
        if base.is_dir() and not base.is_symlink():
            items.extend(sorted(base.rglob("*")))
        for path in items:
            rel = path.relative_to(root)
            if any(part in {"__pycache__", ".pytest_cache"} or part.endswith(".pyc")
                   for part in rel.parts):
                continue
            feed(rel.as_posix(), path)
    if state_paths:
        if state_base is None:
            from .context import common_state_dir

            state_base = common_state_dir(root)
        for name in state_paths:
            feed("@state/" + name, state_base / name)
    return digest.hexdigest()


class FileTransaction:
    def __init__(self, root: Path, directory: str, *, backup_root: Path | None = None):
        self.root = root
        self.backup = (Path(backup_root) if backup_root is not None else root) / directory
        self.journal = self.backup / "transaction.json"

    def read(self):
        return json.loads(self.journal.read_text(encoding="utf-8")) if self.journal.exists() else None

    def save(self, data):
        atomic_write_text(self.journal, json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def begin(self, paths, *, state_paths=()):
        old = self.read()
        if old and old["status"] in OPEN_STATUSES:
            raise ValueError("存在未结束文件事务；先 transaction status，再 rollback 或 accept")
        if self.backup.exists():
            if not self.journal.exists():
                try:
                    non_empty = any(self.backup.iterdir())
                except OSError:
                    non_empty = True
                if non_empty:
                    raise ValueError(
                        "事务目录缺少 journal 但存在残留备份；拒绝开始新事务，先人工核对"
                    )
            shutil.rmtree(self.backup)
        data = {"version": 1, "status": "preparing", "paths": {}, "state_paths": {}, "absent_parents": []}
        self.save(data)
        for name in paths:
            data["paths"][name] = _snapshot_asset(
                self.root, name, self.backup / name, data["absent_parents"]
            )
        if state_paths:
            from .context import common_state_dir

            state_base = common_state_dir(self.root)
            for name in state_paths:
                data["state_paths"][name] = _snapshot_asset(
                    state_base, name, self.backup / "__state__" / name
                )
        data["status"] = "applying"
        self.save(data)

    def _assets(self, data):
        for name, existed in data["paths"].items():
            yield self.root, name, self.backup / name, existed
        state_paths = data.get("state_paths", {})
        if state_paths:
            from .context import common_state_dir

            state_base = common_state_dir(self.root)
            for name, existed in state_paths.items():
                yield state_base, name, self.backup / "__state__" / name, existed

    def current_fingerprint(self, data) -> str:
        return fingerprint(self.root, data["paths"], data.get("state_paths", {}))

    def applied(self, *, verified=False):
        data = self.read()
        data["status"] = "applied"
        data["gates_verified"] = verified
        data["after"] = self.current_fingerprint(data)
        data["state_after"] = fingerprint(self.root, (), data.get("state_paths", {}))
        self.save(data)

    def rollback(self, *, confirm_restore=False, automatic=False):
        data = self.read()
        if not data or data["status"] not in {"preparing", "applying", "applied", "restore_failed", "rolled_back"}:
            raise ValueError("没有可恢复文件事务")
        if data.get("base_head"):
            head = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, timeout=30)
            if head.returncode or head.stdout.strip() != data["base_head"]:
                raise ValueError("同步已提交或 HEAD 已变化；禁止文件回滚，请 git revert 同步 commit，common 状态须人工核对")
        if data["status"] == "rolled_back":
            return
        if data["status"] == "preparing":
            # preparing 阶段尚未改动目标文件，备份可能只写了一部分；直接标记回滚。
            data["status"] = "rolled_back"
            self.save(data)
            return
        if not automatic and not confirm_restore:
            if data["status"] != "applied" or data.get("after") != self.current_fingerprint(data):
                raise ValueError("现场发生变化或事务被中断；先备份新增改动，再用 --confirm-restore 明确恢复基线")
        try:
            for base, name, backup, existed in self._assets(data):
                path = base / name
                _remove(path)
                if existed:
                    _copy(backup, path)
            for name in sorted(data.get("absent_parents", []), key=lambda name: name.count("/"), reverse=True):
                path = self.root / name
                if path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
            if data.get("base_head"):
                result = subprocess.run(["git", "-C", str(self.root), "reset", "-q", data["base_head"], "--", *data["paths"]],
                                        capture_output=True, text=True, timeout=30)
                if result.returncode:
                    raise OSError(f"恢复同步 index 失败：{result.stderr.strip()}")
        except (OSError, subprocess.SubprocessError):
            data["status"] = "restore_failed"
            self.save(data)
            raise
        leftovers = [Path(item) for item in data.get("restore_leftovers", [])]
        remaining = [str(path) for path in leftovers if path.exists() or path.is_symlink()]
        if remaining:
            data["status"] = "restore_failed"
            data["restore_leftovers"] = remaining
            self.save(data)
            raise ValueError("残留文件未清除，事务保持 restore_failed：" + "、".join(remaining))
        data["status"] = "rolled_back"
        data.pop("restore_leftovers", None)
        self.save(data)

    def accept(self, *, verified=False):
        data = self.read()
        if not data or data["status"] != "applied":
            raise ValueError("只有 applied 事务可以 accept")
        current = self.current_fingerprint(data)
        if data.get("state_after") != fingerprint(self.root, (), data.get("state_paths", {})):
            raise ValueError("apply 后 common 状态发生变化，拒绝 accept")
        if data.get("after") != current and not verified:
            raise ValueError("apply 后资产发生变化；先验证并处理事务，不能静默 accept")
        data["after"] = current
        data["status"] = "accepted"
        self.save(data)

    def no_op(self):
        data = self.read()
        if not data or data["status"] not in {"preparing", "applying"}:
            raise ValueError("只有进行中事务可以标记 no_op")
        self.rollback(automatic=True)
        data = self.read()
        data["status"] = "no_op"
        self.save(data)

    def _sync_commit(self, base_head):
        result = subprocess.run(
            ["git", "-C", str(self.root), "rev-list", "--ancestry-path", "--parents",
             f"{base_head}..HEAD"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode:
            raise ValueError("无法解析同步提交链：" + result.stderr.strip())
        children = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and base_head in parts[1:]:
                children.append(parts[0])
        if len(children) != 1:
            raise ValueError(
                "找不到唯一的同步 commit（base_head 直接子提交）；"
                f"候选 {len(children)} 个，须确认 revert 方式"
            )
        return children[0]

    def cancel_post_commit(self):
        data = self.read()
        if not data or data["status"] != "applied":
            raise ValueError("只有 applied 事务可以提交后取消")
        base_head = data.get("base_head")
        if not base_head:
            raise ValueError("事务未记录同步前 base_head；请用 transaction rollback")
        head = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30,
        )
        if head.returncode:
            raise ValueError("无法读取当前 HEAD：" + head.stderr.strip())
        if head.stdout.strip() == base_head:
            raise ValueError("HEAD 仍为同步前 base_head（尚未提交）；请用 transaction rollback")
        paths = list(data["paths"])
        status = subprocess.run(
            ["git", "-C", str(self.root), "status", "--porcelain", "--", *paths],
            capture_output=True, text=True, timeout=30,
        )
        if status.returncode or status.stdout.strip():
            raise ValueError(
                "提交后取消核验未通过：事务路径工作区不干净；"
                "先提交或清理本轮改动，只读核验不覆盖文件"
            )
        diff = subprocess.run(
            ["git", "-C", str(self.root), "diff", "--quiet", base_head, "HEAD", "--", *paths],
            capture_output=True, text=True, timeout=30,
        )
        if diff.returncode not in (0, 1):
            raise ValueError("无法比较 base_head 与 HEAD 的受管路径：" + diff.stderr.strip())
        if diff.returncode == 1:
            raise ValueError(
                "受管路径在 HEAD 与 base_head 内容不一致；"
                "请先 git revert 同步 commit，只读核验不覆盖文件"
            )
        state_paths = data.get("state_paths", {})
        baseline = fingerprint(self.root, (), state_paths, state_base=self.backup / "__state__")
        current = fingerprint(self.root, (), state_paths)
        if baseline != current:
            raise ValueError(
                "common 状态未恢复基线；git revert 不自动恢复 common 状态，"
                "请从备份人工恢复 sync_state/ledger 等后重试"
            )
        cancelled_commit = self._sync_commit(base_head)
        data["status"] = "cancelled"
        data["cancelled_commit"] = cancelled_commit
        data["cancelled_at"] = datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=8))
        ).strftime("%Y-%m-%dT%H:%M:%S+08:00")
        self.save(data)
        return cancelled_commit
