"""transaction 子命令：持久同步事务的查询、回滚与接受。"""

from __future__ import annotations

import json

from repo_task.file_transaction import SYNC_TRANSACTION_DIR, state_transaction
from repo_task.locks import CONTROL_LOCK_NAME, git_common_lock
from repo_task.validation import ValidationError, load_policy

from . import context as ctx
from .common import _git
from .gates import _run_tests


def _within_roots(path: str, roots) -> bool:
    return any(path == root or path.startswith(root + "/") for root in roots)


def _commit_scope_error(sync: ctx.SyncContext, data) -> str | None:
    """同步 commit 只能包含事务声明路径；范围外文件说明混入了非同步改动。"""
    base_head = data.get("base_head")
    if not base_head:
        return None
    diff = _git(sync.consumer, "diff", "--name-only", "--no-renames", "-z", f"{base_head}..HEAD")
    if diff.returncode:
        return "无法核对同步 commit 范围：" + diff.stderr.strip()
    outside = sorted(
        path for path in diff.stdout.split("\0")
        if path and not _within_roots(path, data["paths"])
    )
    if outside:
        return "同步 commit 包含事务范围外文件，拒绝 accept：" + "、".join(outside)
    return None


def cmd_transaction(args):
    sync: ctx.SyncContext = args.sync
    with git_common_lock(sync.consumer, CONTROL_LOCK_NAME):
        transaction = state_transaction(sync.consumer, SYNC_TRANSACTION_DIR)
        try:
            if args.action == "status":
                print(json.dumps(transaction.read(), ensure_ascii=False, indent=2))
            elif args.action == "rollback":
                transaction.rollback(confirm_restore=args.confirm_restore)
                print("同步事务已回滚；备份保留")
            else:
                data = transaction.read()
                if not data or data["status"] != "applied":
                    raise ctx.SyncError("只有 applied 事务可以 accept")
                status = _git(sync.consumer, "status", "--porcelain", "--", *data["paths"])
                if status.returncode or status.stdout.strip():
                    raise ctx.SyncError("accept 前须提交本轮同步资产；不接受未提交现场")
                if data.get("after") != transaction.current_fingerprint(data):
                    raise ctx.SyncError("apply 后文件或 common 状态发生变化，拒绝 accept；先核对审批 diff 与提交内容")
                if data.get("base_head"):
                    parents = _git(sync.consumer, "rev-list", "--parents", "-n", "1", "HEAD")
                    if parents.returncode or parents.stdout.split()[1:] != [data["base_head"]]:
                        raise ctx.SyncError("一次同步须由单个 commit 提交，且父提交必须为同步前 HEAD")
                    scope_error = _commit_scope_error(sync, data)
                    if scope_error:
                        raise ctx.SyncError(scope_error)
                load_policy(sync.consumer)
                if not data.get("gates_verified"):
                    if not _run_tests(sync.consumer):
                        raise ctx.SyncError("accept 前 contract 复验失败，事务保持 applied")
                transaction.accept(verified=True)
                print("同步事务已接受；备份保留到下次同步")
        except (ValueError, ValidationError) as error:
            raise ctx.SyncError(str(error)) from error
    return 0
