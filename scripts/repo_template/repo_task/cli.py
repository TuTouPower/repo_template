"""Argument parser and command routing for task.py."""

import argparse
import sys

import repo_task.context as ctx

from .control import (
    cmd_attempt_bind,
    cmd_attempt_escalate,
    cmd_attempt_report,
    cmd_attempt_reserve,
    cmd_attempt_silent_alert,
    cmd_attempt_terminal,
    cmd_ledger_record,
    cmd_ledger_tail,
    cmd_observe,
    cmd_ps,
    cmd_reconcile,
    cmd_view,
)
from .integration import cmd_cleanup_worktree, cmd_integrate, cmd_integrate_chain, cmd_start
from .lifecycle import (
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


def _add_identity(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--execution-id", required=True)


def main():
    parser = argparse.ArgumentParser(
        description="task 状态入口（状态权威 = task.md；执行权威 = exact attempt identity）"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("add", help="新增 backlog task")
    add.add_argument("--title", required=True)
    add.add_argument("--slug", required=True)
    add.add_argument("--note", default="")
    add.add_argument("--review-level", choices=ctx.REVIEW_LEVELS, default=ctx.DEFAULT_REVIEW_LEVEL)
    add.set_defaults(func=cmd_add)

    edit = sub.add_parser("edit", help="修改 backlog task")
    edit.add_argument("tid")
    edit.add_argument("--title")
    edit.add_argument("--note")
    edit.add_argument("--note-append")
    edit.add_argument("--review-level", choices=ctx.REVIEW_LEVELS)
    edit.add_argument("--depends-on")
    edit.add_argument("--depends-append")
    edit.add_argument("--depends-remove")
    edit.add_argument("--conflicts-with")
    edit.add_argument("--conflicts-append")
    edit.add_argument("--conflicts-remove")
    edit.add_argument("--schedule-status", choices=ctx.SCHEDULE_STATUSES)
    edit.set_defaults(func=cmd_edit)

    start = sub.add_parser("start", help="backlog -> active：创建 task branch/worktree")
    start.add_argument("tid")
    start.add_argument("--base", help="可选上一已完成 task 分支")
    start.set_defaults(func=cmd_start)

    preflight = sub.add_parser("preflight", help="开干前门禁")
    preflight.add_argument("tid")
    preflight.add_argument("--allow-backlog", action="store_true")
    preflight.add_argument("--ref")
    preflight.add_argument("--require-verified", action="store_true")
    preflight.set_defaults(func=cmd_preflight)

    block = sub.add_parser("block", help="active -> blocked")
    block.add_argument("tid")
    block.add_argument("--reason", required=True, choices=ctx.BLOCK_REASONS)
    block.set_defaults(func=cmd_block)

    resume = sub.add_parser("resume", help="blocked -> active")
    resume.add_argument("tid")
    resume.set_defaults(func=cmd_resume)

    finish = sub.add_parser("finish", help="active -> done")
    finish.add_argument("tid")
    finish.set_defaults(func=cmd_finish)

    drop = sub.add_parser("drop", help="活跃状态 -> dropped")
    drop.add_argument("tid")
    drop.add_argument("--reason", required=True)
    drop.set_defaults(func=cmd_drop)

    rewind = sub.add_parser("rewind", help="状态撤回")
    rewind.add_argument("tid")
    rewind.add_argument("--to", choices=("backlog", "active"))
    rewind.add_argument("--yes", action="store_true")
    rewind.add_argument("--reason", required=True)
    rewind.set_defaults(func=cmd_rewind)

    purge = sub.add_parser("purge", help="删除误建 backlog task")
    purge.add_argument("tid")
    purge.add_argument("--reason", required=True)
    purge.set_defaults(func=cmd_purge)

    listing = sub.add_parser("list", help="列出 task")
    listing.add_argument("--status", choices=ctx.VALID_STATUSES)
    listing.add_argument("--ref")
    listing.add_argument("--rebuild", action="store_true")
    listing.set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="显示 task front matter")
    show.add_argument("tid")
    show.add_argument("--ref")
    show.set_defaults(func=cmd_show)

    view = sub.add_parser("view", help="task 调度全景")
    view.set_defaults(func=cmd_view)

    attempt = sub.add_parser("attempt", help="统一 exact attempt 生命周期")
    attempt_sub = attempt.add_subparsers(dest="attempt_cmd", required=True)

    reserve = attempt_sub.add_parser("reserve", help="原子分配 attempt/execution_id")
    reserve.add_argument("tid")
    reserve.add_argument("--executor", required=True, choices=ctx.ATTEMPT_EXECUTORS)
    reserve.add_argument("--model")
    reserve.set_defaults(func=cmd_attempt_reserve)

    bind = attempt_sub.add_parser("bind", help="绑定 agent execution 与宿主 worker")
    bind.add_argument("tid")
    _add_identity(bind)
    bind.add_argument("--host-worker-id")
    bind.set_defaults(func=cmd_attempt_bind)

    terminal = attempt_sub.add_parser("terminal", help="标记 exact attempt 宿主终态")
    terminal.add_argument("tid")
    _add_identity(terminal)
    terminal.add_argument("--status", required=True, choices=ctx.LEDGER_TERMINAL_STATUSES)
    terminal.set_defaults(func=cmd_attempt_terminal)

    report = attempt_sub.add_parser("report", help="记录 exact attempt 业务报告")
    report.add_argument("tid")
    _add_identity(report)
    report.add_argument("--status", required=True, choices=ctx.LEDGER_REPORT_STATUSES)
    report.add_argument("--sha")
    report.add_argument("--class", dest="fail_class", choices=ctx.LEDGER_FAIL_CLASSES)
    report.add_argument("--reason")
    report.set_defaults(func=cmd_attempt_report)

    escalate = attempt_sub.add_parser("escalate", help="terminal attempt 转人工处置")
    escalate.add_argument("tid")
    _add_identity(escalate)
    escalate.add_argument("--reason", required=True)
    escalate.set_defaults(func=cmd_attempt_escalate)

    silent = attempt_sub.add_parser("silent-alert", help="记录 exact fingerprint 静默告警")
    silent.add_argument("tid")
    _add_identity(silent)
    silent.add_argument("--fingerprint", required=True)
    silent.set_defaults(func=cmd_attempt_silent_alert)

    cleanup = sub.add_parser("cleanup-worktree", help="清理 exact terminal attempt worktree")
    cleanup.add_argument("tid")
    _add_identity(cleanup)
    cleanup.set_defaults(func=cmd_cleanup_worktree)

    integrate = sub.add_parser("integrate", help="合并单个 exact terminal attempt")
    integrate.add_argument("tid")
    _add_identity(integrate)
    integrate.add_argument("--continue", dest="continue_merge", action="store_true")
    integrate.add_argument("--keep-branch", action="store_true")
    integrate.set_defaults(func=cmd_integrate)

    chain = sub.add_parser("integrate-chain", help="聚合校验后一次合并线性 task 链尾")
    chain.add_argument("tail_tid")
    chain.add_argument("--continue", dest="continue_merge", action="store_true")
    chain.set_defaults(func=cmd_integrate_chain)

    observe = sub.add_parser("observe", help="观察 exact running attempt")
    observe.add_argument("tid")
    _add_identity(observe)
    observe.add_argument("--json", action="store_true")
    observe.set_defaults(func=cmd_observe)

    ps_parser = sub.add_parser("ps", help="attempt 活表")
    ps_parser.add_argument("--all", action="store_true")
    ps_parser.add_argument("--silent-minutes", type=int, default=30)
    ps_parser.set_defaults(func=cmd_ps)

    reconcile = sub.add_parser("reconcile", help="只读生成 attempt 调度建议")
    reconcile.add_argument("--limit", type=int, default=3)
    reconcile.add_argument("--tids")
    reconcile.add_argument("--model-ladder", default="")
    reconcile.add_argument("--silent-minutes", type=int, default=30)
    reconcile.add_argument("--max-auto-retries", type=int, default=1)
    reconcile.add_argument("--json", action="store_true")
    reconcile.set_defaults(func=cmd_reconcile)

    ledger = sub.add_parser("ledger", help="非生命周期账本记录与读取")
    ledger_sub = ledger.add_subparsers(dest="ledger_cmd", required=True)
    record = ledger_sub.add_parser("record", help="仅 note/breaker")
    record.add_argument("--event", required=True, choices=ctx.LEDGER_RECORDABLE_EVENTS)
    record.add_argument("--tid")
    record.add_argument("--model")
    record.add_argument("--state", choices=ctx.LEDGER_BREAKER_STATES)
    record.add_argument("--reason")
    record.set_defaults(func=cmd_ledger_record)
    tail = ledger_sub.add_parser("tail", help="倒序读取账本")
    tail.add_argument("--tid")
    tail.add_argument("-n", type=int, default=20)
    tail.set_defaults(func=cmd_ledger_tail)

    args = parser.parse_args()
    try:
        args.func(args)
    except ctx.TaskDataError as error:
        sys.exit(f"{error}\n数据不一致；请提示用户处理。")
