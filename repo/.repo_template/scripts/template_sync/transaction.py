"""transaction 子命令：持久同步事务的查询、回滚与接受。"""

from __future__ import annotations

import json

from repo_task.file_transaction import FileTransaction, fingerprint
from repo_task.locks import CONTROL_LOCK_NAME, git_common_lock
from repo_task.validation import ValidationError, load_policy

from . import context as ctx
from .common import _git
from .gates import _run_tests


def cmd_transaction(args):
    with git_common_lock(ctx.CONSUMER, CONTROL_LOCK_NAME):
        transaction = FileTransaction(ctx.CONSUMER, ".scratch/repo_sync_rollback")
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
                status = _git(ctx.CONSUMER, "status", "--porcelain", "--", *data["paths"])
                if status.returncode or status.stdout.strip():
                    raise ctx.SyncError("accept 前须提交本轮同步资产；不接受未提交现场")
                load_policy(ctx.CONSUMER)
                if not data.get("gates_verified") or data.get("after") != fingerprint(ctx.CONSUMER, data["paths"]):
                    if not _run_tests(ctx.CONSUMER):
                        raise ctx.SyncError("accept 前 contract 复验失败，事务保持 applied")
                transaction.accept(verified=True)
                print("同步事务已接受；备份保留到下次同步")
        except (ValueError, ValidationError) as error:
            raise ctx.SyncError(str(error)) from error
    return 0
