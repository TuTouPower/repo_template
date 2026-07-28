---
name: pending-to-task
description: none
disable-model-invocation: true
---

# pending-to-task

把 `docs/pending.md`「遗留待办」节里该做未做的项转成 backlog task。

**task 流程很重**——每个 task 有 spec、实施、黑盒、审阅、收尾、commit 整套门禁。pending 条目只是记录，不等于值得立 task。建 task 前先核实再决定。

未修 bug（`bNNN`）由 `task-bug` 完成复现、根因与修复 task 立项，不从本 skill 重复创建。

## 步骤

1. **读总账**（只读）：
   - `docs/pending.md`「遗留待办」节中 `处理：未开 task` 的条目——**主要来源**。
   - 补扫尚未登记进总账的项（登记本应由 `tasks-run` 收尾完成，此处扫漏）：
     - `scripts/task.py list` + 各 task 的 `task.md`：`## Review 处置` 中 `status=遗留` 但 `fix_ref` 为空的行
     - `review_code.md` / `review_test.md` / `review_general.md` 中仍开放的 important/critical
     - `docs/handoff.md` 末段待办
     - 用户点名的目录或 diff
   - 扫到未登记项：先补进 `docs/pending.md`「遗留待办」节（`- 来源` 写 finding_id 或原 tid），再走下面流程。总账是唯一入口，不绕过。

2. **核实条目**（必做，不是可选）。pending 里的描述可能过时、片面或记错——登记时的情况跟现在不一样是常态。对每条候选：
   - 读当前代码确认「这个问题现在还在吗」；不在 → 标 `已闭环`（`- 处理：已验证不存在`），不走 task
   - 读当前 spec / 测试确认「描述跟现状一致吗」；不一致 → 按现状重写或更新条目，再决定建不建 task
   - 评估影响范围：是真的重要（会影响正确性 / 安全 / 数据），还是只是「当时应该做但没关系」？后者留给用户拍板，不自行建 task
   - 能通过小修直接解决的（非 bug 级小调整），报给用户判断要不要直接修——不是每条都值得走 task 流程

3. **合并与去重**：
   - 已有等价 backlog/active task → 不新建，把该 tid 写进条目 `- 处理：`，汇报已有 tid
   - 同主题的多个条目 → **合并成一个 task**；跨条目的同一系统性缺口 → 一个 follow-up task
   - minor 品味项默认不建，除非用户要求或累积为明确债务包

4. **确认范围**。候选多、含争议、或评估后判断条目不重要时，向用户呈核实结论（现在还在不在、影响、建议）并确认；用户已说「全部捞」则全建非重复、仍有效的项。

5. **每个确认项落盘**（按 `task-create` 流程，链式调用）。spec 上下文区写清来源 `fNNN` / finding_id / 原 tid + 核实结论（什么时候核实、核实结果）。

6. **回写总账**。每个已建 task 的条目：`- 处理：未开 task` 改为 `- 处理：{tid}`，**整条**移入 `docs/archive/pending.md`「已处理遗留」节。条目留在总账里等于没转。

7. **询问提交总账回写**。`task-create` 已逐 task 提交 task 目录与派生 index；这里只列出 `docs/pending.md`、`docs/archive/pending.md`，询问用户是否提交。

## 边界

- 新建时不写生产代码；编码与补测方向写进 spec 上下文区，等 `tasks-run`。
- 不把「已 done 且仅文档考古」无差别全建成 task。
- 条目核实后判断不存在或已过时，不建 task；直接闭环归档即可。
- 未修 bug 交给 `task-bug`；已验证的技术发现属 `docs/findings.md`，不是待办，不转 task。

## 完成

汇报：核实后仍有效的条目、新建 tid、合并的条目列表、判断已不存在的条目（已闭环）、跳过（已有）列表、建议 `tasks-run` 顺序。
