# 计划：task 批次调度（首轮 Agent 分析，后续脚本推导）

2026-07-30 用户提出，审阅决策已通过。实施按用户特批直接修改 main，保持未提交。

## 需求

`tasks-parallel` 现状：每次调用都由 Agent 重新读取全部 backlog task 的 spec，分析依赖与改动面冲突，只输出当时第一批可并发 task。用户完成一个或多个 task 后，要获取下一批时必须重新调用 Agent，速度慢且结论可能漂移。

目标：

1. Agent 首次分析当前 backlog task，将依赖、冲突结论写成结构化调度数据。
2. 后续用户把已完成 task ID 传给固定脚本，脚本用纯图算法计算下一批，零 LLM。
3. 只设计调度层。脚本输出 task ID 后，用户自行调用多个 Agent 分别执行现有 `tasks-run tNNN`；不改 `tasks-run`、`task.py start`、worktree、分支或合并流程。

## 范围

| 做 | 不做 |
|----|------|
| front matter 增加调度字段 | 修改 `tasks-run` 或 `task.py start` |
| `tasks-parallel` 改名 `/tasks-schedule`，分析并落盘调度图 | 新增执行层 skill |
| `task.py next-batch` 纯脚本命令 | 自动启动 Agent、worktree、分支或合并 |
| 调度数据的 edit、rewind、merge、drop 生命周期 | batch 身份、priority 字段、第三张图 |

## 调度模型

### 依赖 DAG

`depends_on` 表示硬前置关系：下游 task 只有在全部前置被视为完成后才能进入下一批候选。

### 互斥图

`conflicts_with` 表示两个 task 不可同时执行：

- active/blocked task 会阻止与其冲突的 backlog task 进入新批次；
- 同一批次内 task 两两不得冲突；
- 冲突只约束并发窗口，不进入依赖 DAG，也不规定谁先完成。

不能把冲突写成依赖边，否则会虚构顺序并损失并行空间。

## 数据落点

状态权威继续是 `docs/tasks/{tid}_{slug}/task.md` front matter，只允许通过 `scripts/task.py` 写入。

当前 front matter 解析器只支持标量，列表采用逗号分隔的规范 tid 字符串：

```yaml
depends_on: "t001,t003"
conflicts_with: "t006"
schedule_status: "scheduled"
```

字段定义：

| 字段 | 值 | 语义 |
|------|----|------|
| `depends_on` | 逗号分隔 `tNNN`，空字符串表示无依赖 | 依赖 DAG 入边 |
| `conflicts_with` | 逗号分隔 `tNNN`，空字符串表示无冲突 | 无向互斥边 |
| `schedule_status` | `scheduled` | 已完成调度分析；即使两张边表都为空，也可进入算法 |
| `schedule_status` | `pending_clarification` | spec 改动面或依赖无法确认，不得进入批次 |
| `schedule_status` | 缺失/空 | 未调度（通常是新建 task），不得进入批次 |

不另建调度文件。调度元数据随 task 状态和归档移动，避免第二状态源及 hash 漂移。

`tasks_index.json` 重建后新增同名字段，属于向后兼容 schema 扩展；旧消费者可忽略新字段。

## `/tasks-schedule`：Agent 分析并写入调度图

原 `/tasks-parallel` 改名 `/tasks-schedule`。保留：

```yaml
description: none
disable-model-invocation: true
```

仅用户斜杠或合法 skill 链式调用可触发。skill 从只读变为写操作，但禁止直接编辑 `task.md`，所有写入必须调用 `task.py edit`。

分析步骤沿用现有 `tasks-parallel`：

1. 读取登记 worktree、未合并 task 分支 ref 与 main，建立进行中基线。
2. 读取 backlog task 的 spec 范围、非范围、依赖与约束、blueprint 更新点。
3. 推导代码路径、共享契约和显式顺序依赖。
4. 生成 `depends_on` DAG 和 `conflicts_with` 无向图，校验悬空引用与环。
5. 对每个分析范围内 task 写入：
   - 判断完整：依赖/冲突字段 + `schedule_status=scheduled`；
   - 无法判断：`schedule_status=pending_clarification`，不猜测。
6. 调用 `task.py next-batch` 输出第一批。

无参数时分析全部 backlog；指定 tid 时补充或重算指定 task，但判断冲突时仍须与全部已调度和进行中 task 比较。

## `task.py next-batch`：机械计算下一批

### 调用

```bash
python3 scripts/task.py next-batch
python3 scripts/task.py next-batch --done t11 t012 13 t0015 T14 T00025
python3 scripts/task.py next-batch --done t11,t012,13
```

可选个人 shell alias：

```bash
alias tn='python3 scripts/task.py next-batch'
```

alias 属用户环境配置，不进仓库。Claude Code 不支持固定逻辑自定义 slash command，因此不创建 `/tasks-next` skill。

### 状态来源

命令仅允许从主仓调用，状态读取优先级与现有 task 流程一致：

1. 登记 worktree 中的 task 状态；
2. 未合并 task 分支 ref 的累计状态；
3. main 状态；
4. `docs/archive/tasks/` 中的 done/dropped 状态。

该基线发现逻辑在 `task.py` 内实现为共用函数，不复制到 skill。

`--done` 是本次计算的用户完成集：

- 与仓库发现的 done 集合取并集；
- 若传入 tid 在仓库中仍为 active/blocked，本次计算按 assumed done 处理，不再计入 active/blocked；
- 只影响本次输出，不改 front matter、不归档、不执行 finish、不声明代码已合并；
- 用户需要累计传入尚未进入仓库 done 状态的所有已完成 task。

### `--done` 宽松解析

仅此人机入口宽松；Agent 和其他脚本入口继续严格使用仓库记录的规范 tid。

解析规则：

1. 支持空格、逗号或两者混合分隔多个 ID；
2. 去除可选的大小写 `t` 前缀；
3. 剩余部分必须是正整数数字串；
4. 转为整数后，在当前 task、归档 task、worktree/ref task 全集中按数值唯一匹配；
5. 输出和内部计算使用仓库实际记录的规范 tid。

示例：

| 输入 | 规范化结果 |
|------|------------|
| `t11` / `T11` | `t011` |
| `13` | `t013` |
| `t0015` | `t015` |
| `T14` | `t014` |
| `T00025` | `t025` |
| `t1000` | 若仓库存在则为 `t1000` |

非数字、数值 0、无对应 task 或数值匹配不唯一时，报错并列出原始输入，不猜测。

### 算法

先把所有 `conflicts_with` 声明归一化为无向邻接表，兼容已有单向数据。

```text
assumed_done = 仓库 done ∪ --done
active       = 仓库 active/blocked - --done
scheduled    = backlog 且 schedule_status=scheduled
ready        = scheduled 且 depends_on ⊆ assumed_done
eligible     = ready 中不与 active 冲突的 task
next_batch   = eligible 按 tid 数值升序做确定性贪心独立集
```

贪心规则：依次扫描 eligible；与已选 task 冲突则跳过。结果保证互不冲突，但不保证批次大小全局最优。冲突密度高导致批次过小时，重跑 `/tasks-schedule` 检查是否过度保守。

`pending_clarification` 和未调度 task 均不进入 scheduled：

- `pending_clarification` 单列；
- 缺失/空 `schedule_status` 单列为 unscheduled；
- 未调度 task 按 task fail-closed，但不阻断其他已调度 task 的批次计算。

依赖图存在环，或依赖/冲突引用不存在、dropped task 时，输出 `invalid_graph` 并退出失败，不计算批次。

### 固定输出

Python 直接输出，不经 Agent 转述，不另建模板。无内容段省略：

```text
next_batch: t005 t006
assumed_done: t001 t002
waiting_dependencies: t008<-t003,t004
blocked_by_active_conflict: t009<->t007(active)
pending_clarification: t010
unscheduled: t011
```

错误统一：

```text
next-batch=FAIL：invalid_graph: depends_on cycle t003 -> t005 -> t003
```

## edit 参数与一致性

### 依赖字段

| 参数 | 语义 |
|------|------|
| `--depends-on t001,t003` | 整体覆盖；`""` 清空 |
| `--depends-append t004` | 追加单值，幂等去重 |
| `--depends-remove t001` | 移除单值；不存在时报错 |

### 冲突字段

| 参数 | 语义 |
|------|------|
| `--conflicts-with t006,t008` | 整体覆盖；`""` 清空 |
| `--conflicts-append t009` | 追加单值，幂等去重 |
| `--conflicts-remove t006` | 移除单值；不存在时报错 |

冲突字段由脚本维护对称性：覆盖、追加、移除时同步更新受影响 backlog task 的反向边。被引用 task 不是可编辑 backlog 时拒绝操作并列出原因。读取侧仍按无向图归一化，兼容历史单向数据。

### 调度状态

```bash
python3 scripts/task.py edit t005 --schedule-status scheduled
python3 scripts/task.py edit t005 --schedule-status pending_clarification
```

只接受两个枚举值。`/tasks-schedule` 写入完整依赖/冲突字段时设置 `scheduled`；判断不完整时设置 `pending_clarification`。

## 调度数据生命周期

| 操作 | 处理 |
|------|------|
| add | 新 task 无 `schedule_status`，由 `next-batch` 列为 unscheduled |
| rewind 到 backlog | 自动置 `pending_clarification`，保留旧边供重新分析参考，但不参与批次 |
| tasks-merge | 不猜测合并后的图；目标 task 和所有引用源 tid 的 backlog task 置 `pending_clarification`，重跑 `/tasks-schedule` |
| drop | 若任何 task 的依赖/冲突字段引用目标 tid，列出引用并拒绝静默 drop；清理或重算引用后再执行 |
| finish/archive | 调度字段随 task 目录归档，供依赖校验和审计 |

## 批次消费边界

`next-batch` 只输出 task ID 和原因分类，不执行 task。用户取得：

```text
next_batch: t005 t006
```

之后自行调用两个 Agent：

```text
tasks-run t005
tasks-run t006
```

每个 Agent 继续使用现有 `tasks-run` 独立执行。调度方案不规定 worktree base、分支拓扑、finish、merge 或冲突恢复，也不修改 `task.py start`。并行执行与合并策略若需自动化，另立方案。

## 改动面

| 文件 | 改动 |
|------|------|
| `scripts/task.py` | 新增三个 front matter key、列表 edit 参数、冲突边对称维护、调度数据生命周期、状态基线共用函数、`next-batch` 子命令与宽松 `--done` 解析 |
| `docs/tasks/task_template/task.md` | 增加空 `depends_on` / `conflicts_with`；不预填 `schedule_status` |
| `.agents/skills/tasks-parallel/` | 改名 `tasks-schedule`，只经 `task.py edit` 写图并调用 `next-batch` |
| `.agents/skills/tasks-merge/SKILL.md` | 合并 task 后标记受影响调度数据待澄清 |
| `.claude/skills/` | 删除 `tasks-parallel` 软链，新增 `tasks-schedule` 软链 |
| `AGENTS.md` / `CLAUDE.md` | 更新 skill 路由和 `task.py` 使用示例；`tasks-run` 规则不改 |
| `docs_repo/decision_log.md` | 修正 L21 与代码不一致的「已落地」状态，记录本方案待实施范围 |
| `docs/tasks_index.json` / `docs/archive/tasks_index.json` | 重建后增加调度字段（派生、向后兼容，不手改） |

## 验证

正常状态测试通过 `task.py` 命令构造；悬空引用、非法状态和历史单向冲突等兼容/损坏场景可直接构造 front matter fixture。

1. 调度状态：scheduled、pending_clarification、unscheduled 三类准确过滤。
2. DAG：无依赖、链式、菱形、环、悬空引用、引用 dropped task。
3. 冲突图：候选间冲突、与 active/blocked 冲突、单向旧数据归一化、贪心顺序稳定。
4. 状态来源：主仓、登记 worktree、未合并 ref、archive 混合状态遵循优先级。
5. `--done`：逐一覆盖 `t11 t012 13 t0015 T14 T00025`、逗号/空格混合、未来四位规范 tid、无匹配和非法输入。
6. assumed done：参数覆盖仓库 active/blocked 后正确解锁下游，且磁盘状态不变。
7. edit：依赖/冲突覆盖、追加、移除、清空、去重、反向边同步和不可编辑引用拒绝。
8. 生命周期：add、rewind、tasks-merge、drop、finish 后调度状态符合表格。
9. 输出：固定段落、空段省略、错误前缀稳定；不依赖 Agent。
10. 回归：无调度字段的旧 task 仍可由既有 `tasks-run` 正常执行；`task.py start` 行为不变。

## 实施拆分

1. **调度字段与生命周期**：front matter、edit、对称边、add/rewind/merge/drop 规则。
2. **`next-batch` 脚本**（依赖 1）：状态发现、图校验、算法、`--done` 人机解析和固定输出。
3. **`tasks-schedule` skill 迁移**（依赖 2）：改名、软链、路由、分析落盘和首批输出。

落地后更新 `docs_repo/decision_log.md`。本次按用户特批直接修改 main，保持未提交。
