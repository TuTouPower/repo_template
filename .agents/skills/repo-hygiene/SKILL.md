---
name: repo-hygiene
description: none
disable-model-invocation: true
---

# repo-hygiene

把**已修 / 已过时**内容迁入 `docs/archive/`，`docs/` 只保留**未做未修**与**整理后的最新**。

## 目标视图

| 路径 | 只保留 | 迁 archive |
|------|--------|------------|
| `docs/bugs.md` | 未修 bug | 已修条目 → `docs/archive/bugs.md`（整条追加） |
| `docs/handoff.md` | 最新一版交接 | 过时段落 → `docs/archive/handoff.md`（整段追加） |
| `docs/tasks_index.json` | 活跃 task（经 `task.py`） | 已由 `finish`/`drop` 处理，本 skill 不手改 |
| 其它过时文档 | 仍生效的说明 | 明确过时且有历史价值 → `docs/archive/` 镜像路径 |

## 步骤

1. **盘点**（只读）：
   - `scripts/task.py list`：活跃 vs 归档是否与目录一致（残留目录、缺目录 → 报告，不私自 rm）。
   - `docs/handoff.md`、`docs/bugs.md`、`docs/guides/`、根目录杂散 md、明显草案。
   - `docs/archive/bugs.md`、`docs/archive/handoff.md` 是否已存在（只追加，不截断）。

2. **bugs**（补迁：`tasks-run` 收尾本应已迁；此处扫漏）：
   - 未修（`修复：未修` 或等价）→ 留在 `docs/bugs.md`。
   - 已修（有 `修复：tXXX` 等明确修复标记）→ **整条**从 `docs/bugs.md` 删掉，**追加**到 `docs/archive/bugs.md`。
   - 不改写条目历史字段；迁移时保持原标题与 bullet 原文。
   - 不把「看起来过时」但未标已修的 bug 静默删掉；不确定则报告用户。

3. **handoff**：
   - `docs/handoff.md` 只留**当前有效**一节（或整理后的最新摘要）。
   - 更早段落整段迁入 `docs/archive/handoff.md`（append）；不截断 archive。
   - 若整理时需刷新近况：可在 `docs/handoff.md` 写/替换「当前」节（UTC+8 日期、branch、head、活跃 tid 摘要）；被替换的旧「当前」先入 archive 再写新内容。

4. **其它过时文档**：
   - 仍生效 → 留原位，必要时改一句过时表述（最小块）。
   - 明确过时且有历史价值 → 迁 `docs/archive/` 镜像路径。
   - 无价值草稿且用户确认 → 可删；未确认不删。

5. **tasks_index 一致性**：用 `task.py list` 展示 active；发现 JSON 与目录不一致时**报告用户**，用 `drop` / `finish` / `purge` 等合法命令修。禁止手编 JSON。

6. **提交**：改动做一个 hygiene commit（或按用户要求不提交）；subject 如 `docs: repo hygiene`。

## 边界

- **不手改** `docs/tasks_index.json` / `docs/archive/tasks_index.json`（只经 `scripts/task.py`）。
- **archive 只追加**：`docs/archive/bugs.md`、`docs/archive/handoff.md` 禁止截断、改写已归档条目。
- **docs 侧可删迁**：从 active 文件移除已修/过时内容属于本 skill 职责，不是「篡改历史」。
- **不挪 active task 目录**（归档只由 `finish` / `drop` 完成）。
- 不借机改 `src/` 业务逻辑；不批量 `finish` 未完成 task；不 `purge` 有目录/有 commit 的项。
- 不把 skill / AGENTS 正文当「过时」误归档。

## 完成

汇报：迁入 archive 的 bugs/handoff 条目、docs 中保留的未修/最新内容、跳过项、仍需用户决定的不一致项。
