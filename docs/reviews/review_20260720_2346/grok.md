# Grok Review Report

审阅时间：2026-07-20 23:56 UTC+8  
审阅者：Grok 4.5（xAI）

## 当前模型判断依据

本会话由 Grok Build / Grok 4.5 执行，身份由系统提示 `You are Grok 4.5 released by xAI` 声明。未读取 `~/.claude/settings.json` 作为本路模型依据；本报告为独立 grok 路，不继承 Claude 会话模型配置。

## 审阅范围

9 个 commit（2026-07-20 UTC+8，按时间序）：

| SHA（短） | 说明 |
|-----------|------|
| `28fcff34` | merge: docs_spike_rule — 工作流重构（specs/task/commit + 独立 review） |
| `cf7fd156` | docs(template): 完善文档结构与测试命令占位符 |
| `7c034bb3` | docs(template): 加 schemas/ 和 config/ 目录 |
| `265766c9` | docs(template): 重构 task 工作流，拆分阶段写 spec/plan |
| `0087c52a` | merge: workflow_refine |
| `f7ac4a6e` | docs: 调整 task 拆分要求措辞 |
| `703b23d4` | fix(template): 修复 step 编号引用与分支约束 |
| `eddd2b31` | fix(template): review agent 收窄为代码和测试 |
| `ba66be5f` | feat(template): 重构 reviewer 提示词和工作流，verdict+重审+严格模式 |

**审阅对象**：上述 commit 落在 `ba66be5f`（HEAD）上的终态文档与模板；对照各 commit 意图检查中间态引入的冲突是否已消解。

涉及文件：`AGENTS.md`、`README.md`、`config/.gitkeep`、`schemas/.gitkeep`、`docs/blueprint/conventions.md`、`docs/specs_index.md`、`docs/templates/task/{adoption,review,review_prompt,review_prompt_code,review_prompt_test,task_report,log,spec,plan}.md`

**说明**：working tree 存在未提交的 `docs/templates/task/review_prompt.md` 删除，**不纳入本审阅结论**（审阅的是 7.20 已提交历史与 HEAD 内容；该删除若意图落地需另 commit）。

## 高优先级问题（CRITICAL / HIGH）

### C1. review 目标 `git diff <diff_anchor>...HEAD` 与「commit 前 review」互斥

- 位置：`AGENTS.md:56-60,80`；`README.md` 设计原则 6–7；`review_prompt_code.md:8,76`；`review_prompt_test.md:8,83`；`conventions.md:55-56`
- 现象：
  1. README 明确：**review 和 adoption 在 commit 前完成**；一个 task = 一个 commit，commit 在 step 8。
  2. step 1 把 `diff_anchor` 记为 task 开始时 HEAD；step 5 要求评审 `git diff <diff_anchor>...HEAD`。
  3. step 2–7 的实现、测试、文档、review 报告均未要求中间 commit。
  4. 在「无中间 commit」前提下，`HEAD` 仍停在 `diff_anchor`，三/双点 diff **为空**；真实改动只在 working tree / index。
  5. 新提示词 Process 步骤写 `git diff {diff_anchor}...HEAD`，共享规则却写「一切以 **working tree diff** 为准」——同一文件内证据源分裂。
- 影响：agent 严格按 `git diff anchor...HEAD` 会得到空 diff，产出虚假 clean review / 0 finding PASS，绕过全部审查；或擅自改用 working tree 却与模板字段 `target` 不符。这是流程可执行性层面的硬伤。
- 建议（择一并全文统一）：
  - **A（推荐，贴合现有「单 commit」）**：review target 改为 `git diff <diff_anchor>`（含 unstaged/staged，或明确 `git diff <diff_anchor> && git diff --cached`），字段与提示词全部改写；或
  - **B**：允许 task 内 WIP commit，review 审 `anchor...HEAD`，最终 squash/改写为单 commit（需在 AGENTS 写清）；或
  - **C**：review 改到 commit 之后、merge 之前（改「task=commit」语义与 README）。
- 置信度：98%
- 优先级：**CRITICAL**

### H1. 遗留 `review_prompt.md` 与新 review 系统严重冲突

- 位置：`265766c9` 引入；`ba66be5f` 未删除；HEAD 仍含 `docs/templates/task/review_prompt.md`（314 行）
- 现象：`ba66be5f` 用 `review_prompt_code.md` + `review_prompt_test.md` 替换职责，AGENTS step 5 只指向新文件，但旧文件仍在模板目录。旧文件与终态至少在以下维度冲突：
  1. 流程 step：写 `AGENTS.md step 6`（现 review 为 step 5）；finalization 写 `step 8`（现收尾 step 7，commit step 8）
  2. target：`working tree` vs 新系统 `git diff <diff_anchor>...HEAD`
  3. 严重度：四级 `critical/high/medium/low` vs 三级 `critical/important/minor`
  4. verdict PASS：旧=无 critical/high；新=0 finding 或前轮全修且无新 finding（严格模式）
  5. reviewer 名称：仍为「文档+代码」；`eddd2b31`/`ba66be5f` 已收窄为「代码」
  6. 重审格式：「局部重审 N」vs「Round N」
  7. 注释仍写「verdict 待 conventions.md 同步」——`ba66be5f` 已同步
- 影响：模板目录下存在可被误复制/误注入的过期 prompt；agent 若按文件名 `review_prompt.md` 加载会得到错误指令。
- 建议：从 `docs/templates/task/` 删除该文件，或改名为并加首行 `DEPRECATED`；AGENTS / conventions 确认无链接。
- 置信度：100%
- 优先级：**HIGH**

### H2. Round 1 PASS 直达 step 7，与 step 7「须 Round 2 PASS」前置矛盾

- 位置：`AGENTS.md:64` vs `AGENTS.md:73`
- 现象：
  - step 5：`verdict: PASS`（0 finding）→ **跳过 step 6 直接进 step 7**
  - step 7 前置：**两 reviewer Round 2 均 `verdict: PASS`**，或遗留经用户批准
- 影响：Round 1 零发现路径在字面上永远不满足 step 7 前置（没有 Round 2）。agent 可能卡死、强行再跑一轮、或忽略前置。
- 建议：step 7 改为「两路均为最新 round `verdict: PASS`（Round 1 零发现可无 Round 2；有 adoption 则须 Round 2 PASS）」。
- 置信度：95%
- 优先级：**HIGH**

### H3. 「局部重审」未定义，与 Round 体系混用

- 位置：`AGENTS.md:68`（唯一出现）；旧规则仅存于 `review_prompt.md` 续写节
- 现象：step 6 写「仅文档事实类触发**局部重审**，按改动范围分流……」，但未定义：
  - 是否派 sub agent、是否计入 2 轮上限
  - 输出是追加 `## Round N` 还是另格式
  - 与「处置完进 step 5 Round 2」的顺序关系（先局部再 Round 2？局部是否替代 Round 2？）
- 影响：修复触及 spec/AGENTS/blueprint 时流程分叉不可执行。
- 建议：要么把局部重审并入 Round 体系（明确 scope 过滤），要么用独立小节定义触发/执行/输出/是否计轮次。
- 置信度：95%
- 优先级：**HIGH**

## 中低优先级问题（MEDIUM / LOW）

### M1. 新提示词未迁移「零发现合法」与「finding 边界」

- 位置：旧 `review_prompt.md` 有完整规则；`review_prompt_code.md` / `review_prompt_test.md` 无对应条款
- 现象：新系统 PASS=0 finding 隐式允许 clean review，但未写「禁止凑数」；也未写「范围外问题进结论、不进 finding 表」。在「所有 finding 必修」严格模式下，范围外 finding 会错误抬高 FAIL 成本。
- 影响：凑数 finding 或范围外 finding 迫使无效修复；或 agent 漏报边界规则。
- 建议：两份新 prompt 的共享规则中补回这两条（术语对齐 diff_anchor 后的 target）。
- 置信度：90%
- 优先级：**MEDIUM**

### M2. step 6「处置完进 step 5 Round 2」循环表述易误读

- 位置：`AGENTS.md:71-72`
- 现象：step 5 ↔ step 6 形成循环；「进 step 5」与「回本 step」方向需上下文推断。2 轮上限写在 step 6，step 5 本身无「当前 round 号」计算规则。
- 影响：agent 可能在 Round 编号、何时停上出错。
- 建议：用伪代码写清 `round=1..2` 循环，或明确「Round 2 仅由 step 6 触发一次完整 step 5」。
- 置信度：85%
- 优先级：**MEDIUM**

### M3. Round 2 FAIL 后 adoption / 用户决策路径不完整

- 位置：`AGENTS.md:72`；`task_report.md:13`；`adoption.md`
- 现象：Round 2 FAIL → 不得 done，用户决策（降级/拆 task/重写）。未说明：是否必须再写 adoption Round 2 小节；「降级」时 finding 的 status 用什么（严格模式无「无需修改」）；用户批准如何落到 `task_report` / index。
- 影响：blocked task 文档状态不一致。
- 建议：补「用户决策」时 adoption status 约定（如 `遗留`+用户批准引用，或显式 `用户降级` 枚举）。
- 置信度：85%
- 优先级：**MEDIUM**

### M4. `log.md` 模板未体现 `diff_anchor` 首行约定

- 位置：`AGENTS.md:56` vs `docs/templates/task/log.md`
- 现象：流程要求 log 首行 `diff_anchor: <SHA>`；模板只有通用「记录」区，无该字段脚手架。
- 影响：owner 易漏记，后续 review 无基准。
- 建议：模板首部加 `diff_anchor: <SHA>` 占位。
- 置信度：95%
- 优先级：**MEDIUM**

### M5. 提示词内「working tree diff」与「git diff anchor...HEAD」术语残留冲突

- 位置：`review_prompt_code.md:49` vs `:76`；`review_prompt_test.md:56` vs `:83`
- 现象：共享规则强调 working tree，Process 用 commit range。即便 C1 选定一种 target，此处也需统一。
- 影响：reviewer 证据源不一致。
- 建议：与 C1 一并统一用词。
- 置信度：95%
- 优先级：**MEDIUM**

### M6. README 目录概览未包含 `schemas/`、`config/`

- 位置：`7c034bb3` 已写入 AGENTS 与 conventions；`README.md` 树状概览无这两项
- 现象：初始化说明与 AGENTS 目录表不一致。
- 影响：新人/agent 只读 README 时找不到契约与配置约定落点。
- 建议：README 树补充 `schemas/`、`config/`。
- 置信度：100%
- 优先级：**MEDIUM**

### M7. finding 标题分隔符不一致

- 位置：`review.md:14` 用 ` - `；`review_prompt_code.md:96` / `review_prompt_test.md:105` 用 ` — `
- 现象：模板与 prompt 输出示例不一致。
- 影响：格式噪声，解析脚本若依赖标题会脆。
- 建议：统一为 ASCII `-`。
- 置信度：100%
- 优先级：**LOW**

### L1. `review_prompt_test.md` 笔误「read-only 辱界」

- 位置：`review_prompt_test.md:50`
- 现象：应为「边界」（`review_prompt_code.md:43` 正确）。
- 影响：可读性；偶发检索失败。
- 建议：改「边界」。
- 置信度：100%
- 优先级：**LOW**

### L2. 危险模式严重度：旧 critical 硬阻断 vs 新 important+

- 位置：旧 `review_prompt.md` 危险模式「命中即 critical」；新 `review_prompt_test.md:27-42,71-75` 多为 important，critical 收窄为「假行为/删关键 AC/mock 被测逻辑」
- 现象：`eddd2b31`→`ba66be5f` 有意降档部分危险模式，但未在 conventions/AGENTS 说明迁移理由；与「禁止降级为 minor」并存。
- 影响：不一定是 bug，但历史对比易误解为弱化审查。
- 建议：在 conventions 或 prompt 注释一句「危险模式默认 important，仅假覆盖升 critical」。
- 置信度：80%
- 优先级：**LOW**

### L3. `265766c9` 引入的过渡文件未在后续 commit message 中宣告废弃

- 位置：git 历史 `review_prompt.md`
- 现象：后续 feat 未 `D` 该文件，message 也未写 deprecate。
- 影响：历史检索易误用。
- 建议：删除时在 commit message 标明 supersede。
- 置信度：90%
- 优先级：**LOW**

## 改进建议

1. **先修 C1**：选定 review 的真实 diff 源，并与「单 commit / commit 前 review」语义对齐；这是其余 review 机制可信的前提。
2. **删除或 DEPRECATED `review_prompt.md`**（H1），避免双系统并存。
3. **理顺 Round 1 PASS / Round 2 / step 7 前置**（H2）与**局部重审**（H3）。
4. 新 prompt 补「零发现合法」「finding 边界」；统一 working tree / git range 用词。
5. `log.md` 模板加 `diff_anchor`；README 补 `schemas/`、`config/`；修「辱界」笔误；统一 finding 标题分隔符。
6. 可选：把 step 5–6 写成显式状态机（round、何时 adoption、何时停），降低 agent 歧义。

## 不确定项 / 可能误报

### U1. C1 是否被「隐性 WIP commit」实践消解

- 说明：若团队实际在 step 3 后随手 commit、step 8 再 amend/squash，则 `anchor...HEAD` 非空。AGENTS/README 未写此惯例；按字面仍是 CRITICAL。若作者意图是 B 方案，应降为文档缺失（HIGH）而非逻辑自洽错误。
- 置信度：75%

### U2. 严格模式要求 minor 也必须修，是否故意的重流程

- 说明：风格类 minor 强制修 + 最多 2 轮，可能导致大量无功能收益的往返。可能是刻意的质量闸门，也可能未考虑 agent 成本。未标 finding，仅作产品取舍观察。
- 置信度：70%

### U3. working tree 上已删除 `review_prompt.md` 是否表示作者已知 H1

- 说明：未提交删除暗示清理进行中；本报告仍对 **已提交 HEAD** 报 H1。若下一 commit 删除该文件，H1 可关闭。
- 置信度：90%

### U4. merge commit `28fcff34` / `0087c52a` 未单独深挖分支内历史

- 说明：审阅以 7.20 当日 commit 与终态为主；`docs_spike_rule` 分支上更早的多轮审阅落地（`807f687` 等）未逐条重审。终态若已覆盖则无增量风险。
- 置信度：85%

## 按 commit 简评（非 finding，便于对照）

| Commit | 评价 |
|--------|------|
| `28fcff34` | 大重构落地：task/spec/review 结构成型，合理 |
| `cf7fd156` | 目录表与 `{test_cmd}` 占位清晰，正向 |
| `7c034bb3` | schema 落点表实用；README 未跟进（M6） |
| `265766c9` | 工作流前移 spec/plan 合理；引入日后过期的 `review_prompt.md` |
| `0087c52a` | merge，无独立内容 |
| `f7ac4a6e` | 措辞微调，无问题 |
| `703b23d4` | step 引用与分支约束修复，正确 |
| `eddd2b31` | 收窄 reviewer 轴正确；旧 prompt 未同步 |
| `ba66be5f` | 能力增强最大（verdict/Round/严格模式/双 prompt）；同时固化 C1/H2/H3，且未清理旧 prompt |

## 总结

7.20 提交把模板仓库的 task review 从「双 agent + 文档轴」推进到「code/test 双轴 + verdict + 严格 adoption + 有上限重审」，方向正确，字段与 conventions 大体同步。

**必须先处理**：review 的 diff 源与 commit 时序（C1）、旧 prompt 残留（H1）、Round/收尾前置（H2）、局部重审定义（H3）。其余为一致性与可执行性打磨。

未改任何被审源文件；本文件为唯一写入。
