"""同步期实体消重与引用更新辅助：由 entity_storage_migration 调用，不注册独立迁移阶段。

本模块只做纯计算与明确的文本转换，不持有事务、不写盘、不提供运行期兼容路径。
"""

from __future__ import annotations

import posixpath
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from _id_scan import (
    ENTITIES,
    IdScanError,
    branch_uses_current_schema,
    canonical_entry_id,
    local_branches,
    scan_occupied_max_ids,
    validate_snapshot_ids,
)
from repo_task.context import PROGRESS_FILE, FenceState

from .common import _git

_SKIP_SCAN_DIRS = frozenset({
    ".git", ".scratch", ".repo_template", "node_modules",
    ".venv", "__pycache__", ".pytest_cache",
})
_RETIRED_DOC_RELS = frozenset({"docs/pending/README.md", "docs/findings/README.md"})


@dataclass(frozen=True)
class CandidateIdentity:
    """一条实体来源：计划键、解析基准、Git 历史路径三者显式分离。

    identity_key 是计划内唯一键（旧总账条目含 #ordinal，不是真实路径）；
    doc_base 是真实文档位置（旧相对链接解析基准 + 展示）；
    history_path 是首次新增时间的真实 Git 查询路径。
    两类来源由 from_file / from_ledger 工厂一次性正确构造，调用方不再推断。
    """

    prefix: str
    orig_num: int
    orig_id: str
    slug: str
    identity_key: str
    doc_base: str
    history_path: str
    ordinal: int = 0
    is_dir: bool = False
    ledger: bool = False

    @classmethod
    def from_file(
        cls, prefix: str, orig_num: int, orig_id: str, slug: str, rel: str,
        *, is_dir: bool = False,
    ) -> "CandidateIdentity":
        return cls(prefix, orig_num, orig_id, slug, rel, rel, rel, is_dir=is_dir)

    @classmethod
    def from_ledger(
        cls, prefix: str, orig_num: int, orig_id: str, slug: str,
        ledger_rel: str, ordinal: int,
    ) -> "CandidateIdentity":
        return cls(
            prefix, orig_num, orig_id, slug,
            f"{ledger_rel}#{ordinal}", ledger_rel, ledger_rel,
            ordinal=ordinal, ledger=True,
        )


@dataclass(frozen=True)
class RenumberDecision:
    """一条来源的最终编号与目标路径。"""

    prefix: str
    orig_num: int
    orig_id: str
    final_num: int
    final_id: str
    slug: str
    source_rel: str
    target_rel: str
    ordinal: int
    kept_source_rel: str | None = None
    from_ledger: bool = False

    @property
    def renumbered(self) -> bool:
        return self.final_num != self.orig_num


def current_branch_name(consumer: Path) -> str | None:
    res = _git(consumer, "symbolic-ref", "--quiet", "--short", "HEAD")
    if res.returncode != 0:
        return None
    return res.stdout.strip() or None


def check_other_branch_duplicates(consumer: Path) -> list[str]:
    """选项 B：除当前检出分支外的当前协议本地分支存在编号冲突/布局违规即阻断迁移。"""
    current = current_branch_name(consumer)
    issues: list[str] = []
    for branch in local_branches(consumer):
        if current is not None and branch == current:
            continue
        try:
            if not branch_uses_current_schema(consumer, branch):
                continue
            validate_snapshot_ids(consumer, branch)
        except IdScanError as error:
            issues.append(f"分支 {branch} 存在编号冲突或布局违规：{error}")
    return issues


def first_added_timestamp(consumer: Path, rel: str, *, is_dir: bool = False) -> int:
    """当前分支可达历史中来源首次新增的提交时间；无记录返回 sys.maxsize。"""
    query_rel = f"{rel}/report.md" if is_dir else rel
    res = _git(
        consumer, "-c", "core.quotePath=false", "log", "--follow",
        "--diff-filter=A", "--format=%ct", "--", query_rel,
    )
    if res.returncode != 0 or not res.stdout.strip():
        return sys.maxsize
    lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
    if not lines:
        return sys.maxsize
    try:
        return int(lines[-1])
    except ValueError:
        return sys.maxsize


def occupied_max_ids(consumer: Path, extra_nums: dict[str, list[int]] | None = None) -> dict[str, int]:
    """统一占用最大号：本地历史/远端/tag、分支 tip、worktree 磁盘与 index，并入旧总账解析出的编号。"""
    max_ids = scan_occupied_max_ids(consumer)
    for prefix, numbers in (extra_nums or {}).items():
        if numbers:
            max_ids[prefix] = max(max_ids.get(prefix, 0), max(numbers))
    return max_ids


def assign_final_ids(
    consumer: Path,
    candidates_by_prefix: dict[str, list[CandidateIdentity]],
    occupied_max: dict[str, int],
) -> dict[tuple[str, str, int], RenumberDecision]:
    """按 d -> p -> s、原编号升序分配最终编号；同号分组按首次提交时间稳定保留。"""
    target_dir_map = {p: s.current_dir for p, s in ENTITIES.items()}
    decisions: dict[tuple[str, str, int], RenumberDecision] = {}
    running_max = dict(occupied_max)
    for prefix in ("d", "p", "s"):
        for item in candidates_by_prefix.get(prefix, ()):
            if item.orig_num > running_max.get(prefix, 0):
                running_max[prefix] = item.orig_num

    for prefix in ("d", "p", "s"):
        grouped: dict[int, list[CandidateIdentity]] = {}
        for item in candidates_by_prefix.get(prefix, ()):
            grouped.setdefault(item.orig_num, []).append(item)
        base_dir = target_dir_map[prefix]

        def _target(num_id: str, slug: str, is_dir: bool) -> str:
            return (
                f"{base_dir}/{num_id}_{slug}"
                if is_dir
                else f"{base_dir}/{num_id}_{slug}.md"
            )

        for orig_num in sorted(grouped):
            group = grouped[orig_num]
            if len(group) == 1:
                only = group[0]
                decisions[(prefix, only.identity_key, only.ordinal)] = RenumberDecision(
                    prefix, orig_num, only.orig_id, orig_num, only.orig_id,
                    only.slug, only.identity_key,
                    _target(only.orig_id, only.slug, only.is_dir), only.ordinal,
                    from_ledger=only.ledger,
                )
                continue

            ts_cache = {
                (c.identity_key, c.ordinal): first_added_timestamp(
                    consumer, c.history_path, is_dir=c.is_dir
                )
                for c in group
            }
            ordered = sorted(
                group,
                key=lambda c: (
                    ts_cache[(c.identity_key, c.ordinal)],
                    c.doc_base,
                    c.ordinal,
                ),
            )
            winner = ordered[0]
            decisions[(prefix, winner.identity_key, winner.ordinal)] = RenumberDecision(
                prefix, orig_num, winner.orig_id, orig_num, winner.orig_id,
                winner.slug, winner.identity_key,
                _target(winner.orig_id, winner.slug, winner.is_dir), winner.ordinal,
                from_ledger=winner.ledger,
            )
            for dup in ordered[1:]:
                running_max[prefix] = running_max.get(prefix, 0) + 1
                new_num = running_max[prefix]
                new_id = canonical_entry_id(prefix, new_num)
                decisions[(prefix, dup.identity_key, dup.ordinal)] = RenumberDecision(
                    prefix, orig_num, dup.orig_id, new_num, new_id,
                    dup.slug, dup.identity_key,
                    _target(new_id, dup.slug, dup.is_dir), dup.ordinal,
                    kept_source_rel=winner.identity_key,
                    from_ledger=dup.ledger,
                )
    return decisions


def validate_and_rewrite_h1(
    body: str, prefix: str, orig_id: str, final_id: str, *, source_label: str
) -> str:
    """校验围栏外首个 H1 的身份声明；改号时替换对应编号，不含编号的 H1 保持原样。"""
    lines = body.splitlines(keepends=True)
    fence = FenceState()
    h1_re = re.compile(r"^( {0,3}#[ \t]+)(.*)$")
    lead_id_re = re.compile(
        rf"^( {{0,3}}#[ \t]+(?:Spike[ \t]+)?)({prefix}[0-9]+)(?![0-9])(.*)$"
    )
    any_id_re = re.compile(rf"(?<![A-Za-z0-9_])({prefix}[0-9]{{3,}})(?![0-9])")
    for idx, raw_line in enumerate(lines):
        stripped = raw_line.rstrip("\r\n")
        if fence.feed(stripped):
            continue
        m_h1 = h1_re.match(stripped)
        if not m_h1:
            continue
        ending = raw_line[len(stripped):]
        m_lead = lead_id_re.match(stripped)
        if m_lead:
            if m_lead.group(2) != orig_id:
                raise ValueError(
                    f"{source_label}: H1 声明编号 {m_lead.group(2)} 与来源编号 {orig_id} 不一致"
                )
            if final_id != orig_id:
                lines[idx] = f"{m_lead.group(1)}{final_id}{m_lead.group(3)}{ending}"
            return "".join(lines)
        tokens = any_id_re.findall(m_h1.group(2))
        for tok in tokens:
            if tok != orig_id:
                raise ValueError(
                    f"{source_label}: H1 声明编号 {tok} 与来源编号 {orig_id} 不一致"
                )
        if tokens and final_id != orig_id:
            lines[idx] = f"{m_h1.group(1)}{any_id_re.sub(final_id, m_h1.group(2), count=1)}{ending}"
        return "".join(lines)
    return body


def update_exact_path_references(
    text: str,
    *,
    doc_rel: str,
    final_doc_rel: str,
    path_mappings: list[tuple[str, str, bool]],
    consumer: Path | None = None,
) -> str:
    """按本轮真实来源→目标映射更新精确路径与可解析相对引用（保留锚点）。

    文档搬迁时，即使链接目标本身未搬迁/未改号，也要按最终文档位置重新表达相对地址；
    目标命中迁移映射则跟随改号。无映射且文档未搬迁时不改动任何文本；裸编号与外链不碰。
    consumer 用于校验未命中映射的本地目标真实存在，避免改写正文示例。
    """
    sorted_mappings = sorted(
        [(o, n, d) for o, n, d in path_mappings if o != n],
        key=lambda item: len(item[0]),
        reverse=True,
    )
    doc_parent = posixpath.dirname(doc_rel) or "."
    final_parent = posixpath.dirname(final_doc_rel) or "."
    need_relink = bool(sorted_mappings) or doc_parent != final_parent
    exact_patterns: list[tuple[re.Pattern[str], str]] = []
    for old_rel, new_rel, is_dir in sorted_mappings:
        if is_dir:
            pat = re.compile(
                rf"(?<![A-Za-z0-9_./-]){re.escape(old_rel)}(?=$|/|#|\?|[^A-Za-z0-9_.-])"
            )
        else:
            pat = re.compile(
                rf"(?<![A-Za-z0-9_./-]){re.escape(old_rel)}(?![A-Za-z0-9_.-])"
            )
        exact_patterns.append((pat, new_rel))

    def _resolve(raw_path: str) -> str:
        had_dot = raw_path.startswith("./")
        # 从真实旧文档位置解析，得到仓内目标（迁移前）
        norm_old = posixpath.normpath(posixpath.join(doc_parent, raw_path))
        updated = norm_old
        hit = False
        for old_rel, new_rel, is_dir in sorted_mappings:
            if not is_dir and norm_old == old_rel:
                updated, hit = new_rel, True
                break
            if is_dir and (norm_old == old_rel or norm_old.startswith(old_rel + "/")):
                updated = new_rel + (
                    norm_old[len(old_rel):] if norm_old.startswith(old_rel + "/") else ""
                )
                hit = True
                break
        # 文档未搬迁且目标未改号：保持原相对字符串（也保护正文示例）
        if doc_parent == final_parent and not hit:
            return raw_path
        # 未命中映射时仅重写目标真实存在的本地链接，避免改写示例/精确仓内路径
        if consumer is not None and not hit and not (consumer / updated).exists():
            return raw_path
        # 输出位置可能已改变：按最终文档目录重新表达相对地址
        rel_new = posixpath.relpath(updated, final_parent)
        if had_dot and not rel_new.startswith("."):
            rel_new = "./" + rel_new
        return rel_new

    # 单次扫描：markdown 链接整体优先，剩余裸相对 token 兜底，避免同一片段被处理两次
    local_link_re = re.compile(
        r"(\]\(\s*<?)([^)>\s#?]+)((?:[?#][^)>\s]*)?>?\s*\))"
        r"|(?<![A-Za-z0-9_./-])(\.\.?/[A-Za-z0-9_./-]+)"
    )

    def _replace(match: re.Match[str]) -> str:
        if match.group(1) is not None:
            target = match.group(2)
            if target.startswith(("http://", "https://", "mailto:", "#", "/")) or ":" in target:
                return match.group(0)
            return f"{match.group(1)}{_resolve(target)}{match.group(3)}"
        return _resolve(match.group(4))

    # 逐行改写：围栏代码块（含开/闭栏行）原样保留，避免篡改文档示例
    fence = FenceState()
    rewritten: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        ending = line[len(stripped):]
        if fence.feed(stripped):
            rewritten.append(line)
            continue
        new_line = stripped
        for pat, new_rel in exact_patterns:
            new_line = pat.sub(new_rel, new_line)
        if need_relink:
            new_line = local_link_re.sub(_replace, new_line)
        rewritten.append(new_line + ending)
    return "".join(rewritten)


def is_protected_task_asset(rel: str) -> bool:
    """task 核心资产：task.md / progress.md / handoff.json / review_*.md（只读报告，不改写）。"""
    for prefix in ("docs/tasks/", "docs/archive/tasks/"):
        if rel.startswith(prefix):
            name = rel.rsplit("/", 1)[-1]
            return name in (PROGRESS_FILE, "task.md", "handoff.json") or (
                name.startswith("review_") and name.endswith(".md")
            )
    return False


def collect_tracked_project_files(consumer: Path) -> list[str]:
    """消费项目已跟踪的非模板文件 + 固定维护文档，排除模板/Git/依赖/缓存目录。"""
    res = _git(consumer, "-c", "core.quotePath=false", "ls-files", "-z")
    raw_items = list(res.stdout.split("\0")) if res.returncode == 0 else []
    for extra in ("README.md", "docs/handoff.md", "docs/specs_index.md"):
        if (consumer / extra).exists() or (consumer / extra).is_symlink():
            raw_items.append(extra)
    seen: set[str] = set()
    rels: list[str] = []
    for rel in raw_items:
        if not rel or rel in seen:
            continue
        seen.add(rel)
        parts = PurePosixPath(rel).parts
        if rel in _RETIRED_DOC_RELS:
            continue
        if any(part in _SKIP_SCAN_DIRS for part in parts) or rel.startswith(".repo_template/"):
            continue
        rels.append(rel)
    return sorted(rels)


def build_reference_reports(
    *,
    renumbered: list[RenumberDecision],
    entity_final_texts: dict[str, tuple[str, str]],
    outside_doc_texts: dict[str, str],
    protected_asset_texts: dict[str, str],
) -> tuple[list[str], list[str]]:
    """生成裸编号待复核引用与受保护 task 历史引用报告，不改写、不声称引用已修复。"""
    if not renumbered:
        return [], []
    by_orig: dict[str, list[RenumberDecision]] = {}
    for dec in renumbered:
        by_orig.setdefault(dec.orig_id, []).append(dec)
    mapping: dict[str, str] = {}
    for orig_id, decs in sorted(by_orig.items()):
        kept = decs[0].kept_source_rel or orig_id
        pairs = "，".join(f"{d.source_rel} -> {d.final_id} ({d.target_rel})" for d in decs)
        mapping[orig_id] = f"{orig_id} 保留 {kept}；{pairs}"
    id_re = re.compile(
        r"(?<![A-Za-z0-9_])("
        + "|".join(re.escape(oid) for oid in sorted(by_orig))
        + r")(?![0-9A-Za-z_])"
    )
    old_rels = {
        d.source_rel: d.orig_id
        for d in renumbered
        if not d.from_ledger
    }
    bare_reports: list[str] = []
    protected_reports: list[str] = []

    normal_docs = [
        (rel, own_id, text)
        for rel, (own_id, text) in sorted(entity_final_texts.items())
    ] + [(rel, None, text) for rel, text in sorted(outside_doc_texts.items())]
    for rel, own_id, text in normal_docs:
        for lineno, line in enumerate(text.splitlines(), 1):
            for oid in sorted(set(id_re.findall(line))):
                if oid != own_id:
                    bare_reports.append(
                        f"裸编号待复核引用: {rel}:{lineno} 含 {oid}（{mapping[oid]}）"
                    )
    for rel, text in sorted(protected_asset_texts.items()):
        for lineno, line in enumerate(text.splitlines(), 1):
            matched = set(id_re.findall(line))
            for old_rel, oid in old_rels.items():
                if old_rel in line:
                    matched.add(oid)
            for oid in sorted(matched):
                protected_reports.append(
                    f"受保护历史引用（只读不改写）: {rel}:{lineno} 含 {oid}（{mapping[oid]}）"
                )
    return bare_reports, protected_reports
