---
name: task-debt
description: none
disable-model-invocation: true
---

# task-debt

从仓库**已有记录**捞「该做未做」的项，建成 backlog task。落盘步骤同 `task-create`。

## 步骤

1. **扫描**（只读，优先 active + 近期 archive，不扫无关历史全库）：
   - `scripts/task.py list` + 各 task 的 `task.md`：`## Review 处置` 中 `status=遗留`、收尾报告「遗留」、过程记录的 follow-up
   - `review_code.md` / `review_test.md` 中未进处置或仍开放的 important/critical（以 task.md 处置为准，报告为辅）
   - `docs/bugs.md` 中 `修复：未修`
   - `docs/handoff.md` 末段待办
   - 用户点名的目录或 diff

2. **去重**：
   - 已有等价 backlog/active task → 不新建，汇报已有 tid
   - 跨 task 同一系统性缺口 → 一个 follow-up task
   - minor 品味项默认不建，除非用户要求或累积为明确债务包

3. **确认范围**。候选多或含争议时与用户确认；用户已说「全部捞」则全建非重复项。

4. **每个确认项落盘**（按 `task-create` 流程，链式调用）。plan 写清来源 finding_id / bNNN / 原 tid。

5. **询问提交**。列出新建/修改的文档，询问用户是否提交；同意后才 commit（创建期可一批）。

## 边界

- 新建时不写生产代码；编码/补测写 plan，等 `tasks-run`。
- 不手改 `tasks_index.json`（只经 `scripts/task.py`）。
- 不把「已 done 且仅文档考古」无差别全建成 task。
- 未经用户同意不 commit 创建物。

## 完成

汇报：新建 tid、跳过（已有）列表、建议 `tasks-run` 顺序。
