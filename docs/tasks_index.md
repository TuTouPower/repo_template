# 任务总清单

- `tid` 在此分配，全局递增；取 `docs/tasks/` 与 `docs/archive/tasks/` 中最大 `tid` 加一，无历史时从 `t001` 开始。
- 状态只使用：`backlog`、`active`、`blocked`、`done`、`dropped`。
- `backlog` 已建目录，含已填写的 `spec.md` / `plan.md` / `task.md`（验收标准非空；front matter 含 `tid`/`slug`）；`active` 补齐 branch，且必须在 `{tid}_{slug}` 分支上工作。
- `blocked`：黑盒轮次达 `max_verify_round`（默认 5）未过，或双审 `round` 达 `max_review_round`（默认 2）仍 FAIL；目录仍在 `docs/tasks/`，**不**归档；备注 `blocked: blackbox` 或 `blocked: review`；agent 停自动推进并向用户请求：加轮 / dropped。权威见 `AGENTS.md`「blocked」。
- `done` 及所有 `dropped` 任务目录**一律**移入 `docs/archive/tasks/`。`blocked` 不归档。
- branch 为工作分支，形如 `{tid}_{slug}`（如 `t001_foo`）。

| tid | 标题 | 状态 | branch | 备注 |
|-----|------|------|--------|------|
| t001 | 落地 multi-model-adoption：工作流与模板一致性 | done | t001_workflow_adoption_fix | review_20260720_2346 |
