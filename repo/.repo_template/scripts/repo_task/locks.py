"""跨平台文件锁与 git 公共目录锁 helper。

消除 _id_scan.id_lock / repo_task.ledger._with_lock / repo_task.integration._chain_locked
多处重复的 fcntl/msvcrt 锁原语。同线程可重入，跨线程/进程仍互斥。
"""

import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from functools import wraps

if os.name == "nt":
    import msvcrt
else:
    import fcntl

TASK_ID_LOCK_NAME = "repo_template_id.lock"
CONTROL_LOCK_NAME = "repo_template_control.lock"
VALIDATION_COMMON_DIR = "REPO_TEMPLATE_VALIDATION_COMMON_DIR"
_registry_guard = threading.Lock()
_thread_locks = {}
_held = threading.local()


def _reset_after_fork():
    global _registry_guard, _thread_locks, _held
    _registry_guard = threading.Lock()
    _thread_locks = {}
    _held = threading.local()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_after_fork)


def lock_fh(fh) -> None:
    fh.seek(0)
    if os.name == "nt":
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
    else:
        fcntl.flock(fh, fcntl.LOCK_EX)


def unlock_fh(fh) -> None:
    fh.seek(0)
    if os.name == "nt":
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fh, fcntl.LOCK_UN)


def git_common_dir(repo_root: Path) -> Path:
    """返回所有 worktree 共享的 git 公共目录绝对路径。"""
    import subprocess

    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--git-common-dir"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30,
    )
    if result.returncode != 0:
        raise OSError(f"无法解析 git 公共目录：{result.stderr.strip()}")
    text = result.stdout.strip()
    if not text:
        raise OSError("无法解析 git 公共目录（空输出）")
    path = Path(text)
    return path if path.is_absolute() else (repo_root / path).resolve()


@contextmanager
def file_lock(lock_path: Path):
    """Thread-reentrant OS lock. Never write the shared sentinel before locking."""
    lock_path = lock_path.resolve()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _registry_guard:
        mutex = _thread_locks.setdefault(str(lock_path), threading.RLock())
    with mutex:
        held = getattr(_held, "paths", None)
        if held is None:
            held = _held.paths = set()
        if str(lock_path) in held:
            yield
            return
        if os.name == "nt" and not lock_path.exists():
            # Publish a fully initialized inode atomically; competing creators
            # never observe a half-written Windows one-byte lock file.
            fd, temporary = tempfile.mkstemp(dir=lock_path.parent, prefix=".lock_init_")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(b"\0")
                try:
                    os.link(temporary, lock_path)
                except FileExistsError:
                    pass
            finally:
                Path(temporary).unlink(missing_ok=True)
        with lock_path.open("a+", encoding="utf-8") as fh:
            if os.name == "nt" and os.fstat(fh.fileno()).st_size == 0:
                raise OSError(f"旧空锁文件需在所有会话停止后修复：{lock_path}")
            lock_fh(fh)
            try:
                if os.fstat(fh.fileno()).st_size == 0:
                    fh.write("\0")
                    fh.flush()
                held.add(str(lock_path))
                yield
            finally:
                held.discard(str(lock_path))
                unlock_fh(fh)


@contextmanager
def git_common_lock(repo_root: Path, name: str):
    """Serialize Git shared state; validation children fail fast on this repo."""
    common = git_common_dir(repo_root).resolve()
    validating = os.environ.get(VALIDATION_COMMON_DIR)
    if name == CONTROL_LOCK_NAME and validating and Path(validating).resolve() == common:
        from .context import TaskDataError
        raise TaskDataError("同步验证期间禁止修改当前仓库控制面；请在隔离 Git 仓库中测试 task CLI，避免父子进程锁死锁")
    with file_lock(common / name):
        yield


def validation_environment(repo_root: Path):
    environment = os.environ.copy()
    environment[VALIDATION_COMMON_DIR] = str(git_common_dir(repo_root).resolve())
    return environment


def control_locked(function):
    """Serialize main control-plane mutations across tasks and template upgrades."""
    @wraps(function)
    def locked(*args, **kwargs):
        from . import context as ctx
        with git_common_lock(ctx.REPO_ROOT, CONTROL_LOCK_NAME):
            return function(*args, **kwargs)
    return locked
