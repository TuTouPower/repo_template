"""显式迁移入口：把旧布局一次性收敛到当前 schema。

日常 `apply` 不再自动迁移；发现旧布局时阻断并指向本入口。
每项单项迁移在其持久文件事务内执行：先备份，失败回滚本项；`migrate all`
按类型逐项提交，已完成步骤保留，失败仅阻断当前类型。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import context as ctx
from .common import consumer_locked, migration_transaction
from .conventions_migration import conventions_migration_status, migrate_conventions
from .adr_migration import adr_migration_status, migrate_adr
from .agents_zone_migration import agents_zone_migration_status, migrate_agents_zones
from .entity_storage_migration import (
    _check_clean_working_copy,
    entity_storage_migration_status,
    migrate_entity_storage,
)
from .hooks import migrate_retired_hooks, retired_hooks_status
from .source import assert_not_self, resolve_src
from .reviews_migration import migrate_reviews, reviews_migration_status
from .specs_migration import migrate_specs_layout, specs_migration_status
from .state_root_migration import migrate_state_root, state_root_migration_status
from .state import migrate_legacy_state, read_state
from .workflow_migration import force_migrate_workflow_tasks, workflow_migration_status

KINDS = (
    "legacy-state",
    "workflow-schema",
    "pending-ledger",
    "findings-ledger",
    "entity-storage",
    "state-root",
    "conventions",
    "agents-zones",
    "specs-layout",
    "reviews",
    "adr",
    "retired-hooks",
)


def migration_findings(kind: str, sync: ctx.SyncContext, src: Path | None = None) -> list[str]:
    """单一来源：某类迁移的待处理项；status / plan / apply / migrate 共用。

    常驻门禁的布局检测只报告迁移前遗留物：完成迁移后，合法日常编辑不得
    重新产生同类 finding；空列表表示无需该类迁移。spec 内容合法性由公开
    codec/validate_specs 单独校验，不以渲染后的字节或空白比较判定旧布局。
    reviews 是已裁定的动态保留策略例外，超过五个会再次要求显式清理；
    entity-storage 同时承担当前实体合法性校验，不承诺非法编辑仍为空。
    各入口共用本集合，消除「plan 显示一致但 apply 阻断」的分叉。
    """
    if kind in {"pending-ledger", "findings-ledger"}:
        rel = "docs/pending.md" if kind == "pending-ledger" else "docs/findings.md"
        return [f"{rel} 需显式拆分为当前条目"] if (sync.consumer / rel).exists() else []
    if kind == "entity-storage":
        return entity_storage_migration_status(sync=sync)
    if kind == "state-root":
        return state_root_migration_status(sync=sync)
    if kind == "legacy-state":
        legacy = sync.legacy_state_path()
        return [f"旧布局 state {sync.rel(legacy)} 待迁移"] if legacy.exists() else []
    if kind == "workflow-schema":
        if src is None:
            return []
        migrations, worktrees, errors = workflow_migration_status(src, sync=sync)
        findings = list(errors)
        findings.extend(f"已登记 task worktree {branch} @ {root}" for root, branch in worktrees)
        findings.extend(migrations)
        return findings
    if kind == "conventions":
        return conventions_migration_status(sync=sync)
    if kind == "agents-zones":
        return agents_zone_migration_status(sync=sync)
    if kind == "specs-layout":
        return specs_migration_status(sync=sync)
    if kind == "reviews":
        return reviews_migration_status(sync=sync)
    if kind == "adr":
        return adr_migration_status(sync=sync)
    if kind == "retired-hooks":
        return retired_hooks_status(sync=sync)
    raise ctx.SyncError(f"未知迁移目标：{kind}")


def migrate_command(src: Path, sync: ctx.SyncContext, kind: str = "all") -> str:
    """基于固定来源 product 与消费仓身份的可直接执行迁移命令。

    统一从来源 product 生成并带固定 pin（含 --source/--checkout，均 shlex.quote），
    避免旧消费仓本地脚本缺子命令或来源漂移。--source 为 canonical 身份，
    --checkout 为本轮固定本地 checkout，不自动联网。
    """
    import shlex

    script = src / ".repo_template/scripts/repo_sync.py"
    source_value: str | None = None
    try:
        current = read_state(sync)
    except ctx.SyncError:
        current = None
    if isinstance(current, dict):
        ts = current.get("template_source")
        if isinstance(ts, dict):
            candidate = ts.get("value")
            if isinstance(candidate, str) and candidate.strip():
                source_value = candidate
    if source_value is None:
        source_value = str(src)
    return (
        f"python3 {shlex.quote(str(script))}"
        f" --consumer {shlex.quote(str(sync.consumer))}"
        f" --source {shlex.quote(source_value)}"
        f" --checkout {shlex.quote(str(src))}"
        f" migrate {kind} --write"
    )


def _transactional(paths: list[str], action, verify, label: str, *, sync: ctx.SyncContext) -> list[str]:
    transaction = migration_transaction(sync)
    try:
        transaction.begin(paths)
    except ValueError as error:
        raise ctx.SyncError(str(error)) from error
    try:
        reports = action(set())
        problems = verify()
        if problems:
            raise ctx.SyncError(
                f"{label} 迁移后仍有不可机械转换的数据：\n- " + "\n- ".join(problems)
            )
        transaction.applied()
        transaction.accept()
    except Exception as error:
        # 任意异常（含 AttributeError / IdScanError 等）都必须回滚，绝不让 journal
        # 停留在 applying。KeyboardInterrupt / SystemExit 属 BaseException，不吞。
        try:
            transaction.rollback(automatic=True)
        except Exception as rollback_error:  # noqa: BLE001 - 恢复失败必须显式报告
            raise ctx.SyncError(
                f"{label} 迁移失败（{error}）；自动回滚失败：{rollback_error}；"
                f"备份保留在 {sync.rel(transaction.backup)}，请人工恢复"
            ) from error
        raise ctx.SyncError(f"{label} 迁移失败，已回滚：{error}") from error
    return reports


def _resolve_source(sync: ctx.SyncContext) -> Path:
    import shlex

    legacy = sync.legacy_state_path()
    if legacy.exists():
        raise ctx.SyncError(
            "存在旧布局 state，须先单独迁移 legacy-state 后再迁移其他类型："
            f"`python3 <product>/.repo_template/scripts/repo_sync.py"
            f" --consumer {shlex.quote(str(sync.consumer))} migrate legacy-state --write`"
        )
    state = read_state(sync)
    if not state:
        raise ctx.SyncError("未初始化：无同步来源，无法读取模板 schema")
    src = resolve_src(state, sync=sync)
    assert_not_self(src, sync=sync)
    return src


def _migrate_workflow(sync: ctx.SyncContext, write: bool = False) -> list[str]:
    src = _resolve_source(sync)
    migrations, worktrees, errors = workflow_migration_status(src, sync=sync)
    blockers = list(errors)
    if worktrees:
        blockers.extend(f"已登记 task worktree {branch} @ {root}" for root, branch in worktrees)
    if blockers:
        raise ctx.SyncError("workflow schema 迁移前须处理现场：\n- " + "\n- ".join(blockers))
    if not migrations:
        return []
    if not write:
        return [f"{path} 拟强制迁移到当前 workflow schema" for path in migrations]
    template_dir = src / ".repo_template/docs/task_template"
    return _transactional(
        ["docs/tasks", "docs/archive/tasks"],
        lambda changed: force_migrate_workflow_tasks(
            changed, template_template_dir=template_dir, sync=sync
        ),
        lambda: [path for path in workflow_migration_status(src, sync=sync)[0]],
        "workflow schema",
        sync=sync,
    )


def _migrate_agents_zones(sync: ctx.SyncContext, write: bool = False) -> list[str]:
    status = agents_zone_migration_status(sync=sync)
    if not status:
        return []
    src = _resolve_source(sync)
    if not write:
        return [f"{path} 拟拆为项目区 + 模板区" for path in status]
    return _transactional(
        ["AGENTS.md"],
        lambda _changed: migrate_agents_zones(sync, src, write=True),
        lambda: agents_zone_migration_status(sync=sync),
        "AGENTS 两区",
        sync=sync,
    )


def _migrate_specs_layout(sync: ctx.SyncContext, write: bool = False) -> list[str]:
    status = specs_migration_status(sync=sync)
    if not status:
        return []
    if not write:
        return migrate_specs_layout(sync, write=False)
    return _transactional(
        ["docs/specs", "docs/archive/specs", "docs/specs_index.md"],
        lambda _changed: migrate_specs_layout(sync, write=True),
        lambda: specs_migration_status(sync=sync),
        "specs front matter",
        sync=sync,
    )


def _migrate_reviews(sync: ctx.SyncContext, write: bool = False) -> list[str]:
    status = reviews_migration_status(sync=sync)
    if not status:
        return []
    if not write:
        return migrate_reviews(sync, write=False)
    return _transactional(
        ["docs/reviews", "docs/archive/reviews"],
        lambda _changed: migrate_reviews(sync, write=True),
        lambda: reviews_migration_status(sync=sync),
        "review 保留",
        sync=sync,
    )


def _migrate_adr(sync: ctx.SyncContext, write: bool = False) -> list[str]:
    status = adr_migration_status(sync=sync)
    if not status:
        return []
    if not write:
        return migrate_adr(sync, write=False)
    return _transactional(
        ["docs/blueprint/decisions.md", "docs/blueprint/decisions"],
        lambda _changed: migrate_adr(sync, write=True),
        lambda: adr_migration_status(sync=sync),
        "ADR 拆分",
        sync=sync,
    )


def _migrate_conventions(sync: ctx.SyncContext, write: bool = False) -> list[str]:
    status = conventions_migration_status(sync=sync)
    if not status:
        return []
    if not write:
        return [
            f"{path} 拟自动迁移原子写约定为 from repo_task.documents import atomic_write_text（保留消费仓自定义内容）"
            for path in status
        ]
    return _transactional(
        ["docs/blueprint/conventions.md"],
        lambda changed: migrate_conventions(changed, sync=sync),
        lambda: conventions_migration_status(sync=sync),
        "conventions 原子写约定",
        sync=sync,
    )


def _migrate_retired_hooks(sync: ctx.SyncContext, write: bool = False) -> list[str]:
    status = retired_hooks_status(sync=sync)
    if not status:
        return []
    if not write:
        return [f"{path} 拟退役并移除/清理" for path in status]
    # 事务根必须是 .claude/hooks 目录（可快照子软链），不能用软链文件作资产根：
    # FileTransaction.begin 明确拒绝软链资产根。
    return _transactional(
        [".claude/hooks", ".claude/settings.json"],
        lambda _changed: migrate_retired_hooks(sync=sync),
        lambda: retired_hooks_status(sync=sync),
        "退役 hooks 清理",
        sync=sync,
    )


def _migrate_ledger(sync: ctx.SyncContext, prefix: str, *, write: bool = False) -> list[str]:
    from .legacy_ledgers import apply_legacy_migration, plan_legacy_migration
    from _id_scan import IdScanError
    from md_format import format_file
    from pending import dump_pending_front_matter, validate_pending_front_matter
    from repo_task.documents import parse_front_matter_text

    directory = "docs/pending" if prefix == "p" else "docs/findings"
    ledger = directory + ".md"
    if (sync.consumer / ledger).is_symlink():
        raise ctx.SyncError(f"旧总账是软链，拒绝迁移：{ledger}")
    if not (sync.consumer / ledger).exists():
        return []
    try:
        items = plan_legacy_migration(sync.consumer, prefix=prefix, ledger_rel=ledger, target_dir=sync.consumer / directory)
    except IdScanError as error:
        raise ctx.SyncError(str(error)) from error
    reports = [f"{item.entry_id} -> {sync.rel(item.target)}" for item in items]
    if not write:
        return reports

    def render(entry_id: str, slug: str, title: str, body: str) -> str:
        front = dump_pending_front_matter({"pid": entry_id, "slug": slug, "status": "todo"}) if prefix == "p" else ""
        return front + f"# {entry_id} {title}\n\n{body}\n"

    def action(_changed):
        apply_legacy_migration(items, repo_root=sync.consumer, prefix=prefix, ledger_rel=ledger,
                               render=render, formatter=format_file)
        return reports

    def verify():
        if (sync.consumer / ledger).exists():
            return [ledger]
        for item in items:
            text = item.target.read_text(encoding="utf-8")
            if prefix == "p":
                fm, _ = parse_front_matter_text(text, source=str(item.target))
                validate_pending_front_matter(fm, filename=item.target.name, source=str(item.target))
            if not text.strip():
                return [f"迁移结果为空：{item.target}"]
        return []

    return _transactional([ledger, directory], action, verify, f"{prefix} 总账", sync=sync)


_STEPS = {
    "legacy-state": lambda sync, write=False, require_clean=True: migrate_legacy_state(sync=sync, write=write),
    "workflow-schema": lambda sync, write=False, require_clean=True: _migrate_workflow(sync, write=write),
    "conventions": lambda sync, write=False, require_clean=True: _migrate_conventions(sync, write=write),
    "agents-zones": lambda sync, write=False, require_clean=True: _migrate_agents_zones(sync, write=write),
    "specs-layout": lambda sync, write=False, require_clean=True: _migrate_specs_layout(sync, write=write),
    "reviews": lambda sync, write=False, require_clean=True: _migrate_reviews(sync, write=write),
    "adr": lambda sync, write=False, require_clean=True: _migrate_adr(sync, write=write),
    "retired-hooks": lambda sync, write=False, require_clean=True: _migrate_retired_hooks(sync, write=write),
    "pending-ledger": lambda sync, write=False, require_clean=True: _migrate_ledger(sync, "p", write=write),
    "findings-ledger": lambda sync, write=False, require_clean=True: _migrate_ledger(sync, "d", write=write),
    "entity-storage": lambda sync, write=False, require_clean=True: migrate_entity_storage(
        sync=sync, write=write, require_clean=require_clean
    ),
    "state-root": lambda sync, write=False, require_clean=True: migrate_state_root(
        sync=sync, write=write
    ),
}


@consumer_locked
def cmd_migrate(args: argparse.Namespace) -> int:
    """执行显式迁移；`all` 按固定顺序逐项执行，宁可停下也不猜测数据。

    逐项事务、已完成步骤保留的语义不变；clean 门禁仅 entity-storage 生效
    （workflow/conventions/specs-index/retired-hooks 可脏运行）。
    state-root 不套 `_transactional`：它在自身标准 FileTransaction 内以
    state_paths 管理 common 状态目录，与逐项事务顺序执行、不产生同目录嵌套。
    all 写盘前仅当 entity-storage 有待迁移项时查干净，避免前序步骤落盘后
    entity 步再查干净造成顺序死锁；entity 单项写盘仍自查干净。
    dry-run 下仅有旧布局 state 时只展示 legacy-state 计划并指引先单迁，
    不让后续步骤因无新 state 而报未初始化断层。
    """
    sync: ctx.SyncContext = args.sync
    kinds = KINDS if args.kind == "all" else (args.kind,)
    write = getattr(args, "write", False)
    if args.kind == "all" and not write:
        legacy_findings = migration_findings("legacy-state", sync)
        if legacy_findings:
            reports = _STEPS["legacy-state"](sync, write=False, require_clean=False)
            if reports:
                print("legacy-state: dry-run 拟定完成 "
                      f"{len(reports)} 项")
                for line in reports:
                    print(f"  {line}")
            print(
                "存在旧布局 state：其他类型须先单独执行 "
                "`migrate legacy-state --write` 生成新 state 后再评估，"
                "本次 dry-run 仅展示 legacy-state 计划"
            )
            print("迁移完成（dry-run，未写盘；加 --write 执行落盘）")
            return 0
    if write and args.kind == "all":
        if entity_storage_migration_status(sync=sync):
            _check_clean_working_copy(sync.consumer)
    for kind in kinds:
        step = _STEPS.get(kind)
        if step is None:
            raise ctx.SyncError(f"未知迁移目标：{kind}")
        reports = step(sync, write=write, require_clean=(args.kind != "all"))
        if not reports:
            print(f"{kind}: 已是当前格式，无需迁移")
            continue
        mode_str = "落盘" if write else "dry-run 拟定"
        print(f"{kind}: {mode_str}完成 {len(reports)} 项")
        for line in reports:
            print(f"  {line}")
    if write:
        print("迁移完成；校验并提交迁移结果")
    else:
        print("迁移完成（dry-run，未写盘；加 --write 执行落盘）")
    return 0
