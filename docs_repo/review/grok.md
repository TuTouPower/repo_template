# docs_repo 审阅意见（Grok）

- **范围**：`docs_repo/` 下全部文件（含 `archive/`）
- **对照基线**：当前 `repo_template` 的 `AGENTS.md` / `CLAUDE.md`、`.agents/skills/*`、`docs/tasks/task_template/*`、`docs/reviews/prompts/*`
- **日期**：2026-07-27
- **目的**：判断这批笔记对模板演进的价值、哪些结论已落地、哪些仍是开放债、建议优先动哪几刀

---

## 1. 总评

`docs_repo` 不是业务文档，是**流程实战证据库**：从 t001 初跑到 omni_media 21 task、再到 omni_usage 80MB+ 会话日志，证据密度高、因果链清楚。对「私人 agent + 强流程模板」这类仓库，这是罕见的一手材料。

**优点**

- 多数结论有**条数 / 轮次 / 会话 id / 用户原话**，不是感想文。
- 三份主报告（`workflow_feedback` / `workflow_retrospective` / `workflow_session_analysis`）分层清楚：理论 → 实战 → 运行态。
- 已有部分结论反哺进模板（spec 不写版本号、AC 引用不复制、`max_review_round=4`、skill 拆分、技术约束不判 blocking、follow-up 字段等）。

**问题**

- **建议与落地状态未标注**：读者难分「已改」「仍缺」「已否决」。
- **重复度高**：审阅分级、plan 无用、一 task 一 commit、JSON merge 冲突在多份文件各写一遍，无索引表。
- **彼此冲突未裁决**：例如「直接 main 做」vs「必须 worktree 隔离」；「plan 承载设计细节」vs「plan 可省 / 测试策略进 spec」。
- **`11111.md`、`workflow_record.md` 形态不一致**：一个是碎片备忘，一个是事故复盘草稿，缺元信息与入库标准。

**一句话**：证据强于方案；方案强于落地跟踪。下一刀应是「裁决冲突 + 标已落地 + 只推 3 个 P0」，而不是再写第 N 份反思。

---

## 2. 文件清单与角色

| 文件 | 角色 | 质量 | 与当前模板关系 |
|------|------|------|----------------|
| `workflow_retrospective_0.md` | t001–t007 初跑复盘（痛点 1–7 + 横向缺口根因） | 高：例子具体、ROI 表清晰 | 部分已吸收（AC 行为化、round 默认 4）；横向开 task / doctor / 审阅分级未制度化 |
| `workflow_retrospective.md` | t041–t061 实战（merge 灾难、跳过审阅、plan 无用） | 高：P0=merge 冲突判断准确 | 分支策略仍默认 task branch；worktree 仅在 `tasks-parallel` 提示 |
| `workflow_feedback.md` | 结构假设 + plan/spec/粒度/审阅/索引 设计债 | 高；后半「按读者切」是升级版 | plan 模板仍旧；无 risk/review_level；无 depends_on |
| `workflow_session_analysis_2026-07.md` | omni_usage 会话实证（信噪比、/goal、TDD 违规） | **最高价值**：P0 根因写到 prompt 层 | Pre-Report Gate 已有雏形；**AC 硬边界 / 决策上下文注入 / infra blocked / 旧测只删不改**仍弱或缺失 |
| `workflow_record.md` | t071 未提交丢失事故（branch ≠ worktree） | 中高：事实清楚；格式偏对话草稿 | 未写成 skill 硬步骤；与 retrospective「直接 main」冲突未消解 |
| `archive/workflow_skill_split_proposal.md` | AGENTS 与 skill 拆分方案 | 高且**基本已实现** | 现状 skill 表比提案更细（create/run/bug/debt/merge/parallel/preflight/hygiene/clean）；archive 合理 |
| `11111.md` | 三条碎片备忘 | 低：无日期、无上下文 | 内容仍有效（遗留清单、禁 plan mode、并发索引冲突）但应并入正式笔记 |

---

## 3. 已落地 vs 仍开放（对照模板）

### 3.1 已吸收（不必再论证）

| 历史建议 | 当前落点 |
|----------|----------|
| skill 从 AGENTS 拆出 | `.agents/skills/*` + CLAUDE 路由表；`disable-model-invocation` |
| 创建 / 执行边界 | `task-create` vs `tasks-run` |
| AC 不写版本号/库/目录 | `task_template/spec.md` + code_prompt 技术约束判断 |
| AC 唯一源，task 引用不复制 | `task_template/task.md` 收尾 |
| max_review_round 抬高 | 默认 **4**（原 2） |
| 横向缺口 → follow-up task 字段 | review 输出「系统性 follow-up」；`task-debt` skill |
| minor 不阻断 | share_prompt + task 处置语义 |
| 纯文档 task 0 finding 合法 | test_prompt |
| 红灯归因 / 危险模式扫描 | test_prompt 较完整 |
| Pre-Report Gate | share_prompt（质量门雏形） |

### 3.2 仍开放（高价值未落地）

| 议题 | 来源 | 现状缺口 | 建议优先级 |
|------|------|----------|------------|
| 审阅 finding **AC 硬边界**（只报阻塞级） | session §1 | Gate 有，但无「无界覆盖建议 → non-blocking」硬规则；无撤回率监控 | **P0** |
| review prompt **注入决策上下文 / 有意不测清单** | feedback 补充 + session §1b | `render_review_prompts`  predominantly 仍以 spec 为中心；plan 上下文未强制进 reviewer | **P0** |
| **branch ≠ worktree** 隔离 | record + retrospective merge | `tasks-run` 未强制 worktree；并行只「提示」不创建 | **P0**（并发/多会话场景） |
| `tasks_index.json` **跨分支 merge 冲突** | retrospective §1 + 11111 | 仍 JSON 权威 + 每分支改 index；无 main-only / derived / 服务端方案 | **P0**（若保留多分支） |
| **审阅分级**（full/single/none） | feedback + retrospective | 仍「固定审阅」；文档/格式 task 成本未制度化 | **P1** |
| plan 按读者/复杂度 | feedback | 模板仍「步骤+风险+blueprint」；与实施脱节 | **P1** |
| TDD：**旧绿测只删不改** | session §3 | test_prompt 有红灯归因，**无「禁止就地改预期」硬句** | **P1** |
| blocked 原因 **infra**（503 等） | session §4 | blocked 表只有 blackbox / review | **P1** |
| 未知契约清单 + 假设审计 | session §5 | spec 模板无此字段 | **P1** |
| depends_on / 依赖图 | feedback D | 依赖仍散落 spec | **P2** |
| 遗留待办与 bugs 统一登记 | 11111 | bugs.md 只管未修 bug；finding 遗留靠 task-debt 捞 | **P2** |
| 一 task 一 commit → 一主题 N commit | feedback B / retrospective §7 | 规则仍「执行期一个 task 一个 commit」 | **待裁决**（见下） |

### 3.3 未裁决冲突（必须先拍板再改代码）

1. **隔离策略**  
   - retrospective：推荐 **不切分支、直接 main**（方案 C）  
   - record：必须 **独立 worktree**，仅 branch 不够  
   - 11111 / parallel：并发会撞 `tasks_index.json`  
   **意见**：两者回答不同问题。  
   - 单会话串行 → main 直做 **可行**，且最省 merge 税。  
   - 多会话 / 并行 / 长未提交窗口 → **必须 worktree**（或至少 WIP commit）。  
   模板应写成：**默认 main 串行；一旦可能共享目录被切走，强制 worktree；禁止「有 branch 无 worktree 且长期 uncommitted」**。不要二选一写成全局教条。

2. **plan 的命运**  
   - feedback A：plan 分三套、承载设计细节  
   - feedback 补充：按读者切，测试策略进 **spec**，plan 可省  
   - retrospective：实战 plan 几乎未读  
   **意见**：采纳「按读者」——spec = 契约+可测试性+有意不测；plan = 可选实施笔记。**不要**再做三套 plan 模板（维护成本 > 收益）。简单 task 无 plan 合法。

3. **一 task 一 commit**  
   实战已反复打破。  
   **意见**：保留「一个 task 一个可 review 的交付单元」，commit 数放开为 **≥1 个原子 commit**，收尾仍 `finish` 一次。若坚持单 commit，则必须允许 WIP commit 或 worktree，否则 record 类事故会重演。

4. **索引权威**  
   - feedback E：archive-only 或放开手改  
   - 当前：`task.py` 唯一写 JSON  
   **意见**：唯一写权保留（防手贱），但 **状态落点改为 per-task 文件（front matter）+ 派生 index**，从根上消 merge 冲突。这比「禁止并行」或「手改 JSON」都干净。

---

## 4. 分文件意见

### 4.1 `workflow_retrospective_0.md`（t001–t007）

**价值**：定义了早期噪音分类（真 bug 28% vs 噪音 36%）和「横向缺口不拆 task」元根因。#0 建议（发现系统性缺口立即 backlog）仍是最高 ROI 流程规则之一。

**挑剔**

- 部分建议已被模板部分消化（spec 行为化、round 抬高），正文未标「已吸收」，读起来像未决清单。
- 「vitest / env_doctor」是**业务项目**债，不是模板债；模板侧应落「何时开基础设施 task」规则，而不是写死框架名。
- 痛点 5 的工程偏离（round 内修完不 blocked）与当前「禁止同轮临时修复后翻 PASS」**正面对撞**——模板选了更严路径。应在复盘里补一句：后续默认 4 轮 + 同轮禁翻 PASS 是有意选择，偏离需用户加轮。

**建议动作**：保留为历史证据；在文首加「状态：部分吸收」小节，链到 AGENTS 相关标题。

### 4.2 `workflow_retrospective.md`（t041–t061）

**价值**：把 merge 冲突坐实为**最大结构性税**；用 11 分支 vs main 直做对照，说服力强。审阅对文档/格式浪费与 max_round 不够，和 session 分析互相印证。

**挑剔**

- 方案 C「全部 main」忽略后续 record 事故与并行 skill 存在——结论过满。
- t057–t061 缩进事故是**工具选择错误**（手写脚本 vs prettier），可归入 conventions「禁止手写批量变换」，不必占流程主叙事。
- 「agent 可验证 vs [deploy] AC」很好，**尚未进 spec 模板**，应晋升。

**建议动作**：P0 吸收「状态存储与分支正交」设计；[deploy] AC 前缀进模板。

### 4.3 `workflow_feedback.md`

**价值**：唯一把「工作流核心假设」摊开检验的文档；§「按读者重划 spec/plan」比早期 plan 三套模板更正确。

**挑剔**

- 待决策 6 项全开，无推荐默认 → 容易永远停在「再讨论」。
- 改进 E「允许 agent 直接改 index」与硬约束冲突且会放大并发损坏，**应否决**。
- 粒度 B「review 按 commit」在无 PR 流程仓库成本过高，更现实是 **task 级总审 + 危险模式扫描**。

**建议动作**：把「按读者切」写成正式 ADR / conventions；关闭 E；B 采用「一主题多 commit、task 级 review」。

### 4.4 `workflow_session_analysis_2026-07.md`（最应优先读）

**价值**：唯一把审阅灾难归因到 **prompt 无界 + 上下文不对称**，并明确排除「换模型」幻想。数据（PASS 29–30%、遗留 761）足够支撑 P0 改造。

**挑剔**

- `/goal` hook、Electron ABI 是**宿主/项目特有**，不应原样写进通用模板；应抽象为：  
  - 单会话 task 上限 / finish 后停  
  - 原生模块/平台陷阱 → `known_pitfalls` 或 doctor  
- 「撤回率 >30% 强制复盘提示词」好，但缺脚本钩子设计，易成空话。
- subagent 回显改文件路径：与当前 render 脚本方向一致，应明确为工程任务而非口号。

**建议动作**：以本文 P0 三条驱动模板下一轮改动；宿主特有条目迁出或标「非模板」。

### 4.5 `workflow_record.md`（t071 丢失）

**价值**：把「我建了分支所以安全」证伪；责任边界清楚。

**挑剔**

- 格式是对话回复，无标题层级 / 日期 / 关联 tid 索引，入库形态差。
- 未写成可执行检查清单（`tasks-run` Step 1 应有的 3–5 条命令级门禁）。
- 未回答：若坚持一 task 一 commit，WIP 是否允许 stash / 临时 commit？

**建议动作**：改写成 `docs_repo` 标准事故卡（现象 / 根因 / 防护 / 是否已进 skill），并推动 `tasks-run` Step 1：

1. `git status` 干净或仅本 task 文件  
2. 当前 branch 匹配 tid  
3. 非独占仓库 → 创建 worktree 再改代码  
4. 禁止在共享目录长期 uncommitted 实现

### 4.6 `archive/workflow_skill_split_proposal.md`

**价值**：拆分原则「AGENTS 定必须遵守什么，skill 定怎么做」正确且已基本落地。

**挑剔**

- 提案只 foresaw create/run 两个 skill；现状 skill 面更全——说明演进健康。
- archive 后未在 active 笔记写「已实施偏差表」（例如串行队列、禁止模型自触发 skill）。

**建议动作**：保持 archive；在 `docs_repo` 根 README 或索引写一句「skill 拆分已落地，以 CLAUDE 路由表为准」。

### 4.7 `11111.md`

**价值**：三条都仍真——遗留总账、禁无 plan mode 空转、并发撞 index。

**问题**：文件名无语义、无结构、易丢。

**建议动作**：删除或并入正式笔记：

- 遗留总账 → 产品决策：扩展 `bugs.md` 或新建 `docs/followups.md`（仅未关闭项）  
- plan mode 禁令 → 已在用户习惯/agent 规则，不必单独文件  
- 统一 task 服务 → 记入「未决：index 并发」与 P0 状态存储改造绑定  

---

## 5. 对整库结构的意见

### 5.1 缺索引

`docs_repo` 无 README / 时间线 / 「问题 → 文件 → 是否落地」。建议最少：

```text
docs_repo/
  README.md          # 阅读顺序 + 落地状态表
  review/            # 多模型/多作者审阅（本文件）
  archive/           # 已实施或过时方案
  workflow_*.md      # 保留主证据
```

### 5.2 命名与生命周期

- `workflow_retrospective_0` vs `workflow_retrospective`：编号语义不明，建议改 `..._t001_t007` / `..._t041_t061` 或保留并在标题写清范围。  
- `11111.md`：应消亡。  
- `review/`：适合放对照审阅；约定命名 `{author}.md` 或 `{date}_{author}.md`。

### 5.3 与 CLAUDE「docs_repo 可删」的关系

CLAUDE 写新项目可删 `docs_repo`。正确——这些是**模板维护者**笔记，不应进业务 clone 的必读路径。  
但模板仓自身应把**已裁决结论**搬进 `AGENTS` / skill / prompt / conventions，而不是让维护者每次重读 1200+ 行复盘。

---

## 6. 建议的落地优先级（仅模板仓）

面向 `repo_template` 本身，而不是 omni_* 业务仓：

### P0（直接减事故 / 减噪音）

1. **Reviewer 有界**  
   - share/code/test prompt：blocking finding 必须锚定 AC 或可观测行为缺陷；「可以再测更细」→ 备注或 minor，且默认不撑 FAIL。  
   - `render_review_prompts`：注入 spec 上下文区 + 「有意不测」清单（无则写「无」）。  
2. **工作区隔离写进 `tasks-run` Step 1**  
   - status 检查 + 共享目录风险 → worktree 或拒绝开干。  
3. **状态存储消 merge 冲突**  
   - 设计：task 状态写 `task.md` front matter（或 per-task json）；`tasks_index.json` 由 `task.py list --rebuild` 派生；finish 在 merge 到集成线后于主线执行，或仅主线 touch 派生 index。

### P1（降成本）

4. `review_level: full|single|none`（或 risk）写进 spec front matter；`tasks-run` 分支流程。  
5. spec 增加：未知契约清单、可测试性 / `[deploy]` AC、有意不测。  
6. TDD 硬句：旧绿测只删不改预期；改测必须新红测覆盖新语义。  
7. blocked 增加 `--reason infra`。

### P2

8. `depends_on` 进 index 或 task front matter。  
9. 遗留统一登记（bugs 或 followups）。  
10. `known_pitfalls.md` 模板（可选）。

### 明确不建议

- 放开 agent 手改 `tasks_index.json`  
- 为 plan 维护三套永久模板  
- 把 `/goal`、Electron ABI 等宿主细节写进通用 AGENTS  
- 用「换 reviewer 模型」当审阅信噪比主修复  

---

## 7. 文档本身的写法评价

| 维度 | 评价 |
|------|------|
| 实证性 | 强；session 分析可作范本 |
| 可执行性 | 中；动作表有，缺「已落地 / 负责人 / 验收」 |
| 去重 | 弱；审阅分级等主题 ≥3 处全文重述 |
| 冲突处理 | 弱；main vs worktree、plan 归属未收敛 |
| 对模板可迁移性 | 中；业务细节与流程原则混写 |
| 可读路径 | 弱；无推荐阅读顺序 |

**推荐阅读顺序（后人）**

1. `workflow_session_analysis_2026-07.md`（运行态 P0）  
2. `workflow_retrospective.md`（merge / 分级）  
3. `workflow_feedback.md` 后半「按读者切」（文档模型）  
4. `workflow_record.md`（隔离事故）  
5. `workflow_retrospective_0.md`（历史噪音分类，可选）  
6. `archive/workflow_skill_split_proposal.md`（已落地背景，可选）

---

## 8. 结论

`docs_repo` 完成了它该做的事：**用真实 task 与会话，把流程税和事故钉死在纸上**。模板已经吃掉 skill 拆分、AC 行为化、round 默认 4、部分 review 纪律；**没吃掉的是更贵的三块——有界审阅、工作区隔离、索引/状态的 git 友好存储**。

再写复盘的边际收益已低。维护动作应转为：

1. 裁决 §3.3 四个冲突；  
2. 执行 §6 的 P0；  
3. 给 `docs_repo` 加索引与「落地状态」列；  
4. 消化 `11111.md`，避免碎片继续增生。

**对这批文档的采用态度**：当**证据与问题清单**保留；当**未裁决设计草案**时，以本审阅 §3.3 / §6 为收敛意见，而不是并行维护多套互相打架的「推荐方案」。
