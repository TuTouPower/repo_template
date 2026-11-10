"""finish 的持久提交事务：journal、快照、结果判定与幂等恢复。

事务目录位于 worktree 的 Git 私有目录（linked worktree 自己的 gitdir），
避免污染工作区与主仓。任何业务文件写入前必须先持久化 journal 与快照。

判定只有三种结果：
- ``committed``：HEAD 已前进，实现 commit 已产生；不得回滚或覆盖。
- ``rollback_safe``：HEAD 未前进、Git 无残留锁、现场与暂存期望一致；可精确回滚。
- ``unknown``：HEAD 不可读、存在 Git 锁、现场被外部修改；保留 journal 与现场，
  输出“结果未知”，禁止自动回滚与盲目重试。

journal 是唯一状态机载体：每步写入前持久化 before/after 指纹，staging 前持久化
目标 index tree；恢复只接受该步骤已记录的两侧状态。回滚前先校验快照副本，写入
``rolling_back`` 阶段后才修改现场；该阶段允许从同一快照幂等重做整个回滚。提交
认领检查阶段、唯一父提交与执行身份，accept 再检查落盘 tree。不保留旧 journal
格式兼容分支。
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
JOURNAL_VERSION = 3

# task_rel 必须是 docs/tasks/<tid>_<slug> 单层目录（DS L208）。
TASK_DIR_RE = re.compile(r"docs/tasks/(t[0-9]+)_[^/]+")
PHASES = frozenset(
    {"prepared", "writing", "staging", "committing", "committed", "rolling_back"}
)

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
    "original_index_tree": str,
    "index_existed": bool,
    "files": dict,
    "phase": str,
    "expected_tree": str,
    "expected_files": dict,
    "pending_files": dict,
    "pending_tree": str,
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
    for name in ("files", "expected_files", "pending_files"):
        for rel, digest in data[name].items():
            if not isinstance(rel, str) or not isinstance(digest, str) or _is_unsafe_rel(rel):
                raise ctx.TaskDataError(f"提交事务 journal {name} 路径或结构非法：{rel!r}")


def load(worktree: Path) -> dict | None:
    path = txn_dir(worktree) / JOURNAL
    if path.is_symlink() or (os.path.lexists(path) and not path.is_file()):
        raise ctx.TaskDataError("提交事务 journal 非普通文件，可能被替换或损坏；人工核查后处理")
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ctx.TaskDataError(f"提交事务 journal 无法读取：{error}") from error
    _validate_journal(data)
    return data


def _fsync_path(path: Path) -> None:
    if os.name == "nt" and path.is_dir():
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_journal(directory: Path, data: dict) -> None:
    _validate_journal(data)
    atomic_write_text(directory / JOURNAL, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    _fsync_path(directory)


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
    _fsync_path(directory)


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_symlink():
        digest.update(os.fsencode(os.readlink(path)))
        return "120000:" + digest.hexdigest()
    with path.open("rb") as handle:
        while chunk := handle.read(65536):
            digest.update(chunk)
    mode = "100755" if path.stat().st_mode & 0o111 else "100644"
    return mode + ":" + digest.hexdigest()


def _snapshot_task_dir(task_dir: Path, directory: Path) -> dict[str, str]:
    """落盘并 fsync 后回读副本；副本摘要与源文件不一致时中止 begin。"""
    files: dict[str, str] = {}
    for path in sorted(task_dir.rglob("*")):
        if not path.is_file() and not path.is_symlink():
            continue
        rel = path.relative_to(task_dir).as_posix()
        dest = directory / FILES_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        source_digest = _file_digest(path)
        shutil.copy2(path, dest, follow_symlinks=False)
        if not dest.is_symlink():
            _fsync_path(dest)
        _fsync_path(dest.parent)
        if _file_digest(dest) != source_digest:
            raise ctx.TaskDataError(
                f"恢复快照副本与源文件摘要不一致：{rel}；中止提交事务，尚未写业务文件"
            )
        files[rel] = source_digest
    return files


def _snapshot_copy_problems(directory: Path, files: dict[str, str]) -> list[str]:
    """逐个校验快照副本；返回与 journal 摘要不一致的路径。"""
    problems: list[str] = []
    for rel in sorted(files):
        source = directory / FILES_DIR / rel
        try:
            digest = _file_digest(source)
        except OSError as error:
            problems.append(f"{rel} 快照副本不可读（{error}）")
            continue
        if digest != files[rel]:
            problems.append(f"{rel} 快照副本摘要与 journal 不一致")
    return problems


def begin(
    worktree: Path, *, tid: str, attempt: int, execution_id: str, branch: str,
    diff_anchor: str, review_scope: str, task_rel: str, message_file: str,
) -> dict:
    """持久化 journal 与快照；调用方随后才允许写业务文件。

    先 O_EXCL 占位 journal 再写快照：占位与快照之间崩溃会留下合法但空的
    journal，load 能读到并判 unknown（fail-closed），而不是无 journal 被误认为
    无事务而覆盖。
    """
    directory = txn_dir(worktree)
    git_dir = _git_dir(worktree)
    index_path = git_dir / "index"
    index_existed = index_path.is_file()
    original_index_tree = _write_tree_with_index(worktree, None) if index_existed else ""
    head = _git(["rev-parse", "HEAD"], root=worktree)
    if head.returncode != 0 or not head.stdout.strip():
        raise ctx.TaskDataError("无法读取事务前 HEAD，拒绝建立提交事务")
    directory.mkdir(parents=True, exist_ok=True)
    _fsync_path(directory.parent)
    placeholder = {
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
        "original_index_tree": original_index_tree,
        "index_existed": index_existed,
        "files": {},
        "phase": "prepared",
        "expected_tree": "",
        # 每步写入先记录 pending after-image；完成后推进 expected_files。
        "expected_files": {},
        "pending_files": {},
        "pending_tree": "",
    }
    _create_journal_excl(directory, placeholder)
    if index_existed:
        shutil.copy2(index_path, directory / INDEX_BACKUP)
        _fsync_path(directory / INDEX_BACKUP)
    task_dir = worktree / task_rel
    files = _snapshot_task_dir(task_dir, directory) if task_dir.is_dir() else {}
    data = dict(placeholder)
    data["files"] = files
    data["expected_files"] = dict(files)
    _write_journal(directory, data)
    return data


def mark_phase(worktree: Path, data: dict, phase: str) -> None:
    data["phase"] = phase
    _write_journal(txn_dir(worktree), data)


def write_file(worktree: Path, data: dict, rel: str, content: str) -> None:
    """先持久化精确 after-image，再原子写单个业务文件；两侧状态都可恢复。"""
    if rel not in {ctx.PROGRESS_FILE, "handoff.json"}:
        raise ctx.TaskDataError(f"finish 事务不拥有写入路径：{rel}")
    task_dir = worktree / data["task_rel"]
    if data["pending_files"] or _snapshot_hashes(task_dir) != data["expected_files"]:
        raise ctx.TaskDataError("写入前现场偏离事务期望或上一步未完成，保留 journal")
    planned = dict(data["expected_files"])
    target = task_dir / rel
    mode = "100755" if target.exists() and target.stat().st_mode & 0o111 else "100644"
    planned[rel] = mode + ":" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    data["pending_files"] = planned
    data["phase"] = "writing"
    _write_journal(txn_dir(worktree), data)
    atomic_write_text(task_dir / rel, content)
    data["expected_files"] = planned
    data["pending_files"] = {}
    _write_journal(txn_dir(worktree), data)


def stage(worktree: Path, data: dict) -> None:
    """在私有 index 计算 after-tree，落 journal 后才修改真实 index。"""
    directory = txn_dir(worktree)
    planned_index = directory / "index.planned"
    index = _git_dir(worktree) / "index"
    if index.is_file():
        shutil.copy2(index, planned_index)
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(planned_index)
    result = subprocess.run(
        ["git", "-C", str(worktree), "add", "-A", "--", data["task_rel"]],
        env=env, capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise ctx.TaskDataError(f"无法计算暂存期望：{result.stderr.strip()}")
    data["pending_tree"] = _write_tree_with_index(worktree, planned_index)
    data["phase"] = "staging"
    _write_journal(directory, data)
    result = _git(["add", "-A", "--", data["task_rel"]], root=worktree)
    if result.returncode != 0:
        raise ctx.TaskDataError(f"git add 失败：{result.stderr.strip()}")
    mark_staged(worktree, data)


def mark_staged(worktree: Path, data: dict) -> None:
    """暂存必须匹配写入前已持久化的 tree；不得重新认领未知现场。"""
    tree = _write_tree_with_index(worktree, None)
    if tree != data["pending_tree"]:
        raise ctx.TaskDataError("真实暂存区偏离事务计划，保留 journal")
    if _snapshot_hashes(worktree / data["task_rel"]) != data["expected_files"]:
        raise ctx.TaskDataError("暂存后文件偏离事务期望，保留 journal")
    data["expected_tree"] = tree
    data["pending_tree"] = ""
    data["phase"] = "committing"
    _write_journal(txn_dir(worktree), data)


def _snapshot_hashes(task_dir: Path) -> dict[str, str]:
    if not task_dir.is_dir():
        return {}
    return {
        path.relative_to(task_dir).as_posix(): _file_digest(path)
        for path in sorted(task_dir.rglob("*"))
        if path.is_file() or path.is_symlink()
    }


def _write_tree_with_index(worktree: Path, index_file: Path | None) -> str:
    """计算指定 index 文件的 tree；index_file=None 时用当前 index。"""
    env = None
    if index_file is not None:
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index_file)
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree), "write-tree"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60, env=env,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ctx.TaskDataError(f"git write-tree 执行失败：{error}") from error
    if result.returncode != 0 or not result.stdout.strip():
        raise ctx.TaskDataError(f"git write-tree 失败：{result.stderr.strip()}")
    return result.stdout.strip()


def _backup_tree_matches(worktree: Path, data: dict) -> bool:
    """prepared/writing 阶段：当前暂存区 tree 是否与 journal 原始 tree 一致。"""
    git_dir = _git_dir(worktree)
    if not data.get("index_existed"):
        return not (git_dir / "index").exists()
    try:
        current_tree = _write_tree_with_index(worktree, None)
    except ctx.TaskDataError:
        return False
    return current_tree == data["original_index_tree"]


def _head(worktree: Path) -> str | None:
    result = _git(["rev-parse", "HEAD"], root=worktree)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip()


def _locks_present(worktree: Path) -> list[str]:
    git_dir = _git_dir(worktree)
    return [name for name in LOCK_FILES if (git_dir / name).exists()]


def judge(worktree: Path, data: dict) -> tuple[str, str]:
    """返回 (committed|rollback_safe|unknown, 说明)。只读；所有阶段统一比对现场。

    HEAD 只有在提交阶段、唯一父提交等于 original_head 且回执身份匹配时才可认领。
    业务写入与 staging 允许已持久化步骤两侧状态；其余变化均为 unknown。
    """
    _validate_journal(data)
    locks = _locks_present(worktree)
    if locks:
        return "unknown", f"Git 锁仍在（{', '.join(locks)}），子进程可能未结束"
    head = _head(worktree)
    if head is None:
        return "unknown", "无法读取 HEAD，提交结果未知"
    if head != data["original_head"]:
        if data["phase"] not in {"committing", "committed"}:
            return "unknown", "提交阶段之前 HEAD 已前进，不能认领外部提交"
        result = _git(["rev-list", "--parents", "-n", "1", head], root=worktree)
        tree = _git(["rev-parse", f"{head}^{{tree}}"], root=worktree)
        if (result.returncode != 0 or result.stdout.split() != [head, data["original_head"]]
                or tree.returncode != 0):
            return "unknown", "HEAD 提交形状与该事务不符，保留快照"
        handoff = _git(["show", f"{head}:{data['task_rel']}/handoff.json"], root=worktree)
        try:
            receipt = json.loads(handoff.stdout) if handoff.returncode == 0 else None
        except ValueError:
            receipt = None
        if not isinstance(receipt, dict) or any(
            receipt.get(key) != data[key] for key in ("tid", "attempt", "execution_id", "branch")
        ) or receipt.get("base_sha") != data["original_head"]:
            return "unknown", "HEAD 执行回执与该事务身份不符，保留快照"
        return "committed", f"已核对实现提交 {head[:12]} 的父提交与执行身份；tree 是否一致须由 accept 验证"
    if data["phase"] == "committed":
        return "unknown", "已记录提交完成但 HEAD 回到起点，禁止自动回滚外部改写"
    snapshot_problems = _snapshot_copy_problems(txn_dir(worktree), data["files"])
    if snapshot_problems:
        return "unknown", (
            "恢复快照副本与 journal 摘要不一致（"
            + "；".join(snapshot_problems)
            + "）；拒绝自动回滚，保留 journal 交人工处理"
        )
    if data["phase"] == "rolling_back":
        return "rollback_safe", "回滚已开始但未完成；重跑 finish 将从快照幂等重做整个回滚"
    # 允许当前步骤已持久化的前后状态；除此以外均视为外部修改。
    task_dir = worktree / data["task_rel"]
    if _snapshot_hashes(task_dir) not in (data["expected_files"], data["pending_files"]):
        return "unknown", "task 目录内容与提交事务期望不一致（现场被外部修改或人工修复）"
    if data["phase"] == "staging":
        try:
            current = _write_tree_with_index(worktree, None)
            if current != data["pending_tree"] and not _backup_tree_matches(worktree, data):
                return "unknown", "暂存区不属于该事务 staging 前后状态"
        except ctx.TaskDataError as error:
            return "unknown", str(error)
    elif data["phase"] in {"committing", "committed"}:
        current_tree = _git(["write-tree"], root=worktree)
        if current_tree.returncode != 0 or current_tree.stdout.strip() != data["expected_tree"]:
            return "unknown", "暂存区与提交事务期望不一致（现场被外部修改）"
    else:
        # prepared/writing 阶段 finish 尚未暂存：暂存区必须与事务开始时一致。
        try:
            matches = _backup_tree_matches(worktree, data)
        except ctx.TaskDataError as error:
            return "unknown", str(error)
        if not matches:
            return "unknown", "暂存区与提交事务开始时的快照不一致（现场被外部修改）"
    return "rollback_safe", "HEAD 未前进且现场与事务期望一致"


def verify_committed_tree(worktree: Path, data: dict) -> None:
    """清理快照之前复验落盘 tree，hook 改写同样保留恢复证据。"""
    verdict, detail = judge(worktree, data)
    if verdict != "committed":
        raise ctx.TaskDataError(f"无法认领提交：{detail}")
    result = _git(["rev-parse", "HEAD^{tree}"], root=worktree)
    if result.returncode != 0 or result.stdout.strip() != data["expected_tree"]:
        raise ctx.TaskDataError("落盘 tree 与事务期望不一致；保留 journal，运行 recovery/finish-unwind")


def _restore_problems(worktree: Path, data: dict) -> list[str]:
    """回滚后复验：task 哈希集合与 index tree 都必须等于 journal 原始值。"""
    problems: list[str] = []
    git_dir = _git_dir(worktree)
    if not data.get("index_existed"):
        if (git_dir / "index").exists():
            problems.append("暂存区 index 应不存在但恢复后仍存在")
    elif not (git_dir / "index").is_file():
        problems.append("暂存区 index 恢复后缺失")
    else:
        try:
            if _write_tree_with_index(worktree, None) != data["original_index_tree"]:
                problems.append("暂存区 tree 与 journal 原始值不一致")
        except ctx.TaskDataError as error:
            problems.append(f"暂存区 tree 校验失败（{error}）")
    original = data["files"]
    current = _snapshot_hashes(worktree / data["task_rel"])
    if current != original:
        parts: list[str] = []
        missing = sorted(set(original) - set(current))
        extra = sorted(set(current) - set(original))
        changed = sorted(
            rel for rel in set(original) & set(current) if original[rel] != current[rel]
        )
        if missing:
            parts.append("缺失 " + ", ".join(missing))
        if extra:
            parts.append("多出 " + ", ".join(extra))
        if changed:
            parts.append("内容不符 " + ", ".join(changed))
        problems.append("task 目录哈希集合与事务前不一致（" + "；".join(parts) + "）")
    return problems


def rollback(worktree: Path, data: dict) -> list[str]:
    """精确回滚 finish 自身写入；返回失败项，空列表表示全部成功。

    回滚前先校验全部快照副本，任一不符即拒绝并保留 journal。回滚开始前原子写入
    ``rolling_back`` 阶段标记；中途再次中断后，重跑 rollback 会从同一快照幂等重做。
    回滚完成后 task 哈希集合与 index tree 均须等于 journal 原始值才 finalize。
    """
    _validate_journal(data)
    task_dir = worktree / data["task_rel"]
    verdict, detail = judge(worktree, data)
    if verdict != "rollback_safe":
        raise ctx.TaskDataError(f"禁止自动回滚：{detail}")
    directory = txn_dir(worktree)
    snapshot_problems = _snapshot_copy_problems(directory, data["files"])
    if snapshot_problems:
        raise ctx.TaskDataError(
            "恢复快照副本校验失败，拒绝回滚："
            + "；".join(snapshot_problems)
            + "；保留 journal 与现场，人工核查"
        )
    if data.get("index_existed"):
        backup = directory / INDEX_BACKUP
        if not backup.is_file():
            raise ctx.TaskDataError("提交事务 index 快照缺失，拒绝回滚；保留 journal 交人工处理")
        try:
            backup_tree = _write_tree_with_index(worktree, backup)
        except ctx.TaskDataError as error:
            raise ctx.TaskDataError(
                f"提交事务 index 快照损坏（{error}）；拒绝回滚，保留 journal 交人工处理"
            ) from error
        if backup_tree != data["original_index_tree"]:
            raise ctx.TaskDataError(
                "提交事务 index 快照 tree 与 journal 原始值不一致；拒绝回滚，保留 journal 交人工处理"
            )
    mark_phase(worktree, data, "rolling_back")
    problems: list[str] = []
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
            if target.is_symlink():
                target.unlink()
            shutil.copy2(source, target, follow_symlinks=False)
        except OSError as error:
            label = "front matter 还原失败" if rel == ctx.PROGRESS_FILE else "内容还原失败"
            problems.append(f"{rel} {label}（{error}）")
    if not problems:
        problems.extend(_restore_problems(worktree, data))
    if not problems:
        try:
            finalize(worktree)
        except ctx.TaskDataError as error:
            problems.append(f"提交事务清理失败（{error}）")
    return problems


def finalize(worktree: Path) -> None:
    """删除事务目录；目录仍在则抛错，调用方不得报成功。"""
    directory = txn_dir(worktree)
    shutil.rmtree(directory, ignore_errors=True)
    if directory.exists():
        raise ctx.TaskDataError(f"提交事务目录无法清理：{directory}；请人工核查后删除")


def run_git_commit(worktree: Path, message_file: str, timeout: int = 600) -> subprocess.CompletedProcess:
    """执行提交；超时不再直接抛裸异常，由调用方按 judge 判定结果。"""
    try:
        return _git(["commit", "-F", message_file], root=worktree, timeout=timeout)
    except ctx.TaskDataError:
        # 超时被 _git 包装为 TaskDataError；返回失败结果交由 judge 判定。
        return subprocess.CompletedProcess(args=["git", "commit"], returncode=1, stdout="", stderr="git commit 超时")
