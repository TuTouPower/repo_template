"""status / plan 子命令：差异摘要与预览输出。"""

from __future__ import annotations

import argparse

from . import context as ctx
from .agents_merge import agents_section_diagnosis, agents_section_status
from .assets import hard_sync_status, retired_template_warnings
from .common import _git, consumer_locked
from .conventions_migration import conventions_migration_status
from .shared_files import shared_status
from .skills_links import opencode_commands_status, skill_status, symlink_status
from .source import assert_not_self, resolve_src, src_dirty
from .specs_index_migration import specs_index_migration_status, specs_index_report
from .state import active_prompts, prompt_substrings, read_state
from .workflow_migration import workflow_migration_status


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["------"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


@consumer_locked
def cmd_status(args: argparse.Namespace) -> int:
    state = read_state()
    if not state:
        print("未初始化：无 sync_state.json。先运行 init --source <path|url>")
        return 0
    src = resolve_src(state, refresh=getattr(args, "fetch", False))
    if state["template_source"].get("kind") == "url" and not getattr(args, "fetch", False):
        print("来源缓存：未联网刷新（--fetch 刷新；首次会自动建缓存）")
    head = _git(src, "rev-parse", "HEAD").stdout.strip() or "?"
    dirty = src_dirty(src)
    prompts = active_prompts(state)
    print(f"template_source: {state['template_source']['kind']} {state['template_source']['value']}")
    print(f"SRC HEAD: {head}  dirty={dirty}")
    print(f"last_synced_commit: {state.get('last_synced_commit')}")
    print(f"last_synced_at: {state.get('last_synced_at')}")
    print(f"生效 user_prompts: {len(prompts)} 条（共 {len(state.get('user_prompts', []))} 条）")
    for i, p in enumerate(prompts):
        print(f"  #{i} {p.get('at')} {p.get('text')}")
    if (ctx.CONSUMER / "AGENTS.md").is_symlink():
        print("警告: apply 不支持软链 AGENTS.md，先替换为真实文件")
    print("\n硬同步差异:")
    for item in hard_sync_status(src):
        mark = {"write": "需写入", "delete": "需删除", "same": "一致"}[item["state"]]
        print(f"  {item['state']:6s} {item['rel']}  ({mark})")
    print("\nskill:")
    for item in skill_status(src):
        mark = {"override": "强制覆盖", "same": "一致", "keep": "保留", "stale": "旧真实目录待转软链"}[item["action"]]
        print(f"  {item['action']:8s} {item['name']}  ({mark})")
    print("\n裁定单元:")
    for item in shared_status(src):
        print(f"  {item['cls']:14s} {item['unit']}")
    migrations, worktrees, migration_errors = workflow_migration_status(src)
    conv_migrations = conventions_migration_status()
    print("\nworkflow schema 强制迁移:")
    if migration_errors:
        for error in migration_errors:
            print(f"  BLOCKED: {error}")
    if worktrees:
        print("  BLOCKED: 先完成或 rewind task worktree：" + ", ".join(branch for _, branch in worktrees))
    if migrations:
        for path in migrations:
            print(f"  migrate {path}")
    if conv_migrations:
        for path in conv_migrations:
            print(f"  migrate {path}（原子写约定）")
    if not migration_errors and not worktrees and not migrations and not conv_migrations:
        print("  无")
    print("\nformat 豁免 (.prettierignore):")
    dst = ctx.CONSUMER / ".prettierignore"
    existing = {ln.strip() for ln in dst.read_text(encoding="utf-8").splitlines()} if dst.exists() else set()
    missing = [r for r in ctx.PRETTIERIGNORE_TEMPLATE_RULES if r not in existing]
    if missing:
        for rule in missing:
            print(f"  缺失 {rule}（apply 追加）")
    else:
        print("  齐全")
    print("\nopencode 命令薄层 (.opencode/commands):")
    cmd_items = opencode_commands_status()
    drifted_cmds = [it for it in cmd_items if it["state"] not in ("same", "unmanaged")]
    if not cmd_items:
        print("  无")
    elif not drifted_cmds:
        print("  一致")
    else:
        for item in drifted_cmds:
            print(f"  {item['state']:8s} /{item['name']}  ({item['desc']})")
    for warning in retired_template_warnings():
        print(f"  STALE: {warning}")
    print("\nspecs_index 契约:")
    si_migrations = specs_index_migration_status()
    si_problems = specs_index_report()
    for path in si_migrations:
        print(f"  migrate {path}")
    for problem in si_problems:
        print(f"  违规: {problem}")
    if not si_migrations and not si_problems:
        print("  一致")
    return 0


@consumer_locked
def cmd_plan(args: argparse.Namespace) -> int:
    state = read_state()
    if not state:
        print("未初始化：无 sync_state.json。先运行 init --source <path|url>")
        return 0
    src = resolve_src(state, refresh=getattr(args, "fetch", False))
    assert_not_self(src)
    if state["template_source"].get("kind") == "url" and not getattr(args, "fetch", False):
        print("来源缓存：未联网刷新（--fetch 刷新；首次会自动建缓存）")
    head = _git(src, "rev-parse", "HEAD").stdout.strip() or "?"
    dirty = src_dirty(src)
    prompts = active_prompts(state)

    print("## repo-template-sync 预览")
    print(f"\nSRC: {state['template_source']['value']} @ {head}  dirty={dirty}")
    print("\n### 站立指令 user_prompts（裁定优先）")
    if prompts:
        rows = [[str(i), p.get("at", ""), p.get("text", "")] for i, p in enumerate(prompts)]
        print(_md_table(["#", "at", "text"], rows))
    else:
        print("无")

    print("\n### 强制覆盖（同步分类）")
    rows = []
    for item in hard_sync_status(src):
        if item["state"] != "same":
            rows.append([item["rel"], item["state"], "树对树覆盖" if item["state"] == "write" else "删除"])
    for item in skill_status(src):
        if item["action"] == "override":
            rows.append([f".repo_template/skills/{item['name']}", "both_differ", "强制覆盖（含 front matter）"])
        elif item["action"] == "stale":
            rows.append([f".agents/skills/{item['name']}", "stale_dir", "旧真实目录转软链（备份原内容）"])
        elif item["action"] == "keep":
            rows.append([f".agents/skills/{item['name']}", "consumer_only", "保留（禁止删）"])
    print(_md_table(["单元", "分类", "处置"], rows))

    print("\n### 裁定同步 — 逐项（有 diff 必出现）")
    rows = []
    blocked = prompt_substrings(state)
    agents_is_sectioned = agents_section_status(src) is not None
    for item in shared_status(src):
        if item["unit"] == "AGENTS.md" and agents_is_sectioned:
            continue
        if item["cls"] == "both_identical":
            continue
        if item["unit"] == ".gitignore" and item["cls"] in ("both_differ", "template_only"):
            disp = "merge_into_consumer" + ("（遵从 prompt 拦截）" if blocked else "")
        elif item["cls"] == "template_only":
            disp = "建议 update_from_template"
        elif item["cls"] == "consumer_only":
            disp = "keep_consumer"
        else:
            disp = "ask_user / agent 决策"
        rows.append([item["unit"], item["cls"], disp])
    print(_md_table(["单元", "分类", "disposition"], rows))

    print("\n### AGENTS.md 分区")
    if (ctx.CONSUMER / "AGENTS.md").is_symlink():
        print("apply 不支持软链 AGENTS.md，先替换为真实文件")
    section_status = agents_section_status(src)
    if section_status is None:
        print("未识别到完整标题协议：需要 ## 目录与读写规则、## 开发原则")
        for issue in agents_section_diagnosis(src):
            print(f"- {issue}")
    else:
        print(_md_table(["区段", "状态", "处置"], [
            ["项目介绍", section_status["intro"], "绝不更新，保留消费仓内容"],
            ["目录与读写规则", section_status["merge"], "agent 语义合并，脚本不覆盖"],
            ["开发原则", section_status["force"], "每轮强制从模板更新"],
        ]))

    print("\n### 软链")
    rows = []
    for item in symlink_status():
        mark = {
            "ok": "正确",
            "repair": "需建/修",
            "unmanaged": "非本机制，不碰",
            "convert": "需转软链（原内容入回滚栈）",
            "cleanup": "需清理（skill 已删除）",
        }[item["state"]]
        rows.append([item["rel"], item["state"], mark])
    print(_md_table(["路径", "状态", "说明"], rows))

    print("\n### format 豁免（.prettierignore 机械合并）")
    dst = ctx.CONSUMER / ".prettierignore"
    existing = {ln.strip() for ln in dst.read_text(encoding="utf-8").splitlines()} if dst.exists() else set()
    missing = [r for r in ctx.PRETTIERIGNORE_TEMPLATE_RULES if r not in existing]
    if missing:
        rows = [[rule, "缺失", "apply 追加"] for rule in missing]
        print(_md_table(["规则", "状态", "处置"], rows))
    else:
        print("齐全（模板自有路径已豁免）")
    print("\n### opencode 命令薄层 (.opencode/commands)")
    cmd_items = opencode_commands_status()
    drifted_cmds = [it for it in cmd_items if it["state"] not in ("same", "unmanaged")]
    if not drifted_cmds:
        print("齐全一致（各 public skill 与 .opencode/commands 描述一致）")
    else:
        rows = [[item["rel"], item["state"], item["desc"]] for item in drifted_cmds]
        print(_md_table(["路径", "状态", "处置"], rows))
    for warning in retired_template_warnings():
        print(f"- STALE: {warning}")

    print("\n### workflow schema 强制迁移")
    migrations, worktrees, migration_errors = workflow_migration_status(src)
    conv_migrations = conventions_migration_status()
    if migration_errors:
        print("BLOCKED：以下问题阻塞自动迁移：")
        for error in migration_errors:
            print(f"- {error}")
    if worktrees:
        print("BLOCKED：先完成或 rewind 已登记 task worktree：")
        for root, branch in worktrees:
            print(f"- {branch} @ {root}")
    if migrations:
        for path in migrations:
            print(f"- {path}")
    if conv_migrations:
        for path in conv_migrations:
            print(f"- {path}（更新原子写约定为 repo_task.documents.atomic_write_text，保留自定义内容）")
    if not migration_errors and not worktrees and not migrations and not conv_migrations:
        print("无")

    print("\n### state 推进预期")
    if dirty:
        print(f"SRC dirty，apply 可写盘但**不**推进 last_synced_commit")
    else:
        print(f"apply 通过后推进 last_synced_commit -> {head}")
    print("\n### specs_index 契约")
    si_migrations = specs_index_migration_status()
    si_problems = specs_index_report()
    for path in si_migrations:
        print(f"- migrate {path}")
    for problem in si_problems:
        print(f"- 违规: {problem}")
    if not si_migrations and not si_problems:
        print("一致")
    return 0
