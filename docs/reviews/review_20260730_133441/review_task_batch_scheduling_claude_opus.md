# 审阅报告：task 批次调度方案（plan_task_batch_scheduling.md）

## 当前模型判断依据
主会话模型：未知。上下文仅提供 default_model 占位符，无可靠模型 ID；用户调用 my-review 并授权 opus，本路由按 opus 视角独立审阅，不复用 antigravity 路结论。

## 模块 slug
task_batch_scheduling

## 审阅范围
`/home/karon/karson_ubuntu/repo_template/docs_repo/plan_task_batch_scheduling.md` 全量。交叉核对对象：`scripts/task.py`、`.agents/skills/tasks-parallel/SKILL.md`、`.agents/skills/tasks-run/SKILL.md`、`docs/blueprint/decisions.md`、`docs/tasks/task_template/task.md`、`CLAUDE.md`。

## 高优先级

### H1 并行批与既有「链式分支」决策（decisions 001）正面冲突，方案未给拓扑裁决
- 位置：plan Line 105-109（批的消费方式、多链尾合并）；对照 `docs/blueprint/decisions.md` 001 与 `CLAUDE.md`「后续 task 基于上一 task 分支」
- 现象：现行模型是**单链**——后续 task 必须基于上一已完成 task 分支启动，最终只合并链尾。并行批要求批内 N 个 task 各自从 main（或各自 base）开 worktree 并行推进，产生 N 条互不为祖先的链尾。plan 承认了这点（Line 109「链尾从 1 个变 N 个」），但把它定性为「`task.py` 层面的小扩展」，未修改 decisions 001，也未说明并行批期间 `start --base` 的语义：批内第二个 task 的 base 是 main 还是批内先完成的兄弟分支？若基于兄弟分支则批内产生先后、破坏「批内无依赖」前提；若都基于 main，则与「后续 task 基于上一 task 分支」的 CLAUDE.md 硬性规则直接矛盾。
- 影响：这是方案最核心的语义断层。两条规则并存时，`start` 实现无法同时满足；`tasks-run` 的「链尾发现：存在多个互不为祖先的链尾时整批停止」逻辑（tasks-run SKILL.md Line 51）会把并行批产物判为异常并整批停止。
- 建议：在方案中显式新增/替代一条决策，定义「并行批」拓扑：批内 task 一律从同一 base（main 或上一批链尾）分叉、批内互不依赖、批末多链尾按 tid 升序逐个 merge；并明确 decisions 001 是被「替代」还是「按批模式二选一」。同步改写 CLAUDE.md「后续 task 基于上一 task 分支」为按模式分述。
- 置信度：高
- 优先级：高

### H2 ready 推导公式未排除 pending_clarification / 未调度 task，与失效条件自相矛盾
- 位置：plan Line 79-82 与 Line 125-129
- 现象：算法定义 `ready = backlog 中 depends_on ⊆ done 的 task`。`depends_on` 为空即满足 ⊆ done，故 `schedule_status: pending_clarification` 的 task 与从未跑过 tasks-schedule 的无字段 task 都会被算进 ready 并可能进批。而 Line 82 输出原因里列了「待澄清 / 未调度」、Line 129 又把「后补的 backlog task 不在图内」列为报错退出条件。三处口径互斥。
- 影响：按 Line 79 原样实现会把未判定依赖的 task 直接并发放行，正好摧毁本方案要解决的问题（语义判断只做一次且可信）。
- 建议：ready 公式补前置条件：task 须已调度（存在调度字段或显式标记 scheduled）且非 pending_clarification；「未调度」统一为单一处置——建议默认阻塞输出清单（fail-closed），提供 `--allow-unscheduled` 之类显式放宽，而不是一处报错一处仅列清单。
- 置信度：高
- 优先级：高

### H3 多链尾合并缺 CLI 承载点与断点恢复语义
- 位置：plan Line 107-109、Line 143
- 现象：方案说「批末多链尾合并是 task.py 层面的小扩展，按 tid 升序逐个合并，冲突暂停报告」，但未定义：挂在哪个子命令（新 `merge-batch`？还是扩展 `finish`？）、入参（自动发现未合并链尾还是显式列分支）、合并到第 k 条冲突后已合并的 k-1 条是否保留（答案应是保留，但方案未写）、恢复后如何续跑、index 重建在哪一步发生。
- 影响：实现者无法据此落地；用户中断后状态不可推断（main 已含部分 task 的 merge commit，剩余分支游离），违反本仓「非幂等操作失败后先查实际状态」的容错约定所需的先验信息。
- 建议：补一节「merge-batch 语义」：子命令名、链尾自动发现规则（沿用 tasks-run 的 merge-base 归并）、逐条合并的进度记录方式（建议不落盘，每次调用现算剩余链尾，天然幂等）、冲突时输出已完成集与剩余集、index 只在全部链尾合并完成后重建一次。
- 置信度：高
- 优先级：高

### H4 `start` 冲突门禁存在时间窗（TOCTOU），并行批两个会话可同时通过校验
- 位置：plan Line 100-103
- 现象：门禁校验 `conflicts_with` 与「当前 active/blocked task」的交集。但 `start` 是在干净主仓执行、状态写入新 worktree 的执行 commit；两个会话几乎同时 start 两个互相冲突的 task 时，彼此都看不到对方的 active 状态（对方 commit 尚未发生或 main 未感知），双门禁均放行。
- 影响：conflicts_with 的硬保证在并发 start 下失效，退化为「最佳努力」。方案把冲突当硬边界设计（Line 31「不可同批并行」），但机制上保证不了。
- 建议：承认该时间窗并在方案中写明补偿：把 `start` 门禁定位为单会话顺序 start 的保护；并行 start 的正确性由「批内两两无冲突」的调度输入保证，冲突漏标的兜底是批末 merge 冲突暂停（Line 109 已有）。即把 start 门禁降级为防御层、把 merge 冲突升级为权威兜底，文案对齐，避免读者误以为门禁是强一致保证。
- 置信度：高
- 优先级：高（定性问题，机制上无法靠 start 侧消除）

### H5 active/blocked 集合读取口径在并行批下失真
- 位置：plan Line 74「active/blocked 集合从 docs/tasks/ 读」；对照 task.py 头部「task 状态读取优先级：登记 worktree → 未合并 task 分支链尾 ref → main」
- 现象：批次执行期间 active 状态写在该 task 自己 worktree 的 task.md 里，主仓 `docs/tasks/` 的 main 副本仍是 backlog（CLAUDE.md 明示「批次期间 main 中 task 状态可能滞后」）。`next-batch` 若只读主仓 docs/tasks/，看不到任何 active/blocked，会把与进行中 task 冲突的 backlog task 排进下一批。
- 影响：next-batch 在批次进行中给出错误批次，与 start 门禁读同一失真源（H4 的另一半）。
- 建议：next-batch 的 active/blocked 发现逻辑复用 tasks-parallel 既有基线方法（`git worktree list` + `list --ref <未合并分支>` 归并链尾），方案 Line 74 需改写为与该口径一致，而非「从 docs/tasks/ 读」一句带过。done 集合读 archive 是对的（finish 后目录已移入 archive 且分支保留），这点不变。
- 置信度：高
- 优先级：高

## 中低优先级

### M1 `edit` 当前拒绝改非 backlog 且拒绝链上覆盖 task，与「澄清后 edit 清除 schedule_status」存在流程断点
- 位置：plan Line 59；对照 task.py cmd_edit（status != backlog 直接拒绝、task_effective_state 覆盖拒绝）
- 现象：tasks-schedule 只对 backlog 建图，edit 也只允许改 main 中未进链的 backlog，这部分自洽。但 plan 未说明：已 active 的 task 被 rewind 回 backlog 后，其旧 depends_on/conflicts_with 是否还有效（spec 可能在执行期漂移）；也没说 tasks-merge 合并两个 backlog task 时源 task 的调度字段如何并入目标。
- 影响： rewind/merge 后调度图悄悄过期，next-batch 按旧图放行。属于 H2 之外的第二类漂移通道。
- 建议：方案补规则——rewind 至 backlog 的 task 由脚本自动置 `schedule_status: pending_clarification`（或文档规定必须重跑 tasks-schedule）；tasks-merge 的改动面中补「源 task 调度字段并集写入目标、指向源 tid 的第三方引用重写为目标 tid」。
- 置信度：中
- 优先级：中

### M2 edit 列表参数表只列 depends 系列，conflicts 系列靠「语义对齐」一句推断
- 位置：plan Line 112-121
- 现象：参数表给出 `--depends-on / --depends-append / --depends-remove`，conflicts 侧仅 Line 121 提了对称性。实现者需自行类推 `--conflicts-with / --conflicts-append / --conflicts-remove` 的存在与命名。
- 影响：命名漂移风险（`-remove` vs `-delete`、单复数），CLI 帮助与 AGENTS.md 示例不一致。
- 建议：表格补齐六个参数全名与语义。
- 置信度：高
- 优先级：中

### M3 算法描述未写明无向化时点
- 位置：plan Line 79-82 与 Line 121
- 现象：无向图要求写在 Line 121（edit 语义节），算法节（Line 80-81）只写「不与 active/blocked task 冲突者」。实现若按算法节字面构图，单向声明漏判。
- 影响：同 antigravity 路中优 2，独立核对确认成立。
- 建议：算法节直接写「先将全部 task 的 conflicts_with 并成无向邻接表（A→B 蕴含 B→A），再做过滤与贪心」。
- 置信度：高
- 优先级：中

### M4 schedule_status 枚举、清除路径、进入 next-batch 输出的格式未规范
- 位置：plan Line 42、Line 59、Line 159
- 现象：仅一个枚举值 pending_clarification；清除靠「edit 清除」一句，未给参数形式（`--schedule-status ""`？还是编辑 depends_on 时自动清？）；FRONT_MATTER_KEYS 加入后 `list` 输出、index 记录结构（task.py Line 633 按 FRONT_MATTER_KEYS 派生 record）会变，方案未提 index schema 变化对既有消费者的影响。
- 影响：残留状态污染后续批次；index schema 静默变更。
- 建议：明确 schedule_status 仅允许 `pending_clarification` 或缺省；edit 写 depends_on/conflicts_with 时若值非空自动清除 schedule_status（少一个手忘的通道），同时提供显式 `--schedule-status` 覆盖；说明 tasks_index.json record 增加三 key 属向后兼容变更。
- 置信度：中
- 优先级：中

### M5 验证清单未覆盖并行批拓扑与 tasks-run 的多链尾互斥
- 位置：plan Line 150-161
- 现象：验证项覆盖 next-batch 算法、start 门禁、edit 语义、多链尾合并本身，但缺：①两个会话并行 start 冲突 task 的时间窗行为（H4 的验证）；②并行批产生多链尾后，`tasks-run` 的链尾发现逻辑（多链尾整批停止）是否会被误触发——tasks-run 与并行批共存场景没有回归项；③rewind 后调度字段处置（M1）。
- 影响：H1/H4/M1 的问题在验证阶段发现不了。
- 建议：补三条验证：并行 start 时间窗文档化行为、tasks-run 遇并行批多链尾的明确行为（拒绝并提示走 merge-batch）、rewind 后 schedule_status 状态。
- 置信度：中
- 优先级：中

### L1 done 集合定义忽略「分支已 done 但 finish 未归档」与 dropped 引用的细粒度
- 位置：plan Line 74、Line 85
- 现象：done 仅认 archive 中 status: done。`--done` 补充机制覆盖了「finish 未走」的场景，但 start 门禁（Line 102「depends_on 全部 done」）是否同样认 --done 无法传入（start 无此参数）——前置 task 已执行 commit、cleanup 完成但 finish 归档未做时，下游 start 被挡，方案未给逃生口。
- 影响：正常收尾顺序（finish 在 worktree 内、archive 移动即时完成）下窗口极小，但 finish 归档移动失败回滚（task.py _close_task）正好制造这个窗口。
- 建议：方案注明 start 门禁的 done 口径 = archive done ∪ （可选）`--base` 链尾 ref 中已 done 的 tid；或接受该窗口并在门禁报错信息中提示「前置已提交未 finish，请先 finish」。
- 置信度：中
- 优先级：低

### L2 `--done` 宽容解析的「超出 tid 范围拒绝」与动态 tid 上限
- 位置：plan Line 94
- 现象：「超出 tid 范围」依赖查询当前最大 tid；4 位以上数字一律拒绝隐含 tid 永不超过 t999（TID_RE 本身是 `^t([0-9]+)$` 无位数限制）。
- 影响：tid 破千后规则需要改；属远期问题。
- 建议：解析规则改为「去前导零后若匹配现存 tid 则接受，否则拒绝」，去掉位数硬编码。
- 置信度：中
- 优先级：低

### L3 范围表重复行
- 位置：plan Line 20-21
- 现象：「`task.py next-batch` 子命令 | 自动开 worktree / 自动合并的驱动逻辑」出现两次。
- 影响：无功能影响，文档瑕疵。
- 建议：删一行。
- 置信度：高
- 优先级：低

## 改进建议

### S1 改动面漏 decision_log/blueprint 与 tasks-run 适配
- 位置：plan Line 140-149 对照 Line 166 与 H1
- 现象：待办里要求更新 `decision_log.md`（仓内实际路径是 `docs/blueprint/decisions.md`，plan 两处名称不一致——Line 166 写 `decision_log.md`），改动面表却未列；H1 所需的 decisions 001 替代条目、CLAUDE.md「后续 task 基于上一 task 分支」改写、tasks-run 与多链尾共存的适配说明均未进改动面。
- 建议：改动面补 `docs/blueprint/decisions.md`（新决策 + 001 替代引用）、`tasks-run/SKILL.md`（多链尾共存行为）、并统一决策文件名称。
- 置信度：高
- 优先级：建议

### S2 拆 task 建议的粒度过粗
- 位置：plan Line 165
- 现象：待办建议拆两 task（①task.py 全部改动 ②skill 改名）。task ①含字段+edit+门禁+next-batch+多链尾合并五个可独立验证单元，违反本仓「需求拆分为可独立验证的 task」原则；且 H1 的拓扑决策未落地前 task ①不可 start。
- 建议：先一个决策/文档 task 落 H1 拓扑裁决与 decisions 更新；再拆 ②edit 列表字段+门禁 ③next-batch ④多链尾合并 ⑤tasks-schedule 改名。至少把多链尾合并与 next-batch 分开。
- 置信度：中
- 优先级：建议

### S3 start 门禁报错格式规范化
- 位置：plan Line 100-103
- 现象：未定义拒绝时输出结构。task.py 现有报错风格为 `sys.exit("start=FAIL：...")`，方案应沿用并机器可读（列出阻断的 tid 与原因类别）。
- 建议：规范输出形如 `start=FAIL：depends_on 未满足: t001(active)；conflicts_with 冲突: t006(active)`。
- 置信度：高
- 优先级：建议

## 不确定项

### U1 「批」是否需要持久身份
- 位置：plan Line 105-109
- 现象：多链尾合并按「tid 升序逐个合并」处理全部未合并 task 分支。若用户同时存在两个独立并行批（A 批 t002/t003，B 批 t005/t006 基于不同 base），合并操作无法区分批次归属，只能按分支发现全量合并。
- 影响：可能需要 batch 标识（front matter 或分支命名约定），也可能用户场景下永不出两批并存。
- 建议：与用户对齐是否存在多批并发场景；若存在，front matter 加 `batch` 字段或接受「合并即全量」。
- 置信度：中
- 优先级：不确定

### U2 旧 tasks-parallel 输出契约的消费者
- 位置：plan Line 51
- 现象：tasks-parallel 改名且产出从「打印首批」改为「落盘+next-batch」。若用户已有 muscle memory 或外部文档引用旧输出格式，改名后行为变化（只读→写）需要显式告知。仓内未见其它引用，但是否有仓外使用无法确认。
- 建议：落地时在 handoff 记一行行为变更。
- 置信度：低
- 优先级：不确定
