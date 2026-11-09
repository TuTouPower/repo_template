#!/usr/bin/env python3
"""pending.py - 待办条目的分配、列举与状态管理入口。

一条目一文件，所有状态由文件元数据 (front matter) 表达，存放在固定扁平目录：

  docs/pending/pNNN_slug.md

支持状态：
  - todo   : 待办（默认）
  - parked : 暂搁（不办，须有非空 reason）
  - closed : 闭环（终态，须有非空 reason 与合法 resolution）

状态流转：
  - todo -> parked -> todo（parked 必须先 revive 回 todo，才能 closed）
  - todo -> closed（closed 为终态，不可逆）

用法：
  python3 .repo_template/scripts/pending.py new <slug> [--title TITLE] [--kind {entry,bug}]
  python3 .repo_template/scripts/pending.py list [--state {todo,parked,closed,all}]
  python3 .repo_template/scripts/pending.py park pNNN ... --reason "..." [--write]
  python3 .repo_template/scripts/pending.py revive pNNN ... [--write]
  python3 .repo_template/scripts/pending.py close pNNN ... --reason "..." [--fix-ref REF] [--resolution RES] [--write]
  python3 .repo_template/scripts/repo_sync.py migrate pending-ledger [--write]

旧仓残留 docs/pending.md 单文件总账时，先用 repo_sync migrate pending-ledger 显式拆成一条目一文件，
提交迁移结果后再分配新编号；编号分配本身不解析旧正文。
"""

from __future__ import annotations

import argparse
from repo_task.context import RepoContext, TaskDataError
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _id_scan import (
    IdScanError,
    _assert_no_symlink,
    allocate,
    canonical_entry_id,
    entry_file_pattern,
    id_lock,
    parse_entry_file_name,
    slug_from_title,
    SLUG_RE,
)
from md_format import MdFormatError, format_file
from repo_task.documents import atomic_write_text, parse_front_matter_text

PREFIX = "p"
SCAN_DIRS = ("docs/pending",)
ID_RE = re.compile(r"^p(0[0-9]{2}|[1-9][0-9]{2,})$")
ENTRY_FILE_RE = re.compile(entry_file_pattern(PREFIX))
# 宽松匹配：同号但 padding/字符非法的文件，用于格式错误诊断而非静默“未找到”。
_LOOSE_ENTRY_RE = re.compile(r"^p([0-9]+)_.*\.md$")
HANDLE_RE = re.compile(r"^[ \t]*-[ \t]*处理[ \t]*[:：].*$")
PARKED_RE = re.compile(r"^[ \t]*-[ \t]*暂搁[ \t]*[:：].*$")
SCRATCH_REF_RE = re.compile(r"\.scratch/[^\s`'\"<>()\[\]{}，。；：、！？]+")
_SCRATCH_TRAILING = ".,;:!?，。；：、！？'\"`)]}>"

ALLOWED_FM_KEYS = frozenset({"pid", "slug", "status", "resolution", "reason", "fix_ref"})
VALID_STATUSES = frozenset({"todo", "parked", "closed"})
VALID_RESOLUTIONS = frozenset({"resolved", "transferred", "obsolete", "duplicate"})

ENTRY_TEMPLATE = """---
pid: "{id}"
slug: "{slug}"
status: "todo"
---
# {id} {title}

- 来源：{tNNN 遗留 / 用户提出 / 技术债自查}
- 内容：{该做什么、为什么现在没做}
- 处理：未开
"""

BUG_TEMPLATE = """---
pid: "{id}"
slug: "{slug}"
status: "todo"
---
# {id} {title}

- 现象：{发生什么预期外行为}
- 影响：{受影响的功能和范围；含已确认同类位点并集}
- 根因：{可验证机制与分类；已确认同类位点清单或「已扫无同类」}
- 测试缺口：{现有测试为何漏过、应补哪层验证；须覆盖各已确认位点}
- 线索：{`.scratch/` 最小复现路径}
- 处理：未开
"""


def _rel(path: Path, *, repo: RepoContext) -> str:
    try:
        return path.relative_to(repo.root).as_posix()
    except ValueError:
        return path.as_posix()


def parse_ids(specs: list[str]) -> list[int]:
    """解析 pNNN 列表，返回升序去重后的整数列表。"""
    out: list[int] = []
    for spec in specs:
        match = ID_RE.match(spec)
        if not match:
            raise IdScanError(f"非法编号：{spec!r}（期望 pNNN）")
        out.append(int(match.group(1)))
    return sorted(set(out))


def validate_pending_front_matter(
    fm: dict[str, Any],
    *,
    filename: str | None = None,
    source: str = "",
) -> None:
    src_prefix = f"{source}: " if source else ""
    extra_keys = set(fm.keys()) - ALLOWED_FM_KEYS
    if extra_keys:
        raise IdScanError(f"{src_prefix}包含未知 front matter 字段：{sorted(extra_keys)}")

    pid = fm.get("pid")
    if not pid or not ID_RE.match(str(pid)):
        raise IdScanError(f"{src_prefix}pid 非法（期望 pNNN）：{pid!r}")

    slug = fm.get("slug")
    if not slug or not SLUG_RE.match(str(slug)):
        raise IdScanError(f"{src_prefix}slug 非法（须 snake_case）：{slug!r}")

    if filename is not None:
        parsed = parse_entry_file_name(PREFIX, filename)
        if parsed is None:
            raise IdScanError(f"{src_prefix}文件名不符合规范 pNNN_slug.md：{filename}")
        fn_pid = canonical_entry_id(PREFIX, parsed[0])
        fn_slug = parsed[1]
        if pid != fn_pid:
            raise IdScanError(f"{src_prefix}元数据 pid ({pid}) 与文件名编号 ({fn_pid}) 不一致")
        if slug != fn_slug:
            raise IdScanError(f"{src_prefix}元数据 slug ({slug}) 与文件名 slug ({fn_slug}) 不一致")

    status = fm.get("status")
    if status not in VALID_STATUSES:
        raise IdScanError(f"{src_prefix}status 非法：{status!r}（期望 {sorted(VALID_STATUSES)}）")

    for k, v in fm.items():
        if isinstance(v, str) and ("\n" in v or "\r" in v):
            raise IdScanError(f"{src_prefix}字段 {k} 不能包含换行符")

    if status == "todo":
        if "resolution" in fm:
            raise IdScanError(f"{src_prefix}status=todo 时不得包含 resolution 字段")
        if "reason" in fm:
            raise IdScanError(f"{src_prefix}status=todo 时不得包含 reason 字段")
        if "fix_ref" in fm:
            raise IdScanError(f"{src_prefix}status=todo 时不得包含 fix_ref 字段")

    elif status == "parked":
        if "resolution" in fm:
            raise IdScanError(f"{src_prefix}status=parked 时不得包含 resolution 字段")
        if "fix_ref" in fm:
            raise IdScanError(f"{src_prefix}status=parked 时不得包含 fix_ref 字段")
        reason = (fm.get("reason") or "").strip()
        if not reason:
            raise IdScanError(f"{src_prefix}status=parked 时必须提供非空 reason 说明暂搁理由")

    elif status == "closed":
        resolution = fm.get("resolution")
        if resolution not in VALID_RESOLUTIONS:
            raise IdScanError(
                f"{src_prefix}status=closed 时 resolution 非法：{resolution!r}（期望 {sorted(VALID_RESOLUTIONS)}）"
            )
        reason = (fm.get("reason") or "").strip()
        if not reason:
            raise IdScanError(f"{src_prefix}status=closed 时必须提供非空 reason 说明闭环依据")


def dump_pending_front_matter(fm: dict[str, str]) -> str:
    key_order = ["pid", "slug", "status", "resolution", "reason", "fix_ref"]
    lines = ["---"]
    for key in key_order:
        if key in fm and fm[key] is not None:
            val = str(fm[key])
            escaped = (
                val.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\n", "\\n")
                .replace("\r", "\\r")
            )
            lines.append(f'{key}: "{escaped}"')
    lines.append("---")
    return "\n".join(lines) + "\n"


def _read_and_parse_entry(path: Path, *, repo: RepoContext) -> tuple[dict[str, str], str, str]:
    """读取并解析 pending 文件的 front matter 与正文，校验严格字段，并返回内容 SHA256。"""
    if path.is_symlink():
        raise IdScanError(f"拒绝读取软链条目：{_rel(path, repo=repo)}")
    _assert_no_symlink(repo.root, path)
    if not path.is_file():
        raise IdScanError(f"文件不存在：{_rel(path, repo=repo)}")
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise IdScanError(f"读取文件失败（{_rel(path, repo=repo)}）：{e}") from e
    digest = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
        fm, body = parse_front_matter_text(text, source=_rel(path, repo=repo))
    except Exception as e:
        raise IdScanError(f"解析 front matter 失败（{_rel(path, repo=repo)}）：{e}") from e
    validate_pending_front_matter(fm, filename=path.name, source=_rel(path, repo=repo))
    return fm, body, digest


def parse_pending_file(path: Path, *, repo: RepoContext) -> tuple[dict[str, str], str]:
    """读取并解析 pending 文件的 front matter 与正文，校验严格字段。"""
    fm, body, _ = _read_and_parse_entry(path, repo=repo)
    return fm, body


def open_scratch_references(repo: RepoContext) -> dict[str, str]:
    """未关闭 pending 条目引用的现存 scratch 路径 → 主人说明。

    只读取 todo / parked 条目的正文；引用必须写成 ``.scratch/...`` 形式、
    位于仓库内且目标存在。条目损坏（front matter 或正文解析失败）向上抛出，
    由清理器把 scratch 整体视为不可判定并跳过。
    """
    pending_dir = repo.root / "docs/pending"
    if pending_dir.is_symlink():
        raise IdScanError(f"拒绝经软链目录读取 pending 引用：{_rel(pending_dir, repo=repo)}")
    if not pending_dir.is_dir():
        return {}
    references: dict[str, list[str]] = {}
    for path in sorted(pending_dir.glob("*.md")):
        if parse_entry_file_name(PREFIX, path.name) is None:
            continue
        fm, body = parse_pending_file(path, repo=repo)
        if fm["status"] not in ("todo", "parked"):
            continue
        owner = f"{fm.get('pid', path.stem)}（{fm['status']}）"
        for match in SCRATCH_REF_RE.findall(body):
            rel = match.rstrip(_SCRATCH_TRAILING)
            if not rel or rel.rstrip("/") == ".scratch":
                continue
            parts = Path(rel).parts
            if Path(rel).is_absolute() or ".." in parts:
                continue
            target = repo.root / rel
            if not target.exists() and not target.is_symlink():
                continue
            owners = references.setdefault(rel, [])
            if owner not in owners:
                owners.append(owner)
    return {rel: "、".join(owners) for rel, owners in references.items()}


def _malformed_for_number(number: int, *, repo: RepoContext) -> list[Path]:
    """返回同号但文件名不规范的候选，用于诊断（不含严格命中）。"""
    bad: list[Path] = []
    if not (repo.root / "docs/pending").is_dir():
        return bad
    for path in sorted((repo.root / "docs/pending").glob("*.md")):
        if parse_entry_file_name(PREFIX, path.name) is not None:
            continue
        loose = _LOOSE_ENTRY_RE.match(path.name)
        if loose:
            try:
                if int(loose.group(1)) == number:
                    bad.append(path)
            except ValueError:
                continue
    return bad


def find_entry(number: int, *, repo: RepoContext) -> tuple[str, Path]:
    """在 docs/pending 中定位条目，返回 (status, path)。"""
    if (repo.root / "docs/pending").is_symlink():
        raise IdScanError(f"拒绝经软链目录定位条目：{_rel((repo.root / "docs/pending"), repo=repo)}")
    if not (repo.root / "docs/pending").is_dir():
        raise IdScanError(f"未找到 p{number:03d}（{_rel((repo.root / "docs/pending"), repo=repo)} 目录不存在）")
    _assert_no_symlink(repo.root, (repo.root / "docs/pending"))
    hits = sorted(
        path
        for path in (repo.root / "docs/pending").glob("*.md")
        if (parsed := parse_entry_file_name(PREFIX, path.name)) and parsed[0] == number
    )
    if not hits:
        bad = _malformed_for_number(number, repo=repo)
        if bad:
            joined = ", ".join(_rel(p, repo=repo) for p in bad)
            raise IdScanError(
                f"未找到 p{number:03d}；存在同号但文件名不规范：{joined}；"
                "须改成 pNNN_slug.md 规范命名（小写 snake_case，三位起）"
            )
        raise IdScanError(f"未找到 p{number:03d}")
    if len(hits) > 1:
        joined = ", ".join(_rel(p, repo=repo) for p in hits)
        raise IdScanError(f"p{number:03d} 同时存在多个文件：{joined}；请先解决同号多处")
    path = hits[0]
    if path.is_symlink():
        raise IdScanError(f"拒绝经软链条目写入：{_rel(path, repo=repo)}")
    _assert_no_symlink(repo.root, path)
    fm, _ = parse_pending_file(path, repo=repo)
    return fm["status"], path


def set_field_text(body: str, pattern: re.Pattern, line: str) -> str:
    """替换首个匹配行；无匹配则追加到末尾。"""
    lines = body.splitlines()
    for index, existing in enumerate(lines):
        if pattern.match(existing):
            lines[index] = line
            break
    else:
        lines.append(line)
    return "\n".join(lines).rstrip() + "\n"


def cmd_new(args: argparse.Namespace) -> None:
    repo = args.repo
    slug = args.slug or args.slug_opt
    if not slug and args.title:
        slug = slug_from_title(args.title)
    if not slug:
        sys.exit("错误: 必须提供 slug 或通过 --title 推导")
    if not SLUG_RE.match(slug):
        sys.exit(f"错误: slug 非法（须 snake_case）：{slug!r}")

    title = args.title or "{一句话简述}"
    template = BUG_TEMPLATE if args.kind == "bug" else ENTRY_TEMPLATE
    body = template.replace("{title}", title)
    try:
        path = allocate(
            repo.root,
            prefix=PREFIX,
            dirs=SCAN_DIRS,
            target_dir=(repo.root / "docs/pending"),
            slug=slug,
            body=body,
            formatter=format_file,
        )
    except (IdScanError, MdFormatError) as error:
        sys.exit(f"错误: {error}")
    print(_rel(path, repo=repo))


def cmd_list(args: argparse.Namespace) -> None:
    repo = args.repo
    if (repo.root / "docs/pending").is_symlink():
        raise IdScanError(f"拒绝经软链目录列举条目：{_rel((repo.root / "docs/pending"), repo=repo)}")
    if not (repo.root / "docs/pending").is_dir():
        print("(no entries)")
        return
    rows: list[tuple[str, str, str]] = []
    for path in sorted((repo.root / "docs/pending").glob("*.md")):
        if parse_entry_file_name(PREFIX, path.name) is None:
            loose = _LOOSE_ENTRY_RE.match(path.name)
            if loose or path.name.startswith("p"):
                sys.stderr.write(
                    f"警告：{_rel(path, repo=repo)} 文件名不符合规范 pNNN_slug.md，已跳过；"
                    "请改成规范命名后重试\n"
                )
            continue
        fm, body = parse_pending_file(path, repo=repo)
        status = fm["status"]
        if args.state != "all" and status != args.state:
            continue
        title = ""
        for line in body.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        rows.append((status, path.stem, title))
    if not rows:
        print("(no entries)")
        return
    for status, stem, title in sorted(rows, key=lambda row: row[1]):
        print(f"{status:<8} {stem:<40} {title}")


def _apply_state_updates(
    write: bool,
    plan: list[tuple[int, Path, dict[str, str], str, str]],
    note: str,
    *, repo: RepoContext,
) -> None:
    if not write:
        sys.stderr.write("dry-run；以下为拟定改动，加 --write 落盘。\n")
        for _num, path, new_fm, _new_body, _expected_sha in plan:
            target_status = new_fm["status"]
            sys.stderr.write(f"  {_rel(path, repo=repo)} → status: {target_status}  {note}\n")
        return

    modified: list[tuple[Path, bytes]] = []
    try:
        with id_lock(repo.root):
            snapshots: list[tuple[Path, str, bytes]] = []
            for number, path, new_fm, new_body, expected_sha in plan:
                curr_status, curr_path = find_entry(number, repo=repo)
                if curr_path.resolve() != path.resolve():
                    raise IdScanError(
                        f"p{number:03d} 在加锁前路径已变（当前 {_rel(curr_path, repo=repo)}）；请重试"
                    )
                raw = path.read_bytes()
                curr_sha = hashlib.sha256(raw).hexdigest()
                if curr_sha != expected_sha:
                    raise IdScanError(
                        f"p{number:03d} 在加锁前内容已被并发修改（内容摘要不一致）；请重试"
                    )
                validate_pending_front_matter(new_fm, filename=path.name, source=_rel(path, repo=repo))
                new_text = dump_pending_front_matter(new_fm) + new_body
                snapshots.append((path, new_text, raw))

            for index, (path, new_text, orig_bytes) in enumerate(snapshots, 1):
                try:
                    _assert_no_symlink(repo.root, path)
                    if path.is_symlink():
                        raise IdScanError(f"拒绝向软链条目写入：{_rel(path, repo=repo)}")
                    atomic_write_text(path, new_text)
                    modified.append((path, orig_bytes))
                    sys.stderr.write(f"已更新：{_rel(path, repo=repo)}\n")
                except Exception as error:
                    failures: list[tuple[Path, str]] = []
                    for mod_path, orig in reversed(modified):
                        try:
                            atomic_write_text(mod_path, orig.decode("utf-8"))
                        except Exception as rb_err:
                            failures.append((mod_path, str(rb_err)))
                    if failures:
                        details = "；".join(f"{_rel(p, repo=repo)}: {err}" for p, err in failures)
                        sys.stderr.write(
                            f"更新失败于第 {index} 条（{_rel(path, repo=repo)}）：{error}；"
                            f"成功回滚 {len(modified) - len(failures)}/{len(modified)} 条，未恢复：{details}\n"
                        )
                        raise IdScanError("更新失败且回滚不完整；请按上方未恢复条目人工处理") from error
                    sys.stderr.write(
                        f"更新失败于第 {index} 条（{_rel(path, repo=repo)}）：{error}；"
                        f"已回滚 {len(modified)} 条已更新条目（含正文与元数据）\n"
                    )
                    if isinstance(error, IdScanError):
                        raise
                    raise IdScanError(f"更新 I/O 操作异常：{error}") from error
    except IdScanError:
        raise


def cmd_park(args: argparse.Namespace) -> None:
    repo = args.repo
    reason = (args.reason or "").strip()
    if not reason:
        raise IdScanError("暂搁必须提供非空 --reason 说明理由")
    if "\n" in reason or "\r" in reason:
        raise IdScanError("reason 不能包含换行符（收到多行输入）")
    numbers = parse_ids(args.ids)
    plan: list[tuple[int, Path, dict[str, str], str, str]] = []
    for number in numbers:
        state, path = find_entry(number, repo=repo)
        if state != "todo":
            if state == "parked":
                raise IdScanError(f"p{number:03d} 当前在 parked；只有 todo 可暂搁")
            if state == "closed":
                raise IdScanError(f"p{number:03d} 当前在 closed（终态）；禁止暂搁")
            raise IdScanError(f"p{number:03d} 当前在 {state}；只有 todo 可暂搁")
        fm, body, expected_sha = _read_and_parse_entry(path, repo=repo)
        new_fm = {
            "pid": fm["pid"],
            "slug": fm["slug"],
            "status": "parked",
            "reason": reason,
        }
        new_body = set_field_text(body, PARKED_RE, f"- 暂搁：{reason}")
        new_body = set_field_text(new_body, HANDLE_RE, "- 处理：不办")
        plan.append((number, path, new_fm, new_body, expected_sha))

    _apply_state_updates(args.write, plan, note=f"暂搁：{reason}", repo=repo)


def cmd_revive(args: argparse.Namespace) -> None:
    repo = args.repo
    numbers = parse_ids(args.ids)
    plan: list[tuple[int, Path, dict[str, str], str, str]] = []
    for number in numbers:
        state, path = find_entry(number, repo=repo)
        if state != "parked":
            if state == "todo":
                raise IdScanError(f"p{number:03d} 当前在 todo；只有 parked 可复活")
            if state == "closed":
                raise IdScanError(f"p{number:03d} 当前在 closed（终态）；禁止复活")
            raise IdScanError(f"p{number:03d} 当前在 {state}；只有 parked 可复活")
        fm, body, expected_sha = _read_and_parse_entry(path, repo=repo)
        new_fm = {
            "pid": fm["pid"],
            "slug": fm["slug"],
            "status": "todo",
        }
        new_body = set_field_text(body, HANDLE_RE, "- 处理：未开")
        plan.append((number, path, new_fm, new_body, expected_sha))

    _apply_state_updates(args.write, plan, note="revive", repo=repo)


def cmd_close(args: argparse.Namespace) -> None:
    repo = args.repo
    reason = (args.reason or "").strip()
    if not reason:
        raise IdScanError("闭环必须提供非空 --reason，说明处理结果与闭环依据")
    resolution = getattr(args, "resolution", "resolved") or "resolved"
    if resolution not in VALID_RESOLUTIONS:
        raise IdScanError(f"非法 resolution: {resolution!r}（期望 {sorted(VALID_RESOLUTIONS)}）")
    fix_ref = (args.fix_ref or "").strip()
    for name, val in (("fix_ref", fix_ref), ("reason", reason), ("resolution", resolution)):
        if "\n" in val or "\r" in val:
            raise IdScanError(f"{name} 不能包含换行符（收到多行输入）")

    ref_part = f"{fix_ref}；" if fix_ref else ""
    note = f"处理：{ref_part}resolution={resolution}；reason={reason}"

    numbers = parse_ids(args.ids)
    plan: list[tuple[int, Path, dict[str, str], str, str]] = []
    for number in numbers:
        state, path = find_entry(number, repo=repo)
        if state != "todo":
            if state == "closed":
                raise IdScanError(f"p{number:03d} 已在 closed；禁止重复闭环")
            if state == "parked":
                raise IdScanError(f"p{number:03d} 在 parked（暂搁而非闭环）；如需闭环请先 revive")
            raise IdScanError(f"p{number:03d} 当前在 {state}；只有 todo 可闭环")
        fm, body, expected_sha = _read_and_parse_entry(path, repo=repo)
        new_fm = {
            "pid": fm["pid"],
            "slug": fm["slug"],
            "status": "closed",
            "resolution": resolution,
            "reason": reason,
        }
        if fix_ref:
            new_fm["fix_ref"] = fix_ref
        new_body = set_field_text(body, HANDLE_RE, f"- {note}")
        plan.append((number, path, new_fm, new_body, expected_sha))

    _apply_state_updates(args.write, plan, note=note, repo=repo)


def main(argv: list[str] | None = None, *, repo: RepoContext | None = None) -> None:
    parser = argparse.ArgumentParser(description="待办条目分配与状态管理")
    parser.add_argument("--root", type=Path, help="目标仓库根（默认脚本所在仓库）")
    sub = parser.add_subparsers(dest="command", required=True)

    new_parser = sub.add_parser("new", help="在 docs/pending 创建 status: todo 条目")
    new_parser.add_argument("slug", nargs="?", default=None, help="snake_case 主题标识")
    new_parser.add_argument("--slug", dest="slug_opt", default=None, help="snake_case 主题标识")
    new_parser.add_argument("--title", default=None, help="条目标题（未提供 slug 时由 title 推导）")
    new_parser.add_argument(
        "--kind", choices=("entry", "bug"), default="entry", help="条目模板类型"
    )
    new_parser.set_defaults(func=cmd_new)

    list_parser = sub.add_parser("list", help="列举条目")
    list_parser.add_argument(
        "--state", choices=("todo", "parked", "closed", "all"), default="todo", help="按状态过滤"
    )
    list_parser.set_defaults(func=cmd_list)

    park_parser = sub.add_parser("park", help="暂搁：todo → parked")
    park_parser.add_argument("ids", nargs="+", metavar="pNNN")
    park_parser.add_argument("--reason", required=True, help="暂搁理由")
    park_parser.add_argument("--write", action="store_true", help="落盘（默认 dry-run）")
    park_parser.set_defaults(func=cmd_park)

    revive_parser = sub.add_parser("revive", help="复活：parked → todo")
    revive_parser.add_argument("ids", nargs="+", metavar="pNNN")
    revive_parser.add_argument("--write", action="store_true", help="落盘（默认 dry-run）")
    revive_parser.set_defaults(func=cmd_revive)

    close_parser = sub.add_parser("close", help="闭环：todo → closed")
    close_parser.add_argument("ids", nargs="+", metavar="pNNN")
    close_parser.add_argument(
        "--fix-ref", metavar="REF", help="可选引用，如 main、commit、PR、条目或 task；不要求 task ID"
    )
    close_parser.add_argument(
        "--resolution", choices=("resolved", "transferred", "obsolete", "duplicate"), default="resolved"
    )
    close_parser.add_argument("--reason", required=True, help="必填备注：处理结果与闭环依据，不能只填空白")
    close_parser.add_argument("--write", action="store_true", help="落盘（默认 dry-run）")
    close_parser.set_defaults(func=cmd_close)

    args = parser.parse_args(argv)
    args.repo = repo if repo is not None else (RepoContext.for_root(args.root) if args.root else RepoContext.from_code_location(__file__))
    if args.command == "new":
        if not args.slug and not args.slug_opt and not args.title:
            new_parser.error("必须提供 slug 或 --title")
    try:
        args.func(args)
    except (IdScanError, TaskDataError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
