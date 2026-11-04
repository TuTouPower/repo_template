"""Repository observation, handoff verification, and attempt-based monitoring."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path

import repo_task.context as ctx

from .attempts import (
    attempt_for_identity,
    attempts_for_tid,
    current_attempt_record,
    in_flight_attempts,
    overlapping_attempts,
    project_attempts,
)
from .documents import (
    _extract_markdown_section,
    _visible_markdown_lines,
    extract_ac_ids,
    extract_ac_statuses,
    parse_front_matter_text,
)
from .git_ops import _git, _git_bytes, has_unmerged_commits, worktree_paths
from .store import _task_branch_names, git_text_at_ref, load_task_at_ref


def _ledger_tid_sort_key(tid: str):
    match = ctx.TID_RE.fullmatch(tid or "")
    return (0, int(match.group(1)), "") if match else (1, 0, tid or "")


def _hash_part(digest, label: bytes, value: bytes) -> None:
    digest.update(len(label).to_bytes(4, "big"))
    digest.update(label)
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


# review prompt 渲染输出文件名（按 review_level）：render_review_prompts 的输出 dict key
# 同源于此（单一真相源）。产物经 --out-dir 落 task 目录后是 untracked 文件，若不入指纹
# 排除列表会参与重算 → check_review_status 误判 review_scope=stale；本常量防两处漂移。
REVIEW_PROMPT_OUTPUT_FILES = {
    "full": ("code_review_prompt.md", "test_review_prompt.md"),
    "single": ("general_review_prompt.md",),
}
# task 目录级 review 流程文件（review 门禁指纹排除全量）：过程文件 + prompt 渲染产物。
REVIEW_PROCESS_FILES = (
    ctx.PROGRESS_FILE,
    "review_code.md",
    "review_test.md",
    "review_general.md",
    "handoff.json",
) + tuple(name for names in REVIEW_PROMPT_OUTPUT_FILES.values() for name in names)


def implementation_commit(ref: str, base_sha: str) -> str:
    """校验执行分支形状：base_sha..ref 恰好一个非 merge 实现 commit，first parent 等于 base_sha。"""
    resolved = []
    for name, value in (("ref", ref), ("base", base_sha)):
        result = _git(["rev-parse", "--verify", "--quiet", f"{value}^{{commit}}"])
        if result.returncode != 0 or not result.stdout.strip():
            raise ctx.TaskDataError(f"{name} {value!r} 无法解析为 commit")
        resolved.append(result.stdout.strip())
    tip, base = resolved
    listing = _git(["rev-list", "--parents", f"{base}..{tip}"])
    if listing.returncode != 0:
        raise ctx.TaskDataError(f"无法列出 {base[:12]}..{tip[:12]} 的提交：{listing.stderr.strip()}")
    commits = [line.split() for line in listing.stdout.splitlines() if line.strip()]
    if len(commits) != 1:
        raise ctx.TaskDataError(
            f"执行分支必须恰好一个实现 commit：{base[:12]}..{tip[:12]} 有 {len(commits)} 个 commit"
        )
    sha, parents = commits[0][0], commits[0][1:]
    if len(parents) != 1 or parents[0] != base:
        raise ctx.TaskDataError(
            f"执行分支必须恰好一个实现 commit：{sha[:12]} 必须是单 parent 且 first parent 等于 {base[:12]}"
        )
    return sha


# 仓库级 review 门禁指纹排除（非 task 目录路径）。
SCOPE_FINGERPRINT_EXCLUDES = (
    ":(exclude)docs/pending", ":(exclude)docs/findings",
    ":(exclude)docs/archive", ":(exclude)docs/tasks_index.json",
    ":(exclude)docs/archive/tasks_index.json", ":(exclude)docs/spikes",
    ":(exclude).scratch",
)


def _review_scope_path(path: str, task_name: str) -> str | None:
    # 当前 task 目录在执行分支内始终位于 docs/tasks/（归档发生在 integrate 的 merge
    # commit，之后不再对该 task 计算指纹）：过程文件排除，spec/附件保留。
    prefix = f"docs/tasks/{task_name}/"
    if path.startswith(prefix):
        return None if path[len(prefix):] in REVIEW_PROCESS_FILES else path
    excluded = [item.removeprefix(":(exclude)") for item in SCOPE_FINGERPRINT_EXCLUDES]
    if any(path == item or path.startswith(item + "/") for item in excluded):
        return None
    return path


def _review_tree(root: Path, ref: str, task_name: str) -> dict[str, tuple[str, str]]:
    data = _required_git_bytes(["ls-tree", "-r", "-z", ref], root)
    tree = {}
    for entry in data.split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, _, oid = metadata.decode("ascii").split()
        path = _review_scope_path(os.fsdecode(raw_path), task_name)
        if path is not None:
            tree[path] = (mode, oid)
    return tree


_REVIEW_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB 单文件上限（Issue #50）


def _review_worktree(root: Path, task_name: str) -> dict[str, tuple[str, str]]:
    data = _required_git_bytes(["ls-files", "--cached", "--others", "--exclude-standard", "-z"], root)
    algorithm = _required_git_bytes(["rev-parse", "--show-object-format"], root).decode().strip()
    tree = {}
    for raw in set(data.split(b"\0")) - {b""}:
        physical = os.fsdecode(raw)
        path = _review_scope_path(physical, task_name)
        if path is None:
            continue
        source = root / physical
        try:
            mode = source.lstat().st_mode
        except FileNotFoundError:
            continue  # tracked deletion
        if stat.S_ISLNK(mode):
            content, git_mode = os.fsencode(os.readlink(source)), "120000"
            blob = f"blob {len(content)}\0".encode() + content
            tree[path] = (git_mode, hashlib.new(algorithm, blob).hexdigest())
        elif stat.S_ISREG(mode):
            git_mode = "100755" if mode & 0o111 else "100644"
            size = source.stat().st_size
            if size > _REVIEW_MAX_FILE_SIZE:
                raise ctx.TaskDataError(
                    f"review scope 文件过大（{physical} 达到 {size // (1024 * 1024)}MB，上限 50MB）；"
                    "请将其放入 .gitignore 或划入排除目录"
                )
            h = hashlib.new(algorithm)
            h.update(f"blob {size}\0".encode())
            with open(source, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            tree[path] = (git_mode, h.hexdigest())
        elif stat.S_ISDIR(mode):
            # Gitlinks are opaque dependencies. Bind their checked-out commit.
            git_mode = "160000"
            oid = _required_git_bytes(["rev-parse", "HEAD"], source).decode().strip()
            tree[path] = (git_mode, oid)
        else:
            raise ctx.TaskDataError(f"review scope 不支持文件类型：{physical}")
    return tree


def review_scope_fingerprint(
    diff_anchor: str, rel_task_dir: str, *, repo_root: Path | None = None,
    ref: str | None = None,
) -> str:
    """Hash the delivered content delta, independent of staging/commit.

    ref=None reads the complete worktree (including untracked outputs); ref reads
    only immutable committed blobs. A deleted file, mode or symlink change is part
    of the scope. Process files are excluded, never the current task's spec.
    Old diff-serialization fingerprints deliberately require a fresh review.
    """
    root = (repo_root or ctx.REPO_ROOT).resolve()
    task_name = Path(rel_task_dir).name
    try:
        before = _review_tree(root, diff_anchor, task_name)
        after = _review_tree(root, ref, task_name) if ref else _review_worktree(root, task_name)
        digest = hashlib.sha256(b"repo-task-review-scope-v2\0")
        task_path = f"docs/tasks/{task_name}/{ctx.PROGRESS_FILE}"
        review_level = "full"
        text = None
        if ref:
            result = _git(["show", f"{ref}:{task_path}"], root=root)
            if result.returncode == 0:
                text = result.stdout
        elif (root / task_path).exists():
            text = (root / task_path).read_text(encoding="utf-8")
        if text is not None:
            fm, _ = parse_front_matter_text(text, source=task_path)
            review_level = fm.get("review_level", "full")
            if review_level not in ctx.REVIEW_LEVELS:
                raise ctx.TaskDataError(f"review_level 非法：{review_level!r}")
        _hash_part(digest, b"review_level", review_level.encode())
        for path in sorted(before.keys() | after.keys()):
            if before.get(path) == after.get(path):
                continue
            _hash_part(digest, b"path", os.fsencode(path))
            for label, tree in ((b"before", before), (b"after", after)):
                value = " ".join(tree[path]).encode() if path in tree else b"deleted"
                _hash_part(digest, label, value)
        return digest.hexdigest()[:16]
    except (ctx.TaskDataError, OSError, ValueError):
        return ""  # fail closed; an unreadable output is never silently omitted


def _required_git_bytes(args: list[str], root: Path) -> bytes:
    result = _git_bytes(args, root=root)
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise ctx.TaskDataError(f"无法计算仓库状态指纹：git {' '.join(args)}：{error}")
    return result.stdout


_FINGERPRINT_LARGE_FILE_LIMIT = 1024 * 1024  # 1 MB
_FINGERPRINT_LARGE_FILE_PREFIX = 8192  # 大文件只读前 8 KB


def repository_fingerprint(root: Path) -> dict:
    """Hash HEAD, binary diffs, and sorted non-ignored untracked entries.

    超过 1 MB 的 untracked 常规文件不全量读入，只哈希 (size, mtime_ns,
    前 8 KB 内容)，避免 observe 被大文件拖慢；静默检测对超大文件变化
    的精度随之降级（仅内容前缀变化可感知）。

    仅供观测，不是 review 证据门禁；review scope 使用
    review_scope_fingerprint 对交付文件全量哈希。
    """
    root = root.resolve()
    head = _required_git_bytes(["rev-parse", "HEAD"], root).strip()
    staged = _required_git_bytes(
        ["diff", "--binary", "--cached", "--no-ext-diff", "--full-index", "--"], root
    )
    unstaged = _required_git_bytes(
        ["diff", "--binary", "--no-ext-diff", "--full-index", "--"], root
    )
    untracked_raw = _required_git_bytes(
        ["ls-files", "--others", "--exclude-standard", "-z"], root
    )
    untracked = sorted(path for path in untracked_raw.split(b"\0") if path)
    digest = hashlib.sha256()
    _hash_part(digest, b"head", head)
    _hash_part(digest, b"staged", staged)
    _hash_part(digest, b"unstaged", unstaged)
    for raw_path in untracked:
        path = root / os.fsdecode(raw_path)
        try:
            info = path.lstat()
        except FileNotFoundError:
            raise ctx.TaskDataError(f"计算仓库状态指纹时文件消失：{os.fsdecode(raw_path)}")
        file_type = stat.S_IFMT(info.st_mode)
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISLNK(info.st_mode):
            kind = b"symlink"
            content = os.fsencode(os.readlink(path))
        elif stat.S_ISREG(info.st_mode):
            kind = b"file"
            if info.st_size > _FINGERPRINT_LARGE_FILE_LIMIT:
                # 大文件：只记 size + mtime_ns + 前 8 KB，不读全量。
                with path.open("rb") as fh:
                    prefix = fh.read(_FINGERPRINT_LARGE_FILE_PREFIX)
                content = (
                    f"large:{info.st_size}:{info.st_mtime_ns}:".encode("ascii") + prefix
                )
            else:
                try:
                    content = path.read_bytes()
                except OSError:
                    # lstat 后 read 前文件被并发删除/截断：视为消失，避免裸抛（F38）
                    raise ctx.TaskDataError(
                        f"计算仓库状态指纹时文件消失：{os.fsdecode(raw_path)}"
                    ) from None
        else:
            kind = f"special:{file_type:o}".encode("ascii")
            content = b""
        _hash_part(digest, b"path", raw_path)
        _hash_part(digest, b"kind", kind)
        _hash_part(digest, b"mode", f"{mode:o}".encode("ascii"))
        _hash_part(digest, b"content", content)
    return {
        "fingerprint": digest.hexdigest(),
        "head": head.decode("ascii", errors="replace"),
        "untracked_count": len(untracked),
    }


def worktree_dirty_summary(root: Path) -> str:
    result = _git_bytes(["status", "--porcelain=v1", "-z"], root=root)
    if result.returncode != 0:
        return "status-unavailable"
    staged = unstaged = untracked = 0
    fields = result.stdout.split(b"\0")
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if not entry:
            continue
        code = entry[:2]
        if code == b"??":
            untracked += 1
        else:
            if code[:1] not in (b" ", b"?"):
                staged += 1
            if code[1:2] not in (b" ", b"?"):
                unstaged += 1
        if code[:1] in (b"R", b"C"):
            index += 1
    if not any((staged, unstaged, untracked)):
        return "clean"
    return f"staged={staged} unstaged={unstaged} untracked={untracked}"
_HANDOFF_TYPES = {
    "tid": str,
    "attempt": int,
    "execution_id": str,
    "status": str,
    "branch": str,
    "base_sha": str,
    "tests": str,
    "blackbox": str,
    "review": str,
    "review_scope": str,
    "pending": list,
    "findings": list,
}


# progress.md「收尾报告 / 验收与验证」中的模板占位；handoff 的 tests/blackbox/review 不得是这些值
HANDOFF_PLACEHOLDERS = ("待执行时填写",)
_SUMMARY_LINES = (("tests", "测试"), ("blackbox", "黑盒"), ("review", "review"))
_PENDING_PATH_RE = re.compile(
    r"^docs/(?:pending/(?:todo|parked)|archive/pending)/(p[0-9]+)_[^/]+\.md$"
)
_FINDING_PATH_RE = re.compile(r"^docs/findings/(d[0-9]+)_[^/]+\.md$")


def read_verification_summary(note_body: str) -> dict[str, str]:
    """读取 progress.md「收尾报告 / 验收与验证」的测试、黑盒、review 三行；空值或模板占位拒绝。"""
    report = _extract_markdown_section(note_body, 2, "收尾报告")
    section = _extract_markdown_section(report, 3, "验收与验证") if report is not None else None
    if section is None:
        raise ctx.TaskDataError(f"{ctx.PROGRESS_FILE} 缺「收尾报告 / 验收与验证」小节，无法生成 handoff.json")
    lines = _visible_markdown_lines(section)
    summary = {}
    for key, label in _SUMMARY_LINES:
        pattern = re.compile(rf"^ {{0,3}}-[ \t]+{label}[ \t]*[:：][ \t]*(.*?)[ \t]*$")
        values = [match.group(1) for match in map(pattern.match, lines) if match]
        if not values:
            raise ctx.TaskDataError(f"{ctx.PROGRESS_FILE}「收尾报告 / 验收与验证」缺 `- {label}：` 行")
        value = values[0]
        if (
            not value
            or any(placeholder in value for placeholder in HANDOFF_PLACEHOLDERS)
            or ctx.TEMPLATE_PLACEHOLDER_RE.search(value)
        ):
            raise ctx.TaskDataError(
                f"{ctx.PROGRESS_FILE}「收尾报告 / 验收与验证」的 `- {label}：` 为空或仍是模板占位；"
                "写入实际验证结果后再 finish"
            )
        summary[key] = value
    return summary


def staged_ledger_entries(root: Path) -> tuple[list[str], list[str]]:
    """暂存区相对 HEAD 新增（含归档迁入）的 pending / findings 条目编号。"""
    result = _git(["diff", "--cached", "--name-status", "--no-renames", "-z", "HEAD", "--"], root=root)
    if result.returncode != 0:
        raise ctx.TaskDataError(f"无法读取暂存区相对 HEAD 的变更：{result.stderr.strip()}")
    fields = [field for field in result.stdout.split("\0") if field]
    pending, findings = set(), set()
    for status, path in zip(fields[0::2], fields[1::2]):
        if status != "A":
            continue
        for pattern, bucket in ((_PENDING_PATH_RE, pending), (_FINDING_PATH_RE, findings)):
            match = pattern.match(path)
            if match:
                bucket.add(match.group(1))
    return (
        sorted(pending, key=lambda item: int(item[1:])),
        sorted(findings, key=lambda item: int(item[1:])),
    )


def build_handoff(
    *, tid: str, attempt: int, execution_id: str, branch: str, base_sha: str,
    review_scope: str, note_body: str, root: Path,
) -> dict:
    """由 execution context、ledger、暂存区与 progress.md 机器生成 handoff.json；不手写。"""
    summary = read_verification_summary(note_body)
    pending, findings = staged_ledger_entries(root)
    handoff = {
        "tid": tid,
        "attempt": attempt,
        "execution_id": execution_id,
        "status": "done",
        "branch": branch,
        "base_sha": base_sha,
        **summary,
        "review_scope": review_scope,
        "pending": pending,
        "findings": findings,
    }
    for key, expected in _HANDOFF_TYPES.items():
        if not isinstance(handoff.get(key), expected) or (expected is str and not handoff[key]):
            raise ctx.TaskDataError(f"生成的 handoff 字段 {key} 类型或值非法：{handoff.get(key)!r}")
    return handoff


def _validate_string_list(name: str, value) -> str | None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return f"{name} 必须是字符串数组"
    return None


def verify_integrate_ready(
    tid: str,
    attempt: int,
    execution_id: str,
) -> tuple[str, str, str]:
    """Verify one execution commit and exact handoff provenance.

    返回 (verdict, kind, detail)。kind 是机器可判的失败类别，调用方（recovery、
    finish-unwind）按 kind 分流，不得匹配 detail 自然语言：
    ready（通过）、incomplete（条件未齐）、identity（参数非法）、branch（分支异常）、
    handoff（执行回执异常）、spec（验收标准异常）、provenance（血缘异常）、
    review_scope_mismatch（提交内容偏离 review 内容）、review_gate（review 门禁失败）。
    """
    if (
        not isinstance(attempt, int)
        or isinstance(attempt, bool)
        or attempt <= 0
        or not isinstance(execution_id, str)
        or not execution_id
    ):
        return "contract", "identity", "verify 必须提供正整数 attempt 与非空 execution_id"
    branches = _task_branch_names(tid)
    if not branches:
        return "incomplete", "incomplete", "无本地 task 分支"
    if len(branches) > 1:
        return "contract", "branch", f"存在多个分支：{', '.join(branches)}"
    branch = branches[0]
    try:
        task, fm, _ = load_task_at_ref(tid, branch)
    except ctx.TaskDataError as error:
        return "incomplete", "incomplete", str(error)
    status = fm.get("status")
    if status not in ctx.ARCHIVED_STATUSES:
        return "incomplete", "incomplete", f"分支 {branch!r} tip status={status!r} 非终态"
    handoff_path = f"{task['dir']}/handoff.json"
    try:
        handoff = json.loads(git_text_at_ref(branch, handoff_path))
    except ctx.TaskDataError:
        return "contract", "handoff", f"分支 {branch!r} tip 缺 {handoff_path}"
    except json.JSONDecodeError:
        return "contract", "handoff", f"分支 {branch!r} tip {handoff_path} 无法解析"
    if not isinstance(handoff, dict):
        return "contract", "handoff", f"{handoff_path} 非 JSON 对象"
    for key, expected in _HANDOFF_TYPES.items():
        if key not in handoff:
            return "contract", "handoff", f"{handoff_path} 缺必填字段 {key}"
        if (
            not isinstance(handoff[key], expected)
            or (key == "attempt" and isinstance(handoff[key], bool))
            or (key == "attempt" and handoff[key] <= 0)
            or (expected is str and not handoff[key])
        ):
            return "contract", "handoff", f"{handoff_path} 字段 {key} 类型或值非法"
    for key in ("pending", "findings"):
        problem = _validate_string_list(key, handoff[key])
        if problem:
            return "contract", "handoff", f"{handoff_path} {problem}"
    spec_path = f"{task['dir']}/task.md"
    try:
        spec_text = git_text_at_ref(branch, spec_path)
    except ctx.TaskDataError:
        return "contract", "spec", f"分支 {branch!r} tip 缺 {spec_path}"
    ac_statuses = extract_ac_statuses(spec_text)
    if not ac_statuses:
        return "contract", "spec", f"{spec_path} 验收标准未定义任何 AC"
    uncompleted = [ac_id for ac_id, checked in ac_statuses if not checked]
    if uncompleted:
        return "contract", "spec", f"{spec_path} 存在未完成 AC（未打勾）：{', '.join(uncompleted)}"
    expected_values = {
        "tid": tid,
        "attempt": attempt,
        "execution_id": execution_id,
        "status": status,
        "branch": branch,
    }
    for key, expected in expected_values.items():
        if handoff.get(key) != expected:
            return "contract", "handoff", f"{handoff_path} {key}={handoff.get(key)!r} 与当前 {expected!r} 不符"
    tip_result = _git(["rev-parse", f"refs/heads/{branch}^{{commit}}"])
    base_result = _git(["rev-parse", f"{handoff['base_sha']}^{{commit}}"])
    if tip_result.returncode != 0:
        return "contract", "branch", f"分支 {branch!r} tip 无法解析 commit"
    if base_result.returncode != 0:
        return "contract", "handoff", f"{handoff_path} base_sha 无法解析 commit"
    base = base_result.stdout.strip()
    tip = tip_result.stdout.strip()
    diff_anchor = fm.get("diff_anchor")
    if not isinstance(diff_anchor, str) or not diff_anchor:
        return "contract", "provenance", f"分支 {branch!r} task diff_anchor 缺失或非法"
    base_level = "full"
    try:
        base_text = git_text_at_ref(base, f"{task['dir']}/{ctx.PROGRESS_FILE}")
    except ctx.TaskDataError:
        base_text = None
    if base_text is not None:
        base_fm, _ = parse_front_matter_text(base_text, source=f"{base}:{task['dir']}/{ctx.PROGRESS_FILE}")
        base_level = base_fm.get("review_level", "full")
    if base_level not in ctx.REVIEW_LEVELS:
        return "contract", "provenance", f"diff_anchor {base[:12]} 处 review_level 非法：{base_level!r}"
    tip_level = fm.get("review_level", "full")
    if tip_level not in ctx.REVIEW_LEVELS:
        return "contract", "provenance", f"分支 {branch!r} tip review_level 非法：{tip_level!r}"
    if tip_level != base_level and not (base_level == "single" and tip_level == "full"):
        return "contract", "provenance", (
            f"分支 {branch!r} review_level 被降级：{base_level} -> {tip_level}；"
            "review_level 只能保持不变或 single -> full"
        )
    shape = "执行分支必须恰好一个实现 commit"
    try:
        implementation_commit(tip, base)
    except ctx.TaskDataError as error:
        return "contract", "provenance", (
            f"{handoff_path} base_sha={handoff['base_sha']!r} 与分支 {branch!r} tip 不符：{error}；{shape}"
        )
    if handoff["base_sha"] != base or diff_anchor != base:
        return "contract", "provenance", (
            f"{handoff_path} base_sha={handoff['base_sha']!r}、task diff_anchor="
            f"{diff_anchor!r} 与实现 commit 的 first parent {base!r} 不一致；{shape}"
        )
    scope = review_scope_fingerprint(base, task["dir"], ref=tip)
    if not scope:
        return "contract", "provenance", f"无法计算分支 {branch!r} tip 的 review scope；{shape}"
    if handoff["review_scope"] != scope:
        return "contract", "review_scope_mismatch", (
            f"{handoff_path} review_scope={handoff['review_scope']!r} 与分支 {branch!r} tip "
            f"重算的 {scope!r} 不一致；提交内容偏离 review 内容，不能 amend——"
            f"无后继分支且 report 未 done 时用 `task.py finish-unwind {tid}` 撤销实现 commit 回到 active 重做"
        )
    if status == "done":
        from .review import ReviewDataError, evaluate_review
        try:
            with tempfile.TemporaryDirectory(prefix="repo-task-review-") as tmp:
                target = Path(tmp)
                for name in (ctx.PROGRESS_FILE, "task.md", "review_code.md", "review_test.md", "review_general.md"):
                    try:
                        text = git_text_at_ref(branch, f"{task['dir']}/{name}")
                    except ctx.TaskDataError:
                        if name in (ctx.PROGRESS_FILE, "task.md"):
                            raise
                        continue
                    (target / name).write_text(text, encoding="utf-8")
                result = evaluate_review(target, fm, scope)
            if result["overall"] != "PASS":
                return "contract", "review_gate", (
                    f"review gate：overall={result['overall']} "
                    f"review_scope={result['review_scope']} "
                    f"missing_disposition={result['missing_disposition']} "
                    f"ac_review={result['ac_review']} "
                    f"ac_review_detail={result['ac_review_detail']}；"
                    "最终提交须有同内容的 PASS 证据"
                )
        except (ctx.TaskDataError, ReviewDataError, OSError) as error:
            return "contract", "review_gate", f"review gate：{error}"
    detail = "分支 tip terminal + exact handoff + 恰好一个实现 commit + review_scope 一致"
    if not has_unmerged_commits(branch):
        detail += "（已合入）"
    return "ready", "ready", detail


def compute_ps_rows(
    events: list[dict],
    effective: dict[str, dict],
    main_statuses: dict[str, str],
    *,
    verifier=verify_integrate_ready,
) -> list[dict]:
    # verifier 协议与 verify_integrate_ready 同形：(tid, attempt, execution_id) -> (verdict, kind, detail)。
    records = project_attempts(events)
    ledger_tids = {record["tid"] for record in records.values()}
    # 合并 effective 中 active 的 tid：frontmatter 被手工改状态但未走
    # reserve 的脏状态也要可见，避免状态不一致被掩盖（无 reserve 标注）。
    active_tids = {
        tid for tid, task in effective.items()
        if task.get("status") == "active"
    }
    rows = []
    for tid in sorted(ledger_tids | active_tids, key=_ledger_tid_sort_key):
        record = current_attempt_record(tid, events)
        effective_status = effective.get(tid, {}).get("status", "")
        base = {
            "tid": tid,
            "attempt": record["attempt"] if record else None,
            "execution_id": record["execution_id"] if record else "",
            "executor": record["executor"] if record else "",
            "model": record["model"] if record else "",
            "last_activity": "-",
            "note": "",
        }
        if record is None:
            # frontmatter active 但无 attempt：脏状态，标注无 reserve。
            base["state"] = f"{effective_status}(无 reserve)"
            rows.append(base)
            continue
        if main_statuses.get(tid) in ctx.ARCHIVED_STATUSES:
            base["state"] = main_statuses[tid]
            rows.append(base)
            continue
        report = record.get("report")
        if record["state"] == "integrated":
            base["state"] = "done"
        elif record["state"] == "running":
            base["state"] = "running(inline)"
        else:
            verdict, _kind, detail = verifier(
                record["tid"], record["attempt"], record["execution_id"]
            )
            if verdict == "ready":
                base["state"] = "done待合并"
                base["note"] = detail
            elif report and report.get("status") == "blocked":
                base["state"] = "blocked"
                base["note"] = report.get("reason", "")
            elif record["terminal_status"] in ("failed", "stopped") or (
                report and report.get("status") == "failed"
            ):
                fail_class = (report or {}).get("class", "task")
                base["state"] = f"failed:{fail_class}"
                base["note"] = (report or {}).get("reason", "")
            else:
                base["state"] = f"terminal:{record['terminal_status']}"
                base["note"] = detail if verdict == "contract" else ""
        if record["attempt"] in overlapping_attempts(tid, events):
            base["state"] = "contract:overlap"
        rows.append(base)
    return rows
