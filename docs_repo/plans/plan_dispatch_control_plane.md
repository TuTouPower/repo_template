# 调度控制面：统一 attempt + 水位触发 reconcile

> **过时（2026-08-12 标注）**：本文描述的 coordinator / reconcile / 扇出 dispatch / 5 分钟 cron 机制已于 [`decision_log.md`](../decision_log.md) L35 退役。并发只保留用户手动多会话 `task-run`。当前执行架构权威见 `../../docs/blueprint/architecture_repo_template.md`；`task.py view --serve` 是现行只读看板入口。

全部执行拓扑共用统一 attempt 控制面。`task-run` 使用链式 branch topology 与 inline executor；`task-dispatch` 使用扇出 branch topology 与 agent executor。本文重点描述 coordinator 在扇出调度中的 reconcile 行为；attempt 生命周期权威定义见 [`plan_attempt_lifecycle_closure.md`](plan_attempt_lifecycle_closure.md)，静默算法见 [`plan_worker_silence_monitoring.md`](plan_worker_silence_monitoring.md)。

## Reconcile 是并行动作来源

```text
reconcile():
    observed = exact attempts + git refs + worktree registrations + handoff.json + observations
    desired  = authorized scope ∩ runnable graph ∩ concurrency limit ∩ conflict exclusion
    plan     = diff(observed, desired)
```

唤醒来源包括 Agent 通知、5 分钟 cron、integrate 完成和用户消息。每次唤醒先按 `host_worker_id` 查询宿主状态，观察 current running identities，补齐 terminal/report，再运行 reconcile。coordinator 只有在 plan 为空且不存在 silent hold 时才可安全空闲。

reconcile 只读并输出行动计划；副作用由 `start`、`attempt`、`observe`、`cleanup-worktree`、`integrate` 和受限的 `ledger record` 分别承担。

## Identity 与 executor

执行 identity 固定为 `(tid, attempt, execution_id)`：

- `execution_id` 是执行 provenance，所有命令按它精确归属。
- `executor=inline|agent` 表示执行位置，不改变 identity 规则。
- `host_worker_id` 只在 executor=agent 时保存宿主句柄，供 coordinator 查询后台状态；它不是 provenance，不参与 handoff 与 integrate identity。
- 当前 attempt 未 terminal 时禁止 reserve 更高 attempt。

## 扇出调度动作

|action|coordinator 动作|
|------|------|
|`dispatch`|`task.py start TID` → `attempt reserve TID --executor agent [--model M]` → Agent prompt 携带 reserve 返回的 attempt/execution_id → Agent 启动取得宿主句柄后 `attempt bind`。失败重试也走本动作，带 `mode=resume|restart` 字段。|
|`observe`|对宿主仍 running 的 current identity 执行 `observe TID --attempt N --execution-id ID`。|
|`terminal`|宿主进入 `completed|failed|stopped` 后，以 exact identity 执行 `attempt terminal`。|
|`report`|terminal 后，根据 handoff 与宿主结果执行 `attempt report --status done|blocked|failed`。|
|`await-report`|`terminal failed/stopped` 且尚无 report 时输出：先写 report 再进入 dispatch/escalate，禁止 report 落账前自动重派（否则新 attempt 成为 current 后旧 identity 的 class/reason 永久丢失）。|
|`integrate`|terminal completed 且 refs/handoff ready 后，exact cleanup，再 exact 单 task integrate；正常调度仍先按同一 identity 写 report；完成后同回合再次 reconcile。|
|`escalate`|对已 terminal identity 执行 `attempt escalate` 并请求用户裁决。reconcile 输出 escalate 即释放该 tid 并发槽（等待用户期间不阻塞其他 task）；`reserved` 悬挂超过 silent 阈值同样输出 escalate。|
|`alert-silent`|对 current running identity 的 fingerprint 执行 `attempt silent-alert`，报告用户并停止自动调度；不取消、不重派。|

## 持久控制面

`docs/runtime/dispatch_ledger.jsonl` 是仅主仓存在、gitignored、append-only 的 attempt 控制面。路径名称是兼容名称，不表示它只用于并行 dispatch。

控制面记录以下事实：

|类别|关键字段|写入入口|
|------|------|------|
|reserve|tid, attempt, execution_id, executor, model?, state|`attempt reserve`|
|bind|exact identity, host_worker_id?, state=running|`attempt bind`|
|terminal|exact identity, status=`completed|failed|stopped`|`attempt terminal`|
|report|exact identity, status=`done|blocked|failed`, sha?, class?, reason?|`attempt report`|
|observation|exact identity, fingerprint, head, worktree, dirty|`observe`|
|silent alert|exact identity, fingerprint|`attempt silent-alert`|
|escalated|exact identity, reason|`attempt escalate`|
|integrated|exact identity, merge_sha|`integrate` / `integrate-chain`|
|note|tid/reason|`ledger record`|

`ledger record` 只允许 `note`，不能写生命周期事件；`ledger tail` 只读。账本追加使用文件锁，损坏行警告后跳过，不让单条截断写破坏整个控制面。

## Refs 与 handoff 是业务完成证据

worker 完成后在 task 分支 tip 提供终态 front matter 与完整 `handoff.json`。reconcile 对 current identity 验证：

1. task 分支存在且包含恰好一个未合入主干的执行 commit；
2. tip task 状态与 handoff status 一致；
3. handoff 的 tid/attempt/execution_id/branch 精确匹配控制面与 refs，attempt 为非 bool 正整数；
4. tests/blackbox/review 是非空字符串，pending/findings 是字符串数组；
5. handoff `base_sha` 同时等于 task `diff_anchor` 与 branch tip first parent 完整 SHA；链成员后继还等于紧邻 predecessor tip。

refs/handoff ready 不能替代 executor terminal。terminal completed + handoff/refs ready 才输出 cleanup/integrate。正常 coordinator 流程仍在 terminal 后先写 report；report 保存业务结果，但不替代 cleanup/integrate 的 exact terminal 与 handoff 门禁。running identity 即使分支已出现终态文件也继续占槽，不 cleanup、不 integrate、不 reserve 新 attempt。

worker 只写 handoff，不写控制面。report 是 coordinator 在 terminal 后写入的业务结论，不由 worker 直接落账。

## Cleanup 与 integrate

单 task 路径永远携带完整 identity：

```bash
python3 scripts/repo_template/task.py cleanup-worktree TID --attempt N --execution-id ID
python3 scripts/repo_template/task.py integrate TID --attempt N --execution-id ID [--continue]
```

cleanup 的门禁覆盖 exact current、completed terminal、overlap、branch tip、handoff 与 worktree clean ownership。integrate 重新验证同一 identity，只合一个 task，并写 exact integrated。

链式拓扑不调用单 task integrate；各成员 exact cleanup 后调用：

```bash
python3 scripts/repo_template/task.py integrate-chain TAIL_TID [--continue]
```

它建立 Git dir 下的 aggregate transaction snapshot，对全链成员的 identity、terminal、handoff/status/worktree/ancestry 做整体门禁，全通过才一次合并链尾。transaction 记录 `prepared/merged/indexed/awaiting_verification` phase、`merge_sha` 与 `index_sha`；成员 integrated 在一次 ledger 锁内整体预检并幂等批量追加。merge/index/integrated 后保留分支与 transaction，外部验证通过后以同一 `--continue` 最终删除。预检失败零 merge、零 integrated。

## 失败与重试

|类别|来源|自动策略|升级条件|
|------|------|------|------|
|infra|provider/API/宿主错误的 terminal/report failed|同模型重试一次（按现场 resume/restart）；不降档|额度用尽|
|contract|failed/stopped identity 的 refs/handoff/identity 验证失败|同模型 resume 补契约|completed identity、重犯或无安全现场|
|task|黑盒/review 等显式失败或 blocked|按既有额度处理|blocked 总是升级|

重试前旧 identity 必须 terminal，且 terminal 为 `failed/stopped` 或 exact report 明确为 `failed`。completed identity 必须先 integrate 或显式 escalate，不能被新 reserve 顶掉；已 integrated identity 不可重跑。`attempt reserve` 在锁内机械执行这些规则。迟到旧 identity 的通知只能补其原记录，不影响 current attempt。

仓库 fingerprint 长时间不变不是 failed，只产生 silent alert。

## 并发与闩锁

- `reserved`、`running`、silent、待 terminal、待 report、待 cleanup、待 integrate、待 retry 都占槽。
- 当前 attempt 未 terminal 时不能 reserve 新 attempt。
- escalated identity 进入人工处置并释放自动调度槽；旧 identity 的迟到事件不得锁住新 identity。
- silent identity 继续占槽；存在 silent hold 时不补任何新 dispatch。
- conflicts_with 对端不并发；主干已终态 task 不再占槽或阻塞冲突对端。
- integrate 串行执行，主仓保持单写者。

## 5 分钟兜底

cron 每 5 分钟唤醒 coordinator：

1. 从 current agent attempt 读取 `host_worker_id` 并查询宿主状态；
2. running identity 执行 exact observe；
3. terminal 宿主先执行 exact terminal，再执行 exact report；
4. 运行 reconcile 并执行计划；
5. 遇 silent alert 时记录 exact fingerprint，注销 cron并等待用户，不自动取消或重派。

通知丢失可以由 refs/handoff 补充业务证据，但不能绕过宿主 terminal 与 exact identity 门禁。

## 当前命令面

```bash
python3 scripts/repo_template/task.py attempt reserve TID --executor inline|agent [--model MODEL]
python3 scripts/repo_template/task.py attempt bind TID --attempt N --execution-id ID [--host-worker-id HOST_ID]
python3 scripts/repo_template/task.py attempt terminal TID --attempt N --execution-id ID --status completed|failed|stopped
python3 scripts/repo_template/task.py attempt report TID --attempt N --execution-id ID --status done|blocked|failed [--sha SHA] [--class CLASS] [--reason REASON]
python3 scripts/repo_template/task.py attempt escalate TID --attempt N --execution-id ID --reason REASON
python3 scripts/repo_template/task.py attempt silent-alert TID --attempt N --execution-id ID --fingerprint SHA256
python3 scripts/repo_template/task.py observe TID --attempt N --execution-id ID [--json]
python3 scripts/repo_template/task.py cleanup-worktree TID --attempt N --execution-id ID
python3 scripts/repo_template/task.py integrate TID --attempt N --execution-id ID [--continue]
python3 scripts/repo_template/task.py integrate-chain TAIL_TID [--continue]
python3 scripts/repo_template/task.py ps [--all] [--silent-minutes N]
python3 scripts/repo_template/task.py reconcile [--limit N] [--tids TIDS] [--silent-minutes N] [--max-auto-retries N] [--json]
python3 scripts/repo_template/task.py ledger record --event note [--tid TID] [--reason REASON]
python3 scripts/repo_template/task.py ledger tail [--tid TID] [-n N]
```
