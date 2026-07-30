# docs_repo

**仅本模板仓**的流程设计笔记与实战复盘。不是业务文档，复制成新项目时不得带入。

裁决结论落在 `AGENTS.md`、`.agents/skills/`、`docs/reviews/prompts/`、`docs/tasks/task_template/`；本目录只保留证据与推导过程。改流程前读 `decision_log.md`，不必重读全部复盘。

## 推荐阅读顺序

| 顺序 | 文件 | 覆盖范围 | 为什么读 |
|------|------|----------|----------|
| 1 | `decision_log.md` | 全部 | 议题 → 各文档结论 → 当前裁决与落点。改流程从这里开始 |
| 2 | `analysis_omni_usage_2026_07.md` | omni_usage，t041–t121，80MB 会话日志 | 运行态实证：审阅信噪比、context 溢出、TDD 违规。根因分析质量最高 |
| 3 | `analysis_omni_gate_gaps_2026_07.md` | omni_media，150 归档 task 审计 | 门禁缺口：typecheck:test/build/lint 未全覆盖的实证与推荐 |
| 4 | `retro_t041_t061.md` | omni_media，21 task / 31 commit | merge 冲突、审阅无差别浪费、plan 实际弃用 |
| 5 | `workflow_feedback.md` | omni_media，理论层 | 「按读者切 spec/plan」的推导；后半优于前半 |
| 6 | `incident_t071_worktree_loss.md` | 单次事故 | branch ≠ worktree，未提交改动丢失 |
| 7 | `retro_t001_t007.md` | 首跑 MVP | finding 分类表（真 bug 28% / 噪音 36%）是量化噪音的原始证据 |
| 8 | `archive/` | 已实施或过时的方案 | 追溯背景时才读 |

## 命名

| 前缀 | 文体 |
|------|------|
| `analysis_*` | 会话或日志实证分析 |
| `plan_*` | 待实施方案 |
| `retro_*` | 按 task 区间的定期复盘 |
| `incident_*` | 单次事故复盘 |
| `review/{author}.md` | 对本目录的多方审阅意见 |
| `decision_log.md` | 跨文档裁决总账（唯一权威状态表） |

## 纪律

- 同一议题的裁决状态只在 `decision_log.md` 维护；复盘正文不重复列「待决策」清单。
- 结论被后续文档修正时，在原文段落首行标注被哪份文档发展，不静默保留多版本。
- 复盘不再新增未闭环的建议清单。新发现走 `decision_log.md` 加行。
