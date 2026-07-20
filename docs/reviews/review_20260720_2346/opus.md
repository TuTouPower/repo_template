## 当前模型判断依据

可观测配置中，`/home/karon/.claude/settings.json` 顶层 `model` 为 `opus`；`env.ANTHROPIC_MODEL` 为 `default_model`，并配置 `ANTHROPIC_DEFAULT_HAIKU_MODEL=default_haiku[1m]`、`ANTHROPIC_DEFAULT_SONNET_MODEL=default_sonnet[1m]`、`ANTHROPIC_DEFAULT_OPUS_MODEL=default_opus[1m]`。主会话可见标识仅为 `default_model`。综合判断：当前路继承主会话，配置意图为 opus，但无法从可观测信息确认实际后端精确模型或版本；不声称读取到运行时内部状态。

## 审阅范围

- commits：`28fcff3` `cf7fd15` `7c034bb` `265766c` `0087c52` `f7ac4a6` `703b23d` `eddd2b3` `ba66be5`
- 路径：`AGENTS.md`、`config/.gitkeep`、`docs/blueprint/conventions.md`、`docs/specs_index.md`、`docs/templates/task/adoption.md`、`docs/templates/task/review.md`、`docs/templates/task/review_prompt.md`、`docs/templates/task/review_prompt_code.md`、`docs/templates/task/review_prompt_test.md`、`docs/templates/task/task_report.md`、`schemas/.gitkeep`，外加被 `28fcff3` merge 带入的 `README.md`
- 工作区改动 `D docs/templates/task/review_prompt.md` 排除；仅以 `ba66be5` 及历史 commit 快照判定
- 方法：`git diff <parent>..<commit>` / `git show <commit>:<path>` 逐个比对，再以 `ba66be5` 为终态统一回溯交互影响

## 高优先级问题（CRITICAL / HIGH）

### H1. CRITICAL — spec.md / plan.md 无任何步骤规定填写时机

- 位置：`ba66be5:AGENTS.md:42-58`（"新需求拆分与创建 task" step 2 + "单 task 流程" step 1-2）
- 现象：`265766c` 把旧版 step 1"写 spec + plan 交用户审核"改成"登记 active + 记录 diff_anchor"，后续步骤直接到 step 2"可测试部分先写红"。新需求拆分 step 2 只说"从 `docs/templates/task/` 复制模板创建 `spec.md`、`plan.md`、`log.md`"——即只创建空模板文件。整个工作流中**没有任何一步要求 owner 填写 spec.md 的背景/范围/验收标准，也没有要求填写 plan.md**。
- 影响：
  - agent 按 AGENTS.md 走流程会留空模板直接写红测试；但 step 2 "可测试部分先写红" 需要验收标准作依据，无 spec 验收标准则红测试凭空捏造。
  - step 5 reviewer 对照"task spec 出 finding 清单"——spec 是空模板，review 无基准。
  - step 7 收尾 task_report"对照 spec 验收标准逐条勾选"——无验收标准可勾。
  - `specs driven` 开发原则彻底架空。
- 建议：在单 task 流程 step 1 前或 step 1 内补"填写 `spec.md` 验收标准和 `plan.md` 主要步骤"，或把"新需求拆分 step 2"改为复制模板后由 owner 立即填写 `spec.md`/`plan.md` 并提交用户审核。明确登记 active 与写 spec/plan 的先后。
- 置信度：高
- 优先级：CRITICAL

### H2. CRITICAL — step 7 前置条件阻断 Round 1 零 finding 的正常路径

- 位置：`ba66be5:AGENTS.md:64,71,73`
- 现象：
  - step 5 末："verdict: PASS（0 finding，跳过 step 6 直接进 step 7）"。
  - step 6 末："处置完进 step 5 Round 2（重审）……进 step 7"。
  - step 7 标题："前置：两 reviewer Round 2 均 `verdict: PASS`，或遗留项经用户显式批准保留"。
  - 当 Round 1 就是 0 finding 时，从未进入 step 6，也就没有 Round 2；step 7 前置"两 reviewer Round 2 均 PASS"永不满足。
- 影响：干净 task（零 finding）按字面规则无法进 step 7 收尾，流程死锁。
- 建议：step 7 前置改为"Round 1 就 PASS 时直接进 step 7；否则两 reviewer Round 2 均 PASS 或遗留项经用户显式批准"。
- 置信度：高
- 优先级：CRITICAL

### H3. CRITICAL — Round 2 FAIL 后无合法出口进 step 7

- 位置：`ba66be5:AGENTS.md:71-73`
- 现象：step 6 "2 轮上限：同一 task 最多 2 轮 review。Round 2 仍 FAIL -> task 不得 done，需用户决策（降级 / 拆 task / 重写），在 `task_report.md` 记录 blocked 原因。" step 7 前置只接受"Round 2 PASS 或遗留项经用户批准"，而用户决策的"降级 / 拆 task / 重写"既不是 PASS 也不是遗留批准。
- 影响：Round 2 FAIL 后唯一合法出口是"不得 done"，但项目层又要求"循环执行所有 task"才能推进需求，被卡住的 task 既不能 done 也不能跳过，需求无法完结。
- 建议：明确"用户批准降级/拆 task/重写"时如何在 task 层落地——例如允许以 `dropped` 终止本 task 并新建 successor task，或在 step 7 前置补"用户显式批准的降级处置"。
- 置信度：高
- 优先级：CRITICAL

### H4. HIGH — `docs/templates/task/review_prompt.md` 旧版未删除，与新 prompt 并存且指令冲突

- 位置：`ba66be5:docs/templates/task/review_prompt.md`（由 `265766c` 引入；`eddd2b3`、`ba66be5` 未同步或删除）
- 现象：`ba66be5` 新增 `review_prompt_code.md`/`review_prompt_test.md` 并在 AGENTS.md step 5 指定用这两份。但旧 `review_prompt.md` 仍在 `docs/templates/task/` 目录里，描述：
  - "见 `AGENTS.md` step 6"（AGENTS.md 终态 review 是 step 5）
  - "Agent A：文档+代码"、"Agent B：测试"（AGENTS.md 终态已收窄为"代码/测试"）
  - "各自只读 working tree"（AGENTS.md 终态用 `git diff <diff_anchor>...HEAD`）
  - 严重度 `critical/high/medium/low`（AGENTS.md 终态是 `critical/important/minor`）
- 影响：agent 读 templates 目录会看到三份 prompt；AGENTS.md 表 `docs/templates/` 用途"task / task review+adoption / spike 模板"未列出 review_prompt* 的角色。旧版与新版冲突，reviewer 若复制错模板会产出与工作流不兼容的报告。
- 建议：在 HEAD 中删除 `docs/templates/task/review_prompt.md`（工作区已 D，提交即可），或在 AGENTS.md/templates 表里明确该文件已废弃。
- 置信度：高
- 优先级：HIGH

### H5. HIGH — step 7 "更新 `docs/specs/`" 与"目录与读写规则"直接冲突

- 位置：`ba66be5:AGENTS.md:12,74`
- 现象：表 `docs/specs/<slug>.md` 写入规则"仅全需求 task done 后写入一次；废弃时移入 archive"。但 step 7 收尾"更新本次 task 受影响文档：……`docs/specs/`……"。单个 task done 时需求里可能还有其他 task 未完成，按 step 7 写 `docs/specs/` 直接违反表规则。
- 影响：agent 按哪条执行产出不同结果。若按 step 7 每个 task 都写 specs/，累积式 spec 与"一次性固化"语义冲突；若按表规则不写，step 7 指令空转。
- 建议：明确 docs/specs/ 只在全需求 task 都 done 后写（挪到需求级完结节），或把表规则改回"task 黑盒通过后累积"。二者只能选一。
- 置信度：高
- 优先级：HIGH

### H6. HIGH — 需求级完结路径被彻底删除

- 位置：`ba66be5:AGENTS.md:33-91`（对照 `265766c` 之前的"需求完整周期"段）
- 现象：`265766c` 删除了"需求完整周期"代码块（新需求 → 拆 task → 循环 → 所有 task done → 需求 spec 状态改 done → 移入 archive）。终态 AGENTS.md 没有任何位置规定"一个需求何时算完结、何时把 `docs/specs/<slug>.md` 写入 `docs/specs_index.md`、何时把 spec 移入 `docs/archive/specs/`"。`docs/specs_index.md` 文件头说"需求全 task done 后首次写入"但工作流里找不到对应步骤。
- 影响：需求级状态机不完整。最后一个 task done 后没人负责把 slug 写入 specs_index、没人负责归档 spec。`dropped` 节"需求级废弃"假设 spec 已在 specs_index，但写入时机未定义。
- 建议：补"需求完结"小节：所有 task done 后，owner 把 spec 固化到 `docs/specs/<slug>.md`、写入 `docs/specs_index.md`；之后若废弃才走 dropped 的需求级流程。
- 置信度：高
- 优先级：HIGH

### H7. HIGH — README.md 仍按旧工作流描述，与 AGENTS.md 终态多处冲突

- 位置：`ba66be5:README.md:25,28,31-41`
- 现象（由 `28fcff3` 引入后未更新）：
  - line 25 "specs driven + TDD：spec 和 plan 先行并过用户审核"——AGENTS.md 终态已删除"交用户审核"步骤。
  - line 28 目录注 `specs/  # 需求 spec（task 黑盒验证后累积）`——AGENTS.md 终态改成"仅全需求 task done 后写入一次"。
  - line 22 `tasks/  # active task 工作区`——AGENTS.md 终态把 `tasks/TNNN_slug/` 描述为"task 工作区（含开发中 spec）"，`dropped` 节里 backlog 目录也要建在 tasks/ 下，不只是 active。
- 影响：README 是仓库入口，agent 或人类按 README 理解的流程与 AGENTS.md 矛盾。
- 建议：同步 README 的"设计原则"和"目录概览"注释到 AGENTS.md 终态语义。
- 置信度：高
- 优先级：HIGH

### H8. HIGH — `log.md` 模板无 `diff_anchor` 首行结构

- 位置：`ba66be5:AGENTS.md:56` vs `ba66be5:docs/templates/task/log.md`
- 现象：AGENTS.md step 1 "记录 `diff_anchor`（当前 HEAD SHA）到 `docs/tasks/TNNN_slug/log.md`（首行 `diff_anchor: <SHA>`）"。`log.md` 模板首行是 `# Task log` 标题，无 `diff_anchor` 字段位或说明。
- 影响：owner 按模板填会与 AGENTS.md 要求冲突——若保留 `# Task log` 标题，"首行 diff_anchor" 无处置；若把 diff_anchor 放标题前破坏 Markdown 标题首行惯例。
- 建议：在 `log.md` 模板顶部加 frontmatter 或固定小节 `## diff_anchor` / `- diff_anchor: <SHA>`，AGENTS.md step 1 同步引用该位置。
- 置信度：高
- 优先级：HIGH

## 中低优先级问题（MEDIUM / LOW）

### M1. MEDIUM — tasks_index 状态枚举无集中定义

- 位置：`f7ac4a6:AGENTS.md`（删除"tasks_index 状态：`backlog`、`active`、`done`、`dropped`"一行）
- 现象：`f7ac4a6` 删除唯一枚举位置后，状态值散落在 step 1 (`backlog`)、单 task 流程 step 1 (`active`)、step 7 (`done`)、dropped 节 (`dropped`)，无一处集中声明。读者需通读才能拼出状态机。
- 影响：状态集不直观，新增状态或改动时容易遗漏同步点。
- 建议：在"总览"段补一行 `tasks_index 状态：backlog / active / done / dropped`。
- 置信度：高
- 优先级：MEDIUM

### M2. MEDIUM — Round 编号与 step 编号混用造成阅读混乱

- 位置：`ba66be5:AGENTS.md:60,64,71`
- 现象：step 5 标题"review Round 1"，step 6 末"处置完进 step 5 Round 2（重审）"。"step 5 Round 2"把步骤号和轮次号叠加；Round 2 实际是在 step 6 处置完再触发 step 5 的第二轮执行。
- 影响：agent 按字面"进 step 5 Round 2"会理解为"重做 step 5 全部动作包括派 agent"，但 step 5 里的"首次复制模板/登记 finding 前缀"等子动作 Round 2 不应重做。
- 建议：把 Round 抽象为独立小节或在 step 5 标明"Round 1 和 Round 2 共用此 step，仅 Round 编号和输出小节不同"。
- 置信度：中
- 优先级：MEDIUM

### M3. MEDIUM — 严重度分级在三处描述不一致

- 位置：`ba66be5:AGENTS.md:65` vs `ba66be5:docs/blueprint/conventions.md:66-72` vs `ba66be5:docs/templates/task/review_prompt_code.md:30-33` / `review_prompt_test.md:44-48`
- 现象：
  - AGENTS.md `critical = bug/安全/数据丢失`；`important` 含 "AC 缺失实现或测试"。
  - conventions.md `critical = bug/安全/数据丢失/broken functionality`（多了 broken functionality）；`important` 列表与 AGENTS.md 相同。
  - `review_prompt_code.md` `critical = bug/安全/数据丢失/broken functionality`（同 conventions.md）。
  - `review_prompt_test.md` `critical = 测了假行为致 AC 看似覆盖但实际未验证 / 删除关键 AC 的测试 / mock 掉被测逻辑`（与 AGENTS.md 完全不重叠）；`important` 含 "删 expect、AC 缺测试、红灯未归因"。
- 影响：reviewer 按不同来源给同一问题定不同严重度，adoption 无法机械对齐。
- 建议：把 critical/important/minor 的分级与示例集中到 conventions.md 一处，AGENTS.md 与两份 prompt 都引用同一份定义。
- 置信度：高
- 优先级：MEDIUM

### M4. MEDIUM — task_report.md 模板对零 finding / 无 Round 2 场景未定义

- 位置：`ba66be5:docs/templates/task/task_report.md:10-14`、`ba66be5:AGENTS.md:76`
- 现象：模板"adoption 处置摘要"写"已修 N 项 / 遗留 K 项 + Round 2 verdict：PASS/FAIL"。零 finding 时不进 step 6、无 adoption 文件、无 Round 2。模板字段无值可填。
- 影响：零 finding task 的 task_report 填法不确定，agent 可能瞎填或留占位符。
- 建议：模板补"Round 1 PASS 无 finding 时，adoption 摘要写'Round 1 零 finding，未进 adoption'"。
- 置信度：中
- 优先级：MEDIUM

### M5. MEDIUM — adoption.md 模板固定写"Round N"，零 finding 时整个文件不创建与字段引用脱节

- 位置：`ba66be5:docs/templates/task/adoption.md:1`、`ba66be5:docs/blueprint/conventions.md:74-91`
- 现象：conventions.md "task 文件模板"表列出 `adoption.md` 为 task 固定文件，但零 finding task 按 AGENTS.md step 6 不会创建 adoption.md。模板与流程对"是否必有 adoption.md"的认知不一致。
- 影响：task 完结检查清单若按 conventions.md 表执行会误判缺文件。
- 建议：conventions.md 表注明 adoption.md 仅在 review 非 PASS 时存在，或改为"可选"。
- 置信度：中
- 优先级：MEDIUM

### M6. MEDIUM — 局部重审无轮次上限，可与 step 6 形成无穷回路

- 位置：`ba66be5:AGENTS.md:68`
- 现象：step 6 "仅文档事实类触发局部重审……重审发现新问题回到本 step 处置"。局部重审不算 Round，无 2 轮上限。若每次修文档都触发新局部重审、每次局部重审又报新 finding，循环无上限。
- 影响：文档类 finding 可能把 task 卡在循环里。
- 建议：明确局部重审也受"2 轮上限"或"总 finding 上限"约束。
- 置信度：中
- 优先级：MEDIUM

### M7. MEDIUM — "新需求拆分 step 2" 要求先建目录登记 backlog，dropped 流程会把大量空目录塞进 archive

- 位置：`ba66be5:AGENTS.md:47-50,85`
- 现象：拆需求时"循环每个 task 一次性完成：登记 backlog + 创建目录 + 复制空模板"。若需求拆 10 个 task 只做了 3 个，剩 7 个走 dropped backlog 流程"目录移入 `docs/archive/tasks/`"，archive 堆积大量只含空模板的目录。
- 影响：archive 噪音；空模板与真实废弃 task 混杂，历史追溯困难。
- 建议：backlog task 放弃时只把 tasks_index 标 dropped，目录若仅含未填写模板可不归档。
- 置信度：中
- 优先级：MEDIUM

### M8. MEDIUM — 何时创建 `task_tnnn_slug` 分支未规定

- 位置：`ba66be5:AGENTS.md:39,48,56`
- 现象：总览"一个需求拆成 N 个 task（TNNN，独立分支 `task_tnnn_slug`……）"，新需求拆分 step 2 只说"登记 backlog + 建目录 + 复制模板"（无 branch），单 task 流程 step 1 "填 owner 和 branch" 但未说"此时 `git checkout -b task_tnnn_slug`"。
- 影响：agent 不知何时切分支，可能到 step 1 才切（已错过 step 2 的"建目录"动作），或在默认分支直接提交。
- 建议：在单 task 流程 step 1 显式加"创建并切换到分支 `task_tnnn_slug`"。
- 置信度：中
- 优先级：MEDIUM

### M9. MEDIUM — `703b23d` 删除"新需求 spec 引用旧 slug"，削弱需求级追溯

- 位置：`703b23d:AGENTS.md`（删除"若被新需求替代：在新需求的 spec 里引用旧 slug；旧 spec 文件头注明 `被 <新 slug> 替代，归档于 YYYY-MM-DD`"）
- 现象：删除后，需求级废弃只保留"把 spec 移入 archive + 从 index 删行"，无反向引用。
- 影响：新需求替代旧需求时，旧 slug 在 archive 中无被替代记录，新需求 spec 无历史来源。
- 建议：保留 `703b23d` 之前的互相引用规则，或在 `decisions.md` 里要求记录替代关系。
- 置信度：中
- 优先级：MEDIUM

### L1. LOW — 占位符 `{TID}` 与 `{TNNN}` 混用

- 位置：`ba66be5:AGENTS.md:61-62`（`TNNN_code_fNNN`）vs `review_prompt_code.md:5,9`、`review_prompt_test.md:5,9`（`{TID}_code_fNNN`）
- 现象：AGENTS.md 用 `TNNN_` 描述编号格式，prompt 文件用占位符 `{TID}`。替换后产物一致，但占位符名不统一。
- 影响：阅读时需脑内转换；新人可能把 `{TNNN}` 当占位符去替换出错。
- 建议：统一为 `{TID}`（因为 ID 值就是 T001/T042 等）。
- 置信度：高
- 优先级：LOW

### L2. LOW — conventions.md "task 文件模板"表未含 log.md 的 diff_anchor 字段

- 位置：`ba66be5:docs/blueprint/conventions.md:41`
- 现象：表里 log.md 字段"进展；踩坑；中途决策；偏离 plan 的原因；关键验证结果"——未列 diff_anchor（AGENTS.md step 1 要求 log.md 首行记 diff_anchor）。
- 影响：字段说明不全。
- 建议：log.md 字段补"diff_anchor（首行，task 开始时 HEAD SHA）"。
- 置信度：高
- 优先级：LOW

### L3. LOW — `review.md` 模板标题与字段命名前后不一致

- 位置：`ba66be5:docs/templates/task/review.md:1,5-9`
- 现象：标题"Task review TNNN（reviewer_focus: {代码/测试}）"，但字段 `reviewer_focus` 后已无值列（标题已含），模板仍列 `round` 字段占位。字段顺序与 conventions.md review 报告字段表（task/spec/diff_anchor/target/reviewer_focus/round/reviewed_at）不完全对齐。
- 影响：reviewer 复制模板填写时字段顺序混乱。
- 建议：对齐字段顺序与 conventions.md 表。
- 置信度：中
- 优先级：LOW

### L4. LOW — `cf7fd15` 表合并后"内容"列丢失

- 位置：`cf7fd15:AGENTS.md`（两张表合一）
- 现象：原"按需阅读"表有独立的"内容"列描述每个文档的内容，合并后该信息并入"用途"列，部分条目描述变简。
- 影响：轻微可读性损失，无功能性差异。
- 建议：无需修改。
- 置信度：高
- 优先级：LOW

### L5. LOW — `review_prompt_test.md` 有错别字"read-only 辱界"

- 位置：`ba66be5:docs/templates/task/review_prompt_test.md:53`（标题"### read-only 辱界"）
- 现象：应为"边界"，错字"辱界"。
- 影响：阅读体验。
- 建议：改"边界"。
- 置信度：高
- 优先级：LOW

### L6. LOW — conventions.md "实验代码入库保留，但不代表可直接用于生产" 与 AGENTS.md "仅作为验证材料" 措辞分歧

- 位置：`ba66be5:docs/blueprint/conventions.md:109` vs `ba66be5:AGENTS.md:103`
- 现象：两处都允许实验代码入库，但一者强调"不代表可直接用于生产"，一者强调"仅作为验证材料"。意思相近但措辞不统一。
- 影响：理解歧义。
- 建议：统一措辞。
- 置信度：中
- 优先级：LOW

## 改进建议

1. 补回"填写 spec.md/plan.md"步骤（修 H1），这是整个工作流能否运转的前提。
2. 修正 step 7 前置与 Round 2 FAIL 出口（修 H2、H3），状态机才能闭合。
3. 清理 `docs/templates/task/review_prompt.md` 旧文件（修 H4）。
4. 同步 README（修 H7），避免人类与 agent 按错误流程初始化项目。
5. 集中严重度定义到 conventions.md 一处，两份 prompt 和 AGENTS.md 都引用（修 M3）。
6. 补需求级完结路径（修 H6、H5），让需求状态机闭合。
7. 全文统一 `step N` / `Round N` 语义（修 M2），可在"单 task 流程"开头给状态转换图。
8. `log.md` 模板加 diff_anchor 字段位（修 H8、L2）。

## 不确定项 / 可能误报

- **M7（backlog 目录归档）**：取决于实际使用频率，若项目需求都小而稳，dropped backlog 数量低，影响轻微。若按"先全部拆 task 再执行"的大需求常见，则实际是 HIGH。本报告按 MEDIUM 估。
- **M5（adoption.md 是否必有）**：conventions.md "所有 active task 固定使用以下文件"原意可能指"可能出现的文件集"而非"每个 task 都会有"。若解读为后者，则 M5 不成立。
- **H8（log.md diff_anchor 位置）**：AGENTS.md "首行 diff_anchor: <SHA>" 可能被解读为"在 `# Task log` 标题之前的 frontmatter 或 metadata 行"，Markdown 允许此类结构。若项目惯例如此，则与模板不冲突。
- **L4（表合并丢信息）**：影响极小，可能不算问题。
- **commit `28fcff3` merge 内容**：本次审阅把 merge 带入的 README 改动也视为这批 commit 的影响面，若审阅边界严格限定在 9 个 commit 自身（不含 merge 携带的另一分支产物），README 相关问题应单独归类。本报告仍按"合并结果"纳入。
