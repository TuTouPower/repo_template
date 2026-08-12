# Opus 审阅：docs_repo 全部复盘笔记

审阅范围：`workflow_feedback.md`（omni_media 理论）、`workflow_retrospective.md`（omni_media 实战 21 task）、`workflow_retrospective_0.md`（omni_media t001-t007）、`workflow_session_analysis_2026-07.md`（omni_usage 会话实证 80MB 日志）、`workflow_record.md`（worktree 事故）、`11111.md`（遗留备忘）、`archive/workflow_skill_split_proposal.md`（skill 拆分方案）。

> **文件名勘误（2026-08-12 标注）**：本审阅正文保留审阅时点文件名，此后已改名：`11111.md` → 已删除，内容并入 `decision_log.md`；`workflow_feedback.md` → `workflow_reflection_1.md`；`workflow_record.md` → `workflow_reflection_2.md`；`workflow_retrospective_0.md` → `workflow_reflection_3.md`；`workflow_retrospective.md` → `workflow_reflection_4.md`；`workflow_session_analysis_2026-07.md` → `workflow_reflection_5.md`。`archive/workflow_skill_split_proposal.md` 未改名。

现状基线：AGENTS.md + 9 个 skill + task.py + review prompt 模板 + task_template，skill 拆分提案已落地。以下按主题对照笔记结论与当前状态。

---

## 1. 元观察：四份笔记说的同一件事

四份文档独立得出同一结构判断，可信度很高：

> **当前工作流偏向"防止 agent 犯错"，对"agent 的执行效率"关注不够。**

- workflow_feedback §一个根本性的观察：门禁/审阅/blocked/索引同步是防错；plan 退化/审阅无差别/依赖分散是低效。
- workflow_retrospective：最大改进杠杆 = 去 task 分支 + 审阅分级。
- workflow_retrospective_0：噪音 finding 36%，真 bug 28%，噪音消耗的 turn 比真 bug 多。
- session_analysis：首轮审阅 PASS 率 29-30%，单 task 处置最多 1454 条。

我同意这个判断，且补充一层：**防错机制自身已产生新的失败模式**。审阅本是质量兜底，现在成了最大噪音源（P0）；tasks_index 本是可追溯，现在成了最大 merge 冲突源（P0）；/goal hook 本为推进，现在撑爆 context（P0）。防错过了临界点后，收益递减、成本递增，reviewer 与主 agent 的博弈消耗超过 bug 本身。

当前 AGENTS.md 已部分响应（`max_review_round` 从 2 提到 4、skill 拆分落地、task-bug/task-debt 路由补齐），但**最高 ROI 的三项未动**：审阅分级、reviewer 上下文注入、task 粒度量化。详见 §3/§4/§5。

---

## 2. skill 拆分提案：已落地，但遗留两点

`archive/workflow_skill_split_proposal.md` 提的 task_create/task_run 已扩展为 9 个 skill，职责划分合理（创建/执行/预审/并发/合并/卫生/清理/bug/技术债）。对照提案的「实施判定标准」：

- ✅ 未调用 skill 时 agent 仍能从 AGENTS.md 理解状态/门禁/禁区。
- ✅ task-create 不进入 active、不改实现。
- ✅ 两个 skill 不复制 AGENTS.md 完整规则。
- ⚠️ **「固定两个独立 reviewer 未弱化」形式上满足，实质上被架空**——审阅对所有 task 一视同仁，正是 retrospective_0 痛点 3 与 session_analysis P0 共同攻击的点。形式上审阅还在，实质上 reviewer 在低价值 task 上凑 finding、在高价值 task 上报无界 finding，两头都不讨好。
- ⚠️ **「中途恢复」部分满足**：tasks-run 有状态恢复表（backlog→Step1、active→Step2…），但 workflow_record.md 暴露的 worktree 事故不在恢复表覆盖范围——**分支 ≠ 隔离工作区**这一课，AGENTS.md 与 tasks-run 均未写入。Step 1 只校验 `git branch --show-current`，不校验「当前物理目录是否被其他 task 共享」。建议 Step 1 增加硬校验：`git worktree list` + 确认本 task 在独立 worktree 或主 worktree 无并发 task。

提案 §「不解决的问题」有先见之明：skill 无程序级强制力。session_analysis §4（subagent 失控 13→8→2、503 后不走 blocked）正是这一弱点的实证——**blocked 触发条件只覆盖黑盒满轮与审阅满轮两类，无基础设施失败路径**。AGENTS.md blocked 表至今只有两行。建议加第三行：基础设施失败（503/网络/subagent 启动失败 N 次）→ `block <tid> --reason infra`，禁止 agent 自定「容错上限」停手。

---

## 3. 审阅信噪比：笔记诊断正确，但「按读者重划」方案只完成一半

session_analysis §1 的根因四层（角色保守偏差 / 信息不对称 / finding 无锚 / 同模型盲区）是我见过的对 reviewer 失效最准确的分析。结论「换模型不解决问题，根因在 prompt + 上下文 + 阈值」我同意。

workflow_feedback §「spec/plan 边界的再评估」给出的解法「按读者切，不按字段语义切」方向也对：reviewer 需要的决策上下文（有意不测分支）、测试策略、未知契约清单应从 plan 前移到 spec。

**但对照当前模板，这个方案只完成了一半：**

- `docs/tasks/task_template/spec.md` 仍是「背景/范围/非范围/验收标准/依赖与约束」五段，**无「上下文区」**（决策依据 / 测试策略 / 未知契约清单）。
- `docs/reviews/prompts/code_prompt.txt` 只注入 `{tid}/{slug}/{spec_path}/{task_dir}/{diff_anchor}`，**不注入 plan.md 的决策上下文**。reviewer 仍只读 spec 不读 plan。
- `scripts/render_review_prompts.py` 占位符正则 `\{(tid|slug|spec_path|task_dir|diff_anchor)\}` 同样无 plan 相关字段。

净效果：笔记在 2026-07 已诊断「reviewer 信息不对称 → 撤回率高」，提出「spec 收编决策上下文」，但 render 脚本与 prompt 模板仍维持信息不对称现状。这是**诊断与处方都对、但处方未落地**的典型案例。

具体建议（按笔记因果链）：

1. **spec 模板分契约区 / 上下文区**。契约区=范围/非范围/AC（含可测试性声明）；上下文区=决策依据/测试策略/未知契约清单。reviewer prompt 明确「判 AC 只看契约区；判测试覆盖核对上下文区」。
2. **render_review_prompts.py 注入决策上下文**。新增占位符 `{plan_context}`（或直接把 plan.md 的「已判定不测分支」「测试策略」段落抽进 prompt），消除信息差。
3. **reviewer 硬阈值**：prompt 加「只报 AC 阻塞 / 行为级缺陷；建议加强测试降级为 non-blocking 备注」。当前 prompt 有「为凑数制造 finding」禁止项，但无积极阈值——负向约束不够，需正向锚定。
4. **AC 断言清单前置**：Step 2 红阶段产出「AC→断言映射表」，reviewer 只核对清单覆盖，不自由发挥「还可以测什么」。
5. **撤回率 > 30% 强制 reviewer 复盘 prompt**：check_review_status.py 检测 `已修/(已修+撤回+遗留)` 比例，超阈值则下一轮 review prompt 注入「上轮撤回原因」。

---

## 4. task 粒度与 commit 策略：笔记一致要求「一主题 N commit」，当前仍是「一 task 一 commit」

三份笔记独立撞同一堵墙：

- workflow_feedback §2：task 粒度「独立可验证 + 一个 commit」模糊，建议改「一个 task 一个主题，N 个 commit」。
- workflow_retrospective §7：t041 含 6 个独立修复点打包一个 commit、t048+t049+t051 合并一个 commit——实际粒度由「方便 merge」驱动，不是规则驱动。
- workflow_retrospective_0：AC 三处维护（spec/task.md/处置表）。

**当前 AGENTS.md「commit 策略」仍写死「执行期一个 task 一个 commit」**。这是笔记与现状最尖锐的矛盾点。

我的判断：**笔记对，现状错**。理由：

1. 「一个 commit」的初衷是可追溯 + 易回滚，但 21 task 实证显示它要么被违反（多修复点打包）、要么被架空（多 task 合一 commit），规则名存实亡。
2. 「一主题 N commit」不损失可追溯——commit subject 含 tid，task.md 收尾引用全部 commit hash；反而比「强行一个巨型 commit」更易 review（每个 commit 原子）。
3. 与审阅分级联动：commit 级 review（每原子 commit 独立）或 task 级总 review（最后一次），可由 risk_level 决定。

建议改 commit 策略：

- task = 一组逻辑相关改动；commit = task 内原子提交。
- spec 验收 = task 级；review 粒度 = commit 级或 task 级（按 review_level）。
- merge 到 main 保留 commit 历史（不 squash），task.md 收尾列出本 task 全部 commit hash。

同时落实 retrospective_0 痛点 7 的改进：AC 唯一源 = spec.md，task.md 收尾引用不复制。

---

## 5. 审阅分级 + max_review_round：当前用「统一提高上限」回避了「分级」

笔记一致要求按风险分级：

- workflow_feedback 改进 C：CRITICAL 审阅+e2e / MEDIUM 单审 / LOW lint+自验收。
- workflow_retrospective 改进 P1：spec front matter 加 `review_level: full|single|none`。
- workflow_retrospective_0 痛点 3：审阅对基础设施/CRUD 过重，minor-only FAIL 强制处置是浪费。
- session_analysis：max_review_round=2 偏紧（t102/t105 被加轮到 5）。

**当前 AGENTS.md 的应对是把 `max_review_round` 从 2 统一提到 4**。这是用「抬高上限」回避「分级」——对复杂 task 仍可能不够（t044 用过 5 轮），对简单 task 仍是过度（文档/格式 task 走 4 轮审阅是纯浪费）。

抬高上限只解决「复杂 task 不够」，不解决「简单 task 太重」，反而加重了后者。正确方向是分级，不是统一抬上限：

- spec front matter 加 `review_level: full|single|none` 与 `complexity: high|medium|low`。
- `full`（安全/资金/并发/鉴权）：审阅 + max_review_round=4~5。
- `single`（普通 API/前端/测试）：单审 + max_review_round=2。
- `none`（文档/配置/格式）：build+test 通过即可，max_review_round=0。
- minor-only FAIL 不强制 round 2（round 2 阈值 = 至少 1 条 important/critical）。
- round N 新 finding 允许 round N 内处置+验证收尾，不强制 round N+1（解决 retrospective_0 痛点 5 的 blocked 陷阱）。

review_level 由 task-create 时 agent 推断 + 用户确认；task.py 按 review_level 提示对应流程。

---

## 6. tasks_index.json merge 冲突：笔记给三方案，当前选了最弱的「不切分支」回避

workflow_retrospective §1（最大问题）：11 分支从同一 main 切出，task.py finish 都改 tasks_index.json，merge 必冲突；`-X theirs` 后已 finish 状态被旧 backlog 覆盖。给三方案：

- A：tasks_index 只在 main 维护，分支不改，merge 后统一 finish。
- B：状态写入 task.md front matter（每文件独立不冲突），tasks_index 由脚本扫描生成（derived data）。
- C：不切分支，全在 main 上做。

**当前现状**：AGENTS.md「硬约束」仍坚持 tasks_index.json 只能 task.py 改；tasks-run Step 1 仍「建并切换分支」；tasks-parallel 仍以 git branch 为基线。即**既保留分支、又保留 JSON 单点写入、又无 derived data 机制**——三个方案一个都没采纳，merge 冲突的结构性根因仍在。

11111.md 第 3 条「并发 task 总在 tasks_index.json 冲突，需要统一 task 服务」是同一问题的并发版。

我的判断：**方案 B（derived data）是根治，方案 C（不切分支）是回避**。方案 C 对单人串行可行（t048/t049/t051 验证过），但与 tasks-parallel 的 worktree 并发模型冲突——并发必须分支+worktree，而分支+JSON 单点写必冲突。方案 B 让状态从「集中 JSON」变「分散 front matter + 派生索引」，天然免疫 merge 冲突，且与「一个 task 一个目录」的物理结构一致。

建议：

- task.py 状态写入 `docs/tasks/{tid}_{slug}/task.md` front matter（status/branch/round）。
- tasks_index.json 改为 `scripts/task.py rebuild-index` 扫描 task.md 生成的 derived data，加 .gitignore 或仅作缓存，不入 git。
- archive/tasks_index.json 同理派生。
- 这样分支 merge 时 task.md 各文件独立不冲突，JSON 不再是同步点。

---

## 7. /goal hook context 溢出：笔记 P0，当前无对应机制

session_analysis §2：/goal Stop hook 强制 8 task 串行同一会话 → 两个 19MB 会话、Request too large、2377 次 error。建议每 task 切会话/切 worktree、单会话 ≤2 task、git-finalize 后强制 compact、subagent prompt 用文件路径而非内联回显。

当前 AGENTS.md 与 skill 无 /goal 相关约束（omni_media 未用 /goal，本模板仓也未引入）。这属于「尚未踩坑但笔记已预警」的项。若未来引入 /goal 或类似 Stop hook 推进机制，必须同时引入：

- 单会话 task 数硬上限。
- 检测 task.py finish 后强制 stop 让用户重启。
- subagent 派发 prompt 写 `.scratch/review_prompts/*.md`，prompt 内只传文件路径，不内联回显（86 subagent × 80K token 回显 ≈ 7MB，占 37% 会话体积——这条对当前 tasks-run Step 5 直接适用，render_review_prompts.py 已写 `.scratch/review_prompts`，方向对，但需确认 subagent prompt 本身不内联大段 spec/plan 正文）。

---

## 8. TDD 顺序与测试断言：笔记 P0，当前无硬约束

session_analysis §3：t098 实现改测试适配新实现（非测试驱动实现）、t105 改测试回 Step 3、5f8fdc72 auth 改坏仍过测。建议：实现变更致旧测试语义失效时必须新增红测；旧绿测只许删不许就地改预期；bug 修复前必答「现有测试为何没 catch」。

当前 AGENTS.md「开发原则」TDD 只一句「可测部分先红后绿；测试须触达生产逻辑」。task-bug skill 有「测试缺口分析（必做）」是亮点（对应「为何没 catch」），但 tasks-run 流程无「旧绿测只删不改」硬约束。

建议在 AGENTS.md 开发原则或 tasks-run Step 6 加：**实现变更导致旧测试语义失效时，必须新增红测覆盖新语义；旧绿测只允许删除，禁止就地改预期**。reviewer prompt 的 test 轴加复核项「改动是否在断言预期而非现状」。

---

## 9. 各文档个别点评

**workflow_feedback.md**：结构最完整（问题→保留→建议→待决策），「按读者重划 spec/plan」是全部笔记里最有理论价值的洞察。待决策项 6 条至今未决——建议不要等全量推行，先在一两个新 task 试点 review 分级 + spec 上下文区（笔记自己也这么建议）。

**workflow_retrospective.md**：数据扎实（21 task/31 commit/343→370 测试），「做得好的设计」清单克制准确（specs driven、max 上限、blocked、adoption_decision 追溯链、审阅抓真 bug 都该保留）。§1 merge 冲突三方案中推荐 C（不切分支）我持保留——见 §6，C 与并发模型冲突，B 才是根治。

**workflow_retrospective_0.md**：finding 分类表（真 bug 28%/噪音 36%）是量化噪音的最好证据。「根因：发现横向需求默认绕过+标遗留，不开新 task 根治」是元改进——当前 AGENTS.md 已有 task-debt skill 对应「捞遗留/技术债建 task」，方向已补；但「reviewer 发现横向缺口时建议开新 task 而非标遗留」这条 reviewer prompt 侧未落实（code_prompt.txt 有「系统性 follow-up」字段，算半落实，需在 prompt 中强化「已有 follow-up tid 则引用，不重复 finding」）。

**workflow_session_analysis_2026-07.md**：样本与方法最硬（80MB 日志、6 路并行 subagent、jq/grep 抽样），根因四层分析是全部笔记的高峰。优先级汇总表可直接当 roadmap。「亮点」清单（数据恢复纪律、黑盒真实运行时验证、如实标注未验证、交叉验证、审阅抓真 bug）提醒：改进时别把孩子和洗澡水一起倒掉——审阅在关键 task 上抓到 new Function RCE/余额泄漏/幂等缺口/local-api 越权，这些是真价值，分级是为了保住高价值场景的审阅精力，不是取消审阅。

**workflow_record.md**：单点事故复盘，教训明确（branch ≠ worktree）。见 §2，建议固化为 tasks-run Step 1 的 worktree 隔离校验。

**11111.md**：三条备忘。第 1 条「遗留文档与 bugs.md 结合记录所有遗留待办需求」已由 task-debt skill 部分覆盖；第 2 条「已列好计划直接执行，绝不准再进 plan mode」是情绪性备忘，不宜固化（与「先思考再编码」的全局约定冲突，且 plan mode 与 task 流程的 plan.md 是两回事）；第 3 条 tasks_index 并发冲突见 §6。

**archive/workflow_skill_split_proposal.md**：已落地，质量高（职责表、权威来源划分、不解决的问题、风险与控制都清晰）。「skill 无程序级强制力，关键限制仍靠脚本或 hook」是对的——当前 blocked 无 infra 路径、worktree 无校验、/goal 无约束，都是「需要脚本/hook 兜底但还没有」的点。

---

## 10. 改进优先级（综合四份笔记 + 当前现状）

|优先级|项|来源|当前状态|动作|
|------|------|------|------|------|
|P0|reviewer 注入决策上下文 + AC 硬阈值|session_analysis P0 / feedback 再评估|未做（render 无 plan 注入，prompt 无阈值）|改 render_review_prompts.py + code/test prompt + spec 模板分契约/上下文区|
|P0|审阅按 risk_level 分级|三份笔记一致|未做（仅统一抬 max 到 4）|spec front matter 加 review_level + complexity；task.py 按级提示|
|P0|tasks_index merge 冲突|retrospective §1 / 11111|未做（仍分支+JSON 单点写）|状态写 task.md front matter，JSON 改 derived data|
|P1|commit 策略改「一主题 N commit」|feedback B / retrospective §7|未做（仍一 task 一 commit）|改 AGENTS.md commit 策略；task.md 收尾列 commit hash|
|P1|blocked 加 infra 触发|session_analysis §4|未做（blocked 表只两行）|AGENTS.md blocked 表加第三行|
|P1|worktree 隔离硬校验|workflow_record|未做|tasks-run Step 1 加 git worktree 校验|
|P1|TDD 旧绿测只删不改|session_analysis §3|未做|AGENTS.md 开发原则 + test prompt 复核项|
|P2|subagent prompt 文件路径化|session_analysis §2|部分（render 写 .scratch，需确认不内联正文）|审 tasks-run Step 5 派发方式|
|P2|横向缺口 reviewer 建议开 task|retrospective_0 根因|半落实（prompt 有 follow-up 字段）|prompt 强化「引用已有 tid，不重复 finding」|
|P2|AC 唯一源 spec.md|retrospective_0 痛点 7|未做（task.md 模板仍复制 AC）|改 task_template/task.md 收尾引用不复制|
|P3|环境前置 doctor + 已知陷阱文档|retrospective_0 痛点 6 / session §9|未做|建 env_doctor task + known_pitfalls.md|
|P3|/goal 切会话 + 单会话 ≤2 task|session_analysis §2|本仓未引入 /goal，预防性|引入 /goal 时同步加约束|

**一句话**：四份笔记诊断高度一致且相互印证，方向（分级/按读者切/derived data/横向开 task）都对；当前模板仓吸收了约三成（skill 拆分、max 抬 4、task-debt/bug 路由），剩下七成（reviewer 上下文、审阅分级、JSON 冲突、commit 粒度、blocked infra、worktree 校验、TDD 硬约束）是最该先做的。建议按 P0 三项先试点，不要全量推。
