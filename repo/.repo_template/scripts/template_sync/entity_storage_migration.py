"""entity-storage 一次性实体存储与冷文稿归档收敛迁移。

把 Tasks、Pending、Spikes、审计日志与交接历史从旧的多目录/归档布局
一次性迁移到固定路径契约：
- docs/archive/tasks/tNNN_slug/ -> docs/tasks/tNNN_slug/ (需终态证据 done/dropped)
- docs/tasks/tNNN_slug/         -> 保留原路径并校验身份
- docs/pending/todo/pNNN.md     -> docs/pending/pNNN.md (status: todo)
- docs/pending/parked/pNNN.md   -> docs/pending/pNNN.md (status: parked, 保留 reason)
- docs/archive/pending/pNNN.md  -> docs/pending/pNNN.md (status: closed, 保留 resolution 与 reason)
- docs/archive/spikes/sNNN/     -> docs/spikes/sNNN/ (status: completed, 校验闭环证据与 findings 关联)
- docs/spikes/sNNN/             -> 保持原路径并校验身份
- docs/archive/tasks_audit.log  -> docs/runtime/tasks_audit.log (保持原始字节)
- docs/archive/handoff.md       -> 确认进入 Git 历史后移除
- 更新维护文档中的确定引用并清理空旧目录。

默认 dry-run；加 --write 在 FileTransaction 内执行，失败完整回滚。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pending import (
    ALLOWED_FM_KEYS as PENDING_ALLOWED_FM_KEYS,
    ENTRY_FILE_RE as PENDING_FILE_RE,
    ID_RE as PENDING_ID_RE,
    VALID_RESOLUTIONS as PENDING_VALID_RESOLUTIONS,
    VALID_STATUSES as PENDING_VALID_STATUSES,
    dump_pending_front_matter,
    validate_pending_front_matter,
)
from repo_task.context import (
    PROGRESS_FILE,
    TaskDataError,
    TID_RE,
    VALID_STATUSES as TASK_VALID_STATUSES,
)
from repo_task.documents import (
    atomic_write_text,
    dump_front_matter,
    parse_front_matter_text,
)
from repo_task.file_transaction import FileTransaction
from repo_task.locks import CONTROL_LOCK_NAME, git_common_lock
from repo_task.spikes import (
    ENTRY_DIR_RE as SPIKE_DIR_RE,
    SPIKE_SID_RE,
    validate_spike_metadata,
)

from . import context as ctx

_IGNORED_NAMES = frozenset({".DS_Store", ".gitkeep", ".git"})
_CLEANABLE_NAMES = frozenset({".DS_Store", ".gitkeep"})
_STALE_INDEX_RELS = ("docs/tasks_index.json", "docs/archive/tasks_index.json")
TASK_DIR_RE = re.compile(r"^(t\d{3,})_([a-z0-9_]+)$")


class MigrationError(ctx.SyncError):
    """实体存储迁移阻断异常。"""


def _render_front_matter(fm: dict, body: str) -> str:
    """统一 Front Matter 序列化：YAML 双引号转义 + 正文保真。

    消除按字段白名单重建与 f-string 拼接——含引号/反斜杠的 reason 不再写出非法 YAML，
    未知字段由 validate_* 校验阻断而非静默丢弃。
    """
    clean = body.lstrip("\r\n").rstrip()
    return dump_front_matter(fm) + "\n" + (clean + "\n" if clean else "")


def _validate_spike_fm(fm: dict, sid: str, slug: str, rep: Path, sync: ctx.SyncContext) -> None:
    try:
        validate_spike_metadata(fm, expected_sid=sid, expected_slug=slug)
    except (ValueError, TaskDataError) as err:
        raise MigrationError(f"{sync.rel(rep)}: spike Front Matter 非法：{err}") from err


def _reject_symlink_entities(consumer: Path, sync: ctx.SyncContext) -> None:
    """迁移前对每个实体目录 lstat：拒绝任何软链，防沿软链写/删仓外文件。"""
    roots = (
        "docs/tasks", "docs/archive/tasks",
        "docs/pending", "docs/pending/todo", "docs/pending/parked", "docs/archive/pending",
        "docs/spikes", "docs/archive/spikes",
    )
    for rel in roots:
        parent = consumer / rel
        if parent.is_symlink():
            raise MigrationError(f"{rel} 是软链目录，拒绝沿软链迁移实体")
        if not parent.is_dir():
            continue
        for child in parent.iterdir():
            if child.is_symlink():
                raise MigrationError(
                    f"{sync.rel(child)} 是软链，拒绝沿软链迁移实体（仓外写入风险）"
                )


def _assert_within_consumer(path: Path, sync: ctx.SyncContext) -> None:
    if not sync.contains(path):
        raise MigrationError(f"{sync.rel(path)} 解析到消费仓外，拒绝写入")


@dataclass
class PendingItem:
    pid: str
    slug: str
    source_path: Path
    target_path: Path
    status: str
    resolution: str | None = None
    reason: str | None = None
    fix_ref: str | None = None
    body: str = ""


@dataclass
class SpikeItem:
    sid: str
    slug: str
    source_dir: Path
    target_dir: Path
    status: str
    report_content: str = ""


@dataclass
class TaskItem:
    tid: str
    slug: str
    source_dir: Path
    target_dir: Path
    status: str


@dataclass
class MigrationPlan:
    tasks: list[TaskItem]
    pendings: list[PendingItem]
    spikes: list[SpikeItem]
    audit_log_move: tuple[Path, Path] | None
    handoff_archive_remove: Path | None
    reference_updates: list[tuple[Path, str, str]]
    dirs_to_clean: list[Path]
    stale_index_paths: list[Path]


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def _check_clean_working_copy(consumer: Path) -> None:
    """检查工作区是否干净（staged 和 unstaged 均无修改）。"""
    res = _git(consumer, "status", "--porcelain")
    if res.returncode != 0:
        raise MigrationError(f"检查工作区状态失败：{res.stderr.strip()}")
    dirty = []
    for line in res.stdout.splitlines():
        if not line:
            continue
        if line.startswith("??"):
            continue
        dirty.append(line)
    if dirty:
        details = "\n".join(f"- {d}" for d in dirty[:10])
        raise MigrationError(f"迁移前工作区必须干净（存在已暂存或未暂存修改）：\n{details}")


def entity_storage_migration_status(sync: ctx.SyncContext) -> list[str]:
    """检测消费仓是否仍存在旧实体存储布局，返回待迁移项描述列表。"""
    consumer = sync.consumer
    problems: list[str] = []

    # 1. 旧 task 归档目录
    archived_tasks_dir = consumer / "docs/archive/tasks"
    if archived_tasks_dir.is_dir():
        for item in sorted(archived_tasks_dir.iterdir()):
            if item.name in _IGNORED_NAMES:
                continue
            if not item.is_dir() or not TASK_DIR_RE.match(item.name):
                problems.append(f"{sync.rel(item)}: docs/archive/tasks 存在非规范任务项或未知散落文件（阻断）")
        children = [p for p in archived_tasks_dir.iterdir() if p.is_dir() and TASK_DIR_RE.match(p.name)]
        if children:
            problems.append(f"docs/archive/tasks 存在 {len(children)} 个归档任务目录")

    # 2. 旧 pending 子目录
    for sub_rel in ("docs/pending/todo", "docs/pending/parked", "docs/archive/pending"):
        d = consumer / sub_rel
        if d.is_dir():
            valid_files = []
            for item in sorted(d.iterdir()):
                if item.name in _IGNORED_NAMES:
                    continue
                if not item.is_file() or not PENDING_FILE_RE.match(item.name):
                    problems.append(f"{sync.rel(item)}: {sub_rel} 存在非规范待办项或未知散落文件（阻断）")
                else:
                    valid_files.append(item)
            if valid_files:
                problems.append(f"{sub_rel} 存在 {len(valid_files)} 个待办文件")

    # 3. 检查扁平 docs/pending 下的文件是否都具备合格的 front matter
    pending_root = consumer / "docs/pending"
    if pending_root.is_dir():
        for p in pending_root.glob("p*.md"):
            if not p.is_file() or not PENDING_FILE_RE.match(p.name):
                continue
            try:
                fm, _ = parse_front_matter_text(p.read_text(encoding="utf-8"), source=sync.rel(p))
                validate_pending_front_matter(fm, filename=p.name, source=sync.rel(p))
            except (TaskDataError, ValueError, KeyError) as err:
                problems.append(f"{sync.rel(p)} 缺少或包含非法 Front Matter：{err}")

    # 4. 旧 spike 归档目录
    archived_spikes_dir = consumer / "docs/archive/spikes"
    if archived_spikes_dir.is_dir():
        for item in sorted(archived_spikes_dir.iterdir()):
            if item.name in _IGNORED_NAMES:
                continue
            if not item.is_dir() or not SPIKE_DIR_RE.match(item.name):
                problems.append(f"{sync.rel(item)}: docs/archive/spikes 存在非规范 spike 项或未知散落文件（阻断）")
        children = [p for p in archived_spikes_dir.iterdir() if p.is_dir() and SPIKE_DIR_RE.match(p.name)]
        if children:
            problems.append(f"docs/archive/spikes 存在 {len(children)} 个归档 spike 目录")

    # 5. 旧 tasks_audit.log
    old_audit = consumer / "docs/archive/tasks_audit.log"
    if old_audit.is_file():
        problems.append("docs/archive/tasks_audit.log 需迁至 docs/runtime/tasks_audit.log")

    # 6. 旧 handoff.md 归档
    old_handoff = consumer / "docs/archive/handoff.md"
    if old_handoff.is_file():
        problems.append("docs/archive/handoff.md 待退役移除")

    # 7. 已废弃派生索引：模板已删并 ignore，但旧仓已跟踪文件不受 ignore 影响
    for rel in _STALE_INDEX_RELS:
        if (consumer / rel).is_file():
            problems.append(f"{rel} 为已废弃派生索引，迁移将删除")

    return sorted(problems)


def _parse_legacy_pending_content(
    path: Path, raw_text: str, default_status: str, source_label: str, source_sub: str
) -> tuple[dict[str, Any], str]:
    """解析旧 pending 文件（可能带或不带 Front Matter，或仅正文中有状态字段）。"""
    fm: dict[str, Any] = {}
    body = raw_text
    if raw_text.startswith("---"):
        try:
            fm, body = parse_front_matter_text(raw_text, source=source_label)
        except (TaskDataError, ValueError, KeyError) as err:
            raise MigrationError(f"{source_label}: Front Matter 解析失败：{err}") from err
        unknown = set(fm) - PENDING_ALLOWED_FM_KEYS
        if unknown:
            raise MigrationError(
                f"{source_label}: 包含未知 Front Matter 字段 {sorted(unknown)}，拒绝丢弃迁移"
            )

    file_match = PENDING_FILE_RE.match(path.name)
    if not file_match:
        raise MigrationError(f"{source_label}: 文件名不符合规范 pNNN_slug.md")
    pid = f"p{int(file_match.group(1)):03d}"
    slug = file_match.group(2)

    declared_status = fm.get("status")
    # 目录来源与 Front Matter 状态一致性检查（契约第 253 行）
    if source_sub == "archive":
        if declared_status is not None and declared_status != "closed":
            raise MigrationError(
                f"{source_label}: 状态与所在目录矛盾（归档目录中却声明 status: {declared_status!r}）"
            )
        status = "closed"
    elif source_sub == "todo":
        if declared_status is not None and declared_status != "todo":
            raise MigrationError(
                f"{source_label}: 状态与所在目录矛盾（todo 目录中却声明 status: {declared_status!r}）"
            )
        status = "todo"
    elif source_sub == "parked":
        if declared_status is not None and declared_status != "parked":
            raise MigrationError(
                f"{source_label}: 状态与所在目录矛盾（parked 目录中却声明 status: {declared_status!r}）"
            )
        status = "parked"
    elif source_sub == "flat":
        if not declared_status:
            raise MigrationError(f"{source_label}: 扁平 docs/pending 下必须声明 status")
        status = declared_status
    else:
        status = declared_status or default_status

    resolution = fm.get("resolution")
    reason = fm.get("reason")
    fix_ref = fm.get("fix_ref")

    if status == "parked" and not reason:
        # 从正文中解析「- 暂搁：...」
        m = re.search(r"^[ \t]*-[ \t]*暂搁[ \t]*[:：][ \t]*(.*)$", body, re.MULTILINE)
        if m and m.group(1).strip():
            reason = m.group(1).strip()
    if status == "parked" and reason in ("未开", "不办", "无"):
        reason = None

    if status == "closed":
        # 从正文中解析「- 处理：...」。逐段识别具名字段，无条件保留 fix_ref；
        # 裸 tid / main / PR 等仍按旧规则收作 fix_ref，避免与 reason= 同时出现时丢失。
        m = re.search(r"^[ \t]*-[ \t]*处理[ \t]*[:：][ \t]*(.*)$", body, re.MULTILINE)
        if m:
            line = m.group(1).strip()
            free_parts: list[str] = []
            for part in [p.strip() for p in re.split(r"[；;]", line) if p.strip()]:
                if part.startswith("resolution="):
                    resolution = part.split("=", 1)[1].strip()
                elif part.startswith("fix_ref="):
                    fix_ref = part.split("=", 1)[1].strip()
                elif part.startswith("reason="):
                    reason = part.split("=", 1)[1].strip()
                else:
                    free_parts.append(part)
            for part in free_parts:
                if re.match(r"^t\d{3,}$", part) or part in ("main", "PR", "untracked"):
                    if not fix_ref:
                        fix_ref = part
                elif not reason and part not in ("未开", "不办", "无"):
                    reason = part

        if reason in ("未开", "不办", "无"):
            reason = None

    result_fm = {
        "pid": pid,
        "slug": slug,
        "status": status,
    }
    if status == "parked":
        if not reason or not str(reason).strip():
            raise MigrationError(f"{source_label}: 暂搁条目缺少暂搁依据 (reason)")
        result_fm["reason"] = str(reason).strip()
    elif status == "closed":
        # 绝不使用默认 resolution = "resolved" 兜底；缺少闭环结论或依据直接阻断
        if not resolution or resolution not in PENDING_VALID_RESOLUTIONS:
            raise MigrationError(
                f"{source_label}: 闭环条目缺少有效闭环结论 (resolution 缺失或非法：{resolution!r})"
            )
        if not reason or not str(reason).strip():
            raise MigrationError(f"{source_label}: 闭环条目缺少闭环依据 (reason)")
        result_fm["resolution"] = resolution
        result_fm["reason"] = str(reason).strip()
        if fix_ref:
            result_fm["fix_ref"] = str(fix_ref).strip()

    return result_fm, body


def _validate_spike_findings_references(report_text: str, source_label: str, consumer: Path) -> None:
    """校验 spike 报告引用的 findings 文件在 docs/findings/ 中是否真实存在。"""
    finding_refs = set(re.findall(r"\b(d\d{3,})\b", report_text))
    finding_refs.update(re.findall(r"docs/findings/(d\d{3,})_[a-z0-9_]+", report_text))
    for did in sorted(finding_refs):
        findings_dir = consumer / "docs/findings"
        matches = list(findings_dir.glob(f"{did}_*.md")) if findings_dir.is_dir() else []
        if not any(m.is_file() for m in matches):
            raise MigrationError(
                f"{source_label}: 引用的 finding {did} 在 docs/findings/ 中不存在真实文件"
            )


def _validate_spike_closed_evidence(report_text: str, source_label: str, consumer: Path) -> None:
    """校验归档 spike 报告是否包含有效的闭环结论与 findings 事实。"""
    if not report_text.strip():
        raise MigrationError(f"{source_label}: report.md 为空")
    # 占位符检测
    for ph in ("{结论}", "{采纳决定}", "{Findings}", "{结论与采纳}"):
        if ph in report_text:
            raise MigrationError(f"{source_label}: report.md 仍包含未替换占位符 {ph}")

    # 必须包含结论/采纳相关内容
    has_conclusion = any(
        kw in report_text for kw in ("## 结论", "## 结果", "## 决策", "结论：", "结论:")
    )
    if not has_conclusion:
        raise MigrationError(f"{source_label}: report.md 缺少实质结论与闭环证据")

    # 必须包含有效 findings 关联（如含有 dNNN 引用）
    finding_refs = set(re.findall(r"\b(d\d{3,})\b", report_text))
    finding_refs.update(re.findall(r"docs/findings/(d\d{3,})_[a-z0-9_]+", report_text))
    if not finding_refs:
        raise MigrationError(
            f"{source_label}: report.md 缺少有效 findings 关联（未包含 dNNN 引用）"
        )
    _validate_spike_findings_references(report_text, source_label, consumer)


def _validate_done_task_implementation_and_lineage(
    consumer: Path, task: TaskItem, sync: ctx.SyncContext
) -> str:
    """对 status: done 任务执行 _dependency_implementations 算法验证实现提交与血缘（方案第 257–269 行）。"""
    source_label = sync.rel(task.source_dir)
    handoff_file = task.source_dir / "handoff.json"
    if not handoff_file.is_file():
        raise MigrationError(f"{source_label}: done 任务缺失 handoff.json 终态证据")
    try:
        handoff_data = json.loads(handoff_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as err:
        raise MigrationError(f"{source_label}: handoff.json 损坏或无法读取：{err}") from err

    if not isinstance(handoff_data, dict):
        raise MigrationError(f"{source_label}: handoff.json 格式错误（须为 JSON 对象）")
    if handoff_data.get("tid") != task.tid:
        raise MigrationError(
            f"{source_label}: handoff.json tid 不匹配（{handoff_data.get('tid')} != {task.tid}）"
        )

    _HANDOFF_TYPES = {
        "tid": str,
        "attempt": int,
        "execution_id": str,
        "status": str,
        "branch": str,
        "base_sha": str,
        "spec_source_sha": str,
        "tests": str,
        "blackbox": str,
        "review": str,
        "review_scope": str,
        "pending": list,
        "findings": list,
    }
    for key, expected in _HANDOFF_TYPES.items():
        if key not in handoff_data:
            raise MigrationError(f"{source_label}: handoff 缺失必需键 {key}")
        val = handoff_data[key]
        if not isinstance(val, expected) or (expected is int and isinstance(val, bool)):
            raise MigrationError(f"{source_label}: handoff 字段 {key} 类型非法")

    if not (
        isinstance(handoff_data.get("attempt"), int)
        and not isinstance(handoff_data.get("attempt"), bool)
        and handoff_data["attempt"] > 0
    ):
        raise MigrationError(f"{source_label}: handoff attempt 必须为正整数")
    if not (
        isinstance(handoff_data.get("execution_id"), str)
        and bool(handoff_data["execution_id"].strip())
    ):
        raise MigrationError(f"{source_label}: handoff execution_id 必须为非空字符串")
    if handoff_data.get("status") != "done":
        raise MigrationError(
            f"{source_label}: handoff status 必须为 done（实际为 {handoff_data.get('status')!r}）"
        )

    base_sha = handoff_data.get("base_sha")
    if not base_sha or not isinstance(base_sha, str):
        raise MigrationError(f"{source_label}: handoff base_sha 缺失")

    res_base = _git(
        consumer, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{base_sha}^{{commit}}"
    )
    if res_base.returncode != 0 or not res_base.stdout.strip():
        raise MigrationError(f"{source_label}: handoff base_sha {base_sha!r} 无法解析为 commit")
    resolved_base = res_base.stdout.strip()

    res_hash = _git(consumer, "hash-object", str(handoff_file))
    if res_hash.returncode != 0 or not res_hash.stdout.strip():
        raise MigrationError(f"{source_label}: 无法计算 handoff.json blob OID")
    blob_oid = res_hash.stdout.strip()

    # 获取主干分支 sha
    res_def = _git(consumer, "rev-parse", "--verify", "--quiet", "refs/heads/main")
    main_ref = "refs/heads/main" if res_def.returncode == 0 else "HEAD"
    main_commit_res = _git(consumer, "rev-parse", "--verify", "--quiet", f"{main_ref}^{{commit}}")
    if main_commit_res.returncode != 0 or not main_commit_res.stdout.strip():
        raise MigrationError(f"{source_label}: 无法解析主干分支提交")
    main_sha = main_commit_res.stdout.strip()

    # 在提交历史中按 Blob OID 定位非 merge 实现提交
    log_res = _git(
        consumer, "log", "--format=%H", "--no-merges", f"--find-object={blob_oid}", main_sha
    )
    if log_res.returncode != 0:
        raise MigrationError(f"{source_label}: git log --find-object 失败：{log_res.stderr.strip()}")
    candidates = [line.strip() for line in log_res.stdout.splitlines() if line.strip()]

    matched_commits = []
    for sha in candidates:
        parents_res = _git(consumer, "rev-parse", f"{sha}^@")
        if parents_res.returncode != 0:
            continue
        parents = parents_res.stdout.splitlines()
        if len(parents) != 1:
            continue
        first_parent = parents[0].strip()
        fp_resolve = _git(consumer, "rev-parse", "--verify", "--quiet", f"{first_parent}^{{commit}}")
        if fp_resolve.returncode != 0 or fp_resolve.stdout.strip() != resolved_base:
            continue
        tree_res = _git(consumer, "ls-tree", "-r", "-z", sha)
        if tree_res.returncode != 0:
            continue
        sha_blobs = {
            entry.split("\t", 1)[0].split()[2]
            for entry in tree_res.stdout.split("\0")
            if entry
            and len(entry.split("\t", 1)[0].split()) >= 3
            and entry.split("\t", 1)[0].split()[1] == "blob"
        }
        if blob_oid in sha_blobs:
            matched_commits.append(sha)

    if len(matched_commits) != 1:
        raise MigrationError(
            f"{source_label}: done 任务实现提交复验失败：预期唯一匹配，实际找到 {len(matched_commits)} 个候选"
        )
    impl_sha = matched_commits[0]

    anc_res = _git(consumer, "merge-base", "--is-ancestor", impl_sha, main_sha)
    if anc_res.returncode != 0:
        raise MigrationError(
            f"{source_label}: done 任务实现提交 {impl_sha} 血缘不合法（不在主干祖先链中）"
        )

    return impl_sha


def _clean_empty_dir_strictly(dir_path: Path, sync: ctx.SyncContext) -> None:
    """严格清理空目录：若非空直接抛出 MigrationError 阻断，严禁 rmtree 或静默忽略。"""
    if not dir_path.exists():
        return
    if not dir_path.is_dir():
        raise MigrationError(f"清理失败：{sync.rel(dir_path)} 不是目录")

    # 自底向上清理空子目录；模板自带占位文件（.DS_Store / .gitkeep）同样清理，
    # 否则旧消费仓的 .gitkeep 会让整个迁移事务在清理阶段回滚。
    for item in sorted(dir_path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if item.is_file():
            if item.name in _CLEANABLE_NAMES:
                item.unlink()
            else:
                raise MigrationError(
                    f"清理旧目录失败：{sync.rel(dir_path)} 仍包含非空残留文件 {sync.rel(item)}"
                )
        elif item.is_dir():
            try:
                item.rmdir()
            except OSError as err:
                raise MigrationError(
                    f"清理旧子目录失败（非空）：{sync.rel(item)} - {err}"
                ) from err

    try:
        dir_path.rmdir()
    except OSError as err:
        raise MigrationError(
            f"清理旧目录失败（非空）：{sync.rel(dir_path)} - {err}"
        ) from err


def _check_worktrees_clean(root: Path) -> None:
    r = _git(root, "worktree", "list", "--porcelain")
    if r.returncode != 0:
        raise MigrationError(f"无法查询 git worktree（{r.stderr.strip()}）；拒绝放行迁移")
    active_wt = []
    current_root = None
    for line in r.stdout.splitlines() + [""]:
        if line.startswith("worktree "):
            current_root = Path(line.removeprefix("worktree ")).resolve()
        elif line.startswith("branch refs/heads/") and current_root:
            branch = line.removeprefix("branch refs/heads/")
            if current_root != root.resolve() and re.match(r"^t\d{3,}_", branch):
                active_wt.append(f"{branch} @ {current_root}")
        elif not line:
            current_root = None
    if active_wt:
        raise MigrationError(
            "迁移前须清理或收尾已登记 task worktree：\n- " + "\n- ".join(active_wt)
        )


def _check_no_unknown_entries(dir_path: Path, kind: str, sync: ctx.SyncContext) -> None:
    if not dir_path.is_dir():
        return
    for item in sorted(dir_path.iterdir()):
        if item.name in _IGNORED_NAMES:
            continue
        if kind == "tasks":
            if not item.is_dir() or not TASK_DIR_RE.match(item.name):
                raise MigrationError(f"{sync.rel(item)}: docs/archive/tasks 存在非规范任务项或未知散落文件")
        elif kind == "spikes":
            if not item.is_dir() or not SPIKE_DIR_RE.match(item.name):
                raise MigrationError(f"{sync.rel(item)}: docs/archive/spikes 存在非规范 spike 项或未知散落文件")
        elif kind == "pending":
            if not item.is_file() or not PENDING_FILE_RE.match(item.name):
                raise MigrationError(f"{sync.rel(item)}: {sync.rel(dir_path)} 存在非规范待办项或未知散落文件")


def build_migration_plan(sync: ctx.SyncContext) -> MigrationPlan:
    """预检并构建实体存储一次性迁移计划；若存在冲突或数据不符则阻断。"""
    consumer = sync.consumer

    # 1. 检查 worktree 现场与软链实体（拒绝沿软链写/删仓外文件）
    _check_worktrees_clean(consumer)
    _reject_symlink_entities(consumer, sync)

    tasks_plan: list[TaskItem] = []
    pendings_plan: list[PendingItem] = []
    spikes_plan: list[SpikeItem] = []
    dirs_to_clean: list[Path] = []

    # 2. Tasks 预检
    seen_tids: dict[str, Path] = {}
    active_tasks_dir = consumer / "docs/tasks"
    archived_tasks_dir = consumer / "docs/archive/tasks"

    # 收集当前 docs/tasks/
    if active_tasks_dir.is_dir():
        for child in sorted(active_tasks_dir.iterdir()):
            if not child.is_dir() or child.name == "task_template" or child.name.startswith("."):
                continue
            m = re.match(r"^(t\d{3,})_([a-z0-9_]+)$", child.name)
            if not m:
                continue
            tid, slug = m.group(1), m.group(2)
            seen_tids[tid] = child
            prog = child / PROGRESS_FILE
            if not prog.is_file():
                raise MigrationError(f"{sync.rel(child)}: 缺失 {PROGRESS_FILE}")
            fm, _ = parse_front_matter_text(prog.read_text(encoding="utf-8"), source=sync.rel(prog))
            if fm.get("tid") != tid:
                raise MigrationError(f"{sync.rel(prog)}: tid 不匹配（{fm.get('tid')} != {tid}）")
            t_status = fm.get("status", "unknown")
            item = TaskItem(tid, slug, child, child, t_status)
            if t_status == "done":
                _validate_done_task_implementation_and_lineage(consumer, item, sync)
            tasks_plan.append(item)

    # 收集 docs/archive/tasks/
    if archived_tasks_dir.is_dir():
        _check_no_unknown_entries(archived_tasks_dir, "tasks", sync)
        dirs_to_clean.append(archived_tasks_dir)
        for child in sorted(archived_tasks_dir.iterdir()):
            if child.name in _IGNORED_NAMES or not child.is_dir() or child.name.startswith("."):
                continue
            m = re.match(r"^(t\d{3,})_([a-z0-9_]+)$", child.name)
            if not m:
                continue
            tid, slug = m.group(1), m.group(2)
            if tid in seen_tids:
                raise MigrationError(
                    f"同号多处：tid {tid} 同时存在于 {sync.rel(seen_tids[tid])} 和 {sync.rel(child)}"
                )
            target_dir = active_tasks_dir / child.name
            if target_dir.exists():
                raise MigrationError(f"目标占用：{sync.rel(target_dir)} 已存在")

            prog = child / PROGRESS_FILE
            legacy_note = child / "note.md"
            task_file = child / "task.md"
            source_file = prog if prog.is_file() else (legacy_note if legacy_note.is_file() else task_file)
            if not source_file.is_file():
                raise MigrationError(f"{sync.rel(child)}: 缺少任务进度/过程总账文件")
            fm, _ = parse_front_matter_text(source_file.read_text(encoding="utf-8"), source=sync.rel(source_file))
            status = fm.get("status")
            if status not in ("done", "dropped"):
                raise MigrationError(
                    f"{sync.rel(child)}: 归档任务状态与目录矛盾，必须为 done 或 dropped（实际为 {status!r}）"
                )
            item = TaskItem(tid, slug, child, target_dir, status)
            if status == "done":
                _validate_done_task_implementation_and_lineage(consumer, item, sync)

            seen_tids[tid] = child
            tasks_plan.append(item)

    # 3. Pending 预检
    seen_pids: dict[str, Path] = {}
    pending_sources: list[tuple[Path, str, str]] = []

    # 扫描旧目录
    p_todo = consumer / "docs/pending/todo"
    if p_todo.is_dir():
        _check_no_unknown_entries(p_todo, "pending", sync)
        dirs_to_clean.append(p_todo)
        for f in sorted(p_todo.glob("p*.md")):
            if f.is_file() and PENDING_FILE_RE.match(f.name):
                pending_sources.append((f, "todo", "todo"))
    p_parked = consumer / "docs/pending/parked"
    if p_parked.is_dir():
        _check_no_unknown_entries(p_parked, "pending", sync)
        dirs_to_clean.append(p_parked)
        for f in sorted(p_parked.glob("p*.md")):
            if f.is_file() and PENDING_FILE_RE.match(f.name):
                pending_sources.append((f, "parked", "parked"))
    p_archived = consumer / "docs/archive/pending"
    if p_archived.is_dir():
        _check_no_unknown_entries(p_archived, "pending", sync)
        dirs_to_clean.append(p_archived)
        for f in sorted(p_archived.glob("p*.md")):
            if f.is_file() and PENDING_FILE_RE.match(f.name):
                pending_sources.append((f, "closed", "archive"))

    # 扫描已在 docs/pending/ 扁平目录的文件
    p_flat = consumer / "docs/pending"
    if p_flat.is_dir():
        for f in sorted(p_flat.glob("p*.md")):
            if f.is_file() and PENDING_FILE_RE.match(f.name):
                pending_sources.append((f, "todo", "flat"))

    for path, default_status, source_sub in pending_sources:
        match = PENDING_FILE_RE.match(path.name)
        assert match is not None
        pid = f"p{int(match.group(1)):03d}"
        if pid in seen_pids and seen_pids[pid].resolve() != path.resolve():
            raise MigrationError(
                f"同号多处：pid {pid} 同时存在于 {sync.rel(seen_pids[pid])} 和 {sync.rel(path)}"
            )
        seen_pids[pid] = path
        target_path = consumer / "docs/pending" / path.name
        if target_path.exists() and target_path.resolve() != path.resolve():
            raise MigrationError(f"目标占用：{sync.rel(target_path)} 已存在")

        content = path.read_text(encoding="utf-8")
        fm, body = _parse_legacy_pending_content(
            path, content, default_status, sync.rel(path), source_sub
        )
        validate_label = sync.rel(path)
        try:
            validate_pending_front_matter(fm, filename=path.name, source=validate_label)
        except (TaskDataError, ValueError, KeyError) as err:
            raise MigrationError(f"{validate_label} 非法 Front Matter：{err}") from err
        pendings_plan.append(
            PendingItem(
                pid=fm["pid"],
                slug=fm["slug"],
                source_path=path,
                target_path=target_path,
                status=fm["status"],
                resolution=fm.get("resolution"),
                reason=fm.get("reason"),
                fix_ref=fm.get("fix_ref"),
                body=body,
            )
        )

    # 4. Spikes 预检
    seen_sids: dict[str, Path] = {}
    active_spikes_dir = consumer / "docs/spikes"
    archived_spikes_dir = consumer / "docs/archive/spikes"

    # 收集当前 docs/spikes/
    if active_spikes_dir.is_dir():
        for child in sorted(active_spikes_dir.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            m = SPIKE_DIR_RE.match(child.name)
            if not m:
                continue
            sid = f"s{int(m.group(1)):03d}"
            slug = child.name.split("_", 1)[1]
            seen_sids[sid] = child
            rep = child / "report.md"
            if not rep.is_file():
                raise MigrationError(f"{sync.rel(child)}: 缺失 report.md")
            rep_text = rep.read_text(encoding="utf-8")
            if rep_text.startswith("---"):
                fm, body = parse_front_matter_text(rep_text, source=sync.rel(rep))
                status = fm.get("status", "active")
            else:
                fm = {"sid": sid, "slug": slug, "status": "active"}
                body = rep_text
                status = "active"
            final_fm = dict(fm)
            final_fm["status"] = status
            _validate_spike_fm(final_fm, sid, slug, rep, sync)
            new_rep = _render_front_matter(final_fm, body)
            _validate_spike_findings_references(rep_text, sync.rel(rep), consumer)
            spikes_plan.append(SpikeItem(sid, slug, child, child, status, new_rep))

    # 收集 docs/archive/spikes/
    if archived_spikes_dir.is_dir():
        _check_no_unknown_entries(archived_spikes_dir, "spikes", sync)
        dirs_to_clean.append(archived_spikes_dir)
        for child in sorted(archived_spikes_dir.iterdir()):
            if child.name in _IGNORED_NAMES or not child.is_dir() or child.name.startswith("."):
                continue
            m = SPIKE_DIR_RE.match(child.name)
            if not m:
                continue
            sid = f"s{int(m.group(1)):03d}"
            slug = child.name.split("_", 1)[1]
            if sid in seen_sids:
                raise MigrationError(
                    f"同号多处：sid {sid} 同时存在于 {sync.rel(seen_sids[sid])} 和 {sync.rel(child)}"
                )
            target_dir = active_spikes_dir / child.name
            if target_dir.exists():
                raise MigrationError(f"目标占用：{sync.rel(target_dir)} 已存在")
            rep = child / "report.md"
            if not rep.is_file():
                raise MigrationError(f"{sync.rel(child)}: 缺失 report.md 闭环证据")
            rep_text = rep.read_text(encoding="utf-8")

            # 归档 spike：统一 Front Matter 校验 + YAML 序列化，保留全部既有字段。
            # 未知字段与类型问题由 validate_spike_metadata 阻断，不再白名单重建静默丢弃；
            # reason 等含引号/反斜杠的值经 dump_front_matter 转义，不再写出非法 YAML。
            if rep_text.startswith("---"):
                existing_fm, body = parse_front_matter_text(rep_text, source=sync.rel(rep))
            else:
                existing_fm, body = {}, rep_text
            if not isinstance(existing_fm, dict):
                raise MigrationError(f"{sync.rel(rep)}: Front Matter 解析结果非法")
            declared = existing_fm.get("status")
            if declared == "active":
                raise MigrationError(
                    f"{sync.rel(rep)}: 归档 spike 状态与目录矛盾（声明为 active）"
                )
            target_status = "superseded" if declared == "superseded" else "completed"
            final_fm = dict(existing_fm)
            final_fm["status"] = target_status
            if not final_fm.get("sid"):
                final_fm["sid"] = sid
            if not final_fm.get("slug"):
                final_fm["slug"] = slug
            _validate_spike_fm(final_fm, sid, slug, rep, sync)
            if target_status == "superseded":
                _validate_spike_findings_references(rep_text, sync.rel(rep), consumer)
            else:
                _validate_spike_closed_evidence(rep_text, sync.rel(rep), consumer)
            new_rep = _render_front_matter(final_fm, body)
            seen_sids[sid] = child
            spikes_plan.append(SpikeItem(sid, slug, child, target_dir, target_status, new_rep))

    # 5. tasks_audit.log 预检
    old_audit = consumer / "docs/archive/tasks_audit.log"
    new_audit = consumer / "docs/runtime/tasks_audit.log"
    audit_move = None
    if old_audit.is_file():
        if new_audit.is_file():
            # 严格契约第 253 行：即使同字节也绝不擅自决定删除，阻断并要求人工介入
            raise MigrationError(
                f"目标占用：{sync.rel(new_audit)} 已存在，禁止擅自覆盖或删除旧归档日志，请人工介入处理"
            )
        audit_move = (old_audit, new_audit)

    # 6. docs/archive/handoff.md 预检
    old_handoff = consumer / "docs/archive/handoff.md"
    handoff_remove = None
    if old_handoff.is_file():
        diff_res = _git(consumer, "diff", "--quiet", "--", "docs/archive/handoff.md")
        cached_diff = _git(consumer, "diff", "--cached", "--quiet", "--", "docs/archive/handoff.md")
        ls_res = _git(consumer, "ls-files", "--error-unmatch", "docs/archive/handoff.md")
        if diff_res.returncode != 0 or cached_diff.returncode != 0 or ls_res.returncode != 0:
            raise MigrationError(
                "docs/archive/handoff.md 含有未提交修改或未入库内容，请先提交或备份到 Git 历史"
            )
        handoff_remove = old_handoff

    # 7. 维护文档中的引用改写计划
    ref_updates: list[tuple[Path, str, str]] = []
    replacement_map = [
        ("docs/archive/tasks/", "docs/tasks/"),
        ("docs/pending/todo/", "docs/pending/"),
        ("docs/pending/parked/", "docs/pending/"),
        ("docs/archive/pending/", "docs/pending/"),
        ("docs/archive/spikes/", "docs/spikes/"),
        ("docs/archive/tasks_audit.log", "docs/runtime/tasks_audit.log"),
    ]
    for rel_doc in ("docs/handoff.md", "docs/specs_index.md"):
        doc_path = consumer / rel_doc
        if doc_path.is_file():
            text = doc_path.read_text(encoding="utf-8")
            new_text = text
            for old_pat, new_pat in replacement_map:
                new_text = new_text.replace(old_pat, new_pat)
            if new_text != text:
                ref_updates.append((doc_path, text, new_text))

    # 8. 已废弃派生索引：模板已删并 ignore，旧仓已跟踪文件必须显式删除
    stale_index_paths = [
        consumer / rel for rel in _STALE_INDEX_RELS if (consumer / rel).is_file()
    ]

    return MigrationPlan(
        tasks=tasks_plan,
        pendings=pendings_plan,
        spikes=spikes_plan,
        audit_log_move=audit_move,
        handoff_archive_remove=handoff_remove,
        reference_updates=ref_updates,
        dirs_to_clean=dirs_to_clean,
        stale_index_paths=stale_index_paths,
    )


def _prune_ancestor_paths(paths: set[str]) -> list[str]:
    """若集合中同时存在父目录与子路径，剪除子路径以防 FileTransaction 重复嵌套复制报错。"""
    sorted_paths = sorted(paths, key=lambda p: (p.count("/"), len(p)))
    roots: list[str] = []
    for p in sorted_paths:
        if not any(p == r or p.startswith(r + "/") for r in roots):
            roots.append(p)
    return sorted(roots)


def _collect_transaction_paths(consumer: Path, plan: MigrationPlan) -> list[str]:
    """收集进入文件事务的所有受影响路径（使用顶级或相对路径）。"""
    paths_set: set[str] = set()

    for t in plan.tasks:
        if t.source_dir != t.target_dir:
            paths_set.add(t.source_dir.relative_to(consumer).as_posix())
            paths_set.add(t.target_dir.relative_to(consumer).as_posix())
    if (consumer / "docs/tasks").exists():
        paths_set.add("docs/tasks")
    if (consumer / "docs/archive/tasks").exists():
        paths_set.add("docs/archive/tasks")

    for p in plan.pendings:
        paths_set.add(p.source_path.relative_to(consumer).as_posix())
        paths_set.add(p.target_path.relative_to(consumer).as_posix())
    if (consumer / "docs/pending").exists():
        paths_set.add("docs/pending")
    if (consumer / "docs/archive/pending").exists():
        paths_set.add("docs/archive/pending")

    for s in plan.spikes:
        paths_set.add(s.source_dir.relative_to(consumer).as_posix())
        paths_set.add(s.target_dir.relative_to(consumer).as_posix())
    if (consumer / "docs/spikes").exists():
        paths_set.add("docs/spikes")
    if (consumer / "docs/archive/spikes").exists():
        paths_set.add("docs/archive/spikes")

    if plan.audit_log_move:
        paths_set.add(plan.audit_log_move[0].relative_to(consumer).as_posix())
        paths_set.add(plan.audit_log_move[1].relative_to(consumer).as_posix())
    if plan.handoff_archive_remove:
        paths_set.add(plan.handoff_archive_remove.relative_to(consumer).as_posix())

    for doc_path, _, _ in plan.reference_updates:
        paths_set.add(doc_path.relative_to(consumer).as_posix())

    for index_path in plan.stale_index_paths:
        paths_set.add(index_path.relative_to(consumer).as_posix())

    return _prune_ancestor_paths(paths_set)


def migrate_entity_storage(
    sync: ctx.SyncContext, *, write: bool = False, require_clean: bool = True,
) -> list[str]:
    """执行 entity-storage 迁移：默认 dry-run，加 write=True 在事务中落盘。

    require_clean 由 cmd_migrate 在 all 模式置 False：all 入口已统一做一次干净检查，
    逐项事务落盘造成的工作区变更不得被 entity 步误判为「现场脏」而死锁。
    """
    consumer = sync.consumer

    with git_common_lock(consumer, CONTROL_LOCK_NAME):
        # 先判空：无迁移需求时直接返回，不检查工作区（migrate all 前序步骤落盘后
        # 工作区已脏，若 entity 步进门先查干净会必然误报）。
        problems = entity_storage_migration_status(sync=sync)
        if not problems:
            return []
        if require_clean:
            _check_clean_working_copy(consumer)

        plan = build_migration_plan(sync=sync)
        reports: list[str] = []

        # 生成拟定改动清单
        for t in plan.tasks:
            if t.source_dir != t.target_dir:
                reports.append(
                    f"Task {t.tid}: {sync.rel(t.source_dir)} -> {sync.rel(t.target_dir)} (status: {t.status})"
                )
            else:
                reports.append(f"Task {t.tid}: 原位保留 {sync.rel(t.source_dir)} (status: {t.status})")

        for p in plan.pendings:
            if p.source_path != p.target_path:
                reports.append(
                    f"Pending {p.pid}: {sync.rel(p.source_path)} -> {sync.rel(p.target_path)} (status: {p.status})"
                )
            else:
                reports.append(
                    f"Pending {p.pid}: 原位更新 {sync.rel(p.target_path)} (status: {p.status})"
                )

        for s in plan.spikes:
            if s.source_dir != s.target_dir:
                reports.append(
                    f"Spike {s.sid}: {sync.rel(s.source_dir)} -> {sync.rel(s.target_dir)} (status: {s.status})"
                )
            else:
                reports.append(
                    f"Spike {s.sid}: 原位校验 {sync.rel(s.target_dir)} (status: {s.status})"
                )

        if plan.audit_log_move:
            reports.append(
                f"Audit log: {sync.rel(plan.audit_log_move[0])} -> {sync.rel(plan.audit_log_move[1])}"
            )
        if plan.handoff_archive_remove:
            reports.append(f"Handoff archive: 移除 {sync.rel(plan.handoff_archive_remove)}")
        for doc_path, _, _ in plan.reference_updates:
            reports.append(f"Reference update: 更新 {sync.rel(doc_path)} 中过时路径引用")
        for index_path in plan.stale_index_paths:
            reports.append(f"Stale index: 删除 {sync.rel(index_path)}（已废弃派生索引）")

        if not write:
            print("模式：dry-run；以下为实体存储迁移计划（未写盘，加 --write 执行落盘）：")
            for r in reports:
                print(f"- {r}")
            return reports

        # 真实写入：持久文件事务保护
        tx_paths = _collect_transaction_paths(consumer, plan)
        transaction = FileTransaction(consumer, ".scratch/repo_sync_rollback")
        try:
            transaction.begin(tx_paths)
        except ValueError as err:
            raise MigrationError(f"事务无法启动：{err}") from err

        try:
            # 1. 搬移并更新 Tasks
            for t in plan.tasks:
                if t.source_dir != t.target_dir:
                    _assert_within_consumer(t.source_dir, sync)
                    _assert_within_consumer(t.target_dir, sync)
                    t.target_dir.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(t.source_dir), str(t.target_dir))

            # 2. 搬移并重写 Pendings
            for p in plan.pendings:
                _assert_within_consumer(p.target_path, sync)
                p.target_path.parent.mkdir(parents=True, exist_ok=True)
                fm_data = {
                    "pid": p.pid,
                    "slug": p.slug,
                    "status": p.status,
                }
                if p.status == "parked":
                    fm_data["reason"] = p.reason
                elif p.status == "closed":
                    fm_data["resolution"] = p.resolution
                    fm_data["reason"] = p.reason
                    if p.fix_ref:
                        fm_data["fix_ref"] = p.fix_ref
                clean_body = p.body.lstrip("\r\n").rstrip()
                new_text = dump_pending_front_matter(fm_data) + (clean_body + "\n" if clean_body else "")
                atomic_write_text(p.target_path, new_text)
                if p.source_path.resolve() != p.target_path.resolve() and p.source_path.exists():
                    _assert_within_consumer(p.source_path, sync)
                    p.source_path.unlink()

            # 3. 搬移并重写 Spikes
            for s in plan.spikes:
                if s.source_dir != s.target_dir:
                    _assert_within_consumer(s.source_dir, sync)
                    _assert_within_consumer(s.target_dir, sync)
                    s.target_dir.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(s.source_dir), str(s.target_dir))
                rep_file = s.target_dir / "report.md"
                _assert_within_consumer(rep_file, sync)
                atomic_write_text(rep_file, s.report_content)

            # 4. 迁移 tasks_audit.log
            if plan.audit_log_move:
                src_log, dst_log = plan.audit_log_move
                _assert_within_consumer(src_log, sync)
                _assert_within_consumer(dst_log, sync)
                dst_log.parent.mkdir(parents=True, exist_ok=True)
                dst_log.write_bytes(src_log.read_bytes())
                src_log.unlink()

            # 5. 移除 docs/archive/handoff.md
            if plan.handoff_archive_remove and plan.handoff_archive_remove.exists():
                _assert_within_consumer(plan.handoff_archive_remove, sync)
                plan.handoff_archive_remove.unlink()

            # 6. 更新引用
            for doc_path, _, new_content in plan.reference_updates:
                _assert_within_consumer(doc_path, sync)
                atomic_write_text(doc_path, new_content)

            # 7. 删除已废弃派生索引（旧仓已跟踪文件，.gitignore 不生效）
            for index_path in plan.stale_index_paths:
                if index_path.is_file():
                    _assert_within_consumer(index_path, sync)
                    index_path.unlink()

            # 8. 清理空旧目录（严格逐级 rmdir，绝不使用 rmtree，非空直接阻断报错）
            for d in plan.dirs_to_clean:
                if d.exists() and d.is_dir():
                    _clean_empty_dir_strictly(d, sync)

            # 9. 写入后验证：再次查询必须无旧布局残留
            remaining = entity_storage_migration_status(sync=sync)
            if remaining:
                raise MigrationError(
                    "实体存储迁移落盘后仍有未收敛项：\n- " + "\n- ".join(remaining)
                )

            transaction.applied(verified=True)
            transaction.accept()
        except Exception as error:
            try:
                transaction.rollback(automatic=True)
            except Exception as rb_err:
                raise MigrationError(
                    f"实体存储迁移失败（{error}）；自动回滚失败：{rb_err}；"
                    "备份保留在 .scratch/repo_sync_rollback，请人工恢复"
                ) from error
            raise MigrationError(f"实体存储迁移失败，已自动回滚：{error}") from error

        return reports
