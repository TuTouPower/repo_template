"""Canonical git_ops implementation for the task toolchain."""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import repo_task.context as ctx


def _git(
    args: list, *, repo: "ctx.RepoContext | None" = None, root: Path | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    if root is None and repo is None:
        raise ctx.TaskDataError("git 调用必须显式提供 repo 或 root")
    target = root or repo.root
    try:
        return subprocess.run(
            ["git", "-C", str(target), *args],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        # TimeoutExpired 非 OSError，CLI 捕不到会裸 traceback；统一包装（F22）
        raise ctx.TaskDataError(f"git {' '.join(args)} 超时（{timeout}s）") from error


def _git_bytes(
    args: list[str], *, input: bytes | None = None, repo: "ctx.RepoContext | None" = None, root: Path | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """Run Git without text decoding for binary-safe snapshots and diffs."""
    if root is None and repo is None:
        raise ctx.TaskDataError("git 调用必须显式提供 repo 或 root")
    target = root or repo.root
    try:
        return subprocess.run(
            ["git", "-C", str(target), *args],
            input=input,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise ctx.TaskDataError(f"git {' '.join(args)} 超时（{timeout}s）") from error


def _parse_cat_file_batch_output(
    stdout: bytes, expected_oids: Sequence[str]
) -> dict[str, bytes | None]:
    results: dict[str, bytes | None] = {}
    pos = 0
    total_len = len(stdout)
    num_expected = len(expected_oids)

    for idx, expected_oid in enumerate(expected_oids):
        if pos >= total_len:
            raise ctx.TaskDataError(
                f"git cat-file --batch 输出截断：预期 {num_expected} 项，第 {idx + 1} 项处 EOF"
            )
        newline_idx = stdout.find(b"\n", pos)
        if newline_idx == -1:
            raise ctx.TaskDataError(
                f"git cat-file --batch 输出截断：第 {idx + 1} 项头部缺少换行符"
            )
        header = stdout[pos:newline_idx]
        pos = newline_idx + 1

        if header.endswith(b" missing"):
            results[expected_oid] = None
            continue

        parts = header.split()
        if len(parts) != 3:
            raise ctx.TaskDataError(
                f"git cat-file --batch 头部格式非法：{header.decode('utf-8', errors='replace')!r}"
            )
        _sha_bytes, _type_bytes, size_bytes = parts
        try:
            size = int(size_bytes)
            if size < 0:
                raise ValueError
        except ValueError:
            raise ctx.TaskDataError(
                f"git cat-file --batch 头部 size 非法：{header.decode('utf-8', errors='replace')!r}"
            )

        if pos + size > total_len:
            raise ctx.TaskDataError(
                f"git cat-file --batch 输出截断：预期内容 {size} 字节，剩余 {total_len - pos} 字节"
            )
        content = stdout[pos : pos + size]
        pos += size

        if pos >= total_len or stdout[pos : pos + 1] != b"\n":
            raise ctx.TaskDataError(
                "git cat-file --batch 输出截断或格式错误：内容末尾缺少换行符"
            )
        pos += 1

        results[expected_oid] = content

    if pos < total_len:
        raise ctx.TaskDataError(
            f"git cat-file --batch 产生多余未解析数据：剩余 {total_len - pos} 字节"
        )

    return results


def batch_cat_file(
    oids: Sequence[str],
    *,
    root: Path | None = None,
    repo: "ctx.RepoContext | None" = None,
    timeout: int = 60,
    batch_size: int = 1000,
) -> dict[str, bytes | None]:
    """基于 git cat-file --batch 的单次非交互式批量 blob 读取。

    使用 subprocess.run(..., input=..., capture_output=True, timeout=...) 执行，
    由内部 communicate 处理，不可交互挂起。
    严格按照 git cat-file --batch 协议解析（<oid> <type> <size>\\n<content>\\n 与 <oid> missing\\n），
    处理 size 校验与截断检查。
    """
    if root is None and repo is None:
        raise ctx.TaskDataError("git 调用必须显式提供 repo 或 root")
    oid_list = list(oids)
    if not oid_list:
        return {}

    for oid in oid_list:
        if "\n" in oid or "\r" in oid:
            raise ctx.TaskDataError(f"非法对象 ID：{oid!r}")

    unique_oids = list(dict.fromkeys(oid_list))
    parsed_all: dict[str, bytes | None] = {}

    for i in range(0, len(unique_oids), batch_size):
        chunk = unique_oids[i : i + batch_size]
        input_bytes = b"".join(
            (oid.encode("utf-8") if isinstance(oid, str) else oid).rstrip(b"\r\n") + b"\n"
            for oid in chunk
        )
        r = _git_bytes(
            ["cat-file", "--batch"],
            input=input_bytes,
            root=root,
            repo=repo,
            timeout=timeout,
        )
        if r.returncode != 0:
            raise ctx.TaskDataError(
                f"git cat-file --batch 失败：{r.stderr.decode('utf-8', errors='replace').strip()}"
            )
        parsed = _parse_cat_file_batch_output(r.stdout, chunk)
        parsed_all.update(parsed)

    return {oid: parsed_all[oid] for oid in oid_list}


def _parse_batch_check_output(
    stdout: bytes, expected_oids: Sequence[str]
) -> dict[str, int]:
    """解析 ``git cat-file --batch-check`` 输出（<oid> <type> <size> 或 <oid> missing）。"""
    results: dict[str, int] = {}
    pos = 0
    total_len = len(stdout)
    for idx, expected_oid in enumerate(expected_oids):
        if pos >= total_len:
            raise ctx.TaskDataError(
                f"git cat-file --batch-check 输出截断：第 {idx + 1} 项处 EOF"
            )
        newline_idx = stdout.find(b"\n", pos)
        if newline_idx == -1:
            raise ctx.TaskDataError(
                f"git cat-file --batch-check 第 {idx + 1} 项头部缺少换行符"
            )
        header = stdout[pos:newline_idx].decode("utf-8", errors="replace")
        pos = newline_idx + 1
        parts = header.split()
        if len(parts) == 2 and parts[1] == "missing":
            raise ctx.TaskDataError(f"git 对象缺失：{expected_oid}")
        if len(parts) != 3:
            raise ctx.TaskDataError(
                f"git cat-file --batch-check 头部格式非法：{header!r}"
            )
        try:
            size = int(parts[2])
            if size < 0:
                raise ValueError
        except ValueError:
            raise ctx.TaskDataError(
                f"git cat-file --batch-check size 非法：{header!r}"
            )
        results[expected_oid] = size
    if pos < total_len:
        raise ctx.TaskDataError(
            f"git cat-file --batch-check 产生多余未解析数据：剩余 {total_len - pos} 字节"
        )
    return results


def batch_object_sizes(
    oids: Sequence[str],
    *,
    root: Path | None = None,
    repo: "ctx.RepoContext | None" = None,
    timeout: int = 60,
    batch_size: int = 1000,
) -> dict[str, int]:
    """批量读取对象大小（不读内容），供隐私扫描在 cat-file 前做大小预过滤。

    缺失对象直接抛 TaskDataError（已跟踪对象不应缺失，fail-closed）。
    """
    if root is None and repo is None:
        raise ctx.TaskDataError("git 调用必须显式提供 repo 或 root")
    oid_list = list(oids)
    if not oid_list:
        return {}
    for oid in oid_list:
        if "\n" in oid or "\r" in oid:
            raise ctx.TaskDataError(f"非法对象 ID：{oid!r}")
    unique_oids = list(dict.fromkeys(oid_list))
    all_sizes: dict[str, int] = {}
    for i in range(0, len(unique_oids), batch_size):
        chunk = unique_oids[i : i + batch_size]
        input_bytes = b"".join(oid.encode("utf-8") + b"\n" for oid in chunk)
        r = _git_bytes(
            ["cat-file", "--batch-check"],
            input=input_bytes,
            root=root,
            repo=repo,
            timeout=timeout,
        )
        if r.returncode != 0:
            raise ctx.TaskDataError(
                f"git cat-file --batch-check 失败：{r.stderr.decode('utf-8', errors='replace').strip()}"
            )
        all_sizes.update(_parse_batch_check_output(r.stdout, chunk))
    return {oid: all_sizes[oid] for oid in oid_list}


def default_branch(
    root: Path | None = None, *, repo: "ctx.RepoContext | None" = None,
) -> str:
    """主干分支名：origin/HEAD → init.defaultBranch → 探测 main/master → 本地唯一非 task 分支。"""
    if root is None and repo is None:
        raise ctx.TaskDataError("git 调用必须显式提供 repo 或 root")
    root = root or repo.root
    r = _git(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], root=root)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip().removeprefix("origin/")
    r = _git(["config", "--get", "init.defaultBranch"], root=root)
    if r.returncode == 0 and r.stdout.strip():
        name = r.stdout.strip()
        if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"], root=root).returncode == 0:
            return name
    for name in ("main", "master"):
        if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"], root=root).returncode == 0:
            return name
    branches = _git(["for-each-ref", "--format=%(refname:short)", "refs/heads"], root=root)
    if branches.returncode == 0:
        non_task = [
            b.strip() for b in branches.stdout.splitlines()
            if b.strip() and not ctx.TASK_BRANCH_RE.fullmatch(b.strip())
        ]
        if len(non_task) == 1:
            return non_task[0]
    return "main"

def resolve_local_branch(
    name: str, *, repo: "ctx.RepoContext",
) -> tuple[str, str]:
    """校验本地分支并返回 (分支名, 完整 HEAD SHA)。"""
    if not name or name.startswith("-"):
        raise ctx.TaskDataError(f"本地分支名非法：{name!r}")
    ref = f"refs/heads/{name}"
    if _git(["show-ref", "--verify", "--quiet", ref], repo=repo).returncode != 0:
        raise ctx.TaskDataError(f"本地分支不存在：{name!r}")
    r = _git(["rev-parse", f"{ref}^{{commit}}"], repo=repo)
    if r.returncode != 0 or not r.stdout.strip():
        raise ctx.TaskDataError(f"无法解析本地分支 {name!r} 的 HEAD")
    return name, r.stdout.strip()

def _get_head_short(*, repo: "ctx.RepoContext") -> str:
    r = _git(["rev-parse", "--short", "HEAD"], repo=repo)
    return (r.stdout.strip() or "unknown") if r.returncode == 0 else "unknown"

def _get_head(*, repo: "ctx.RepoContext") -> str:
    """全量 hash：存 diff_anchor 用（短 hash 长历史下有歧义风险）；展示场景用 _get_head_short。"""
    r = _git(["rev-parse", "HEAD"], repo=repo)
    return (r.stdout.strip() or "unknown") if r.returncode == 0 else "unknown"

def has_unmerged_commits(branch: str, *, repo: "ctx.RepoContext") -> bool:
    """branch 上是否有未合并进主干的 commit；不可判定时保守返回 True。"""
    if not branch:
        return False
    base = default_branch(repo=repo)
    if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{base}"], repo=repo).returncode != 0:
        print(f"WARNING: 主干分支 '{base}' 不存在，无法判断是否已合并；按「有未合并 commit」处理", file=sys.stderr)
        return True
    if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], repo=repo).returncode != 0:
        return False
    r = _git(["rev-list", "--count", f"{base}..{branch}"], repo=repo)
    if r.returncode != 0:
        print(f"WARNING: 无法比对 {branch} 与 {base}；按「有未合并 commit」处理", file=sys.stderr)
        return True
    try:
        return int(r.stdout.strip()) > 0
    except ValueError:
        return True

def tracked_anywhere(rel_path: str, *, repo: "ctx.RepoContext") -> bool:
    """路径是否在**任意分支**被跟踪（不只当前签出的索引）。"""
    if _git(["ls-files", "--error-unmatch", rel_path], repo=repo).returncode == 0:
        return True
    r = _git(["rev-list", "--all", "--max-count=1", "--", rel_path], repo=repo)
    return r.returncode == 0 and bool(r.stdout.strip())

def _porcelain_paths(stdout: str, *, include_rename_sources: bool = True) -> list[str]:
    """解析 `git status --porcelain -z` 输出的路径列表。

    当 include_rename_sources=True 时，包含重命名/复制的来源路径与目标路径两端（Issue #90）。
    """
    out, fields, i = [], stdout.split("\0"), 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if not entry:
            continue
        code, path = entry[:2], entry[3:]
        if code[0] in ("R", "C"):  # 重命名/复制：下一字段是来源路径
            source_path = fields[i]
            i += 1
            if include_rename_sources and source_path:
                out.append(source_path)
        out.append(path)
    return out

def porcelain_entries(
    root: Path | None = None,
    *,
    include_rename_sources: bool = True,
    repo: "ctx.RepoContext",
) -> list[str]:
    """`git status --porcelain -z` 的路径列表，避免引号与转义带来的解析错误。

    git 失败时抛 TaskDataError（fail-closed）：调用方把空集合理解为「干净」，
    若静默降级会在 merge/rewind 时带着未提交改动继续。
    """
    r = _git(["status", "--porcelain", "-z", "-uall"], root=root, repo=repo)
    if r.returncode != 0:
        raise ctx.TaskDataError(f"git status --porcelain 失败：{r.stderr.strip()}")
    return _porcelain_paths(r.stdout, include_rename_sources=include_rename_sources)

def tracked_dirty_entries(
    root: Path | None = None,
    *,
    include_rename_sources: bool = True,
    repo: "ctx.RepoContext",
) -> list[str]:
    """只含已跟踪文件的未提交改动；未跟踪文件不影响 merge 结果，不计入。"""
    r = _git(["status", "--porcelain", "-z", "--untracked-files=no"], root=root, repo=repo)
    if r.returncode != 0:
        raise ctx.TaskDataError(
            f"git status --porcelain (tracked) 失败：{r.stderr.strip()}"
        )
    return _porcelain_paths(r.stdout, include_rename_sources=include_rename_sources)

def worktree_paths(*, repo: "ctx.RepoContext") -> dict[str, str]:
    """`git worktree list --porcelain` → {绝对路径: 分支名}。

    键统一为 Path.resolve() 后的字符串，与所有调用方 str(path) 比较对齐。
    git 输出正斜杠路径，Path.resolve() 在 Windows 给反斜杠——规范化消除差异。
    git 失败时抛 TaskDataError（fail-closed）：空 dict 会让 discard/remove_worktree
    误报「已清理」。
    """
    result, current = {}, None
    r = _git(["worktree", "list", "--porcelain"], repo=repo)
    if r.returncode != 0:
        raise ctx.TaskDataError(f"git worktree list 失败：{r.stderr.strip()}")
    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            raw = line[len("worktree "):].strip()
            current = str(Path(raw).resolve()) if raw else None
            if current:
                result[current] = ""
        elif line.startswith("branch ") and current:
            result[current] = line[len("branch "):].strip().removeprefix("refs/heads/")
    return result

def primary_worktree_path(*, repo: "ctx.RepoContext") -> Path:
    """返回 Git 登记的主工作区；worktree list 第一项始终是主工作区。"""
    paths = worktree_paths(repo=repo)
    if not paths:
        sys.exit("无法读取 git worktree 列表")
    return Path(next(iter(paths))).resolve()

def in_primary_worktree(*, repo: "ctx.RepoContext") -> bool:
    return repo.root.resolve() == primary_worktree_path(repo=repo)

def current_branch(*, repo: "ctx.RepoContext") -> str:
    r = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo=repo)
    return r.stdout.strip() if r.returncode == 0 else ""

def require_primary_worktree(*, repo: "ctx.RepoContext") -> None:
    if not in_primary_worktree(repo=repo):
        sys.exit("此命令只能在主工作区执行；请 cd 回主仓")
    base = default_branch(repo=repo)
    branch = current_branch(repo=repo)
    if branch != base:
        sys.exit(f"此命令只能在主干 {base!r} 执行（当前 {branch!r}）")

def task_worktree_path(fm: dict, *, repo: "ctx.RepoContext") -> Path:
    return (repo.primary_root / repo.effective_worktree(fm)).resolve()

def in_own_task_worktree(fm: dict, *, repo: "ctx.RepoContext") -> bool:
    expected = task_worktree_path(fm, repo=repo)
    return (
        repo.root.resolve() == expected
        and str(expected) in worktree_paths(repo=repo)
        and current_branch(repo=repo) == fm.get("branch", "")
    )

def require_own_task_worktree(fm: dict, *, repo: "ctx.RepoContext") -> None:
    if not in_own_task_worktree(fm, repo=repo):
        sys.exit(
            f"{fm['tid']} 必须在自身 worktree {repo.effective_worktree(fm)} 的分支 "
            f"{fm.get('branch')!r} 执行"
        )


def is_ancestor(
    maybe_ancestor: str, descendant: str, *, repo: "ctx.RepoContext",
) -> bool:
    """严格判定 maybe_ancestor 是否为 descendant 的祖先（0->True, 1->False, 其余->TaskDataError）。"""
    result = _git(["merge-base", "--is-ancestor", maybe_ancestor, descendant], repo=repo)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    error = result.stderr.strip() or f"退出码 {result.returncode}"
    raise ctx.TaskDataError(f"git merge-base 失败：{error}")

