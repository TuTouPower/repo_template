"""跨平台文件锁与 git 公共目录锁 helper。

消除 _id_scan.id_lock / repo_task.ledger._with_lock / repo_task.integration._chain_locked
多处重复的 fcntl/msvcrt 锁原语。锁文件先写 1 字节再锁，兼容 Windows msvcrt
（空文件上锁 1 字节超出 EOF 会抛 OSError，RT-011）。
"""

import os
from contextlib import contextmanager
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

TASK_ID_LOCK_NAME = "repo_template_id.lock"


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
def git_common_lock(repo_root: Path, name: str):
    """在 git 公共目录上取排他锁，覆盖「扫描取号 → 建文件」全过程。"""
    lock_path = git_common_dir(repo_root) / name
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as fh:
        if fh.tell() == 0:
            fh.write("\0")
            fh.flush()
        lock_fh(fh)
        try:
            yield
        finally:
            unlock_fh(fh)
