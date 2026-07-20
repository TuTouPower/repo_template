# 任务总清单

- ID 在此分配，全局递增；取 `docs/tasks/` 与 `docs/archive/tasks/` 中最大 ID 加一，无历史时从 T001 开始。
- 状态只使用：`backlog`、`active`、`done`、`dropped`。
- `backlog` 已建目录，含已填写的 `spec.md` / `plan.md` / `log.md`（验收标准非空）；`active` 补齐 owner / branch，且必须在 `task_tnnn_slug` 分支上工作。
- `done` 及曾 active 的 `dropped` 任务目录必须移入 `docs/archive/tasks/`。仅含未实质填写模板的 backlog `dropped` 可不归档，删除工作区目录即可。
- owner 和 branch 表示当前归属；工作分支推荐 `task_tnnn_slug`。
- 用户批准 exception 收尾时：状态仍可为 `done`，备注栏记 `done_with_exception`、批准人/时间/finding ID（reviewer verdict 不改写）。

| ID | 标题 | 状态 | owner | branch | 备注 |
|----|------|------|-------|--------|------|
| T001 | 落地 multi-model-adoption：工作流与模板一致性 | done | grok | task_t001_workflow_adoption_fix | review_20260720_2346 |
