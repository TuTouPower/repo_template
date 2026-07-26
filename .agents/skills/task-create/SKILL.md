---
name: task-create
description: none
disable-model-invocation: true
---

# task-create

把**用户需求**拆成合格 **backlog** task。规则见 `AGENTS.md`；`task-bug` / `task-debt` 建 task 的落盘步骤也以本 skill 为准。

## 不用本 skill 的场景

| 场景 | 用 |
|------|-----|
| 修 bug、复现、根因、补测分析 | `task-bug` |
| 捞遗留 finding / 技术债 | `task-debt` |
| 执行实施 | `tasks-run` |
| 只查缺什么资源 | `tasks-preflight` |

## 步骤

1. **查重**。`scripts/task.py list`，避免重复 slug/等价范围。

2. **拆分**。拆成结果可独立验证的 task；过大继续拆；记下建议顺序与依赖。

3. **每个 task 落盘**：
   1. `scripts/task.py add --title "..." --slug "..."`
   2. 建 `docs/tasks/{tid}_{slug}/`
   3. 从 `docs/tasks/task_template/` 复制 `spec.md` / `plan.md` / `task.md`
   4. 只读仓库，填写：
      - `spec.md`：行为 AC 非空；版本号/库/目录不进 AC
      - `plan.md`：步骤、验证、风险；编码/spike 待执行
      - `task.md`：`tid`/`slug`；`diff_anchor` 可占位（开干时由 `tasks-run` 实写）

4. **自检** AC 可验收。

5. **询问提交**。列出新建/修改的文档（task 目录 + index），询问用户是否提交；同意后才 commit（创建期可一批，不含生产实现）。用户不提交则保持工作区。

## 边界

- 不 `start` / `finish` / 实施修复。
- 不写实现或测试到未 ignore 路径；编码、spike 写入 plan，等 `tasks-run`。
- 新建时只读仓库。JSON 只经 `scripts/task.py`。
- 未经用户同意不 commit 创建物。

## 完成

backlog 已就绪，未 start。下一步 `tasks-run`（或先 `tasks-preflight`）。
