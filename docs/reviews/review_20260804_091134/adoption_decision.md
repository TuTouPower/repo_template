# 审阅结果决策

## 目录
docs/reviews/review_20260804_091134

## 报告来源
- 已读：review_repository_antigravity.md, review_repository_claude_current.md, review_repository_claude_haiku.md, review_repository_claude_opus.md, review_repository_claude_sonnet.md, review_repository_grok.md
- 缺失：无（6 路齐全：Claude 四档 + Grok + Antigravity）

## 统计
- 采纳：11 项
- 不采纳：4 项
- 待决定：0 项

## 采纳项

### A1. task-work 未说明 --base 由谁传入，串行链式接线断裂

- 来源：review_repository_claude_sonnet M2, review_repository_grok 1, review_repository_claude_opus M2, review_repository_claude_haiku H1（隐含）
- 位置：.agents/skills/task-work/SKILL.md:62-64（Step 1.2）；.agents/skills/task-run/SKILL.md:55-59
- 优先级：HIGH
- 详细判断理由：4 路确认。task-work 只接受 tNNN，Step 1.2 写「在主仓默认分支执行 task.py start」无 --base 提及。串行恢复或 worker 独立被调用时会从主干扇出，断掉链式拓扑。task-run 队列循环注释写了 task-work(--base t001) 但 task-work 无该参数，分工只能从伪码推断。
- 修复说明：task-work Step 1.2 补一句「串行链式由调用方（task-run）先执行 start --base 建 worktree，本 skill 发现现成 worktree 时不得重新 start」；task-run 队列循环把「coordinator 先 start --base，再调 task-work」写成显式步骤。

### A2. AGENTS worktree 行「恒基于当前主干 HEAD」与串行 --base 矛盾

- 来源：review_repository_grok 2
- 位置：AGENTS.md 目录表 `../{repo}_{tid}/` 行
- 优先级：HIGH
- 详细判断理由：grok 指出表写「恒基于当前主干 HEAD」，与同文件「串行=链式」段及 task.py 的 --base 支持矛盾。agent 读表优先时会忽略 --base。与 A1 同源。
- 修复说明：改为「并行：主干 HEAD；串行：--base 上一已完成 task 分支（须先 cleanup-worktree）」。

### A3. skill 表 task-run 写「逐个执行并合并」，合并时机错误

- 来源：review_repository_grok 3, review_repository_claude_current L2
- 位置：AGENTS.md skill 调用表 task-run 行（第 95 行附近）
- 优先级：HIGH
- 详细判断理由：2 路确认。表写「并发度 1 的调度：逐个执行并合并」，但 task-run 是链式——每 task 仅 cleanup-worktree，全部完成后一次性 integrate --chain。该描述会让 coordinator 在链中途对中间节点 integrate，破坏「主干只进一次 merge」。
- 修复说明：改为「链式串行：逐个执行+cleanup；链尾一次 merge」。

### A4. task.py view 串行链中段「假阻塞」

- 来源：review_repository_grok 8, review_repository_claude_current L3, review_repository_antigravity 2, review_repository_claude_opus H3（并行视角）
- 位置：scripts/repo_template/task.py:1062-1072 cmd_view；task-run SKILL.md:52-65
- 优先级：MEDIUM
- 详细判断理由：4 路。view 的 done_set = main_done_set（调度判断用 main 视角）。串行链上前置在分支 done 但未合 main，下游在 view 里显示「依赖阻塞」。对并行（完成即合并）正确，对串行（链尾一次合）误导。view 是只读快照工具，不改其语义；在 task-run 恢复说明和 view 输出注明「主干视角，链上 done 未合 main 不算解锁」即可。
- 修复说明：task-run「恢复」节补一句「view 以主干视角判 done；链上已完成未合 main 的前置不显示为解锁，链式恢复时按分支 tip 判依赖」；不改 cmd_view 逻辑（D1 选 B 的前提下）。

### A5. share_prompt.txt 引用旧路径 tasks-run / scripts/task.py

- 来源：review_repository_claude_opus H1, review_repository_grok 6, review_repository_grok 16
- 位置：docs/reviews/prompts/share_prompt.txt:53,62,70,74,76
- 优先级：HIGH
- 详细判断理由：2 路确认。第 53 行 tasks-run（多了 s）、第 62 行 scripts/task.py（缺 repo_template/）、第 70/74/76 行 .agents/skills/tasks-run/SKILL.md。reviewer 按这些路径找文件必然失败，去重机制实际不可用。skill 改名（8ba36e6）后引入，prompt 模板不在任何一致性校验范围内。
- 修复说明：改正 5 处路径为 task-run / scripts/repo_template/task.py / .agents/skills/task-run/SKILL.md（部分应指向 task-work，因 max_review_round/Step 6 处置现归 task-work）。

### A6. merge_guard 与 task.py 双通道未说明

- 来源：review_repository_claude_opus M3, review_repository_claude_sonnet H2, review_repository_claude_haiku H3, review_repository_grok 11, review_repository_claude_current M3
- 位置：.claude/hooks/merge_guard.py；scripts/repo_template/task.py:2135 cmd_integrate；.agents/skills/task-integrate/SKILL.md；AGENTS.md
- 优先级：MEDIUM
- 详细判断理由：5 路确认。task.py integrate 内 subprocess git merge 不经 Bash 工具，hook 拦不到，靠 skill 会话级授权。设计合理但 AGENTS/task-integrate/merge_guard docstring 均未说明。后来者可能误判 hook 冗余或 integrate 绕过授权是 bug。另：git merge --abort 也被 hook 拦（target=unspecified），task-integrate 教用户 --abort 但未提示要走 token。
- 修复说明：task-integrate「边界」或 AGENTS「执行角色与合并时机」补一句「脚本内 merge 由会话级授权覆盖，merge_guard 只拦脚本外的手动 merge；git merge --abort 同样过 token」。

### A7. index 重建表述与实际行为不符

- 来源：review_repository_claude_opus M1, review_repository_grok 7
- 位置：AGENTS.md:18（docs/tasks_index.json 行）
- 优先级：MEDIUM
- 详细判断理由：2 路确认。表写「仅主仓 coordinator 由 task.py integrate 在合并后重建并单独 commit」。实际 add/edit/rewind/purge 也在主仓重建 index（不落独立 commit，由 task-schedule/task-create 维护 commit 携带）。权威定义与实现分叉。
- 修复说明：改为「工作区可由 add/edit/rewind/purge 重建；入库 commit：维护期随操作提交，合并后由 integrate 单独 chore commit；执行 commit 不带 index」。

### A8. view 阻塞分组标题「被 active 冲突阻塞」语义过窄

- 来源：review_repository_claude_opus M4, review_repository_grok 12
- 位置：scripts/repo_template/task.py:1125-1132,1173-1179
- 优先级：LOW
- 详细判断理由：2 路确认。blocked_conflicts 汇集三类（peer active/blocked、peer 未合 main done、peer 序号更小 backlog），标题统一为「被 active 冲突阻塞」。第三类不是 active，标题误导。task-schedule 让 agent 原样报告 view，标题进用户视野。
- 修复说明：标题改「▸ 被冲突阻塞」。

### A9. testing.md 占位符无门禁

- 来源：review_repository_claude_sonnet M5, review_repository_grok 10
- 位置：docs/blueprint/testing.md；task-work Step 1；scripts/repo_template/task.py preflight
- 优先级：MEDIUM
- 详细判断理由：2 路确认。{doctor_cmd}/{test_cmd}/{blackbox_verify} 是占位符，项目复制后未填则门禁形同虚设。preflight 不检查占位符。
- 修复说明：preflight 加一条 warn（不阻塞）：testing.md 中 {doctor_cmd}/{test_cmd}/{blackbox_verify} 未填时提示项目方初始化。

### A10. FINDING_RE 报错信息不友好

- 来源：review_repository_antigravity 1（原 D2，用户选 B）
- 位置：scripts/repo_template/check_review_status.py:33 FINDING_RE
- 优先级：MEDIUM
- 详细判断理由：antigravity 报 reviewer 可能误写 t001_general_f001。规范是 {tid}_gen_fNNN（conventions.md 与 general_prompt.txt 一致），不兼容 general 保持规范唯一性。但现状报 ReviewDataError 无友好提示，reviewer 写错时 Step 6 中断且不知如何修。
- 修复说明：保持 FINDING_RE 为 `^(t[0-9]+)_(?:code|test|gen)_f[0-9]+$`；报错时补提示「single 轴应为 {tid}_gen_fNNN，参考 conventions.md」。

### A11. task-bug commit 边界措辞含混

- 来源：review_repository_claude_sonnet H4（原 D3，用户选 A）
- 位置：.agents/skills/task-bug/SKILL.md:34-43（第 8 步）
- 优先级：MEDIUM
- 详细判断理由：sonnet 指出第 8 步「已批准立项：task-create 已批量提交 task 目录与派生 index；这里一并列出，用户同意后提交 bug 登记」中「一并列出」含混，可能让 agent 把 task 目录 + bug 条目合并进一个 commit。task-from-pending 第 7 步已清晰，可作模板。
- 修复说明：task-bug 第 8 步显式注明「task 目录与 index 由 task-create 第 6 步独立 commit；bug 条目文件单独 commit，不与 task 目录合并」。

## 不采纳项

### R1. --chain 无链式拓扑校验（混用场景）

- 来源：review_repository_claude_haiku H1, review_repository_grok 5
- 位置：scripts/repo_template/task.py _resolve_chain（2042-2072）
- 优先级：HIGH
- 详细判断理由：haiku H1 与 grok 5 担心串行与并行混用时 --chain 会误并/误删并行分支。但「串行链式」与「并行扇出」是两套独立拓扑，混用本身就是错误用法——AGENTS 已明确分设。--chain 只收集「链尾祖先且未合 main」的分支，并行扇出分支不会是链尾祖先（它们从 main 扇出，互不祖先）。grok 5 说的「链上 t001 done+cleanup、t002 仍 active 时对 t001 integrate 会成功」——这违反 task-run 队列循环（只对链尾调 integrate，不对中间节点），是 agent 误操作而非脚本缺陷。混用场景应靠 skill 流程约束（task-run 不中途 integrate），不靠 _resolve_chain 兜底。补测试说明混用禁区可接受，但「加拓扑校验」过度。

### R2. task-run 默认队列不自动拓扑排序 / start 不加调度图硬门禁

- 来源：review_repository_claude_haiku H2, review_repository_claude_current M1, review_repository_grok 4（原 D1，用户选 B）
- 位置：.agents/skills/task-run/SKILL.md:27-38；scripts/repo_template/task.py cmd_start
- 优先级：MEDIUM
- 详细判断理由：用户选 B——不加硬门禁，文档明确链式靠 ancestry、并行靠 view+main_done_set。诉求是 task-run 默认队列应按依赖图拓扑排序，或 start 加 --enforce-schedule 硬门禁。但 task-run 输入表已写「依赖被前置 task 满足的 backlog 可入队，只要前置排在其前」，用户显式指定顺序时由用户负责拓扑。自动拓扑排序剥夺用户显式控制权，且链式拓扑下 git ancestry 已保证继承。硬门禁与链式 ancestry-based 依赖语义冲突（链上前置在分支 done 但未合 main，硬门禁会误拒）。A4 已在 task-run 补主干视角说明，配合「start 前 view」纪律足够。

### R3. front matter 三处解析不一致（抽公共模块）

- 来源：review_repository_claude_opus M5, review_repository_claude_sonnet M4, review_repository_claude_current L4, review_repository_grok 9
- 位置：task.py _unquote vs render_review_prompts.py/check_review_status.py strip('"')
- 优先级：LOW
- 详细判断理由：4 路提及但全部标 LOW/MEDIUM。两份简化副本只消费 tid/slug/diff_anchor/review_level/status（受控词汇不含引号），title/note 不经这两个脚本。属潜在陷阱而非现行 bug。抽公共模块改动面大（三脚本同目录可 import，但引入新模块依赖），收益不抵成本。注释已写明「改规则需三处同步」。保持现状，注释提醒足够。

### R4. cmd_add tid 分配只扫当前工作区

- 来源：review_repository_grok 17
- 位置：scripts/repo_template/task.py cmd_add
- 优先级：LOW
- 详细判断理由：grok 自己标 LOW 且置信度中。tid 分配只在主仓创建且创建后立刻 commit（AGENTS 已约束），未合并分支占更高 tid 的场景需异常操作史。pending/findings/spikes 走 _id_scan 全局扫描是因为它们可能在 worktree 内创建；task 创建只在主仓，场景不同。文档钉死「只在主干创建且创建后立刻 commit」即可，无需改实现。
