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
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        raise IdScanError(f"git {' '.join(args)} 失败：{stderr or e.returncode}") from e
    except FileNotFoundError as e:
        raise IdScanError("git 不可用，无法扫描分支与 worktree") from e
    return result.stdout


@contextmanager
def id_lock(repo_root: Path):
    """在 git 公共目录上取排他锁，覆盖「扫描取号 → 建文件」全过程。

    委托 repo_task.locks.git_common_lock：锁文件初始化与同线程重入由公共 helper 处理，兼容 Windows
    msvcrt（空文件锁 1 字节超 EOF 抛 OSError，RT-011）。git 失败包装为 IdScanError。
    """
    try:
        with git_common_lock(repo_root, LOCK_NAME):
            yield
    except OSError as error:
        raise IdScanError(str(error)) from error


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
    text = _run_git(
        repo_root, ["ls-tree", "-r", "--name-only", branch, "--", *dirs]
    )
    found: dict[int, list[str]] = {}
    dir_parts = {d: Path(d).parts for d in dirs}
    for rel in text.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        parts = Path(rel).parts
        if kind == "file":
            # 文件型条目身份取文件自身相对路径。
            candidates = [(parts[-1], rel)]
        else:
            # 目录型条目只接受 `{dir}/<name>` 直接子目录，与 worktree 顶层扫描口径一致；
            # 嵌套路径（如 docs/spikes/misc/s005_fixture/report.md）不计入编号。
            candidates = []
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
        if not base.is_dir():
            continue
        entries = base.rglob("*.md") if kind == "file" else base.iterdir()
        for path in entries:
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


def _publish_staged_file(staging: Path, target: Path) -> None:
    _verify_staged_file(staging, target.name)
    if target.is_symlink() or target.exists():
        raise IdScanError(f"{target} 已存在；请先处理后重试")
    os.replace(staging, target)


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
        if path.exists():
            raise IdScanError(f"{path} 已存在；请先处理后重试")
        _assert_no_symlink(repo_root, target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        _assert_no_symlink(repo_root, target_dir)
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
        if path.exists():
            raise IdScanError(f"{path} 已存在；请先处理后重试")
        _assert_no_symlink(repo_root, target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        _assert_no_symlink(repo_root, target_dir)
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
            os.replace(staging, path)
        except BaseException as error:
            _cleanup_staging(staging)
            _reraise_publish_error(error)
    return path


# ---------------------------------------------------------------------------
# 旧单文件总账解析（迁移用）
# ---------------------------------------------------------------------------


_LEGACY_HEADING_RE = re.compile(
    r"^(?P<hashes>#{1,6})[ \t]+(?P<prefix>[pd])(?P<num>[0-9]{3,})(?![0-9])[ \t]*(?P<title>.*)$"
)
_LEGACY_CANDIDATE_RE = re.compile(r"^#{1,6}[ \t]+(?P<prefix>[pd])[0-9]{3,}\b")


def legacy_ledger_entries(text: str, prefix: str) -> list[tuple[int, str, str]]:
    """解析旧单文件总账正文，返回 [(编号, 标题, 正文)]；正文不含标题行。

    标题级别放宽到 `#{1,6}`，并在返回前把「全文候选标题数」与「解析条目数」比对：
    不一致说明存在无法解析的条目标题，直接中止迁移，避免 git rm 旧总账后条目静默丢失。
    """
    entries: list[tuple[int, str, str]] = []
    current: tuple[int, str, int] | None = None  # (number, title, level)
    body: list[str] = []

    def flush() -> None:
        if current is not None:
            entries.append((current[0], current[1], "\n".join(body).strip()))

    for line in text.splitlines():
        match = _LEGACY_HEADING_RE.match(line)
        if match and match.group("prefix") == prefix:
            flush()
            current = (
                int(match.group("num")),
                match.group("title").strip(),
                len(match.group("hashes")),
            )
            body = []
            continue
        if current is not None:
            heading = re.match(r"^(#{1,6})[ \t]+", line)
            if heading and len(heading.group(1)) <= current[2]:
                flush()
                current = None
                body = []
                continue
            body.append(line)
    flush()

    expected = sum(
        1
        for line in text.splitlines()
        if (candidate := _LEGACY_CANDIDATE_RE.match(line))
        and candidate.group("prefix") == prefix
    )
    if expected != len(entries):
        raise IdScanError(
            f"旧总账标题解析不完整：检测到 {expected} 个 {prefix}NNN 标题，"
            f"仅解析出 {len(entries)} 条；拒绝迁移以避免条目静默丢失"
        )
    return entries


def slug_from_title(title: str) -> str:
    # 去掉标题末尾的括号说明（如「（2026-07-01 发现）」）再取 slug。
    title = re.sub(r"[（(][^）)]*[）)]\s*$", "", title.strip())
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return slug or "entry"
