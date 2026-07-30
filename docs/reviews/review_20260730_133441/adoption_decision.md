# 审阅结果决策

## 目录

docs/reviews/review_20260730_133441

## 报告来源

- 已读：review_task_batch_scheduling_antigravity.md、review_task_batch_scheduling_claude_haiku.md、review_task_batch_scheduling_claude_opus.md、review_task_batch_scheduling_claude_sonnet.md
- 缺失：无

## 统计

- 采纳：13 项
- 不采纳：4 项
- 待决定：0 项

## 待决定项（请先决策）

无。

## 采纳项

### A1. 修正 ready 集合过滤和未调度处置矛盾

- 来源：review_task_batch_scheduling_antigravity、review_task_batch_scheduling_claude_opus、review_task_batch_scheduling_claude_sonnet
- 位置：`docs_repo/plan_task_batch_scheduling.md:76-83,123-129`
- 优先级：HIGH
- 详细判断理由：当前公式会把空依赖的待澄清或未调度 task 纳入 ready，与输出分类和失效条件冲突，可能直接放行未经分析的 task。
- 修复说明：引入明确正向状态：`schedule_status` 仅允许 `scheduled` / `pending_clarification`；`tasks-schedule` 必须为分析范围内每个 backlog task 写一个状态。`ready = status=backlog ∧ schedule_status=scheduled ∧ depends_on⊆done`。`pending_clarification` 与缺失 `schedule_status` 均不进 ready，分别列为待澄清和未调度；存在未调度 task 时 fail-closed 输出清单，不影响已调度 task 的批次计算。

### A2. 从本计划删除多链尾合并和执行拓扑设计

- 来源：review_task_batch_scheduling_antigravity、review_task_batch_scheduling_claude_haiku、review_task_batch_scheduling_claude_opus、review_task_batch_scheduling_claude_sonnet
- 位置：`docs_repo/plan_task_batch_scheduling.md:105-109,139-166`
- 优先级：HIGH
- 详细判断理由：多链尾合并缺 CLI、恢复语义，并与现有链式 ADR 冲突；更关键的是用户已明确裁决「保留调度」，不新增执行层。继续保留多链尾合并属于范围越界。
- 修复说明：删除批末多链尾合并、合并冲突处理及对应改动面/验证/待办；批次消费仅说明用户自行驱动，不承诺或规定 worktree base、分支拓扑、finish/merge 方式。`tasks-run` 和 `docs/blueprint/decisions.md` 001/002 均不改。并行执行/合并另立方案时再裁决拓扑。

### A3. `next-batch` 状态发现复用现有权威优先级

- 来源：review_task_batch_scheduling_claude_haiku、review_task_batch_scheduling_claude_opus、review_task_batch_scheduling_claude_sonnet
- 位置：`docs_repo/plan_task_batch_scheduling.md:74-82`
- 优先级：HIGH
- 详细判断理由：只读主仓 `docs/tasks/` 看不到外部 worktree 内 active/blocked 状态，也可能遗漏未合并分支 ref 的累计状态，批次期间必然失真。
- 修复说明：`next-batch` 复用现有 `tasks-parallel` 基线口径：登记 worktree → 未合并 task 分支 ref → main；done 读取 archive，并结合 ref 中有效状态。命令只允许从主仓调用。状态发现逻辑在 `task.py` 提取为共用函数，不在 skill 中复制。

### A4. 明确 `conflicts_with` 表示不可同时执行

- 来源：review_task_batch_scheduling_claude_sonnet
- 位置：`docs_repo/plan_task_batch_scheduling.md:28-33,76-83`
- 优先级：HIGH
- 详细判断理由：原文一处写「不可同批」，算法却排除与 active/blocked 冲突的候选，语义不一致。用户会在首批仍有 task 运行时汇报已完成子集并取下一批，因此 active task 与新批次不能重叠执行才符合真实调度需求；这是时间互斥，不是完成依赖。
- 修复说明：字段语义改为「两 task 不可同时 active/blocked 执行」；`next-batch` 先排除与当前 active/blocked 集合冲突的 task，再在 ready 候选间选无冲突集合。文档明确该边只约束并发窗口，不要求某一方完成后永久解锁另一方，也不进入 `depends_on` DAG。

### A5. 完整定义 `schedule_status` 生命周期

- 来源：review_task_batch_scheduling_antigravity、review_task_batch_scheduling_claude_haiku、review_task_batch_scheduling_claude_opus、review_task_batch_scheduling_claude_sonnet
- 位置：`docs_repo/plan_task_batch_scheduling.md:37-59`
- 优先级：HIGH
- 详细判断理由：仅定义 `pending_clarification` 无法区分「已分析且无边」和「从未分析」，清除方式也不明确，容易永久卡死或误放行。
- 修复说明：合法值限定为 `scheduled`、`pending_clarification`；缺字段表示未调度。`tasks-schedule` 每次覆盖分析范围内状态；写入完整依赖/冲突后自动置 `scheduled`；无法判定则置 `pending_clarification`。提供严格 `edit --schedule-status scheduled|pending_clarification`，不接受空字符串作为隐式状态。

### A6. 补齐列表参数并由脚本维护冲突边对称性

- 来源：review_task_batch_scheduling_antigravity、review_task_batch_scheduling_claude_opus、review_task_batch_scheduling_claude_sonnet
- 位置：`docs_repo/plan_task_batch_scheduling.md:111-121`
- 优先级：MEDIUM
- 详细判断理由：参数表只列依赖侧，且依靠 Agent 双写反向冲突边会造成长期单向数据。状态一致性应由脚本保证。
- 修复说明：完整列出 `--depends-on/--depends-append/--depends-remove` 与 `--conflicts-with/--conflicts-append/--conflicts-remove`。conflicts 任一覆盖/追加/移除操作自动同步受影响 backlog task 的反向边；读取时仍按无向图归一化，作为旧数据兼容兜底。被引用 task 非可编辑 backlog 时拒绝并给出原因。

### A7. 修正 `--done` 人机输入规范化规则

- 来源：review_task_batch_scheduling_antigravity、review_task_batch_scheduling_claude_opus、review_task_batch_scheduling_claude_sonnet
- 位置：`docs_repo/plan_task_batch_scheduling.md:85-96`
- 优先级：MEDIUM
- 详细判断理由：当前规则一面接受 `T00025`，一面拒绝四位以上数字，自相矛盾；也违背用户明确要求兼容 `t11 t012 13 t0015 T14 T00025`。
- 修复说明：仅 `--done` 使用宽松解析：支持空格/逗号混合分隔；去可选大小写 `t` 前缀；数字串转整数；在当前/归档 task 全集中按数值匹配唯一 tid，再输出仓库记录的规范 tid。这样 `t0015→t015`、`T00025→t025`，同时自然支持未来 `t1000`。非数字、数值 0、无匹配或重复歧义时报错。其他 Agent/脚本入口继续严格使用仓库规范 tid。

### A8. 明确 `--done` 是只读计算补充而非状态写入

- 来源：review_task_batch_scheduling_claude_haiku、review_task_batch_scheduling_claude_opus、review_task_batch_scheduling_claude_sonnet
- 位置：`docs_repo/plan_task_batch_scheduling.md:74,85-96`
- 优先级：MEDIUM
- 详细判断理由：用户需要在完成一个或多个 task 后手工传入完成集，但脚本不能因此绕过 finish/review 或改 task 状态。当前计划虽写「补充」，仍混入「实现完成但未 finish」场景，容易被理解成执行解锁。
- 修复说明：`--done` 只影响本次 `next-batch` 计算，不写 front matter、不归档、不声明 Git 变更已合并。输出显式标注 `assumed_done`。仓库 archive done 与参数集合取并集；用户可累计传入多个 ID。若后续真实执行需要前置代码，仍由现有 task 流程保证，不由调度脚本修改状态。

### A9. 补全图数据失效和 task 生命周期规则

- 来源：review_task_batch_scheduling_claude_haiku、review_task_batch_scheduling_claude_opus
- 位置：`docs_repo/plan_task_batch_scheduling.md:123-129`
- 优先级：MEDIUM
- 详细判断理由：rewind、tasks-merge、drop 会让已落盘依赖/冲突图过期；只检查环和悬空引用不足以维持图有效性。
- 修复说明：rewind 到 backlog 自动置 `pending_clarification`；tasks-merge 不尝试猜测合并图，目标 task 及所有引用源 tid 的 backlog task 统一置 `pending_clarification`，由 `/tasks-schedule` 重算；drop 前列出所有 depends/conflicts 引用并拒绝静默继续，用户处理引用后再 drop。新增 backlog 因缺 `schedule_status` 自动归为未调度。

### A10. 保留 `tasks-schedule` 的显式触发和现有分析边界

- 来源：review_task_batch_scheduling_claude_haiku、review_task_batch_scheduling_claude_sonnet
- 位置：`docs_repo/plan_task_batch_scheduling.md:49-59`
- 优先级：MEDIUM
- 详细判断理由：skill 从只读变写后，必须防止 Agent 自行触发；同时需明确冲突边仍来自现有 spec 改动面分析，而不是用户手工维护。
- 修复说明：保留 `description: none`、`disable-model-invocation: true`；仅用户斜杠或合法 skill 链式调用。沿用现有 tasks-parallel 步骤 1-4：建进行中基线、读 spec 推导候选改动面、识别显式依赖、判路径/共享契约冲突；所有写入只调用 `task.py edit`，禁止直接改 front matter。

### A11. 规范纯脚本输出和错误信息

- 来源：review_task_batch_scheduling_antigravity、review_task_batch_scheduling_claude_haiku、review_task_batch_scheduling_claude_opus
- 位置：`docs_repo/plan_task_batch_scheduling.md:61-83`
- 优先级：LOW
- 详细判断理由：`next-batch` 是人类直接调用的机械接口，输出应自解释且稳定，不依赖 Agent 二次转述。
- 修复说明：由 Python 直接输出固定段落：`next_batch`、`assumed_done`、`waiting_dependencies`、`blocked_by_active_conflict`、`pending_clarification`、`unscheduled`、`invalid_graph`；无内容段省略。错误统一以 `next-batch=FAIL：<类别>: <tid 列表>` 开头。不另建输出模板或 skill。

### A12. 修正文档改动面、验证和 task 拆分

- 来源：review_task_batch_scheduling_antigravity、review_task_batch_scheduling_claude_haiku、review_task_batch_scheduling_claude_opus、review_task_batch_scheduling_claude_sonnet
- 位置：`docs_repo/plan_task_batch_scheduling.md:14-22,139-166`
- 优先级：MEDIUM
- 详细判断理由：范围表有重复行，改动面遗漏调度数据生命周期相关 skill/脚本与 docs_repo 裁决记录，验证漏掉 `--done` 宽松输入，建议的两个 task 粒度过粗。
- 修复说明：删除重复行；改动面补 `tasks-merge`/rewind/drop 相关规则、`docs_repo/decision_log.md`（修正 L21「已落地」与代码不一致）；注明 index 仅新增可忽略字段，属向后兼容。验证补齐所有用户输入样例、未调度/待澄清过滤、worktree/ref 状态优先级、生命周期失效。实施至少拆为：①front matter+edit+生命周期；②next-batch 算法与 CLI；③tasks-parallel→tasks-schedule skill/路由迁移，且②依赖①、③依赖②。

### A13. 删除 `start` 调度门禁

- 来源：review_task_batch_scheduling_claude_haiku、review_task_batch_scheduling_claude_opus、review_task_batch_scheduling_claude_sonnet
- 位置：`docs_repo/plan_task_batch_scheduling.md:98-103`
- 优先级：HIGH
- 详细判断理由：用户明确说明 `next-batch` 只输出一组 task ID，随后由多个 Agent 分别调用既有 `tasks-run tNNN`；每个 tasks-run 自己负责 start 和执行。把调度依赖/冲突检查加入 `task.py start` 会侵入执行层、复制调度判断，并引入 worktree/ref 状态扫描和并发竞态，超出需求。
- 修复说明：从计划范围、设计、改动面和验证中删除 `start` 双门禁及相关状态发现要求；`task.py start` 和 `tasks-run` 保持现状。`next-batch` 只负责机械输出当前下一批 task ID，不参与后续 Agent 执行。

## 不采纳项

### R1. `depends_on` 已在当前实现落地，需设计迁移兼容

- 来源：review_task_batch_scheduling_claude_haiku
- 位置：`scripts/task.py:125-128,1101-1142`
- 优先级：HIGH
- 详细判断理由：已核对当前 main：`FRONT_MATTER_KEYS` 不含 `depends_on`，`cmd_edit` 也无相关参数。`docs_repo/decision_log.md` L21 的「已落地」与代码不一致，属于裁决记录错误，不存在单值到列表的数据迁移。本次应新增字段并同步修正记录。

### R2. 贪心独立集需要改为求最大独立集

- 来源：review_task_batch_scheduling_claude_haiku
- 位置：`docs_repo/plan_task_batch_scheduling.md:79-82`
- 优先级：LOW
- 详细判断理由：报告只指出最优并行度可能下降，未发现正确性缺陷。确定性 tid 顺序贪心成本低、可解释，符合机械调度目标；最大独立集复杂度与实现成本不值得。可在输出中提示高冲突密度，但不改算法。

### R3. 为多批并存增加 batch 持久标识

- 来源：review_task_batch_scheduling_claude_opus
- 位置：`docs_repo/plan_task_batch_scheduling.md:105-109`
- 优先级：MEDIUM
- 详细判断理由：用户已要求本方案只保留调度层，且 A2 删除多链尾合并与批次执行状态，因此没有需要持久归属的 batch 实体。引入 batch 字段会制造第三类状态和无消费者数据。

### R4. `decision_log.md` 文件名错误，应全部改为 `docs/blueprint/decisions.md`

- 来源：review_task_batch_scheduling_claude_opus、review_task_batch_scheduling_claude_sonnet
- 位置：`docs_repo/plan_task_batch_scheduling.md:166`
- 优先级：LOW
- 详细判断理由：仓库同时存在 `docs_repo/decision_log.md` 和 `docs/blueprint/decisions.md`。本计划原文引用 L21，明确指向前者，不是不存在的文件。A2 删除执行拓扑改动后，无需修改 blueprint ADR 001/002；应更新的是 `docs_repo/decision_log.md` 中与实际代码不一致的 L21。报告将两个权威范围不同的文件混为一处。