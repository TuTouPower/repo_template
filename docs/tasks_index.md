# 任务总清单

- `tid` 在此分配，全局递增；取 `docs/tasks/` 与 `docs/archive/tasks/` 中最大 `tid` 加一，无历史时从 `t001` 开始。
- 状态只使用：`backlog`、`active`、`done`、`dropped`。
- `backlog` 已建目录，含已填写的 `spec.md` / `plan.md` / `task.md`（验收标准非空；front matter 含 `tid`/`slug`）；`active` 补齐 branch，且必须在 `{tid}_{slug}` 分支上工作。
- `done` 及所有 `dropped` 任务目录**一律**移入 `docs/archive/tasks/`。
- branch 为工作分支，形如 `{tid}_{slug}`（如 `t001_foo`）。
- 有遗留时：状态仍为 `done`，备注 `done_with_exception` 及 `finding_id`；详情在 `task.md` 收尾报告并口头报告。

| tid | 标题 | 状态 | branch | 备注 |
|-----|------|------|--------|------|
| t001 | 落地 multi-model-adoption：工作流与模板一致性 | done | t001_workflow_adoption_fix | review_20260720_2346 |
