"""finish 的持久提交事务：journal、快照、结果判定与幂等恢复。

事务目录位于 worktree 的 Git 私有目录（linked worktree 自己的 gitdir），
避免污染工作区与主仓。任何业务文件写入前必须先持久化 journal 与快照。

判定只有三种结果：
- ``committed``：HEAD 已前进，实现 commit 已产生；不得回滚或覆盖。
- ``rollback_safe``：HEAD 未前进、Git 无残留锁、现场与暂存期望一致；可精确回滚。
- ``unknown``：HEAD 不可读、存在 Git 锁、现场被外部修改；保留 journal 与现场，
  输出“结果未知”，禁止自动回滚与盲目重试。

journal 是唯一状态机载体：结构/路径在每次读写时统一校验，所有阶段都用同一
``expected_files`` 比对 task 目录哈希，消除 judge/rollback/settle 各自散落的
隐式判断。不保留任何旧 journal 格式兼容分支。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import repo_task.context as ctx

from .documents import atomic_write_text
from .git_ops import _git

TXN_DIR_NAME = "repo-task-finish"
JOURNAL = "journal.json"
INDEX_BACKUP = "index.backup"
FILES_DIR = "files"
LOCK_FILES = ("index.lock", "HEAD.lock", "shallow.lock", "MERGE_HEAD")
JOURNAL_VERSION = 1

# task_rel 必须是 docs/tasks/<tid>_<slug> 单层目录（DS L208）。
TASK_DIR_RE = re.compile(r"docs/tasks/(t[0-9]+)_[^/]+")
PHASES = frozenset({"prepared", "writing", "committing", "committed"})

# journal 必需键 → 期望类型（缺键/类型错统一 TaskDataError）。
_REQUIRED_KEYS: dict[str, type] = {
    "version": int,
    "tid": str,
    "attempt": int,
    "execution_id": str,
    "branch": str,
    "diff_anchor": str,
    "review_scope": str,
    "task_rel": str,
    "message_file": str,
    "original_head": str,
    "index_existed": bool,
    "files": dict,
    "phase": str,
    "expected_tree": str,
    "expected_files": dict,
}


def _git_dir(worktree: Path) -> Path:
    result = _git(["rev-parse", "--absolute-git-dir"], root=worktree)
    if result.returncode != 0 or not result.stdout.strip():
        raise ctx.TaskDataError("无法解析 worktree Git 目录，不能建立提交事务")
    return Path(result.stdout.strip())


def txn_dir(worktree: Path) -> Path:
    return _git_dir(worktree) / TXN_DIR_NAME


def txn_lock_path(worktree: Path) -> Path:
    """全程互斥锁路径；置于事务目录之外，避免 finalize 删除锁文件。"""
    return _git_dir(worktree) / f"{TXN_DIR_NAME}.lock"


def _is_unsafe_rel(rel: str) -> bool:
    path = Path(rel)
    return not rel or path.is_absolute() or ".." in path.parts


def _validate_journal(data: dict) -> None:
    """journal 结构/路径/类型统一校验；非法一律 TaskDataError（DS L208）。"""
    if not isinstance(data, dict):
        raise ctx.TaskDataError("提交事务 journal 须为 JSON 对象")
    if data.get("version") != JOURNAL_VERSION:
        raise ctx.TaskDataError("提交事务 journal 版本非法")
    for key, expected_type in _REQUIRED_KEYS.items():
        if key not in data:
            raise ctx.TaskDataError(f"提交事务 journal 缺必需键：{key}")
        value = data[key]
        if expected_type is int:
            valid = isinstance(value, int) and not isinstance(value, bool)
        else:
            valid = isinstance(value, expected_type)
        if not valid:
            raise ctx.TaskDataError(f"提交事务 journal 键 {key} 类型非法")
    if not ctx.TID_RE.fullmatch(data["tid"]):
        raise ctx.TaskDataError(f"提交事务 journal tid 非法：{data['tid']!r}")
    if data["phase"] not in PHASES:
        raise ctx.TaskDataError(f"提交事务 journal phase 非法：{data['phase']!r}")
    task_rel = data["task_rel"]
    match = TASK_DIR_RE.fullmatch(task_rel)
    if not match or match.group(1) != data["tid"]:
        raise ctx.TaskDataError(f"提交事务 journal task_rel 非法：{task_rel!r}")
    for name in ("files", "expected_files"):
        for rel, digest in data[name].items():
            if not isinstance(rel, str) or not isinstance(digest, str) or _is_unsafe_rel(rel):
                raise ctx.TaskDataError(f"提交事务 journal {name} 路径或结构非法：{rel!r}")


def load(worktree: Path) -> dict | None:
    path = txn_dir(worktree) / JOURNAL
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ctx.TaskDataError(f"提交事务 journal 无法读取：{error}") from error
    _validate_journal(data)
    return data


def _write_journal(directory: Path, data: dict) -> None:
    _validate_journal(data)
    atomic_write_text(directory / JOURNAL, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _create_journal_excl(directory: Path, data: dict) -> None:
    """O_EXCL 创建 journal，消除 exists()+mkdir 的 TOCTOU（DS L209）。"""
    _validate_journal(data)
    path = directory / JOURNAL
    payload = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ctx.TaskDataError("已存在未完成的提交事务；先按 recovery 判定处理") from error
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_task_dir(task_dir: Path, directory: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(task_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(task_dir).as_posix()
        dest = directory / FILES_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        files[rel] = _file_digest(path)
    return files


def begin(
    worktree: Path, *, tid: str, attempt: int, execution_id: str, branch: str,
    diff_anchor: str, review_scope: str, task_rel: str, message_file: str,
) -> dict:
    """持久化 journal 与快照；调用方随后才允许写业务文件。"""
    directory = txn_dir(worktree)
    git_dir = _git_dir(worktree)
    index_path = git_dir / "index"
    index_existed = index_path.is_file()
    directory.mkdir(parents=True, exist_ok=True)
    if index_existed:
        shutil.copy2(index_path, directory / INDEX_BACKUP)
    task_dir = worktree / task_rel
    files = _snapshot_task_dir(task_dir, directory) if task_dir.is_dir() else {}
    head = _git(["rev-parse", "HEAD"], root=worktree)
    if head.returncode != 0 or not head.stdout.strip():
        raise ctx.TaskDataError("无法读取事务前 HEAD，拒绝建立提交事务")
    data = {
        "version": JOURNAL_VERSION,
        "tid": tid,
        "attempt": attempt,
        "execution_id": execution_id,
        "branch": branch,
        "diff_anchor": diff_anchor,
        "review_scope": review_scope,
        "task_rel": task_rel,
        "message_file": str(message_file),
        "original_head": head.stdout.strip(),
        "index_existed": index_existed,
        "files": files,
        "phase": "prepared",
        "expected_tree": "",
        # 所有阶段共用同一期望：prepared/writing 期望=原始现场，committing
        # 由 mark_staged 覆盖为暂存后现场。任何差异一律 unknown（P1-1）。
        "expected_files": dict(files),
    }
    _create_journal_excl(directory, data)
    return data


def mark_phase(worktree: Path, data: dict, phase: str) -> None:
    data["phase"] = phase
    _write_journal(txn_dir(worktree), data)


def mark_staged(worktree: Path, data: dict) -> None:
    """提交前记录期望：暂存 tree 与 task 目录内容指纹。"""
    tree = _git(["write-tree"], root=worktree)
    if tree.returncode != 0 or not tree.stdout.strip():
        raise ctx.TaskDataError(f"无法记录暂存 tree：{tree.stderr.strip()}")
    data["expected_tree"] = tree.stdout.strip()
    task_dir = worktree / data["task_rel"]
    data["expected_files"] = _snapshot_hashes(task_dir)
    data["phase"] = "committing"
    _write_journal(txn_dir(worktree), data)


def _snapshot_hashes(task_dir: Path) -> dict[str, str]:
    if not task_dir.is_dir():
        return {}
    return {
        path.relative_to(task_dir).as_posix(): _file_digest(path)
        for path in sorted(task_dir.rglob("*"))
        if path.is_file()
    }


def _head(worktree: Path) -> str | None:
    result = _git(["rev-parse", "HEAD"], root=worktree)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip()


def _locks_present(worktree: Path) -> list[str]:
    git_dir = _git_dir(worktree)
    return [name for name in LOCK_FILES if (git_dir / name).exists()]


def judge(worktree: Path, data: dict) -> tuple[str, str]:
    """返回 (committed|rollback_safe|unknown, 说明)。只读；所有阶段统一比对现场。"""
    _validate_journal(data)
    locks = _locks_present(worktree)
    if locks:
        return "unknown", f"Git 锁仍在（{', '.join(locks)}），子进程可能未结束"
    head = _head(worktree)
    if head is None:
        return "unknown", "无法读取 HEAD，提交结果未知"
    if head != data["original_head"]:
        return "committed", f"HEAD 已由 {data['original_head'][:12]} 前进到 {head[:12]}"
    # 所有阶段统一比对 task 目录哈希：崩溃后的人工修复/外部改写一律 unknown（P1-1）。
    task_dir = worktree / data["task_rel"]
    if _snapshot_hashes(task_dir) != data["expected_files"]:
        return "unknown", "task 目录内容与提交事务期望不一致（现场被外部修改或人工修复）"
    if data["phase"] == "committing":
        current_tree = _git(["write-tree"], root=worktree)
        if current_tree.returncode != 0 or current_tree.stdout.strip() != data["expected_tree"]:
            return "unknown", "暂存区与提交事务期望不一致（现场被外部修改）"
    return "rollback_safe", "HEAD 未前进且现场与事务期望一致"


def rollback(worktree: Path, data: dict) -> list[str]:
    """精确回滚 finish 自身写入；返回失败项，空列表表示全部成功。"""
    _validate_journal(data)
    task_dir = worktree / data["task_rel"]
    # 纵深防御：即便调用方绕过 judge，现场偏离期望也拒绝自动覆盖。
    if _snapshot_hashes(task_dir) != data["expected_files"]:
        raise ctx.TaskDataError("现场偏离事务期望，禁止自动回滚")
    problems: list[str] = []
    directory = txn_dir(worktree)
    git_dir = _git_dir(worktree)
    index_path = git_dir / "index"
    backup = directory / INDEX_BACKUP
    try:
        if data.get("index_existed"):
            temporary = index_path.with_name("index.finish_restore")
            shutil.copy2(backup, temporary)
            os.replace(temporary, index_path)
        else:
            index_path.unlink(missing_ok=True)
    except OSError as error:
        problems.append(f"暂存区还原失败（{error}）")

    original = data["files"]
    current = _snapshot_hashes(task_dir)
    for rel in sorted(set(current) - set(original)):
        target = task_dir / rel
        try:
            target.unlink()
        except OSError as error:
            problems.append(f"{rel} 删除失败（{error}）")
    for rel in sorted(original):
        target = task_dir / rel
        source = directory / FILES_DIR / rel
        try:
            if target.is_dir():
                raise IsADirectoryError(f"{target} 是目录")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        except OSError as error:
            label = "front matter 还原失败" if rel == ctx.PROGRESS_FILE else "内容还原失败"
            problems.append(f"{rel} {label}（{error}）")
    if not problems:
        finalize(worktree)
    return problems


def finalize(worktree: Path) -> None:
    shutil.rmtree(txn_dir(worktree), ignore_errors=True)


def run_git_commit(worktree: Path, message_file: str, timeout: int = 600) -> subprocess.CompletedProcess:
    """执行提交；超时不再直接抛裸异常，由调用方按 judge 判定结果。"""
    try:
        return _git(["commit", "-F", message_file], root=worktree, timeout=timeout)
    except ctx.TaskDataError:
        # 超时被 _git 包装为 TaskDataError；返回失败结果交由 judge 判定。
        return subprocess.CompletedProcess(args=["git", "commit"], returncode=1, stdout="", stderr="git commit 超时")
