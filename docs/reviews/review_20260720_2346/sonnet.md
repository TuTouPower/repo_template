# Sonnet Review Report

审阅时间：2026-07-20 23:55 UTC+8

## 当前模型判断依据

可观测配置中，`~/.claude/settings.json` 顶层 `model` 为 `opus`；`env.ANTHROPIC_MODEL` 为 `default_model`，并配置 `ANTHROPIC_DEFAULT_SONNET_MODEL=default_sonnet[1m]`。本次审阅由主会话以 sonnet 视角执行，实际后端模型无法从可观测信息确认。

## 审阅范围

9 个 commit（2026-07-20 UTC+8）：
- `28fcff34` merge: docs_spike_rule
- `cf7fd156` docs(template): 完善文档结构与测试命令占位符
- `7c034bb3` docs(template): 加 schemas/ 和 config/ 目录
- `265766c9` docs(template): 重构 task 工作流
- `0087c52a` merge: workflow_refine
- `f7ac4a6e` docs: 调整 task 拆分要求措辞
- `703b23d4` fix(template): 修复 step 编号引用与分支约束
- `eddd2b31` fix(template): review agent 收窄为代码和测试
- `ba66be5f` feat(template): 重构 reviewer 提示词和工作流

涉及文件：`AGENTS.md`、`config/.gitkeep`、`docs/blueprint/conventions.md`、`docs/specs_index.md`、`docs/templates/task/adoption.md`、`docs/templates/task/review.md`、`docs/templates/task/review_prompt.md`、`docs/templates/task/review_prompt_code.md`、`docs/templates/task/review_prompt_test.md`、`docs/templates/task/task_report.md`、`schemas/.gitkeep`

## 高优先级问题（CRITICAL / HIGH）

### H1. 遗留 `review_prompt.md` 与新系统严重冲突

- 位置：`ba66be5f:docs/templates/task/review_prompt.md`（全文件，314 行）
- 现象：该文件在 `265766c9` 引入，是旧版单文件 review 提示词模板。`ba66be5f` 用 `review_prompt_code.md` + `review_prompt_test.md` 替换了其功能，但未删除或更新该文件。旧文件包含以下与新系统不一致的内容：
  1. **step 编号过期**：第 5 行引用 `AGENTS.md step 6`（应为 step 5）；第 117 行引用 `step 8`（已不存在，现在是 step 7）
  2. **review target 错误**：第 5 行和多处引用 `working tree`（未提交改动），新系统使用 `git diff <diff_anchor>...HEAD`（已提交改动）
  3. **严重度四级 vs 三级**：旧文件使用 `critical / high / medium / low`，新系统使用 `critical / important / minor`
  4. **verdict PASS 门槛不兼容**：旧文件 PASS 门槛 = "无 critical / high finding"，新系统 PASS = "0 finding 或前轮全修且无新 finding"（所有 finding 必修，严格模式）
  5. **reviewer 名称过期**：Agent A 仍叫"文档+代码 reviewer"，新系统已改为"代码 reviewer"
  6. **重审机制过期**：使用"局部重审 N（触发:原因）"格式，新系统使用"Round N"；旧文件有"零发现合法"和"finding 边界"规则，新系统未明确
- 影响：agent 或人类如果读到此文件作为 review 指引，会得到完全错误的指令。step 编号、target、严重度、verdict 四个关键维度全部不兼容。可能导致 review 产出无效报告、用错误 diff 范围、误判严重度。
- 建议：删除 `docs/templates/task/review_prompt.md`，或至少更新首行为 `DEPRECATED: see review_prompt_code.md and review_prompt_test.md`。
- 置信度：100%
- 优先级：**HIGH**

### H2. "局部重审" 概念未定义，与 "Round" 术语混用

- 位置：`ba66be5f:AGENTS.md:68`
- 现象：step 6 中写道"仅文档事实类触发局部重审"，但"局部重审"在整个文件中仅出现一次，没有定义触发条件、执行规则、输出格式或如何与 Round 2 交互。同文件其他位置（step 5、step 6、step 7）统一使用 "Round N" 术语。旧 `review_prompt.md` 有"局部重审"的详细规则（追加小节格式、触发原因标注），但新系统未迁移这些规则。
- 影响：agent 遇到文档类修改时不知道如何执行"局部重审"——是否需要重新派两个 reviewer？是否算入 2 轮上限？输出格式是什么？可能导致 review 流程卡死或跳过必要检查。
- 建议：（a）明确"局部重审"的完整规则并写入 AGENTS.md 或指向模板；或（b）将局部重审统一为"Round N"的一部分，在 Round 2 的执行范围说明中包含文档类修改的处理。
- 置信度：100%
- 优先级：**HIGH**

## 中低优先级问题（MEDIUM / LOW）

### M1. step 6 "处置完进 step 5 Round 2" 循环引用表述不清

- 位置：`ba66be5f:AGENTS.md:71`
- 现象：step 6 写道"处置完进 step 5 Round 2（重审）"。实际含义是：修复完成后回到 step 5 触发第二轮完整 review。但"进 step 5"容易被理解为"开始执行 step 5"而非"回到 step 5"，且后半句"回本 step 继续"指回到 step 6，形成 step 5 <-> step 6 循环。
- 影响：agent 可能误解流程方向。虽然结合上下文可推断意图，但表述不够清晰。
- 建议：改为"处置完后回 step 5 触发 Round 2（重审）"或在 step 6 末尾用流程图/伪代码说明循环。
- 置信度：90%
- 优先级：**MEDIUM**

### M2. review_prompt.md 旧 verdict 系统与新系统术语残留冲突

- 位置：`ba66be5f:docs/templates/task/review_prompt.md:54`
- 现象：旧文件第 54 行写"verdict（本模板引入，待 conventions.md 同步）"，但实际上 conventions.md 已在 `ba66be5f` 中同步了新的 verdict 字段。旧文件的"待同步"注释已过时。
- 影响：可能误导阅读者认为 verdict 机制尚未落地。
- 建议：删除该文件（同 H1）。
- 置信度：100%
- 优先级：**MEDIUM**

### M3. 旧 "零发现合法" 和 "finding 边界" 规则未显式迁移到新系统

- 位置：`ba66be5f:docs/templates/task/review_prompt.md:37-43`（旧规则）；`ba66be5f:docs/templates/task/review_prompt_code.md` 和 `review_prompt_test.md`（新文件无对应规则）
- 现象：旧 `review_prompt.md` 有两条规则：(1) "零发现合法：clean review 是有效输出。禁止凑数"（第 37 行）；(2) "范围内问题进 finding 表，范围外问题在结论段提示，不进 finding 表"（第 43 行）。新 `review_prompt_code.md` / `review_prompt_test.md` 未包含这些规则。新系统的 verdict PASS 条件（0 finding → PASS）隐式允许零发现，但"禁止凑数"和"finding 边界"规则完全缺失。
- 影响：reviewer agent 可能为凑 finding 而报告非问题；或将范围外问题混入 finding 表导致 verdict 误判 FAIL。
- 建议：在 `review_prompt_code.md` 和 `review_prompt_test.md` 的"共享规则"或"严重度"小节补充"零发现合法"和"finding 边界"规则。
- 置信度：90%
- 优先级：**MEDIUM**

### M4. `review.md` 模板与 `review_prompt_code.md`/`review_prompt_test.md` 输出格式标题不一致

- 位置：`ba66be5f:docs/templates/task/review.md:14` vs `ba66be5f:docs/templates/task/review_prompt_code.md:82`
- 现象：`review.md` 模板中 finding 标题格式为 `### TNNN_<前缀>_f001 - {标题}`（用 `-` 分隔），`review_prompt_code.md` 和 `review_prompt_test.md` 的输出格式示例中使用 `### {TID}_code_f001 — {标题}`（用 `—` 分隔，em dash）。
- 影响：minor 不一致。agent 在从模板复制时可能混用两种格式。
- 建议：统一为一种分隔符（建议 `-`）。
- 置信度：100%
- 优先级：**LOW**

### M5. 2 轮上限下 Round 2 FAIL 后的 adoption 处理未说明

- 位置：`ba66be5f:AGENTS.md:72`
- 现象：step 6 写"Round 2 仍 FAIL -> task 不得 done，需用户决策（降级 / 拆 task / 重写）"。但未说明此时 adoption.md 的状态——Round 2 的 finding 是否仍需写 adoption 处置？如果用户选择"降级"，adoption 中的 finding 如何标记？
- 影响：agent 在 Round 2 FAIL + 用户决策后不知道是否需要更新 adoption.md。
- 建议：补充说明 Round 2 FAIL 后的 adoption 处置流程（如"在 adoption.md 追加 Round 2 小节，标记为用户决策结果"）。
- 置信度：85%
- 优先级：**MEDIUM**

### L1. commit `265766c9` 引入了 `review_prompt.md` 但 commit message 未提及此为过渡文件

- 位置：`265766c9` commit message
- 现象：commit message 说"新增 review_prompt.md 用于派 review sub agent"，但后续 `ba66be5f` 用两个新文件替代了它。`265766c9` 未说明这是过渡方案。
- 影响：git 历史中可能有人根据 commit message 使用旧文件。
- 建议：无需代码修改，仅记录为历史备注。
- 置信度：95%
- 优先级：**LOW**

## 改进建议

1. **删除或弃用 `review_prompt.md`**：这是最关键的清理项。该文件与新系统在 4 个关键维度（step 编号、target、严重度、verdict）完全不兼容。
2. **统一重审术语**：将"局部重审"明确定义或合并到 "Round N" 体系中。
3. **补充"零发现合法"和"finding 边界"规则**到新 reviewer 提示词文件。
4. **补充 Round 2 FAIL 后的 adoption 处置流程**。
5. **统一 finding 标题分隔符**（`-` vs `—`）。

## 不确定项 / 可能误报

### U1. "进 step 5 Round 2" 是否为明确的工作流循环

- 说明：step 6 -> step 5 -> step 6 的循环是有意设计还是表述不清，取决于作者意图。如果作者认为这是标准的"循环"模式（类似 while loop），则 M1 可能降级为 LOW。但从 agent 可执行性角度看，循环引用需要更清晰的终止条件说明（Round 2 PASS 进 step 7 / FAIL 上限后停止）。
- 置信度：70%

### U2. 旧 `review_prompt.md` 是否为有意保留的参考文档

- 说明：如果作者有意保留该文件供后续对比或参考，则 H1 应降级。但从 AGENTS.md step 5 已明确指向 `review_prompt_code.md` 和 `review_prompt_test.md` 来看，旧文件应为无意遗留。
- 置信度：80%

### U3. `docs/blueprint/conventions.md` 中 adoption 字段表与 AGENTS.md step 6 描述的 "severity" 列位置

- 说明：conventions.md 的 adoption 字段表列顺序为 `finding_id | severity | status | rationale | fix_ref`，AGENTS.md step 6 的描述一致。无问题。
- 置信度：100%（确认无问题）
