## 当前模型判断依据

可观测配置中，`/home/karon/.claude/settings.json` 顶层 `model` 为 `opus`；`env.ANTHROPIC_MODEL` 为 `default_model`，并配置 `ANTHROPIC_DEFAULT_HAIKU_MODEL=default_haiku[1m]`、`ANTHROPIC_DEFAULT_SONNET_MODEL=default_sonnet[1m]`、`ANTHROPIC_DEFAULT_OPUS_MODEL=default_opus[1m]`。主会话可见标识仅为 `default_model`。综合判断：当前路继承主会话，配置意图为 opus，但无法从可观测信息确认实际后端精确模型或版本；不声称读取到运行时内部状态。

## 审阅范围

- 审阅 commit：
  - `28fcff34a4f422ae12077f5d8a669186564a0980`
  - `cf7fd1566c1710fbc5b4aa8b17951a2b46d9a4fa`
  - `7c034bb3131701173afc5780347c51460c8160e5`
  - `265766c9409e996186198ec4e48e9206f9bca495`
  - `0087c52ab78c3ce883992d55701d6c62c48af908`
  - `f7ac4a6e56d63ac18f0187781d3d84c67d7cb6ca`
  - `703b23d40a897cb60eebb642d6952d95bcf95446`
  - `eddd2b31598358ad9a81b15b5c3f49a3f7d4d61f`
  - `ba66be5ffeb0decc94e5cb92e9accd7bb6382a2a`
- 按每个 commit 相对第一父逐项检查 diff；两次 merge 同时核对第二父与 merge tree，未发现 merge 漏项或回退：`28fcff3` 与其第二父在目标路径无差异，`0087c52` 与其第二父在目标路径无差异。
- 全量检查目标 11 路径：`AGENTS.md`、`config/.gitkeep`、`docs/blueprint/conventions.md`、`docs/specs_index.md`、`docs/templates/task/adoption.md`、`docs/templates/task/review.md`、`docs/templates/task/review_prompt.md`、`docs/templates/task/review_prompt_code.md`、`docs/templates/task/review_prompt_test.md`、`docs/templates/task/task_report.md`、`schemas/.gitkeep`。
- 仅使用 commit 快照和 commit diff。当前未提交删除 `D docs/templates/task/review_prompt.md` 未纳入判断，未恢复、覆盖或修改。
- 按要求未运行构建或测试。

## 高优先级问题（CRITICAL / HIGH）

### HIGH：Round 1 零 finding 时流程无法满足收尾前置条件

- 位置：`ba66be5ffeb0decc94e5cb92e9accd7bb6382a2a:AGENTS.md:64,73`
- 现象：step 5 规定 Round 1 `PASS` 时“跳过 step 6 直接进 step 7”；step 7 又要求“两 reviewer Round 2 均 `verdict: PASS`”才可收尾。Round 1 clean review 不会产生 Round 2，因而不满足 step 7 明文前置条件。
- 影响：正常零 finding task 会卡死；agent 只能违反“跳过 step 6”、伪造 Round 2，或违反收尾门槛。`task_report.md` 还固定要求记录 Round 2 verdict，进一步强化冲突。
- 建议：统一为“每个轴最后一轮 verdict 均 PASS；若 Round 1 两轴均 PASS，可直接收尾”；`task_report.md` 改为“最终 verdict / 最后一轮 verdict”，不要强制 Round 2。
- 置信度：高
- 优先级：HIGH

### HIGH：2 轮上限与 Round 2 FAIL 的回环要求互相冲突

- 位置：`ba66be5ffeb0decc94e5cb92e9accd7bb6382a2a:AGENTS.md:71-72`
- 现象：line 71 规定 Round 2 `FAIL` 回 step 6“继续”；line 72 紧接着规定同一 task 最多 2 轮，Round 2 `FAIL` 后不得继续 review，必须等待用户决策。
- 影响：状态机无唯一下一状态。owner 可能继续修复但无法合法发起 Round 3，也可能提前停止；报告、adoption 和 task 状态将不一致。
- 建议：把 Round 2 FAIL 单独定义为 `blocked` 终态：不得回 step 6 自动循环；记录 blocker，等待用户选择“允许额外轮次 / 拆 task / 重写 / 显式降级”。若选择额外轮次，应明确轮次上限被用户覆盖及报告命名规则。
- 置信度：高
- 优先级：HIGH

### HIGH：review 基线可能把并行或基线后提交混入 task diff

- 位置：`ba66be5ffeb0decc94e5cb92e9accd7bb6382a2a:AGENTS.md:39,56,60`；`ba66be5ffeb0decc94e5cb92e9accd7bb6382a2a:docs/blueprint/conventions.md:55-56`
- 现象：task 声称使用独立分支，但流程只要求记录“当前 HEAD SHA”，review 固定执行 `git diff <diff_anchor>...HEAD`，没有要求创建/切换 task 分支、验证当前 branch、保证 `diff_anchor` 是 task branch fork point，也没有处理 task 开始后合并主线或其他 commit 的情况。
- 影响：若 task 未实际切分支，或期间 merge/rebase/引入其他 commit，review diff 会包含非本 task 变更；reviewer 可能对他人改动出 finding，owner 又被严格模式强制修改，破坏工作区隔离和“一 task 一 commit”。
- 建议：step 1 明确创建并切换 `task_tnnn_slug`，校验 branch；记录 fork point/merge-base，并在 review 前验证 `git rev-list` 仅含本 task 预期提交。若允许同步主线，改用能精确标识 task patch 的基线或明确排除同步 commit。
- 置信度：高
- 优先级：HIGH

### HIGH：未完成 task 的 `docs/specs/` 可在单 task 收尾中提前写入

- 位置：`ba66be5ffeb0decc94e5cb92e9accd7bb6382a2a:AGENTS.md:11-12,74`；`ba66be5ffeb0decc94e5cb92e9accd7bb6382a2a:docs/blueprint/conventions.md:103`
- 现象：目录规则和 conventions 明确要求 `docs/specs/<slug>.md`、`docs/specs_index.md` 仅在“需求全部 task done”后首次写入；但每个单 task step 7 又要求更新“本次 task 受影响文档”，明确列出 `docs/specs/`，没有“仅最后一个 task”条件或需求级 finalization 流程。
- 影响：agent 可能在前置 task 完成时提前固化尚未完成需求，造成生效 spec 与实际 task 状态冲突；也可能为遵守目录规则而跳过 step 7，形成不可判定分支。
- 建议：从普通单 task 收尾列表移除 `docs/specs/`，增加“仅当该 task 是需求最后一个 task 时，执行需求级固化：写 spec、更新 specs_index”；其他 task 只更新 task spec/plan 和允许更新的长期文档。
- 置信度：高
- 优先级：HIGH

## 中低优先级问题（MEDIUM / LOW）

### MEDIUM：遗留项批准与 Round 2 verdict 的语义不一致

- 位置：`ba66be5ffeb0decc94e5cb92e9accd7bb6382a2a:AGENTS.md:65,70,73`；`ba66be5ffeb0decc94e5cb92e9accd7bb6382a2a:docs/templates/task/task_report.md:13`
- 现象：规则规定任一未修 finding 即 `FAIL`，遗留项即使获用户批准仍是未修 finding；step 7 却允许“遗留项经用户显式批准保留”进入收尾。`task_report.md` 又把 Round 2 `FAIL` 与“降级/拆 task/重写依据”放在同一占位符中，没有定义批准后 task/reviewer 最终状态。
- 影响：同一 task 可能以 `review FAIL` 状态标 `done`，破坏 verdict 作为质量门禁含义；后续自动检查无法区分“阻塞失败”和“用户接受风险”。
- 建议：增加独立状态，例如 `verdict: PASS_WITH_APPROVED_EXCEPTION`，或保持 reviewer `FAIL` 但 task 状态改 `done_with_exception` 并在 tasks_index 明确记录批准人、时间和 finding。不要把 `FAIL` 当普通收尾状态。
- 置信度：高
- 优先级：MEDIUM

### MEDIUM：严格模式强制采纳 reviewer finding，缺少误报纠正路径

- 位置：`ba66be5ffeb0decc94e5cb92e9accd7bb6382a2a:AGENTS.md:65,67,69`；`ba66be5ffeb0decc94e5cb92e9accd7bb6382a2a:docs/templates/task/adoption.md:5,20-24`
- 现象：owner 对所有 finding 只能“已修”或极少数“遗留”；明确禁止“无需修改”，也没有“误报 / 不成立 / reviewer 撤回”流程。Pre-Report Gate 只能降低误报概率，不能保证 finding 正确。
- 影响：reviewer 错判时，owner 被要求修改正确实现；可能引入回归、偏离 spec，或被迫伪造“已修”。严格模式把 reviewer 从检查者变成不可申诉的规格来源。
- 建议：保留严格处置要求，但增加受控争议流程：owner 提交证据，原 reviewer 复核并以追加记录撤回 finding；撤回不算忽略。只有 reviewer 撤回或用户明确裁决后才可不改代码。
- 置信度：高
- 优先级：MEDIUM

### MEDIUM：危险模式按语法命中，合法测试修改也会被硬阻断

- 位置：`ba66be5ffeb0decc94e5cb92e9accd7bb6382a2a:docs/templates/task/review_prompt_test.md:27-42,84,129`
- 现象：模板要求“任一命中必出 finding”，并把“删测试文件或 test 块”“`.fill()` 冒充输入”等定义为硬阻断。规则未要求先判断删除是否由 spec 明确要求、是否已被等价/更高层测试替代，也把 Playwright 等框架中标准文本输入 API `.fill()` 一概描述为“冒充输入”。
- 影响：合法重构、删除废弃行为测试、合并重复测试、标准 E2E 文本输入都会强制产生 finding；结合“所有 finding 必修”，task 可能无法完成或被迫保留无效测试。
- 建议：把危险模式改为“必须调查并说明”，仅在降低行为覆盖、规避真实交互或掩盖失败时定为 finding；明确 `.fill()` 对文本框输入合法，只不能替代拖拽、点击、键盘事件等 AC 明确要求的交互。
- 置信度：高
- 优先级：MEDIUM

### MEDIUM：已废弃的旧 reviewer 提示词仍留在最终 tree，且规则已冲突

- 位置：`265766c9409e996186198ec4e48e9206f9bca495:docs/templates/task/review_prompt.md:3-5,54-68`；最终仍存在于 `ba66be5ffeb0decc94e5cb92e9accd7bb6382a2a`
- 现象：`ba66be5` 新增 `review_prompt_code.md` / `review_prompt_test.md` 并让 AGENTS 指向新文件，但未删除或标记旧 `review_prompt.md` 废弃。旧文件仍要求审阅 working tree、引用错误的 `AGENTS.md step 6`、使用 `critical/high/medium/low` 四级和“无 critical/high 即 PASS”，还保留“文档+代码”轴；新规则使用 `critical/important/minor`、任何 finding 都 FAIL、代码/测试两轴。
- 影响：agent 或维护者按文件名直觉选中旧模板时，会执行完全不同 review 范围和 verdict 规则；仓库同时存在两套互斥状态机。当前工作区删除该文件不属于本次 commit 历史，不能视为已修复。
- 建议：在历史后续修复中删除旧文件，或替换为只含明确迁移提示的短 stub；同时全仓搜索并移除旧路径引用。
- 置信度：高
- 优先级：MEDIUM

### MEDIUM：任务分支要求没有对应生命周期步骤

- 位置：`703b23d40a897cb60eebb642d6952d95bcf95446:AGENTS.md:39`；最终保留于 `ba66be5ffeb0decc94e5cb92e9accd7bb6382a2a:AGENTS.md:39,56-80`
- 现象：`703b23d` 把独立分支写成硬性结构，但单 task 流程没有创建、切换、合并、删除分支步骤；tasks_index 只记录 branch 字段。active dropped 只说“不把半成品合入默认分支”，也不说明如何清理 task branch。
- 影响：要求不可直接执行，agent 可能只填写 branch 名却仍在当前分支工作；完成后 task commit 也没有规定如何进入目标分支，导致“一 task 一 commit”的交付边界不完整。
- 建议：补齐 branch lifecycle：创建/切换、验证基线、完成后合并策略、dropped 时保留或删除规则；若模板不负责合并，则明确外部 orchestrator 负责并给出接口状态。
- 置信度：高
- 优先级：MEDIUM

### LOW：reviewer 提示词要求校验“owner 指定的项目根”，但无对应占位符

- 位置：`ba66be5ffeb0decc94e5cb92e9accd7bb6382a2a:docs/templates/task/review_prompt_code.md:3,74`；`ba66be5ffeb0decc94e5cb92e9accd7bb6382a2a:docs/templates/task/review_prompt_test.md:3,81`
- 现象：两模板要求 `pwd` 必须等于 owner 指定的项目根，但模板占位符只有 `{TID}`、`{slug}`、`{spec_path}`、`{task_dir}`、`{diff_anchor}`，没有 `{project_root}`，正文也未提供具体根路径。
- 影响：reviewer 无法按字面完成校验，只能猜测仓库根或跳过步骤；在多 worktree/多 repo 场景可能审错目录。
- 建议：增加 `{project_root}` 占位符并要求派发前替换，或改为用 `git rev-parse --show-toplevel` 获取并与 `{task_dir}` 所属仓库核对。
- 置信度：高
- 优先级：LOW

## 改进建议

1. 用显式状态表替代散落跳转：`round_1_pass -> finalize`、`round_1_fail -> adopt -> round_2`、`round_2_pass -> finalize`、`round_2_fail -> blocked/user_decision`。
2. 区分三类结论：reviewer 技术 verdict、用户风险接受、task workflow 状态；不要用一个 `PASS/FAIL` 同时表达三者。
3. 为 task branch 和 diff 基线增加可执行校验，避免严格 adoption 修改范围外代码。
4. 建立唯一 reviewer 模板入口；旧模板删除或显式标记废弃，防止两套严重度和 PASS 门槛并存。
5. 把需求级 spec 固化从单 task 收尾中拆出，仅最后一个 task 或独立 finalization task 执行。

## 不确定项 / 可能误报

- `config/.gitkeep`、`schemas/.gitkeep` 仅用于保留空目录，本批 commit 未引入可判定问题。
- 两个 merge commit 在目标路径均与第二父一致，未发现 merge 结果遗漏或回退；若需审阅 merge 第一父之外更早分支历史，不属于本次指定 9 commit 范围。
- `review_prompt.md` 当前工作区已删除，但该删除未提交且明确排除；因此“旧模板仍存在”按指定历史终点 `ba66be5` 判断，不把工作区状态当修复。
- `.fill()` 是否构成错误取决于具体测试框架和 AC；本问题针对模板“一律硬阻断”的规则过宽，不主张所有 `.fill()` 都合法。
- “独立分支”可能由仓库外层工具自动创建；指定 commit 内未记录该外部保证，因此仍报告为模板自身不可执行。若存在强制 hook/orchestrator，可下调相关问题优先级。
