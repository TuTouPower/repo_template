---
name: task-bug
description: none
disable-model-invocation: true
---

# task-bug

Bug 分析入口。复现、定位根因、做补测分析，结果记录到 `docs/pending.md`。不在本 skill 内修生产代码，不建 task。

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

6. **询问提交**。列出本次修改的文档（`docs/pending.md` + `.scratch/` 笔记如要入库），询问用户是否提交；同意后才 commit（一个 commit，subject 含 `bNNN` 与 bug 简述）。

## 边界

- 复现/探索代码**只许** `.scratch/`；禁止写未 ignore 路径（`src/` `tests/` `scripts/` 等）。
- 不 `start`、不建 task、不做生产修复。
- 后续立 task 走 `pending-to-task`（核实后觉得值得走 task 流程时）；修代码走 `tasks-run`。

## 完成

汇报：`bNNN`、根因一句话、补测要点、`.scratch/` 线索路径。自检：生产树无修复 diff，未建 task 目录。
