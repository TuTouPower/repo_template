# 当前仓库工作流审阅报告

- 审阅时间：2026-08-04（UTC+8）
- 审阅范围：仅 `git ls-files` 返回的 tracked 文件
- 重点：`task-dispatch` 并发、`task-run` 串行；状态机、写域、worktree、合并、依赖冲突、失败路径、index、测试、文档一致性
- 源文件处理：只读
- 验证：`python3 -m pytest tests/repo_template/ -q`，结果 `227 passed in 14.04s`

## 高优先级

### H1. 链式合并冲突恢复后无法删除祖先分支

- 位置：
  - `scripts/repo_template/task.py:2042-2072`
  - `scripts/repo_template/task.py:2085-2097`
  - `scripts/repo_template/task.py:2120-2125`
  - `scripts/repo_template/task.py:2157-2165`
  - `.agents/skills/task-integrate/SKILL.md:49-55`
- 现象：`integrate <链尾> --chain` 遇冲突后，skill 指示执行 `integrate <链尾> --continue`。`--continue` 先提交 merge，再解析链；此时祖先分支均已成为 `HEAD` 祖先，`_resolve_chain()` 在 `task.py:2055-2056` 将其过滤。即使恢复命令补带 `--chain`，解析结果仍为空；最终只删除链尾分支。
- 影响：`task-run` 冲突恢复路径违反“删除整条链分支”契约。代码已合入，不直接丢数据，但祖先 task 分支残留，完成汇报失真，后续分支查看、清理和恢复判断增加歧义。
- 建议：merge 前持久化本次链成员，`--continue` 完成后按原集合校验并删除；或让恢复逻辑从进行中 merge 元数据与链尾历史重建链成员，不使用“尚未合入 HEAD”作为恢复后筛选条件。同步补充链式冲突测试，并让 skill 明确恢复命令参数。
- 置信度：高
- 优先级：高

## 中低优先级

### M1. 默认串行队列不保证依赖拓扑顺序

- 位置：
  - `.agents/skills/task-run/SKILL.md:27-38`
  - `.agents/skills/task-run/SKILL.md:40-49`
- 现象：无参数或状态参数按 tid 升序固定队列；同一文档又要求 `depends_on` 目标排在依赖者之前。若低编号 task 依赖高编号 task，例如 `t002 depends_on t005`，默认顺序直接违反链式继承前提。未发现 CLI 层串行队列拓扑校验。
- 影响：依赖者可能从不含前置成果的 base 开始执行，形成静默错误链；现有 `task.py view` 面向主干扇出调度，不能自动修正 `task-run` 固定队列。
- 建议：队列冻结前执行拓扑排序或至少校验输入顺序。无参数与状态参数应按依赖图排序，并以 tid 作为同层稳定排序；用户显式顺序违反依赖时停止并指出具体边。
- 置信度：高
- 优先级：中

### M2. worktree 内 drop 只扫描旧快照，可能漏掉主干新增引用

- 位置：
  - `scripts/repo_template/task.py:449-465`
  - `scripts/repo_template/task.py:2168-2175`
- 现象：`cmd_drop()` 通过当前工作区 `scan_tasks()` 检查 `depends_on` / `conflicts_with` 引用。task worktree 基于 start 时主干快照；若 start 后主干给其他 backlog task 新增指向当前 task 的调度边，worktree 内 drop 看不到该边并可成功归档当前 task。
- 影响：主干随后可能保留指向 dropped task 的悬空调度边，`task.py view` 才以 `invalid_graph` 暴露，调度图从“操作前拒绝”退化成“事后损坏并失败”。
- 建议：active/blocked task 在 worktree 内 drop 前，额外读取主仓当前 HEAD 检查反向引用；无法确认主仓快照时拒绝 drop。补充“start 后主干新增引用，再在 worktree drop”集成测试。
- 置信度：高
- 优先级：中

### M3. `git merge --abort` 被 merge_guard 当作新合并拦截

- 位置：
  - `.claude/hooks/merge_guard.py:32-35`
  - `.claude/hooks/merge_guard.py:42-55`
  - `.claude/hooks/merge_guard.py:92-103`
  - `.agents/skills/task-integrate/SKILL.md:55`
- 现象：正则匹配所有 `git merge`，`--abort` 没有 target，归类为 `git-merge:unspecified` 并触发授权 token。`--continue` 同样被归入合并命令。skill 则把 `git merge --abort` 定义为冲突后的标准放弃路径。
- 影响：故障逃生路径增加一次与语义不符的授权阻塞；自动恢复流程可能停在 hook，而非可靠回到合并前状态。
- 建议：在 `detect_merge()` 中显式排除 `git merge --abort`；是否排除 `--continue` 需结合授权模型决定，但应与 `task.py integrate --continue` 和 skill 文档保持一致。补充 hook 单测。
- 置信度：高
- 优先级：中

### L1. blocked→active rewind 审计日志可能在并行合并时冲突

- 位置：
  - `scripts/repo_template/task.py:1245-1260`
  - `scripts/repo_template/task.py:2256-2272`
  - `.agents/skills/task-integrate/SKILL.md:37-47`
- 现象：`rewind blocked --to active` 要求在自身 worktree 执行，并向 tracked 文件 `docs/archive/tasks_audit.log` append。多个并行 worker 均发生 rewind 时，各自执行 commit 会携带同一日志文件的追加，后续串行 integrate 可能产生内容冲突；冲突处置表未定义该文件语义。
- 影响：低频失败路径会把状态维护内容混入 task 执行 commit，并在并行合并阶段产生需人工裁决的 append-only 日志冲突。
- 建议：明确审计日志归并策略。优先让 coordinator 在主仓串行追加；若继续随 task 分支合入，冲突处置必须要求保留双方记录并补并行测试。
- 置信度：中高
- 优先级：低

### L2. AGENTS.md 对 task-run 合并时机存在一句矛盾描述

- 位置：
  - `AGENTS.md:82`
  - `AGENTS.md:95`
  - `AGENTS.md:108`
- 现象：第 82、108 行明确“逐个执行、逐个 cleanup、最后一次性合链尾”；第 95 行写为“逐个执行并合并”。
- 影响：路由表可能误导 coordinator 每完成一个 task 就合并，破坏 `task-run` 链式拓扑和一次性合并契约。
- 建议：第 95 行改为“并发度 1：逐个执行并 cleanup，全部完成后一次性合链尾”。
- 置信度：高
- 优先级：低

### L3. task-run 进行中，view 会把链内后续 task 显示为依赖阻塞

- 位置：
  - `scripts/repo_template/task.py:1062-1072`
  - `.agents/skills/task-run/SKILL.md:52-65`
  - `.agents/skills/task-dispatch/SKILL.md:51`
- 现象：`view` 只按主干已合并 done 集合判断依赖解锁。串行链中前置 task 已在分支完成但尚未合入主干，后续 task 会显示为依赖阻塞；`task-run` 实际可通过 `--base` 继续。
- 影响：运行中全景状态与串行执行真实可运行性不一致，人工恢复或观察时容易误判。
- 建议：在 `task-run` 恢复说明和 `view` 输出中标明主干视角限制；更稳妥方案是提供链式队列专用只读检查，按上一分支 tip 判断依赖。
- 置信度：高
- 优先级：低

### L4. front matter 解析存在三份实现，复杂转义行为不一致

- 位置：
  - `scripts/repo_template/task.py:389-391`
  - `scripts/repo_template/render_review_prompts.py:38-56`
  - `scripts/repo_template/check_review_status.py:72-92`
- 现象：后两处以 `strip()` 简化去引号，`task.py` 使用完整反转义逻辑。简单状态字段当前可用，但含转义引号或反斜杠的值解析结果可能不同。
- 影响：review/index 辅助工具与状态权威解析器存在漂移风险；格式演进后可能读取不同 task 元数据。
- 建议：提取共享只读解析模块，或增加三处一致性参数化测试。
- 置信度：高
- 优先级：低

## 改进建议

### I1. 补齐关键失败路径测试

现有 227 个测试全部通过，happy path 与多数拒绝路径覆盖较好。建议增加：

1. `integrate --chain` 冲突 → 解决 → `--continue` → 整条链分支均删除。
2. `git merge --abort` / `git merge --continue` 的 hook 判定。
3. `task-run` 默认队列含前向依赖时的排序或拒绝。
4. task start 后主干新增反向引用，worktree drop 必须拒绝。
5. 两个并行 worktree 均追加 `tasks_audit.log` 后的 integrate 行为。
6. `rewind blocked→active`、`purge`、审计格式和幂等性。

### I2. 明确 task-run 与 task-dispatch 使用不同状态视角

- `task-dispatch`：主干为状态权威，完成即合并，`view` 可用于补位和解锁。
- `task-run`：未合并分支链为进行中权威，主干 `view` 不能代表链内后续 task 是否可执行。

建议在 `AGENTS.md` 与两个 skill 各保留一处权威定义，其他位置引用稳定标题，避免重复描述漂移。

### I3. 扩充 integrate 冲突处置表

建议加入：

- `docs/archive/tasks_audit.log`：append-only，保留双方完整记录。
- 链式 `--continue`：恢复时必须保留原链成员集合。
- index commit 失败：主干已含 merge commit、分支暂不删除；明确恢复命令与幂等条件。

### I4. 清理 tracked 历史设计稿的现状误导

- 位置：
  - `docs_repo/plan_task_batch_scheduling.md`
  - `docs_repo/decision_log.md`
- 现象：仍出现已废弃的 `next-batch`、`pending.md`、`findings.md` 等设计名。
- 建议：若保留历史原文，在 `docs_repo/README.md` 明确“历史设计笔记，不代表当前行为；当前权威为 AGENTS.md、skills 与 task.py”。避免直接重写历史结论。

## 不确定项

### U1. worktree drop 漏检主干引用的实际频率

触发条件要求 task start 后，主干又修改其他 backlog task 的依赖或冲突边。代码允许该时序，但实际调度纪律可能较少出现。缺陷成立，发生频率待真实使用数据确认。

### U2. 审计日志并行冲突是否已被外部执行纪律规避

当前 tracked 文档未发现“同一时间只允许一个 rewind”限制。若外部调度器另有未入库约束，L1 风险会下降；本次仅审阅 tracked 文件，无法确认外部约束。

### U3. merge_guard 对 `--continue` 的授权意图

`--abort` 明确不产生新合并，应排除。`--continue` 会完成已有合并，是否沿用会话级授权或要求新 token，需产品决策；当前 hook、task.py 和 skill 三者语义未统一。

## 已核查且未发现阻断问题

- worker/coordinator 写域总体清晰：worker 写自身 task worktree，coordinator 串行写主仓。
- `start`、失败补偿、`cleanup-worktree`、正常 `integrate`、index 独立 commit、已合入幂等路径有测试覆盖。
- 并行 task 从主干扇出，完成即合并；正常完成顺序不要求与 tid 顺序一致。
- 链式 happy path 能从上一 task 分支继承并只合链尾；未完成成员和登记 worktree 会被拒绝。
- 调度图可检测环、悬空依赖、dropped 引用、冲突与依赖组合。
- `list` 默认只读；index 由 integrate 重建并单独 commit，活跃与归档拆分逻辑有测试覆盖。
- tracked 工作树在审阅开始时干净；审阅未修改任何源文件。
