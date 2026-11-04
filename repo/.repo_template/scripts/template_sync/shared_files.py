"""共享文件机械合并：.gitignore / .prettierignore / MCP，以及裁定单元决策。"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import context as ctx
from .agents_merge import _apply_agents_sectioned
from .assets import _file_differs
from .common import _detect_json_indent

SHARED_DISPOSITIONS = (
    "merge",
    "merge_into_consumer",
    "update",
    "update_from_template",
    "keep",
    "keep_consumer",
)


def _blocked(rule: str, substrings: list[str]) -> bool:
    return any(sub in rule for sub in substrings)


def merge_gitignore(src: Path, blocked: list[str], changed: set[Path]) -> dict:
    """追加模板独有规则（去重、遵从 prompt 拦截）；删除消费侧 prompt 禁止的 ignore 行。"""
    dst = ctx.CONSUMER / ".gitignore"
    src_lines = (src / ".gitignore").read_text(encoding="utf-8").splitlines()
    dst_lines = dst.read_text(encoding="utf-8").splitlines() if dst.exists() else []
    dst_set = {ln.strip() for ln in dst_lines}
    added: list[str] = []
    removed: list[str] = []
    for ln in src_lines:
        stripped = ln.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped in dst_set:
            continue
        if not stripped.startswith("!") and _blocked(stripped, blocked):
            continue
        added.append(stripped)
    if blocked:
        new_dst: list[str] = []
        for ln in dst_lines:
            stripped = ln.strip()
            if (
                stripped
                and not stripped.startswith("#")
                and not stripped.startswith("!")
                and _blocked(stripped, blocked)
            ):
                removed.append(stripped)
                continue
            new_dst.append(ln)
        dst_lines = new_dst
    if added or removed:
        if added:
            dst_lines.append("")
            dst_lines.append("# 同步自模板（repo-template-sync）")
            dst_lines.extend(added)
        text = "\n".join(dst_lines)
        if not text.endswith("\n"):
            text += "\n"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(text.encode("utf-8"))
        changed.add(dst)
    return {"added": added, "removed": removed, "dst": ctx._rel(dst)}


_PRETTIERIGNORE_OLD_HEADER = "# 同步自模板（repo-template-sync）：模板自有路径不受消费仓 format 门禁约束"
_PRETTIERIGNORE_HEADER = (
    "# 同步自模板（repo-template-sync）：模板自有/本地生成路径不受消费仓 format 门禁约束；",
    "# 部分条目（sync_manifest.json / sync_policy.json / .opencode/package*.json）按需生成，不存在时仅为预留",
)


def merge_prettierignore(changed: set[Path]) -> dict:
    """机械合并 .prettierignore：模板自有路径去重追加，消费独有规则保留。"""
    dst = ctx.CONSUMER / ".prettierignore"
    dst_lines = dst.read_text(encoding="utf-8").splitlines() if dst.exists() else []
    dst_set = {ln.strip() for ln in dst_lines}
    added: list[str] = []
    for rule in ctx.PRETTIERIGNORE_TEMPLATE_RULES:
        if rule not in dst_set:
            added.append(rule)
    header_changed = False
    for index, line in enumerate(dst_lines):
        if line.strip() == _PRETTIERIGNORE_OLD_HEADER:
            dst_lines[index:index + 1] = list(_PRETTIERIGNORE_HEADER)
            header_changed = True
            break
    if added or header_changed:
        if added:
            if dst_lines and dst_lines[-1].strip():
                dst_lines.append("")
            if not any(line.strip() == _PRETTIERIGNORE_HEADER[0] for line in dst_lines):
                dst_lines.extend(_PRETTIERIGNORE_HEADER)
            dst_lines.extend(added)
        text = "\n".join(dst_lines)
        if not text.endswith("\n"):
            text += "\n"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(text.encode("utf-8"))
        changed.add(dst)
    return {"added": added, "dst": ctx._rel(dst)}


_SECRET_KEY_RE = re.compile(
    r"(?i)(api[-_]?key|apikey|token|password|passwd|secret|auth|credential)"
)


def _sanitize_mcp_args(args: list) -> list:
    out = list(args)
    index = 0
    while index < len(out):
        item = out[index]
        if isinstance(item, str) and item.startswith("-") and "=" in item:
            flag, _value = item.split("=", 1)
            if _SECRET_KEY_RE.search(flag):
                out[index] = f"{flag}=<{flag.lstrip('-')}_PLACEHOLDER>"
        elif (
            isinstance(item, str)
            and item.startswith("-")
            and _SECRET_KEY_RE.search(item)
            and index + 1 < len(out)
            and isinstance(out[index + 1], str)
            and not out[index + 1].startswith("-")
        ):
            out[index + 1] = f"<{item.lstrip('-')}_PLACEHOLDER>"
            index += 1
        index += 1
    return out


def _sanitize_mcp_url(value: str) -> str:
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError as error:
        raise ctx.SyncError("MCP URL 端口非法，无法解析") from error
    changed = False
    netloc = parts.netloc
    if parts.username is not None or parts.password is not None:
        host = parts.hostname or ""
        port_text = f":{port}" if port else ""
        if parts.password is not None:
            netloc = f"<url_user_PLACEHOLDER>:<url_password_PLACEHOLDER>@{host}{port_text}"
        else:
            netloc = f"<url_user_PLACEHOLDER>@{host}{port_text}"
        changed = True
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    new_pairs = []
    for key, val in pairs:
        if _SECRET_KEY_RE.search(key):
            new_pairs.append((key, f"<{key}_PLACEHOLDER>"))
            changed = True
        else:
            new_pairs.append((key, val))
    if not changed:
        return value
    return urlunsplit((parts.scheme, netloc, parts.path, urlencode(new_pairs), parts.fragment))


def _sanitize_mcp_headers(headers: dict) -> dict:
    for key, val in list(headers.items()):
        if _SECRET_KEY_RE.search(key) or (
            isinstance(val, str) and val.lower().startswith(("bearer ", "basic "))
        ):
            headers[key] = f"<{key}_PLACEHOLDER>"
    return headers


def _sanitize_mcp_servers(servers: dict, source: str | None = None) -> dict:
    """占位化 MCP 服务端的 env/args/url/headers 凭据（Issue #54、#80）。"""
    clean = {}
    for key, val in servers.items():
        if not isinstance(val, dict):
            clean[key] = val
            continue
        v_copy = json.loads(json.dumps(val))
        env = v_copy.get("env")
        if isinstance(env, dict):
            for ek, ev in list(env.items()):
                if isinstance(ev, str) and not (ev.startswith("${") or ev.startswith("<")):
                    env[ek] = f"<{ek}_PLACEHOLDER>"
        args = v_copy.get("args")
        if isinstance(args, list):
            v_copy["args"] = _sanitize_mcp_args(args)
        url = v_copy.get("url")
        if isinstance(url, str):
            try:
                v_copy["url"] = _sanitize_mcp_url(url)
            except ctx.SyncError as error:
                location = f"{source} 的 MCP server {key!r}" if source else f"MCP server {key!r}"
                raise ctx.SyncError(f"{location} URL 端口非法，无法解析；请修正端口") from error
        headers = v_copy.get("headers")
        if isinstance(headers, dict):
            v_copy["headers"] = _sanitize_mcp_headers(headers)
        clean[key] = v_copy
    return clean


def merge_mcp(src: Path, changed: set[Path]) -> list[str]:
    """按 server 键合并 MCP：模板新增键加入，已有键保留消费值（禁冲密钥）。"""
    merged: list[str] = []
    for rel in ctx.MCP_CANDIDATES:
        sp, dp = src / rel, ctx.CONSUMER / rel
        if not sp.exists():
            continue
        try:
            sdata = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"WARNING: 模板侧 {rel} 无法解析，跳过 MCP 合并（{e}）", file=sys.stderr)
            continue
        if not isinstance(sdata, dict) or not isinstance(sdata.get("mcpServers"), dict):
            print(f"WARNING: 模板侧 {rel} 结构不符合预期，跳过 MCP 合并", file=sys.stderr)
            continue
        if not dp.exists():
            dp.parent.mkdir(parents=True, exist_ok=True)
            s_servers = sdata.get("mcpServers", {})
            new_data = {**sdata, "mcpServers": _sanitize_mcp_servers(s_servers, rel)}
            dp.write_text(json.dumps(new_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            changed.add(dp)
            merged.append(f"{ctx._rel(dp)}（新建，已占位化 env/args/url/headers 中的凭据，需审阅全部字段）")
            continue
        try:
            draw = dp.read_text(encoding="utf-8")
            ddata = json.loads(draw)
        except (OSError, json.JSONDecodeError) as e:
            print(f"WARNING: 消费侧 {rel} 无法解析，跳过 MCP 合并（{e}）", file=sys.stderr)
            continue
        if not isinstance(ddata, dict):
            print(f"WARNING: 消费侧 {rel} 顶层非 JSON 对象，跳过 MCP 合并", file=sys.stderr)
            continue
        dservers = ddata.get("mcpServers")
        if dservers is None:
            dservers = ddata.setdefault("mcpServers", {})
        elif not isinstance(dservers, dict):
            print(f"WARNING: 消费侧 {rel} 的 mcpServers 非对象，跳过 MCP 合并", file=sys.stderr)
            continue
        touched = False
        sanitized_src = _sanitize_mcp_servers(sdata.get("mcpServers", {}), rel)
        for key, val in sanitized_src.items():
            if key not in dservers:
                dservers[key] = val
                touched = True
        if touched:
            dp.write_bytes((json.dumps(ddata, ensure_ascii=False, indent=_detect_json_indent(draw)) + "\n").encode("utf-8"))
            changed.add(dp)
            merged.append(ctx._rel(dp))
    return merged


def shared_class(src_path: Path, dst_path: Path) -> str:
    if src_path.exists() and not dst_path.exists():
        return "template_only"
    if dst_path.exists() and not src_path.exists():
        return "consumer_only"
    if src_path.exists() and dst_path.exists():
        if _file_differs(src_path, dst_path):
            return "both_differ"
        return "both_identical"
    return "none"


def shared_status(src: Path) -> list[dict]:
    """裁定单元分类（AGENTS.md 目的地为软链时解引用实体再比对）。"""
    out: list[dict] = []
    for unit in ctx.SHARED_FILES:
        s = src / unit
        if unit == "AGENTS.md":
            d = ctx.CONSUMER / "AGENTS.md"
            if d.is_symlink():
                d = d.resolve()
        else:
            d = ctx.CONSUMER / unit
        out.append({"unit": unit, "cls": shared_class(s, d)})
    return out


def _apply_shared_unit(unit: str, decision: str | None, src: Path, changed: set[Path]) -> None:
    # 标题协议下只自动更新开发原则；其余部分分别保护或交 Agent。
    if unit == "AGENTS.md" and _apply_agents_sectioned(src, changed):
        print("AGENTS.md 已按标题协议处理：开发原则强制更新，目录规则交 agent 合并，项目介绍保留")
        return
    if not decision:
        print(f"裁定单元 {unit} 未提供决策，跳过（待 agent 处理）")
        return
    if decision in ("merge", "merge_into_consumer"):
        print(f"裁定单元 {unit} 为 merge：由 agent 编辑消费文件完成语义合并，脚本不整文件覆盖")
        return
    if decision not in SHARED_DISPOSITIONS:
        print(f"裁定单元 {unit} 非法决策 {decision!r}，跳过")
        return
    if decision in ("keep", "keep_consumer"):
        return
    s = src / unit
    d = ctx.CONSUMER / unit
    if d.is_symlink():
        d = d.resolve()
    if not s.exists():
        return
    if not d.exists() or _file_differs(s, d):
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
        changed.add(d)
