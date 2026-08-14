"""Repository observation, handoff verification, and attempt-based monitoring."""

import hashlib
import json
import os
import stat
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
from .documents import extract_ac_ids
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


# 被审 diff 指纹排除的任务流程文件：check_review_status / render_review_prompts 共用（单一真相源）。
SCOPE_FINGERPRINT_EXCLUDES = (
    ":(exclude)docs/pending", ":(exclude)docs/findings",
    ":(exclude)docs/archive", ":(exclude)docs/tasks_index.json",
    ":(exclude)docs/archive/tasks_index.json", ":(exclude)docs/spikes",
    ":(exclude).scratch",
)


def review_scope_fingerprint(
    diff_anchor: str, rel_task_dir: str, *, repo_root: Path | None = None
) -> str:
    """被审 diff 指纹：`git diff {diff_anchor}` 排除 task 流程文件后的内容摘要。

    未跟踪文件（未 `git add -N` 的新源码）对 `git diff <anchor>` 不可见，会静默绕过
    review 门禁；把 `git ls-files --others` 内容一并纳入哈希，与 repository_fingerprint
    口径一致（RT-002）。git 失败返回 ""。check_review_status / render_review_prompts
    共用本实现，防口径漂移。
    """
    root = (repo_root or ctx.REPO_ROOT).resolve()
    excludes = [
        f":(exclude){rel_task_dir}/task.md",
        f":(exclude){rel_task_dir}/review_code.md",
        f":(exclude){rel_task_dir}/review_test.md",
        f":(exclude){rel_task_dir}/review_general.md",
        f":(exclude){rel_task_dir}/handoff.json",
        *SCOPE_FINGERPRINT_EXCLUDES,
    ]
    try:
        diff = _git_bytes(["diff", "--binary", diff_anchor, "--", ".", *excludes], root=root)
        untracked = _git_bytes(
            ["ls-files", "--others", "--exclude-standard", "-z", "--", ".", *excludes],
            root=root,
        )
    except (ctx.TaskDataError, OSError):
        return ""
    if diff.returncode != 0 or untracked.returncode != 0:
        return ""
    digest = hashlib.sha1()
    digest.update(diff.stdout)
    for raw in untracked.stdout.split(b"\0"):
        if not raw:
            continue
        digest.update(b"U" + raw)
        try:
            digest.update((root / os.fsdecode(raw)).read_bytes())
        except OSError:
            pass
    return digest.hexdigest()[:16]


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
    "ac_evidence": dict,
    "pending": list,
    "findings": list,
}


def _validate_string_list(name: str, value) -> str | None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return f"{name} 必须是字符串数组"
    return None


def verify_integrate_ready(
    tid: str,
    attempt: int,
    execution_id: str,
) -> tuple[str, str]:
    """Verify one execution commit and exact handoff provenance."""
    if (
        not isinstance(attempt, int)
        or isinstance(attempt, bool)
        or attempt <= 0
        or not isinstance(execution_id, str)
        or not execution_id
    ):
        return "contract", "verify 必须提供正整数 attempt 与非空 execution_id"
    branches = _task_branch_names(tid)
    if not branches:
        return "incomplete", "无本地 task 分支"
    if len(branches) > 1:
        return "contract", f"存在多个分支：{', '.join(branches)}"
    branch = branches[0]
    try:
        task, fm, _ = load_task_at_ref(tid, branch)
    except ctx.TaskDataError as error:
        return "incomplete", str(error)
    status = fm.get("status")
    if status not in ctx.ARCHIVED_STATUSES:
        return "incomplete", f"分支 {branch!r} tip status={status!r} 非终态"
    handoff_path = f"{task['dir']}/handoff.json"
    try:
        handoff = json.loads(git_text_at_ref(branch, handoff_path))
    except ctx.TaskDataError:
        return "contract", f"分支 {branch!r} tip 缺 {handoff_path}"
    except json.JSONDecodeError:
        return "contract", f"分支 {branch!r} tip {handoff_path} 无法解析"
    if not isinstance(handoff, dict):
        return "contract", f"{handoff_path} 非 JSON 对象"
    for key, expected in _HANDOFF_TYPES.items():
        if key not in handoff:
            return "contract", f"{handoff_path} 缺必填字段 {key}"
        if (
            not isinstance(handoff[key], expected)
            or (key == "attempt" and isinstance(handoff[key], bool))
            or (key == "attempt" and handoff[key] <= 0)
            or (expected is str and not handoff[key])
        ):
            return "contract", f"{handoff_path} 字段 {key} 类型或值非法"
    for key in ("pending", "findings"):
        problem = _validate_string_list(key, handoff[key])
        if problem:
            return "contract", f"{handoff_path} {problem}"
    ac_evidence = handoff["ac_evidence"]
    for ac_id, refs in ac_evidence.items():
        if not refs or not isinstance(refs, list) or any(
            not isinstance(item, str) or not item for item in refs
        ):
            return "contract", f"{handoff_path} ac_evidence[{ac_id!r}] 值必须是非空字符串数组"
    spec_path = f"{task['dir']}/spec.md"
    try:
        expected_ac = extract_ac_ids(git_text_at_ref(branch, spec_path))
    except ctx.TaskDataError:
        return "contract", f"分支 {branch!r} tip 缺 {spec_path}"
    got, want = set(ac_evidence), set(expected_ac)
    if got != want:
        missing = sorted(want - got)
        extra = sorted(got - want)
        detail = ("缺 " + ", ".join(missing)) if missing else ""
        if extra:
            detail += ("；" if detail else "") + "未知 " + ", ".join(extra)
        return "contract", f"{handoff_path} ac_evidence 与 spec 验收标准 AC 不匹配：{detail}"
    expected_values = {
        "tid": tid,
        "attempt": attempt,
        "execution_id": execution_id,
        "status": status,
        "branch": branch,
    }
    for key, expected in expected_values.items():
        if handoff.get(key) != expected:
            return "contract", f"{handoff_path} {key}={handoff.get(key)!r} 与当前 {expected!r} 不符"
    tip_result = _git(["rev-parse", f"refs/heads/{branch}^{{commit}}"])
    parent_result = _git(["rev-parse", f"refs/heads/{branch}^1"])
    base_result = _git(["rev-parse", f"{handoff['base_sha']}^{{commit}}"])
    if tip_result.returncode != 0 or parent_result.returncode != 0:
        return "contract", f"分支 {branch!r} tip 或 first parent 无法解析 commit"
    if base_result.returncode != 0:
        return "contract", f"{handoff_path} base_sha 无法解析 commit"
    first_parent = parent_result.stdout.strip()
    diff_anchor = fm.get("diff_anchor")
    if not isinstance(diff_anchor, str) or not diff_anchor:
        return "contract", f"分支 {branch!r} task diff_anchor 缺失或非法"
    if (
        base_result.stdout.strip() != first_parent
        or handoff["base_sha"] != first_parent
        or diff_anchor != first_parent
    ):
        return "contract", (
            f"{handoff_path} base_sha={handoff['base_sha']!r}、task diff_anchor="
            f"{diff_anchor!r} 与 branch tip first parent {first_parent!r} 不一致；"
            "一个 task 必须恰有一个执行 commit"
        )
    detail = "分支 tip terminal + exact handoff + diff_anchor/first-parent provenance"
    if not has_unmerged_commits(branch):
        detail += "（已合入）"
    return "ready", detail


def compute_ps_rows(
    events: list[dict],
    effective: dict[str, dict],
    main_statuses: dict[str, str],
    *,
    verifier=verify_integrate_ready,
) -> list[dict]:
    records = project_attempts(events)
    ledger_tids = {record["tid"] for record in records.values()}
    # 合并 effective 中 active/blocked 的 tid：frontmatter 被手工改状态但未走
    # reserve 的脏状态也要可见，避免状态不一致被掩盖（无 reserve 标注）。
    active_tids = {
        tid for tid, task in effective.items()
        if task.get("status") in ("active", "blocked")
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
            # frontmatter active/blocked 但无 attempt：脏状态，标注无 reserve。
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
            verdict, detail = verifier(
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
