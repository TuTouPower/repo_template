"""sync_state.json 读写、user_prompts 管理，以及 init / prompt 子命令。"""

from __future__ import annotations

import argparse
import json
import sys

from . import context as ctx
from .common import _load_json_object, _now_iso, consumer_locked
from .source import source_identity


def legacy_state_path(sync: ctx.SyncContext):
    return sync.legacy_state_path()


def require_no_legacy_state(sync: ctx.SyncContext) -> None:
    """apply/status/plan/link-skills 入口门禁：旧布局 state 存在即阻断。

    旧布局 state 不参与运行期回退；若放行，apply 的软链修复会删掉宿主位真实目录，
    连带删除 user_prompts、同步记录与来源记录。存在时一律先走显式迁移。
    """
    legacy = sync.legacy_state_path()
    if legacy.exists():
        raise ctx.SyncError(
            f"检测到旧布局 state：{sync.rel(legacy)}；本命令不读取它，"
            "先运行 `python3 .repo_template/scripts/repo_sync.py migrate legacy-state --write`"
        )


def read_state(sync: ctx.SyncContext) -> dict | None:
    """只读当前本地 state；旧布局 state 必须经 `migrate legacy-state` 显式迁移。"""
    path = sync.state_path
    data = _load_json_object(path, f"状态文件 {sync.rel(path)}") if path.exists() else None
    manifest = sync.consumer / ".repo_template/sync_manifest.json"
    if data is None and manifest.exists():
        portable = _load_json_object(manifest, f"清单文件 {sync.rel(manifest)}")
        resolved = portable.get("resolved_commit")
        if not isinstance(resolved, str) or not resolved.strip():
            raise ctx.SyncError(
                f"清单文件 {sync.rel(manifest)} 缺 resolved_commit；"
                "请修复或重新运行 init --source <path|url>"
            )
        data = {"last_synced_commit": resolved, "user_prompts": []}
        if portable.get("source_url"):
            data["template_source"] = {"kind": "url", "value": portable["source_url"]}
    if sync.source_override:
        had_persisted_source = data is not None and "template_source" in data
        data = dict(data or {"last_synced_commit": None, "user_prompts": []})
        if had_persisted_source:
            data["_original_template_source"] = data["template_source"]
        else:
            # 覆盖值是临时的：持久化 state 里本来就没有来源，prompt 不得把它写盘。
            data["_ephemeral_source"] = True
        data["template_source"] = source_identity(sync.source_override)
    policy = sync.consumer / ".repo_template/sync_policy.json"
    if data is not None and policy.exists():
        policy_data = _load_json_object(policy, f"策略文件 {sync.rel(policy)}")
        prompts = policy_data.get("user_prompts", [])
        if not isinstance(prompts, list):
            raise ctx.SyncError(f"策略文件 {sync.rel(policy)} 的 user_prompts 必须是数组")
        data["_shared_user_prompts"] = prompts
    return data


def write_state(data: dict, sync: ctx.SyncContext) -> None:
    from repo_task.documents import atomic_write_text
    sync.state_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        {key: value for key, value in data.items() if not key.startswith("_")},
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    atomic_write_text(sync.state_path, text)


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
    sync: ctx.SyncContext = args.sync
    if not args.source:
        raise ctx.SyncError("init 需要 --source <path|url>")
    legacy = legacy_state_path(sync)
    if not sync.state_path.exists() and legacy.exists():
        raise ctx.SyncError(
            f"检测到旧布局 state：{sync.rel(legacy)}；普通 init 不再隐式合并。"
            "先运行 `python3 .repo_template/scripts/repo_sync.py migrate legacy-state`"
        )
    identity = source_identity(args.source)
    data = read_state(sync) or {
        "template_source": None,
        "last_synced_commit": None,
        "last_synced_at": None,
        "user_prompts": [],
    }
    data["template_source"] = identity
    kind = data["template_source"]["kind"]
    write_state(data, sync)
    print(f"template_source 已写：kind={kind}, value={data['template_source']['value']}")
    return 0


def migrate_legacy_state(sync: ctx.SyncContext, *, write: bool = False) -> list[str]:
    """迁移旧布局 state。默认 dry-run，仅在 write=True 时写盘与删除旧文件。"""
    legacy = legacy_state_path(sync)
    if not legacy.exists():
        return []
    try:
        legacy_data = json.loads(legacy.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ctx.SyncError(
            f"旧布局 state 无法解析，保留原文件不删除：{sync.rel(legacy)}（{error}）"
        ) from error
    if not isinstance(legacy_data, dict):
        raise ctx.SyncError(f"旧布局 state 顶层不是对象，保留原文件：{sync.rel(legacy)}")
    data = read_state(sync) or {
        "template_source": None,
        "last_synced_commit": None,
        "last_synced_at": None,
        "user_prompts": [],
    }
    merged = _merge_legacy_state(dict(data), legacy_data)
    if not write:
        return [
            f"{sync.rel(legacy)} 拟迁移至 {sync.rel(sync.state_path)}"
            + (f"（并入字段 {', '.join(sorted(set(merged)))}）" if merged else "")
        ]
    _merge_legacy_state(data, legacy_data)
    write_state(data, sync)
    try:
        legacy.unlink()
    except OSError as error:
        raise ctx.SyncError(
            f"state 已迁移，但旧文件删除失败：{sync.rel(legacy)}（{error}）；请人工删除"
        ) from error
    return [
        f"已迁移旧布局 state：{sync.rel(legacy)} → {sync.rel(sync.state_path)}"
        + (f"；并入字段 {', '.join(sorted(set(merged)))}" if merged else "")
    ]


@consumer_locked
def cmd_prompt(args: argparse.Namespace) -> int:
    sync: ctx.SyncContext = args.sync
    state = read_state(sync)
    if not state:
        raise ctx.SyncError("未初始化：无 sync_state.json。先运行 init --source <path|url>")
    if state.pop("_ephemeral_source", False):
        raise ctx.SyncError(
            "state 未记录来源：先运行 init --source <path|url> 初始化来源；"
            "--source 仅为本次覆盖，不写入 state"
        )
    shared = getattr(args, "scope", "local") == "shared"
    policy_path = sync.consumer / ".repo_template/sync_policy.json"
    data = (json.loads(policy_path.read_text(encoding="utf-8")) if policy_path.exists() else {"version": 1, "user_prompts": []}) if shared else state
    def persist():
        if shared:
            from repo_task.documents import atomic_write_text
            atomic_write_text(policy_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        else:
            if "_original_template_source" in data:
                data["template_source"] = data.pop("_original_template_source")
            write_state(data, sync)
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
