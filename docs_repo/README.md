# docs_repo

**仅本模板仓**的流程设计笔记与实战复盘。不是业务文档，复制成新项目时不得带入。

裁决结论落在 `repo/AGENTS.md`（项目骨架 / 状态机）、`repo/.repo_template/docs/usage.md`（用法 / 工具链写权 / skill 调用）、`repo/.repo_template/skills/`、`repo/.repo_template/docs/`；本目录只保留证据与推导过程。改流程前读 `decision_log.md`，不必重读全部复盘。

## 目录

| 路径 | 内容 |
| --- | --- |
| `README.md` / `decision_log.md` | 入口与跨文档裁决总账（唯一权威状态表） |
| `plans/` | 当前方案：[测试性能优化与工作流基线重构](plans/plan_test_suite_performance.md)、[生命周期实体稳定存储与历史文稿归档重构](plans/plan_entity_storage_and_archive.md)、[scratch 默认清理与在用草稿保护](plans/plan_scratch_cleanup.md)。已实施或被替代方案归入 `archive/` |
| `reflections/` | 实战复盘、会话反思与实证分析（`workflow_reflection_*` / `reflection_*` / `analysis_*`） |
| `reviews/` | [2026-11-09 合并审阅与修复](reviews/review_20261109.md)；各轮评审产物（`2026-07/` 多路评审意见；`review_20260805_133245/` 多模型交叉评审） |
| `archive/` | 已实施、已终止或被替代方案及历史机制说明；含看板 mock demo 的历史计划 `plan_board_v2_chain_panel_demo.md`（demo 源码已删，现行实现见该文首部说明） |

## 命名

| 前缀 | 文体 |
| --- | --- |
| `workflow_reflection_*` | 按 task 区间或主题的实战复盘（原 `retro_*` / `workflow_feedback` / `incident_*` 改名合并） |
| `analysis_*` | 会话或日志实证分析 |
| `plan_*` | 方案与设计笔记（部分已退役，以文档内状态标注为准） |
| `reflection_*` | 单主题反思 |
| `reviews/2026-07/{author}.md` | 对本目录的多方审阅意见 |
| `decision_log.md` | 跨文档裁决总账（唯一权威状态表） |

## 纪律

- 同一议题的裁决状态只在 `decision_log.md` 维护；复盘正文不重复列「待决策」清单。
- 结论被后续文档修正时，在原文段落首行标注被哪份文档发展，不静默保留多版本。
- 复盘不再新增未闭环的建议清单。新发现走 `decision_log.md` 加行。

## 已归档方案导航

以下文档原位于 `plans/`，现移至 `archive/`；历史评审中引用的旧路径与行号保留原意。归档不改变已有裁决或相关能力状态。

|文档|归档依据|
|---|---|
|[plan_goal_mode_autonomy.md](archive/plan_goal_mode_autonomy.md)|goal 方案已落地|
|[plan_macro_overhaul_2026_11.md](archive/plan_macro_overhaul_2026_11.md)|宏观改造已实施并合入 main|
|[plan_manual_concurrency.md](archive/plan_manual_concurrency.md)|手动并发方案已落地|
|[plan_md_kx_template.md](archive/plan_md_kx_template.md)|md_kx 接入已落地|
|[plan_task_batch_scheduling.md](archive/plan_task_batch_scheduling.md)|批次调度方案已由 task.py plan 取代|
|[plan_workflow_simplification.md](archive/plan_workflow_simplification.md)|工作流瘦身已实施|
|[worktree_statusline_tracking.md](archive/worktree_statusline_tracking.md)|已实现状态栏机制说明；现行入口在 usage.md|
