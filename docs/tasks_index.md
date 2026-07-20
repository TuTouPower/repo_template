# 任务总清单

- `tid` 在此分配，全局递增；取 `docs/tasks/` 与 `docs/archive/tasks/` 中最大 `tid` 加一，无历史时从 `t001` 开始。
- 状态只使用：`backlog`、`active`、`blocked`、`done`、`dropped`。
- `backlog` 已建目录，含已填写的 `spec.md` / `plan.md` / `task.md`（验收标准非空；front matter 含 `tid`/`slug`）；`active` 补齐 branch，且必须在 `{tid}_{slug}` 分支上工作。
- `blocked`：黑盒满 5 轮未过或双审满 2 轮仍 FAIL；目录仍在 `docs/tasks/`，**不**归档；备注 `blocked: blackbox` 或 `blocked: review`；等用户加轮 / dropped / exception。权威见 `AGENTS.md`「blocked」。
- `done` 及所有 `dropped` 任务目录**一律**移入 `docs/archive/tasks/`。`blocked` 不归档。
- branch 为工作分支，形如 `{tid}_{slug}`（如 `t001_foo`）。
- exception 收尾：状态 `done`，备注 `done_with_exception` 及依据；详情在 `task.md` 收尾报告并口头报告（须用户对 blocked 显式放行，见 `AGENTS.md`）。

| tid | 标题 | 状态 | branch | 备注 |
|-----|------|------|--------|------|
| t001 | 落地 multi-model-adoption：工作流与模板一致性 | done | t001_workflow_adoption_fix | review_20260720_2346 |
