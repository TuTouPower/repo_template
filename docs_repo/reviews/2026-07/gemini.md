# docs_repo 综合审阅报告与评估意见

**审阅日期**：2026-07-27（UTC+8）  
**审阅范围**：[docs_repo/](file:///home/karon/karson_ubuntu/repo_template/docs_repo) 下所有文件（包含 6 份主文档与 `archive/` 历史文档）  
**审阅者**：Antigravity AI (Gemini 3.6 Flash)

> **文件名勘误（2026-08-12 标注）**：本审阅正文保留审阅时点文件名，此后已改名：`11111.md` → 已删除，内容并入 `decision_log.md`；`workflow_feedback.md` → `workflow_reflection_1.md`；`workflow_record.md` → `workflow_reflection_2.md`；`workflow_retrospective_0.md` → `workflow_reflection_3.md`；`workflow_retrospective.md` → `workflow_reflection_4.md`；`workflow_session_analysis_2026-07.md` → `workflow_reflection_5.md`。`archive/workflow_skill_split_proposal.md` 未改名。

---

## 一、 文件清单与核心内容梳理

`docs_repo/` 记录了项目模板及其工作流在实战（如 `omni_media`、`omni_usage` 项目）中的多维演进、复盘与反思。主要文件结构如下：

1. **[docs_repo/11111.md](file:///home/karon/karson_ubuntu/repo_template/docs_repo/11111.md)**：包含遗留记录与 bugs.md 结合点、禁止进入 plan mode 的指令、以及关于并发 task 在 `tasks_index.json` 冲突的简短随笔。
2. **[docs_repo/workflow_feedback.md](file:///home/karon/karson_ubuntu/repo_template/docs_repo/workflow_feedback.md)**：基于 `omni_media`（t041-t055）实战的理论层面反思，指出 spec/plan 字段重叠、审阅形式主义、索引同步高开销、依靠 git 分支导致的隔离缺失等结构性缺陷。
3. **[docs_repo/workflow_record.md](file:///home/karon/karson_ubuntu/repo_template/docs_repo/workflow_record.md)**：对 t071 事故的专门分析，揭示了“创建 Git 分支 ≠ Git worktree 隔离”的问题，导致未提交代码在共享工作区中因 `git switch/reset` 被抹除。
4. **[docs_repo/workflow_retrospective.md](file:///home/karon/karson_ubuntu/repo_template/docs_repo/workflow_retrospective.md)**：基于 `omni_media` 21 个 task 的实战总结，聚焦多分支 merge 冲突（`tasks_index.json` 灾难）、审阅无差别浪费、plan.md 实际弃用、手写脚本破坏缩进等现实痛点。
5. **[docs_repo/workflow_retrospective_0.md](file:///home/karon/karson_ubuntu/repo_template/docs_repo/workflow_retrospective_0.md)**：针对早早期 MVP（t001-t007）的实跑复盘，暴露了 spec 过早写死技术选型、审阅在简单 task 上过重、无单测框架导致 smoke 膨胀、横向系统性缺口未独立开 task 根治等根本根因。
6. **[docs_repo/workflow_session_analysis_2026-07.md](file:///home/karon/karson_summary/repo_template/docs_repo/workflow_session_analysis_2026-07.md)**：针对 `omni_usage` 80MB+ 会话日志的深入实证分析，揭示了审阅 PASS 率低（29%-30%）、`/goal` hook 导致 context 溢出、TDD 顺序违规、503 错误缺少 blocked 出口、Electron ABI 脚本踩坑等运行态实证问题。
7. **[docs_repo/archive/workflow_skill_split_proposal.md](file:///home/karon/karson_ubuntu/repo_template/docs_repo/archive/workflow_skill_split_proposal.md)**：关于将 `AGENTS.md` 规则/状态机与 `/task_create`、`/task_run` 等按需 skill 拆分的候选设计方案。

---

## 二、 总体评价

`docs_repo/` 中的文档记录了极其宝贵且真实的 AI pair-programming 实践总结。它不仅有理论推演，更有基于数十个真实 Task、数万行代码及兆字节级会话日志的定量分析。

### 突出亮点
1. **实证导向**：拒绝凭空想象，所有结论均由具体的错误日志、Git reflog、会话 Token 占用及审阅 Pass 率支撑。
2. **根因剖析深入**：例如在分析“审阅 Pass 率仅 29%”时，没有盲目归咎于“模型不够聪明”或“审查太严格”，而是精确指出了**Prompt 角色框架的过度报倾向、信息不对称（缺上下文）、以及 Finding 缺少 AC 锚点导致判定无界**的深层原因。
3. **闭环意识强**：对每次事故（如 t071 未提交代码丢失、t057 批量缩进破坏测试）都及时撰写复盘，给出了可操作的防护与改进手段。

---

## 三、 审阅意见与改进建议

### 1. 结构整理与归档（解决文件游离问题）
* **[11111.md](file:///home/karon/karson_ubuntu/repo_template/docs_repo/11111.md)** 属于临时草稿/随笔性质，文件名不规范。建议将其中的有效意见（如关于统一 task 服务的设想）整理吸收至 `workflow_feedback.md` 或 `AGENTS.md` 的待办项中，然后归档或删除。
* **复盘系列文档合并/建立索引**：`workflow_retrospective_0.md`（t001-t007）、`workflow_retrospective.md`（t041-t061）与 `workflow_session_analysis_2026-07.md`（t041-t121）呈现出明显的迭代进化轨迹。建议在 `docs_repo/README.md` 中建立统一索引，明确各复盘文档的时序、侧重点与最终沉淀出的规则映射。

### 2. 工作流重构核心推论认可与补充

#### (1) Git Worktree 与隔离机制（针对 `workflow_record.md`）
* **认可**：在 AI 并行开发或中途频繁切换任务的场景下，单纯 `git branch` 无法保护工作区未提交文件。
* **补充建议**：在 `task.py start` 中直接集成 Worktree 创建逻辑；或者采纳“不再创建分支，统一在 main 上单 task 串行/Worktree 隔离”的方案，避免手工 switch 导致未提交改动丢失。

#### (2) 状态文件合并冲突（针对 `workflow_retrospective.md`）
* **认可**：`tasks_index.json` 被多分支同时修改必定导致 Git 冲突与状态丢失。
* **补充建议**：极力推荐 **Derived Data 方案**（把 Task 状态写在各个 Task 的 `task.md` / `spec.md` front-matter 中，`tasks_index.json` 仅作为只读编译产物/本地索引），彻底消除分布式多分支下的中心索引写入冲突。

#### (3) 重新定义 Spec 与 Plan 的边界（针对 `workflow_feedback.md` & `session_analysis`）
* **认可**：Spec 应作为**对外契约与验收标准**，只关注 WHAT；Plan 应作为**实施者的设计/技术细节笔记**，只关注 HOW。如果 Task 简单，Plan 完全可省。
* **补充建议**：在 Spec 中显式增加「未知契约清单」与「可测试性/不测试分支声明」，使 Reviewer 在审查时拥有完整的决策上下文，大幅降低无界 Finding 和因信息不对称导致的“撤回/遗留”。

#### (4) Review 机制分级与 Prompt 治理（针对 `workflow_session_analysis_2026-07.md`）
* **认可**：审阅无差别派发是巨大的 Token 浪费，且首轮 FAIL 率过高（~70%）主因是判定无界。
* **补充建议**：
  1. **分级审查**：根据 Task 风险（`full` / `single` / `none`）智能选择派发 Reviewer 数量。
  2. **Prompt 改造**：硬性要求 Reviewer Finding 必须挂钩 Spec 里的 AC（验收标准）或 Critical/Important 级别缺陷，禁止输出“建议加测”类 Non-blocking 判定。

---

## 四、 总结

`docs_repo/` 是一份极其优秀的工作流演进档案。建议后续按照上述分析，将复盘中已被多次验证的 P0/P1 改进项（如 Worktree 隔离、Spec/Plan 职责重划分、Derived Index 索引、审阅分级 Prompt 治理）正式吸收落盘到主仓库的 [AGENTS.md](file:///home/karon/karson_ubuntu/repo_template/AGENTS.md) 及 `.agents/skills/` 体系中。
