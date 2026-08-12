#!/usr/bin/env python3
"""repo_sync.py — repo-template-sync 机械化执行器。

把 repo-template-sync skill 中可机械化对齐的部分下沉到脚本执行：硬同步清单
树对树覆盖、skill 整目录覆盖、软链建修、.gitignore / MCP 机械合并、
sync_state.json 字段级原子更新、差异评估与修改路径清单输出。

非机械化裁定（AGENTS.md / conventions.md 等共享文稿的语义合并）由脚本给出
分类与差异摘要，agent 审查后通过 `--decision UNIT:DISPOSITION` 决策，apply
按决策写盘；未提供决策的裁定单元跳过并报告（ask_user 语义）。

用法：
  python3 scripts/repo_template/repo_sync.py init --source <path|url>
  python3 scripts/repo_template/repo_sync.py status
  python3 scripts/repo_template/repo_sync.py plan
  python3 scripts/repo_template/repo_sync.py apply [--decision AGENTS.md:update]... [--skip-tests]
  python3 scripts/repo_template/repo_sync.py prompt list
  python3 scripts/repo_template/repo_sync.py prompt add --text "..." [--tags a,b]
  python3 scripts/repo_template/repo_sync.py prompt revoke --id N
  python3 scripts/repo_template/repo_sync.py link-skills

本脚本只处理模板工具链与模板侧资产，不碰业务代码与项目状态。禁止自动
commit——写盘与 state 更新完成后由 agent 走审批门禁。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

CONSUMER = Path(__file__).resolve().parent.parent.parent
STATE_PATH = CONSUMER / ".agents/skills/repo-template-sync/sync_state.json"
SKILLS_AGENTS = CONSUMER / ".agents/skills"
SKILLS_CLAUDE = CONSUMER / ".claude/skills"

# 硬同步清单：模板为唯一真相，消费侧差异视为漂移。目录用树对树同步（含删除），单文件用拷贝/删除。
HARD_SYNC_DIRS = (
    "scripts/repo_template",
    "tests/repo_template",
    "docs/tasks/task_template",
    "docs/reviews/prompts",
)
HARD_SYNC_FILES = (
    "docs/spikes/report_template.md",
    "docs/blueprint/architecture_repo_template.md",
    ".claude/hooks/merge_guard.py",
)
NOISE_NAMES = {"__pycache__", ".pytest_cache", ".DS_Store"}

# 裁定范围：可定制共享资产。.gitignore / MCP 机械合并；AGENTS.md / conventions.md 语义合并。
SHARED_FILES = ("AGENTS.md", "docs/blueprint/conventions.md", ".gitignore")
MCP_CANDIDATES = (".mcp.json", ".cursor/mcp.json", ".vscode/mcp.json")


class SyncError(Exception):
    pass


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _rel(path: Path) -> str:
    return path.relative_to(CONSUMER).as_posix()


def _is_noise(name: str) -> bool:
    return name in NOISE_NAMES or name.endswith(".pyc")


def _file_differs(a: Path, b: Path) -> bool:
    if a.is_symlink() or b.is_symlink():
        return a.resolve() != b.resolve()
    if a.stat().st_size != b.stat().st_size:
        return True
    return a.read_bytes() != b.read_bytes()


# ---------------------------------------------------------------------------
# sync_state.json 读写（字段级原子更新纪律）
# ---------------------------------------------------------------------------

def read_state() -> dict | None:
    if not STATE_PATH.exists():
        return None
    with open(STATE_PATH, encoding="utf-8") as f:
        return json.load(f)


def write_state(data: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_name(STATE_PATH.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(STATE_PATH)


def active_prompts(state: dict | None) -> list[dict]:
    """返回未 revoked 的 user_prompts 条目。"""
    if not state:
        return []
    return [p for p in state.get("user_prompts", []) if not p.get("revoked")]


def prompt_substrings(state: dict | None) -> list[str]:
    """从活跃 prompt 的 tags 提取拦截子串（供 .gitignore 规则匹配）。"""
    out: list[str] = []
    for p in active_prompts(state):
        for tag in p.get("tags", []):
            t = str(tag).strip()
            if t and t not in out:
                out.append(t)
    return out


def resolve_src(state: dict | None) -> Path:
    if not state or not state.get("template_source", {}).get("value"):
        raise SyncError("未初始化：缺少 template_source。先运行 init --source <path|url>")
    ts = state["template_source"]
    kind = ts.get("kind", "path")
    value = ts["value"]
    if kind == "url":
        cache = CONSUMER / ".scratch/repo_template_sync_src"
        if not (cache / ".git").exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            r = _git(cache.parent, "clone", value, cache.name)
            if r.returncode != 0:
                raise SyncError(f"clone 模板源失败：{r.stderr.strip()}")
        else:
            r = _git(cache, "pull", "--ff-only")
            if r.returncode != 0:
                raise SyncError(f"更新模板源失败：{r.stderr.strip()}")
        src = cache.resolve()
    else:
        src = Path(value).expanduser().resolve()
    if not (src / "scripts/repo_template/task.py").exists():
        raise SyncError(f"模板源无效：{src} 不含 scripts/repo_template/task.py")
    return src


def assert_not_self(src: Path) -> None:
    if src == CONSUMER:
        raise SyncError("当前仓库与 template_source 同一路径，禁止在模板仓当推送源同步")


# ---------------------------------------------------------------------------
# 硬同步（树对树 / 单文件）
# ---------------------------------------------------------------------------

def sync_dir(src_dir: Path, dst_dir: Path, changed: set[Path]) -> None:
    """树对树同步：src 全量复制（含覆盖），dst 中 src 没有的非噪声条目删除。"""
    if not dst_dir.exists():
        dst_dir.mkdir(parents=True)
    for entry in src_dir.iterdir():
        if _is_noise(entry.name):
            continue
        target = dst_dir / entry.name
        if entry.is_dir():
            sync_dir(entry, target, changed)
        else:
            if not target.exists() or _file_differs(entry, target):
                shutil.copy2(entry, target)
                changed.add(target)
    for entry in dst_dir.iterdir():
        if _is_noise(entry.name):
            continue
        if not (src_dir / entry.name).exists():
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            changed.add(entry)


def sync_file(src: Path, dst: Path, changed: set[Path]) -> None:
    """单文件：src 有 → 拷贝（相同则不动）；src 无 → 删 dst。"""
    if not src.exists():
        if dst.exists():
            dst.unlink()
            changed.add(dst)
        return
    if not dst.exists() or _file_differs(src, dst):
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        changed.add(dst)


# ---------------------------------------------------------------------------
# skill 整目录覆盖 + 软链
# ---------------------------------------------------------------------------

def sync_skill(src_skill: Path, dst_skill: Path, changed: set[Path]) -> None:
    """整 skill 目录覆盖（含 front matter），保护 sync_state.json，不做 --delete。"""
    if not dst_skill.exists():
        dst_skill.mkdir(parents=True)
    for entry in src_skill.iterdir():
        if entry.name == "sync_state.json":
            continue
        target = dst_skill / entry.name
        if entry.is_dir():
            sync_dir(entry, target, changed)
        else:
            if not target.exists() or _file_differs(entry, target):
                shutil.copy2(entry, target)
                changed.add(target)


def repair_symlinks(changed: set[Path]) -> list[str]:
    """建/修 .claude/skills/<name> -> ../../.agents/skills/<name>。非本机制链接报告不碰。"""
    reports: list[str] = []
    if not SKILLS_AGENTS.exists():
        return reports
    SKILLS_CLAUDE.mkdir(parents=True, exist_ok=True)
    for entry in SKILLS_AGENTS.iterdir():
        if not entry.is_dir():
            continue
        link = SKILLS_CLAUDE / entry.name
        expected = entry.resolve()
        target_rel = os.path.join("..", "..", ".agents", "skills", entry.name)
        if link.is_symlink():
            if link.resolve() == expected:
                continue
            link.unlink()
            os.symlink(target_rel, link)
            changed.add(link)
        elif link.exists():
            reports.append(f".claude/skills/{entry.name} 非本机制管理，跳过")
        else:
            os.symlink(target_rel, link)
            changed.add(link)
    return reports


# ---------------------------------------------------------------------------
# 差异扫描
# ---------------------------------------------------------------------------

def _dir_has_diff(src_dir: Path, dst_dir: Path) -> bool:
    """目录树是否存在实质差异（非噪声）。"""
    if not src_dir.exists() and not dst_dir.exists():
        return False
    if src_dir.exists() != dst_dir.exists():
        return True
    src_files = {p.relative_to(src_dir) for p in src_dir.rglob("*")
                 if p.is_file() and not any(_is_noise(part) for part in p.relative_to(src_dir).parts)}
    dst_files = {p.relative_to(dst_dir) for p in dst_dir.rglob("*")
                 if p.is_file() and not any(_is_noise(part) for part in p.relative_to(dst_dir).parts)}
    if src_files != dst_files:
        return True
    for rel in src_files:
        if _file_differs(src_dir / rel, dst_dir / rel):
            return True
    return False


def hard_sync_status(src: Path) -> list[dict]:
    """硬同步清单状态：write / delete / same。"""
    out: list[dict] = []
    for rel in HARD_SYNC_DIRS:
        s, d = src / rel, CONSUMER / rel
        if not s.exists() and not d.exists():
            continue
        if not s.exists():
            out.append({"rel": rel + "/", "state": "delete"})
        elif not d.exists() or _dir_has_diff(s, d):
            out.append({"rel": rel + "/", "state": "write"})
        else:
            out.append({"rel": rel + "/", "state": "same"})
    for rel in HARD_SYNC_FILES:
        s, d = src / rel, CONSUMER / rel
        if not s.exists() and not d.exists():
            continue
        if not s.exists():
            out.append({"rel": rel, "state": "delete"})
        elif not d.exists() or _file_differs(s, d):
            out.append({"rel": rel, "state": "write"})
        else:
            out.append({"rel": rel, "state": "same"})
    return out


def skill_status(src: Path) -> list[dict]:
    """skill 状态：模板侧 override；仅消费侧 keep。"""
    out: list[dict] = []
    if (src / ".agents/skills").exists():
        for s in (src / ".agents/skills").iterdir():
            if s.is_dir():
                d = SKILLS_AGENTS / s.name
                diff = not d.exists() or _dir_has_diff(s, d)
                out.append({"name": s.name, "action": "override" if diff else "same"})
    if SKILLS_AGENTS.exists():
        for d in SKILLS_AGENTS.iterdir():
            if d.is_dir() and not (src / ".agents/skills" / d.name).exists():
                out.append({"name": d.name, "action": "keep"})
    return out


def symlink_status() -> list[dict]:
    out: list[dict] = []
    if not SKILLS_AGENTS.exists():
        return out
    for entry in SKILLS_AGENTS.iterdir():
        if not entry.is_dir():
            continue
        link = SKILLS_CLAUDE / entry.name
        if link.is_symlink() and link.resolve() == entry.resolve():
            out.append({"name": entry.name, "state": "ok"})
        elif link.exists() and not link.is_symlink():
            out.append({"name": entry.name, "state": "unmanaged"})
        else:
            out.append({"name": entry.name, "state": "repair"})
    return out


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
    """裁定单元分类（AGENTS.md 处理 CLAUDE.md 软链实体）。"""
    out: list[dict] = []
    for unit in SHARED_FILES:
        s = src / unit
        if unit == "AGENTS.md":
            d = CONSUMER / "AGENTS.md"
            if d.is_symlink():
                d = d.resolve()
        else:
            d = CONSUMER / unit
        out.append({"unit": unit, "cls": shared_class(s, d)})
    return out


def src_dirty(src: Path) -> bool:
    paths = list(HARD_SYNC_DIRS) + list(HARD_SYNC_FILES) + list(SHARED_FILES)
    r = _git(src, "status", "--porcelain", "--", *paths)
    return bool(r.stdout.strip())


# ---------------------------------------------------------------------------
# 机械合并（.gitignore / MCP）
# ---------------------------------------------------------------------------

def _blocked(rule: str, substrings: list[str]) -> bool:
    return any(sub in rule for sub in substrings)


def merge_gitignore(src: Path, blocked: list[str], changed: set[Path]) -> dict:
    """追加模板独有规则（去重、遵从 prompt 拦截）；删除消费侧 prompt 禁止的 ignore 行。"""
    dst = CONSUMER / ".gitignore"
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
        if _blocked(stripped, blocked):
            continue
        added.append(stripped)
    if blocked:
        new_dst: list[str] = []
        for ln in dst_lines:
            stripped = ln.strip()
            if stripped and not stripped.startswith("#") and _blocked(stripped, blocked):
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
        dst.write_text(text, encoding="utf-8", newline="\n")
        changed.add(dst)
    return {"added": added, "removed": removed, "dst": _rel(dst)}


def merge_mcp(src: Path, changed: set[Path]) -> list[str]:
    """按 server 键合并 MCP：模板新增键加入，已有键保留消费值（禁冲密钥）。"""
    merged: list[str] = []
    for rel in MCP_CANDIDATES:
        sp, dp = src / rel, CONSUMER / rel
        if not sp.exists():
            continue
        try:
            sdata = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not dp.exists():
            dp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sp, dp)
            changed.add(dp)
            merged.append(_rel(dp))
            continue
        try:
            ddata = json.loads(dp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        dservers = ddata.setdefault("mcpServers", {})
        touched = False
        for key, val in sdata.get("mcpServers", {}).items():
            if key not in dservers:
                dservers[key] = val
                touched = True
        if touched:
            dp.write_text(json.dumps(ddata, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8", newline="\n")
            changed.add(dp)
            merged.append(_rel(dp))
    return merged


# ---------------------------------------------------------------------------
# apply 组装
# ---------------------------------------------------------------------------

def _apply_shared_unit(unit: str, decision: str | None, src: Path, changed: set[Path]) -> None:
    if not decision:
        print(f"裁定单元 {unit} 未提供决策，跳过（待 agent 处理）")
        return
    if decision in ("merge", "merge_into_consumer"):
        print(f"裁定单元 {unit} 为 merge：由 agent 编辑消费文件完成语义合并，脚本不整文件覆盖")
        return
    if decision not in ("update", "update_from_template", "keep", "keep_consumer"):
        print(f"裁定单元 {unit} 非法决策 {decision!r}，跳过")
        return
    if decision in ("keep", "keep_consumer"):
        return
    s = src / unit
    d = CONSUMER / unit
    if d.is_symlink():
        d = d.resolve()
    if not s.exists():
        return
    if not d.exists() or _file_differs(s, d):
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
        changed.add(d)


def _run_tests(consumer: Path) -> bool:
    r = subprocess.run(
        ["pytest", "tests/repo_template/", "-q"], cwd=str(consumer),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        print(r.stdout[-2000:] if r.stdout else "", file=sys.stderr)
        print(r.stderr[-2000:] if r.stderr else "", file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    if not args.source:
        raise SyncError("init 需要 --source <path|url>")
    if not STATE_PATH.exists():
        write_state({
            "template_source": None,
            "last_synced_commit": None,
            "last_synced_at": None,
            "user_prompts": [],
        })
    data = read_state()
    kind = "path" if Path(args.source).expanduser().exists() else "url"
    data["template_source"] = {"kind": kind, "value": str(Path(args.source).expanduser().resolve() if kind == "path" else args.source)}
    write_state(data)
    print(f"template_source 已写：kind={kind}, value={data['template_source']['value']}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state = read_state()
    if not state:
        print("未初始化：无 sync_state.json。先运行 init --source <path|url>")
        return 0
    src = resolve_src(state)
    head = _git(src, "rev-parse", "--short", "HEAD").stdout.strip() or "?"
    dirty = src_dirty(src)
    prompts = active_prompts(state)
    print(f"template_source: {state['template_source']['kind']} {state['template_source']['value']}")
    print(f"SRC HEAD: {head}  dirty={dirty}")
    print(f"last_synced_commit: {state.get('last_synced_commit')}")
    print(f"last_synced_at: {state.get('last_synced_at')}")
    print(f"生效 user_prompts: {len(prompts)} 条（共 {len(state.get('user_prompts', []))} 条）")
    for i, p in enumerate(prompts):
        print(f"  #{i} {p.get('at')} {p.get('text')}")
    print("\n硬同步差异:")
    for item in hard_sync_status(src):
        mark = {"write": "需写入", "delete": "需删除", "same": "一致"}[item["state"]]
        print(f"  {item['state']:6s} {item['rel']}  ({mark})")
    print("\nskill:")
    for item in skill_status(src):
        mark = {"override": "强制覆盖", "same": "一致", "keep": "保留"}[item["action"]]
        print(f"  {item['action']:8s} {item['name']}  ({mark})")
    print("\n裁定单元:")
    for item in shared_status(src):
        print(f"  {item['cls']:14s} {item['unit']}")
    return 0


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["------"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def cmd_plan(args: argparse.Namespace) -> int:
    state = read_state()
    if not state:
        print("未初始化：无 sync_state.json。先运行 init --source <path|url>")
        return 0
    src = resolve_src(state)
    assert_not_self(src)
    head = _git(src, "rev-parse", "--short", "HEAD").stdout.strip() or "?"
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
            rows.append([f".agents/skills/{item['name']}", "both_differ", "强制覆盖（含 front matter）"])
        elif item["action"] == "keep":
            rows.append([f".agents/skills/{item['name']}", "consumer_only", "保留（禁止删）"])
    print(_md_table(["单元", "分类", "处置"], rows))

    print("\n### 裁定同步 — 逐项（有 diff 必出现）")
    rows = []
    blocked = prompt_substrings(state)
    for item in shared_status(src):
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

    print("\n### 软链")
    rows = []
    for item in symlink_status():
        mark = {"ok": "正确", "repair": "需建/修", "unmanaged": "非本机制，不碰"}[item["state"]]
        rows.append([f".claude/skills/{item['name']}", item["state"], mark])
    print(_md_table(["路径", "状态", "说明"], rows))

    print("\n### state 推进预期")
    if dirty:
        print(f"SRC dirty，apply 可写盘但**不**推进 last_synced_commit")
    else:
        print(f"apply 通过后推进 last_synced_commit -> {head}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    state = read_state()
    if not state:
        raise SyncError("未初始化：无 sync_state.json。先运行 init --source <path|url>")
    src = resolve_src(state)
    assert_not_self(src)
    decisions = getattr(args, "decisions", None) or {}
    changed: set[Path] = set()
    blocked = prompt_substrings(state)

    for rel in HARD_SYNC_DIRS:
        s, d = src / rel, CONSUMER / rel
        if s.exists():
            sync_dir(s, d, changed)
        elif d.exists():
            shutil.rmtree(d)
            changed.add(d)
    for rel in HARD_SYNC_FILES:
        sync_file(src / rel, CONSUMER / rel, changed)

    if (src / ".agents/skills").exists():
        for s in (src / ".agents/skills").iterdir():
            if s.is_dir():
                sync_skill(s, SKILLS_AGENTS / s.name, changed)

    reports = repair_symlinks(changed)
    for r in reports:
        print(f"提示: {r}")

    if (src / ".gitignore").exists():
        info = merge_gitignore(src, blocked, changed)
        if info["added"]:
            print(f".gitignore 追加 {len(info['added'])} 条（模板独有）")
        if info["removed"]:
            print(f".gitignore 删除 {len(info['removed'])} 条（prompt 禁止 ignore）")
    merge_mcp(src, changed)

    for unit in SHARED_FILES:
        if unit == ".gitignore":
            continue
        decision = decisions.get(unit)
        _apply_shared_unit(unit, decision, src, changed)

    if not args.skip_tests:
        if not _run_tests(CONSUMER):
            print("pytest tests/repo_template/ 失败，不推进 state", file=sys.stderr)
            return 1
    else:
        print("跳过测试验证（--skip-tests）")

    dirty = src_dirty(src)
    if not dirty:
        head = _git(src, "rev-parse", "HEAD").stdout.strip()
        state["last_synced_commit"] = head
    state["last_synced_at"] = _now_iso()
    write_state(state)

    print("\n=== 改动路径清单（commit 门禁点名 add）===")
    for path in sorted(changed, key=str):
        print(_rel(path))
    if not changed:
        print("（无改动）")
    print(f"\nstate 推进：last_synced_commit={'已更新' if not dirty else 'SRC dirty，未推进'}, "
          f"last_synced_at={state['last_synced_at']}")
    return 0


def cmd_prompt(args: argparse.Namespace) -> int:
    state = read_state()
    if not state:
        raise SyncError("未初始化：无 sync_state.json。先运行 init --source <path|url>")
    if args.action == "list":
        for i, p in enumerate(state.get("user_prompts", [])):
            rev = " [revoked]" if p.get("revoked") else ""
            print(f"#{i}{rev} {p.get('at')} tags={p.get('tags', [])} {p.get('text')}")
        return 0
    prompts = state.setdefault("user_prompts", [])
    if args.action == "add":
        if not args.text:
            raise SyncError("prompt add 需要 --text")
        tags = [t.strip().lower() for t in (args.tags or "").split(",") if t.strip()]
        for p in prompts:
            if p.get("revoked"):
                continue
            old_tags = {str(t).lower() for t in p.get("tags", [])}
            if old_tags & set(tags):
                p["revoked"] = True
        prompts.append({"at": _now_iso(), "text": args.text, "tags": tags, "revoked": False})
        write_state(state)
        print(f"已登记 prompt #{len(prompts) - 1}: {args.text}")
        return 0
    if args.action == "revoke":
        idx = args.id
        if idx is None or not (0 <= idx < len(prompts)):
            raise SyncError(f"revoke 需要合法 --id（0..{len(prompts) - 1}）")
        prompts[idx]["revoked"] = True
        write_state(state)
        print(f"已作废 prompt #{idx}")
        return 0
    return 0


def cmd_link_skills(args: argparse.Namespace) -> int:
    changed: set[Path] = set()
    reports = repair_symlinks(changed)
    for r in reports:
        print(f"提示: {r}")
    if not changed:
        print("软链均已正确")
    for path in sorted(changed, key=str):
        print(f"已修: {_rel(path)}")
    return 0


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _parse_decisions(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if ":" not in item:
            raise SyncError(f"--decision 格式应为 UNIT:DISPOSITION，收到 {item!r}")
        unit, disp = item.split(":", 1)
        out[unit] = disp
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="repo-template-sync 机械化执行器")
    sub = parser.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="初始化 sync_state.json 的 template_source")
    init.add_argument("--source", help="模板源：本机路径或 git remote URL")
    init.set_defaults(func=cmd_init)

    sub.add_parser("status", help="读取状态与差异摘要").set_defaults(func=cmd_status)
    sub.add_parser("plan", help="计算差异预览（零写盘）").set_defaults(func=cmd_plan)

    apply = sub.add_parser("apply", help="执行对齐写盘")
    apply.add_argument("--decision", action="append", default=[], metavar="UNIT:DISP",
                       help="裁定单元决策（可重复），如 AGENTS.md:update / conventions.md:keep")
    apply.add_argument("--skip-tests", action="store_true", help="跳过 pytest 验证")
    apply.set_defaults(func=cmd_apply)

    prompt = sub.add_parser("prompt", help="管理 user_prompts")
    prompt.add_argument("action", choices=["list", "add", "revoke"])
    prompt.add_argument("--text", help="prompt 正文（add 用）")
    prompt.add_argument("--tags", help="逗号分隔 tags（add 用）")
    prompt.add_argument("--id", type=int, help="条目下标（revoke 用）")
    prompt.set_defaults(func=cmd_prompt)

    sub.add_parser("link-skills", help="校验并修复 .claude/skills 软链").set_defaults(func=cmd_link_skills)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "cmd", None) == "apply":
        args.decisions = _parse_decisions(args.decision)
    try:
        sys.exit(args.func(args))
    except SyncError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
