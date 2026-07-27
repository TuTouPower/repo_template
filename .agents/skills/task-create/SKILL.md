---
name: task-create
description: none
disable-model-invocation: true
---

# task-create

把**用户需求**拆成合格 **backlog** task。规则见 `AGENTS.md`；`task-bug` / `pending-to-task` 建 task 的落盘步骤也以本 skill 为准。

## 不用本 skill 的场景

| 场景 | 用 |
|------|-----|
| 修 bug、复现、根因、补测分析 | `task-bug` |
| 把遗留待办转成 task | `pending-to-task` |
| 执行实施 | `tasks-run` |
| 只查缺什么资源 | `tasks-preflight` |

## 步骤

1. **查重**。`scripts/task.py list`，避免重复 slug/等价范围。

2. **拆分**。拆成结果可独立验证、对应一个 commit 有工程意义的 task；过大继续拆；记下建议顺序与依赖。

3. **每个 task 落盘**：
   1. `scripts/task.py add --title "..." --slug "..." [--review-level full|single]`
      脚本自动分配 tid、建 `docs/tasks/{tid}_{slug}/`、复制模板、写 front matter。
   2. `review_level` 按风险判：
      | level | 适用 |
      |-------|------|
      | `full` | 安全、鉴权、资金、并发、数据迁移、协议兼容（默认） |
      | `single` | 其余全部（含纯文档、配置、格式化） |
      判不准取 `full`。定 `single` 时在提交询问里说明理由，由用户确认。
   3. 只读仓库，填写 `spec.md`：
      - **契约区**：范围、非范围、行为 AC（非空）、可测试性声明。版本号/库/目录不进 AC；需部署或人工验证的 AC 加 `[deploy]`。
      - **上下文区**：有意不测、测试策略、未知契约清单（未核实的外部契约标 `UNVERIFIED`）、风险与回退、依赖与约束、finalization 更新的 blueprint。
      - 契约区执行期不改；上下文区执行期可补。
   4. `task.md`：只填正文能填的部分。front matter 由脚本维护，**不手改**；`diff_anchor` 留空（`tasks-run` Step 1 实写）。
      **不预测实施步骤**——创建期未读代码，写出来的步骤执行时必然失准；步骤由 `tasks-run` 边做边记进「实施笔记」。

4. **自检**：AC 可验收；`spec.md` / `task.md` 无残留 `{...}` 占位符。

5. **spike 需求**：task 需要先做实验确认的事项（新 major、非标准 provider、协议兼容、平台差异、性能或工具行为），写进 spec 上下文区的「风险与回退」或「未知契约清单」，标 `UNVERIFIED`。不在创建期写生产代码，留给 `tasks-run` 执行期做。

6. **询问提交**。列出新建/修改的文档（task 目录 + index），询问用户是否提交；同意后才 commit（一个 task 目录一个 commit，不含生产实现）。用户不提交则保持工作区。

## 边界

- 不 `start` / `finish` / 实施修复。
- 新建时只读仓库。

## 完成

backlog 已就绪，未 start。下一步 `tasks-run`（或先 `tasks-preflight`）。
