# 调度控制面：水位触发 reconcile + 调度账本

并行调度采用水位触发模型：coordinator 不依赖上下文记住下一步，而是根据 Git refs、worktree、handoff 和 append-only 账本重复计算「观察状态与期望状态之差」。Worker 静默监控的权威算法与告警流程见 [`plan_worker_silence_monitoring.md`](plan_worker_silence_monitoring.md)。

## reconcile 是唯一动作来源

```text
reconcile():
    observed = git refs + dispatch ledger + worktree registrations + handoff.json + observations
    desired  = authorized scope ∩ runnable graph ∩ concurrency limit ∩ conflict exclusion
    plan     = diff(observed, desired)
```

唤醒来源包括 worker 通知、5 分钟 cron、integrate 完成和用户消息。每次唤醒先观察仍在运行的 attempt，再运行 reconcile。coordinator 只有在一次 reconcile 后 plan 为空且不存在 silent hold 时才可安全空闲。

行动类型：

| action | coordinator 动作 |
|---|---|
| `dispatch` | `task.py start`，派 worker，记录含 `worker_id` 的 dispatch |
| `worker_terminal` | coordinator 查询宿主进入 `completed|failed|stopped` 后记录；必须精确匹配 `(tid, attempt, worker_id)`，解除 integrate/retry/cleanup 门禁 |
| `redispatch` | 仅由显式失败或 contract 缺陷触发，且当前 attempt 已有 `worker_terminal`；按 mode 续跑或重启并记录新 attempt |
| `integrate` | 仅在当前 attempt 已有匹配 `worker_terminal` 且 handoff/refs 验证通过后调用 `task-integrate`；完成后同回合再次 reconcile |
| `escalate` | 记录 escalated 并请求用户裁决 |
| `alert-silent` | 记录 `silent_alerted`，报告用户并停止自动调度；不取消、不重派 |

## 持久控制面

`docs/runtime/dispatch_ledger.jsonl` 是仅主仓存在、gitignored、append-only 的 JSONL。attempt 由 `(tid, attempt)` 唯一标识。

主要事件：

| event | 关键字段 | 写入者 |
|---|---|---|
| `start` | tid, branch, worktree | `task.py start` |
| `dispatch` | tid, attempt, model, worker_id, parent_attempt?, reason? | coordinator |
| `worker_terminal` | tid, attempt, worker_id, status=`completed|failed|stopped` | coordinator 查询宿主终态后 |
| `observation` | tid, attempt, fingerprint, head, worktree, dirty | `task.py observe` |
| `silent_alerted` | tid, attempt, fingerprint | coordinator 报告静默后 |
| `report` | tid, attempt, status, sha?, class?, reason? | coordinator |
| `failed` | tid, attempt, class, reason | coordinator |
| `integrated` | tid, attempt, merge_sha | `task.py integrate`（并行）；串行无 dispatch 可省略 attempt |
| `escalated` | tid, attempt, reason | coordinator |
| `breaker` | model, state, reason | coordinator |
| `note` | tid?, text | coordinator |

账本追加使用跨平台文件锁；损坏行警告后跳过，不让单条截断写破坏整个控制面。

## refs 与 handoff 是完成真相

worker 完成后在 task 分支 tip 提供终态 front matter 与 `handoff.json`。reconcile 在每个在飞 attempt 上先验证 refs：

1. task 分支存在且有未合并 commit；
2. tip task 状态为 done/dropped；
3. tip `handoff.json` 可解析，tid/status/branch 与实际一致；
4. 并行路径的 `handoff.attempt` 等于当前 attempt；串行无 dispatch 时允许 `null` 或省略。

验证通过且当前 attempt 已有匹配 `worker_terminal` 后才输出 integrate，无需等待 report。worker 仍 running 时，即使 refs ready 也只输出 `await-worker-terminal` 并继续占槽；缺 handoff 或 attempt 契约不一致走 contract，但未 terminal 前不 redispatch。report 是加速线索，不是完成权威。

## 失败策略

| 类别 | 来源 | 自动策略 | 升级条件 |
|---|---|---|---|
| infra | provider/API/宿主错误的显式 failed | 模型阶梯降档；按现场 resume/restart；同模型连续失败可熔断 | 阶梯用尽或额度用尽 |
| resource | 上下文耗尽等显式 failed | 原现场 resume 或 restart | 自动重试额度用尽 |
| contract | refs/handoff 验证失败 | 同模型 resume 补契约 | 重犯或无现场 |
| task | 黑盒/review 等显式失败 | 按既有额度处理 | blocked 总是升级 |

仓库 fingerprint 长时间不变不是 failed，不进入 resource 自动重派。它只产生 `alert-silent`，详见静默监控权威设计。

## 并发与闩锁

- 同 tid 同时只有一个合法在飞 attempt；更高 attempt 必须等待旧 attempt 的 `worker_terminal`。
- progressing、未观察、silent、待 worker 终止、待 integrate、待 redispatch 都占槽。
- worker terminal gate 未解除时，ready 不 integrate，contract/failed 不 redispatch；blocked 可报告但不派替代 worker。
- 未 terminal 即出现更高 dispatch 时，旧、新 attempt 都保留并标记非法重叠，不静默结束旧 attempt。
- escalated 释放槽，并通过闩锁阻止自动补派同 tid；用户裁决后显式新 dispatch 解除闩锁。闩锁按当前 attempt 判断，迟到旧 attempt 事件不影响新 attempt。
- silent attempt 持续占槽；存在 silent hold 时不补任何新 dispatch。
- 冲突边 task 不并发；主干已终态 task 不再占槽或阻塞冲突对端。
- integrate 串行执行，主仓保持单写者。

## 5 分钟兜底

cron 每 5 分钟唤醒 coordinator：先按 dispatch 的 `worker_id` 查询宿主状态；running attempt 执行 observe，已进入 `completed|failed|stopped` 的 attempt 先记录匹配 `worker_terminal`，再 reconcile。通知丢失由 refs 派生完成状态，但 terminal gate 仍以账本证明为准；worker 无仓库变化由 observation 触发静默告警。告警后注销 cron 并等待用户，不自动取消或重派。

## 命令面

```bash
python3 scripts/repo_template/task.py observe TID --attempt N [--json]
python3 scripts/repo_template/task.py ps [--all] [--silent-minutes N]
python3 scripts/repo_template/task.py reconcile [--limit N] [--tids TIDS]
    [--model-ladder "opus>haiku"] [--silent-minutes N]
    [--max-auto-retries N] [--json]
python3 scripts/repo_template/task.py ledger record --event EVENT --tid TID
    [--attempt N] [--model M] [--worker-id ID] [--fingerprint SHA256]
python3 scripts/repo_template/task.py ledger tail [--tid TID] [-n N]
```

`reconcile` 只输出计划；start/integrate/observe 和 coordinator 的 ledger record 分别承担对应副作用。
