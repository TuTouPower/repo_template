---
name: task-dispatch
description: none
disable-model-invocation: true
---

# task-dispatch

并行调度 task：worker 在各自 worktree 执行，coordinator 以 `task.py reconcile` 水位驱动全局。本 skill 是 **coordinator 角色**：主仓唯一写者，自身不执行 task。角色边界见 `AGENTS.md`「执行角色与合并时机」。

前置：候选 task 的调度图已由 `task-schedule` 写入并提交。

## 会话级前置授权

启动前**一次性**向用户说明并取得授权，之后不再逐 task 询问：

```text
准备调度：{tid 列表}（并发上限 N，默认 3）
worker 模型阶梯：{首选}>{回退}（默认继承当前会话模型；infra 失败自动降档，不再逐次询问）
reconcile 节拍：每 10 分钟 cron 兜底，调度结束时注销
每个 task 完成并验证后立即：cleanup-worktree → merge --no-ff 进本地 {主干} → 重建 index → 合并后验证 → 删除该分支
{依赖 task 列表} 会在其前置合并后由 reconcile 自动解锁补位。
不 push，不动远程。
```

用户指定模型后，派发 worker 时 Agent 调用带 `model: <指定>` + prompt 含 `model_override_authorized: 用户指定，session 授权`；继承会话模型则两者省略。

## 核心规则：reconcile 驱动

本调度器没有「循环步骤」，只有一个动作来源。**每次被唤醒**——worker 完成通知（成功或死）、cron 到点、integrate 完成、用户消息——都运行：

```bash
python3 scripts/repo_template/task.py reconcile --limit N [--tids 授权范围] --model-ladder "{阶梯}"
```

reconcile 只读计算「期望状态 − 观察状态」的 diff，输出行动计划；coordinator 逐条执行：

| 动作 | 执行 |
|------|------|
| `dispatch {tid}` | `task.py start {tid}` → Agent 派 worker（只传 tid、worktree 绝对路径、调用 `task-work`）→ `task.py ledger record --event dispatch --tid {tid} --model {模型}` |
| `redispatch {tid} attempt=N mode=resume` | **不跑 start**：直接派 worker 进原 worktree（`task-work` 自重读状态续跑）→ `ledger record --event dispatch --reason "attempt#N-1 {失败类}: {原因}"`；模型按 reconcile 输出 |
| `redispatch {tid} attempt=N mode=restart` | 确认无残留分支/worktree 阻碍后 `task.py start {tid}` → 派 worker → 落账（同上）；有残留阻碍属异常态，按 escalate 处理 |
| `integrate {tid}` | 调用 `task-integrate {tid}` |
| `escalate {tid}` | 记录 `ledger record --event escalated`，按「停止条件」问用户 |

**空闲许可**：只有 reconcile 输出「plan 为空」才允许结束回合。integrate 完成后必须回合内再跑 reconcile——不结束回合、不等任何通知。用户对「xxx 启动没 / 合并没」的询问只应作校验、不作触发器；若询问触发了动作，说明 reconcile 节拍漏了一拍。

**cron 兜底**：启动调度时注册一次（每 10 分钟，提示语：跑 `task.py reconcile` 并执行其计划）；授权范围全部终态后注销。worker 卡死、通知丢失、全员沉默都靠它打破。

**台账纪律**：dispatch/report/failed/escalated 事件由 coordinator 经 `ledger record` 落账；start/integrated 由脚本自动落账。任何重派前先读 `task.py ledger tail --tid {tid}`，禁止与上次失败完全相同参数的盲目重试。

## 收汇报与分诊

worker 通知到达，先落账再 reconcile：

- 汇报完成 → `ledger record --event report --tid {tid} --status done --sha {sha}`。通知与 report 都只是**加速线索，不是必要条件**：reconcile 对在飞 attempt 先查 refs，分支 tip 终态 + `handoff.json` 齐备即直接输出 integrate——通知丢失、worker 死于汇报前都不阻塞合并。验证不过 → contract 类失败进重试策略。worker 没写交接单 = 未完成。
- 汇报 blocked → `ledger record --event report --status blocked` → escalate。
- 死亡/错误通知 → 分诊 worktree（`git -C {worktree} log --oneline -3`、`git -C {worktree} status --short`：有无产出）→ `ledger record --event failed --class {失败类} --reason {原因}` → reconcile 给出 redispatch 或 escalate。

## 失败分类策略

| 失败类 | 判定 | 自动策略 | 升级用户 |
|--------|------|---------|---------|
| infra（API 错误、provider 不兼容） | 早死、worktree 无产出、通知错误码 | 按模型阶梯降档重派；无产出从头、有产出续跑；**同一模型连续 2 次 infra → `ledger record --event breaker --model {模型}` 熔断**，session 内 reconcile 选档自动跳过 | 阶梯用尽（含熔断后无档可降） |
| resource（上下文爆、stalled） | reconcile 判 stalled（超 --stall-minutes 无推进） | 续跑新 attempt | 同 tid 连续 2 次 |
| contract（无交接单、refs 验证不过） | reconcile 验证步骤 | **同模型 resume**：worker 在原 worktree 补 `handoff.json` / 修状态后 finish，不换模型不重开 | 重犯 |
| task 级（黑盒/review 满轮 blocked） | report status=blocked | 现有 blocked 流程 | 总是 |

策略表覆盖的不问用户；覆盖不了的才升级。熔断恢复：用户明示某模型可用时 `ledger record --event breaker --model {模型} --state closed`。

**escalate 闩锁**：escalated 落账后 reconcile 不再自动派发该 tid——用户裁决后由 coordinator 手动 start/dispatch 并落账 dispatch 事件，闩锁自然解除。不会出现 escalate→自动重派→再失败的无用户循环。

## 准入控制（派发前）

- 查 `docs/findings/` 模型不兼容记录，命中直接按阶梯降档，不重复献祭 worker。
- 并发 >1 且首选模型在本环境无成功记录：先派 1 个金丝雀 worker，reconcile 显示 `progressing` 再扇出其余。
- 新确认的「模型 X 在 provider Y 下报 Z」事实用 `findings.py new` 登记——环境事实只被发现一次。

## 并行纪律

- 同一时刻一个 tid 只有一个在飞 attempt。
- 存在 `conflicts_with` 边的 task 不同时启动（reconcile 已内建此判定）。
- 输出 redispatch 的 attempt 原位占槽；escalate 才释放槽——并发上限不会被「重派 + 补位」组合突破。
- merge 冲突待裁决期间该 tid 持续占槽，不补新 task 顶它的位置。
- worker 之间不通信、不互相读 worktree。
- 合并串行：一次只处理一个 integrate，不并发写主干。
- 某个 task `blocked` 只影响它自己与其 `depends_on` 下游；其余继续调度。
- `reconcile` / `ps` / `ledger` 是主仓限定命令，worktree 内不可用。

## 停止条件

reconcile 输出 `escalate` 或遇以下任一即停下询问用户，其余在跑 worker 不强制中断：

- merge 冲突需用户裁决（`task-integrate` 已尝试语义解决仍无法确定）。
- 合并后验证失败——停止全部后续 integrate 与 dispatch。
- task `blocked`（呈加轮 / dropped 选项）。
- 失败策略表达到升级条件（阶梯用尽、连续失败、contract 重犯）。
- 要跑授权范围之外的 task（范围扩大须重新授权）。
- 需用户提供密钥、环境、产品决策等不可替代输入。
- 主仓出现与调度无关的脏改动且无法安全隔离。

## 完成

授权范围全部终态后：注销 cron，汇报——授权范围、已合并 tid 及各自 merge commit、blocked 或未跑 tid 与原因、当前主干 HEAD、遗留 worktree 与分支（若有）。`task.py ps --all` 与 `task.py ledger tail` 作为汇报的事实来源。
