#!/usr/bin/env python3
"""_id_scan.py - 跨 worktree 跨分支的条目编号扫描与互斥分配。

pending.py / findings.py / spikes.py 共用。条目以「一条目一文件」形式存放，文件名形如
`p047_cli_exit_code.md` / `d012_uv_lock_marker.md`，编号直接来自文件名，无需解析正文。

扫描来源：
- 所有本地分支的 git 树（`git ls-tree` 文件名）；
- 所有登记 worktree 的工作区文件（含未提交条目）。

只有声明当前 entity_schema 的分支/worktree 参与分配；历史来源显式隔离。
旧格式转换只存在于 template_sync，运行时不识别旧目录或解析旧正文。
合并边界验证目标 schema 与编号唯一性，禁止旧分支直接进入新协议。

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

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from repo_task.locks import TASK_ID_LOCK_NAME, git_common_lock
from repo_task.context import TaskDataError
from repo_task.entity_schema import SCHEMA_PATH, current_schema, validate_schema

LOCK_NAME = TASK_ID_LOCK_NAME

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
        parent = _run_git(repo_root, ["ls-tree", "--name-only", branch, "--", rel_dir])
        if not parent.strip():
            continue
        text = _run_git(repo_root, ["ls-tree", f"{branch}:{rel_dir}"])
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
        if not branch_uses_current_schema(repo_root, branch):
            continue
        validate_snapshot_ids(repo_root, branch)
        found = _numbers_from_branch(repo_root, branch, prefix, dirs, kind)
        _check_duplicates(f"分支 {branch}", found, prefix)
        maximum = max([maximum, *found])
    for root in worktree_roots(repo_root):
        if not current_schema(root):
            continue
        validate_worktree_shape(root)
        found = _numbers_from_worktree(root, prefix, dirs, kind)
        _check_duplicates(f"worktree {root}", found, prefix)
        maximum = max([maximum, *found])
    return maximum


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
    args = ["ls-files", "-z"] if ref is None else ["ls-tree", "-r", "--name-only", "-z", ref]
    files = _run_git(repo_root, args).split("\0")
    for prefix, directory, kind in (("p", "docs/pending", "file"), ("d", "docs/findings", "file"), ("s", "docs/spikes", "dir")):
        found: dict[int, list[str]] = {}
        for rel in files:
            if not rel.startswith(directory + "/"):
                continue
            tail = rel[len(directory) + 1:].split("/")
            if kind == "file" and len(tail) != 1:
                raise IdScanError(f"当前实体布局禁止嵌套条目：{rel}；先从固定来源运行 repo_sync.py plan / apply 完成内置迁移")
            number = _entry_number(prefix, tail[0], kind=kind)
            if number is not None:
                identity = directory + "/" + tail[0]
                bucket = found.setdefault(number, [])
                if identity not in bucket:
                    bucket.append(identity)
        _check_duplicates(ref or "index", found, prefix)


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
    dirs: tuple[str, ...],
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
