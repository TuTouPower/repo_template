---
name: task-bug
description: none
disable-model-invocation: true
---

# task-bug

Bug 分析与修复立项入口。复现、定位根因、做补测分析，登记 `bNNN`，再按 `task-create` 建 backlog 修复 task。不在本 skill 内改生产代码。

## 步骤

1. **收敛现象**。确认期望 vs 实际、影响范围、是否回归、相关 tid/日志。信息不明就问用户，不靠猜测下结论。

2. **复现（仅 `.scratch/`）**。最小复现；临时脚本与过程笔记写 `.scratch/`（已 gitignore）。
   - 复现失败：汇报已尝试的路径与卡点，停下；不硬登记。

3. **根因**。定位到可验证的机制（代码路径 / 配置 / 数据 / 环境），并归类：产品缺陷、环境问题、配置问题、测试假绿等。

4. **测试缺口分析（必做）**：
   - 现有测试为何没盖住（无测 / 弱断言 / mock 掉被测逻辑 / 只测假路径 / 缺集成层等）。
   - 应如何增删修改测试（层级、场景、断言对象）。

5. **登记到 pending**。根因确认后写入 `docs/pending.md`「未修 bug」节，拿 `bNNN`：
   - 现象：期望 vs 实际，复现步骤
   - 影响：受影响功能与范围
   - 根因：可验证的机制 + 分类
   - 测试缺口：第 4 步结论（为何没 catch + 补测方向）
   - 线索：`.scratch/` 路径
   - 修复：未修

   已有条目时更新内容、保留原 `bNNN`；没有条目追加新条目。

6. **建修复 task**。产品缺陷或测试假绿已确认后，链式调用 `task-create`：
   - spec 上下文区用结构化 `来源` 字段写 `bNNN`；
   - 写清根因、补测方向、风险与回退；
   - task 保持 `backlog`，生产修复交给 `tasks-run`；
   - 环境或配置问题无需改仓库时，不建修复 task，保留 `bNNN` 并汇报所需外部动作。

7. **询问提交 bug 总账**。`task-create` 已单独处理 task 目录与派生 index 的创建 commit；这里只列出 `docs/pending.md`。用户同意后提交 bug 登记；`.scratch/` 已 ignore，不入 commit。

## 边界

- 复现/探索代码**只许** `.scratch/`；禁止写未 ignore 路径（`src/` `tests/` `scripts/` 等）。
- 不 `start`，不做生产修复；修复 task 只按 `task-create` 落盘。
- 后续编码走 `tasks-run`；环境或配置问题按第 6 步汇报外部动作。

## 完成

汇报：`bNNN`、修复 task tid（若需仓库改动）、根因一句话、补测要点、`.scratch/` 线索路径。自检：生产树无修复 diff，task 未 start。
