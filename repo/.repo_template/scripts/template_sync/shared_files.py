"""共享文件机械合并：.gitignore / .prettierignore / MCP，以及裁定单元决策。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
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


def _gitignore_blocks(lines: list[str], marker: str) -> tuple[list[str], list[str], list[str]]:
    begin = f"# repo-template:{marker}:begin" if marker == "prettier" else "# repo-template:begin"
    end = f"# repo-template:{marker}:end" if marker == "prettier" else "# repo-template:end"
    try:
        start = lines.index(begin)
        stop = lines.index(end)
    except ValueError:
        return lines, [], []
    if start > stop:
        raise ctx.SyncError(".gitignore 托管块标记顺序错误")
    return lines[:start], lines[start + 1:stop], lines[stop + 1:]


def merge_gitignore(src: Path, blocked: list[str], changed: set[Path], sync: ctx.SyncContext) -> dict:
    """替换 .gitignore 托管块；块外规则永不被同步删除。"""
    dst = sync.consumer / ".gitignore"
    src_lines = (src / ".gitignore").read_text(encoding="utf-8").splitlines()
    dst_lines = dst.read_text(encoding="utf-8").splitlines() if dst.exists() else []
    before, source_block, source_after = _gitignore_blocks(src_lines, "gitignore")
    if not source_block:
        raise ctx.SyncError("模板 .gitignore 缺 # repo-template:begin/end 托管块")
    kept_source = [
        line for line in source_block
        if not (
            line.strip()
            and not line.strip().startswith(("#", "!"))
            and _blocked(line.strip(), blocked)
        )
    ]
    removed = [
        line for line in source_block
        if line.strip() and not line.strip().startswith(("#", "!"))
        and _blocked(line.strip(), blocked)
    ]
    before_dst, dst_block, after_dst = _gitignore_blocks(dst_lines, "gitignore")
    if not dst_block:
        before_dst, after_dst = dst_lines, []
    duplicates = [
        line for line in before_dst + after_dst
        if line.strip() and not line.strip().startswith("#") and line.strip() in {x.strip() for x in kept_source}
    ]
    result = [*before_dst, "# repo-template:begin", *kept_source, "# repo-template:end", *after_dst]
    text = "\n".join(result)
    if not text.endswith("\n"):
        text += "\n"
    if text != (dst.read_text(encoding="utf-8") if dst.exists() else ""):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8")
        changed.add(dst)
    return {
        "added": kept_source,
        "removed": removed,
        "outside_duplicates": duplicates,
        "dst": sync.rel(dst),
    }


_PRETTIERIGNORE_OLD_HEADER = "# 同步自模板（repo-template-sync）：模板自有路径不受消费仓 format 门禁约束"
_PRETTIERIGNORE_HEADER = (
    "# 同步自模板（repo-template-sync）：模板自有/本地生成路径不受消费仓 format 门禁约束；",
    "# /.repo_template/ 根锚定整目录归模板所有；目录外生成点（handoff.json / .opencode/package*.json）按需生成，不存在时仅为预留",
)

_NESTED_PROBES = ("src/.repo_template/probe.json", "tests/fixtures/.repo_template/probe.json")

# 0b112d0 写入的未锚定规则：根锚定升级时必须回收，否则嵌套同名目录继续被误豁免。
_RETIRED_PRETTIERIGNORE_RULES = (".repo_template/",)

# 模板会写入、但位于工具链目录之外、仍受消费仓自身 format 门禁管理的配置。
DIRECTORY_OUTSIDE_TOOLKIT = (*ctx.MCP_CANDIDATES, ".claude/settings.json")


def _prettierignore_rules(path: Path) -> set[str]:
    """读取 .prettierignore 现有规则（去空行与注释、去首尾空白）。"""
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _git_ignored(rules_text: str, rels: list[str]) -> set[str]:
    """用 git 的真实 gitignore 引擎判定 rels 中哪些路径被规则忽略。

    .prettierignore 采用 gitignore 语法，规则顺序、否定、目录与根锚定必须由真实
    引擎解释；在临时空仓里以 .gitignore 承载规则，排除全局 excludesFile 干扰。
    """
    if not rels:
        return set()
    ignored: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="prettier_ignore_") as directory:
        engine = Path(directory)
        init = subprocess.run(
            ["git", "init", "-q"], cwd=engine,
            capture_output=True, text=True, timeout=30,
        )
        if init.returncode:
            raise ctx.SyncError("无法初始化 ignore 判定引擎：" + init.stderr.strip())
        (engine / ".gitignore").write_text(rules_text, encoding="utf-8")
        for rel in rels:
            result = subprocess.run(
                ["git", "-C", str(engine), "-c", "core.excludesFile=/dev/null",
                 "check-ignore", "--no-index", "-q", "--", rel],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                ignored.add(rel)
            elif result.returncode != 1:
                raise ctx.SyncError(f"ignore 判定失败（{rel}）：" + result.stderr.strip())
    return ignored


def assert_format_exempt(consumer: Path) -> None:
    """模板自有机器资产必须被 .prettierignore 有效豁免；未豁免即在提交前整体回滚。

    用真实 gitignore 引擎判定（消费仓实际规则原文与嵌套目录范围都参与），保留规则
    顺序、否定、目录与根锚定语义；不执行消费仓 formatter，也不复刻 ignore 语法。
    """
    consumer = consumer.resolve()
    ignore = consumer / ".prettierignore"
    rules_text = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    rules = _prettierignore_rules(ignore)
    missing = [rule for rule in ctx.PRETTIERIGNORE_TEMPLATE_RULES if rule not in rules]
    if missing:
        raise ctx.SyncError("format 豁免规则缺失：" + "、".join(missing))
    nested = _git_ignored(rules_text, list(_NESTED_PROBES))
    if nested:
        raise ctx.SyncError(
            "根锚定失效或消费规则过宽，嵌套同名目录被误豁免：" + "、".join(sorted(nested))
        )
    owned = consumer / ".repo_template"
    candidates = sorted(owned.rglob("*.json")) if owned.is_dir() else []
    toolkit = [path.relative_to(consumer).as_posix() for path in candidates if path.is_file()]
    ignored = _git_ignored(rules_text, toolkit)
    uncovered = [rel for rel in toolkit if rel not in ignored]
    if uncovered:
        raise ctx.SyncError("模板自有 JSON 未被 format 豁免覆盖：" + "、".join(uncovered))


def assert_commit_stability(consumer: Path, changed: set[Path]) -> None:
    """本轮写入的目录外配置必须被消费仓 .prettierignore 豁免，否则提交前阻断。

    模板沿用消费仓原缩进，但不宣称其字节与消费仓 formatter 输出逐字节一致；这些
    路径若仍受消费仓 format 门禁管理，提交 hook 可能改写并使 accept 指纹拒绝。
    受支持流程：消费仓把这些路径纳入自己的 .prettierignore，或把提交 hook 改为只读检查。
    """
    consumer = consumer.resolve()
    touched = []
    for path in sorted(changed):
        try:
            rel = path.relative_to(consumer).as_posix()
        except ValueError:
            continue
        if rel in DIRECTORY_OUTSIDE_TOOLKIT:
            touched.append(rel)
    if not touched:
        return
    ignore = consumer / ".prettierignore"
    rules_text = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    ignored = _git_ignored(rules_text, touched)
    conflicts = [rel for rel in touched if rel not in ignored]
    if conflicts:
        raise ctx.SyncError(
            "本轮同步写入了目录外配置，且未被消费仓 .prettierignore 豁免："
            + "、".join(conflicts)
            + "；消费仓提交 hook 的 formatter 可能改写这些文件，使提交内容偏离审批指纹。"
            + "请把这些路径纳入消费仓自己的 .prettierignore，或把提交 hook 改为只读检查后重试。"
        )


def merge_prettierignore(changed: set[Path], sync: ctx.SyncContext) -> dict:
    """机械合并 .prettierignore：回收已下线模板规则，必需规则去重追加，消费独有规则保留。"""
    dst = sync.consumer / ".prettierignore"
    dst_lines = dst.read_text(encoding="utf-8").splitlines() if dst.exists() else []
    removed = [
        line.strip() for line in dst_lines
        if line.strip() in _RETIRED_PRETTIERIGNORE_RULES
    ]
    dst_lines = [
        line for line in dst_lines
        if line.strip() not in _RETIRED_PRETTIERIGNORE_RULES
    ]
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
    if added or header_changed or removed:
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
    return {"added": added, "removed": removed, "dst": sync.rel(dst)}


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


def merge_mcp(src: Path, changed: set[Path], sync: ctx.SyncContext) -> list[str]:
    """按 server 键合并 MCP：模板新增键加入，已有键保留消费值（禁冲密钥）。"""
    merged: list[str] = []
    for rel in ctx.MCP_CANDIDATES:
        sp, dp = src / rel, sync.consumer / rel
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
            merged.append(f"{sync.rel(dp)}（新建，已占位化 env/args/url/headers 中的凭据，需审阅全部字段）")
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
            merged.append(sync.rel(dp))
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


def shared_status(src: Path, sync: ctx.SyncContext) -> list[dict]:
    """裁定单元分类（AGENTS.md 目的地为软链时解引用实体再比对）。"""
    out: list[dict] = []
    for unit in ctx.SHARED_FILES:
        s = src / unit
        if unit == "AGENTS.md":
            d = sync.consumer / "AGENTS.md"
            if d.is_symlink():
                d = d.resolve()
        else:
            d = sync.consumer / unit
        out.append({"unit": unit, "cls": shared_class(s, d)})
    return out


def _apply_shared_unit(unit: str, decision: str | None, src: Path, changed: set[Path], sync: ctx.SyncContext) -> None:
    # 标题协议下只自动更新开发原则；其余部分分别保护或交 Agent。
    if unit == "AGENTS.md" and _apply_agents_sectioned(src, changed, sync):
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
    d = sync.consumer / unit
    if d.is_symlink():
        d = d.resolve()
    if not s.exists():
        return
    if not d.exists() or _file_differs(s, d):
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
        changed.add(d)
