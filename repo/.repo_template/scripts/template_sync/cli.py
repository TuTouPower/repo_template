"""repo_sync.py 的参数解析与子命令分发。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from repo_task.context import TaskDataError

from . import context as ctx
from .apply import cmd_apply
from .hooks import cmd_install_hooks
from .shared_files import SHARED_DISPOSITIONS
from .skills_links import cmd_link_skills
from .state import cmd_init, cmd_prompt
from .status_plan import cmd_plan, cmd_status
from .transaction import cmd_transaction


def _parse_decisions(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if ":" not in item:
            raise ctx.SyncError(f"--decision 格式应为 UNIT:DISPOSITION，收到 {item!r}")
        unit, disp = item.split(":", 1)
        if unit in out:
            raise ctx.SyncError(f"--decision 重复 unit {unit!r}")
        if unit not in ctx.SHARED_FILES:
            raise ctx.SyncError(f"--decision 未知 unit {unit!r}（可选：{', '.join(ctx.SHARED_FILES)}）")
        if disp not in SHARED_DISPOSITIONS:
            raise ctx.SyncError(
                f"--decision 未知 disposition {disp!r}（可选：{', '.join(SHARED_DISPOSITIONS)}）"
            )
        out[unit] = disp
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="repo-template-sync 机械化执行器")
    parser.add_argument("--consumer", type=Path, help="目标消费仓；允许直接运行来源脚本而不覆盖本地同步器")
    parser.add_argument("--source", help="本次来源覆盖；status/plan 不写入 state")
    parser.add_argument("--checkout", type=Path, help="本轮固定本地 checkout；不改 canonical source 身份，不自动联网")
    sub = parser.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="初始化 sync_state.json 的 template_source")
    init.add_argument("--source", help="模板源：本机路径或 git remote URL")
    init.set_defaults(func=cmd_init)

    status_parser = sub.add_parser("status", help="读取状态与差异摘要")
    status_parser.add_argument("--fetch", action="store_true", help="URL 源联网刷新缓存（默认离线只读）")
    status_parser.set_defaults(func=cmd_status)
    plan_parser = sub.add_parser("plan", help="计算差异预览（不改消费仓受管资产；URL 首次会建 .scratch 缓存）")
    plan_parser.add_argument("--fetch", action="store_true", help="URL 源联网刷新缓存（默认离线只读）")
    plan_parser.set_defaults(func=cmd_plan)
    transaction = sub.add_parser("transaction", help="持久同步事务的查询、恢复与接受")
    transaction.add_argument("action", choices=("status", "rollback", "accept"))
    transaction.add_argument("--confirm-restore", action="store_true")
    transaction.set_defaults(func=cmd_transaction)

    apply = sub.add_parser("apply", help="执行对齐写盘")
    apply.add_argument("--decision", action="append", default=[], metavar="UNIT:DISP",
                       help="裁定单元决策（可重复），如 AGENTS.md:update / AGENTS.md:keep")
    apply.add_argument("--skip-tests", action="store_true", help="仅跳过模板契约测试（-m contract），仍校验项目策略")
    apply.add_argument("--allow-dirty-source", action="store_true", help="显式开发模式；不推进 resolved commit")
    apply.add_argument("--expected-commit", help="必须与 plan 的完整来源 SHA 相同，否则零写入拒绝")
    apply.add_argument("--agents-file", type=Path, help="在事务内应用已裁定的 AGENTS 语义合并；项目介绍必须保留")
    apply.set_defaults(func=cmd_apply)

    prompt = sub.add_parser("prompt", help="管理 user_prompts")
    prompt.add_argument("action", choices=["list", "add", "revoke"])
    prompt.add_argument("--text", help="prompt 正文（add 用）")
    prompt.add_argument("--tags", help="逗号分隔 tags（add 用）")
    prompt.add_argument("--id", type=int, help="条目下标（revoke 用）")
    prompt.add_argument("--scope", choices=("local", "shared"), default="local", help="local 存本机 state；shared 存可入库 sync_policy")
    prompt.set_defaults(func=cmd_prompt)

    sub.add_parser("link-skills", help="校验并修复宿主 skills 软链，生成 .opencode/commands 命令薄层").set_defaults(func=cmd_link_skills)
    hooks = sub.add_parser(
        "install-hooks",
        help="幂等设置 core.hooksPath 指向工具链 hooks（已有其它值须 --force）；--statusline 改为启用状态行 hook",
    )
    hooks.add_argument("--force", action="store_true", help="覆盖已有的 core.hooksPath")
    hooks.add_argument(
        "--statusline",
        action="store_true",
        help="只把状态行 PostToolUse hook 写入 .claude/settings.json（不改 core.hooksPath）",
    )
    hooks.set_defaults(func=cmd_install_hooks)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.consumer:
        ctx.configure_consumer(args.consumer)
    ctx.SOURCE_OVERRIDE = args.source if args.cmd != "init" else None
    ctx.CHECKOUT_OVERRIDE = args.checkout
    try:
        if getattr(args, "cmd", None) == "apply":
            args.decisions = _parse_decisions(args.decision)
        sys.exit(args.func(args))
    except (ctx.SyncError, TaskDataError) as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        # 文件系统错误统一入口：裸 OSError 会中断多步写盘且无友好消息（F16）
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
