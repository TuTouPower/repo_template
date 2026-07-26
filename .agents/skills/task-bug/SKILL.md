---
name: task-bug
description: none
disable-model-invocation: true
---

# task-bug

Bug 修复入口。产出 **backlog 修复 task**（可多个），不在本 skill 内修生产代码。权威规则见 `AGENTS.md`「debug」「commit 策略」。

## 步骤

1. **收敛现象**。确认期望 vs 实际、影响范围、是否回归、相关 tid/日志。信息不明就问用户，不靠猜测下结论。

2. **复现（仅 `.scratch/`）**。最小复现；临时脚本与过程笔记写 `.scratch/`（已 gitignore）。
   - 复现失败：汇报已尝试的路径与卡点，停下；不硬建空 task（用户明确要求时可先建调查 task）。

3. **根因**。定位到可验证的机制（代码路径 / 配置 / 数据 / 环境），并归类：产品缺陷、环境问题、配置问题、测试假绿等。

4. **测试缺口分析（必做）**：
   - 现有测试为何没盖住（无测 / 弱断言 / mock 掉被测逻辑 / 只测假路径 / 缺集成层等）。
   - 应如何增删修改测试（层级、场景、断言对象）。

5. **建 task**。根因确认后按 `task-create` 流程落盘（链式调用）；落盘步骤以 `task-create` 为准，这里只规定文件内容：
   - `spec.md`：行为 AC + 复现要点。
   - `plan.md`：根因、修复步骤、补测步骤（含第 4 步结论）、`.scratch/` 线索路径。
   - `docs/bugs.md`：已有该 bug 条目时，plan 引用其 `bNNN`；没有条目且短期不修完时，按 bugs 规范追加条目。本 skill 不修复，不写「修复」行。

6. **询问提交**。列出本次新建/修改的文档，询问用户是否提交；同意后才 commit（创建期可多 task 一批，subject 含 bug 简述或 tid 列表）。

## 边界

- 复现/探索代码**只许** `.scratch/`；禁止写未 ignore 路径（`src/` `tests/` `scripts/` 等）。
- 不 `start`、不做生产修复；红绿验证与修复均留给 `tasks-run`。
- 未经用户同意不 commit 创建物。

## 完成

汇报：tid、根因一句话、补测要点、下一步 `tasks-run`。自检：生产树无修复 diff。
