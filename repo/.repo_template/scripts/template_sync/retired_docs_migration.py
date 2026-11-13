"""同步期清理已下线旧模板说明文档（docs/pending/README.md、docs/findings/README.md）。"""

from __future__ import annotations

from pathlib import Path

from . import context as ctx

RETIRED_DOC_PATHS = ("docs/pending/README.md", "docs/findings/README.md")

_OBSOLETE_MARKERS: dict[str, tuple[str, ...]] = {
    "docs/pending/README.md": (
        "docs/pending/todo/",
        "docs/pending/parked/",
        "docs/archive/pending/",
        "pending.py archive",
        "task-bug",
        "task-from-pending",
        "scripts/repo_template/pending.py",
        "三态由所在目录表达",
    ),
    "docs/findings/README.md": (
        "docs/archive/spikes/",
        "scripts/repo_template/findings.py",
        "UNVERIFIED-BLOCKING",
        "UNVERIFIED-SPIKE",
    ),
}


def _norm_line(line: str) -> str:
    s = line.strip()
    if not s:
        return ""
    s = s.replace(".repo_template/scripts/", "scripts/repo_template/")
    if s.startswith("scripts/repo_template/"):
        s = "python3 " + s
    if s.startswith("|") and s.endswith("|"):
        cells = [cell.strip() for cell in s.strip("|").split("|")]
        if cells and all(cell and set(cell) <= {"-", ":"} and "-" in cell for cell in cells):
            return "|---|" * len(cells)
        return "|" + "|".join(" ".join(cell.split()) for cell in cells) + "|"
    return " ".join(s.split())


def _known_lines(raw_lines: tuple[str, ...]) -> frozenset[str]:
    return frozenset(norm for line in raw_lines if (norm := _norm_line(line)))


_KNOWN_TEMPLATE_LINES: dict[str, frozenset[str]] = {
    "docs/pending/README.md": _known_lines((
        "# 待办与不办总账",
        "项目里「已知、还欠着」的事只在本目录登记：未修 bug、review 遗留、技术债、该做未做的需求，以及用户已确认暂搁的事项。",
        "持续口述登记用 skill `pending-record`（澄清后派子代理落盘；bug 走 `task-bug` 分析再记）。从总账捞成 task 用 `task-from-pending`。",
        "一条目一文件，文件名 `pNNN_{slug}.md`；三态由所在目录表达：",
        "|目录|语义|",
        "|---|---|",
        "|`docs/pending/todo/`|未闭环、待启动|",
        "|`docs/pending/parked/`|用户显式确认暂搁（不办）；不等于闭环|",
        "|`docs/archive/pending/`|已闭环|",
        "`pNNN` 全局递增，跨三个目录共享一条序列，历史编号不复用。已验证的技术发现不属于待办，写 `docs/findings/`。",
        "## 命令",
        "```bash",
        "```",
        "python3 scripts/repo_template/pending.py new --slug cli_exit_code            # 建普通条目",
        "python3 scripts/repo_template/pending.py new --slug crash_on_empty --kind bug # 建 bug 条目",
        "python3 scripts/repo_template/pending.py list --state all                     # 列举",
        "python3 scripts/repo_template/pending.py archive p047 --fix-ref t012 --write  # 闭环",
        "python3 scripts/repo_template/pending.py park p047 --reason \"等外部依赖\" --write",
        "python3 scripts/repo_template/pending.py revive p047 --write                  # parked → todo",
        "`new` 在 git 公共目录的排他锁内完成「扫描取号 → 建文件」，并发 worker 不会撞号；禁止手工创建条目文件。迁移一律走命令，命令默认 dry-run，加 `--write` 落盘。",
        "## 字段",
        "两种模板，按条目性质选一种。`- 处理` 未闭环写「未开」，闭环写 `{tid}` 或外部动作说明，暂搁写「不办」。",
        "- 普通（需求 / 遗留 / 技术债）：`- 来源` / `- 内容` / `- 处理`。`- 来源` 写清出处：finding_id、原 tid、用户提出，或技术债自查。",
        "- bug：`- 现象` / `- 影响` / `- 根因` / `- 测试缺口` / `- 线索` / `- 处理`。bug 由 `task-bug` 登记并完成根因与补测分析。",
        "- bug：`- 现象` / `- 影响` / `- 根因` / `- 测试缺口` / `- 线索` / `- 处理`。bug 由 `task-bug` 或 `pending-record`（派子代理跑 task-bug 第 1–6 步：含同类位点扫描与合并）登记并完成根因与补测分析。`- 根因` 写共享机制与已确认同类位点清单（或「已扫无同类」）；`- 影响` / `- 测试缺口` 覆盖合并后的位点并集。",
        "`parked/` 条目追加 `- 暂搁：YYYY-MM-DD 决定不办的理由`，写清为什么现在不动（风险可控、排期靠后、等外部依赖等）。复活时 `revive` 会删该字段并把 `- 处理` 改回「未开」，保留原编号。",
    )),
    "docs/findings/README.md": _known_lines((
        "# 发现总账",
        "已被验证的技术发现与踩坑记录，跨 task 复用。一条目一文件，文件名 `dNNN_{slug}.md`。",
        "- spike 结论收尾时抽一条到这里；spike 报告全文留在 `docs/archive/spikes/`。",
        "- 日常发现（工具行为、平台差异、依赖坑、性能特征、被证伪的假设）随时追加。",
        "- 只记**已验证**的事实与证据来源；推测和待确认的写进对应 task 的 spec 上下文区：只有用户或外部环境能核实的标 `UNVERIFIED-BLOCKING`，agent 可实验核实的标 `UNVERIFIED-SPIKE`。",
        "- 发现是长期资产，不存在「闭环」，因此不迁 archive。失效时保留条目，改写 `- 现状` 为「YYYY-MM-DD 失效：原因」。",
        "- `dNNN` 全局递增不复用。",
        "## 命令",
        "```bash",
        "```",
        "python3 scripts/repo_template/findings.py new --slug uv_lock_platform_marker",
        "python3 scripts/repo_template/findings.py list",
        "`new` 在 git 公共目录的排他锁内完成「扫描取号 → 建文件」，并发 worker 不会撞号；禁止手工创建条目文件。",
        "## 字段",
        "`- 来源` / `- 结论` / `- 证据` / `- 影响` / `- 现状`。`- 来源` 写 `sNNN` spike、`tNNN` task、或「日常」。新条目现状写「有效」。",
    )),
}


def _inspect_doc(sync: ctx.SyncContext, rel: str) -> tuple[str, list[str]]:
    path = sync.consumer / rel
    if not (path.exists() or path.is_symlink()):
        return "absent", []
    if path.is_symlink() or not path.is_file():
        raise ctx.SyncError(f"{rel} 是软链或目录；请人工处理后重跑同步")
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as error:
        raise ctx.SyncError(f"{rel} 无法读取（{error}）；请人工核对后处理") from error
    markers = _OBSOLETE_MARKERS[rel]
    if not any(marker in text for marker in markers):
        return "clean", []
    known = _KNOWN_TEMPLATE_LINES[rel]
    custom_lines = [
        line.strip()
        for line in text.splitlines()
        if (norm := _norm_line(line)) and norm not in known
    ]
    if custom_lines:
        return "custom", custom_lines
    return "obsolete_pure", []


def retired_docs_migration_status(sync: ctx.SyncContext) -> list[str]:
    """检测仍含已废除旧模板口径的文档（docs/pending/README.md、docs/findings/README.md）。"""
    findings: list[str] = []
    for rel in RETIRED_DOC_PATHS:
        try:
            kind, _custom = _inspect_doc(sync, rel)
        except ctx.SyncError as error:
            findings.append(f"{rel} 存在异常：{error}")
            continue
        if kind == "obsolete_pure":
            findings.append(f"{rel} 为已下线旧模板文档，迁移将删除")
        elif kind == "custom":
            findings.append(f"{rel} 含已下线旧模板口径与消费仓自定义内容，需人工迁出后删除")
    return findings


def migrate_retired_docs(sync: ctx.SyncContext, *, write: bool = False) -> list[str]:
    """预检或在同步事务内清理纯旧版下线文档；含消费定制则阻断。"""
    reports: list[str] = []
    to_delete: list[Path] = []
    for rel in RETIRED_DOC_PATHS:
        kind, custom_lines = _inspect_doc(sync, rel)
        if kind in ("absent", "clean"):
            continue
        if kind == "custom":
            preview = " / ".join(custom_lines[:3])
            more = f"（共 {len(custom_lines)} 行）" if len(custom_lines) > 3 else ""
            raise ctx.SyncError(
                f"{rel} 含已下线旧模板口径且包含消费仓自定义内容{more}：{preview}；"
                "请将有效项目说明迁至 AGENTS.md 项目区或 docs/blueprint/conventions.md 后删除该过期文件"
            )
        to_delete.append(sync.consumer / rel)
        reports.append(f"删除已下线旧模板文档：{rel}")
    if not write:
        return reports
    for path in to_delete:
        if path.is_file() and not path.is_symlink():
            path.unlink()
    return reports
