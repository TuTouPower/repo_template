# 模板仓库执行架构

task 工具链的执行拓扑、attempt 生命周期与合并授权。项目自身模块架构见 `architecture.md`。

## 执行拓扑

链式（`task-run`）描述 **branch topology**；并发只发生在用户维度——多会话各自跑一条链，无自动调度器。attempt 控制面与 exact identity `(tid, attempt, execution_id)` 仍统一使用。`execution_id` 是执行 provenance；`executor` 为 `inline`。

**链式**。task 按执行顺序一个串一个成链，每个从上一个已完成 task 的分支创建（`--base`）。每个 task 一个执行 commit，中间只清理 worktree；全部完成后由 `integrate-chain` 一次性把链尾合并回主干，主干只进一次 merge commit。

```text
主干 ── t001 ── t002 ── t003 ──► 全部完成后 merge 链尾
```

**手动并发**。无自动调度器。用户用 `task.py view` / `view --serve` 看依赖/冲突/运行中状态，用 `task.py plan` 取**本波**并发链与可复制 `/task-run` 后，在多个会话各自跑一段。`start` 加调度门：依赖未完成（完成口径=`done`，不要求已合并主干；`dropped` 已归档不产出代码，引用 dropped 的边非法）硬拒，冲突方正在运行只警告。多会话同时写主仓不互斥——合并撞车由 git 报错、人来收场，index 是派生缓存、撞了重建。

```text
        主干 ──┬── 会话 A：t001 ── t003 ──► 链尾 merge
               └── 会话 B：t002       ──► 链尾 merge
```

## 本波执行计划（`task.py plan`）

写图（`task-schedule` → `depends_on` / `conflicts_with`）与推链分离。`plan` 只读、不落盘，算法权威在 `repo_task.plan.compute_batch_plan`；CLI 与 `view --serve` 看板共用。

- **输入**：当前有效状态 + 已落盘调度图（与 `compute_schedule` / 节点分类同源）。
- **本波链首**：`active` 与 `runnable`（`runnable` = 调度「下一批可跑」`selected`）；被冲突序号压住的 dep-ready 对端不进链首。
- **链首语义**：`active` = 接续运行中（`task-run` 进已有 worktree）；`runnable` = 新启动。默认输出标 `[接续]`；`--copy` 行尾 `# 接续 active`。
- **链延伸**：单父且已排程的后继可串入；**多父汇流点不进任一条本波链**；与他链/他链首/运行中冲突的后继不跨链并行。
- **输出**：可复制 `/task-run`、链内 `tid + title`、停因、冲突暂缓、下一解锁（汇流注明 integrate/`--base` 约束）。`--serial` 全串行；`--copy` 仅命令；`--json` 机器可读。看板「复制链」与 `--copy` 同形。
- **下一批**：状态变化（链完成、汇流前置 done）后**重跑** `plan`，不重跑 schedule。不冻结多波剧本。

## 调度图语义

task 图的两类边各司其职，不可混用：

|边|表达|不表达|
|------|------|------|
|`depends_on`|必须先后（结果/接口/产物依赖）|改动面相交|
|`conflicts_with`|不能并发（改动面相交）|已由依赖保证的先后|

不变式：有（传递）依赖关系的 task 对禁止声明冲突边——依赖已蕴含串行，冲突边冗余；`task.py edit` 在依赖/冲突变更时强制校验，冲突边两端存在任一方向的传递依赖路径即拒。

冲突的阻塞判定：

- 对端 active/blocked（正占资源）→ 阻塞；
- 对端 backlog、自身依赖已满足（dep-ready）且序号更小 → 阻塞（同批择优的排序信号）；
- 对端 backlog 但仍被依赖阻塞 → 不阻塞。dep-ready 规则下等待环只可能是纯 `depends_on` 环，`view` 以 `invalid_graph: depends_on cycle` 拒绝。

停滞哨兵：已排程 backlog 无可跑项且无运行中 task 时（如前置未排程），`view` 输出「调度停滞」告警并列出涉及 tid。

## 职责分工

同一会话内按阶段区分写域，两阶段写域互不重叠：

|阶段|唯一写域|职责|skill|
|------|------|------|------|
|实施|当前 task worktree|接收必填 `attempt` / `execution_id`，实施、测试、黑盒、review、finish、一个执行 commit；只写精确 identity 的 `handoff.json` 并交出 `{tid}: {branch} @ {sha}`|`task-work`|
|调度合并|主仓|`start`；reserve attempt；写 exact terminal/report；以 exact identity cleanup；单 task `integrate` 或链式 `integrate-chain`；派生 index、分支清理、合并后验证|`task-run` / `task-integrate`|

实施阶段不合并任何分支、不重建 index、不 push、不删分支、不清理 worktree、不询问是否合并主干，也不写 attempt 控制面；唯一交接写入是本 task 分支中的 `handoff.json`。`task-run` 在同一会话依次走完调度合并与实施两个阶段——调度阶段调控制面命令，实施阶段进 worktree 调 `task-work`。多会话手动并发时，每个会话在自己的 worktree 内完整跑链，会话间不互斥——合并撞车由 git 报错、人来收场，index 是派生缓存、撞了重建。

`task-run` 每个 task 依次 `start → attempt reserve --executor inline → task-work → attempt terminal → attempt report → cleanup-worktree`，后继以当前分支作 `--base`，全链最终一次 `integrate-chain` merge；merge/index/integrated 后保留 transaction 与链分支，合并后验证通过再以同一命令 `--continue` 完成删除。`start` 加调度门：`depends_on` 未完成（完成口径=`done`，不要求已合并主干；`dropped` 已归档不产出代码，引用 dropped 的边非法）硬拒，最新前置未合并主干时 base 自动落到其分支 tip；`conflicts_with` 对方正在运行（登记 worktree 存在 且 `status=active`）只警告后放行。

goal 模式是队列循环的自治外壳，不改变执行语义：`task.py goal` 按 task-run 入队规则冻结队列快照（`docs/runtime/goal_queue.json`，覆盖式，同时只服务一个队列）并打印 ready-to-paste 的 `/goal` 行；goal 会话内行为与 task-run 队列循环一致，终态由 `task.py goal-check` 只读判定——权威为 ledger 投影、主干状态与 worktree 登记，输出 `GOAL_QUEUE_COMPLETE`（全闭环）/ `GOAL_QUEUE_STOPPED`（含 blocked/failed，合法停止）/ `GOAL_QUEUE_INCOMPLETE`（继续）三个 marker。合并授权在 goal 判定范围外，仍是整链完成后的人工步骤。

## 合并授权

合并主干需用户**会话级授权**，且不在启动时询问。`task-run` 先执行整条链并完成 exact cleanup，完成后询问一次是否需要合入，用户同意后才首次调用 `integrate-chain`；未获授权不 merge，保留已清理的链分支。合并环节只有 merge 冲突需裁决、合并后验证失败、task `blocked` 或范围扩大时停下来问用户；执行环节停止条件见各 skill。多会话并发时，不同链各自走自己的 `integrate-chain`，互不授权；先后合并冲突由 git 报错、人来收场。

`.claude/hooks/merge_guard.py` 拦截 Bash 工具里的 `git merge`（含 `--abort`，要求一次性 token）；`task.py integrate` / `integrate-chain` 内部 merge 经 subprocess 不经 Bash 工具，由会话级授权覆盖，hook 不拦。两层职责分离：脚本通道 = 已授权入口，hook = 防 agent 在脚本外手动 merge。
