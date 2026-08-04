# 调度控制面：水位触发 reconcile + 调度账本

来源：omni_media 并行调度两起事故（2026-08）——4 个 opus worker 因 provider 不兼容全灭、coordinator 未察觉靠用户追问；integrate 完成后未自动补位、用户点名才 start。两起事故同一病根，本文给出目标架构与落地设计。

## 病根

当前调度循环 `view → start → 派发 → 收汇报 → integrate → 回第 1 步` 是**边沿触发**（edge-triggered）模型：每个事件绑定一个特定动作，动作链靠 agent 上下文里的「记得」驱动。三个结构性错配：

1. **执行者是回合制 agent，不是守护进程**。回合之间什么都不在运行，「回第 1 步」没有机械力量。
2. **义务存在于散文与上下文，不存在于环境**。上下文会压缩、会话会重启——只存在于 agent 脑子里的义务迟早丢失。
3. **状态是复制存储而非派生计算**。同一事实存在 worktree/分支/主干 front matter、index、worker 口头汇报多份拷贝，靠优先级约定手工对账。

边沿触发系统丢一条边沿就丢一个动作，且**沉默不是事件**——worker 全灭后的死寂没有任何东西能打破。

## 目标架构

核心转变：**边沿触发 → 水位触发（level-triggered）**。不记忆义务，重计算义务。

### reconcile：唯一入口

整个调度系统收敛为一个幂等、全量的函数：

```text
reconcile():
    observed = git refs + 调度账本 + worktree 观察 + handoff.json
    desired  = 授权范围 ∩ 可跑集 ∩ 并发上限 ∩ 冲突互斥
    plan     = diff(observed, desired)   # dispatch / redispatch / integrate / escalate
```

每次被唤醒（worker 通知、用户消息、cron 到点、integrate 完成）不问「刚才发生了什么事件」，而是重新计算世界应该是什么样、现在是什么样、差在哪。漏掉的边沿由下一次 reconcile 算回来。「补位」「收汇报」「失败重试」「解锁」不再是流程步骤，只是 reconcile 的 diff 输出。

**空闲许可**：coordinator 只有在一次干净的 reconcile（plan 为空）之后才允许结束回合。这一条机械锚点消灭「忘了补位」。

### 事件源

| 事件源 | 语义 |
|--------|------|
| worker 完成通知（成功或死） | 只是「该 reconcile 了」；内容不携带真相、不被信任 |
| cron 定时（默认每 10 分钟） | 死人开关：worker 卡死、通知丢失、全员沉默时兜底 |
| integrate 完成（回合内同步） | 回合内直接再跑 reconcile，不结束回合 |
| 用户消息 | 天然触发 |

### 调度账本（持久控制面）

`docs/runtime/dispatch_ledger.jsonl`（gitignore，仅主仓，append-only JSONL）。引入 **attempt** 概念：同一 tid 第 N 次派发记为 `tid#N`，记录模型、父尝试、重试原因。会话重启、上下文压缩后，读账本 + refs 完整重建调度世界，不依赖任何上下文记忆。

事件类型：

| event | 字段 | 写入者 |
|-------|------|--------|
| `start` | tid, branch, worktree | `task.py start` 自动 |
| `dispatch` | tid, attempt, model, parent_attempt?, reason | coordinator 派发 worker 时 `ledger record` |
| `report` | tid, attempt, status(done\|blocked\|failed), sha?, class?, reason? | coordinator 收到 worker 通知时记录（线索，非真相） |
| `failed` | tid, attempt, class(infra\|task\|resource\|contract), reason | coordinator 分诊后记录 |
| `integrated` | tid, merge_sha | `task.py integrate` 自动 |
| `escalated` | tid, attempt, reason | coordinator 升级用户时记录 |
| `breaker` | model, state(open\|closed), reason | coordinator 熔断/恢复模型时 `ledger record` |
| `note` | tid?, text | 自由备注 |

### worker 交接契约

worker Step 7 写 `docs/tasks/{tid}_{slug}/handoff.json` 并随执行 commit 入库（finish 后随目录在 archive 侧）。schema：

```json
{"tid": "t290", "status": "done", "branch": "t290_x", "base_sha": "<执行 commit 前 HEAD>",
 "tests": "...", "blackbox": "...", "review": "...", "pending": ["p047"], "findings": ["d012"]}
```

reconcile 对 report 事件做**机器验证**，通过才输出 `integrate` 动作：

1. 分支存在且有未合并 commit；
2. 分支 tip 的 task.md front matter status 为终态（done/dropped）；
3. 分支 tip 存在 handoff.json，可解析，`status` 为终态、`tid` 与本 task 一致、`branch` 与解析出的分支名一致。

任一不过 → 判 `contract` 类失败进入重试策略（同模型 resume，原 worktree 补交接单）。worker 没写交接单 = 未完成，无论口头汇报什么。

report 是**加速线索，不是必要条件**：reconcile 对每个在飞 attempt 先查 refs——分支 tip 终态且 handoff.json 齐备，无论有无 report 事件都直接输出 integrate 动作（READY_MERGE）。通知丢失、worker 死于汇报前、会话重启丢上下文，都不阻塞合并。

### 失败分类策略表

| 失败类 | 判定 | 自动策略 | 升级用户 |
|--------|------|---------|---------|
| infra（API 错误、provider 不兼容） | 早死、worktree 无产出、通知错误码 | 按模型阶梯换模型重派；无产出从头、有产出续跑；同一模型连续 2 次 infra → 记 `breaker` 事件熔断，session 内选档自动跳过 | 阶梯用尽（含熔断后无档可降） |
| resource（上下文爆、stalled） | worktree HEAD/mtime 超 `--stall-minutes` 无推进 | 续跑新 attempt | 同 tid 连续 2 次 |
| contract（无交接单、refs 验证不过） | reconcile 验证步骤 | 同模型 resume：原 worktree 补交接单后 finish，不换模型 | 重犯 |
| task 级（黑盒/review 满轮 blocked） | report status=blocked | 现有 blocked 流程 | 总是 |

策略表覆盖的不问用户；覆盖不了的才升级。

### 准入控制（派发前）

- **查不兼容登记**：`docs/findings/` 中已有「模型 X 在 provider Y 下报 Z」记录时直接走阶梯降级，不重复献祭。
- **金丝雀**：并发 >1 且首选模型无本地成功记录时，先派 1 个 worker，reconcile 确认 `progressing` 再扇出其余。
- **模型阶梯**：会话级授权一次说清（如 `opus>haiku`），infra 失败自动降级，不再逐次问。
- 确认新的不兼容事实后 `findings.py new` 登记。环境事实只被发现一次。

### 可观测性

`task.py ps`：活表（tid / attempt / model / state / last_activity / note），数据全部来自账本 + refs + worktree 观察。「现在发生了什么」一个命令回答，不需要问 agent、不需要数 UI 图标。

## 脚本设计（task.py 新增）

```bash
task.py ps [--all] [--stall-minutes N]     # 活表；默认隐藏终态
task.py reconcile [--limit N] [--tids t001,t002] [--model-ladder "opus>haiku"]
                  [--stall-minutes N] [--max-auto-retries N] [--json]
                                            # 只读输出行动计划，不执行
task.py ledger record --event EVENT --tid TID [--attempt N] [--model M]
                  [--status S] [--sha SHA] [--class C] [--reason TEXT]
task.py ledger tail [--tid TID] [-n N]     # 读账本
```

- `start` / `integrate` 自动 append 对应事件，无需手工记录；`integrate` 的 skip-merge（已合入）路径同样记 `integrated`。`--continue` 在 commit 前解析分支并校验 MERGE_HEAD 属于该 tid。
- `reconcile` **无副作用**：只算 diff 输出计划；动作由 coordinator 执行（start/integrate 走脚本，派发走 Agent 工具），执行后记录账本。
- 可跑集计算从 `cmd_view` 抽出共用函数，reconcile 不复制调度图逻辑。
- attempt 号 = 该 tid 账本中 max(attempt)+1，`ledger record --event dispatch` 省略 `--attempt` 时自动分配；report/failed/escalated 省略时归属当前 attempt。
- 失败判定按 attempt 取末条处置事件（failed 与 report 参与）；`report status=failed` 等价 failed 事件。分支已生效 blocked 直接 escalate，不走 stalled 重试。
- **escalate 闩锁**：escalated 之后无新 dispatch 的 tid 不被自动派发；用户裁决后 coordinator 手动 dispatch 落账解除。
- **占槽**：progressing、待合并（integrate）、redispatch 占槽；escalate 释放槽——重派 + 补位不会突破并发上限。占槽按全局在飞计，与 `--tids` 授权范围无关（动作输出仍受范围约束）；主干已终态的 tid 不参与冲突互斥判定。
- **redispatch mode**：`resume`（原 worktree/分支有产出，直接派 worker 续跑，不跑 start）/ `restart`（无残留，start 重来）。contract 类同模型 resume，无现场可续时 escalate。
- 阶梯内无未尝试模型（钳回父 attempt 同档）时，仅 infra 换模类输出 escalate；resource/task/contract 允许同模型续跑吃满重试额度，不做无意义换模。
- stalled 判定：worktree HEAD commit 时间与最近 dispatch 时间的较大者，距今超 `--stall-minutes`（默认 20）无推进。观察优于心跳——不要求 worker 配合。
- 账本：append 持跨平台文件锁防交错写；坏行（截断写）warn + skip 自愈，不使整个控制面不可用。

## skill 改造

- **task-dispatch**：正文从「调度循环流程图」改为 reconcile 语义——授权（范围/并发/模型阶梯/cron 节拍）→ 注册 cron → 每次唤醒跑 reconcile 执行计划 → 空闲许可。保留并行纪律与停止条件（对应 escalate 动作）。
- **task-work**：Step 7 增加写 handoff.json；交出一行保留但不再是契约本体。
- **task-integrate**：注明 integrate 自动写账本；其余不变。
- **task-run**（串行）：单 worker 同步执行，本次不改；reconcile/ps 对串行同样可用。

## 非目标

- 不引入守护进程/消息队列——cron + 事件触发足够，保持「文件 + git + 脚本 + agent」哲学。
- 不让 worker 发心跳——观察优于汇报。
- 不搞多 coordinator/分布式锁——单写者前提保留。
- 集成隔离（integrate 迁入专用 worktree，与用户主仓脏改动解耦）与状态全派生化（front matter 降为缓存）是后续演进方向，不在本轮。

## 验收标准

1. worker 在 worktree 完成 finish + commit 但通知丢失 → 下一轮 reconcile 直接输出 integrate（refs 派生），用户零追问。
2. integrate 解锁下游且有空槽 → 同一回合内 dispatch，不出现「只说了先补位」。
3. infra 失败空 worktree → 不以相同参数盲派；同一模型连续失败触发熔断，`ps` / reconcile 输出可见降档与原因。
4. 并行中主仓 `show` 为 backlog 但分支已 done → `ps` 显示 done 待合并，不错误重派。
5. 用户「xxx 启动没 / 合并没」的问句只作校验、不作触发器——若问句触发了动作，说明 reconcile 节拍漏了一拍，按事故处理。

## 本轮落地范围

账本与 attempt、`ps`、`reconcile`（含 refs 派生 READY_MERGE、handoff 机器验证、stalled 判定、模型阶梯与熔断）、`ledger record/tail`、`start`/`integrate` 自动记账、handoff.json 契约、三个 skill 改造、cron 节拍写入 task-dispatch、AGENTS.md 同步。
