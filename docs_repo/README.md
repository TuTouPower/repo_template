# docs_repo

**仅本模板仓**的流程设计笔记与实战复盘。不是业务文档，复制成新项目时不得带入。

裁决结论落在 `repo/AGENTS.md`（项目骨架 / 状态机）、`repo/.repo_template/docs/usage.md`（用法 / 工具链写权 / skill 调用）、`repo/.repo_template/skills/`、`repo/.repo_template/docs/`；本目录只保留证据与推导过程。改流程前读 `decision_log.md`，不必重读全部复盘。

## 目录

| 路径 | 内容 |
| --- | --- |
| `README.md` / `decision_log.md` | 入口与跨文档裁决总账（唯一权威状态表） |
| `plans/` | 方案与设计推导（`plan_*`；以文档内状态标注区分已落地/已退役）。当前改造方案：`plan_macro_overhaul_2026_11.md`。dispatch / 静默监控等过时提案已迁 `archive/` |
| `reflections/` | 实战复盘、会话反思与实证分析（`workflow_reflection_*` / `reflection_*` / `analysis_*`） |
| `reviews/` | 各轮评审产物（`2026-07/` 多路评审意见；`review_20260805_133245/` 多模型交叉评审） |
| `archive/` | 已终止或过时提案；含看板 mock demo 的历史计划 `plan_board_v2_chain_panel_demo.md`（demo 源码已删，现行实现见该文首部说明） |

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
