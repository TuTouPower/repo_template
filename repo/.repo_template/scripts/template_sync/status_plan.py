"""status / plan 子命令：差异摘要与预览输出。"""

from __future__ import annotations

import argparse

from . import context as ctx
from .agents_merge import agents_section_diagnosis, agents_section_status
from .assets import hard_sync_status, retired_template_warnings
from .common import _git, consumer_locked
from .migrate import migrate_command
from .preflight import sync_preflight
from .shared_files import shared_status
from .skills_links import opencode_commands_status, skill_status, symlink_status
from .source import assert_not_self, require_template_source, resolve_src, src_dirty
from .state import active_prompts, prompt_substrings, read_state, require_no_legacy_state


def _print_preflight(sync: ctx.SyncContext, src) -> None:
    result = sync_preflight(src, sync)
    print(f"BLOCKED: {result.error}" if result.error else "同步预检通过")
    for kind, _findings in result.migrations:
        print(f"  {kind}: {migrate_command(src, sync, kind)}")


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["------"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _opencode_section(sync) -> tuple[list[dict] | None, str | None]:
    """status/plan 专用的 registry 降级：缺失/未登记时返回 BLOCKED 指引，不抛错。

    旧消费仓（registry 引入前）须能完成升级前评审；apply / link-skills 仍由
    opencode_commands_status 直接 fail-closed。此处仅捕获 registry 相关的
    SyncError，其余异常继续上抛。
    """
    try:
        return opencode_commands_status(sync=sync), None
    except ctx.SyncError as error:
        message = str(error)
        if "registry.json" in message or "未登记" in message or "条目结构错误" in message:
            return None, (
                f"BLOCKED: {message}；status/plan 仅展示，其余同步前须补登记"
            )
        raise


@consumer_locked
def cmd_status(args: argparse.Namespace) -> int:
    sync: ctx.SyncContext = args.sync
    require_no_legacy_state(sync)
    state = read_state(sync)
    if not state:
        print("未初始化：无 sync_state.json。先运行 init --source <path|url>")
        return 0
    src = resolve_src(state, refresh=getattr(args, "fetch", False), sync=sync)
    assert_not_self(src, sync=sync)
    kind, _ = require_template_source(state)
    if kind == "url" and not getattr(args, "fetch", False):
        print("来源缓存：未联网刷新（--fetch 刷新；首次会自动建缓存）")
    head = _git(src, "rev-parse", "HEAD").stdout.strip() or "?"
    dirty = src_dirty(src)
    prompts = active_prompts(state)
    kind_value, source_value = require_template_source(state)
    print(f"template_source: {kind_value} {source_value}")
    print(f"SRC HEAD: {head}  dirty={dirty}")
    print(f"last_synced_commit: {state.get('last_synced_commit')}")
    print(f"last_synced_at: {state.get('last_synced_at')}")
    print(f"生效 user_prompts: {len(prompts)} 条（共 {len(state.get('user_prompts', []))} 条）")
    for i, p in enumerate(prompts):
        print(f"  #{i} {p.get('at')} {p.get('text')}")
    if (sync.consumer / "AGENTS.md").is_symlink():
        print("警告: apply 不支持软链 AGENTS.md，先替换为真实文件")
    print("\n硬同步差异:")
    for item in hard_sync_status(src, sync=sync):
        mark = {"write": "需写入", "delete": "需删除", "same": "一致"}[item["state"]]
        print(f"  {item['state']:6s} {item['rel']}  ({mark})")
    print("\nskill:")
    for item in skill_status(src, sync=sync):
        mark = {"override": "强制覆盖", "same": "一致", "keep": "保留", "stale": "旧真实目录待转软链"}[item["action"]]
        print(f"  {item['action']:8s} {item['name']}  ({mark})")
    print("\n裁定单元:")
    for item in shared_status(src, sync=sync):
        print(f"  {item['cls']:14s} {item['unit']}")
    print("\nformat 豁免 (.prettierignore):")
    dst = sync.consumer / ".prettierignore"
    existing = {ln.strip() for ln in dst.read_text(encoding="utf-8").splitlines()} if dst.exists() else set()
    missing = [r for r in ctx.PRETTIERIGNORE_TEMPLATE_RULES if r not in existing]
    if missing:
        for rule in missing:
            print(f"  缺失 {rule}（apply 追加）")
    else:
        print("  齐全")
    print("\nopencode 命令薄层 (.opencode/commands):")
    cmd_items, cmd_blocked = _opencode_section(sync)
    if cmd_blocked is not None:
        print(f"  {cmd_blocked}")
        drifted_cmds: list[dict] = []
    else:
        assert cmd_items is not None
        drifted_cmds = [it for it in cmd_items if it["state"] not in ("same", "unmanaged")]
        if not cmd_items:
            print("  无")
        elif not drifted_cmds:
            print("  一致")
        else:
            for item in drifted_cmds:
                print(f"  {item['state']:8s} /{item['name']}  ({item['desc']})")
    for warning in retired_template_warnings(sync=sync):
        print(f"  STALE: {warning}")
    print("\n迁移待办与契约预检（与 plan / apply 同一判定）:")
    _print_preflight(sync, src)
    return 0


@consumer_locked
def cmd_plan(args: argparse.Namespace) -> int:
    sync: ctx.SyncContext = args.sync
    require_no_legacy_state(sync)
    state = read_state(sync)
    if not state:
        print("未初始化：无 sync_state.json。先运行 init --source <path|url>")
        return 0
    src = resolve_src(state, refresh=getattr(args, "fetch", False), sync=sync)
    assert_not_self(src, sync=sync)
    kind, source_value = require_template_source(state)
    if kind == "url" and not getattr(args, "fetch", False):
        print("来源缓存：未联网刷新（--fetch 刷新；首次会自动建缓存）")
    head = _git(src, "rev-parse", "HEAD").stdout.strip() or "?"
    dirty = src_dirty(src)
    prompts = active_prompts(state)

    print("## repo-template-sync 预览")
    print(f"\nSRC: {source_value} @ {head}  dirty={dirty}")
    print("\n### 站立指令 user_prompts（裁定优先）")
    if prompts:
        rows = [[str(i), p.get("at", ""), p.get("text", "")] for i, p in enumerate(prompts)]
        print(_md_table(["#", "at", "text"], rows))
    else:
        print("无")

    print("\n### 强制覆盖（同步分类）")
    rows = []
    for item in hard_sync_status(src, sync=sync):
        if item["state"] != "same":
            rows.append([item["rel"], item["state"], "树对树覆盖" if item["state"] == "write" else "删除"])
    for item in skill_status(src, sync=sync):
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
    agents_is_sectioned = agents_section_status(src, sync=sync) is not None
    for item in shared_status(src, sync=sync):
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
    if (sync.consumer / "AGENTS.md").is_symlink():
        print("apply 不支持软链 AGENTS.md，先替换为真实文件")
    section_status = agents_section_status(src, sync=sync)
    if section_status is None:
        print("未识别到完整两区标记，先运行 `repo_sync.py migrate agents-zones --write`")
        for issue in agents_section_diagnosis(src, sync=sync):
            print(f"- {issue}")
    else:
        rows = [
            ["项目区", section_status["project"], "消费仓所有，原样保留"],
            ["模板区", section_status["template"], "模板所有，整块替换"],
        ]
        print(_md_table(["区段", "状态", "处置"], rows))

    print("\n### 软链")
    rows = []
    for item in symlink_status(sync=sync):
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
    dst = sync.consumer / ".prettierignore"
    existing = {ln.strip() for ln in dst.read_text(encoding="utf-8").splitlines()} if dst.exists() else set()
    missing = [r for r in ctx.PRETTIERIGNORE_TEMPLATE_RULES if r not in existing]
    if missing:
        rows = [[rule, "缺失", "apply 追加"] for rule in missing]
        print(_md_table(["规则", "状态", "处置"], rows))
    else:
        print("齐全（模板自有路径已豁免）")
    print("\n### opencode 命令薄层 (.opencode/commands)")
    cmd_items, cmd_blocked = _opencode_section(sync)
    if cmd_blocked is not None:
        print(f"- BLOCKED: {cmd_blocked}")
        drifted_cmds: list[dict] = []
    else:
        assert cmd_items is not None
        drifted_cmds = [it for it in cmd_items if it["state"] not in ("same", "unmanaged")]
        if not drifted_cmds:
            print("齐全一致（各 public skill 与 .opencode/commands 描述一致）")
        else:
            rows = [[item["rel"], item["state"], item["desc"]] for item in drifted_cmds]
            print(_md_table(["路径", "状态", "处置"], rows))
    for warning in retired_template_warnings(sync=sync):
        print(f"- STALE: {warning}")

    print("\n### state 推进预期")
    if dirty:
        print(f"SRC dirty，apply 可写盘但**不**推进 last_synced_commit")
    else:
        print(f"apply 通过后推进 last_synced_commit -> {head}")
    print("\n### 迁移待办与契约预检（与 status / apply 同一判定）")
    _print_preflight(sync, src)
    return 0
