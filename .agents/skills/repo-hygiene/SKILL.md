---
name: repo-hygiene
description: none
disable-model-invocation: true
---

# repo-hygiene

把**已闭环 / 已过时**内容迁入 `docs/archive/`，`docs/` 只保留**未闭环 / 暂搁**与**整理后的最新**。

## 目标视图

| 路径 | 只保留 | 迁 archive |
|------|--------|------------|
| `docs/pending.md` | 未闭环待办 + 暂搁（「不办」节） | 已闭环条目 → `docs/archive/pending.md`「已处理待办」节（整条追加） |
| `docs/findings.md` | 全部已验证发现 | **不迁**：发现是长期资产，失效时就地改写「现状」并注明日期 |
| `docs/handoff.md` | 最新一版交接 | 过时段落 → `docs/archive/handoff.md`（整段追加） |
| `docs/tasks/{tid}_{slug}/task.md` | 活跃 task 状态（经 `task.py`） | 已由 `finish`/`drop` 处理，本 skill 不手改 |
| `docs/spikes/{sid}_{slug}/` | 进行中的 spike | 报告已写且结论已入 `docs/findings.md` 的完结 spike → 整目录迁 `docs/archive/spikes/` |
| `docs/reviews/review_*/` | 报告 `review_*.md` 入库保留，`_meta/` 本地保留但不入库 | 用户确认过时 → 包含 `_meta/` 的整目录迁 `docs/archive/reviews/`；archive 下 `_meta/` 继续 gitignore |
| 其它过时文档 | 仍生效的说明 | 明确过时且有历史价值 → `docs/archive/` 镜像路径 |

## 步骤

1. **盘点**（只读）：
   - task 状态按登记 worktree → 未合并 task 分支链尾 ref → main 盘点：用 `git worktree list --porcelain`、`git branch --no-merged <default> --list 't[0-9]*_*'`、`scripts/task.py list/show --ref` 只读核对有效状态与目录。main 中被 worktree或链尾覆盖的旧 backlog 不算状态不一致。两个派生 index 只对应 main 已合并状态；内容错误时留到步骤 5 处理。
   - **list↔目录对照时排除模板**（非工作项，`task.py` 扫描时已跳过，**禁止**当残留报告或删除）：
     - `docs/tasks/task_template/`
     - `docs/spikes/report_template.md`
     - `docs/reviews/prompts/`
     - 各领域下的 `.gitkeep`（若有）
   - `docs/handoff.md`、`docs/pending.md`、`docs/findings.md`、`docs/guides/`、`docs/reviews/review_*/`、根目录杂散 md、明显草案。
   - `docs/archive/pending.md`、`docs/archive/handoff.md` 是否已存在（只追加，不截断）。

2. **pending**（补迁：`task-run` 收尾本应已迁；此处扫漏）：
   - 未闭环（`- 处理：未开` 或等价）→ 留在 `docs/pending.md`。
   - 已闭环（`- 处理：tXXX` 或明确外部动作说明）→ 用 `scripts/pending.py archive` 批量迁入 `docs/archive/pending.md`「## 已处理待办」节：
     ```bash
     # dry-run 先看拟定改动
     python3 scripts/pending.py archive p112 p113 p120-p146 --fix-ref t012
     # 确认后落盘
     python3 scripts/pending.py archive p112 p113 p120-p146 --fix-ref t012 --write
     ```
     脚本整条从 `docs/pending.md` 删除、整条追加到 archive；`--fix-ref` 必传，统一改写 `- 处理:` 字段为指定 tid；「不办」节条目脚本自动拒迁；缺号、重复归档、缺 `- 处理:` 字段均报错。单条手工迁移也可直接编辑两文件，遵循下方规则。
   - **「不办」节条目保留不动**（`- 处理：不办` 属暂搁，非闭环，禁止迁 archive）；用户显式确认彻底放弃或复活闭环后才按对应规则处理。
   - 不改写条目历史字段；脚本迁移时保持原标题与 bullet 原文（仅 `- 处理:` 字段按 `--fix-ref` 改写）。
   - 不把「看起来过时」但未标闭环的条目静默删掉；不确定则报告用户。
   - **扫遗留漏登**：已归档 task 的处置表中 `status=遗留` 但 `fix_ref` 为空的行（`task-run` 收尾应已登记，此处扫漏）→ 先运行 `scripts/pending.py next` 取编号，再补登到 `docs/pending.md`「待办」节（按普通或 bug 模板选），`- 来源` 写 finding_id 与原 tid。
   - **扫 findings 漏抽**：`docs/archive/spikes/` 中已归档但结论未进 `docs/findings.md` 的 spike → 报告用户，由用户确认后补抽（不自行判断哪条结论值得留）。

3. **handoff**：
   - `docs/handoff.md` 只留**当前有效**一节（或整理后的最新摘要）。
   - 更早段落整段迁入 `docs/archive/handoff.md`（append）；不截断 archive。
   - 若整理时需刷新近况：可在 `docs/handoff.md` 写/替换「当前」节（UTC+8 日期、branch、head、活跃 tid 摘要）；被替换的旧「当前」先入 archive 再写新内容。

4. **其它过时文档**：
   - 仍生效 → 留原位，必要时改一句过时表述（最小块）。
   - 明确过时且有历史价值 → 迁 `docs/archive/` 镜像路径。
   - 无价值草稿且用户确认 → 可删；未确认不删。
   - **spike 迁移**：`docs/spikes/{sid}_{slug}/` 报告已写且结论已入 `docs/findings.md` → 整目录迁 `docs/archive/spikes/`；拿不准是否完结则报告用户，不擅自迁。
   - **review 迁移**：逐个检查 `docs/reviews/review_*/`；用户已确认过时 → 包含 `_meta/` 的整目录迁 `docs/archive/reviews/`，保留目录名与全部内容。`review_*.md` 继续入库，`_meta/` 在 archive 路径继续 gitignore。目标目录同名项已存在时停止并报告，不覆盖或合并。
   - **不**把上述模板路径当过时文档归档或删除。

5. **task 状态一致性**：按步骤 1 的有效来源发现 front matter 与目录不一致时**报告用户**，用 `drop` / `finish` / `purge` / `rewind` 等合法命令修。对照目录时**跳过**步骤 1 列出的模板路径。

   派生 index（`docs/tasks_index.json`、`docs/archive/tasks_index.json`）只反映 main 已合并状态。内容与 main 不符时可跑 `task.py list --rebuild`；存在未合并批次链时不得用链尾状态改写 main index。权责见 `AGENTS.md` 目录权责表。

6. **提交**：改动做一个 hygiene commit（或按用户要求不提交）；subject 如 `docs: repo hygiene`。

## 边界

- **docs 侧可删迁**：从 active 文件移除已修/过时内容属于本 skill 职责，不是「篡改历史」。
- **不挪 active task 目录**（归档只由 `finish` / `drop` 完成）。
- **保护模板**：`docs/tasks/task_template/`、`docs/spikes/report_template.md`、`docs/reviews/prompts/` 永不当残留、不 rm、不迁 archive。
- 不借机改 `src/` 业务逻辑；不批量 `finish` 未完成 task；不 `purge` 有目录/有 commit 的项。
- 不把 skill / AGENTS 正文当「过时」误归档。
- **`docs_repo/`**：仅本模板仓维护笔记；本 skill 不整理、不迁 archive、不要求新项目保留（新项目本就不该有该目录）。

## 完成

汇报：迁入 archive 的 pending/handoff 条目、spike/review/其它文档目录，docs 中保留的未闭环/最新内容、跳过项、仍需用户决定的不一致项。
