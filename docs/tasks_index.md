# 任务总清单

- ID 在此分配，全局递增；取 `docs/tasks/` 与 `docs/archive/tasks/` 中最大 ID 加一，无历史时从 T001 开始。
- 状态只使用：`backlog`、`active`、`done`、`dropped`。
- `backlog` 已建目录，含已填写的 `spec.md` / `plan.md` / `task.md`（验收标准非空；front matter 含 tid/slug）；`active` 补齐 owner / branch，且必须在 `task_{tid}_{slug}` 分支上工作（`{tid}` = `{TID}` 小写）。
- `done` 及所有 `dropped` 任务目录**一律**移入 `docs/archive/tasks/`。
- owner 和 branch 表示当前归属；工作分支推荐 `task_{tid}_{slug}`。
- 有遗留时：状态仍为 `done`，备注 `done_with_exception` 及 finding ID；详情在 `task.md` 收尾报告并口头报告。

| ID | 标题 | 状态 | owner | branch | 备注 |
|----|------|------|-------|--------|------|
| T001 | 落地 multi-model-adoption：工作流与模板一致性 | done | grok | task_t001_workflow_adoption_fix | review_20260720_2346 |
