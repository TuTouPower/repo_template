#!/usr/bin/env python3
"""Compatibility façade for the modular :mod:`repo_task` task toolchain.

Run this file directly; implementation lives in ``repo_task/`` and requires no installation.
"""

import sys

from repo_task import context as _context
from repo_task.cli import main
from repo_task.context import (
    TaskDataError,
    _rel,
    effective_worktree,
    worktree_rel_path,
)
from repo_task.control import (
    cmd_attempt_report,
    cmd_attempt_reserve,
    cmd_attempt_terminal,
    cmd_effective_status,
    cmd_ledger_record,
    cmd_ledger_tail,
    cmd_ps,
    cmd_view,
)
from repo_task.plan import (  # noqa: F401 — façade re-export
    build_board_model,
    build_execution_plan,
    cmd_plan,
    compute_batch_plan,
    format_plan_text,
)
from repo_task.documents import (
    _extract_markdown_section,
    _markdown_headings,
    _missing_heading_sequence,
    _quote,
    _strip_inline_code,
    _unquote,
    _visible_markdown_lines,
    dump_front_matter,
    dump_tid_list,
    parse_front_matter,
    parse_front_matter_text,
    parse_tid_list,
    parse_unverified_contracts,
    tid_sort_key,
    unverified_contract_gate,
    validate_task_documents,
    validate_tid_references,
    write_front_matter,
)
from repo_task.git_ops import (
    _get_head,
    _get_head_short,
    _git,
    _git_bytes,
    current_branch,
    default_branch,
    has_unmerged_commits,
    in_own_task_worktree,
    in_primary_worktree,
    porcelain_entries,
    primary_worktree_path,
    require_own_task_worktree,
    require_primary_worktree,
    resolve_local_branch,
    task_worktree_path,
    tracked_anywhere,
    tracked_dirty_entries,
    worktree_paths,
)
from repo_task.goal import cmd_goal, cmd_goal_check
from repo_task.integration import (
    _commit_index,
    _conflicted_paths,
    _merge_in_progress,
    _resolve_integrate_branch,
    cmd_cleanup_worktree,
    cmd_integrate,
    cmd_integrate_chain,
    cmd_start,
)
from repo_task.attempts import (
    append_integrated_batch,
    attempt_for_identity,
    attempts_for_tid,
    current_attempt,
    current_attempt_record,
    current_identity,
    in_flight_attempts,
    overlapping_attempts,
    project_attempts,
    require_exact_terminal,
)
from repo_task.ledger import (
    _ledger_append_safely,
    _ledger_lock_fh,
    _ledger_unlock_fh,
    ledger_allocate_attempt,
    ledger_append,
    ledger_locked_append,
    ledger_locked_append_many,
    ledger_next_attempt,
    ledger_read,
)
from repo_task.lifecycle import (
    _close_task,
    cmd_add,
    cmd_block,
    cmd_drop,
    cmd_edit,
    cmd_finish,
    cmd_list,
    cmd_preflight,
    cmd_purge,
    cmd_resume,
    cmd_rewind,
    cmd_show,
)
from repo_task.monitoring import (
    _ledger_tid_sort_key,
    compute_ps_rows,
    repository_fingerprint,
    verify_integrate_ready,
    worktree_dirty_summary,
)
from repo_task.scheduling import (
    _dependency_cycle,
    compute_schedule,
)
from repo_task.store import (
    _local_task_branches,
    _scan_tasks_in_directories,
    _task_branch_names,
    _task_record,
    _validate_task_records,
    append_audit,
    append_note,
    discover_effective_sources,
    discover_effective_tasks,
    find_task,
    git_text_at_ref,
    load_task,
    load_task_at_ref,
    rebuild_index,
    require_status,
    scan_tasks,
    scan_tasks_at_ref,
    scan_tasks_in_worktree,
    task_effective_state,
    task_schedule_references,
)
from repo_task.worktrees import (
    create_worktree,
    discard_worktree,
    is_managed_env_link,
    link_local_env,
    remove_worktree,
    resolve_start_base,
    rollback_start,
    unlink_managed_env_links,
)

_CONTEXT_EXPORTS = {name for name in dir(_context) if name.isupper()}

def __getattr__(name):
    if name in _CONTEXT_EXPORTS:
        return getattr(_context, name)
    raise AttributeError(name)

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
