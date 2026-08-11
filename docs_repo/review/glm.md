# docs_repo 全量审阅意见（GLM）

- **范围**：`docs_repo/` 下全部文件（含 `archive/` 与 `review/`）
- **已读对照**：`review/gemini.md`、`review/grok.md`、`review/k3.md`——本文不重复三者已建立的共识，仅在其基础上补充分歧、被忽略的盲点，以及更具体的落地形态。
- **日期**：2026-07-27
- **立场**：以"工程落地"为优先——审阅的对象是工作流，而工作流的目标是可重复执行，不是自洽的论文。

---

## 0. 与已有三份审阅的关系

|已有审阅|做对了什么|我补充什么|
|------|------|------|
|gemini|概括了证据强度与主结论，结构清晰|末尾的"落地到 AGENTS"建议过简，未指出落地时的操作冲突；对 `11111.md` 与 `workflow_record.md` 的改造停留在"归档/整理"层，不够|
|grok|"证据强于方案、方案强于落地跟踪"的核心判断准确；§3.3 的冲突裁决到位|§6 的 P0/P1 行动项与 CLAUDE.md 现行字段未做逐条映射，可执行性仍偏弱；"裁决 §3.3"未给出裁决格式|
|k3|跨文档总账意识最强；"缺的是总账而非复盘"这一元判断最锋利|提出了 `decision_log.md` 但没定义它；"建议落地率存疑"是断言但未做盘点|

本文做三件事：(1) 一张落地状态总账，终结"已改/仍缺/已否决"三态不明；(2) 一组裁决表，收敛 grok §3.3 的四个冲突；(3) 一组被三份审阅漏掉但应该单独提出的文档级与流程级问题。

---

## 1. 一张落地状态总账（回答 k3 提出但未建立的缺口）

> 表格只列**被两份以上复盘诊断过**的议题。单文档独有意见不进总账，避免再增噪。

|#|议题|出处（≥2 份）|当前模板状态|建议裁决|
|------|------|------|------|------|
|L1|`tasks_index.json` 多分支 merge 冲突|feedback §4/E、retrospective §1、11111#3、session §12|JSON 仍是唯一权威；task.py 仍是唯一写者|**P0**：状态写 per-task front matter，index 改 derived；见 §3.1|
|L2|审阅 finding 无界 / 信噪比低|session §1、feedback §3/C、retrospective §2、retrospective_0 痛点 3|share_prompt 有 Pre-Report Gate 雏形，无 AC 硬阈值；撤回率无监控|**P0**：reviewer finding 必须锚 AC 或行为级缺陷，"建议加测"降 non-blocking；见 §3.2|
|L3|reviewer 缺决策上下文 → 撤回率堆积|session §1b、feedback 再评估节|render_review_prompts 以 spec 为中心；plan 的"有意不测"未强制进 reviewer prompt|**P0**：review prompt 注入「有意不测清单」与「未知契约清单」；无则显式写"无"|
|L4|branch ≠ worktree，未提交丢失|record、retrospective §1、session §11|tasks-run 未强制 worktree；tasks-parallel 仅提示|**P0**：tasks-run Step 1 加工作区门禁；见 §3.3|
|L5|审阅对所有 task 无差别（文档/格式也审阅）|feedback §3/C、retrospective §2、retrospective_0 痛点 3|仍固定审阅|**P1**：`review_level: full\|single\|none` 进 spec front matter|
|L6|plan.md 实际弃用 / 退化为 spec 副本|feedback §1+A、retrospective §4、session §1b|模板仍是「步骤+风险+blueprint」，未按读者切|**P1**：按读者重划——spec 吸收契约区+上下文区；plan 降为可选实施笔记；见 §3.4|
|L7|spec 写死技术选型 → 过时 → FAIL 循环|retrospective_0 痛点 1、feedback §1|spec 模板已约束"不写版本号/库/目录"；code_prompt 技术约束不判 blocking|**已吸收**；补一条：发现 spec 技术字段漂移时改 spec 不计 FAIL|
|L8|max_review_round 不够 / 语义模糊|retrospective §3、retrospective_0 痛点 5、session §1|默认已抬到 4；round 语义仍混（出场次数 vs 闭环次数）|**P1**：round 定义为"同一批 finding 的回归轮次"；新 finding 不强制 N+1|
|L9|横向缺口只标遗留、不开 task|retrospective_0 根因节、feedback、session §13、11111#1|review 输出"系统性 follow-up"；task-debt skill 已有|**已吸收**；强化：遗留 finding 必须映射到 tid，禁止悬空|
|L10|AC 三处维护（spec / task.md 勾选 / 处置表）|retrospective_0 痛点 7|task_template 已约束"引用不复制"|**已吸收**|
|L11|TDD 顺序违规（改测试适配实现）|session §3|test_prompt 有红灯归因，**无"旧绿测只删不改预期"硬句**|**P1**：加硬句；见 §3.5|
|L12|blocked 原因表只覆盖 blackbox/review|session §4|blocked 表无 infra 路径|**P1**：加 `--reason infra`|
|L13|spec 脑补外部契约|session §5|spec 模板无「未知契约清单」|**P1**：spec 增必填项；见 §3.4|
|L14|commit 粒度"一 task 一 commit"与实战脱节|feedback B、retrospective §7|仍写"执行期一个 task 一个 commit"|**待裁决**；见 §3.6|
|L15|bug 调研后不开 task、只口头|session §6|无硬约束|**P2**：CLAUDE.md 加"只读调研完必须追加 bugs.md + 提议 task"|
|L16|任务建完又删|session §7|无 add 后确认门|**P2**：`task.py add` 后列影响文件，等确认才进 Step 1|

共 16 条。**已吸收 3 条**（L7/L9/L10），**P0 共 4 条**（L1-L4），**P1 共 7 条**，**P2 共 2 条**，**待裁决 1 条**。这就是四份复盘 1200+ 行换来的全部共识，可以收敛。

---

## 2. 三份审阅漏掉的问题

### 2.1 `workflow_retrospective_0.md` 的 finding 分类表（§数据概览）是元资产，未被任何审阅提升为流程改造

retrospective_0 把 39 条 finding 分成五类（真 bug 28% / spec 过时 10% / 重复模式 10% / nitpick 15% / 测试缺口 36%）。k3 正确指出"这个分类方法应沉淀为 review 报告标准字段"，但三份审阅都没提下一步：

**建议**：在 `review_code.md` / `review_test.md` 的 finding 表强制加 `category: bug\|spec_drift\|duplicate\|nitpick\|coverage_gap` 列。这样：
- 撤回率与噪音率可由脚本实时统计，不必等复盘人工分类
- `spec_drift` 类 finding 处置 = 改 spec 不计 FAIL，直接消除 retrospective_0 痛点 1 的循环
- `duplicate` 类可自动与 follow-up tid 联动

这一条的 ROI 高于 L2 的 prompt 改造——因为 prompt 改造是在"怎么报"层，category 是在"报完怎么用"层，后者驱动前者。

### 2.2 `workflow_record.md` 的结论只覆盖了事故的一半——三份审阅都指出了，但没指出更刺眼的那半

三份审阅都提到"流程层应有工作区门禁"。但漏了另一条：**事故报告本身的完成度问题**。

record §4"影响范围"列出"t071 状态：active；docs/tasks_index.json 存在 t102 冲突，不能安全确认或改写"——这意味着事故发生时，**task.py 的状态机本身已不可用**。这不只是"agent 应建 worktree"的个体失误，是**状态文件被污染后整个 task.py 退出工作**的流程级故障。恢复顺序（§5）把 task.py 状态修复排到最后，但事故期间 agent 的任何 `task.py show` / `list` 都可能读到错误状态并据此决策。

**建议**：tasks-run Step 1 除工作区门禁外，加一条 **task.py 状态自检**——`task.py list` 输出与 `docs/tasks/` 实际目录、git 分支三者交叉校验，不一致时拒绝开干。这条比 worktree 门禁更基础。

### 2.3 `workflow_session_analysis_2026-07.md` §1 的建议被三份审阅一致接受，但其数据样本存偏置

session 分析的 PASS 率 29-30%、遗留 761 条，全部来自 omni_usage 一个项目、opus 一个模型、4 天会话。三份审阅都基于此判 P0，但：

- omni_usage 是 Electron + 原生模块项目，测试基础设施弱（无单测层，全靠 smoke/e2e）——这类项目的 finding 信噪比天然偏低
- 761 条遗留集中在 `87f4adb0` 一个会话的 8 个 task，若该会话恰好是 `/goal` hook 强制串行的尾部（context 接近 32MB 上限），reviewer 在极端 context 下的行为可能不代表正常态

**不是说结论错**——审阅无界是结构性问题，与项目无关。但审阅应标注：**PASS 率与遗留数来自单一项目样本，推广到全模板时按"方向正确、数值待校准"对待**。落地 L2 后应在下一个非 Electron 项目复测 PASS 率，确认改造效果。

### 2.4 `archive/workflow_skill_split_proposal.md` 的"实施结果"缺位——三份审阅都提了，但有一层更深的

k3 指出"方案从 2 skill 膨胀到 9 skill 无记录"。但更关键的是：**提案的判定标准第 4 条"两个 skill 不复制 AGENTS.md 完整规则"在膨胀到 9 个后是否仍成立？**

9 个 skill（tasks-run / task-create / task-bug / task-debt / tasks-merge / tasks-preflight / tasks-parallel / repo-hygiene / repo-clean）之间几乎一定存在规则重叠——例如"task 状态转换"在 tasks-run、tasks-merge、tasks-parallel 都会出现。如果每个 skill 各写一份，就回到了提案本想解决的"规则漂移"问题，只是从 AGENTS.md 一处漂移变成了 9 处。

**建议**：做一次 skill 间规则重叠审计（grep 同名硬约束在几个 skill 出现），若 >3 处重复，引入 skill 间的"base rules" include 机制或显式"本条以 AGENTS §X 为准"引用。这是防止下一次复盘发现"skill 体系自身成了新的同步税"的预防性动作。

---

## 3. P0/P1 的具体落地形态（回答 grok §6 与 k3 §跨文档总评的"怎么改"层）

### 3.1 L1：状态存储改为 per-task front matter + derived index

三份审阅一致认可方向，但没给迁移路径。分两步：

1. **写权下沉**：task.py `add`/`start`/`finish`/`drop` 时，在对应 `docs/tasks/{tid}_{slug}/task.md` 的 front matter 写入 `status`、`active_at`、`done_at`、`depends_on`、`review_level`、`risk`。每分支只改自己的 task.md，零冲突。
2. **index 派生**：`tasks_index.json` 改为 `task.py list --rebuild` 扫描所有 task.md 生成；main 上 merge 后跑一次 rebuild。分支不写 JSON。

迁移风险：现有 task.md 的 front matter 可能不规范。先跑一次 `task.py doctor --migrate-frontmatter` 把历史 task 状态补齐，再切换读写路径。

### 3.2 L2：reviewer finding 的 AC 硬阈值——不是改 prompt 那么简单

"reviewer 只报 AC 阻塞级"落地时有三个子问题，三份审阅都没展开：

- **AC 粒度问题**：spec 的 AC 如果本身写得粗（"系统正常工作"），reviewer 没法锚。先强制 AC 可观测化（每条 AC 对应一个可执行断言），再要求 finding 锚 AC——否则 AC 硬阈值会变成"finding 找不到锚就降级为 non-blocking"，等于变相放弃审查。
- **behavior bug 无 AC 锚怎么办**：reviewer 发现的真实 bug（如 session 分析提到的 `local-api /v1/secrets` 在 `check_auth` 之前）不一定对应任一 AC。硬阈值不能写成"必须锚 AC"，应写成"锚 AC 或可观测行为缺陷"——grok §6 措辞正确，但 gemini 和 k3 都简化为"锚 AC"。
- **加测类 finding 的归宿**：降为 non-blocking 后，它仍需一个落点。建议：自动进 `task-debt` 的候选清单，由用户在收尾时决定开 follow-up 还是显式拒绝。这样"建议加测"不被丢弃，只是不再撑 FAIL。

### 3.3 L4：tasks-run Step 1 的工作区门禁——具体四条

record 事故的根因是"branch ≠ worktree"。门禁写成可执行检查：

1. `git status --porcelain` 输出为空，或所有改动属于本 task 目录
2. `git branch --show-current` 与 `{tid}` 匹配（若用分支模式）
3. 若 `git status` 含未提交改动且不属于本 task → **拒绝开干，报告后等用户处置**
4. 若仓库被多会话共享（`tasks-parallel` 模式）→ 强制 `git worktree add` 到独立目录后再改代码

第 4 条是 record 事故的直接防御。第 3 条是 session §11 的建议。前两条是基础卫生。

### 3.4 L6+L13：spec 的契约区 / 上下文区二分——模板具体形态

feedback 再评估节提出的二分，落地为 spec.md 的显式分节：

```markdown
## 契约区（reviewer 判 AC 时只看本区）
- 范围 / 非范围
- 验收标准（可观测、每条对应一个断言）
- 可测试性声明（哪些 AC 不可单测、原因）

## 上下文区（reviewer 判测试覆盖时核对本区）
- 决策上下文（有意不测的分支、原因）
- 测试策略（mock 边界、fixture、断言目标）
- 未知契约清单（endpoint、API 形态、外部依赖，标 UNVERIFIED）
```

reviewer 提示词对应改两句：判 AC 只读契约区；判覆盖核对上下文区。这一刀同时解 L3（决策上下文进 reviewer）和 L6（plan 不再背上下文）。

### 3.5 L11：TDD 硬句的准确措辞

session §3 的建议"旧绿测只删不改"略粗。准确版：

> 实现变更导致旧测试语义失效时：
> - 禁止就地改旧测试的预期以适配新实现
> - 必须新增红测覆盖新语义
> - 旧绿测只允许删除（标注删除理由）或原样保留（若语义仍成立）
> - reviewer 对"改测"类操作必须复核：是否在断言预期而非现状

加最后一句是因为 session §3 的根因是"改测试适配实现"——reviewer 复核"改测"动作是直接防御。

### 3.6 L14：commit 粒度——建议保留"一 task 一可 review 交付单元"，放开 commit 数

feedback B 与 retrospective §7 都建议改"一主题 N commit"。但直接放开有风险：merge 时多个 commit 混入 main 历史，追溯单 task 变更时边界模糊。

折中：**保留"一 task 一交付单元"语义，commit 数 ≥1**。具体：

- task 内允许多个原子 commit（每个对应一个子改动）
- task.py finish 时打一个 `task-{tid}` 标签作为交付边界
- review 仍 task 级（最后一次总审）
- merge 到 main 用 `--no-ff` 保留 task 边界

这样既不违反"可 review 的交付单元"，又不强制单 commit。比 feedback B 的"一主题 N commit"语义更清楚——交付单元是 review 的锚，commit 是实施的原子。

---

## 4. 文档级意见（被三份审阅部分覆盖，这里收敛）

### 4.1 命名与分层

- `workflow_record.md` → 改名 `incident_t071_worktree_loss.md`，或建 `incidents/` 子目录（k3 已提，认可）
- `workflow_retrospective_0.md` 与 `workflow_retrospective.md` 编号语义不明 → 改 `retro_t001_t007.md` / `retro_t041_t061.md`（grok 已提，认可）
- `11111.md` → 分流后删除（三份审阅一致，认可）

### 4.2 docs_repo 缺索引——但不是任意索引

grok 建议 `README.md`，k3 建议 `decision_log.md` / `index.md`。两者目标不同：

- **README.md**：阅读顺序 + 文件角色（grok §5.1 已给雏形）
- **decision_log.md**：问题 → 各文档结论 → 当前裁决状态（k3 跨文档总评已给定义，但没给格式）

两者都需要，不冲突。decision_log 的最小格式：

```markdown
|议题|出处|方案|当前裁决|裁决依据|
|------|------|------|------|------|
|tasks_index 冲突|feedback §4/E, retro §1, 11111#3, session §12|main-only / front matter derived / 不切分支 / 统一服务|front matter + derived index（本审阅 §3.1）|消除多分支写冲突；唯一写权保留|
```

本审阅 §1 的总账就是这个 decision_log 的第一版。直接用它启动，不必再写第五份。

### 4.3 时效标注——feedback 的"再评估"模式应推广

feedback §"spec/plan 边界的再评估"用"本文 §X 已被 YYY 发展"标注演进，是全 repo 最好的元实践。但只用在了一处。建议：

- 所有被后续文档修正的结论，在原文段落首行加 `> 本文 §X 已被 [YYY] §Z 发展/否定，当前立场见 decision_log.md#Lxxx`
- 静默的多版本并存是 k3 指出的"分析充分、决策缺席"的文档层症状——标注是治本的最低成本动作

---

## 5. 一句话结论

**这批文档的诊断能力已经超过工作流本身——四份复盘 1200+ 行换来的共识其实只有 16 条（§1 总账），其中 3 条已吸收、4 条 P0、7 条 P1、2 条 P2、1 条待裁决。先落 §1 总账为 decision_log.md，再按 §3 的具体形态执行 P0 四条（per-task 状态 / reviewer AC 锚 / 工作区门禁 / spec 二分），其余自然归位。再写第五份复盘的边际收益已为负。**
