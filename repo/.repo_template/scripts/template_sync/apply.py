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
from repo_task import context as task_context
from repo_task import store as task_store
from repo_task.file_transaction import FileTransaction
from repo_task.validation import load_policy

from . import context as ctx
from .agents_merge import _agents_parts, _replace_agents_force
from .assets import retired_template_warnings, sync_dir, sync_file
from .common import _git, _now_iso, consumer_locked
from .conventions_migration import migrate_conventions
from .gates import _run_tests
from .shared_files import _apply_shared_unit, merge_gitignore, merge_mcp, merge_prettierignore
from .specs_index_migration import migrate_specs_index, specs_index_report
from .skills_links import repair_symlinks, sync_opencode_commands
from .source import _as_product, assert_not_self, resolve_src, src_dirty
from .state import prompt_substrings, read_state, write_state
from .workflow_migration import force_migrate_workflow_tasks


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


def _apply_sync_write(src: Path, decisions: dict[str, str], changed: set[Path], blocked: list[str]) -> None:
    """apply 的写盘主体；OSError 由调用方回滚（F16）。"""
    for rel in ctx.HARD_SYNC_DIRS:
        s, d = src / rel, ctx.CONSUMER / rel
        if s.exists():
            sync_dir(s, d, changed)
        elif d.exists():
            shutil.rmtree(d)
            changed.add(d)
    for rel in ctx.HARD_SYNC_FILES:
        sync_file(src / rel, ctx.CONSUMER / rel, changed)

    migration_reports = force_migrate_workflow_tasks(changed)
    for report in migration_reports:
        print(f"提示: {report}")
    if migration_reports:
        try:
            # 消费仓根显式传入：repo_task.context 指向来源仓时也不能写错地方。
            task_store.rebuild_index_at(ctx.CONSUMER)
        except task_context.TaskDataError as error:
            raise ctx.SyncError(f"workflow 迁移后重建 tasks index 失败：{error}") from error
        changed.add(ctx.CONSUMER / "docs/tasks_index.json")
        changed.add(ctx.CONSUMER / "docs/archive/tasks_index.json")
    for report in migrate_conventions(changed):
        print(f"提示: {report}")
    for report in migrate_specs_index(changed):
        print(f"提示: {report}")
    for problem in specs_index_report():
        print(f"specs_index 契约违规: {problem}")
    reports = repair_symlinks(changed)
    for r in reports:
        print(f"提示: {r}")
    for r in sync_opencode_commands(changed):
        print(f"提示: {r}")

    if (src / ".gitignore").exists():
        info = merge_gitignore(src, blocked, changed)
        if info["added"]:
            print(f".gitignore 追加 {len(info['added'])} 条（模板独有）")
        if info["removed"]:
            print(f".gitignore 删除 {len(info['removed'])} 条（prompt 禁止 ignore）")
    prettier_info = merge_prettierignore(changed)
    if prettier_info["added"]:
        print(f".prettierignore 追加 {len(prettier_info['added'])} 条（模板自有路径豁免 prettier）")
    for warning in retired_template_warnings():
        print(f"提示: {warning}")
    merge_mcp(src, changed)

    for unit in ctx.SHARED_FILES:
        if unit == ".gitignore":
            continue
        decision = decisions.get(unit)
        _apply_shared_unit(unit, decision, src, changed)


def _print_changed(paths: set[Path], header: str) -> None:
    print(header)
    for path in sorted(paths, key=str):
        print(ctx._rel(path))
    if not paths:
        print("（无改动）")


@consumer_locked
def cmd_apply(args: argparse.Namespace) -> int:
    state = read_state()
    if not state:
        raise ctx.SyncError("未初始化：无同步来源；使用 --source 或 init --source <path|url>")
    src = resolve_src(state)
    assert_not_self(src)
    dirty = src_dirty(src)
    if dirty and not getattr(args, "allow_dirty_source", False):
        raise ctx.SyncError("模板源 dirty；正式同步必须使用 clean commit。开发验证须显式 --allow-dirty-source")
    source_commit = _git(src, "rev-parse", "HEAD").stdout.strip()
    expected = getattr(args, "expected_commit", None)
    if expected and source_commit != expected:
        raise ctx.SyncError(f"来源版本已变化：预期 {expected}，实际 {source_commit}；重新 plan")
    changed: set[Path] = set()
    if (ctx.CONSUMER / "AGENTS.md").is_symlink():
        raise ctx.SyncError("apply 不支持软链 AGENTS.md，先替换为真实文件后重跑")
    transaction = FileTransaction(ctx.CONSUMER, ".scratch/repo_sync_rollback")
    paths = [".repo_template", ".agents/skills", ".claude/skills", ".claude/settings.json",
             ".claude/hooks", ".opencode/commands", ".md_kx.toml", ".gitignore", ".prettierignore",
             "AGENTS.md", "docs/tasks", "docs/archive/tasks", "docs/tasks_index.json",
             "docs/archive/tasks_index.json", "docs/blueprint/conventions.md", "docs/specs_index.md",
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
            _apply_sync_write(src, getattr(args, "decisions", None) or {}, changed, prompt_substrings(state))
        else:
            with tempfile.TemporaryDirectory(prefix="repo_sync_source_", dir=ctx.CONSUMER / ".scratch") as directory:
                snapshot = Path(directory)
                export_template(src, snapshot, source_commit)
                _as_product(snapshot)  # Fail closed before writing if export is incomplete.
                if (snapshot / "AGENTS.md").exists():
                    template_agents = (snapshot / "AGENTS.md").read_text(encoding="utf-8")
                _apply_sync_write(snapshot, getattr(args, "decisions", None) or {}, changed, prompt_substrings(state))
        agents_file = getattr(args, "agents_file", None)
        if agents_file:
            candidate = Path(agents_file).read_text(encoding="utf-8")
            current = (ctx.CONSUMER / "AGENTS.md").read_text(encoding="utf-8")
            try:
                current_parts = _agents_parts(current)
            except ctx.SyncError as error:
                raise ctx.SyncError(
                    "消费仓 AGENTS.md 缺 ## 目录与读写规则 或 ## 开发原则；"
                    "按模板补齐 ## 目录与读写规则、## 开发原则 后重跑"
                ) from error
            if _agents_parts(candidate)["intro"][2] != current_parts["intro"][2]:
                raise ctx.SyncError("AGENTS 语义合并不得修改项目介绍")
            if template_agents is None:
                raise ctx.SyncError("模板快照缺 AGENTS.md，不能应用语义合并")
            result, *_ = _replace_agents_force(template_agents, candidate)
            (ctx.CONSUMER / "AGENTS.md").write_text(result, encoding="utf-8")
            changed.add(ctx.CONSUMER / "AGENTS.md")
        load_policy(ctx.CONSUMER)
        if not args.skip_tests:
            if not _run_tests(ctx.CONSUMER):
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
            (ctx.CONSUMER / ".repo_template/sync_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            changed.add(ctx.CONSUMER / ".repo_template/sync_manifest.json")
        state["last_synced_at"] = _now_iso()
        state["resolved_checkout"] = str(src)
        write_state(state)
        transaction.applied(verified=not args.skip_tests)
    except (OSError, ValueError, ctx.SyncError, subprocess.SubprocessError) as error:
        rollback_ok = False
        try:
            transaction.rollback(automatic=True)
            rollback_ok = True
        except Exception as rb_err:
            print(f"WARNING: 事务自动回滚失败（{rb_err}），现场可能残留未恢复文件", file=sys.stderr)

        if rollback_ok:
            _print_changed(changed, "\n=== 本次写入清单（失败，已回滚，仅供排查）===")
            print(f"错误: {error}；已完整回滚本次写入与 state。"
                  "回滚备份保留在 .scratch/repo_sync_rollback", file=sys.stderr)
        else:
            _print_changed(changed, "\n=== 本次写入清单（失败且回滚未完成，现场可能残留混合状态）===")
            print(f"错误: {error}；自动回滚未完全成功，现场可能存在混合版本工具文件！"
                  "备份保留在 .scratch/repo_sync_rollback，请人工执行 python3 ... repo_sync transaction rollback --confirm-restore 恢复基线", file=sys.stderr)
        return 1
    _print_changed(changed, "\n=== 改动路径清单（commit 门禁点名 add）===")
    print("state 推进：last_synced_commit=" + ("已更新" if not dirty else "SRC dirty，未推进"))
    print("事务 applied；拒绝同步可 transaction rollback，提交并确认后 transaction accept")
    return 0
