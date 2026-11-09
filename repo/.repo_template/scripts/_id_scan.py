#!/usr/bin/env python3
"""_id_scan.py - 跨 worktree 跨分支的条目编号扫描与互斥分配。

pending.py / findings.py / spikes.py 共用。条目以「一条目一文件」形式存放，文件名形如
`p047_cli_exit_code.md` / `d012_uv_lock_marker.md`，编号直接来自文件名，无需解析正文。

扫描来源：
- 所有本地分支的 git 树（`git ls-tree` 文件名）；
- 所有登记 worktree 的工作区文件（含未提交条目）。

旧格式有两类不可见编号来源，分配前必须显式迁移：
- 单文件总账（`docs/pending.md`、`docs/findings.md` 等，编号只在正文小节标题里）；
- 旧布局目录（`docs/archive/pending`、`docs/archive/spikes`、
  `docs/pending/todo`、`docs/pending/parked`）。
两类来源都阻断分配，并把提示指向真实迁移命令。

重复检测语义：
- 同一来源（单个分支或单个 worktree）内同号出现在多个状态目录时报错——迁移残留需立即暴露。
- 跨来源重复不报——并行 task worktree 各自含主干历史，合并前允许暂时重复。

编号与文件名映射由本模块统一定义（`canonical_entry_id` / `parse_entry_file_name` /
`parse_entry_dir_name`），强制规范 padding，杜绝 `p0001_x.md` 这类「可解析但按编号
找不到」的分叉。

互斥分配：
`allocate` 在 git 公共目录的锁文件上取排他锁，锁内完成「扫描取号 → 建文件」。
释放锁时新条目已落盘，后续扫描必然可见，不存在两个进程取到同号的窗口。

条目发布统一走「唯一命名 + O_EXCL|O_NOFOLLOW + fsync + 拒绝软链 + os.replace」原语，
文件型（allocate）与目录型（allocate_dir）共用同一套底层写入/发布/清理实现。
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Callable, NamedTuple

from repo_task.locks import TASK_ID_LOCK_NAME, git_common_lock

LOCK_NAME = TASK_ID_LOCK_NAME

# 规范 padding 的编号串：三位允许 0xx~9xx，四位及以上首位不得为 0。
# 拒绝 p0001 这种「int 可解析但 canonical_entry_id(1) 找不到」的非法文件名。
_CANONICAL_NUMBER = r"(?:0[0-9]{2}|[1-9][0-9]{2,})"

# 旧布局目录：实体曾按状态/归档分目录存放，编号对当前扁平扫描不可见。
LEGACY_LAYOUT_DIRS = (
    "docs/archive/pending",
    "docs/archive/spikes",
    "docs/pending/todo",
    "docs/pending/parked",
)

# 单文件总账按前缀分派到对应脚本的 migrate-legacy；旧布局目录统一走模板同步的
# entity-storage 迁移。提示必须是可直接执行的真实命令。
_LEDGER_MIGRATION_HINTS = {
    "p": "python3 .repo_template/scripts/pending.py migrate-legacy",
    "d": "python3 .repo_template/scripts/findings.py migrate-legacy",
}
_LAYOUT_MIGRATION_HINT = (
    "python3 .repo_template/scripts/repo_sync.py migrate entity-storage --write"
)

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
            ["git", "-C", str(repo_root), *args],
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


def worktree_roots(repo_root: Path) -> list[Path]:
    """返回所有登记 worktree 的根目录（含主仓）。"""
    text = _run_git(repo_root, ["worktree", "list", "--porcelain"])
    roots: list[Path] = []
    for line in text.splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree ") :].strip())
            if path.is_dir():
                roots.append(path.resolve())
    return roots


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


def _numbers_from_branch(
    repo_root: Path, branch: str, prefix: str, dirs: tuple[str, ...], kind: str
) -> dict[int, list[str]]:
    if kind == "dir":
        return _dir_numbers_from_branch(repo_root, branch, prefix, dirs)
    text = _run_git(
        repo_root, ["ls-tree", "-r", "--name-only", branch, "--", *dirs]
    )
    found: dict[int, list[str]] = {}
    for rel in text.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        parts = Path(rel).parts
        if kind == "file":
            # 文件型条目身份取文件自身相对路径。
            candidates = [(parts[-1], rel)]
        else:
            # 目录型只接受 `{dir}/<name>` 直接子目录，与 worktree 顶层扫描口径一致；
            # 嵌套路径（如 docs/spikes/misc/s005_fixture/report.md）不计入编号。
            candidates = []
            dir_parts = {d: Path(d).parts for d in dirs}
            for d, dparts in dir_parts.items():
                if len(parts) > len(dparts) and tuple(parts[: len(dparts)]) == dparts:
                    candidates.append((parts[len(dparts)], "/".join(parts[: len(dparts) + 1])))
        for name, identity in candidates:
            number = _entry_number(prefix, name, kind=kind)
            if number is not None:
                paths = found.setdefault(number, [])
                if identity not in paths:
                    paths.append(identity)
                break
    return found


def _dir_numbers_from_branch(
    repo_root: Path, branch: str, prefix: str, dirs: tuple[str, ...]
) -> dict[int, list[str]]:
    """目录型分支扫描：只认 tree 对象，文件同名不占号。

    对每棵扫描目录用 `git ls-tree <branch>:<dir>` 列直接子项并校验 type==tree，
    与 worktree 侧 `is_dir()` 双校验口径一致。目录在该分支不存在时视为空。
    """
    found: dict[int, list[str]] = {}
    for rel_dir in dirs:
        try:
            text = _run_git(repo_root, ["ls-tree", f"{branch}:{rel_dir}"])
        except IdScanError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                meta, name = line.split("\t", 1)
            except ValueError:
                continue
            meta_parts = meta.split()
            if len(meta_parts) < 2 or meta_parts[1] != "tree":
                continue
            number = _entry_number(prefix, name, kind="dir")
            if number is not None:
                identity = f"{rel_dir}/{name}"
                paths = found.setdefault(number, [])
                if identity not in paths:
                    paths.append(identity)
    return found


def _branch_legacy_kind(
    repo_root: Path, branch: str, ledger_names: set[str]
) -> str | None:
    """返回分支所含旧来源类型：ledger（单文件总账）/ layout（旧布局目录）/ None。"""
    listed = _run_git(
        repo_root, ["ls-tree", "--name-only", branch, "--", *sorted(ledger_names)]
    )
    if listed.strip():
        return "ledger"
    listed = _run_git(
        repo_root, ["ls-tree", "-r", "--name-only", branch, "--", *sorted(LEGACY_LAYOUT_DIRS)]
    )
    if listed.strip():
        return "layout"
    return None


def _worktree_legacy_kinds(root: Path, ledger_names: set[str]) -> set[str]:
    kinds: set[str] = set()
    if any((root / name).is_file() for name in ledger_names):
        kinds.add("ledger")
    if any((root / d).is_dir() and any((root / d).iterdir()) for d in LEGACY_LAYOUT_DIRS):
        kinds.add("layout")
    return kinds


def legacy_ledger_sources(
    repo_root: Path, prefix: str, dirs: tuple[str, ...]
) -> list[tuple[str, str]]:
    """返回仍含旧编号来源的 [(类型, 来源)]，类型 ∈ {"ledger", "layout"}。

    编号分配不再解析旧正文；存在旧单文件总账或旧布局目录时必须先显式迁移，
    或把旧分支/工作区隔离出当前操作集合，避免漏号撞号。
    """
    ledger_names = {f"{directory}.md" for directory in dirs}
    sources: list[tuple[str, str]] = []
    for branch in local_branches(repo_root):
        kind = _branch_legacy_kind(repo_root, branch, ledger_names)
        if kind is not None:
            sources.append((kind, f"分支 {branch}"))
    for root in worktree_roots(repo_root):
        for kind in _worktree_legacy_kinds(root, ledger_names):
            sources.append((kind, f"worktree {root}"))
    return sorted(set(sources))


def require_no_legacy_ledgers(
    repo_root: Path, prefix: str, dirs: tuple[str, ...], *, migration: str = ""
) -> None:
    sources = legacy_ledger_sources(repo_root, prefix, dirs)
    if not sources:
        return
    kinds = {kind for kind, _ in sources}
    labels = sorted({label for _, label in sources})
    hints: list[str] = []
    if "ledger" in kinds:
        hints.append(migration or _LEDGER_MIGRATION_HINTS.get(prefix, _LAYOUT_MIGRATION_HINT))
    if "layout" in kinds:
        hints.append(_LAYOUT_MIGRATION_HINT)
    raise IdScanError(
        "存在旧编号来源（" + "；".join(labels) + "）；编号分配已不解析旧正文/旧布局。"
        f"先执行显式迁移：{'；'.join(hints)}，并提交迁移结果后再分配；"
        "或把上述来源合并/删除"
    )


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
                found.setdefault(number, []).append(str(path.relative_to(root)))
    return found


def _check_duplicates(source: str, found: dict[int, list[str]], prefix: str) -> None:
    duplicated = {n: paths for n, paths in found.items() if len(paths) > 1}
    if duplicated:
        detail = "；".join(
            f"{prefix}{n:03d} 出现在 {', '.join(sorted(paths))}"
            for n, paths in sorted(duplicated.items())
        )
        raise IdScanError(f"{source} 中条目编号重复：{detail}")


def scan_max_id(
    repo_root: Path, prefix: str, dirs: tuple[str, ...], kind: str = "file"
) -> int:
    """扫描所有本地分支与 worktree，返回已用最大编号；无条目返回 0。

    kind="file" 时条目是 `{prefix}NNN_{slug}.md`；kind="dir" 时是同名目录。
    """
    maximum = 0
    for branch in local_branches(repo_root):
        found = _numbers_from_branch(repo_root, branch, prefix, dirs, kind)
        _check_duplicates(f"分支 {branch}", found, prefix)
        maximum = max([maximum, *found])
    for root in worktree_roots(repo_root):
        found = _numbers_from_worktree(root, prefix, dirs, kind)
        _check_duplicates(f"worktree {root}", found, prefix)
        maximum = max([maximum, *found])
    return maximum


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
    dirs: tuple[str, ...],
    target_dir: Path,
    slug: str,
    body: str,
    formatter=None,
    migration: str = "",
) -> Path:
    """锁内完成「取号 → 生成最终内容 → 格式化 → 原子发布」，返回新文件路径。

    formatter 接收唯一命名临时文件路径；抛错时不发布正式条目并清理临时文件。
    存在旧编号来源时拒绝分配，要求先显式迁移（migration 为提示命令）。
    """
    if not SLUG_RE.match(slug):
        raise IdScanError(f"slug 须匹配 {SLUG_RE.pattern}（收到 {slug!r}）")
    with id_lock(repo_root):
        require_no_legacy_ledgers(repo_root, prefix, dirs, migration=migration)
        number = scan_max_id(repo_root, prefix, dirs) + 1
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
    dirs: tuple[str, ...],
    target_dir: Path,
    slug: str,
    files: dict[str, str],
    formatter=None,
    migration: str = "",
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
        require_no_legacy_ledgers(repo_root, prefix, dirs, migration=migration)
        number = scan_max_id(repo_root, prefix, dirs, kind="dir") + 1
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


# ---------------------------------------------------------------------------
# 旧单文件总账解析（迁移用）
# ---------------------------------------------------------------------------


_LEGACY_HEADING_RE = re.compile(
    r"^ {0,3}(?P<hashes>#{1,6})[ \t]+(?P<prefix>[pd])(?P<num>[0-9]{3,})(?![0-9])[ \t]*(?P<title>.*)$"
)
_LEGACY_CANDIDATE_RE = re.compile(r"^ {0,3}#{1,6}[ \t]+(?P<prefix>[pd])[0-9]{3,}\b")
_FENCE_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})")


def _feed_fence(line: str, state: list) -> bool:
    """围栏行返回 True（该行属代码块，不参与标题判定）；state 为 [char, len] 或 [None, 0]。"""
    match = _FENCE_RE.match(line)
    if match is None:
        return state[0] is not None
    seq = match.group("fence")
    char, length = seq[0], len(seq)
    if state[0] is None:
        state[0], state[1] = char, length
        return True
    if char == state[0] and length >= state[1]:
        state[0], state[1] = None, 0
    return True


def legacy_ledger_entries(text: str, prefix: str) -> list[tuple[int, str, str]]:
    """解析旧单文件总账正文，返回 [(编号, 标题, 正文)]；正文不含标题行。

    fence-aware：围栏内 `## pNNN` 不算条目；行首 0-3 空格缩进标题照常解析；
    非条目同级标题不再截断当前条目，而是作为正文保留（挂到最近条目），
     ledger 开头的前言同样并入首条目，杜绝静默丢失。返回前以 fence-aware
    候选标题数核对解析条目数，不一致即中止，避免删源后丢失。
    """
    entries: list[tuple[int, str, str]] = []
    current: tuple[int, str] | None = None  # (number, title)
    body: list[str] = []
    preamble: list[str] = []
    fence: list = [None, 0]

    def flush() -> None:
        if current is not None:
            entries.append((current[0], current[1], "\n".join(body).strip()))

    for line in text.splitlines():
        if _feed_fence(line, fence):
            if current is not None:
                body.append(line)
            else:
                preamble.append(line)
            continue
        match = _LEGACY_HEADING_RE.match(line)
        if match and match.group("prefix") == prefix:
            flush()
            current = (int(match.group("num")), match.group("title").strip())
            body = []
            continue
        if current is not None:
            body.append(line)
        else:
            preamble.append(line)
    flush()

    if entries and any(part.strip() for part in preamble):
        first_number, first_title, first_body = entries[0]
        head = "\n".join(preamble).strip()
        merged = (head + "\n\n" + first_body).strip() if first_body else head
        entries[0] = (first_number, first_title, merged)

    fence_check: list = [None, 0]
    expected = 0
    for line in text.splitlines():
        if _feed_fence(line, fence_check):
            continue
        candidate = _LEGACY_CANDIDATE_RE.match(line)
        if candidate and candidate.group("prefix") == prefix:
            expected += 1
    if expected != len(entries):
        raise IdScanError(
            f"旧总账标题解析不完整：检测到 {expected} 个 {prefix}NNN 标题，"
            f"仅解析出 {len(entries)} 条；拒绝迁移以避免条目静默丢失"
        )
    return entries


class LegacyMigrationItem(NamedTuple):
    """旧总账一条待迁移条目：编号、规范 ID、slug、标题、正文、目标文件。"""

    number: int
    entry_id: str
    slug: str
    title: str
    body: str
    target: Path


def plan_legacy_migration(
    repo_root: Path, *, prefix: str, ledger_rel: str, target_dir: Path
) -> list[LegacyMigrationItem]:
    """解析旧单文件总账，返回待迁移条目；完整性校验与重复编号在此统一完成。"""
    legacy_path = repo_root / ledger_rel
    if not legacy_path.is_file():
        raise IdScanError(f"未找到旧单文件总账：{ledger_rel}")
    try:
        text = legacy_path.read_text(encoding="utf-8")
    except OSError as error:
        raise IdScanError(f"读取旧总账失败（{ledger_rel}）：{error}") from error
    entries = legacy_ledger_entries(text, prefix)
    if not entries:
        raise IdScanError(f"{ledger_rel} 未解析出任何 {prefix}NNN 条目")
    items: list[LegacyMigrationItem] = []
    seen: set[int] = set()
    for number, title, body in entries:
        if number in seen:
            raise IdScanError(f"{ledger_rel} 存在重复编号 {prefix}{number:03d}；迁移中止")
        seen.add(number)
        entry_id = canonical_entry_id(prefix, number)
        slug = slug_from_title(title)
        items.append(
            LegacyMigrationItem(
                number, entry_id, slug, title, body,
                target_dir / f"{entry_id}_{slug}.md",
            )
        )
    return items


def _try_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
            env=_controlled_git_env(),
        )
    except subprocess.TimeoutExpired as error:
        raise IdScanError(f"git {' '.join(args)} 超时（30s）") from error
    except FileNotFoundError as error:
        raise IdScanError("git 不可用") from error
    except OSError as error:
        raise IdScanError(f"git 执行失败（{' '.join(args)}）：{error}") from error


def _remove_legacy_ledger(repo_root: Path, ledger_rel: str) -> None:
    """已入库用 git rm 保留删除记录，未跟踪直接 unlink；失败统一包装 IdScanError。"""
    ledger_path = repo_root / ledger_rel
    if ledger_path.is_symlink():
        raise IdScanError(f"拒绝删除软链旧总账：{ledger_rel}")
    tracked = _try_git(repo_root, ["ls-files", "--error-unmatch", "--", ledger_rel]).returncode == 0
    if not tracked:
        try:
            ledger_path.unlink()
        except FileNotFoundError as error:
            raise IdScanError(f"旧总账已消失：{ledger_rel}") from error
        _fsync_dir(ledger_path.parent)
        return
    result = _try_git(repo_root, ["rm", "-f", "--", ledger_rel])
    if result.returncode != 0:
        raise IdScanError(f"git rm 旧总账失败：{result.stderr.strip()}")


def apply_legacy_migration(
    items: list[LegacyMigrationItem],
    *,
    repo_root: Path,
    prefix: str,
    ledger_rel: str,
    render: Callable[[str, str, str, str], str],
    formatter: Callable[[Path], None] | None = None,
) -> list[Path]:
    """锁内重验旧总账、预扫描、逐条 O_EXCL 原子发布，最后删除旧总账。

    预扫描、稳定性校验、发布、删源全部在 id_lock 内，杜绝锁外 TOCTOU 覆盖；
    发布经 staging + O_EXCL + fsync，目标已存在即中止不覆盖；总账已被并发
    迁移（源消失但目标齐全）时幂等返回，不回滚删除他人条目。
    """
    if not items:
        raise IdScanError("无可迁移条目")
    target_dir = items[0].target.parent
    created: list[Path] = []
    stagings: list[Path] = []
    try:
        with id_lock(repo_root):
            _assert_no_symlink(repo_root, target_dir)
            target_dir.mkdir(parents=True, exist_ok=True)
            _fsync_dir(target_dir.parent if target_dir.parent.exists() else repo_root)
            _assert_no_symlink(repo_root, target_dir)
            for item in items:
                _assert_no_symlink(repo_root, item.target)
            reclaim_staging(target_dir)
            ledger_path = repo_root / ledger_rel
            if ledger_path.is_symlink():
                raise IdScanError(f"拒绝迁移软链旧总账：{ledger_rel}")
            if not ledger_path.is_file():
                missing = [item.target for item in items if not item.target.is_file()]
                if not missing:
                    return [item.target for item in items]
                raise IdScanError(
                    f"旧总账已消失但仍缺 {len(missing)} 个目标（{missing[0].name}…）；"
                    "拒绝回滚删除已发布条目，请人工核对后重试"
                )
            try:
                fresh_text = ledger_path.read_text(encoding="utf-8")
            except OSError as error:
                raise IdScanError(f"读取旧总账失败（{ledger_rel}）：{error}") from error
            fresh = legacy_ledger_entries(fresh_text, prefix)
            if [(n, t, b) for n, t, b in fresh] != [
                (item.number, item.title, item.body) for item in items
            ]:
                raise IdScanError("旧总账在加锁前已被并发修改；迁移中止，请重新执行 migrate-legacy")
            for item in items:
                occupied = sorted(
                    path
                    for path in target_dir.glob("*.md")
                    if not path.is_symlink()
                    and (parsed := parse_entry_file_name(prefix, path.name))
                    and parsed[0] == item.number
                )
                if occupied:
                    raise IdScanError(
                        f"{item.entry_id} 已有条目文件："
                        f"{', '.join(path.as_posix() for path in occupied)}；迁移中止"
                    )
                if item.target.is_symlink() or item.target.exists():
                    raise IdScanError(f"{item.target} 已存在；迁移中止")
            for item in items:
                staging = _stage_file(
                    target_dir,
                    f"{item.entry_id}_{item.slug}",
                    render(item.entry_id, item.slug, item.title, item.body).encode("utf-8"),
                )
                stagings.append(staging)
                try:
                    if formatter is not None:
                        formatter(staging)
                    _publish_staged_file(staging, item.target)
                    stagings.remove(staging)
                except BaseException:
                    _cleanup_staging(staging)
                    if staging in stagings:
                        stagings.remove(staging)
                    raise
                created.append(item.target)
            _remove_legacy_ledger(repo_root, ledger_rel)
    except BaseException as error:
        for staging in list(stagings):
            _cleanup_staging(staging)
        for path in created:
            # 仅删除本轮 O_EXCL 新建的目标；幂等路径（created 为空）不删他人条目。
            path.unlink(missing_ok=True)
        if isinstance(error, OSError) and not isinstance(error, IdScanError):
            raise IdScanError(f"旧总账迁移 I/O 失败：{error}") from error
        raise
    return created


def slug_from_title(title: str) -> str:
    # 去掉标题末尾的括号说明（如「（2026-07-01 发现）」）再取 slug。
    title = re.sub(r"[（(][^）)]*[）)]\s*$", "", title.strip())
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return slug or "entry"
