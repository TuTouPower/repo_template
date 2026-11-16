"""声明式迁移目录：只读 findings/blockers 与无事务 execute。"""

from dataclasses import dataclass
from pathlib import Path

from repo_task.context import PROGRESS_FILE

from . import context as ctx
from .adr_migration import adr_migration_status, migrate_adr
from .agents_zone_migration import agents_zone_migration_status, migrate_agents_zones
from .conventions_migration import conventions_migration_status, migrate_conventions
from .entity_storage_migration import (
    build_migration_plan,
    entity_storage_migration_status,
    migrate_entity_storage,
)
from .goal_snapshot_migration import goal_snapshot_migration_status, migrate_goal_snapshot
from .hooks import migrate_retired_hooks, retired_hooks_status
from .retired_docs_migration import migrate_retired_docs, retired_docs_migration_status
from .reviews_migration import migrate_reviews, reviews_migration_status
from .specs_migration import migrate_specs_layout, specs_migration_status
from .state import migrate_legacy_state
from .state_root_migration import migrate_state_root, state_root_migration_status
from .workflow_migration import (
    _collect_workflow_task_dirs, _inspect_workflow_task_dir, _guide_blocks_by_heading,
    force_migrate_workflow_tasks, workflow_migration_status,
)


def _workflow_writes(sync, src):
    directory = src / ".repo_template/docs/task_template"
    if not (directory / "task.md").is_file() or not (directory / PROGRESS_FILE).is_file():
        return {}
    task = (directory / "task.md").read_text(encoding="utf-8")
    progress = (directory / PROGRESS_FILE).read_text(encoding="utf-8")
    if not _guide_blocks_by_heading(task) or "## 实施笔记" not in progress:
        return {}
    writes = {}
    for path in _collect_workflow_task_dirs(sync):
        _, errors, plan = _inspect_workflow_task_dir(path, task, progress, sync)
        if errors:
            raise ctx.SyncError("；".join(errors))
        writes.update(plan["write"])
    return writes


@dataclass(frozen=True)
class Migration:
    kind: str
    ROOTS: tuple[str, ...]
    STATE_ROOTS: tuple[str, ...] = ()

    def findings(self, sync: ctx.SyncContext, src: Path) -> list[str]:
        if self.kind == "legacy-state":
            return [f"{sync.rel(sync.legacy_state_path())} 待迁移"] if sync.legacy_state_path().exists() or sync.legacy_state_path().is_symlink() else []
        if self.kind == "workflow-schema":
            migrations, worktrees, errors = workflow_migration_status(src, sync=sync)
            return migrations + errors + [f"已登记 task worktree {branch} @ {root}" for root, branch in worktrees]
        return _FINDINGS[self.kind](sync=sync)

    def blockers(self, sync: ctx.SyncContext, src: Path) -> list[str]:
        try:
            if self.kind == "workflow-schema":
                _, worktrees, errors = workflow_migration_status(src, sync=sync)
                return errors + [f"已登记 task worktree {branch} @ {root}" for root, branch in worktrees]
            if self.kind == "entity-storage":
                # 本轮统一计划已注入并验证时不再重建；status/plan 命令无注入则按需构建
                if getattr(sync, "entity_plan", None) is None:
                    build_migration_plan(sync, workflow_writes=_workflow_writes(sync, src))
            else:
                self._run(sync, src, write=False)
        except (ctx.SyncError, ctx.TaskDataError, ValueError, OSError) as error:
            return [str(error)]
        return []

    def _run(self, sync, src, *, write):
        if self.kind == "legacy-state":
            return migrate_legacy_state(sync, write=write)
        if self.kind == "workflow-schema":
            if not write:
                return self.findings(sync, src)
            return force_migrate_workflow_tasks(set(), template_template_dir=src / ".repo_template/docs/task_template", sync=sync)
        if self.kind == "conventions":
            return migrate_conventions(set(), sync=sync) if write else self.findings(sync, src)
        if self.kind == "agents-zones":
            return migrate_agents_zones(sync, src, write=write)
        if self.kind == "goal-snapshot":
            return migrate_goal_snapshot(sync, src, write=write)
        if self.kind == "retired-hooks":
            return migrate_retired_hooks(sync=sync) if write else retired_hooks_status(sync)
        return _EXECUTORS[self.kind](sync, write=write)

    def execute(self, sync: ctx.SyncContext, src: Path) -> list[str]:
        return self._run(sync, src, write=True)


_FINDINGS = {
    "entity-storage": entity_storage_migration_status, "state-root": state_root_migration_status,
    "goal-snapshot": goal_snapshot_migration_status,
    "conventions": conventions_migration_status, "agents-zones": agents_zone_migration_status,
    "specs-layout": specs_migration_status, "reviews": reviews_migration_status,
    "adr": adr_migration_status, "retired-hooks": retired_hooks_status,
    "retired-docs": retired_docs_migration_status,
}
_EXECUTORS = {
    "entity-storage": migrate_entity_storage, "state-root": migrate_state_root,
    "specs-layout": migrate_specs_layout, "reviews": migrate_reviews, "adr": migrate_adr,
    "retired-docs": migrate_retired_docs,
}

MIGRATIONS = (
    Migration("legacy-state", (".agents/skills/repo-template-sync/sync_state.json",), ("sync_state.json",)),
    Migration("workflow-schema", ("docs/tasks", "docs/archive/tasks")),
    Migration("entity-storage", (
        ".repo_template/entity_schema.json", "docs/tasks", "docs/archive/tasks",
        "docs/pending", "docs/pending.md", "docs/archive/pending",
        "docs/pending/todo", "docs/pending/parked",
        "docs/findings", "docs/findings.md",
        "docs/spikes", "docs/archive/spikes",
        "docs/tasks_index.json", "docs/archive/tasks_index.json",
        "docs/archive/tasks_audit.log", "docs/runtime", "docs/archive/handoff.md",
        "docs/handoff.md", "docs/specs_index.md",
    )),
    Migration("state-root", ("docs/runtime", ".repo_template/sync_state.json", ".scratch/statusline_workdir.jsonl",
                             ".scratch/repo_sync_rollback", ".scratch/task_graph_transaction"),
              ("ledger.jsonl", "tasks_audit.log", "goal_queue.json", "statusline_workdir.jsonl", "sync_state.json")),
    Migration("goal-snapshot", (), ("goal_queue.json",)),
    Migration("conventions", ("docs/blueprint/conventions.md",)),
    Migration("agents-zones", ("AGENTS.md",)),
    Migration("specs-layout", ("docs/specs", "docs/archive/specs", "docs/specs_index.md")),
    Migration("reviews", ("docs/reviews", "docs/archive/reviews")),
    Migration("adr", ("docs/blueprint/decisions.md", "docs/blueprint/decisions")),
    Migration("retired-hooks", (".claude/hooks", ".claude/settings.json")),
    Migration("retired-docs", ("docs/pending/README.md", "docs/findings/README.md")),
)
