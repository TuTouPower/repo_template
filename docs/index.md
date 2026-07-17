# 文档地图

`AGENTS.md` 是行为入口；本文件是按需导航入口。只读取当前任务需要的文档，不全量加载。

| 文档 | 内容 | 何时读 |
|------|------|--------|
| `blueprint/architecture.md` | 当前模块划分、数据流、进程和 seam | 修改跨模块行为前 |
| `blueprint/domain.md` | 当前领域概念、术语和命名 | 接触新业务概念时 |
| `blueprint/conventions.md` | 内容字段、命名、task/review/handoff 格式 | 写代码或文档前 |
| `blueprint/decisions.md` | 已确认的非显然决策 | 需要理解历史取舍时 |
| `tasks/index.md` | task ID、状态、owner、branch | 接到新需求或流转状态时 |
| `tasks/TNNN_slug/handoff.md` | 当前 task 交接快照 | 接手 task 时第一个读 |
| `tasks/TNNN_slug/spec.md` | 范围和验收标准 | 执行或审阅 task 时 |
| `tasks/TNNN_slug/plan.md` | 步骤、验证、风险、blueprint 更新清单 | spec 通过后 |
| `tasks/TNNN_slug/log.md` | 进展、偏离、决策和关键验证 | 接手或排查 task 时按需读 |
| `tasks/TNNN_slug/reviews/` | task review 报告和 adoption | review 环节 |
| `reviews/RNN_slug/` | 当前独立 review | 评审非 task 对象时 |
| `spikes/SNN_slug/report.md` | 当前实验问题、证据和结论 | 技术选型或未知风险验证时 |
| `handoff.md` | 跨 task 或项目级交接 | 接手项目级工作时第一个读 |
| `templates/task/` | task 文件模板 | 创建 active task 时复制 |
| `templates/review/` | task 和独立 review 共用模板 | 创建 review 时复制 |
| `templates/spike/` | spike report 模板 | 创建 spike 时复制 |
| `archive/` | 完结或终止的 task、review、spike | 追溯历史时 |

`docs/templates/` 只保存模板，不代表 active 数据。`docs/tasks/`、`docs/reviews/`、`docs/spikes/` 只保存当前工作；历史进入 `docs/archive/`。
