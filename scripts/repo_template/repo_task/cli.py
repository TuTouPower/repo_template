"""Argument parser and command routing for task.py."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import repo_task.context as ctx

from .control import cmd_ledger_record, cmd_ledger_tail, cmd_observe, cmd_ps, cmd_reconcile, cmd_view
from .integration import cmd_cleanup_worktree, cmd_integrate, cmd_start
from .lifecycle import (
    cmd_add, cmd_block, cmd_drop, cmd_edit, cmd_finish, cmd_list, cmd_preflight,
    cmd_purge, cmd_resume, cmd_rewind, cmd_show,
)

def main():
    p = argparse.ArgumentParser(
        description="task 状态入口（状态权威 = task.md front matter；JSON 为派生缓存）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="新增 backlog task：分配 tid、从模板建目录、写 front matter")
    a.add_argument("--title", required=True)
    a.add_argument("--slug", required=True)
    a.add_argument("--note", default="")
    a.add_argument("--review-level", choices=ctx.REVIEW_LEVELS, default=ctx.DEFAULT_REVIEW_LEVEL)
    a.set_defaults(func=cmd_add)

    e = sub.add_parser("edit", help="改 main 中未进入链的 backlog task")
    e.add_argument("tid")
    e.add_argument("--title")
    e.add_argument("--note", help="覆盖 note（传空串则清空）")
    e.add_argument("--note-append", help="在现有 note 后追加")
    e.add_argument("--review-level", choices=ctx.REVIEW_LEVELS)
    e.add_argument("--depends-on", help="逗号分隔 tid；传空串清空")
    e.add_argument("--depends-append", help="追加一个依赖 tid")
    e.add_argument("--depends-remove", help="移除一个依赖 tid")
    e.add_argument("--conflicts-with", help="逗号分隔 tid；传空串清空并同步反向边")
    e.add_argument("--conflicts-append", help="追加一个冲突 tid 并同步反向边")
    e.add_argument("--conflicts-remove", help="移除一个冲突 tid 并同步反向边")
    e.add_argument("--schedule-status", choices=ctx.SCHEDULE_STATUSES)
    e.set_defaults(func=cmd_edit)

    s = sub.add_parser(
        "start",
        help="backlog -> active：从主干或上一 task 分支建 worktree，不修改主仓",
    )
    s.add_argument("tid")
    s.add_argument(
        "--base",
        help="上一已完成 task 的本地分支（串行链式）；省略时从主干 HEAD 扇出（并行）",
    )
    s.set_defaults(func=cmd_start)

    pf = sub.add_parser("preflight", help="开干前门禁：分支/worktree/工作区/spec/索引交叉校验")
    pf.add_argument("tid")
    pf.add_argument(
        "--allow-backlog",
        action="store_true",
        help="只读检查尚未 start 的 backlog 是否具备开干条件",
    )
    pf.add_argument(
        "--ref",
        help="只读检查指定本地分支快照；不检查 worktree 与当前脏改动",
    )
    pf.add_argument(
        "--require-verified",
        action="store_true",
        help="要求未知契约清单不再含 UNVERIFIED-SPIKE（进入实现前使用）",
    )
    pf.set_defaults(func=cmd_preflight)

    b = sub.add_parser("block", help="active -> blocked")
    b.add_argument("tid")
    b.add_argument("--reason", required=True, choices=ctx.BLOCK_REASONS)
    b.set_defaults(func=cmd_block)

    r = sub.add_parser("resume", help="blocked -> active（用户加轮或排除阻塞后）")
    r.add_argument("tid")
    r.set_defaults(func=cmd_resume)

    f = sub.add_parser("finish", help="active -> done；目录归档，提交后从主仓清理 worktree")
    f.add_argument("tid")
    f.set_defaults(func=cmd_finish)

    cw = sub.add_parser(
        "cleanup-worktree",
        help="从主仓清理已提交的 task worktree，保留 task 分支",
    )
    cw.add_argument("tid")
    cw.add_argument(
        "--attempt", type=int,
        help="并行 dispatch 的当前 attempt；串行链式路径可省略",
    )
    cw.set_defaults(func=cmd_cleanup_worktree)

    ig = sub.add_parser(
        "integrate",
        help="把已完成 task 分支合并进主干、重建 index、删除分支",
    )
    ig.add_argument("tid")
    ig.add_argument(
        "--attempt", type=int,
        help="并行 dispatch 的当前 attempt；串行链式路径可省略",
    )
    ig.add_argument(
        "--continue",
        dest="continue_merge",
        action="store_true",
        help="冲突解决并 git add 后继续未完成的 merge",
    )
    ig.add_argument(
        "--keep-branch",
        action="store_true",
        help="合并后保留 task 分支",
    )
    ig.add_argument(
        "--chain",
        action="store_true",
        help="串行链式：只合链尾，祖先自动跟随，删除整条链的 task 分支",
    )
    ig.set_defaults(func=cmd_integrate)

    d = sub.add_parser("drop", help="任意活跃状态 -> dropped；目录归档")
    d.add_argument("tid")
    d.add_argument("--reason", required=True)
    d.set_defaults(func=cmd_drop)

    rw = sub.add_parser("rewind", help="状态撤回（active->backlog / blocked->active；默认撤一步）")
    rw.add_argument("tid")
    rw.add_argument("--to", choices=("backlog", "active"))
    rw.add_argument("--yes", action="store_true",
                    help="跳过「分支有未合并 commit」的交互确认（agent/脚本场景用）")
    rw.add_argument("--reason", required=True)
    rw.set_defaults(func=cmd_rewind)

    pg = sub.add_parser("purge", help="误建彻底删除（仅 backlog 且任一分支都未跟踪；审计留快照）")
    pg.add_argument("tid")
    pg.add_argument("--reason", required=True)
    pg.set_defaults(func=cmd_purge)

    ls = sub.add_parser("list", help="列出当前工作区或本地分支快照的 task")
    ls.add_argument("--status", choices=ctx.VALID_STATUSES)
    ls.add_argument("--ref", help="只读查看指定本地分支中的 task 状态")
    ls.add_argument("--rebuild", action="store_true", help="重建派生索引 JSON（默认只读不写）")
    ls.set_defaults(func=cmd_list)

    sh = sub.add_parser("show", help="显示当前工作区或本地分支中的 task front matter")
    sh.add_argument("tid")
    sh.add_argument("--ref", help="只读查看指定本地分支中的 task 状态")
    sh.set_defaults(func=cmd_show)

    nb = sub.add_parser("view", help="task 全景：运行中 / 待运行分组 / 已结束")
    nb.set_defaults(func=cmd_view)

    ob = sub.add_parser("observe", help="观察在飞 attempt 的仓库状态指纹")
    ob.add_argument("tid")
    ob.add_argument("--attempt", type=int, required=True)
    ob.add_argument("--json", action="store_true", help="输出 JSON")
    ob.set_defaults(func=cmd_observe)

    ps_p = sub.add_parser(
        "ps",
        help="调度活表：tid / attempt / model / worker_id / state / last_activity / note",
    )
    ps_p.add_argument("--all", action="store_true", help="包含主干已 done/dropped 的终态行")
    ps_p.add_argument("--silent-minutes", type=int, default=30,
                      help="fingerprint 连续不变超过该分钟数判 silent?（默认 30）")
    ps_p.set_defaults(func=cmd_ps)

    rc = sub.add_parser(
        "reconcile",
        help="只读计算调度行动计划（含 alert-silent），零副作用",
    )
    rc.add_argument("--limit", type=int, default=3, help="并发上限（默认 3）")
    rc.add_argument("--tids", help="逗号分隔 tid；授权范围，省略=全部")
    rc.add_argument("--model-ladder", default="",
                    help="模型阶梯，如 'opus>haiku'；显式 infra 失败自动降档")
    rc.add_argument("--silent-minutes", type=int, default=30,
                    help="fingerprint 连续不变超过该分钟数告警（默认 30）")
    rc.add_argument("--max-auto-retries", type=int, default=1,
                    help="每 tid 显式失败自动重派额度，用尽转 escalate（默认 1）")
    rc.add_argument("--json", action="store_true", help="输出 JSON 计划")
    rc.set_defaults(func=cmd_reconcile)

    lg = sub.add_parser("ledger", help="调度账本：record 追加事件 / tail 读末 N 条")
    lg_sub = lg.add_subparsers(dest="ledger_cmd", required=True)

    lr = lg_sub.add_parser("record", help="追加一条账本事件")
    lr.add_argument("--event", required=True, choices=ctx.LEDGER_EVENTS)
    lr.add_argument("--tid")
    lr.add_argument(
        "--attempt",
        type=int,
        help=(
            "dispatch 可省略并自动取该 tid 的下一 attempt；"
            "report/failed/escalated/silent_alerted 必须显式提供"
        ),
    )
    lr.add_argument("--model")
    lr.add_argument("--worker-id", help="dispatch 对应的宿主后台任务 ID")
    lr.add_argument(
        "--status",
        choices=tuple(dict.fromkeys(ctx.LEDGER_REPORT_STATUSES + ctx.LEDGER_TERMINAL_STATUSES)),
        help="report 使用 done/blocked/failed；worker_terminal 使用 completed/failed/stopped",
    )
    lr.add_argument("--sha")
    lr.add_argument("--class", dest="fail_class", choices=ctx.LEDGER_FAIL_CLASSES)
    lr.add_argument("--state", choices=ctx.LEDGER_BREAKER_STATES,
                    help="breaker 事件用；省略时默认 open")
    lr.add_argument("--fingerprint", help="silent_alerted 对应的仓库状态指纹")
    lr.add_argument("--reason")
    lr.set_defaults(func=cmd_ledger_record)

    lt = lg_sub.add_parser("tail", help="倒序读账本末 N 条")
    lt.add_argument("--tid", help="只看该 tid")
    lt.add_argument("-n", type=int, default=20, help="条数（默认 20）")
    lt.set_defaults(func=cmd_ledger_tail)


    args = p.parse_args()
    try:
        args.func(args)
    except ctx.TaskDataError as e:
        sys.exit(f"{e}\n数据不一致；请提示用户处理。")
