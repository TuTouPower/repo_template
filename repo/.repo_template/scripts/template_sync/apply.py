"""apply 子命令：事务内写盘主体、dirty 源检查与改动清单。"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from export_template import export_template, is_non_distributable
from repo_task.file_transaction import SYNC_TRANSACTION_DIR, open_transactions, state_transaction
from repo_task.validation import load_policy

from . import context as ctx
from .assets import retired_template_warnings, sync_dir, sync_file
from .common import _git, _now_iso, consumer_locked, finished_legacy_transactions
from .gates import _run_tests
from .preflight import sync_preflight
from .migrations import MIGRATIONS
from .entity_storage_migration import validate_current_entities
from .shared_files import (
    _apply_shared_unit,
    assert_format_exempt,
    merge_gitignore,
    merge_mcp,
    merge_prettierignore,
)
from .skills_links import repair_symlinks, sync_opencode_commands
from .source import _as_product, assert_not_self, resolve_src, src_dirty
from .state import missing_state_message, prompt_substrings, read_state, write_state


def _assert_distributable_tree(src: Path) -> None:
    """dirty 源绕过 export_template，写盘前用同一策略扫描将同步的产物树。"""
    candidates: list[Path] = []
    for rel in ctx.HARD_SYNC_DIRS:
        base = src / rel
        if base.is_dir():
            candidates.extend(path for path in base.rglob("*") if not path.is_dir())
    for rel in ctx.HARD_SYNC_FILES:
        path = src / rel
        if path.exists():
            candidates.append(path)
    for path in candidates:
        rel = path.relative_to(src)
        if any(
            part in ctx.NOISE_NAMES or part in ctx.PROTECT_NAMES or part.endswith(".pyc")
            for part in rel.parts
        ):
            continue
        relpath = rel.as_posix()
        if is_non_distributable(relpath):
            raise ctx.SyncError(
                f"模板源含不可分发文件：{relpath}；dirty 同步拒绝。"
                "请删除或移出产物树后重试"
            )


def _preflight(src: Path, sync: ctx.SyncContext):
    status = _git(sync.consumer, "status", "--porcelain", "--untracked-files=all")
    if status.returncode or status.stdout.strip():
        raise ctx.SyncError("同步前工作区与暂存区必须完全干净（含未跟踪文件）；先处理现有改动")
    unfinished = open_transactions(sync.consumer)
    if unfinished:
        raise ctx.SyncError(f"存在未结束或损坏文件事务：{unfinished}；先恢复或 accept")
    finished_legacy_transactions(sync)
    from md_format import find_md_kx
    if not find_md_kx():
        raise ctx.SyncError("找不到 md_kx；安装 md-kx 或修正 PATH 后重试")
    result = sync_preflight(src, sync)
    if result.error:
        raise ctx.SyncError(result.error)
    return [migration for migration in MIGRATIONS if migration.kind in dict(result.migrations)]


def _transaction_roots(paths):
    roots = []
    for path in sorted(set(paths), key=lambda value: (value.count("/"), value)):
        if not any(path == root or path.startswith(root + "/") for root in roots):
            roots.append(path)
    return roots


def _changed_markdown(sync, paths):
    from md_format import collect_changed
    from repo_task.context import RepoContext
    return [rel for rel in collect_changed(repo=RepoContext.for_root(sync.consumer))
            if any(rel == root or rel.startswith(root + "/") for root in paths)]


def _format_markdown(sync, paths):
    from md_format import format_paths
    from repo_task.context import RepoContext
    format_paths(_changed_markdown(sync, paths), repo=RepoContext.for_root(sync.consumer))


def _validate_result(src, sync, paths):
    from md_format import format_paths
    from repo_task.context import RepoContext
    result = sync_preflight(src, sync)
    if result.error or result.migrations:
        raise ctx.SyncError(f"同步后迁移或数据校验未通过：{result.error or result.migrations}")
    validate_current_entities(sync)
    load_policy(sync.consumer)
    drift = format_paths(_changed_markdown(sync, paths), repo=RepoContext.for_root(sync.consumer), check=True)
    if drift:
        raise ctx.SyncError("Markdown 格式漂移：" + "、".join(drift))
    assert_format_exempt(sync.consumer)


def _apply_sync_write(
    src: Path, decisions: dict[str, str], changed: set[Path], blocked: list[str],
    sync: ctx.SyncContext,
) -> None:
    """apply 的写盘主体；OSError 由调用方回滚（F16）。"""
    for rel in ctx.HARD_SYNC_DIRS:
        s, d = src / rel, sync.consumer / rel
        if s.exists():
            sync_dir(s, d, changed, sync=sync)
        elif d.exists():
            shutil.rmtree(d)
            changed.add(d)
    for rel in ctx.HARD_SYNC_FILES:
        sync_file(src / rel, sync.consumer / rel, changed, sync)

    reports = repair_symlinks(changed, sync=sync)
    for r in reports:
        print(f"提示: {r}")
    for r in sync_opencode_commands(changed, sync=sync):
        print(f"提示: {r}")

    if (src / ".gitignore").exists():
        info = merge_gitignore(src, blocked, changed, sync=sync)
        if info["added"]:
            print(f".gitignore 追加 {len(info['added'])} 条（模板独有）")
        if info["removed"]:
            print(f".gitignore 删除 {len(info['removed'])} 条（prompt 禁止 ignore）")
    prettier_info = merge_prettierignore(changed, sync=sync)
    if prettier_info["added"]:
        print(f".prettierignore 追加 {len(prettier_info['added'])} 条（模板自有路径豁免 prettier）")
    for warning in retired_template_warnings(sync=sync):
        print(f"提示: {warning}")
    merge_mcp(src, changed, sync=sync)

    for unit in ctx.SHARED_FILES:
        if unit == ".gitignore":
            continue
        decision = decisions.get(unit)
        _apply_shared_unit(unit, decision, src, changed, sync=sync)


def _print_changed(paths: set[Path], header: str, sync: ctx.SyncContext) -> None:
    print(header)
    for path in sorted(paths, key=str):
        print(sync.rel(path))
    if not paths:
        print("（无改动）")


@consumer_locked
def cmd_apply(args: argparse.Namespace) -> int:
    sync: ctx.SyncContext = args.sync
    state = read_state(sync)
    if not state:
        raise ctx.SyncError(missing_state_message(sync))
    src = resolve_src(state, sync=sync)
    assert_not_self(src, sync=sync)
    dirty = src_dirty(src)
    if dirty and not getattr(args, "allow_dirty_source", False):
        raise ctx.SyncError("模板源 dirty；正式同步必须使用 clean commit。开发验证须显式 --allow-dirty-source")
    source_commit = _git(src, "rev-parse", "HEAD").stdout.strip()
    expected = getattr(args, "expected_commit", None)
    if expected and source_commit != expected:
        raise ctx.SyncError(f"来源版本已变化：预期 {expected}，实际 {source_commit}；重新 plan")
    if (sync.consumer / "AGENTS.md").is_symlink():
        raise ctx.SyncError("apply 不支持软链 AGENTS.md，先替换为真实文件后重跑")
    changed: set[Path] = set()
    try:
        migrations = _preflight(src, sync)
    except ctx.SyncError as error:
        print(f"错误: {error}", file=sys.stderr)
        return 1
    transaction = state_transaction(sync.consumer, SYNC_TRANSACTION_DIR)
    paths = [".repo_template", ".agents/skills", ".claude/skills",
             ".opencode/commands", ".md_kx.toml", ".gitignore", ".prettierignore",
             "AGENTS.md",
             *ctx.MCP_CANDIDATES]
    paths = _transaction_roots(paths + [root for migration in migrations for root in migration.ROOTS])
    state_paths = sorted({sync.state_path.name, *(root for migration in migrations for root in migration.STATE_ROOTS)})
    try:
        transaction.begin(paths, state_paths=state_paths)
    except (ValueError, OSError) as error:
        if transaction.read() and transaction.read()["status"] == "preparing":
            transaction.rollback(automatic=True)
        raise ctx.SyncError(str(error)) from error
    try:
        data = transaction.read()
        data["base_head"] = _git(sync.consumer, "rev-parse", "HEAD").stdout.strip()
        transaction.save(data)
        def write_snapshot(snapshot):
            migration_sync = replace(sync, checkout_override=src)
            for migration in migrations:
                for report in migration.execute(migration_sync, snapshot):
                    print(f"{migration.kind}: {report}")
            migrated_state = read_state(sync) or state
            _apply_sync_write(snapshot, getattr(args, "decisions", None) or {}, changed,
                              prompt_substrings(migrated_state), sync=sync)
            return migrated_state

        if dirty:
            _assert_distributable_tree(src)
            state = write_snapshot(src)
            _format_markdown(sync, paths)
            _validate_result(src, sync, paths)
        else:
            with tempfile.TemporaryDirectory(prefix="source_", dir=transaction.backup) as directory:
                snapshot = Path(directory)
                export_template(src, snapshot, source_commit)
                _as_product(snapshot)
                state = write_snapshot(snapshot)
                _format_markdown(sync, paths)
                _validate_result(snapshot, sync, paths)
        if not args.skip_tests:
            if not _run_tests(sync.consumer):
                raise ctx.SyncError("契约测试失败（pytest .repo_template/tests/ -q -m contract）")
        else:
            print("跳过模板契约测试（--skip-tests，仅开发验证；项目策略仍须通过校验）")
        if not dirty:
            head = source_commit
            state["last_synced_commit"] = head
            # Portable provenance. Local source paths and private policies stay local.
            from .source import require_template_source as _require_ts
            _kind, _value = _require_ts(state)
            remote = _value if _kind == "url" else _git(src, "remote", "get-url", "origin").stdout.strip()
            if remote and not re.match(r"^(?:https?://|ssh://|git://|[^/\s]+@[^:]+:)", remote):
                remote = None
            if remote and re.match(r"https?://[^/]*@", remote):
                remote = None
            if remote:
                parsed = urlsplit(remote)
                if parsed.password or parsed.query or parsed.fragment:
                    remote = None
            manifest = {"version": 1, "resolved_commit": head, "source_url": remote}
            (sync.consumer / ".repo_template/sync_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            changed.add(sync.consumer / ".repo_template/sync_manifest.json")
        state["last_synced_at"] = _now_iso()
        state["resolved_checkout"] = str(src)
        write_state(state, sync=sync)
        transaction.applied(verified=not args.skip_tests)
    except BaseException as error:
        # 可捕获中断同样先恢复基线；随后重新抛出，保留原退出语义。
        rollback_ok = False
        try:
            transaction.rollback(automatic=True)
            rollback_ok = True
        except Exception as rb_err:
            print(f"WARNING: 事务自动回滚失败（{rb_err}），现场可能残留未恢复文件", file=sys.stderr)

        if rollback_ok:
            _print_changed(changed, "\n=== 本次写入清单（失败，已回滚，仅供排查）===", sync=sync)
            print(f"错误: {error}；已完整回滚本次写入与 state。"
                  f"回滚备份保留在 {SYNC_TRANSACTION_DIR}", file=sys.stderr)
        else:
            _print_changed(changed, "\n=== 本次写入清单（失败且回滚未完成，现场可能残留混合状态）===", sync=sync)
            print(f"错误: {error}；自动回滚未完全成功，现场可能存在混合版本工具文件！"
                  f"备份保留在 {SYNC_TRANSACTION_DIR}，请人工执行 python3 ... repo_sync transaction rollback --confirm-restore 恢复基线", file=sys.stderr)
        if not isinstance(error, Exception):
            raise
        return 1
    tracked = _git(sync.consumer, "diff", "--name-only", "-z", "HEAD")
    untracked = _git(sync.consumer, "ls-files", "--others", "--exclude-standard", "-z")
    changed.update(sync.consumer / rel for rel in (tracked.stdout + untracked.stdout).split("\0") if rel)
    print(f"来源 SHA: {source_commit}")
    _print_changed(changed, "\n=== 改动路径清单（commit 门禁点名 add）===", sync=sync)
    print("state 推进：last_synced_commit=" + ("已更新" if not dirty else "SRC dirty，未推进"))
    print("事务 applied；拒绝同步可 transaction rollback，提交并确认后 transaction accept")
    return 0
