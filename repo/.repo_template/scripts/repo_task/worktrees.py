"""Canonical worktrees implementation for the task toolchain."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Iterable

import repo_task.context as ctx

from .documents import atomic_write_text
from .git_ops import _get_head, _git, current_branch, default_branch, resolve_local_branch, worktree_paths
from .locks import git_common_dir
from .store import discover_effective_sources, load_task_at_ref

WORKTREE_INCLUDE = ".worktreeinclude"
# 控制面与 Git 元数据不随 .worktreeinclude 共享：worktree 写入 docs/runtime
# 会混淆主仓 ledger 与 execution_context。
INCLUDE_DENY = (".git", "docs/runtime")
EXCLUDE_BLOCK_START = "# repo_template:worktreeinclude start"
EXCLUDE_BLOCK_END = "# repo_template:worktreeinclude end"


def _ignored_others(*exclude_args: str, repo: "ctx.RepoContext") -> list[str]:
    r = _git(["ls-files", "-z", "--others", "--ignored", "--directory", *exclude_args], repo=repo)
    if r.returncode != 0:
        raise ctx.TaskDataError(f"git ls-files 失败：{r.stderr.strip()}")
    return [entry for entry in r.stdout.split("\0") if entry]


def _include_sources(repo: "ctx.RepoContext") -> list[Path]:
    """主仓 .worktreeinclude（gitignore 语法）命中且确被 gitignore 的路径。

    命中目录时整目录软链；未被 gitignore 的未跟踪路径（用户在途工作）与
    INCLUDE_DENY 一律跳过，已入库路径不在 --others 结果中，天然排除。
    """
    include = repo.root / WORKTREE_INCLUDE
    if not include.is_file():
        return []
    ignored = set(_ignored_others("--exclude-standard", repo=repo))
    sources = []
    for entry in _ignored_others(f"--exclude-from={include}", repo=repo):
        parts = entry.rstrip("/").split("/")
        rel = "/".join(parts)
        if any(rel == deny or rel.startswith(deny + "/") for deny in INCLUDE_DENY):
            continue
        ancestors = {"/".join(parts[:i]) + "/" for i in range(1, len(parts))}
        if entry in ignored or ancestors & ignored:
            sources.append(repo.root / rel)
    return sources


def _untracked_in(worktree: Path, paths: list[str]) -> list[str]:
    r = _git(["ls-files", "-z", "--others", "--exclude-standard", "--", *paths], root=worktree)
    if r.returncode != 0:
        raise ctx.TaskDataError(f"git ls-files 失败：{r.stderr.strip()}")
    return [entry.rstrip("/") for entry in r.stdout.split("\0") if entry]


def _ensure_links_ignored(
    worktree: Path, linked: list[str], *, repo: "ctx.RepoContext",
) -> None:
    """以 / 结尾的 gitignore 规则只匹配目录，不匹配同名软链，链接会在 worktree
    显示为未跟踪而被误暂存。对这类链接在共享 info/exclude 托管块追加锚定规则；
    这些路径在主仓本已被忽略，规则对主仓与其它 worktree 语义不变。"""
    if not linked:
        return
    # 不再因缺少 .git 而静默跳过边界保护：后续 _untracked_in/git_common_dir 会
    # 对误配置显式报错（fail-closed），单测需用真实/注入的 git 上下文。
    missing = _untracked_in(worktree, linked)
    if not missing:
        return
    exclude = git_common_dir(repo.primary_root) / "info" / "exclude"
    lines = exclude.read_text(encoding="utf-8").splitlines() if exclude.is_file() else []
    if EXCLUDE_BLOCK_START in lines and EXCLUDE_BLOCK_END in lines:
        start, end = lines.index(EXCLUDE_BLOCK_START), lines.index(EXCLUDE_BLOCK_END)
        before, block, after = lines[:start], lines[start + 1:end], lines[end + 1:]
    else:
        before, block, after = lines, [], []
    block += [
        _escape_exclude_segment(rel) for rel in missing
        if _escape_exclude_segment(rel) not in block
    ]
    exclude.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        exclude,
        "\n".join([*before, EXCLUDE_BLOCK_START, *block, EXCLUDE_BLOCK_END, *after]) + "\n",
    )
    still = _untracked_in(worktree, linked)
    if still:
        raise ctx.TaskDataError(f"软链在 worktree 中未被忽略：{', '.join(still)}")


def _escape_exclude_segment(rel: str) -> str:
    """转义 info/exclude 锚定规则中的 glob 元字符。

    路径含 ``* ? [ ]`` 时 git 按 glob 解释，一条规则会误匹配一批无关路径。
    锚定规则只表达字面路径：反斜杠与 glob 元字符加反斜杠转义，尾随空格转义
    （gitignore 会吞掉未转义的尾随空格）。
    """
    out = rel.replace("\\", "\\\\")
    out = re.sub(r"([*?\[\]])", r"\\\1", out)
    stripped = out.rstrip(" ")
    if len(stripped) != len(out):
        out = stripped + "\\ " * (len(out) - len(stripped))
    return "/" + out


def _normalize_rel(p: str) -> str:
    """规范化仓库相对路径：仅移除显式 './' 前缀与尾随 '/'，不剥字面前导点。

    lstrip('./') 会误删 .venv/.env 等点前缀，导致 git 忽略规则永远匹配不到。
    """
    return Path(p).as_posix().removeprefix("./").rstrip("/")


def find_unignored_env_symlinks(root: Path, paths: Iterable[str]) -> dict[str, str]:
    """诊断未跟踪软链中因 Git 规则以 '/' 结尾仅匹配目录而未被忽略的环境软链。

    返回 {rel_path: matched_rule} 映射。未命中目录忽略规则的软链或非软链不返回。
    """
    symlinks = [_normalize_rel(p) for p in paths if (root / p).is_symlink()]
    if not symlinks:
        return {}
    r = _git(["rev-parse", "--git-dir"], root=root)
    if r.returncode != 0:
        return {}
    git_dir_raw = r.stdout.strip()
    git_dir = Path(git_dir_raw) if os.path.isabs(git_dir_raw) else (root / git_dir_raw).resolve()

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        for rel in symlinks:
            parts = Path(rel).parts
            curr_src = root
            curr_dst = td_path
            for part in ["", *parts[:-1]]:
                if part:
                    curr_src = curr_src / part
                    curr_dst = curr_dst / part
                    curr_dst.mkdir(parents=True, exist_ok=True)
                gi = curr_src / ".gitignore"
                if gi.is_file():
                    (curr_dst / ".gitignore").write_bytes(gi.read_bytes())

        check_map = {}
        check_paths = []
        for rel in symlinks:
            cand = f"{rel}/"
            check_paths.append(cand)
            check_map[cand] = rel

        cmd = [
            "-c", "core.quotePath=false",
            "--git-dir", str(git_dir), "--work-tree", str(td_path),
            "check-ignore", "-v", "--no-index", "--", *check_paths,
        ]
        check = _git(cmd, root=td_path)
        results = {}
        if check.returncode == 0:
            for line in check.stdout.strip().splitlines():
                if not line:
                    continue
                # -v 格式 "<source>:<linenum>:<pattern>\t<pathname>"：路径本身可含
                # 制表符时按最后一个制表符切分；quotePath 已关闭，不再出现 C 引号。
                pre, sep, matched_cand = line.rpartition("\t")
                if not sep:
                    continue
                orig = check_map.get(matched_cand)
                if orig:
                    results[orig] = pre
        return results


def link_local_files(worktree: Path, *, repo: "ctx.RepoContext") -> list[str]:
    """把主仓未入库的本地文件软链进 worktree（同相对路径）。

    默认链接任意层级的 .env；主仓有 .worktreeinclude 时另链其命中的
    gitignore 路径（文件或整目录）。.env 扫描跳过 .git、node_modules、
    .scratch 等噪声目录；先物化源列表再建链，并跳过落在目标 worktree
    之内的源，防止把 worktree 内部文件当成主仓源造成级联嵌套。
    """
    linked = []
    skip_dirs = {
        ".git", "node_modules", ".scratch", "__pycache__", "venv", ".venv",
        "target", "worktrees", "artifacts", "data", "local", "docs", ".repo_template",
    }
    try:
        worktree_resolved = worktree.resolve()
    except OSError:
        worktree_resolved = None

    sources = []
    env_sources = []
    for root_dir, dirs, files in os.walk(repo.root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        if ".env" in files:
            src = Path(root_dir) / ".env"
            rel = src.relative_to(repo.root)
            if not any(part in skip_dirs for part in rel.parts[:-1]):
                sources.append(src)
                env_sources.append(rel.as_posix())
    env_count = len(sources)
    sources.extend(_include_sources(repo))

    ignored_env = set()
    if env_sources and ((repo.root / ".git").exists() or (repo.primary_root / ".git").exists()):
        # fail-closed：git 查询失败时无法确定忽略状态，让 TaskDataError 带诊断
        # 传播（由 start 流程回滚），不再静默按「无需补忽略规则」继续链接。
        untracked_in_main = set(_untracked_in(repo.root, env_sources))
        ignored_env = {p for p in env_sources if p not in untracked_in_main}

    to_ensure = []
    for index, src in enumerate(sources):
        try:
            rel = src.relative_to(repo.root)
        except ValueError:
            continue
        rel_posix = rel.as_posix()
        try:
            src_res = src.resolve()
            if worktree_resolved and (
                src_res == worktree_resolved
                or src_res.is_relative_to(worktree_resolved)
                or worktree_resolved.is_relative_to(src_res)
            ):
                continue
        except (ValueError, OSError):
            pass

        if index >= env_count or rel_posix in ignored_env:
            to_ensure.append(rel_posix)

        dst = worktree / rel
        if dst.exists() or dst.is_symlink():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.symlink_to(os.path.relpath(src, dst.parent))
        linked.append(rel_posix)

    _ensure_links_ignored(worktree, to_ensure, repo=repo)
    return linked

def is_managed_link(
    worktree: Path, link: Path, *, repo: "ctx.RepoContext",
) -> bool:
    """仅识别 link_local_files() 为主仓同相对路径创建的软链。"""
    if not link.is_symlink():
        return False
    try:
        rel = link.relative_to(worktree)
    except ValueError:
        return False
    source = repo.root / rel
    expected = os.path.relpath(source, link.parent)
    try:
        target = os.readlink(link)
    except OSError:
        return False
    return (
        target == expected
        and link.resolve(strict=False) == source.resolve(strict=False)
    )

def unlink_managed_links(
    worktree: Path, *, repo: "ctx.RepoContext",
) -> None:
    # 用 os.walk 而非 Path.rglob：rglob 在 Python 3.10+ 会跳过 dangling
    # symlink（源已删的链接），清理时恰恰需要删除这类残留。目录软链出现在
    # dirs 中且 followlinks=False 不会进入，只删链接本身，不触及主仓目标。
    for root, dirs, files in os.walk(worktree, followlinks=False):
        for name in (*dirs, *files):
            link = Path(root) / name
            if is_managed_link(worktree, link, repo=repo):
                link.unlink()

def resolve_start_base(
    base_arg: str | None, *, repo: "ctx.RepoContext",
) -> tuple[str, str]:
    """解析 start base。

    `task-run` 链式：后一个 task 从上一个已完成 task 分支创建，传 `--base`。
    多会话手动并发：用户未传 `--base` 且最新前置未合并主干时，由 `cmd_start`
    自动落到前置分支 tip（见 integration.py）；否则从主干 HEAD 创建。
    """
    primary = default_branch(repo=repo)
    base_branch, base_sha = resolve_local_branch(base_arg or primary, repo=repo)
    if base_branch == primary:
        if current_branch(repo=repo) != primary or _get_head(repo=repo) != base_sha:
            raise ctx.TaskDataError(f"主工作区 HEAD 与本地 {primary!r} 不一致")
        return base_branch, base_sha

    match = ctx.TASK_BRANCH_RE.fullmatch(base_branch)
    if not match:
        raise ctx.TaskDataError(
            f"--base 只接受默认分支或本地 task 分支（收到 {base_branch!r}）"
        )
    previous_tid = match.group(1)
    _, previous_fm, _ = load_task_at_ref(previous_tid, base_sha, repo=repo)
    expected_branch = f"{previous_tid}_{previous_fm.get('slug', '')}"
    if base_branch != expected_branch:
        raise ctx.TaskDataError(
            f"--base 分支名 {base_branch!r} 与 {previous_tid} slug 不符"
            f"（应为 {expected_branch!r}）；拒绝伪装成 task 分支的普通分支"
        )
    if previous_fm.get("status") not in ctx.TERMINAL_STATUSES:
        # 分支 tip 状态由 finish 提交的实现 commit 决定。有效状态显示已完成
        # 而分支 tip 仍是 backlog 时，真实原因是完成状态尚未提交，不是没完成。
        hint = ""
        try:
            effective = discover_effective_sources(repo=repo).get(previous_tid)
        except ctx.TaskDataError:
            effective = None
        if effective and effective["status"] in ctx.TERMINAL_STATUSES:
            hint = (
                f"（{previous_tid} 有效状态为 {effective['status']}（来源 {effective['source']}），"
                "完成状态尚未提交到分支；先在该 task worktree 提交 task commit 再重试）"
            )
        raise ctx.TaskDataError(
            f"--base {base_branch!r} 对应 {previous_tid} status="
            f"{previous_fm.get('status')!r}，须先完成或 drop{hint}"
        )
    registered = [path for path, branch in worktree_paths(repo=repo).items() if branch == base_branch]
    if registered:
        raise ctx.TaskDataError(
            f"--base {base_branch!r} 仍登记 worktree：{', '.join(registered)}；"
            "先 cleanup-worktree"
        )
    return base_branch, base_sha

def _tid_from_worktree_rel(rel: str, repo: "ctx.RepoContext") -> str | None:
    """从 worktree 相对路径反解归属 tid：必须形如 ``../{repo.name}_{tid}``。

    形如 ``/``、绝对路径或命名不符的一律返回 None（fail-closed，由调用方拒绝）。
    repo 名本身可含下划线，按最后一个下划线切分 tid 后缀。
    """
    prefix = f"../{repo.name}_"
    if not rel.startswith(prefix):
        return None
    tid = rel[len(prefix):]
    return tid if ctx.TID_RE.fullmatch(tid) else None


def _check_worktree_ownership(
    rel: str, registered_branch: str | None, tid: str | None,
    *, repo: "ctx.RepoContext",
) -> str | None:
    """破坏性操作前的归属校验；通过返回 None，否则返回拒绝理由。

    ``tid`` 给定时：``rel`` 必须恰等于 ``repo.worktree_rel(tid)`` 派生路径；
    已登记分支必须满足 task 分支命名约束且 tid 前缀与 ``tid`` 一致。``tid``
    缺省时只做路径/分支一致性比对（两者都能解析且 tid 不一致才拒绝），保持
    旧调用兼容；跨 task 伪造（同源篡改 rel 与 branch）须由调用方传 ``tid``。
    """
    if tid is not None:
        if not ctx.TID_RE.fullmatch(tid):
            return f"tid 非法：{tid!r}；拒绝破坏性 worktree 操作"
        expected = repo.worktree_rel(tid)
        if rel != expected:
            return (
                f"{rel} 与 {tid} 派生路径 {expected} 不符；"
                "拒绝操作非归属 worktree"
            )
        if registered_branch:
            match = ctx.TASK_BRANCH_RE.fullmatch(registered_branch)
            if not match or match.group(1) != tid:
                return (
                    f"{rel} 登记分支为 {registered_branch!r}，与 {tid} 不符；"
                    "拒绝操作非归属 worktree"
                )
        return None
    rel_tid = _tid_from_worktree_rel(rel, repo)
    if rel_tid is not None and registered_branch:
        match = ctx.TASK_BRANCH_RE.fullmatch(registered_branch)
        if match and match.group(1) != rel_tid:
            return (
                f"{rel} 登记分支为 {registered_branch!r}，"
                f"与路径归属 {rel_tid} 不符；拒绝操作非归属 worktree"
            )
    return None


def create_worktree(
    tid: str, branch: str, base_sha: str, *, repo: "ctx.RepoContext",
) -> str:
    """从固定 base SHA 创建全新的 task branch/worktree；调用方负责补偿失败。"""
    rel = repo.worktree_rel(tid)
    raw = repo.primary_root / rel
    path = raw.resolve()
    # 存在判定用未解析路径 lexists：目标是 dangling 软链时 resolve() 会跟随到
    # 不存在的 target 而漏判，随后 git worktree add 因路径已存在失败。
    if os.path.lexists(raw) or str(path) in worktree_paths(repo=repo):
        raise ctx.TaskDataError(f"{rel} 已存在；请先清理后再 start")
    if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], repo=repo).returncode == 0:
        raise ctx.TaskDataError(f"分支 {branch!r} 已存在；请先处理后再 start")
    r = _git(["worktree", "add", "-b", branch, str(path), base_sha], repo=repo)
    if r.returncode != 0:
        raise ctx.TaskDataError(f"git worktree add 失败：{r.stderr.strip()}")
    return rel

def rollback_start(
    *,
    base_sha: str,
    branch: str,
    worktree_rel: str,
    repo: "ctx.RepoContext",
) -> str | None:
    """补偿尚未成功返回的 start；不修改主仓 HEAD 或工作区。

    只清理本次调用可确认归属的资源：worktree 登记分支须等于本次 branch，
    分支 HEAD 须仍等于 base_sha。并发 start 撞车时，他方创建的资源不满足
    上述条件，原样保留并报告，由用户裁决。
    """
    failures = []
    raw = repo.primary_root / worktree_rel
    worktree = raw.resolve()
    registered_branch = worktree_paths(repo=repo).get(str(worktree))
    if registered_branch == branch:
        if os.path.lexists(raw):
            unlink_managed_links(worktree, repo=repo)
        r = _git(["worktree", "remove", "--force", str(worktree)], repo=repo)
        if r.returncode != 0:
            failures.append(
                f"强制移除 worktree {worktree_rel} 失败：{r.stderr.strip()}"
            )
    elif registered_branch:
        failures.append(
            f"worktree {worktree_rel} 登记分支为 {registered_branch!r}，"
            f"非本次分支 {branch!r}；可能与其他 start 并发冲突，未移除"
        )
    elif os.path.lexists(raw):
        failures.append(f"路径 {worktree_rel} 存在但未登记本次分支，未自动删除未知内容")
    _git(["worktree", "prune"], repo=repo)

    branch_ref = f"refs/heads/{branch}"
    if _git(["rev-parse", "--verify", "--quiet", branch_ref], repo=repo).returncode == 0:
        r = _git(["rev-parse", branch_ref], repo=repo)
        branch_head = r.stdout.strip() if r.returncode == 0 else ""
        if branch_head != base_sha:
            failures.append(
                f"分支 {branch!r} 已从 base 前进到 {branch_head or 'unknown'}，未自动删除"
            )
        else:
            r = _git(["branch", "-D", branch], repo=repo)
            if r.returncode != 0:
                failures.append(f"删除分支 {branch!r} 失败：{r.stderr.strip()}")

    if os.path.lexists(raw):
        failures.append(f"路径 {worktree_rel} 仍存在，未自动删除未知内容")
    return "; ".join(failures) or None

def _prune_unregistered(
    path: Path, rel: str, *, repo: "ctx.RepoContext",
) -> tuple[bool, str]:
    """未登记 worktree：prune 后必须确认路径确实消失，否则报告孤儿目录。"""
    prune = _git(["worktree", "prune"], repo=repo)
    if prune.returncode != 0:
        return (
            False,
            f"git worktree prune 失败（{prune.stderr.strip()}）；"
            f"无法确认 {rel} 清理状态",
        )
    if os.path.lexists(path) or os.path.lexists(repo.primary_root / rel):
        return (
            False,
            f"git worktree prune 后 {rel} 路径仍存在；该 worktree 未登记且无分支归属，"
            f"拒绝静默返回成功，请手动清理 {rel}",
        )
    return True, f"worktree 不在登记表且路径已不存在，已 prune：{rel}"

def discard_worktree(
    rel: str, *, repo: "ctx.RepoContext", tid: str | None = None,
) -> tuple[bool, str]:
    """显式撤回时强制丢弃 task worktree；调用方必须先取得用户确认。

    ``tid`` 给定时做归属校验：``rel`` 必须恰等于 ``repo.worktree_rel(tid)``
    派生路径，且登记分支的 tid 前缀须一致；不符直接拒绝。破坏性入口
    （rewind/drop）必须传 ``tid``，否则同源篡改的 front matter 可借此删除
    其他 task 的 worktree 与分支。
    """
    path = (repo.primary_root / rel).resolve()
    registered = worktree_paths(repo=repo).get(str(path))
    if registered is None:
        return _prune_unregistered(path, rel, repo=repo)
    refusal = _check_worktree_ownership(rel, registered, tid, repo=repo)
    if refusal:
        return False, refusal
    if Path.cwd().resolve().is_relative_to(path):
        return False, f"当前目录在 {rel} 内，无法移除；请 cd 回主仓"
    unlink_managed_links(path, repo=repo)
    r = _git(["worktree", "remove", "--force", str(path)], repo=repo)
    if r.returncode != 0:
        return False, f"git worktree remove --force 失败（{r.stderr.strip()}）"
    prune = _git(["worktree", "prune"], repo=repo)
    if prune.returncode != 0:
        return (
            False,
            f"worktree 已移除但 prune 失败（{prune.stderr.strip()}）；请手动 prune {rel}",
        )
    return True, f"worktree 已强制移除：{rel}"

def remove_worktree(
    rel: str, *, expected_branch: str | None = None,
    repo: "ctx.RepoContext", tid: str | None = None,
) -> tuple[bool, str]:
    """返回 (是否已确实移除, 说明)。失败不代表流程必须中断，由调用方决定。

    expected_branch 非空时校验登记分支归属，防止 front matter 损坏/冲突解决
    错误导致一个 task 清理另一个 task 的工作区。expected_branch 本身可与 rel
    同源伪造：``tid`` 给定时额外校验派生路径与分支 tid 前缀，不符直接拒绝。
    """
    if not rel:
        return True, "无 worktree"
    path = (repo.primary_root / rel).resolve()
    registered_branch = worktree_paths(repo=repo).get(str(path))
    if registered_branch is None:
        return _prune_unregistered(path, rel, repo=repo)
    refusal = _check_worktree_ownership(rel, registered_branch, tid, repo=repo)
    if refusal:
        return False, refusal
    if expected_branch is not None:
        if tid is not None:
            forged = ctx.TASK_BRANCH_RE.fullmatch(expected_branch)
            if not forged or forged.group(1) != tid:
                return False, (
                    f"预期分支 {expected_branch!r} 与 {tid} 不符"
                    "（须为该 task 自身分支）；拒绝移除其他 task 的 worktree"
                )
        if registered_branch != expected_branch:
            return False, (
                f"{rel} 登记分支为 {registered_branch!r}，预期 {expected_branch!r}；"
                "拒绝移除其他 task 的 worktree"
            )
    if Path.cwd().resolve().is_relative_to(path):
        return False, f"当前目录在 {rel} 内，无法移除；请 cd 出去后执行 git worktree remove {rel}"
    unlink_managed_links(path, repo=repo)
    r = _git(["worktree", "remove", str(path)], repo=repo)
    if r.returncode != 0:
        return False, f"git worktree remove 失败（{r.stderr.strip()}）；请手动处理 {rel}"
    prune = _git(["worktree", "prune"], repo=repo)
    if prune.returncode != 0:
        return (
            False,
            f"worktree 已移除但 prune 失败（{prune.stderr.strip()}）；请手动 prune {rel}",
        )
    return True, f"worktree 已移除：{rel}"
