# 审阅结果决策

## 目录
docs/reviews/review_20260720_2346

## 报告来源
- 已读：current.md / grok.md / haiku.md / opus.md / sonnet.md
- 缺失：无（本轮五份齐全；预期常有 current/haiku/sonnet/opus，本轮额外含 grok.md）

## 统计
- 采纳：26 项（含原 4 项待决定，用户 2026-07-21 全部按推荐 A 批准）
- 不采纳：6 项
- 待决定：0 项

## 用户审批

- 时间：2026-07-21
- 指示：全部按推荐 A；执行落地
- 待决定项结论：1→A（working tree / `git diff <diff_anchor>` + cached）；2→A（backlog 建目录）；3→A（reviewer 撤回争议）；4→A（verdict 不动 + task 备注 exception）

---

## 原待决定项（已转采纳）

### 1. review 目标改为 working tree / index 相对 diff_anchor
- 结论：采纳（选项 A）
- 修复说明：review target = `git diff <diff_anchor>`（unstaged，相对 anchor 的工作区）与 `git diff --cached`（index）；全文统一；保留单 commit、commit 前 review。

### 2. backlog 拆分即建目录
- 结论：采纳（选项 A）
- 修复说明：tasks_index 改为 backlog 已建目录；dropped 未填模板可不归档。

### 3. 严格模式受控争议 / reviewer 撤回
- 结论：采纳（选项 A）
- 修复说明：owner 举证 → 原 reviewer 追加撤回；仅撤回或用户裁决后可不改代码。

### 4. 用户批准 exception 不改写 reviewer verdict
- 结论：采纳（选项 A）
- 修复说明：tasks_index 备注 `done_with_exception` + 批准信息；task_report 分栏 reviewer verdict 与用户处置。

---

## 采纳项

### 5. Round 1 零 finding 时 step 7 前置「须 Round 2 PASS」死锁
- 来源：current, grok（H2）, opus（H2 CRITICAL）, haiku（H3 相关）
- 位置：`AGENTS.md:64,73`；`task_report.md` Round 2 字段
- 优先级：CRITICAL（合并取最高）
- 详细判断理由：step 5 写 Round 1 PASS 跳过 step 6 进 step 7；step 7 前置写两 reviewer Round 2 均 PASS。零 finding 路径字面永不满足。多份报告独立确认，修复为文案/前置条件对齐，无产品取舍歧义。
- 修复说明：
  - `AGENTS.md` step 7 前置改为：两路 reviewer **最新一轮**均为 `verdict: PASS`（Round 1 两轴均 0 finding 可无 Round 2；若进过 adoption 则须 Round 2 PASS），或遗留项经用户显式批准（语义见待决定项 4）。
  - conventions / task_report：用「最终 verdict / 最后一轮」表述，不强制「必须有 Round 2」。
  - 统一 PASS 判定式（可写入 conventions 一处）：`PASS ⟺ 本轮 finding 数 = 0 ∧（无前轮 ∨ 前轮 finding 全部已修）`。

### 6. Round 2 FAIL 与「回 step 6 继续」及 2 轮上限冲突
- 来源：current（HIGH）, opus（H3 CRITICAL）, grok（M2/M3）, sonnet（M1/M5）
- 位置：`AGENTS.md:71-72`
- 优先级：CRITICAL（合并取最高）
- 详细判断理由：line 71 写 Round 2 FAIL 回 step 6 继续；line 72 写最多 2 轮、Round 2 FAIL 不得 done 等用户。状态机无唯一下一状态。应把 Round 2 FAIL 定为 blocked 终态。
- 修复说明：
  - 删除或改写「Round 2 FAIL → 回本 step 继续」为：Round 2 FAIL → **blocked**，不得自动再开 Round 3；写 `task_report` blocked 原因；等用户决策（允许额外轮次 / 拆 task / 重写 / 显式降级）。
  - 若用户批准额外轮次：显式覆盖 2 轮上限，报告仍追加 `## Round N`，adoption 同步追加。
  - step 5–6 用伪代码或状态表写清：`R1 PASS→收尾` / `R1 FAIL→adopt→R2` / `R2 PASS→收尾` / `R2 FAIL→blocked`。

### 7. 工作流未要求填写 spec.md / plan.md
- 来源：opus（H1 CRITICAL）
- 位置：`AGENTS.md:42-58`（新需求拆分 step 2 + 单 task step 1–2）
- 优先级：CRITICAL
- 详细判断理由：拆分只「复制模板创建」空文件；单 task 从登记 active / diff_anchor 直接到写红。无验收标准则 TDD、review 对照 spec、task_report 勾选均架空。属明确遗漏步骤。
- 修复说明：
  - 新需求拆分 step 2 或紧随其后：owner **填写** `spec.md`（背景/范围/验收标准）与 `plan.md`（主要步骤）；禁止空模板进入 active。
  - 单 task step 1 前置校验：spec 验收标准非空，否则不得标 active / 不得写红。
  - 若保留用户审核，在 README/AGENTS 写清审核点；若取消审核，同步删 README「过用户审核」旧述（与采纳项 11 一起）。

### 8. step 7 更新 `docs/specs/` 与目录规则冲突；需求级完结路径缺失
- 来源：current（HIGH）, opus（H5/H6 HIGH）
- 位置：`AGENTS.md:11-12,74` 与全文缺少「需求完结」小节
- 优先级：HIGH
- 详细判断理由：目录表规定 specs 仅全需求 task done 后写一次；step 7 却让每个 task 更新 `docs/specs/`。`265766c` 删掉需求完整周期后，specs_index 写入时机在工作流中无落点。
- 修复说明：
  - 普通单 task step 7 **移除** `docs/specs/`（及 specs_index）更新。
  - 新增「需求完结」：该需求全部 task `done` 后，固化 `docs/specs/<slug>.md`、写入 `docs/specs_index.md`；废弃走现有 dropped。
  - AGENTS 目录表 specs_index 行补「task 期间不写」。

### 9. 旧 `review_prompt.md` 与新双轴 prompt 并存冲突
- 来源：current, grok（H1）, haiku（M9）, opus（H4）, sonnet（H1）
- 位置：HEAD 仍有 `docs/templates/task/review_prompt.md`；工作区已 `D` 未提交
- 优先级：HIGH
- 详细判断理由：旧文件 step 编号、target、严重度四级、PASS 门槛、文档轴均与终态冲突。五份报告一致要求删除或 DEPRECATED。工作区已删，采纳为正式删除并保证无引用。
- 修复说明：提交删除 `docs/templates/task/review_prompt.md`；全仓确认 AGENTS/conventions/模板无指向该路径；commit message 标明 supersede by code/test prompts。

### 10. 「局部重审」未定义且可能绕过 2 轮上限
- 来源：grok（H3 HIGH）, sonnet（H2 HIGH）, opus（M6）, current 未单列
- 位置：`AGENTS.md:68`（全文仅此一处）
- 优先级：HIGH
- 详细判断理由：触发后是否派 sub agent、是否计轮次、输出格式、与 Round 2 顺序均未定义；局部重审还可能形成无上限循环。
- 修复说明：
  - 取消独立「局部重审」术语；文档事实类修复并入 **下一次 Round 范围过滤**（改 spec/AGENTS/blueprint/AC → 两路；仅实现 → code；仅测试 → test）。
  - 所有重审计入 Round 上限（默认最多 Round 2）；不另开无限局部循环。
  - AGENTS 共享规则中「Round N」改为与 2 轮上限一致的表述（Round 2 或 N∈{2}，用户批准额外轮次除外）。

### 11. task 分支生命周期与 review 基线污染
- 来源：current（HIGH 基线 + MEDIUM 生命周期）, opus（M8）, grok 部分
- 位置：`AGENTS.md:39,56,60`；`conventions.md:55-56`
- 优先级：HIGH
- 详细判断理由：总览要求独立分支 `task_tnnn_slug`，但流程无 checkout/校验；diff 固定 `anchor...HEAD`（或未来 working-tree 基线）若不在 task 分支，会混入他人改动并被严格模式强制修。
- 修复说明：
  - 单 task step 1：`git checkout -b task_tnnn_slug`（或切换已有分支）；校验 `git branch --show-current` 与 tasks_index.branch 一致；记录 fork point / `diff_anchor`。
  - review 前校验：当前分支正确；若曾同步主线，重置或记录 diff_anchor 规则（禁止 silent rebase 不改 anchor）。
  - dropped/active 放弃：不把半成品合入默认分支；分支删除/保留策略写一句（可由外部 orchestrator 负责合并，但 task 内必须在正确分支工作）。

### 12. README 与 AGENTS 终态多处不一致
- 来源：opus（H7 HIGH）, grok（M6 MEDIUM）
- 位置：`README.md:13,22-45` 等
- 优先级：HIGH
- 详细判断理由：仍写「过用户审核」「specs 黑盒后累积」；目录树无 `schemas/`、`config/`；tasks 注释过窄。入口文档误导人类与 agent。
- 修复说明：
  - 设计原则对齐 AGENTS：spec/plan 时机、review 时序、specs 仅全需求 done 后固化。
  - 目录树补 `schemas/`、`config/`；`specs/` 注释改为「全需求 task done 后固化」；`tasks/` 含开发中 spec（含 backlog 目录策略随待决定项 2）。

### 13. `log.md` 模板无 `diff_anchor` 脚手架
- 来源：grok（M4）, opus（H8 HIGH）
- 位置：`AGENTS.md:56` vs `docs/templates/task/log.md`；`conventions.md` log 字段表
- 优先级：HIGH
- 详细判断理由：流程要求首行 `diff_anchor: <SHA>`，模板只有 `# Task log`，易漏记。
- 修复说明：
  - `log.md` 模板顶部增加 `diff_anchor: <SHA>`（标题下首行 metadata，或 `## diff_anchor` 固定小节）；AGENTS step 1 指向该位置。
  - conventions task 文件表 log 字段补 diff_anchor。

### 14. prompt 内「working tree diff」与 range diff 术语残留
- 来源：grok（M5）；依赖待决定项 1
- 位置：`review_prompt_code.md:49` vs `:76`；`review_prompt_test.md:56` vs `:83`
- 优先级：MEDIUM
- 详细判断理由：同一文件证据源分裂。修复必须与待决定项 1 选定方案一致。
- 修复说明：待决定项 1 选定后，两份 prompt 的共享规则、Process、target 字段全部改为同一 diff 命令与同一称谓；删除冲突措辞。

### 15. 危险模式「`.fill()` 一律硬阻断」过宽
- 来源：current（MEDIUM）
- 位置：`review_prompt_test.md:27-42,84,129`
- 优先级：MEDIUM
- 详细判断理由：Playwright 等对文本框合法 API 为 `.fill()`；模板写成「冒充输入」会导致合法 E2E 必 FAIL 且严格模式必修。
- 修复说明：改为「必须调查并说明」；仅当用程序赋值/ `.fill()` **替代** AC 要求的拖拽/点击/键盘等真实交互、或降低行为覆盖时出 finding；文本框输入使用 `.fill()` 合法。

### 16. 新 prompt 未迁移「零发现合法」与「finding 边界」
- 来源：grok（M1）, sonnet（M3）
- 位置：旧 `review_prompt.md` 有；`review_prompt_code.md` / `review_prompt_test.md` 无
- 优先级：MEDIUM
- 详细判断理由：严格模式下范围外 finding 会错误抬高 FAIL；缺「禁止凑数」易灌水。
- 修复说明：两份新 prompt 共享规则补：
  - 零发现合法，禁止凑数；
  - 范围内进 finding 表，范围外仅结论段提示、不进表。

### 17. `task_report.md` 仅记 Round 2，零 finding 路径无填法
- 来源：haiku（M5）, opus（M4）
- 位置：`docs/templates/task/task_report.md:10-14`；`AGENTS.md:76`
- 优先级：MEDIUM
- 详细判断理由：Round 1 直接 PASS 无 adoption、无 Round 2，模板字段无值。
- 修复说明：字段改为 `Round 1 verdict` + `Round 2 verdict（N/A | PASS | FAIL）`；零 finding 时 adoption 摘要写「Round 1 零 finding，未进 adoption」。

### 18. 严重度定义三处不一致
- 来源：opus（M3）
- 位置：`AGENTS.md:65`；`conventions.md:66-72`；两份 review_prompt
- 优先级：MEDIUM
- 详细判断理由：critical 示例在 code/test/AGENTS 不对齐；test 轴 critical 与实现轴不重叠却未说明分层。
- 修复说明：在 `conventions.md` 集中定义 critical/important/minor（含 code 轴与 test 轴示例）；AGENTS 与两 prompt 改为引用 conventions，不各写一套。

### 19. finding 标题分隔符 `-` vs `—` 不一致
- 来源：haiku（H2，优先级偏高）、sonnet（M4 LOW）、grok（M7 LOW）
- 位置：`review.md:14` 用 ` - `；prompt 输出块用 ` — `
- 优先级：MEDIUM（合并后按传染面；非逻辑 bug）
- 详细判断理由：模板与 prompt 不一致，下游解析易脆。
- 修复说明：统一为 ASCII ` - `（`### {TID}_code_f001 - {标题}`）；同步 review.md 与两 prompt 示例。

### 20. `review_prompt_test.md` 笔误「辱界」
- 来源：haiku（M1）, grok（L1）, opus（L5）, sonnet 未列
- 位置：`review_prompt_test.md`「### read-only 辱界」
- 优先级：LOW（haiku 标 MEDIUM，合并取合理：LOW 笔误但必改）
- 详细判断理由：应为「边界」；code prompt 正确。
- 修复说明：改为「### read-only 边界」。

### 21. reviewer 要求校验「owner 指定项目根」但无占位符
- 来源：current（LOW）
- 位置：`review_prompt_code.md:74`；`review_prompt_test.md:81`
- 优先级：LOW
- 详细判断理由：字面无法执行，多 worktree 易审错目录。
- 修复说明：改为 `git rev-parse --show-toplevel` 与 `{task_dir}` 所属仓库核对；或增加 `{project_root}` 并要求派发前替换。推荐前者，少一个占位符。

### 22. tasks_index 状态枚举无集中声明
- 来源：opus（M1）
- 位置：`f7ac4a6` 后 AGENTS 删除集中枚举；散落各 step
- 优先级：MEDIUM
- 详细判断理由：状态集需通读才能拼齐；改动成本极低。
- 修复说明：AGENTS「总览」补一行：`tasks_index 状态：backlog / active / done / dropped`（与 tasks_index 头部一致）。

### 23. conventions 将 adoption.md 写成固定文件，零 finding 时不存在
- 来源：opus（M5）
- 位置：`conventions.md` task 文件表；`AGENTS.md` step 6
- 优先级：MEDIUM
- 详细判断理由：零 finding 不进 step 6、无 adoption；表与流程不一致。
- 修复说明：conventions 注明 `adoption.md` 仅 review 非零 finding / 进入 adoption 时存在（可选）。

### 24. 文档「笔误类」vs「事实类」边界无规则
- 来源：haiku（M4）
- 位置：`AGENTS.md` step 6
- 优先级：MEDIUM
- 详细判断理由：owner 可把事实改动标笔误跳过重审。
- 修复说明：补客观判定——涉及语义（名词、路径、函数名、参数、版本、状态）= 事实类；纯排版/标点/不改语义的错别字 = 笔误类。

### 25. 需求替代时互相引用规则被删
- 来源：opus（M9）
- 位置：`703b23d` 删除「新需求 spec 引用旧 slug」等
- 优先级：MEDIUM
- 详细判断理由：废弃只移 archive+删 index，无双向追溯。恢复成本低、有历史价值。
- 修复说明：在 dropped 需求级或恢复需求节补回：新 spec 引用旧 slug；旧 spec 文件头注明被替代与日期（archive 内只增不改则写在新 spec / decisions，避免改 archive；推荐仅在新 spec 与 specs_index 备注引用旧 slug）。

### 26. `review.md` 模板含派发侧说明、与 prompt 输出块双源
- 来源：haiku（H4）
- 位置：`review.md` 首部注释；AGENTS step 5「首次复制 review.md」
- 优先级：MEDIUM
- 详细判断理由：派发指引不应出现在 reviewer 报告骨架；与 prompt 输出块双源导致格式漂移（连带分隔符问题）。
- 修复说明：`review.md` 精简为字段骨架；删派发侧长说明；AGENTS 明确：报告结构以 `review_prompt_{code,test}.md` 输出格式为准，`review.md` 仅空骨架参考。

### 27. 占位符 `{TID}` 与叙述 `TNNN` 混用
- 来源：opus（L1）
- 位置：`AGENTS.md:61-62` vs 两 prompt `{TID}`
- 优先级：LOW
- 详细判断理由：替换后一致但阅读易混。
- 修复说明：AGENTS 叙述与示例统一用 `{TID}`（值为 T001 等）；编号格式写 `{TID}_code_fNNN`。

### 28. conventions `spec` 路径绝对 vs review.md 相对
- 来源：haiku（M6 LOW）
- 位置：`conventions.md` vs `review.md:3`
- 优先级：LOW
- 详细判断理由：同字段两种合法格式，跨 task 比较需归一化。
- 修复说明：统一为相对 `spec.md`（同目录，随归档移动仍有效）；conventions 字段说明同步。

---

## 不采纳项

### 29. 历史 commit message 未标明 review_prompt 为过渡文件
- 来源：sonnet（L1）, grok（L3）
- 位置：git 历史 `265766c9` 等
- 优先级：LOW
- 详细判断理由：已发生历史无法也不应改写 message；删除文件时在新 commit message 标明 supersede 即可（见采纳项 9）。

### 30. `cf7fd15` 表合并后「内容」列信息变简
- 来源：opus（L4）
- 位置：历史 AGENTS 表结构
- 优先级：LOW
- 详细判断理由：报告自身建议无需修改；可读性轻微损失，无功能影响。

### 31. spike 实验代码措辞「不用于生产」vs「仅作验证材料」
- 来源：opus（L6）
- 位置：`conventions.md:109` vs `AGENTS.md:103`
- 优先级：LOW
- 详细判断理由：语义等价，属风格偏好；强行统一收益低于改动面。

### 32. 历史严重度四级→三级迁移说明
- 来源：haiku（M10）
- 位置：`conventions.md` 严重度表
- 优先级：LOW
- 详细判断理由：本模板仓尚无真实 archive review 用四级；archive 只增不改。可选注释价值低，不强制本次落地。

### 33. 危险模式严重度从 critical 降 important 需写迁移理由
- 来源：grok（L2）
- 位置：旧 prompt vs 新 test prompt
- 优先级：LOW
- 详细判断理由：有意分层（假覆盖才 critical）合理；非缺陷。集中定义严重度（采纳项 18）后自然澄清。

### 34. review.md 补「危险模式不得 minor」提示
- 来源：haiku（M7）
- 位置：`review.md` 严重度枚举
- 优先级：LOW
- 详细判断理由：reviewer 以 prompt 为准；骨架再写易双源。采纳项 18 集中定义即可。

---

## 落地方式备注（执行阶段）

本仓库 `AGENTS.md` / `CLAUDE.md` 规定开发与修复走 **task 流程**（spec/plan、红绿、黑盒、review、adoption、commit）。用户批准本决策后：

1. 待决定项 1–4 需用户给出选择（或「按推荐」）。
2. 将采纳项（及已决策的待决定项）拆为独立可验证 task，**不绕过 task 流程直接改代码**。
3. 建议拆分方向（执行时再定 ID）：
   - T-workflow-state：待决定 1 选定后的 diff 源 + 项 5/6/10/14/17（Round/step 状态机与 prompt target）
   - T-spec-lifecycle：项 7/8/12 部分 + 待决定 2（spec 填写、需求完结、backlog 目录、README）
   - T-branch-log：项 11/13（分支生命周期、diff_anchor 模板）
   - T-review-templates：项 9/15/16/18/19/20/21/26/27/28（删旧 prompt、危险模式、零发现、严重度、格式）
   - T-strict-exception：待决定 3/4 选定后的严格模式与 exception 语义
   - 其余 MEDIUM/LOW 可并入邻近 task，避免过碎
