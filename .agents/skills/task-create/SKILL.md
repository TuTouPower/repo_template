---
name: task-create
description: none
disable-model-invocation: true
---

# task-create

把**用户需求**拆成合格 **backlog** task。规则见 `AGENTS.md`；`task-bug` / `task-from-pending` 建 task 的落盘步骤也以本 skill 为准。

## 不用本 skill 的场景

| 场景 | 用 |
|------|-----|
| 修 bug、复现、根因、补测分析 | `task-bug` |
| 把待办转成 task | `task-from-pending` |
| 执行实施 | `task-run` |
| 只查缺什么资源 | `task-preflight` |

## 步骤

1. **查重**。按登记 worktree → 未合并 task 分支链尾 ref → main 的优先级读取现有 task，避免重复 slug/等价范围；main 中被链覆盖的旧状态不重复计。

2. **拆分**。拆成结果可独立验证、对应一个实现 commit 有工程意义的 task；过大继续拆；记下建议顺序与依赖。

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
      - **上下文区**：有意不测、测试策略、未知契约清单、风险与回退、依赖与约束、finalization 更新的 blueprint。未知契约须分类：只有用户或外部环境能核实的标 `UNVERIFIED-BLOCKING`；agent 可在执行期实验核实的标 `UNVERIFIED-SPIKE`；禁止裸 `UNVERIFIED`。
      - 契约区执行期不改；上下文区执行期可补。
   4. `task.md`：只填正文能填的部分。front matter 由脚本维护，**不手改**；`diff_anchor` 留空（`task-run` Step 1 实写）。
      **不预测实施步骤**——创建期未读代码，写出来的步骤执行时必然失准；步骤由 `task-run` 边做边记进「实施笔记」。

4. **逐 task 自检**：
   - AC 可验收，`spec.md` / `task.md` 无残留 `{...}` 占位符。
   - 每个 task 填写完成后运行 `python3 scripts/task.py preflight {tid} --allow-backlog`；全部 `preflight=PASS` 才能进入统一询问提交。

5. **未知契约分类**：
   - task 需要先做实验确认的事项（新 major、非标准 provider、协议兼容、平台差异、性能或工具行为），写进「未知契约清单」并标 `UNVERIFIED-SPIKE`。不在创建期写生产代码，留给 `task-run` Step 1 实验。
   - 只有用户或外部环境能核实的事项标 `UNVERIFIED-BLOCKING`；task 可保持 backlog，但核实前 `start` 必须失败。
   - 核实后删除标记，改写为结论与验证方式；裸 `UNVERIFIED` 视为格式错误。

6. **全部 task 落盘自检后统一询问提交**。本次所有 task 完成第 3-5 步落盘与自检后，一次性列出全部新建 task 目录与本次重建的两个 index，询问用户是否提交；同意后用**一个创建 commit** 提交本次全部 task 目录与派生 index，不含生产实现。单 task 场景行为一致（落盘自检后提交一次）。链式调用时，调用方改动的总账不纳入本 commit，由调用方在 task 创建完成后单独回写、确认与提交。用户不提交则保持工作区。

## 边界

- 不 `start` / `finish` / 实施修复。
- 创建期只读生产树；只允许写当前 task 目录与 `task.py` 自动重建的两个 index。

## 完成

backlog 已就绪，未 start。下一步 `task-run`（或先 `task-preflight`）。
