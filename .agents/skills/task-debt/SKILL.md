---
name: pending-to-task
description: none
disable-model-invocation: true
---

# pending-to-task

把 `docs/pending.md`「遗留待办」节里该做未做的项转成 backlog task。落盘步骤同 `task-create`。

未修 bug（`bNNN`）走 `task-bug`，不用本 skill。

## 步骤

1. **读总账**（只读）：
   - `docs/pending.md`「遗留待办」节中 `处理：未开 task` 的条目——**主要来源**。
   - 补扫尚未登记进总账的项（登记本应由 `tasks-run` 收尾完成，此处扫漏）：
     - `scripts/task.py list` + 各 task 的 `task.md`：`## Review 处置` 中 `status=遗留` 但 `fix_ref` 为空的行
     - `review_code.md` / `review_test.md` 中仍开放的 important/critical
     - `docs/handoff.md` 末段待办
     - 用户点名的目录或 diff
   - 扫到未登记项：先补进 `docs/pending.md`「遗留待办」节（`- 来源` 写 finding_id 或原 tid），再走下面流程。总账是唯一入口，不绕过。

2. **去重**：
   - 已有等价 backlog/active task → 不新建，把该 tid 写进条目 `- 处理：`，汇报已有 tid
   - 跨 task 同一系统性缺口 → 合并成一个 follow-up task，多个条目共用同一 tid
   - minor 品味项默认不建，除非用户要求或累积为明确债务包

3. **确认范围**。候选多或含争议时与用户确认；用户已说「全部捞」则全建非重复项。

4. **每个确认项落盘**（按 `task-create` 流程，链式调用）。spec 上下文区写清来源 `fNNN` / finding_id / 原 tid。

5. **回写总账**。每个已建 task 的条目：`- 处理：未开 task` 改为 `- 处理：{tid}`，**整条**移入 `docs/archive/pending.md`「已处理遗留」节。条目留在总账里等于没转。

6. **询问提交**。列出新建/修改的文档（task 目录、`docs/pending.md`、`docs/archive/pending.md`），询问用户是否提交；同意后才 commit（创建期可一批）。

## 边界

- 新建时不写生产代码；编码与补测方向写进 spec 上下文区，等 `tasks-run`。
- task 状态只经 `scripts/task.py`（index JSON 是派生缓存，不手改也不入 commit）。
- `docs/archive/pending.md` 只追加，禁止截断或改写已归档条目。
- 不把「已 done 且仅文档考古」无差别全建成 task。
- 未修 bug 交给 `task-bug`；已验证的技术发现属 `docs/findings.md`，不是待办，不转 task。
- 未经用户同意不 commit 创建物。

## 完成

汇报：新建 tid、跳过（已有）列表、回写并归档的 `fNNN`、建议 `tasks-run` 顺序。
