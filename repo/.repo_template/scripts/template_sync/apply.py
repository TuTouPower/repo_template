"""apply 子命令：事务内写盘主体、dirty 源检查与改动清单。"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from export_template import export_template, is_non_distributable
from repo_task.file_transaction import FileTransaction
from repo_task.validation import load_policy

from . import context as ctx
from .agents_merge import _agents_parts, _replace_agents_force
from .assets import retired_template_warnings, sync_dir, sync_file
from .common import _git, _now_iso, consumer_locked
from .gates import _run_tests
from .migrate import KINDS, migrate_command, migration_findings
from .shared_files import _apply_shared_unit, merge_gitignore, merge_mcp, merge_prettierignore
from .skills_links import repair_symlinks, sync_opencode_commands
from .source import _as_product, assert_not_self, resolve_src, src_dirty
from .state import prompt_substrings, read_state, require_no_legacy_state, write_state
from .specs_index_migration import specs_index_report


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


def _require_current_schema(src: Path, sync: ctx.SyncContext) -> None:
    """日常 apply 只接受当前 schema；旧布局必须先走显式 migrate（A10）。

    与 status / plan / migrate 共用 migration_findings 这一单一可处理集合，
    消除「plan 显示一致但 apply 阻断」。引导命令基于固定来源 product 与消费仓身份，
    不再指向旧消费仓本地脚本。
    """
    entity_problems = migration_findings("entity-storage", sync)
    if entity_problems:
        raise ctx.SyncError(
            "检测到旧实体存储布局；apply 不再自动迁移。先运行 "
            f"`{migrate_command(src, sync, 'entity-storage')}`：\n- "
            + "\n- ".join(entity_problems)
        )
    problems: list[str] = []
    for kind in KINDS:
        if kind == "entity-storage":
            continue
        problems.extend(
            f"{kind} 旧布局：{item}"
            for item in migration_findings(kind, sync, src=src)
        )
    if problems:
        raise ctx.SyncError(
            "检测到旧布局；apply 不再自动迁移。先运行 "
            f"`{migrate_command(src, sync, 'all')}`（或按类型单项迁移）：\n- "
            + "\n- ".join(problems)
        )


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
    require_no_legacy_state(sync)
    state = read_state(sync)
    if not state:
        raise ctx.SyncError("未初始化：无同步来源；使用 --source 或 init --source <path|url>")
    src = resolve_src(state, sync=sync)
    assert_not_self(src, sync=sync)
    dirty = src_dirty(src)
    if dirty and not getattr(args, "allow_dirty_source", False):
        raise ctx.SyncError("模板源 dirty；正式同步必须使用 clean commit。开发验证须显式 --allow-dirty-source")
    source_commit = _git(src, "rev-parse", "HEAD").stdout.strip()
    expected = getattr(args, "expected_commit", None)
    if expected and source_commit != expected:
        raise ctx.SyncError(f"来源版本已变化：预期 {expected}，实际 {source_commit}；重新 plan")
    changed: set[Path] = set()
    try:
        _require_current_schema(src, sync=sync)
    except ctx.SyncError as error:
        print(f"错误: {error}", file=sys.stderr)
        return 1
    if (sync.consumer / "AGENTS.md").is_symlink():
        raise ctx.SyncError("apply 不支持软链 AGENTS.md，先替换为真实文件后重跑")
    transaction = FileTransaction(sync.consumer, ".scratch/repo_sync_rollback")
    paths = [".repo_template", ".agents/skills", ".claude/skills", ".claude/settings.json",
             ".claude/hooks", ".opencode/commands", ".md_kx.toml", ".gitignore", ".prettierignore",
             "AGENTS.md", "docs/tasks", "docs/blueprint/conventions.md",
             "docs/specs_index.md",
             *ctx.MCP_CANDIDATES]
    try:
        transaction.begin(paths)
    except ValueError as error:
        raise ctx.SyncError(str(error)) from error
    try:
        template_agents = None
        if dirty:
            _assert_distributable_tree(src)
            if (src / "AGENTS.md").exists():
                template_agents = (src / "AGENTS.md").read_text(encoding="utf-8")
            _apply_sync_write(
                src, getattr(args, "decisions", None) or {}, changed, prompt_substrings(state), sync=sync
            )
        else:
            with tempfile.TemporaryDirectory(prefix="repo_sync_source_", dir=sync.consumer / ".scratch") as directory:
                snapshot = Path(directory)
                export_template(src, snapshot, source_commit)
                _as_product(snapshot)  # Fail closed before writing if export is incomplete.
                if (snapshot / "AGENTS.md").exists():
                    template_agents = (snapshot / "AGENTS.md").read_text(encoding="utf-8")
                _apply_sync_write(
                    snapshot, getattr(args, "decisions", None) or {}, changed,
                    prompt_substrings(state), sync=sync,
                )
        agents_file = getattr(args, "agents_file", None)
        if agents_file:
            candidate = Path(agents_file).read_text(encoding="utf-8")
            current = (sync.consumer / "AGENTS.md").read_text(encoding="utf-8")
            try:
                current_parts = _agents_parts(current)
            except ctx.SyncError as error:
                msg = str(error)
                if not msg.startswith("消费仓 "):
                    msg = f"消费仓 {msg}"
                raise ctx.SyncError(msg) from error
            try:
                candidate_parts = _agents_parts(candidate)
            except ctx.SyncError as error:
                msg = str(error)
                if not msg.startswith("候选 "):
                    msg = f"候选 {msg}"
                raise ctx.SyncError(msg) from error
            if candidate_parts["intro"][2] != current_parts["intro"][2]:
                raise ctx.SyncError("AGENTS 语义合并不得修改项目介绍")
            if template_agents is None:
                raise ctx.SyncError("模板快照缺 AGENTS.md，不能应用语义合并")
            result, *_ = _replace_agents_force(template_agents, candidate)
            (sync.consumer / "AGENTS.md").write_text(result, encoding="utf-8")
            changed.add(sync.consumer / "AGENTS.md")
        load_policy(sync.consumer)
        specs_problems = specs_index_report(sync=sync)
        if specs_problems:
            for problem in specs_problems:
                print(f"specs_index 契约违规: {problem}")
            raise ctx.SyncError(
                "docs/specs_index.md 契约违规（同步中止）：" + "；".join(specs_problems)
            )
        if not args.skip_tests:
            if not _run_tests(sync.consumer):
                raise ctx.SyncError("契约测试失败（pytest .repo_template/tests/ -q -m contract）")
        else:
            print("跳过模板契约测试（--skip-tests，仅开发验证；项目策略仍须通过校验）")
        if not dirty:
            head = source_commit
            state["last_synced_commit"] = head
            # Portable provenance. Local source paths and private policies stay local.
            source = state["template_source"]
            remote = source["value"] if source["kind"] == "url" else _git(src, "remote", "get-url", "origin").stdout.strip()
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
    except (OSError, ValueError, ctx.SyncError, subprocess.SubprocessError) as error:
        rollback_ok = False
        try:
            transaction.rollback(automatic=True)
            rollback_ok = True
        except Exception as rb_err:
            print(f"WARNING: 事务自动回滚失败（{rb_err}），现场可能残留未恢复文件", file=sys.stderr)

        if rollback_ok:
            _print_changed(changed, "\n=== 本次写入清单（失败，已回滚，仅供排查）===", sync=sync)
            print(f"错误: {error}；已完整回滚本次写入与 state。"
                  "回滚备份保留在 .scratch/repo_sync_rollback", file=sys.stderr)
        else:
            _print_changed(changed, "\n=== 本次写入清单（失败且回滚未完成，现场可能残留混合状态）===", sync=sync)
            print(f"错误: {error}；自动回滚未完全成功，现场可能存在混合版本工具文件！"
                  "备份保留在 .scratch/repo_sync_rollback，请人工执行 python3 ... repo_sync transaction rollback --confirm-restore 恢复基线", file=sys.stderr)
        return 1
    _print_changed(changed, "\n=== 改动路径清单（commit 门禁点名 add）===", sync=sync)
    print("state 推进：last_synced_commit=" + ("已更新" if not dirty else "SRC dirty，未推进"))
    print("事务 applied；拒绝同步可 transaction rollback，提交并确认后 transaction accept")
    return 0
