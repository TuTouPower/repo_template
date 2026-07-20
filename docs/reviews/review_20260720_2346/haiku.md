# haiku 视角审阅报告

## 当前模型判断依据

`~/.claude/settings.json` 顶层 `model=opus`，`env.ANTHROPIC_MODEL=default_model`，默认映射含 `default_haiku[1m]`、`default_sonnet[1m]`、`default_opus[1m]`；主会话仅可见 `default_model`。配置意图为 opus，但无法确认实际后端精确版本。

## 审阅范围

只读全量审阅 UTC+8 2026-07-20 的 9 个提交（按时序）：

- 28fcff3 merge: docs_spike_rule — 工作流重构（specs/task/commit + 独立 review）
- cf7fd15 docs(template): 完善文档结构与测试命令占位符
- 7c034bb docs(template): 加 schemas/ 和 config/ 目录，补充 schema 类型落点
- 265766c docs(template): 重构 task 工作流，拆分阶段写 spec/plan，收尾统一更新文档
- 0087c52 merge: workflow_refine - 拆分阶段写 spec/plan，收尾统一更新文档
- f7ac4a6 docs: 调整 task 拆分要求措辞
- 703b23d fix(template): 修复 review 发现的 step 编号引用与分支约束
- eddd2b3 fix(template): review agent 收窄为代码和测试，去掉文档审阅
- ba66be5 feat(template): 重构 reviewer 提示词和工作流，引入 verdict+重审+严格模式

对象：以上提交对 `AGENTS.md`、`docs/blueprint/conventions.md`、`docs/specs_index.md`、`docs/tasks_index.md`、`docs/templates/task/*`（adoption / review / review_prompt_code / review_prompt_test / task_report）、`schemas/`、`config/` 的改动及合并交互。

已排除：用户未提交删除 `docs/templates/task/review_prompt.md`，未读取，未引用。

## 高优先级问题（CRITICAL / HIGH）

### H1. 工作流与 tasks_index 关于"backlog 是否建目录"互相矛盾

- 位置：`0087c52 / ba66be5:AGENTS.md`「新需求拆分与创建 task」第 2 步（lines 46-50）；`265766c:docs/tasks_index.md:4`。
- 现象：
    - `AGENTS.md` 步骤 2 写：循环每个 task 一次性完成「登记 `backlog`」+「创建 `docs/tasks/TNNN_slug/`」+「复制 spec/plan/log 模板」。
    - `docs/tasks_index.md` 头部规则保留：「`backlog` 不建目录；`active` 必须有 `TNNN_slug/` 目录」。
    - 265766c 把 AGENTS.md 改为「登记 backlog + 建目录」同时执行，但未同步修订 tasks_index 头部规则；后续提交（含 ba66be5）一直未对齐。
- 影响：agent 执行时两处规则冲突。按 AGENTS.md 就违反 tasks_index 不变量；按 tasks_index 就无法完成 AGENTS.md 要求的一次性 spec/plan/log 模板复制。流程不可执行。
- 建议：明确二选一。若保留「拆分阶段即写 spec/plan」，应把 tasks_index.md 第 4 行改为「`backlog` 已建目录含 spec/plan/log；`active` 补齐 owner/branch」；或回退 AGENTS.md 创建步骤为「登记 backlog（不建目录）→ active 时建目录并复制模板」。
- 置信度：高（直接读两处快照可见）。
- 优先级：HIGH。

### H2. `docs/templates/task/review.md` 模板与派生提示词 finding 标题分隔符不一致

- 位置：`ba66be5:docs/templates/task/review.md` 第 14 行 `### TNNN_<前缀>_f001 - {标题}`（短横线带空格，无 em dash）；`ba66be5:docs/templates/task/review_prompt_code.md` / `review_prompt_test.md` 「输出格式」块 `### {TID}_code_f001 — {标题}`（em dash）。
- 现象：reviewer 提示词要求按"输出格式"块出报告，但 AGENTS.md step 5 又规定"首次复制 review.md 模板"。同一 finding 标题在两种来源下写法不同（`-` vs `—`）。
- 影响：reviewer 无论选哪一种都不会与另一处完全一致；对依赖 finding ID 正则的下游（adoption 表 / task_report 摘要）可能引入格式漂移。
- 建议：统一为同一字符（建议沿用 em dash `—`，与 review_prompt 输出块对齐），并把 review.md 的示例改为 `### TNNN_<前缀>_f001 — {标题}`。
- 置信度：高（直接读两处模板文件可见）。
- 优先级：HIGH（模板一致性缺陷，会传染每个 task）。

### H3. review Round 2 与 verdict 状态机存在漏洞：PASS 定义在 Round 1 / Round 2 语义不一致

- 位置：`ba66be5:AGENTS.md` step 5 末行规则；step 6 末行规则；`docs/blueprint/conventions.md`「review 报告字段」verdict 段。
- 现象：
    - step 5（Round 1）：`verdict: PASS`（0 finding，跳过 step 6 直接进 step 7）。
    - step 6 末段：两 reviewer 复核前轮 finding 是否真修 + 扫新 finding，`verdict: PASS`（全修且无新 finding）进 step 7。
    - conventions.md：「verdict：末行 `verdict: PASS`（0 finding 或前轮全修且无新 finding）」。
    - 三处对 PASS 的定义粒度不同。Round 1 的 PASS = "0 finding"；Round 2 的 PASS = "全修 + 无新 finding"；conventions 把两者并列用"或"连接，reviewer 在 Round 2 拿到"前轮全修但有新 finding"时无明确指令（应 FAIL，因"有 finding 或前轮未修透"覆盖，但"0 finding 或前轮全修且无新 finding"的"或"语义容易误读为两种独立 PASS 情形，未涵盖"前轮全修但有新 finding"的明确 FAIL 指向）。
- 影响：reviewer 子 agent 字面执行时可能把"前轮全修但有新 finding"误判，或把"前轮部分未修但无新 finding"误读为 PASS。状态机边界不清晰。
- 建议：统一为唯一判定式：「PASS 当且仅当 本轮 finding 数 = 0 且 前轮 finding 全部已修」。Round 1 无前轮，自然退化为 0 finding。conventions 字段说明同步收紧。
- 置信度：高。
- 优先级：HIGH。

### H4. `review.md` 模板首部注释与 step 5 提示词注入关系错位

- 位置：`ba66be5:docs/templates/task/review.md` 第 8 行「流程（两 agent 并行、续写规则、权限）见 AGENTS.md step 5。两 agent 各自从对应提示词文件注入……」。
- 现象：review.md 是 reviewer 首次输出报告时复制的"模板"。但模板正文写"两 agent 各自从对应提示词文件注入"——这是对 owner（派发方）的指示，而非 reviewer 自己需要的信息。reviewer 拿到的是 `review_prompt_code.md` / `review_prompt_test.md` 注入的提示词，复制 review.md 只是填空模板；模板正文写派发侧说明属于错位。
- 影响：reviewer 阅读自己产出的报告时，首段是 owner 视角的派发指引，降低报告可读性；若 reviewer 严格按"输出格式"块写报告（review_prompt 中已含完整首部），根本不会使用 review.md 的正文段，导致 review.md 实际被架空——AGENTS.md step 5 又强制"首次复制 review.md 模板"，规则自相矛盾。
- 建议：删除 review.md 第 8 行派发侧说明（提示词文件已自包含），或者把 review.md 精简为纯字段骨架，把"流程 step 5"这类指引只留在 AGENTS.md 和 review_prompt 中。同时澄清"复制模板"与"按 review_prompt 输出格式写"的优先级（建议以 review_prompt 输出块为准，review.md 仅作为空骨架）。
- 置信度：中（属设计与可读性问题，agent 实作时大概率以 review_prompt 输出块为准）。
- 优先级：HIGH（影响每个 task 的 review 产出一致性）。

## 中低优先级问题（MEDIUM / LOW）

### M1. `review_prompt_test.md` 存在错别字"辱界"

- 位置：`ba66be5:docs/templates/task/review_prompt_test.md` 「共享规则」节标题 `### read-only 辱界`。
- 现象：应为「边界」。review_prompt_code.md 同一位置写作「边界」。
- 影响：错别字，不影响语义，但会被注入到每个 test reviewer 的提示词中。
- 建议：改为「边界」。
- 置信度：高。
- 优先级：MEDIUM。

### M2. AGENTS.md step 5 共享规则把"重审追加"规则写在 Round 1 之前，首次阅读易引起 Round N 含义混淆

- 位置：`ba66be5:AGENTS.md` step 5 「共享规则」行：「重审追加（首次复制 `review.md` 模板；后续在文件末尾追加 `## Round N (YYYY-MM-DD HH:MM UTC+8)` 小节，不覆盖；finding ID 跨轮全局续编）」。
- 现象：Round 1 时尚无 Round 2，但规则已在 Round 1 上下文里讲"后续追加 Round N 小节"。Step 6 才出现"处置完进 step 5 Round 2（重审）"，此处 Round N 的 N 实际只能是 2（受 2 轮上限约束），但文本用变量 N。
- 影响：语义不清；reviewer 模板 review.md 的"round：{1/2}"字段也仅允许 1 或 2，但正文写 "Round N" 让 agent 误以为可有 Round 3+。
- 建议：把 "Round N" 改成 "Round 2"（与"2 轮上限"一致）；或在共享规则中说明"N ∈ {2}"。
- 置信度：高。
- 优先级：MEDIUM。

### M3. AGENTS.md step 6 "触代码或测试 -> 回 step 3 重新跑 test_cmd，再回 step 4 黑盒验证" 与 step 5 评审对象的 diff 范围存在循环引用模糊

- 位置：`ba66be5:AGENTS.md` step 6 第 2 个 bullet。
- 现象：修复代码后回 step 3 跑测试 + step 4 黑盒；然后"处置完进 step 5 Round 2"。Round 2 仍使用 `git diff <diff_anchor>...HEAD`。若修复过程中 HEAD 已变化（如 rebase、合并），diff 范围不再是"task 改动"。规则未约束 task 期间 HEAD 不变。
- 影响：多 task 并行或长 task 跨外部 rebase 时，Round 2 reviewer 看到的 diff 可能含外部改动，finding 噪声增多。
- 建议：明确「task 期间 diff_anchor 分支基线不可变；若必须 rebase，重置 diff_anchor 并在 log.md 记录」。
- 置信度：中（理论漏洞，单 task 串行执行时不会触发）。
- 优先级：MEDIUM。

### M4. step 6 "仅文档笔误类直接继续" 与 "仅文档事实类触发局部重审" 边界判定无规则

- 位置：`ba66be5:AGENTS.md` step 6 第 2 个 bullet。
- 现象：规则把文档改动分为"笔误类（错字、格式）"与"事实类"，但无判定标准。例：改正 spec 中错误的函数名，既可视为"错字"也可视为"事实"。
- 影响：owner 有裁量空间，可能将"事实类"偷偷归为"笔误类"跳过重审。
- 建议：补一条客观判定："涉及语义（名词、路径、函数名、参数、版本、状态）改动 = 事实类；纯排版、标点、错别字（不改语义） = 笔误类"。
- 置信度：高。
- 优先级：MEDIUM。

### M5. `task_report.md` 模板未要求记录 Round 1 verdict，只记 Round 2

- 位置：`ba66be5:docs/templates/task/task_report.md` 「adoption 处置摘要」`{Round 2 verdict：PASS / FAIL…}`。
- 现象：只记 Round 2 verdict。Round 1 若直接 PASS（0 finding）就跳过 step 6 进 step 7，task_report 模板没有对应字段体现"Round 1 PASS 直接进 step 7"路径。
- 影响：审计 task_report 时无法区分"Round 1 0 finding 直接通过"与"Round 2 通过"，追溯信息丢失。
- 建议：模板字段改为 `{Round 1 verdict：PASS / FAIL；Round 2 verdict：N/A / PASS / FAIL}`。
- 置信度：高。
- 优先级：MEDIUM。

### M6. conventions.md "review 报告字段" 中 `spec` 路径写绝对，review.md 模板写相对

- 位置：`ba66be5:docs/blueprint/conventions.md` 字段表 `spec：docs/tasks/TNNN_slug/spec.md`；`ba66be5:docs/templates/task/review.md` 第 3 行 `spec：\`spec.md\`（同目录，随归档移动仍有效）`。
- 现象：两处对同一字段的路径要求不同。reviewer 按 review.md 写相对路径，按 conventions 写绝对路径都对。
- 影响：同字段两种合法格式，跨 task 比较时需额外归一化。
- 建议：统一为相对路径（review.md 的"同目录，随归档移动仍有效"理由成立），把 conventions 字段说明改为 `spec.md（同目录相对路径）`。
- 置信度：高。
- 优先级：LOW。

### M7. review.md 模板「严重度」枚举与 review_prompt_test.md 危险模式严重度下限表述存在隐含冲突

- 位置：`ba66be5:docs/templates/task/review.md` 严重度枚举 `{critical / important / minor}`；`ba66be5:docs/templates/task/review_prompt_test.md` 「危险模式扫描（命中即 important+，硬阻断）」+「禁止」「危险模式降级为 minor（危险模式最低 important）」。
- 现象：模板允许 minor；提示词禁止把危险模式标 minor。两者不矛盾（提示词更严格），但 review.md 模板本身未提示"危险模式不得 minor"。reviewer 仅看模板字段说明时无此约束。
- 影响：若 reviewer 跳过提示词细则，可能把恒真断言等标为 minor。
- 建议：review.md 严重度枚举下补一行：「危险模式（见 review_prompt_test.md）最低 important」。
- 置信度：中（reviewer 实际按提示词工作，风险有限）。
- 优先级：LOW。

### M8. `specs_index.md` 字段说明与 AGENTS.md「目录与读写规则」对写入时机描述粒度不同

- 位置：`ba66be5:docs/blueprint/conventions.md` 「specs_index 字段」：「task 期间不写本表；全需求 task done 才首次写入」；`ba66be5:AGENTS.md` 目录表 `docs/specs_index.md` 写入规则：「需求全部 task done 后首次写入；废弃时删除行」。
- 现象：两处描述一致但粒度不同。conventions 多一条"task 期间不写本表"的明确禁令，AGENTS.md 未复述。
- 影响：agent 仅看 AGENTS.md 时理论上可能在 task 中途误写 specs_index。
- 建议：AGENTS.md 目录表 specs_index 行写入规则补「task 期间不写」。
- 置信度：高。
- 优先级：LOW。

### M9. 265766c 引入的 `docs/templates/task/review_prompt.md`（单文件含 Agent A/B 双提示词）在 ba66be5 被 review_prompt_code.md / review_prompt_test.md 取代，但未被删除

- 位置：`265766c:docs/templates/task/review_prompt.md`（新增 314 行）；`ba66be5:docs/templates/task/review_prompt_code.md` + `review_prompt_test.md`（新增）。ba66be5 未删除 review_prompt.md。
- 现象：同一份"reviewer 提示词"内容在仓库以两种组织形式并存（单文件双 Agent vs 双文件单轴）。用户已在工作区把 review_prompt.md 删除（git status 显示 `D docs/templates/task/review_prompt.md`），但该删除在审阅时点未提交，ba66be5 提交本身保留旧文件，形成冗余。
- 影响：模板目录双源真相；后续维护者改一处忘改另一处。
- 建议：用户提交该删除后关闭本项；若要审阅侧记录，登记为"已知遗留，待用户提交删除"。
- 置信度：高（git show ba66be5 --stat 无 review_prompt.md 删除记录；用户工作区显示已删）。
- 优先级：MEDIUM（用户已自理，登记提示）。

### M10. ba66be5 在 conventions.md 把 review 严重度从四级（critical/high/medium/low）改三级（critical/important/minor），未提供历史 review 报告的迁移路径

- 位置：`ba66be5:docs/blueprint/conventions.md` 严重度三级表；`265766c:docs/templates/task/review_prompt.md` 严重度四级表。
- 现象：严重度等级体系变更，但 `docs/archive/` 规则写"内部文件只准新增，不准修改"。历史 task 若曾用四级严重度写 review，新规范与历史归档不可对齐。
- 影响：跨时间审计同一项目时严重度语义漂移。
- 建议：在 conventions.md 严重度表下加一行"2026-07-20 起由四级改三级；历史归档中的 high ≈ important，medium/low ≈ minor"。
- 置信度：中（本仓库当前无 active task，纯理论问题；落到真实项目即触发）。
- 优先级：LOW。

## 改进建议

1. 统一 review 报告产出方式：AGENTS.md step 5 删除"首次复制 review.md 模板"，改为"reviewer 按 review_prompt_{code,test}.md 输出格式块写入 review_{code,test}.md"。review.md 仅保留作为字段速查表或删除。
2. verdict 判定式集中定义：在 conventions.md 写一次「PASS ⟺ 本轮 finding = 0 ∧ 前轮 finding 全部已修」，AGENTS.md / review_prompt 引用该处，避免三处定义漂移。
3. 状态机字段闭环：tasks_index.md 的 backlog / active 目录规则与 AGENTS.md 创建步骤对齐（见 H1）；review.md 的 round 枚举与 AGENTS.md 的"2 轮上限"对齐（见 M2）；task_report.md 增加 Round 1 verdict 字段（见 M5）。
4. 措辞统一：review.md / review_prompt_code.md / review_prompt_test.md 的 finding 标题分隔符（`-` vs `—`）、严重度枚举、reviewer_focus 取值逐字对齐。
5. 把"task 期间 specs_index 不写"从 conventions.md 上提到 AGENTS.md 目录表（见 M8），保证 AGENTS.md 单读即可执行。

## 不确定项 / 可能误报

1. H4 中关于"review.md 实际被架空"的判断依赖 reviewer 严格按 review_prompt 输出块写报告的假设。若 reviewer 先复制 review.md 再按提示词填字段，则两种来源同时被使用，格式冲突仍以 H2 形式呈现。此不确定不影响 H4 整改方向。
2. M3 关于 diff_anchor 在 rebase 后失效的问题，本仓库模板未实战，理论漏洞；若项目实际禁止 task 期间 rebase，则可忽略。
3. M9 双源提示词问题，用户已在工作区删除旧 review_prompt.md，本报告仅作登记提示；提交后自动消解。
4. M10 历史严重度迁移：本仓库尚未有真实 task 进入 archive，仅作为模板仓库需预先声明的规则，非紧迫问题。
