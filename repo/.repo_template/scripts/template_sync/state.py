"""sync_state.json 读写、user_prompts 管理，以及 init / prompt 子命令。"""

from __future__ import annotations

import argparse
import json
import sys

from . import context as ctx
from .common import _load_json_object, _now_iso, consumer_locked
from .source import source_identity


def read_state() -> dict | None:
    path = ctx.STATE_PATH if ctx.STATE_PATH.exists() else ctx.LEGACY_STATE_PATH
    data = _load_json_object(path, f"状态文件 {ctx._rel(path)}") if path.exists() else None
    manifest = ctx.CONSUMER / ".repo_template/sync_manifest.json"
    if data is None and manifest.exists():
        portable = _load_json_object(manifest, f"清单文件 {ctx._rel(manifest)}")
        resolved = portable.get("resolved_commit")
        if not isinstance(resolved, str) or not resolved.strip():
            raise ctx.SyncError(
                f"清单文件 {ctx._rel(manifest)} 缺 resolved_commit；"
                "请修复或重新运行 init --source <path|url>"
            )
        data = {"last_synced_commit": resolved, "user_prompts": []}
        if portable.get("source_url"):
            data["template_source"] = {"kind": "url", "value": portable["source_url"]}
    if ctx.SOURCE_OVERRIDE:
        had_persisted_source = data is not None and "template_source" in data
        data = dict(data or {"last_synced_commit": None, "user_prompts": []})
        if had_persisted_source:
            data["_original_template_source"] = data["template_source"]
        else:
            # 覆盖值是临时的：持久化 state 里本来就没有来源，prompt 不得把它写盘。
            data["_ephemeral_source"] = True
        data["template_source"] = source_identity(ctx.SOURCE_OVERRIDE)
    policy = ctx.CONSUMER / ".repo_template/sync_policy.json"
    if data is not None and policy.exists():
        policy_data = _load_json_object(policy, f"策略文件 {ctx._rel(policy)}")
        prompts = policy_data.get("user_prompts", [])
        if not isinstance(prompts, list):
            raise ctx.SyncError(f"策略文件 {ctx._rel(policy)} 的 user_prompts 必须是数组")
        data["_shared_user_prompts"] = prompts
    return data


def write_state(data: dict) -> None:
    from repo_task.documents import atomic_write_text
    ctx.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        {key: value for key, value in data.items() if not key.startswith("_")},
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    atomic_write_text(ctx.STATE_PATH, text)


def active_prompts(state: dict | None) -> list[dict]:
    """返回未 revoked 的 user_prompts 条目。"""
    if not state:
        return []
    return [p for p in state.get("user_prompts", []) + state.get("_shared_user_prompts", []) if not p.get("revoked")]


def prompt_substrings(state: dict | None) -> list[str]:
    """从活跃 prompt 的 tags 提取拦截子串（供 .gitignore 规则匹配）。"""
    out: list[str] = []
    for p in active_prompts(state):
        for tag in p.get("tags", []):
            t = str(tag).strip()
            if t and t not in out:
                out.append(t)
    return out


def _merge_legacy_state(data: dict, legacy: dict) -> list[str]:
    """把 legacy 中未被新 state 吸收的字段并入 data，返回并入的键。"""
    merged = []
    for key, value in legacy.items():
        if key == "user_prompts":
            continue
        if key not in data or data[key] in (None, "", [], {}):
            data[key] = value
            merged.append(key)
    current = data.setdefault("user_prompts", [])
    if not isinstance(current, list):
        current = []
        data["user_prompts"] = current
    seen = {(str(item.get("at", "")), str(item.get("text", "")))
            for item in current if isinstance(item, dict)}
    for item in legacy.get("user_prompts", []) or []:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("at", "")), str(item.get("text", "")))
        if key not in seen:
            current.append(item)
            seen.add(key)
            merged.append("user_prompts")
    return merged


@consumer_locked
def cmd_init(args: argparse.Namespace) -> int:
    if not args.source:
        raise ctx.SyncError("init 需要 --source <path|url>")
    identity = source_identity(args.source)
    legacy = None
    legacy_readable = True
    if ctx.LEGACY_STATE_PATH.exists():
        try:
            legacy = json.loads(ctx.LEGACY_STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            legacy = None
            legacy_readable = False
            print(
                f"WARNING: 旧布局 state 无法解析，保留原文件不删除："
                f"{ctx.LEGACY_STATE_PATH.relative_to(ctx.CONSUMER).as_posix()}",
                file=sys.stderr,
            )
    if not ctx.STATE_PATH.exists():
        base = legacy if isinstance(legacy, dict) else read_state() or {
            "template_source": None,
            "last_synced_commit": None,
            "last_synced_at": None,
            "user_prompts": [],
        }
        write_state(base)
        if isinstance(legacy, dict):
            print(f"已迁移旧布局 state：{ctx.LEGACY_STATE_PATH.relative_to(ctx.CONSUMER).as_posix()} → {ctx.STATE_PATH.relative_to(ctx.CONSUMER).as_posix()}")
        legacy = None
    data = read_state()
    data["template_source"] = identity
    if isinstance(legacy, dict):
        merged = _merge_legacy_state(data, legacy)
        if merged:
            print("旧布局 state 已合并字段：" + ", ".join(sorted(set(merged))))
    kind = data["template_source"]["kind"]
    write_state(data)
    if ctx.LEGACY_STATE_PATH.exists() and legacy_readable:
        try:
            ctx.LEGACY_STATE_PATH.unlink()
            print(f"旧布局 state 已移除：{ctx.LEGACY_STATE_PATH.relative_to(ctx.CONSUMER).as_posix()}")
        except OSError:
            pass
    print(f"template_source 已写：kind={kind}, value={data['template_source']['value']}")
    return 0


@consumer_locked
def cmd_prompt(args: argparse.Namespace) -> int:
    state = read_state()
    if not state:
        raise ctx.SyncError("未初始化：无 sync_state.json。先运行 init --source <path|url>")
    if state.pop("_ephemeral_source", False):
        raise ctx.SyncError(
            "state 未记录来源：先运行 init --source <path|url> 初始化来源；"
            "--source 仅为本次覆盖，不写入 state"
        )
    shared = getattr(args, "scope", "local") == "shared"
    policy_path = ctx.CONSUMER / ".repo_template/sync_policy.json"
    data = (json.loads(policy_path.read_text(encoding="utf-8")) if policy_path.exists() else {"version": 1, "user_prompts": []}) if shared else state
    def persist():
        if shared:
            from repo_task.documents import atomic_write_text
            atomic_write_text(policy_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        else:
            if "_original_template_source" in data:
                data["template_source"] = data.pop("_original_template_source")
            write_state(data)
    if args.action == "list":
        for i, p in enumerate(data.get("user_prompts", [])):
            rev = " [revoked]" if p.get("revoked") else ""
            print(f"#{i}{rev} {p.get('at')} tags={p.get('tags', [])} {p.get('text')}")
        return 0
    prompts = data.setdefault("user_prompts", [])
    if args.action == "add":
        if not args.text:
            raise ctx.SyncError("prompt add 需要 --text")
        tags = [t.strip().lower() for t in (args.tags or "").split(",") if t.strip()]
        for p in prompts:
            if p.get("revoked"):
                continue
            old_tags = {str(t).lower() for t in p.get("tags", [])}
            if old_tags & set(tags):
                p["revoked"] = True
        prompts.append({"at": _now_iso(), "text": args.text, "tags": tags, "revoked": False})
        persist()
        print(f"已登记 prompt #{len(prompts) - 1}: {args.text}")
        return 0
    if args.action == "revoke":
        idx = args.id
        if idx is None or not (0 <= idx < len(prompts)):
            raise ctx.SyncError(f"revoke 需要合法 --id（0..{len(prompts) - 1}）")
        prompts[idx]["revoked"] = True
        persist()
        print(f"已作废 prompt #{idx}")
        return 0
    return 0
