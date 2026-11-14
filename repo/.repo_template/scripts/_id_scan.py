#!/usr/bin/env python3
"""_id_scan.py - 跨 worktree 跨分支的条目编号扫描与互斥分配。

task.py / pending.py / findings.py / spikes.py 共用。四类条目分别递增：
- `docs/tasks/tNNN_{slug}/`
- `docs/pending/pNNN_{slug}.md`
- `docs/findings/dNNN_{slug}.md`
- `docs/spikes/sNNN_{slug}/`

扫描来源（仅读取本机已有数据，不执行任何网络请求）：
- 本地 `refs/heads/*`、本机已有 `refs/remotes/*`、tag 及登记 worktree HEAD 可达的本地历史路径；
- 当前本地分支和本机已有远端分支 tip（排除远端 HEAD 等符号引用）；
- 主仓及所有登记 worktree 的磁盘条目（含未跟踪条目）；
- 每个 worktree 各自的 Git index（含已暂存后又从磁盘删除的条目）。

占用计算与当前校验分离：
- `scan_occupied_max_ids`：单轮批量收集本机可达历史、分支 tip、worktree 磁盘与 index
  的规范路径并计算四类最大占用号；相同编号出现多次仍正常取最大值。
- `scan_max_id(repo_root, prefix)`：普通创建入口，按 prefix 查表，先对当前协议来源
  校验目录形状与同快照零重复，再取最大占用号。
- `validate_snapshot_ids`：合并/提交/验收边界校验当前快照（`t`/`p`/`d`/`s`）零重复。

编号与文件名映射由本模块统一定义（`canonical_entry_id` / `parse_entry_file_name` /
`parse_entry_dir_name`），强制规范 padding，杜绝 `p0001_x.md` 这类「可解析但按编号
找不到」的分叉。

互斥分配：
`allocate` / `allocate_dir`（以及 `task.py add`）在 git 公共目录的排他锁 `TASK_ID_LOCK_NAME`
上取锁，锁内完成「校验当前数据 → 扫描最大占用号 → 生成并原子发布条目」，释放锁时新条目已落盘。
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from repo_task.locks import TASK_ID_LOCK_NAME, git_common_lock
from repo_task.context import TaskDataError
from repo_task.entity_schema import SCHEMA_PATH, current_schema, validate_schema

LOCK_NAME = TASK_ID_LOCK_NAME

@dataclass(frozen=True)
class EntitySpec:
    """唯一实体描述：前缀、当前布局目录、历史/旧布局目录、条目形态。

    运行期取号与同步迁移共用本表；当前/历史/目标路径不再分散硬编码。
    """

    prefix: str
    current_dir: str
    history_dirs: tuple[str, ...]
    kind: str


ENTITIES: dict[str, EntitySpec] = {
    "t": EntitySpec("t", "docs/tasks", ("docs/tasks", "docs/archive/tasks"), "dir"),
    "p": EntitySpec("p", "docs/pending", ("docs/pending", "docs/archive/pending", "docs/pending/todo", "docs/pending/parked"), "file"),
    "d": EntitySpec("d", "docs/findings", ("docs/findings",), "file"),
    "s": EntitySpec("s", "docs/spikes", ("docs/spikes", "docs/archive/spikes"), "dir"),
}

# 派生遍历视图（非独立定义），供扫描/校验循环使用。
ENTRY_KINDS: tuple[tuple[str, str, str], ...] = tuple(
    (p, s.current_dir, s.kind) for p, s in ENTITIES.items()
)

# 规范 padding 的编号串：三位允许 0xx~9xx，四位及以上首位不得为 0。
# 拒绝 p0001 这种「int 可解析但 canonical_entry_id(1) 找不到」的非法文件名。
_CANONICAL_NUMBER = r"(?:0[0-9]{2}|[1-9][0-9]{2,})"

_O_EXCL_FLAGS = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
_O_NOFOLLOW_READ = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)

# 宿主 GIT_* 透传会把 git 重定向到错误仓库（hook/IDE 环境）：生产入口统一剥离。
_GIT_REDIRECT_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_CEILING_DIRECTORIES",
)


def _controlled_git_env() -> dict[str, str]:
    """返回剥离仓库重定向变量后的 git 子进程环境。"""
    env = dict(os.environ)
    for key in list(env.keys()):
        if key in _GIT_REDIRECT_VARS or key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_"):
            env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _fsync_dir(dir_path: Path) -> None:
    """fsync 目录项，保证 rename/新建掉电后可见；失败抛 IdScanError。"""
    try:
        fd = os.open(str(dir_path), os.O_RDONLY)
    except OSError as error:
        raise IdScanError(f"目录 fsync 失败（{dir_path}）：{error}") from error
    try:
        os.fsync(fd)
    except OSError as error:
        raise IdScanError(f"目录 fsync 失败（{dir_path}）：{error}") from error
    finally:
        os.close(fd)


class IdScanError(ValueError):
    """编号扫描或分配失败。"""


def _run_git(repo_root: Path, args: list[str]) -> str:
    """运行 git 子进程；失败统一包装为 IdScanError。"""
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotePath=false", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=30,
            env=_controlled_git_env(),
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        raise IdScanError(f"git {' '.join(args)} 失败：{stderr or e.returncode}") from e
    except subprocess.TimeoutExpired as e:
        raise IdScanError(f"git {' '.join(args)} 超时（30s）") from e
    except FileNotFoundError as e:
        raise IdScanError("git 不可用，无法扫描分支与 worktree") from e
    except OSError as e:
        raise IdScanError(f"git 执行失败（{' '.join(args)}）：{e}") from e
    return result.stdout


@contextmanager
def id_lock(repo_root: Path):
    """在 git 公共目录上取排他锁，覆盖「扫描取号 → 建文件」全过程。

    委托 repo_task.locks.git_common_lock：锁文件初始化与同线程重入由公共 helper 处理，兼容 Windows
    msvcrt（空文件锁 1 字节超 EOF 抛 OSError，RT-011）。git 失败包装为 IdScanError。
    锁内 git 公共目录解析同样剥离宿主 GIT_* 透传（locks helper 直接读 os.environ，
    此处临时清理重定向变量，退出时恢复）。
    """
    saved_redirect: dict[str, str] = {}
    for key in list(os.environ.keys()):
        if key in _GIT_REDIRECT_VARS or key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_"):
            saved_redirect[key] = os.environ.pop(key)
    saved_prompt = os.environ.get("GIT_TERMINAL_PROMPT")
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    try:
        try:
            with git_common_lock(repo_root, LOCK_NAME):
                yield
        except OSError as error:
            raise IdScanError(str(error)) from error
    finally:
        for key, value in saved_redirect.items():
            os.environ[key] = value
        if saved_prompt is None:
            os.environ.pop("GIT_TERMINAL_PROMPT", None)
        else:
            os.environ["GIT_TERMINAL_PROMPT"] = saved_prompt


def local_branches(repo_root: Path) -> list[str]:
    text = _run_git(
        repo_root, ["for-each-ref", "--format=%(refname:short)", "refs/heads"]
    )
    return [line.strip() for line in text.splitlines() if line.strip()]


def remote_refs(repo_root: Path) -> list[str]:
    """返回本机已保存的非符号引用远端分支完整 refname（如 refs/remotes/origin/main）。"""
    text = _run_git(
        repo_root, ["for-each-ref", "--format=%(refname)\t%(symref)", "refs/remotes"]
    )
    refs: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        refname, _, symref = line.partition("\t")
        if symref.strip():
            continue
        if refname.strip():
            refs.append(refname.strip())
    return refs


def _worktree_records(repo_root: Path) -> list[tuple[Path, str | None]]:
    """用 NUL 分隔读取所有登记 worktree 的 (绝对路径, HEAD_sha_or_None)，不 .strip() 路径。"""
    text = _run_git(repo_root, ["worktree", "list", "--porcelain", "-z"])
    records: list[tuple[Path, str | None]] = []
    current_path: Path | None = None
    current_head: str | None = None
    for field in text.split("\0"):
        if not field:
            if current_path is not None and current_path.is_dir():
                records.append((current_path.resolve(), current_head))
            current_path = None
            current_head = None
            continue
        if field.startswith("worktree "):
            current_path = Path(field[len("worktree ") :])
        elif field.startswith("HEAD "):
            sha = field[len("HEAD ") :]
            if sha and not set(sha) <= {"0"}:
                current_head = sha
    if current_path is not None and current_path.is_dir():
        records.append((current_path.resolve(), current_head))
    return records


def worktree_roots(repo_root: Path) -> list[Path]:
    """返回所有登记 worktree 的根目录（含主仓）。"""
    return [path for path, _head in _worktree_records(repo_root)]


def canonical_entry_id(prefix: str, number: int) -> str:
    """编号 → 规范条目 ID；所有文件名生成与查找共用这一映射。"""
    return f"{prefix}{number:03d}"


def parse_entry_file_name(prefix: str, name: str) -> tuple[int, str] | None:
    """解析规范的 `{prefix}NNN_{slug}.md`，返回 (number, slug)；非规范返回 None。"""
    match = re.fullmatch(rf"{prefix}({_CANONICAL_NUMBER})_([a-z0-9_]+)\.md", name)
    return (int(match.group(1)), match.group(2)) if match else None


def parse_entry_dir_name(prefix: str, name: str) -> tuple[int, str] | None:
    """解析规范的 `{prefix}NNN_{slug}` 目录名，返回 (number, slug)；非规范返回 None。"""
    match = re.fullmatch(rf"{prefix}({_CANONICAL_NUMBER})_([a-z0-9_]+)", name)
    return (int(match.group(1)), match.group(2)) if match else None


def entry_file_pattern(prefix: str) -> str:
    """供各脚本编译文件名正则：group(1) 为编号串、group(2) 为 slug。"""
    return rf"^{prefix}({_CANONICAL_NUMBER})_([a-z0-9_]+)\.md$"


def entry_dir_pattern(prefix: str) -> str:
    """供各脚本编译目录名正则：group(1) 为编号串、group(2) 为 slug。"""
    return rf"^{prefix}({_CANONICAL_NUMBER})_([a-z0-9_]+)$"


def _entry_number(prefix: str, name: str, *, kind: str) -> int | None:
    parsed = (
        parse_entry_file_name(prefix, name)
        if kind == "file"
        else parse_entry_dir_name(prefix, name)
    )
    return parsed[0] if parsed else None


def _parse_ls_tree_entries(text: str) -> list[tuple[str, str]]:
    """解析 `git ls-tree -r -t -z` 输出，返回 [(obj_type, rel_path)]。"""
    entries: list[tuple[str, str]] = []
    for item in text.split("\0"):
        if not item:
            continue
        meta, _, rel = item.partition("\t")
        parts = meta.split()
        if len(parts) < 2 or not rel:
            continue
        entries.append((parts[1], rel))
    return entries


def _numbers_from_tree_entries(
    tree_entries: list[tuple[str, str]],
    prefix: str,
    dirs: tuple[str, ...],
    kind: str,
) -> dict[int, list[str]]:
    found: dict[int, list[str]] = {}
    dir_parts_list = [PurePosixPath(d).parts for d in dirs]
    for obj_type, rel in tree_entries:
        parts = PurePosixPath(rel).parts
        if kind == "file":
            if obj_type != "blob":
                continue
            for dparts in dir_parts_list:
                if len(parts) > len(dparts) and parts[: len(dparts)] == dparts:
                    number = _entry_number(prefix, parts[-1], kind="file")
                    if number is not None:
                        bucket = found.setdefault(number, [])
                        if rel not in bucket:
                            bucket.append(rel)
                    break
        else:
            if obj_type != "tree":
                continue
            for d, dparts in zip(dirs, dir_parts_list):
                if len(parts) == len(dparts) + 1 and parts[: len(dparts)] == dparts:
                    number = _entry_number(prefix, parts[-1], kind="dir")
                    if number is not None:
                        identity = f"{d}/{parts[-1]}"
                        bucket = found.setdefault(number, [])
                        if identity not in bucket:
                            bucket.append(identity)
                    break
    return found


def _numbers_from_worktree(
    root: Path, prefix: str, dirs: tuple[str, ...], kind: str
) -> dict[int, list[str]]:
    found: dict[int, list[str]] = {}
    for rel_dir in dirs:
        base = root / rel_dir
        if base.is_symlink() or not base.is_dir():
            continue
        entries = base.rglob("*.md") if kind == "file" else base.iterdir()
        for path in entries:
            if path.is_symlink():
                continue
            if kind == "dir" and not path.is_dir():
                continue
            number = _entry_number(prefix, path.name, kind=kind)
            if number is not None:
                identity = path.relative_to(root).as_posix()
                bucket = found.setdefault(number, [])
                if identity not in bucket:
                    bucket.append(identity)
    return found


def _numbers_from_file_paths(
    file_paths: list[str], prefix: str, dirs: tuple[str, ...], kind: str
) -> dict[int, list[str]]:
    """从 NUL 分隔的相对文件路径列表（如 ls-files -z 或 log --name-only -z）提取编号。"""
    found: dict[int, list[str]] = {}
    dir_parts_list = [PurePosixPath(d).parts for d in dirs]
    for rel in file_paths:
        if not rel:
            continue
        parts = PurePosixPath(rel).parts
        if kind == "file":
            for dparts in dir_parts_list:
                if len(parts) > len(dparts) and parts[: len(dparts)] == dparts:
                    number = _entry_number(prefix, parts[-1], kind="file")
                    if number is not None:
                        bucket = found.setdefault(number, [])
                        if rel not in bucket:
                            bucket.append(rel)
                    break
        else:
            for d, dparts in zip(dirs, dir_parts_list):
                if len(parts) > len(dparts) + 1 and parts[: len(dparts)] == dparts:
                    name = parts[len(dparts)]
                    number = _entry_number(prefix, name, kind="dir")
                    if number is not None:
                        identity = f"{d}/{name}"
                        bucket = found.setdefault(number, [])
                        if identity not in bucket:
                            bucket.append(identity)
                    break
    return found


def _check_duplicates(source: str, found: dict[int, list[str]], prefix: str) -> None:
    duplicated = {n: paths for n, paths in found.items() if len(paths) > 1}
    if duplicated:
        detail = "；".join(
            f"{prefix}{n:03d} 出现在 {', '.join(sorted(paths))}"
            for n, paths in sorted(duplicated.items())
        )
        raise IdScanError(f"{source} 中条目编号重复：{detail}")


def scan_occupied_max_ids(
    repo_root: Path,
    *,
    _branches: list[str] | None = None,
    _wt_records: list[tuple[Path, str | None]] | None = None,
) -> dict[str, int]:
    """单轮批量扫描本地历史、分支 tip、worktree 磁盘与 index，返回四类最大占用号。

    只读本机已有数据，不执行任何网络请求；相同编号出现多次仍正常取最大值。
    """
    branches = _branches if _branches is not None else local_branches(repo_root)
    remotes = remote_refs(repo_root)
    wt_records = _wt_records if _wt_records is not None else _worktree_records(repo_root)
    wt_heads: list[str] = []
    for _root, head_sha in wt_records:
        if head_sha and head_sha not in wt_heads:
            wt_heads.append(head_sha)

    specs: list[tuple[str, tuple[str, ...], str]] = [
        (p, s.history_dirs, s.kind) for p, s in ENTITIES.items()
    ]
    all_scan_dirs = tuple(dict.fromkeys(d for _p, sdirs, _k in specs for d in sdirs))
    max_ids: dict[str, int] = {p: 0 for p in ENTITIES}

    # 1. 单次 git log 批量扫描全部可达本地历史路径
    log_text = _run_git(
        repo_root,
        [
            "log",
            "-m",
            "--branches",
            "--remotes",
            "--tags",
            *wt_heads,
            "--no-renames",
            "--name-only",
            "-z",
            "--format=",
            "--",
            *all_scan_dirs,
        ],
    )
    history_paths = [p for p in log_text.split("\0") if p]
    if history_paths:
        for sp, sdirs, skind in specs:
            found = _numbers_from_file_paths(history_paths, sp, sdirs, skind)
            if found:
                max_ids[sp] = max([max_ids[sp], *found])

    # 2. 本地分支与本机已保存远端分支当前 tip
    for ref in [*branches, *remotes]:
        tree_entries = _parse_ls_tree_entries(
            _run_git(repo_root, ["ls-tree", "-r", "-t", "-z", ref, "--", *all_scan_dirs])
        )
        for sp, sdirs, skind in specs:
            found = _numbers_from_tree_entries(tree_entries, sp, sdirs, skind)
            if found:
                max_ids[sp] = max([max_ids[sp], *found])

    # 3. 主仓及所有登记 worktree 的磁盘条目与各自 Git index
    for root, _head in wt_records:
        index_paths = [
            p for p in _run_git(root, ["ls-files", "-z", "--", *all_scan_dirs]).split("\0") if p
        ]
        for sp, sdirs, skind in specs:
            disk_found = _numbers_from_worktree(root, sp, sdirs, skind)
            if disk_found:
                max_ids[sp] = max([max_ids[sp], *disk_found])
            if index_paths:
                idx_found = _numbers_from_file_paths(index_paths, sp, sdirs, skind)
                if idx_found:
                    max_ids[sp] = max([max_ids[sp], *idx_found])

    return max_ids


def scan_max_id(repo_root: Path, prefix: str) -> int:
    """普通创建入口：按 prefix 查表，校验当前协议来源零重复/布局，再返回最大占用号。"""
    if prefix not in ENTITIES:
        raise IdScanError(f"未知条目类别前缀：{prefix!r}")
    branches = local_branches(repo_root)
    wt_records = _worktree_records(repo_root)

    for branch in branches:
        if branch_uses_current_schema(repo_root, branch):
            validate_snapshot_ids(repo_root, branch)

    for root, _head in wt_records:
        if not current_schema(root):
            continue
        validate_worktree_shape(root)
        for sp, sdir, skind in ENTRY_KINDS:
            found_disk = _numbers_from_worktree(root, sp, (sdir,), skind)
            _check_duplicates(f"worktree {root}", found_disk, sp)
        validate_snapshot_ids(root)

    return scan_occupied_max_ids(
        repo_root,
        _branches=branches,
        _wt_records=wt_records,
    ).get(prefix, 0)


def validate_worktree_shape(root: Path) -> None:
    """按当前模型约束目录形状，不识别任何历史路径名称。"""
    for name in ("pending", "findings"):
        directory = root / "docs" / name
        if directory.is_dir():
            nested = [path for path in directory.iterdir() if path.is_dir() and not path.name.startswith(".tmp_")]
            if nested:
                raise IdScanError(f"当前 {name} 布局禁止嵌套目录：{nested[0]}；先从固定来源运行 repo_sync.py plan / apply 完成内置迁移")


def require_current_schema(repo_root: Path) -> None:
    if not current_schema(repo_root):
        raise IdScanError("未建立当前实体 schema；先从固定来源运行 repo_sync.py apply 完成内置迁移，批准后单次提交同步结果")


def branch_uses_current_schema(repo_root: Path, ref: str) -> bool:
    listed = _run_git(repo_root, ["ls-tree", "--name-only", ref, "--", SCHEMA_PATH])
    if not listed.strip():
        return False
    text = _run_git(repo_root, ["show", f"{ref}:{SCHEMA_PATH}"])
    try:
        data = json.loads(text)
    except ValueError as error:
        raise IdScanError(f"{ref}:{SCHEMA_PATH} 损坏：{error}") from error
    try:
        validate_schema(data, source=f"{ref}:{SCHEMA_PATH}")
    except TaskDataError as error:
        raise IdScanError(str(error)) from error
    return True


def validate_merge_sources(repo_root: Path) -> bool:
    """原生 merge 的每个来源都须属于当前协议；版本标记不能被主干静默继承。

    返回是否处于 merge 提交阶段。``pre-merge-commit`` 阶段 MERGE_HEAD 已被移除，
    自动 merge 的来源校验由 ``commit-msg`` hook 调用本函数完成。
    """
    raw = Path(_run_git(repo_root, ["rev-parse", "--git-path", "MERGE_HEAD"]).strip())
    path = raw if raw.is_absolute() else repo_root / raw
    try:
        heads = path.read_text(encoding="ascii").splitlines()
    except FileNotFoundError:
        return False
    for head in heads:
        if not re.fullmatch(r"[0-9a-f]{40,64}", head):
            raise IdScanError("MERGE_HEAD 非法，停止提交")
        if not branch_uses_current_schema(repo_root, head):
            raise IdScanError(f"merge 来源 {head[:12]} 未完成实体协议切换；禁止直接合入")
    return True


def validate_snapshot_ids(repo_root: Path, ref: str | None = None) -> None:
    """合并/提交边界校验当前布局编号唯一性，不加载历史解析器。"""
    if ref is None:
        files = _run_git(repo_root, ["ls-files", "-z"]).split("\0")
        for prefix, directory, kind in ENTRY_KINDS:
            found: dict[int, list[str]] = {}
            for rel in files:
                if not rel.startswith(directory + "/"):
                    continue
                tail = rel[len(directory) + 1:].split("/")
                if kind == "file" and len(tail) != 1:
                    raise IdScanError(f"当前实体布局禁止嵌套条目：{rel}；先从固定来源运行 repo_sync.py plan / apply 完成内置迁移")
                if kind == "dir" and len(tail) < 2:
                    continue
                number = _entry_number(prefix, tail[0], kind=kind)
                if number is not None:
                    identity = directory + "/" + tail[0]
                    bucket = found.setdefault(number, [])
                    if identity not in bucket:
                        bucket.append(identity)
            _check_duplicates("index", found, prefix)
        return

    tree_entries = _parse_ls_tree_entries(
        _run_git(repo_root, ["ls-tree", "-r", "-t", "-z", ref])
    )
    for prefix, directory, kind in ENTRY_KINDS:
        found = {}
        for obj_type, rel in tree_entries:
            if not rel.startswith(directory + "/"):
                continue
            tail = rel[len(directory) + 1:].split("/")
            if kind == "file":
                if obj_type != "blob":
                    continue
                if len(tail) != 1:
                    raise IdScanError(f"当前实体布局禁止嵌套条目：{rel}；先从固定来源运行 repo_sync.py plan / apply 完成内置迁移")
                number = _entry_number(prefix, tail[0], kind="file")
            else:
                if obj_type != "tree" or len(tail) != 1:
                    continue
                number = _entry_number(prefix, tail[0], kind="dir")
            if number is not None:
                identity = directory + "/" + tail[0]
                bucket = found.setdefault(number, [])
                if identity not in bucket:
                    bucket.append(identity)
        _check_duplicates(ref, found, prefix)


def slug_from_title(title: str) -> str:
    """从标题派生新建条目的 slug；仅用于条目创建，不构成运行期兼容路径。

    去掉标题末尾的括号说明（如「（2026-07-01 发现）」）再取 slug。
    """
    title = re.sub(r"[（(][^）)]*[）)]\s*$", "", title.strip())
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return slug or "entry"


SLUG_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")


# ---------------------------------------------------------------------------
# 统一条目发布原语：唯一命名 + O_EXCL|O_NOFOLLOW + fsync + 拒绝软链 + os.replace
# ---------------------------------------------------------------------------


def _assert_no_symlink(repo_root: Path, path: Path) -> None:
    """path 及从仓库根到它的每一级都不得为软链；越界直接拒绝。"""
    root = Path(os.path.abspath(repo_root))
    absolute = Path(os.path.abspath(path))
    try:
        rel = absolute.relative_to(root)
    except ValueError as error:
        raise IdScanError(f"目标路径越出仓库边界：{path}") from error
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise IdScanError(f"拒绝向软链路径写入：{current}")


def _write_exclusive(path: Path, data: bytes) -> None:
    """O_CREAT|O_EXCL|O_NOFOLLOW 创建文件，写入并 fsync；FileExistsError 向上传播。"""
    if path.is_symlink():
        raise IdScanError(f"拒绝向软链写入：{path}")
    fd = os.open(path, _O_EXCL_FLAGS, 0o666)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def reclaim_staging(target_dir: Path) -> int:
    """回收 target_dir 下残留的 .tmp_* staging（SIGKILL/掉电后遗留）。

    只删本模块命名的 `.tmp_*` 前缀，不碰正式条目；软链一律保留（攻击者预置的
    可预测软链不得被清理掩盖，也不跟随写入），只回收普通文件/目录残留。
    返回清理数量。allocate/allocate_dir 在锁内发布前调用。
    """
    removed = 0
    try:
        entries = list(target_dir.iterdir())
    except FileNotFoundError:
        return 0
    except OSError:
        return 0
    for path in entries:
        if not path.name.startswith(".tmp_"):
            continue
        if path.is_symlink():
            continue
        _cleanup_staging(path)
        removed += 1
    return removed


def _stage_file(parent: Path, label: str, data: bytes) -> Path:
    """在公共目录里创建唯一命名的 staging 文件，返回其路径。"""
    for _ in range(100):
        candidate = parent / f".tmp_{label}_{secrets.token_hex(8)}.md"
        try:
            _write_exclusive(candidate, data)
        except FileExistsError:
            continue
        return candidate
    raise IdScanError(f"无法为 {label} 创建唯一临时文件")


def _stage_dir(parent: Path, label: str) -> Path:
    """在公共目录里创建唯一命名的 staging 目录（mkdir 自带 O_EXCL），返回其路径。"""
    for _ in range(100):
        candidate = parent / f".tmp_{label}_{secrets.token_hex(8)}"
        try:
            os.mkdir(candidate)
        except FileExistsError:
            continue
        _fsync_dir(parent)
        return candidate
    raise IdScanError(f"无法为 {label} 创建唯一临时目录")


def _verify_staged_file(path: Path, label: str) -> None:
    """发布前校验内容非空、重新 fsync（formatter 可能原地重写），并拒绝软链。"""
    if path.is_symlink():
        raise IdScanError(f"生成条目文件为软链：{label}；拒绝发布")
    fd = os.open(path, _O_NOFOLLOW_READ)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    if not path.read_text(encoding="utf-8").strip():
        raise IdScanError(f"生成条目内容为空：{label}；拒绝发布")


def _fsync_parent_of(target: Path) -> None:
    """os.replace 后 fsync 父目录，保证新条目掉电后可见。"""
    _fsync_dir(target.parent)


def _publish_staged_file(staging: Path, target: Path) -> None:
    _verify_staged_file(staging, target.name)
    if target.is_symlink() or target.exists():
        raise IdScanError(f"{target} 已存在；请先处理后重试")
    os.replace(staging, target)
    _fsync_parent_of(target)


def _cleanup_staging(staging: Path) -> None:
    """清理 staging；目录用 rmtree、文件/软链用 unlink，避免 IsADirectoryError 掩盖主异常。"""
    if staging.is_dir() and not staging.is_symlink():
        shutil.rmtree(staging, ignore_errors=True)
    else:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass


def _reraise_publish_error(error: BaseException) -> None:
    """业务/校验异常原样传播；底层 I/O 异常统一包装为 IdScanError。"""
    if isinstance(error, (IdScanError, KeyboardInterrupt, SystemExit)):
        raise error
    if isinstance(error, OSError):
        raise IdScanError(f"条目发布 I/O 失败：{error}") from error
    raise error


def allocate(
    repo_root: Path,
    *,
    prefix: str,
    target_dir: Path,
    slug: str,
    body: str,
    formatter=None,
) -> Path:
    """锁内完成「取号 → 生成最终内容 → 格式化 → 原子发布」，返回新文件路径。

    formatter 接收唯一命名临时文件路径；抛错时不发布正式条目并清理临时文件。
    存在旧编号来源时拒绝分配，要求先显式迁移（migration 为提示命令）。
    """
    if not SLUG_RE.match(slug):
        raise IdScanError(f"slug 须匹配 {SLUG_RE.pattern}（收到 {slug!r}）")
    with id_lock(repo_root):
        require_current_schema(repo_root)
        number = scan_max_id(repo_root, prefix) + 1
        entry_id = canonical_entry_id(prefix, number)
        path = target_dir / f"{entry_id}_{slug}.md"
        if path.is_symlink() or path.exists():
            raise IdScanError(f"{path} 已存在；请先处理后重试")
        _assert_no_symlink(repo_root, target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        _fsync_dir(target_dir.parent if target_dir.parent.exists() else repo_root)
        _assert_no_symlink(repo_root, target_dir)
        _assert_no_symlink(repo_root, path)
        reclaim_staging(target_dir)
        staging = _stage_file(
            target_dir,
            f"{entry_id}_{slug}",
            body.replace("{id}", entry_id).replace("{slug}", slug).encode("utf-8"),
        )
        try:
            if formatter is not None:
                formatter(staging)
            _publish_staged_file(staging, path)
        except BaseException as error:
            _cleanup_staging(staging)
            _reraise_publish_error(error)
    return path


def _safe_file_name(name: str) -> str:
    """files 键必须是目标目录内的相对路径；拒绝绝对路径、..、反斜杠与盘符。"""
    if not name or "\x00" in name:
        raise IdScanError(f"文件名非法：{name!r}")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in name
        or ":" in name
        or not path.parts
    ):
        raise IdScanError(
            f"文件名非法（不得为绝对路径或越出目标目录）：{name!r}"
        )
    return path.as_posix()


def allocate_dir(
    repo_root: Path,
    *,
    prefix: str,
    target_dir: Path,
    slug: str,
    files: dict[str, str],
    formatter=None,
) -> Path:
    """锁内分配编号、在唯一临时目录准备完整内容并格式化，随后原子发布目录条目。

    files 为 {相对文件名: 内容}；内容中的 `{id}` 替换为分配到的编号。
    formatter 接收临时文件路径；抛错时不发布正式目录。
    存在旧编号来源时拒绝分配，要求先显式迁移。
    """
    if not SLUG_RE.match(slug):
        raise IdScanError(f"slug 须匹配 {SLUG_RE.pattern}（收到 {slug!r}）")
    safe_files = {_safe_file_name(name): content for name, content in files.items()}
    with id_lock(repo_root):
        require_current_schema(repo_root)
        number = scan_max_id(repo_root, prefix) + 1
        entry_id = canonical_entry_id(prefix, number)
        path = target_dir / f"{entry_id}_{slug}"
        if path.is_symlink() or path.exists():
            raise IdScanError(f"{path} 已存在；请先处理后重试")
        _assert_no_symlink(repo_root, target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        _fsync_dir(target_dir.parent if target_dir.parent.exists() else repo_root)
        _assert_no_symlink(repo_root, target_dir)
        _assert_no_symlink(repo_root, path)
        reclaim_staging(target_dir)
        staging = _stage_dir(target_dir, f"{entry_id}_{slug}")
        try:
            for name, content in safe_files.items():
                dest_file = staging / name
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                _write_exclusive(dest_file, content.replace("{id}", entry_id).encode("utf-8"))
                if formatter is not None:
                    formatter(dest_file)
                _verify_staged_file(dest_file, name)
            if path.is_symlink() or path.exists():
                raise IdScanError(f"{path} 已存在；请先处理后重试")
            _assert_no_symlink(repo_root, target_dir)
            os.replace(staging, path)
            _fsync_parent_of(path)
        except BaseException as error:
            _cleanup_staging(staging)
            _reraise_publish_error(error)
    return path
