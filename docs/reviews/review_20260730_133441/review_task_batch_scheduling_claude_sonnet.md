# 审阅：task 批次调度方案

## 当前模型判断依据

主会话模型未在上下文暴露可靠 ID（仅 `default_sonnet[1m]` 占位符）。本路按 my-review 授权以 sonnet 视角出判断，不对外宣称具体模型版本。

## 模块 slug

`task_batch_scheduling`

## 审阅范围

全量审阅 `docs_repo/plan_task_batch_scheduling.md`（1-166 行），并交叉核对：

- `scripts/task.py`（FRONT_MATTER_KEYS、cmd_edit、cmd_start、状态枚举、归档口径）
- `.agents/skills/tasks-parallel/SKILL.md`
- `docs/tasks/task_template/task.md`
- `AGENTS.md` / `CLAUDE.md` skill 路由表与使用示例
- `docs/blueprint/decisions.md`（L001 链式分支、L002 取消 main 冻结）

只读审阅，未改动任何源文件。

## 高优先级

### H1：`start` 门禁与链式分支基线口径冲突

- 位置：plan §`start` 门禁（L98-103）；交叉 `scripts/task.py:1145 cmd_start`、`AGENTS.md` 开发原则「`start` 无绕过参数；只能从干净主仓默认分支调用。首 task 基于本地主干，后续 task 基于上一已完成且已清理 worktree 的 task 分支」。
- 现象：方案要求 `start` 校验「`conflicts_with` 与当前 active/blocked task 无交集（含反向）」。但 `cmd_start` 现有实现只 `require_primary_worktree(clean=True)` 并基于 `--base` 解析基线分支，未读取「当前 active/blocked task」集合。active/blocked task 的 worktree 在仓库外 `../{repo}_{tid}/`，其 `task.md` 状态写在各自 worktree 内（见 CLAUDE.md 目录表「`../{repo}_{tid}/`」行），主仓 `docs/tasks/` 副本仍为 backlog。
- 影响：`start` 在主仓执行时无法直接读到 worktree 内的 active 状态，必须枚举 `git worktree list` 并跨 worktree 读 front matter，否则门禁形同虚设或误判（主仓副本仍是 backlog，会放行同冲突 task）。方案未声明这一数据源口径，与 `tasks-parallel` SKILL 现有「基线固定为进行中或尚未合并」的复杂建基线逻辑（L22-41）未对齐——后者已实现跨 worktree + 未合并分支的基线收集，但 plan 未引用、未复用。
- 建议：明确 `start` 门禁的 active/blocked 集合来源=登记 worktree 内 `task.md` + 未合并 task 分支 `list --ref` 累计状态（对齐 tasks-parallel SKILL 步骤 1），并指出该基线收集逻辑应由 `task.py` 内部复用，而非让 `start` 重新实现一套。否则需说明 `start` 如何在不进入 worktree 的情况下拿到 active 集合。
- 置信度：高
- 优先级：高

### H2：多链尾合并与 L001 链式分支模型语义冲突未化解

- 位置：plan §批的消费方式（L107-109）、改动面（L143）；交叉 `decisions.md` L001「所有 task 共用一个分支 vs 线性祖先链，结论采用链式 task 分支……整批完成后一次询问，只合并链尾分支；Git ancestry 是链关系权威」。
- 现象：plan 引入「批末多链尾合并是 `task.py` 层面的小扩展（链尾从 1 个变 N 个，按 tid 升序逐个合并）」。但 L001 的链式模型假设整批 task 共享一条祖先链、唯一链尾。一旦同批 N 个 task 并行各自 `start`，它们基于同一 `--base`（上一链尾或 main）分叉出 N 条独立分支，彼此无 ancestry 关系，不再是「链」。plan 自己在 L107 承认「同批 task 之间永远无依赖边」，即 N 条独立分支。
- 影响：
  1. 「链尾」概念从「唯一祖先链尾」退化为「N 条并行分支全部」，L001 的「只合并链尾分支」语义被静默改写，plan 却在待办 L166 只写「多链尾合并记新行（L4 worktree 原则的并行扩展）」，未说明 L001 结论是否被替代或扩展。
  2. N 条分支逐个 `git merge --no-ff` 入 main 时，第 2 个起均为三方 merge，冲突概率随 N 上升；plan 在 L109 只说「冲突暂停报告」，未规定合并顺序对冲突面的影响（tid 升序未必是冲突最小顺序）。
  3. `tasks-run` 串行链模型（`decisions.md` L001 + AGENTS.md「后续 task 基于上一已完成 task 分支」）与并行批的 N 条独立分支在拓扑上不可兼容——同批 task 若想「基于上一 task 分支」则退化回串行；plan 未说明 `tasks-run` 与 `tasks-schedule` 两套拓扑如何共存或何时切换。
- 建议：在 plan 内补一节「与 L001/L002 的关系」：明确并行批是 L001 链式模型的**并行扩展**（批内 N 条独立分支，批间仍可串行接续），还是**替代** L001（凡并行批即多链尾）。并明确 `tasks-run` 的串行链是否仅适用于「单链批」场景，`tasks-schedule` 适用于「并行批」场景，两者由用户选择而非自动判定。否则状态机出现两套拓扑语义却无权威定义。
- 置信度：高
- 优先级：高

### H3：`next-batch` 算法对 active task 冲突的处理可能让批次永久卡死

- 位置：plan §后续：脚本推导（L76-83 算法块）。
- 现象：算法定义「可执行 = ready 中不与 active/blocked task 冲突者」。若 active task A 声明 `conflicts_with: [B]`，B 在 ready 集合，则 B 永远不进任何批，直到 A finish/drop。但 A 的 finish 需要走 review、合并等长流程，期间 B 无法启动。若 A 因 blocked 长期挂起，B 永久饥饿。
- 影响：`conflicts_with` 本意是「不可同批并行」，但算法把它放大成「active 期间不可 start」，等价于把互斥图降级为依赖边——正是 plan §两张图（L33）自己批评的「虚构先后关系」。语义自相矛盾。
- 建议：区分两种冲突语义：(a) 「不可同批 start」（互斥图本意）——active A 不阻止 B 在下一批 start，只要 B 与 A 不在同批即可，而 A 已 active 不在新批内，B 可直接进批；(b) 「不可同时 active」（资源互斥）——A active 期间 B 不能 start。两者对调度影响完全不同。plan 当前算法采用 (b) 但未说明理由，且与 §两张图对互斥图的定义（L31「可同时 ready，不可同批并行」）矛盾——「同批并行」指同一 `next-batch` 输出，A 已 active 不在 ready 集合，本就不会与 B 同批。按 L31 定义，B 应当可以进批。建议采用 (a) 语义，或显式说明为何需要 (b)。
- 置信度：中高
- 优先级：高

## 中低优先级

### M1：`schedule_status` 字段职责与「未调度」边界模糊

- 位置：plan L42-43、L59、L83、L129、L159。
- 现象：`schedule_status: pending_clarification` 用于「Agent 无法确认依赖或冲突」。但 L83 输出分类含「未调度」（无字段 task），L129 失效条件含「后补的 backlog task 不在图内」。三种状态：有字段且 pending_clarification、有 conflicts_with/depends_on 但无 schedule_status、完全无字段——`next-batch` 如何区分「已调度完成」与「待澄清」与「未调度」未在算法块体现。L159 验证项提到分类输出但算法块（L76-83）只有「待澄清 / 未调度」两类输出。
- 影响：`pending_clarification` task 是否进 ready？其 depends_on 是否已可信？若 depends_on 可信只是 conflicts_with 未定，则它应进 ready 但不进批；若两者都未定，则应阻塞。plan 未区分。
- 建议：明确 `pending_clarification` task 在算法中的位置（建议：不进可执行集合，单列输出），并区分「pending_clarification 且 depends_on 已定」（可算 ready 但不进批）与「pending_clarification 且 depends_on 未定」（不进 ready）。
- 置信度：中
- 优先级：中

### M2：`--done` 宽容解析与严格入口的边界描述有漏洞

- 位置：plan §后续（L85-96 表格）。
- 现象：L94 规则「4 位以上数字（`t0015`）、非数字、超出 tid 范围 拒绝」。但 L92「`T00025` 去多余零为 `t025`」与「4 位以上拒绝」冲突——`T00025` 是 5 位数字，按 L94 应拒绝，按 L92 应规约为 `t025`。`t025` 本身是 3 位，但输入是 5 位。
- 影响：规则自相矛盾，实现时需自行裁决，与「能唯一确定的宽容，有歧义的拒绝」原则冲突——`T00025` 能唯一确定为 `t025`（去前导零），但 L94 按位数硬拒。
- 建议：统一为「解析整数后按现有 tid 位数补零；整数超出当前最大 tid+余量则拒绝」。位数判断应基于解析后的整数范围，而非输入字符串长度。`T00025` -> 整数 25 -> `t025`（若 t025 存在），不按字符串长度拒。
- 置信度：中高
- 优先级：中

### M3：`conflicts_with` 对称性写入依赖 Agent，校验侧兜底但写入侧无校验

- 位置：plan §edit 列表字段语义（L121）。
- 现象：「`conflicts_with` 对称性由 tasks-schedule 负责双向写入；`next-batch` 与 `start` 校验时按无向图处理」。即写入靠 Agent 自觉双向，校验侧兜底。但 `edit --conflicts-with t006` 是单点写入，Agent 若只写 t002 的 conflicts_with=[t006] 忘写 t006 的=[t002]，状态机不报错，长期累积单向边。
- 影响：数据一致性依赖 Agent 行为而非状态机约束。`tasks-schedule` 改名后是唯一写入口尚可接受，但 `edit` 命令本身开放给用户手动调用（见 AGENTS.md 使用示例 L81），用户手动 `edit` 时不会自动补对称边。
- 建议：要么 `edit --conflicts-with t006` 时自动在 t006 front matter 补写反向边（状态机保证对称），要么明确禁止用户手动 `edit` 冲突字段、只允许 `tasks-schedule` 写。前者更稳。
- 置信度：中
- 优先级：中

### M4：`tasks-schedule` 性质从只读变写，但 skill 路由表「禁止自行进入」约束需重申

- 位置：plan §首轮（L51）；交叉 AGENTS.md L56「仅在用户斜杠或其它 skill 链式调用时进入；禁止自行进入」。
- 现象：`tasks-parallel` 当前 `disable-model-invocation: true` 且只读。改名 `tasks-schedule` 后变写（调 `edit` 落盘 front matter）。写权限 skill 若被 Agent 自行触发，会在未授权时改 front matter。plan 在改动面 L145 提到「边界声明重写」但未明确新 skill 是否保持 `disable-model-invocation: true`。
- 影响：写权限 skill 的触发约束若未在 plan 显式声明，实施时可能遗漏 front matter 的 `disable-model-invocation` 字段。
- 建议：plan 明确 `tasks-schedule` 保留 `disable-model-invocation: true`，且写操作只经 `edit` 子命令（状态机审计），不直接改文件。
- 置信度：中高
- 优先级：中

### M5：done 集合定义未覆盖「finish 未走但 task 分支已合并」的中间态

- 位置：plan §后续（L74）。
- 现象：done 定义为「`docs/archive/tasks/` 中 `status: done` 的 tid 并集（`finish` 移入归档）」。但 L85 `--done` 场景说明提到「task 实现完成但 finish 收尾未走」。这意味着存在「分支已合并入 main 但 task 未 finish、仍在 `docs/tasks/`」的中间态。此时主仓 `docs/tasks/` 副本可能仍 active，但 main 已含其改动。
- 影响：`next-batch` 不带 `--done` 时，该 task 不在 done 集，但其下游 task 的 depends_on 可能已满足（main 已含前置改动）。算法会误判下游 task 为「依赖未满足」。
- 建议：done 集合补充「task 分支已合并入 main」的检测（`git branch --merged <default>`），或明确该中间态必须用 `--done` 手动补报，属于已知限制。
- 置信度：中
- 优先级：中

### L1：验证项缺少 `--done` 宽容解析的边界用例

- 位置：plan §验证（L152-161）。
- 现象：验证项覆盖算法、门禁、edit 字段、index、混合数据源、无图、多链尾合并、tasks-run 回归，但未覆盖 `--done` 宽容解析表格（L89-96）中的各类输入（`t11`/`T11`/`13`/`T00025`/逗号空格分隔/4 位拒绝）。
- 影响：宽容解析是用户接口，边界用例漏测会导致规约歧义。
- 建议：补验证项：`--done` 各宽容输入与拒绝用例的输入-输出对照。
- 置信度：高
- 优先级：低

### L2：改动面遗漏 `docs/tasks_index.json` 派生字段说明

- 位置：plan §改动面（L141-148）。
- 现象：L47 提到「`tasks_index.json` 重建时自然带上新字段」，但改动面表未列出 index schema 变更的影响范围（下游消费方）。`tasks_index.json` 是派生缓存，新增 `depends_on`/`conflicts_with`/`schedule_status` 字段后，任何读 index 的脚本/tooling 需感知。
- 影响：若存在外部消费 index 的工具（如 dashboard），新增字段可能破坏解析。
- 建议：改动面补一行说明 index schema 扩展为向后兼容（新增字段，旧消费方忽略即可），或列出需同步的下游。
- 置信度：中
- 优先级：低

### L3：待办 L166 引用 `decision_log.md` 与实际文件名 `decisions.md` 不一致

- 位置：plan §待办（L166）。
- 现象：plan 写「落地后更新 `decision_log.md`」，但仓库实际文件为 `docs/blueprint/decisions.md`（见 AGENTS.md L24、decisions.md 自身标题「决策记录（ADR）」）。且 plan 引用「L21」「L4」行号，但 decisions.md 现仅 L001/L002 两条，无 L4/L21 行号对应（行号指 decisions.md 内部编号还是 plan 内部行号不明）。
- 影响：实施时按 `decision_log.md` 找不到文件；行号引用失效。
- 建议：文件名改为 `docs/blueprint/decisions.md`；「L21」「L4」改为决策编号引用（如「D001 结论」「D002 背景」）或删除行号用语义描述。注意 plan 本身是 docs_repo 设计笔记，不受 AGENTS.md「禁止元引用」约束，但落地到 decisions.md 时须遵守该规范。
- 置信度：高
- 优先级：低

## 改进建议

1. 补「与 L001/L002 关系」节（见 H2），明确并行批拓扑是扩展还是替代。
2. 明确 `start` 门禁的 active/blocked 数据源口径（见 H1），建议复用 tasks-parallel SKILL 的建基线逻辑。
3. 重新审视 `conflicts_with` 在算法中的语义（见 H3），避免互斥图降级为依赖边。
4. `edit --conflicts-with` 自动补写反向边（见 M3）。
5. `tasks-schedule` 保留 `disable-model-invocation: true`（见 M4）。
6. 统一 `--done` 解析规则（见 M2），按整数范围而非字符串长度判。
7. 修正待办文件名与行号引用（见 L3）。
8. 验证项补 `--done` 宽容解析用例（见 L1）。

## 不确定项

1. `tasks-schedule` 改名后，旧 `tasks-parallel` 软链删除是否影响正在进行的 task worktree（worktree 内可能仍引用旧 skill 名）——需实施时验证，plan 未提迁移策略。
2. 多链尾合并冲突时「暂停报告冲突 task 对」后，用户解决冲突的具体 git 操作流（继续 merge / abort / rewind）未在 plan 规定，属实施细节，但需确保与 `git merge --abort` 回退路径（L002 结论）一致。
3. `schedule_status` 加入 `FRONT_MATTER_KEYS` 后，`tasks-run` 串行流程是否需要感知该字段（plan L161 验证项称「无调度字段的 task 行为不变」，但 `schedule_status: pending_clarification` 的 task 若进入 `tasks-run` 串行链，是否应阻断）——plan 未说明。
4. 菱形依赖汇点「等前置全部 done 后自然进入后续批次，从最新 main 开 worktree」（L107）隐含批次间 main 推进后重新 `start` 的基线更新，但 `start` 门禁（H1）若读取主仓 `docs/tasks/` 副本而非 worktree 状态，汇点 task 的 depends_on 校验可能基于滞后副本——需实施时确认 `start` 读的是 ref 还是主仓副本。
