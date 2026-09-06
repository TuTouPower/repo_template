# 工作流瘦身提案：严格文件契约，放开 Agent 执行方式

> **状态：提案，未实施。** 记录日期：2026-09-06。
>
> 本文供用户审阅，不代表已裁决。现有实现和 `decision_log.md` 仍是当前行为真相；只有用户明确批准后，才拆 task 实施。

## 背景

用户希望保留严格的文件模板和确定性数据校验，但不喜欢大量宿主 hook、自研状态机和特别详细的 Agent 工作步骤。期望工作流只约束交付结果、文件写权和危险操作授权，其余由模型根据具体任务自行判断。

当前模板已经从“仓库约定 + 少量工具”逐渐演化成一套运行在 Git 上的任务编排系统：

- 15 个消费仓 skill，正文约 1730 行；
- attempt、ledger、goal、integration、monitoring、recovery、review 等状态机制约 2936 行；
- `merge_guard.py` 约 388 行；
- `track_worktree.py` 约 167 行；
- 24 个测试文件中，11 个直接覆盖 attempt、execution identity、goal、review scope、merge guard 或 worktree tracking 等特殊机制。

这与 `decision_log.md` L35 的方向存在张力：编程 Agent 能力会持续提高，工作流应保持薄而通用，不应不断用 hook、skill 和控制面替代 Agent 自身能力。

2026-09-06 的 commit `f87e81b` 修复了现有复杂工作流中的依赖继承、review 最终内容绑定和中断恢复问题。该 commit 是对当前架构的正确性修补，不代表应永久保留全部架构。如果本文获批，后续可主动删除其中不再需要的 recovery、review scope 和 retry budget 等机制。

## 目标原则

### 严格层

脚本只严格处理确定性、可机械判定的内容：

1. task、pending、finding 等文件模板结构；
1. ID 唯一分配；
1. front matter 字段和状态合法性；
1. AC 编号、必填字段和占位符；
1. task 依赖引用存在且无环；
1. 模板同步产物一致；
1. Markdown 格式、测试和项目静态检查。

### 自由层

以下决策交给 Agent，不建立额外状态机：

- 调试、阅读和实现顺序；
- 是否严格采用红绿重构顺序；
- 测试层级和测试命令组合；
- review 次数；
- commit 数量和拆分方式；
- 是否使用子代理；
- 如何处理中途发现；
- 上下文快耗尽时是否主动保存进度；
- Git 中断后的具体恢复命令。

### 最小安全边界

仍保留少量高价值边界：

- 未经用户明确同意，不 merge、不 push；
- 不修改无关 task 的状态和文件；
- 不读取或提交 secret；
- 需要用户产品决策、账号、密钥或不可替代环境时停止；
- 模板和状态文件只能经规定脚本修改；
- 有真实 merge 冲突或工作区归属冲突时停止，不猜测覆盖。

这些边界写成简短指令，不通过 token hook 或自研调度协议实施。

## 总体判断

当前设计中，真正值得保留的是：

- 严格文件模板；
- ID 分配锁；
- task 基本状态；
- worktree 隔离能力；
- 依赖图和拓扑计划；
- 模板同步；
- 测试和格式检查。

优先考虑删除的是：

- Claude 专属 hooks；
- attempt / execution_id / ledger 控制面；
- 自研 goal 外壳；
- 自研 merge transaction；
- review scope fingerprint 和轮次预算；
- “一个 task 恰好一个 commit”的机械门禁；
- 详细到命令顺序的 task-work/task-run 操作剧本；
- pending-record 的后台 worker 会话状态机。

## 建议一：删除全部 Agent hooks

### 删除 merge token hook 同意

建议删除：

- `repo/.repo_template/hooks/merge_guard.py`；
- `repo/.claude/hooks/merge_guard.py` 软链；
- `repo/.claude/settings.json` 中的 `PreToolUse`；
- `.claude/state/` 相关 ignore 和状态；
- `test_merge_guard.py`；
- `repo_sync.py` 中生成 merge guard 软链的逻辑；
- README、usage、architecture 中的 merge token 说明。

当前机制为了防一次未授权 merge，实现了命令解析、目标识别、一次性 token、过期、命令绑定和使用状态。它只覆盖 Claude Code 的 Bash，无法覆盖脚本内部 Git 或其它宿主；`task.py integrate` 又刻意走脚本内部 subprocess 绕过该 hook。

建议替换成一条普通规则：

> 未经用户明确同意，不 merge、不 push；误操作按 Git 原生机制恢复。

### 删除 worktree 状态栏 hook 拒绝

建议删除：

- `.claude/settings.json` 中的 `PostToolUse`；
- `.repo_template/scripts/track_worktree.py`；
- `.scratch/statusline_workdir.jsonl`；
- 对应同步和测试逻辑；
- `docs_repo/plans/worktree_statusline_tracking.md` 后续标为退役。

现有机制在每次 Bash 后启动 Python，解析宿主 payload、正则提取 tid、扫描 ledger，再写全局状态栏消费的 JSON。它依赖 Claude Code hook、全局 status line、session ID 和特定命令文本，不属于通用模板能力。

替代方式：

```bash
git worktree list
```

`task.py start` 继续直接输出 worktree 路径即可。

### 删除自动格式化 pre-commit hook 拒绝

建议删除：

- `.repo_template/hooks/pre-commit`；
- `repo_sync.py install-hooks`；
- `core.hooksPath` 管理；
- `test_precommit_hook.py`；
- AGENTS 中 commit 自动格式化和重新暂存的规则。

保留显式检查：

```bash
python3 .repo_template/scripts/md_format.py --check
```

格式严格可由 validator 或 CI 保证，不需要 commit 时隐式修改并重新暂存文件。这样也不会干扰 partial staging。

## 建议二：退役 attempt 控制面 拒绝

建议删除：

- `attempt reserve`；
- `attempt terminal`；
- `attempt report`；
- `attempt` 整数；
- `execution_id`；
- dispatch ledger；
- `ps`；
- `recovery`；
- overlap attempt 检查；
- terminal/report/integrated 事件；
- `review_limit` / `verify_limit`；
- handoff 中的 attempt 和 execution_id。

可能退役的模块包括：

- `repo_task/attempts.py`；
- `repo_task/ledger.py`；
- `repo_task/recovery.py`；
- `repo_task/control.py` 中 attempt、ps、ledger 部分；
- `repo_task/goal.py`；
- `repo_task/monitoring.py` 中 attempt 投影；
- `repo_task/integration.py` 中 exact identity 门禁。

### 理由

自动 dispatcher 已退役，executor 只有 `inline`。当前没有多个自动 worker 竞争同一 task，因此：

```text
tid + attempt + execution_id + branch + worktree + handoff
```

是在用多套身份表达同一次本地执行。

简化后使用：

```text
task tid + branch
```

需要不可变 provenance 时使用：

```text
branch + HEAD SHA
```

Git 已经提供身份、历史和恢复，不需要另建 append-only 运行账本。

## 建议三：使用 Git 原生恢复和事务 同意

最终采用 **`git merge --no-ff --no-commit`，先验证、后创建 merge commit**。

### 单 task 合并

```bash
python3 .repo_template/scripts/task.py integrate t001
```

工具只做确定性工作：

1. 检查当前在主仓主分支且工作区可合并；
1. 检查 task 分支存在、状态为 done、worktree 已安全清理；
1. 检查 handoff、review、AC evidence 和依赖 provenance；
1. 执行 `git merge --no-ff --no-commit <branch>`；
1. 重建派生 index，并把它放入同一个待提交 merge；
1. 提示 Agent 在当前合并结果上运行项目验证。

验证通过后创建唯一 merge commit；验证失败则：

```bash
git merge --abort
```

main 回到合并前状态，task 分支保留用于继续修复。不会先产生 merge commit 再进入模板自研的 awaiting-verification 状态。

### 链式合并

链由 Git ancestry 表达：

```text
main → t001 → t002 → t003
```

整链完成后只对链尾执行：

```bash
git merge --no-ff --no-commit t003_branch
```

合并前仍保留 aggregate gate，确认链成员、task 状态、handoff、review、依赖和分支 ancestry 合法；但不再维护：

```text
prepared → merged → indexed → awaiting_verification → complete
```

也不再需要 transaction JSON、成员 integrated batch 或模板自定义的多阶段 `--continue`。

### 冲突和中断恢复

完全使用 Git 原生状态：

```bash
git status
git add <resolved-files>
git commit       # 冲突解决且验证通过
git merge --abort
```

中断后由 `git status` 判断是否仍在 merge，不再让 Git 状态和 `integrate-chain.json` 成为两个真相源。

### 分支清理

merge commit 创建并验证成功后，检查成员分支均为 main 的祖先，再使用普通 `git branch -d` 删除。未完全合入或状态发生变化的分支保留并报告。

## 建议四：保留 review 核心闭环，只删除低价值附加机制 同意

第一版对 review 的删减过多。review 中不少机制直接保障最终交付质量，不应为了缩短流程而删除。

### 保留两类 finding ID

#### `docs/findings/dNNN`

这是跨 task 复用的已验证技术事实，全局 ID 应严格保留：

- task 归档后仍可稳定引用；
- 多个 task 可以复用同一事实；
- 一条目一文件适合 Git 历史和并发写入；
- 与待办 `pNNN`、task `tNNN` 的职责清楚分离。

#### task review finding ID

` tNNN_code_fNNN`、`tNNN_test_fNNN`、`tNNN_gen_fNNN` 应保留：

- review 报告与处置表可以精确关联；
- 多轮 review 不会覆盖旧问题；
- 可以区分 code/test/general 来源；
- checker 能发现报告中存在但尚未处置的 finding；
- 遗留 finding 可以稳定指向 `pNNN` 或 follow-up task。

可以缩短 ID 规则的重复说明，但不取消机器可关联性。

### 保留 PASS / FAIL / INCOMPLETE 三态

三态表达不同问题：

| 状态 | 含义 | 动作 |
| --- | --- | --- |
| `PASS` | 业务审阅通过，证据完整且对应当前内容 | 允许进入收尾 |
| `FAIL` | 存在未解决的重要业务问题 | 修复后重审；达到上限请用户决策 |
| `INCOMPLETE` | 报告、处置或 scope 证据不完整 | 补证或重审，不消耗业务失败轮次 |

如果只保留 PASS/FAIL，报告缺失、处置缺失和真正的实现缺陷会被混成一类。`INCOMPLETE + next_action` 能避免把证据问题误算成业务失败，也防止缺报告被静默放行。

### 保留 review scope fingerprint

scope fingerprint 防止以下情况：

```text
review PASS
→ Agent 又改代码、测试或交付文档
→ 最终提交仍沿用旧 PASS
```

当前实现已经统一了 worktree、staging、commit 和 task archive 前后的指纹口径，并覆盖未跟踪文件、文件 mode 和 symlink。这是可靠性门禁，应保留。

skill 不需要解释指纹算法，只需说明：

- reviewer 报告写回 `reviewed_scope`；
- 最终内容变化会让旧 PASS 失效；
- checker 和 integrate 共用同一实现；
- scope stale 时必须重新审当前内容。

### 保留 Review 处置表和 fix_ref

处置表应继续使用：

- `已修`；
- `遗留`；
- `撤回`。

每个结构化 finding 都必须有处置。`遗留` 必须指向 `pNNN` 或 follow-up tid，避免 task 归档后问题失踪。critical/important 未解决时不得 PASS。

可简化的是模板说明：

- 三种状态各用一句话定义；
- 只保留一张 Round 表格示例；
- “未进表提示”、spec drift 等边缘规则移到 usage；
- 撤回只要求清楚理由，不必在多个文件重复写同一段话。

### 保留 full/single review 分级

高风险 task 使用 code + test 两路审阅，普通 task 使用 general 单路审阅，这一分级有效降低成本，应保留。

建议继续使用：

- 安全、鉴权、资金、并发、迁移、协议兼容：`full`；
- 普通行为变更、文档和配置：`single`；
- 是否增加 `none` 另行裁决，不在本轮顺手加入。

### 保留最大 review 轮次，但简化预算机制

无限 review 循环可能发生，默认最大轮次仍应保留，例如 5 轮。达到上限后停止并请用户决定。

可以简化：

- `withdraw_rate` 是统计指标，不是可靠性门禁，删除或移到离线分析；
- `prompt_hint` 自动调参收益间接，可以删除；
- `review_limit` 和 `verify_limit` 两个持久字段都保留，分别约束 review 与黑盒重试；
- 新 attempt 不重置历史轮次或上限；
- 用户批准追加轮次时，用独立的只增上限命令记录新的绝对值和原因；
- 不再用 resume 状态机表达“多给几轮”。

### Review 机制裁决表

| 机制 | 结论 | 理由 |
| --- | --- | --- |
| `dNNN` 全局 findings | 保留 | 跨 task 技术事实账本 |
| task review finding ID | 保留 | 报告与处置精确关联 |
| PASS/FAIL/INCOMPLETE | 保留 | 区分业务失败与证据缺失 |
| `next_action` | 保留 | INCOMPLETE 有确定恢复动作 |
| scope fingerprint | 保留 | 防 PASS 后继续改产物 |
| 处置表 | 保留，缩短说明 | 防 finding 丢失 |
| 遗留 `fix_ref` | 保留 | 归档后仍可跟踪 |
| full/single | 保留 | 风险分级有效 |
| AC evidence | 保留 | 验收标准与证据闭合 |
| `review_limit` / `verify_limit` | 两个都保留 | 分别防 review 和黑盒无界循环 |
| withdraw rate | 删除或离线化 | 统计指标，不是门禁 |
| prompt hint 自动调参 | 删除或离线化 | 增加协议复杂度，收益间接 |
| 大量 Round 文案规则 | 简化 | 保留历史和最终结果即可 |

## 建议五：task-work 结果导向，但保留测试、黑盒和 review 硬门禁 同意

简化 task-work 不能以牺牲黑盒验证为代价。正确方向是：

> **结果门禁保持严格；实现、调试和验证准备方式由 Agent 自主决定。**

### 必须保留的三道门禁

#### 门禁 A：项目测试

- 必须运行 `docs/blueprint/testing.md` 定义的相关测试；
- 测试命令未定义时，明确记录为项目配置缺口，不能静默写“通过”；
- 代码或测试在验证后变化，应重新运行相关测试；
- handoff 保留测试结果摘要；
- 禁止通过弱化断言、mock 掉被测逻辑或只修改预期制造假绿。

#### 门禁 B：黑盒验证

- `{blackbox_verify}` 有定义时必须执行；
- 黑盒验证应触达用户或调用方可观察行为，而不是只看内部函数；
- 黑盒失败不得进入 review PASS、finish 或执行 commit；
- 修复后必须重新黑盒验证；
- 项目未定义黑盒命令时必须明确写“未定义”，不能默认通过；
- handoff 保留黑盒结果摘要。

#### 门禁 C：独立 review

- 按 `review_level` 执行；
- 最终内容必须对应 PASS scope；
- FAIL finding 必须处置；
- INCOMPLETE 必须补证；
- review 后改产物必须重新 review。

这三道门禁应在 skill 中醒目标出，不能删掉，也不应被大量操作细节淹没。

### TDD 改为强默认，不做绝对状态机

当前红→绿顺序是好实践，但不是所有任务都适用，例如纯配置、文档、工具链迁移、先实验才能确定接口的 spike、修正错误测试本身。

建议改为：

> 对可测试的行为变更，默认先建立能失败的测试或复现证据，再实现；不适用时在实施笔记简述原因。

保留“测试必须触达生产逻辑”和“不得制造假绿”，但不要求 Agent 为每种任务机械执行完全相同的红绿步骤。

### 删除固定 cleanliness grep

当前固定搜索 `print`、`pprint`、`TODO`、`FIXME`、`XXX` 只适合部分技术栈，也可能误报合法代码。

改为结果要求：

- 检查新增调试输出、临时文件和未解释 TODO；
- 运行项目定义的 lint、format、typecheck；
- 运行 `git diff --check`；
- Agent 根据项目技术栈选择检查方法。

项目确实需要固定 grep 时，将命令写进该项目 `testing.md`，不放在通用 skill。

### 顺手发现只处理实际遇到的问题

当前每个 task 收尾都要求扫存量问题、扫假绿、调用 task-bug、抽 findings，容易把小 task 扩张成仓库审计。

建议改为：

> 实施过程中实际遇到的、确定有价值且不属于本 task 的问题，应登记 pending 或 finding；不要求为了收尾额外做全仓扫描，也不得把旁支问题静默修进当前 task。

`task-bug analysis-only` 保留为可调用能力，但不再是每个 task 的固定步骤。

### 推荐的 task-work 流程

目标不是缩到极短，而是约 90～120 行，保留可靠性顺序：

1. 确认 worktree、tid、spec 和当前状态；
1. 跑 preflight，处理真正阻塞的未知契约；
1. Agent 自主实施，TDD 为强默认；
1. 项目测试硬门禁；
1. 黑盒硬门禁；
1. 更新受影响的交付文档；
1. 独立 review 硬门禁；
1. 处置 finding，PASS/FAIL/INCOMPLETE 按 checker 输出处理；
1. 写 task.md、handoff，finish 并形成一个执行 commit；
1. 交给 task-run cleanup/integrate。

简化的是微观命令剧本、固定技术栈检查和额外范围扩张，不是测试、黑盒和 review。

## 建议六：暂时保留“一 task 恰好一个执行 commit” 同意

取消该约束会影响当前大量设计，不适合作为普通文档瘦身顺手删除。

### 当前约束带来的便利

1. task 分支 tip 就是完整交付；
1. `diff_anchor..HEAD` 精确对应一个 task；
1. review scope 容易定义；
1. handoff 的 base/HEAD provenance 清楚；
1. chain ancestry 简单：每个 task 增加一个 commit；
1. cherry-pick、revert 和代码考古直观；
1. task 粒度失控会及时暴露，而不是用大量小 commit 掩盖。

### 取消后需要修改的内容

- `base_sha == branch tip first parent`；
- `diff_anchor` 与执行 commit 的关系；
- `verify_integrate_ready`；
- handoff schema；
- cleanup gate；
- chain member 识别；
- review scope 最终 ref 推导；
- task-create 的原子性约定；
- task-work finish/commit 顺序；
- integrate-chain 的成员检查；
- 大量真实 Git 集成测试。

迁移成本较高，而且取消后还需要定义“哪些 commit 属于这个 task”的新规则。

### 已知缺点

- 长 task 不能用正式 commit 保存中间检查点；
- 变更会积累到最后；
- review 修复不能自然形成独立 commit；
- Agent 可能迟迟不 commit。

这些问题优先通过缩小 task 粒度解决。当前模板本来就是 specs-driven task 工厂，一 task 一个工程意义明确的 commit 与整体设计一致。

### 结论

**本轮保留“一 task 一个执行 commit”。**

只简化相关文字：

- skill 不反复解释 first-parent 数学关系；
- 脚本负责机械验证；
- task-work 只说明最终必须形成一个执行 commit，期间不要创建正式中间 commit；
- 详细恢复逻辑放脚本输出或 usage，不在多个 skill 重复。

如果后续实战持续证明中断风险明显，再单独设计“允许 WIP commit、finish 前 squash”的方案，不与本轮瘦身混做。

## 建议七：保留严格模板结构，主要缩短重复说明 同意

用户明确要求文件模板严格，因此本轮不删除 spec/task 的主要结构。简化重点是减少每个生成文件中重复的教学文字。

### spec.md 保留项

| 区块 | 结论 | 理由 |
| --- | --- | --- |
| 背景 | 保留 | 解释需求来源 |
| 范围 | 保留 | 控制 task 边界 |
| 非范围 | 保留 | 防实现扩张 |
| 验收标准 + AC ID | 严格保留 | spec 核心契约 |
| 可测试性声明 | 保留 | 防不可测 AC 被伪装成已验证 |
| 来源 | 保留 | pending/finding/task 链路 |
| 有意不测 | 保留 | 防 reviewer 重复要求明确不测场景 |
| 测试策略 | 保留，可写“按项目默认” | mock、fixture 和断言边界仍有价值 |
| 未知契约清单 | 保留 | 防 Agent 脑补第三方接口 |
| 风险与回退 | 保留 | 低风险可写“无” |
| 依赖与约束 | 保留 | 调度和实现都需要 |
| Finalization blueprint | 保留 | 防长期文档遗忘 |

### spec.md 具体简化

- 三段 AC `<!-- 规范 -->` 合成一个短规范块；
- 可观察行为、稳定 AC ID 和 `[deploy]` 各用一句话说明；
- 长篇解释移到 usage；
- 模板继续保留占位符，validator 继续检查结构、AC 和占位符；
- “只替换占位符、不得删除规范块”继续保留。

### task.md front matter

当前架构下保留：

- `tid`、`slug`、`title`、`status`；
- `branch`、`worktree`、`diff_anchor`；
- `review_level`；
- `depends_on`、`conflicts_with`；
- `note`。

`branch/worktree/diff_anchor` 仍被一 task 一 commit、worktree ownership 和恢复门禁使用，暂不删除。

用户已同意删除 blocked 状态，但 `review_limit` 与 `verify_limit` 两个字段继续保留。它们不再依赖 resume：用户批准追加轮次时，由独立的只增上限命令更新，并记录新的绝对上限与授权原因。

### task.md 正文

保留：

- 实施笔记；
- Review 处置；
- 收尾报告。

缩短：

- 三种处置状态各用一句话；
- Round 示例只留一张表；
- “未进表提示”、spec drift 等详细解释移到 usage；
- Reviewer verdict 按实际报告写摘要，不复制 full/single 两套长模板；
- 收尾报告只写结果、验证和遗留引用，不重复复制 AC 正文。

### 结论

本轮不删除模板主要字段和章节。目标是将模板正文缩短约 30%～45%，同时保持 validator、AC、review 处置和收尾证据不变。

## 建议八：谨慎缩减 task CLI 同意

第一版删除范围过大。CLI 中很多命令对应真实不同语义，不应为了数量少而合并。

### 删除正式 blocked 状态

删除：

- task 状态中的 `blocked`；
- `task.py block`；
- `task.py resume`。

但 **attempt 的业务结果 `report=blocked` 保留**。阻塞时：

```text
task.md status 保持 active
→ 实施笔记写阻塞原因、所需输入和恢复入口
→ 当前 attempt terminal=stopped
→ 当前 attempt report=blocked
→ 当前会话停止
```

用户补齐条件后：

```text
原 task 和 worktree 继续使用
→ 直接 reserve 新 attempt
→ 按原进度继续 task-work
```

不再通过 `blocked → active` 的 task 状态迁移表达重试。这样既保留 attempt 的执行审计，也减少一套业务状态。

### review/verify 上限继续持久化

`review_limit` 和 `verify_limit` **两个字段都保留**：

- `review_limit` 防 review 无界循环；
- `verify_limit` 防黑盒失败无界重试；
- 新 attempt 不重置历史轮次或上限；
- 用户批准追加轮次时必须记录新的绝对上限和原因。

删除 resume 后，新增一个不改变 task 状态的命令，例如：

```bash
python3 .repo_template/scripts/task.py limits t001 \
  --review 8 \
  --verify 7 \
  --reason "用户批准追加验证轮次"
```

该命令只允许在 task worktree 中对 active task **增加**上限，不允许降低或清零历史，并把授权理由写入 task note/实施记录。命令名实施时可调整，但语义固定。

### 删除低价值命令

| 命令 | 结论 | 理由 |
| --- | --- | --- |
| `ledger record` | 删除 | 目前只允许 note，价值较低；普通说明写 task 实施笔记 |

### 保留的命令

| 命令 | 结论 | 理由 |
| --- | --- | --- |
| `add` | 保留 | 严格模板和 ID 分配入口 |
| `edit` | 保留 | 避免手改 front matter 和调度边 |
| `start` | 保留 | 创建分支/worktree并写状态 |
| `preflight` | 保留 | 创建有效性、执行就绪和严格验证有不同语义 |
| `finish` | 保留 | active → done 和归档 |
| `drop` | 保留 | 明确终止并保留历史 |
| `rewind` | 保留 | 合法撤回 active 到 backlog |
| `purge` | 保留为低层命令 | 只删从未提交的误建 task，语义与 drop 不同；常用文档无需突出 |
| `list/show` | 保留 | 基本读接口 |
| `effective-status` | 保留 | 多 worktree/未合并分支下防读主干旧状态 |
| `view/plan` | 保留 | 手动并发模式的核心只读工具 |
| `cleanup-worktree` | 保留 | worktree 安全清理仍需要 |
| `integrate` | 保留 | 单 task 合并入口 |
| `integrate-chain` | 保留入口，内部改 Git 原生事务 | 链式执行核心功能 |
| `goal/goal-check` | 保留 | 建议九已拒绝 |
| `attempt reserve/terminal/report` | 保留 | exact cleanup、handoff、重试和恢复 |
| `ps` | 保留 | 观察 current attempt |
| `ledger tail` | 保留 | 恢复和审计依据 |
| `recovery` | 保留 | 判定 finish/commit/attempt 中断阶段 |

### preflight 三种语义保留

- `--creation`：文件是否可作为 backlog 入库；
- `--allow-backlog`：是否具备启动条件；
- `--require-verified`：未知契约是否已全部验证。

未来可以统一命名为：

```bash
task.py validate t001 --phase create|start|implement
```

但只能整合入口，不能丢掉三种检查层次。

### 结论

本轮 CLI 瘦身范围为：

1. 删除 task blocked 状态和 block/resume；
1. 保留 attempt 的 blocked report，并以新 attempt 恢复；
1. 保留两个 limit，新增独立的只增上限命令；
1. 删除低价值 `ledger record`；
1. integrate/integrate-chain 按建议三改用 Git 原生事务；
1. 其它命令和 goal 保留。

## 建议九：删除 goal 自研外壳 拒绝

建议删除：

- `task.py goal`；
- `task.py goal-check`；
- `docs/runtime/goal_queue.json`；
- ready-to-paste `/goal` 行；
- `GOAL_QUEUE_COMPLETE` / `STOPPED` / `INCOMPLETE` marker；
- goal 队列同时只能服务一个会话的限制。

用户可直接调用：

```text
/task-run t001 t002 t003
```

或使用宿主原生 goal / continuation 能力。固定队列可由 `task-run` 在会话开头记录，无需另建运行态 JSON。

## 建议十：pending-record 保留子代理，只简化交互协议 同意

第一版建议删除后台 worker 过于激进。pending-record 的核心场景是用户连续口述待办，子代理能让主会话保持响应，尤其 bug 分析可能耗时，应保留。

### 4a / 4b / 4c 的含义

#### 4a：普通条目新建

用于新功能、遗留和技术债。子代理负责：

- 再查重，避免与其它会话竞态；
- 调用 `pending.py new` 分配 `pNNN`；
- 填来源、内容和处理状态；
- 用户要求时 park；
- 回报 create/reuse/park。

#### 4b：bug 分析并登记或更新

这是最有价值的子代理路径：

- 收敛现象；
- 在 `.scratch/` 复现；
- 定位根因；
- 扫同类位点；
- 分析测试缺口；
- 新建或更新 bug pending；
- 不立项、不改生产代码、不 commit。

主会话可以继续接收用户口述，不必等待完整 bug 分析。

#### 4c：已有条目更新

查重命中已有 `pNNN` 且用户同意更新时，子代理：

- 只更新指定条目；
- 保留编号；
- 必要时 rename 或 park；
- 不新建重复条目；
- 回报更新摘要。

### 应保留的机制

- 4a / 4b / 4c 三种路由；
- bug 使用分析子代理；
- in-flight 主题参与查重；
- worker 写域限制；
- worker 禁止 commit 和生产修复；
- 提交前等待 worker 完成，或明确排除未完成项；
- session 结束时汇总本次已变更 pending。

### 真正需要简化的地方

1. 用户描述已经清楚时，不再强制额外做一次 3～6 行共享确认；一句复述后可直接派发，只有歧义、park 或覆盖已有条目时才确认；
1. in-flight 只记 `worker_id / topic / target pNNN（若有）`，不用把它描述成完整状态机；
1. session ledger 简化为“本次已变更路径 + 失败项”，不定义过多 action 状态；
1. 4a 和 4c 共用一段 worker 边界，减少重复禁止项，但保留两个路由名称以防 create/update 混淆；
1. 4b 只引用 `task-bug analysis-only`，不在 pending-record 复制完整 1～6 步；
1. 收尾屏障保留，但用一句规则表达；
1. 宿主没有后台子代理能力时，允许主会话顺序执行同一流程，不把“必须子代理”变成不可用条件。

### 目标形态

pending-record 仍然是：

```text
用户连续口述
→ 清楚则直接派 4a/4b/4c，歧义才问
→ 主会话继续接下一条
→ worker 回报 pNNN/失败
→ 结束时等待或排除 in-flight
→ 用户批准后统一 commit
```

目标从 167 行缩到约 100～120 行，保留子代理和并发价值，而不是改回主会话逐条阻塞写文件。

## 建议十一：保留依赖图，但降级为建议系统 同意

建议保留：

```yaml
depends_on:
conflicts_with:
```

`task-schedule` 继续由 Agent 分析依赖，`task.py plan` 做拓扑排序。

建议删除或放松：

- `schedule_status`；
- 冲突反向边的复杂维护；
- 传递依赖与冲突边冗余的硬拒；
- dep-ready 小编号优先规则；
- 停滞哨兵；
- 多父汇流特殊状态；
- 每次 edit 后全图复杂一致性修复。

最小算法：

1. `depends_on` 有环时报错；
1. 未满足依赖的 task 暂不建议执行；
1. `conflicts_with` 只影响并发建议，不影响串行执行；
1. 多会话并发由用户自行选择；
1. 实际 Git 冲突交给 Git 和 Agent 处理。

## 建议十二：简化模板同步 拒绝

不同意删除或合并同步能力。以下行为全部保留：

- 启动器先刷新 `repo-template-sync-core`；
- status / plan / apply 分阶段；
- 模板源解析和缓存；
- 硬同步工具链；
- AGENTS、`.gitignore`、MCP 等共享文件裁定；
- 手写文件保护；
- apply 失败回滚；
- apply 后运行测试；
- sync state 推进；
- 用户审批后才 commit。

允许的简化仅限 `repo-template-sync` 和 `repo-template-sync-core` 两份 SKILL.md 的重复文字：脚本已经机械处理的文件分类、回滚内部细节和长汇报模板可改为引用脚本输出或 usage。同步行为、边界、审批和失败语义不得改变。

## 建议十三：逐个 skill 精简，保留各自真正有价值的门禁 同意

以下不是要求每个 skill 都越短越好，而是删除重复叙述，让每个 skill 聚焦自己的决策。

### 1. `task-create`

#### 当前有价值的部分

- 查重；
- 需求澄清；
- 原子 task 拆分；
- 严格填写 spec；
- review_level 风险判断；
- 创建有效性 preflight；
- 用户批准后统一创建 commit。

#### 当前问题

- 路由表、模板规则、未知契约分类和 preflight 规则重复 usage/模板；
- “拆分三问”写得过于仪式化；
- 强制每个问题都附推荐答案，不适合没有可靠推荐的产品选择；
- 对 commit 文件清单说明过细。

#### 建议改法

保留五步：

1. 查重和澄清；
1. 拆成独立可验收 task；
1. 用 `task.py add` 创建并严格填写模板；
1. `preflight --creation`；
1. 列出 task，请用户批准创建 commit。

模板字段解释只引用 spec 模板和 usage，不在 skill 复制。拆分三问改成简短质量检查：AC 可证伪、task 有单一交付结果、依赖最小。

目标约 45～55 行。

### 2. `task-schedule`

#### 当前有价值的部分

- 分析 depends_on 和 conflicts_with；
- 考虑 active worktree 实际改动面；
- 不把冲突误写为依赖；
- 用 `task.py edit` 写图；
- `view/plan` 验证；
- 用户批准调度 commit。

#### 当前问题

- skill 内复述了调度算法的大量内部判定；
- 规定逐个 Git 命令扫描 branch/worktree，限制 Agent选择读取方式；
- 与 `scheduling.py`、architecture 重复；
- 输出格式和提示命令过细。

#### 建议改法

skill 只要求：读取 spec 和当前有效改动面，写真实硬依赖和并发冲突；不确定标待澄清；通过脚本写入并运行 view/plan。调度算法细节留在 architecture 和脚本测试。

目标约 50～65 行。

### 3. `task-preflight`

#### 当前有价值的部分

- 只读；
- 使用 effective source，避免读主干旧状态；
- 区分机器门禁和用户侧缺口；
- 检查密钥存在性但不读值；
- baseline 健康检查。

#### 当前问题

- 输入组合、每种来源命令和输出表模板写得太长；
- 重复解释 UNVERIFIED 分类；
- baseline 失败的所有分支过细。

#### 建议改法

保留：确定有效来源、跑对应 preflight、跑 baseline、列用户不可替代缺口。输出表格式可由 Agent 自行组织，只要求 tid、状态、来源、门禁结果和用户动作。

删除 blocked 后同步删 blocked 输入和处理。

目标约 55～70 行。

### 4. `task-run`

#### 当前有价值的部分

- 固定用户指定队列；
- 后继从前一 task 分支继承；
- 每 task 调 task-work；
- 全链完成后再询问 merge；
- 停止时保留现场。

#### 当前问题

- 同时承担队列解析、goal 模式、attempt 生命周期、恢复分类、blocked 重试、integrate transaction 说明；
- 大量内容实际属于 task-work、task-integrate 或脚本；
- “禁止 plan mode”“不得因上下文主观停止”等在限制 Agent 思考方式；
- 详细命令编号导致调用方机械照抄而不是观察状态。

#### 建议改法

删除 goal 和 blocked 后，skill 只保留：

1. 冻结本次 tid 顺序；
1. 对每个 task：恢复已有 worktree或 start，调用 task-work，完成后安全 cleanup；
1. 后继以已完成前一分支为 base；
1. 任一 task 需要用户输入或失败则停止并报告剩余队列；
1. 全部完成后询问一次是否调用 task-integrate 合链尾；
1. 不 push。

如果 attempt 暂保留，只写一句“生命周期命令由 task.py 输出的 exact identity 驱动”，不在 skill 复制完整恢复表。

目标约 60～80 行。

### 5. `task-work`

#### 当前有价值的部分

- worktree 写域；
- preflight；
- 测试、黑盒、review 三门禁；
- review finding 处置；
- 最终文档和 handoff；
- 一个执行 commit；
- 不 merge、不 push。

#### 当前问题

- 223 行，门禁被大量步骤细节淹没；
- TDD 被写成绝对状态机；
- cleanliness grep 技术栈特化；
- 强制扫描存量问题和假绿扩大范围；
- 强制 task-bug analysis-only；
- 恢复入口表和 task-run 重复；
- 对每种 review 回流路径逐条规定。

#### 建议改法

保留测试、黑盒、review 为醒目硬门禁；TDD 改强默认；删除固定 grep和额外全仓扫描；实际遇到的重要旁支问题才登记。review checker 的 PASS/FAIL/INCOMPLETE 规则保留，但用一张小表表达。

一个执行 commit 暂保留。

目标约 90～120 行。

### 6. `task-integrate`

#### 当前有价值的部分

- merge 必须获用户授权；
- 合并前整体校验；
- 单 task和链式入口；
- 合并后验证；
- 冲突无法判断时停；
- 不 push；
- 分支只有验证通过后删除。

#### 当前问题

- 175 行主要在解释自研 transaction phase；
- exact identity、handoff schema 在 task-work、usage 和脚本重复；
- Git merge 状态与 transaction 双重恢复；
- index 独立 commit又增加阶段。

#### 建议改法

保留 aggregate gate、review/handoff 基本校验和合并后验证；将 transaction 改为 Git 原生 `merge --no-commit` 路径，或至少把 phase 细节移到脚本 help，不在 skill 展开。冲突处置表可保留，因为有实际语义价值。

目标约 65～90 行。

### 7. `task-bug`

#### 当前有价值的部分

- 先复现再定根因；
- 同类位点扫描；
- 测试缺口分析；
- pending 登记；
- 立项前用户批准；
- analysis-only 调用模式。

#### 当前问题

整体并不算过度复杂。主要是同类扫描说明较长，与 pending-record 4b 重复。

#### 建议改法

保留流程，不删能力。pending-record 4b 只引用 `task-bug analysis-only`，不复制其 1～6 步全文。task-bug 内部把“怎么找同类位点”缩成示例列表。

目标约 55～65 行。

### 8. `task-from-pending`

#### 当前有价值的部分

- 核实现状而不是机械转 task；
- 查重和聚合；
- bug 必须有根因/同类/补测；
- 建 task 后归档 pending；
- parked 不自动捞。

#### 当前问题

- bug A/B/C 分诊和子代理细节较多；
- 与 task-bug、task-create 重复模板要求；
- 用户确认规则分散。

#### 建议改法

保留核实、去重、聚合、bug 分析和归档。具体建 task 直接引用 task-create；bug 直接调用 task-bug analysis-only。只在范围争议或 bug 需要深分析时询问。

目标约 45～60 行。

### 9. `task-merge`

#### 当前有价值的部分

- 只合并 backlog；
- 用户确认合并范围；
- AC 和上下文合并；
- 源 task drop 保留历史；
- 调度边失效后重跑 schedule。

#### 当前问题

- 清除每类边的命令细节较多；
- 合并后所有字段处理与 task.py edit 能力重复；
- 输出和 commit 文件清单过细。

#### 建议改法

保留语义门禁，新增或增强一个脚本命令机械执行“合并来源、重写引用、失效调度图、drop 源”。skill 负责判断 AC 是否矛盾和用户确认，不负责逐条描述图维护算法。

目标约 45～55 行。

### 10. `pending-record`

#### 当前有价值的部分

- 连续口述模式；
- 后台子代理；
- 4a普通、4b bug、4c更新；
- in-flight 去重；
- worker 写域限制；
- commit 屏障。

#### 当前问题

- 每项强制二次共享确认；
- 三个 worker 路由复制大量禁止项；
- session ledger 状态定义过细；
- 不支持后台 Agent 的宿主缺少回退；
- bug 规则重复 task-bug。

#### 建议改法

保留三个路由和子代理。清楚输入直接派，歧义才问；4b 只引用 task-bug；共享一段 worker 边界；in-flight 只记必要字段；收尾保留屏障；无后台能力时允许主会话顺序执行。

目标约 100～120 行。

### 11. `repo-cleanup`

#### 当前有价值的部分

- 默认 dry-run；
- 分类清理；
- keep 保护；
- 不误删业务数据。

#### 当前问题

73 行大部分是脚本参数映射，Agent 可以从 `--help` 获取。

#### 建议改法

skill 只写：先 dry-run、用户确认范围、执行 apply、报告删除项；列出永不触碰的目录。参数细节交脚本 help。

目标约 30～40 行。

### 12. `repo-hygiene`

#### 当前有价值的部分

- 处理过时 handoff/pending/spike/review；
- archive 只追加；
- 不自动处理 parked；
- 不误动模板；
- 迁移前 dry-run。

#### 当前问题

- 每种文档迁移的具体命令和判定都写在一个 skill 中；
- 与 repo_hygiene.py help、usage 重复；
- 一些边缘恢复说明过长。

#### 建议改法

该 skill 涉及迁移和删除，不能过度缩短。保留对象分类、确认规则和保护路径；具体命令示例每类只留一个。目标约 65～80 行。

### 13. `repo-template-sync`

#### 当前有价值的部分

- 启动器先更新 core，防旧同步流程漏新路径；
- 自身职责单一。

#### 当前问题

29 行已经较薄。唯一问题是首次源推断说明略多。

#### 建议改法

同步能力和流程完全不变，只删重复背景和已由 core 明确的说明。目标约 20～25 行。

### 14. `repo-template-sync-core`

#### 当前有价值的部分

- status/plan/apply 分离；
- 共享文件需裁定；
- apply 后测试；
- 用户批准才 commit；
- 回滚和 state 推进。

#### 当前问题

- 199 行，详细解释每类文件、每种裁定、站立指令和汇报模板；
- 很多机械规则应由 repo_sync.py 实现并输出；
- skill 同时像使用手册和操作协议。

#### 建议改法

同步能力和流程完全保留：确认源、status、plan、用户裁定、apply+test、state 推进和审批 commit 都不变。只把 repo_sync.py 已机械实现的文件分类、回滚内部算法和重复汇报模板改为引用脚本输出；skill 继续解释 Agent 必须判断的 AGENTS/MCP/共享文件冲突。

目标约 90～120 行，以不改变任何同步语义为前提。

### 15. `template-issue-report`

#### 当前有价值的部分

- 区分模板问题和业务问题；
- 只描述现象，不替模板仓定根因；
- 本地完整版和公开脱敏版；
- secret/路径脱敏；
- gh 不可用时回退。

#### 当前问题

- 报告模板与步骤说明有重复；
- 对模糊表述的禁止示例较多；
- gh 命令细节可缩短。

#### 建议改法

保留报告模板和脱敏硬要求，这是严格文件模板的典型合理场景。流程缩成确认现象、判断归属、写两版、发布或回退、报告路径。

目标约 70～85 行。

## 推荐目标架构

### 保留的可靠性核心

```text
严格 task/spec/pending/finding 模板
ID 分配锁与 AC 编号
backlog / active / done / dropped 状态
Git worktree 隔离与 effective status
attempt / execution_id / ledger / ps / recovery
goal / goal-check
review finding ID、PASS/FAIL/INCOMPLETE、scope、处置与 AC evidence
review_limit / verify_limit
一 task 一个执行 commit
depends_on / conflicts_with / view / plan
repo-template-sync 完整能力、回滚、测试和审批
pending-record 4a/4b/4c 子代理与提交屏障
```

### 计划删除或替换

```text
merge token hook                         → 简短 merge 授权规则
正式 blocked task 状态 + block/resume    → active task + attempt report=blocked
ledger record note                       → task 实施笔记
自研 integrate-chain transaction phases  → git merge --no-ff --no-commit
withdraw_rate / prompt_hint               → 删除或仅留离线分析
固定技术栈 cleanliness grep              → 项目 testing.md / Agent 按栈选择
强制全仓旁支问题扫描                     → 只登记实际遇到的重要旁支问题
skill 中重复的脚本算法和恢复剧本          → 脚本 help / usage 单一说明
```

### 明确保留的 hooks

- worktree 状态栏 PostToolUse hook；
- Markdown pre-commit hook。

只删除 merge token PreToolUse hook及其状态、同步和测试。

### 一条 task 的目标流程

```text
task-create
→ 可选 task-schedule / task.py plan
→ task-run
    → start / reserve attempt
    → Agent 自主选择实现方法
    → 项目测试硬门禁
    → 黑盒硬门禁
    → review 三态与 scope 硬门禁
    → finish + 一个执行 commit
    → terminal/report + cleanup
→ 用户批准后 integrate
    → git merge --no-ff --no-commit
    → 合并后验证
    → 通过后 commit，失败则 abort
```

## 分阶段实施建议

### 第一阶段：删除低争议重复机制

1. 删除 merge token hook，保留另外两个 hooks；
1. 删除 review 的 withdraw_rate 和 prompt_hint；
1. task-work 删除固定 cleanliness grep和强制全仓旁支扫描；
1. pending-record 保留子代理，减少重复确认和重复协议；
1. 缩短 task/spec 模板说明，不改字段和 validator；
1. 逐个精简 skill，将脚本细节移到 help/usage；
1. 同步流程和能力不变，只精简两个同步 SKILL.md 的重复文字。

### 第二阶段：状态和合并简化

1. 删除 task blocked 状态和 block/resume；
1. 保留 attempt report=blocked，定义 stopped + blocked → 新 attempt 的恢复路径；
1. 保留 review_limit 和 verify_limit，增加独立只增上限命令；
1. 删除 ledger record；
1. integrate/integrate-chain 改为 Git `--no-commit` 事务；
1. 保留 aggregate gate、handoff/review/AC 校验和合并后验证；
1. 更新相关恢复、goal、ps、测试和文档。

### 第三阶段：调度与 skill 去重

1. 简化调度图算法，保留 depends/conflicts、无环校验、view 和 plan；
1. 删除 `schedule_status` 及低价值复杂 tie-break/停滞机制；
1. 增强脚本输出，使 skill 不再复制算法；
1. 完成 15 个 skill 的目标行数和职责收敛；
1. 更新 README、AGENTS、usage、architecture 和 decision log。

## 每阶段验收

### 第一阶段

- merge 未授权规则仍明确，但不再需要 token；
- worktree status hook 和 pre-commit hook 行为不变；
- 项目测试、黑盒和 review 三门禁不降低；
- task/spec/pending 模板结构和 validator 不变；
- pending-record 仍支持后台 bug 分析和连续记录；
- 同步 status/plan/apply、回滚、测试、审批和 state 全部保留。

### 第二阶段

- task 状态不再包含 blocked；
- 阻塞 attempt 仍可审计，task 保持 active；
- 用户追加 review/verify 轮次有持久上限和授权记录；
- attempt、execution_id、ledger、ps、recovery 和 goal 继续工作；
- 合并验证失败时 main 不产生 merge commit，可直接 `git merge --abort`；
- 不再存在 integrate-chain transaction JSON 和模板自研 phase；
- 一 task 一个执行 commit、handoff、review scope 和 AC evidence 不变。

### 第三阶段

- 调度仍能阻止依赖环并给出可靠并发建议；
- skill 总行数显著下降，但删除的是重复说明而非门禁；
- Agent 可以自主选择实现、调试和测试准备方式；
- 新用户无需理解脚本内部算法即可正确使用；
- 同一规则只在模板、usage、脚本或 skill 的一个权威位置详细定义。

## 风险与取舍

### 会降低的保障

- 删除 merge token 后，不再由 Claude hook 技术性拦截手工 merge；
- 删除正式 blocked 状态后，task 状态本身不再区分正在实施和等待外部输入；
- Git 原生 merge transaction 不再记录模板自定义的每个 phase；
- 删除 withdraw_rate/prompt_hint 后，不再自动根据撤回率调整 reviewer prompt；
- skill 缩短后，Agent 获得更多实现自由，需要依靠结果门禁而不是步骤约束。

### 继续保留的保障

- exact attempt identity 与执行审计；
- worktree ownership 和恢复；
- 一 task 一个执行 commit；
- 项目测试和黑盒验证；
- review 三态、scope 和 finding 处置；
- AC evidence；
- goal 终态判定；
- 严格文件模板和 validator；
- 同步回滚、测试和用户审批；
- pre-commit 格式门禁；
- merge/push 的文字授权边界。

### 获得的收益

- merge 恢复只看 Git，不再维护双事务状态；
- task 状态减少一个长期分支；
- skill 上下文显著缩短；
- Agent 可根据项目选择实现和调试方法；
- 保留真正影响质量的测试、黑盒、review 和证据闭环；
- pending 子代理和 goal 等已验证有用的能力不受影响；
- 同步行为不发生高风险重写。

## 暂不删除的机制

- attempt、execution_id、ledger、ps、recovery；
- goal / goal-check；
- review finding ID、三态、scope、处置、fix_ref、AC evidence；
- review_limit / verify_limit；
- 一 task 一个执行 commit；
- worktree 状态栏 hook；
- Markdown pre-commit hook；
- pending-record 子代理；
- repo_sync.py 完整同步能力、回滚、测试和审批；
- task/spec/pending/finding 的严格模板和 ID 锁；
- 依赖无环和有效状态读取。

## 最终裁决摘要

截至 2026-09-06，本文各建议的用户裁决为：

| 建议 | 裁决 |
| --- | --- |
| 一：Agent hooks | 只删除 merge token hook；保留 worktree status 和 pre-commit |
| 二：退役 attempt | 拒绝；attempt 全套保留 |
| 三：Git 原生事务 | 同意；采用 `merge --no-ff --no-commit` 后验证 |
| 四：review 精简 | 同意修正版；核心闭环保留，只删低价值附加统计 |
| 五：task-work 结果导向 | 同意；测试、黑盒、review 仍是硬门禁 |
| 六：一 task 一 commit | 保留 |
| 七：模板精简 | 只缩短说明，不删主要字段和结构 |
| 八：CLI 精简 | 删除 blocked/resume 和 ledger record；其它保留，增加 limits 命令 |
| 九：删除 goal | 拒绝；保留 |
| 十：pending-record | 保留子代理，只简化协议 |
| 十一：调度图精简 | 同意 |
| 十二：同步简化 | 拒绝行为变更；能力和流程全部保留，只精简 SKILL.md 重复文字 |
| 十三：逐 skill 精简 | 同意，按可靠性门禁优先原则执行 |

## 尚未阻塞实施的小参数

没有需要再次做产品方向裁决的事项。以下属于实施期可由 Agent按现有语义选择并通过测试确定的命名细节：

- 新的上限调整命令最终叫 `limits`、`extend-limits` 或其它清晰名称；
- Git 原生 integrate 是否用 `--finish/--abort` 包装，或直接提示原生 Git 命令；
- 各 skill 最终行数不必机械命中目标区间，以不重复且不丢门禁为准。
