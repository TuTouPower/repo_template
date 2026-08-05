---
name: task-dispatch
description: none
disable-model-invocation: true
---

# task-dispatch

并行调度 task：worker 在各自 worktree 执行，coordinator 以 `task.py reconcile` 水位驱动全局。本 skill 是 coordinator 角色：主仓唯一写者，自身不执行 task。角色边界见 `AGENTS.md`「执行角色与合并时机」，静默算法见 `docs_repo/plan_worker_silence_monitoring.md`。

前置：候选 task 的调度图已由 `task-schedule` 写入并提交。

## 会话级前置授权

启动前一次性向用户说明并取得授权，之后不再逐 task 询问：

```text
准备调度：{tid 列表}（并发上限 N，默认 3）
worker 模型阶梯：{首选}>{回退}（默认继承当前会话模型；显式 infra 失败自动降档）
观察/reconcile 节拍：每 5 分钟 cron 兜底，调度结束或静默告警时注销
每个 task 完成并验证后立即：cleanup-worktree {tid} --attempt {N} → integrate {tid} --attempt {N} → 重建 index → 合并后验证 → 删除该分支；串行 task-run 无 dispatch 时省略 `--attempt`。
{依赖 task 列表} 会在其前置合并后由 reconcile 自动解锁补位。
不 push，不动远程。
```

用户指定模型后，派发 worker 时 Agent 调用带 `model: <指定>`，prompt 含 `model_override_authorized: 用户指定，session 授权`；继承会话模型则两者省略。

## 唯一动作来源

每次被唤醒——worker 完成/错误通知、5 分钟 cron、integrate 完成、用户消息——严格执行：

1. `task.py ps` / ledger 读取当前在飞 attempt 及 dispatch 保存的 `worker_id`。
2. 根据 `worker_id` 查询宿主后台任务状态。
3. 对宿主仍为 running 的 attempt 执行：

   ```bash
   python3 scripts/repo_template/task.py observe {tid} --attempt {N} --json
   ```

4. 对宿主已进入 `completed|failed|stopped` 的 attempt，先记录终态证明，再记录 report/failed：

   ```bash
   python3 scripts/repo_template/task.py ledger record \
     --event worker_terminal --tid {tid} --attempt {N} \
     --worker-id {WORKER_ID} --status completed|failed|stopped
   ```

   `worker_terminal` 必须与该 attempt 的 dispatch 及 `worker_id` 精确匹配；未记录前不得 integrate、redispatch 或 cleanup。
5. 执行：

   ```bash
   python3 scripts/repo_template/task.py reconcile --limit N [--tids 授权范围] \
     --model-ladder "{阶梯}" --silent-minutes 30
   ```

6. 按输出顺序执行计划；integrate 完成后同回合回到第 1 步。

worker 不发送 heartbeat/progress，不调用 observe，不写调度账本。宿主状态只由 coordinator 查询；`task.py` 不猜测 agent 是否存活。

## 行动执行

| 动作 | 执行 |
|---|---|
| `dispatch {tid}` | `task.py start {tid}` → Agent 派 worker 并取得宿主后台任务 ID → `ledger record --event dispatch --tid {tid} --attempt {N} --model {模型} --worker-id {ID}`；若同 tid 上一并行 attempt 未有 `worker_terminal`，账本拒绝新 dispatch。 |
| `redispatch ... mode=resume` | 不跑 start；直接派 worker 进入原 worktree 续跑 → 记录新 attempt、model、worker-id 与失败原因；旧 attempt 必须先有 `worker_terminal` |
| `redispatch ... mode=restart` | 确认无残留分支/worktree 阻碍后 start，再派 worker 并记录新 attempt；残留阻碍升级用户；旧 attempt 必须先有 `worker_terminal` |
| `integrate {tid}` | 并行调用 `task-integrate` 时使用 `cleanup-worktree {tid} --attempt {N}`、`integrate {tid} --attempt {N}`；串行无 dispatch 时省略参数；完成后立即再次 observe/reconcile |
| `escalate {tid}` | `ledger record --event escalated --tid {tid} --attempt {N} --reason ...`，按停止条件问用户 |
| `alert-silent {tid}` | 先记录 `ledger record --event silent_alerted --tid {tid} --attempt {N} --fingerprint {fp}`，再按静默报告流程处理 |

所有 attempt 事件必须显式携带 attempt，避免迟到通知绑定到最新 attempt。`report`、`failed`、`escalated`、`silent_alerted` 以及 `worker_terminal` 都按精确 `(tid, attempt)` 归属。

## 空闲许可与 cron

- 只有 reconcile plan 为空且没有 silent hold，才允许结束回合。
- 启动调度时注册一个每 5 分钟 cron；授权范围全部终态后注销。
- cron 提示语必须包含：按 worker-id 查询宿主状态 → observe running attempts → reconcile → 执行计划。
- integrate 完成后不等待 cron或通知，回合内立即再跑一轮。

## 收汇报与分诊

worker 通知到达时先确认通知对应的 attempt 和宿主终态，再按顺序落账并 reconcile：

1. 宿主任务已进入 `completed|failed|stopped` 时，先记录：
   `ledger record --event worker_terminal --tid {tid} --attempt {N} --worker-id {ID} --status {status}`。
2. 完成：再记录 `report --status done --sha {sha}`；refs + handoff 验证通过且 terminal gate 已解除才可 integrate。
3. blocked：再记录 `report --status blocked --reason ...`；reconcile 可输出 escalate，但不得派替代 worker。
4. 死亡/错误：检查宿主状态与 worktree 的 `git log -3`、`git status --short`，记录显式 failed 分类；terminal gate 已解除后 reconcile 才决定 redispatch 或 escalate。

worker 仍处于 running 时，ready/contract/failed 都不能 integrate、redispatch 或 cleanup；attempt 继续占槽。

## 失败分类

| 类别 | 判定 | 自动策略 | 升级用户 |
|---|---|---|---|
| infra | API/provider/宿主错误的显式 failed | 按模型阶梯降档；有产出 resume、无产出 restart；同模型连续 2 次可记 breaker | 阶梯用尽或额度用尽 |
| resource | 上下文耗尽等显式 failed | 原模型按现场 resume/restart | 同 tid 连续 2 次 |
| contract | handoff/refs 验证失败 | 同模型 resume 补交接契约 | 重犯或无现场 |
| task | 黑盒/review 等失败或 blocked | 既有 blocked 流程 | blocked 总是 |

仓库 fingerprint 长时间不变不是 resource failed，不自动重派。静默 attempt 继续占槽。

## 静默告警

reconcile 输出 `alert-silent` 时：

1. 记录当前 fingerprint 的 `silent_alerted`；
2. 向用户报告：tid、attempt、静默时长、宿主状态、最后变化时间、HEAD、worktree、dirty 摘要；
3. 注销 cron，停止本轮和后续自动调度；
4. 保留 worker、worktree、分支和 attempt 原状。

禁止取消 worker、记录 failed、重派、启动同 tid 新 attempt或用空槽补新 task。等待用户决定继续观察、人工检查、显式判失败或其他处置。同 fingerprint 已记录 `silent_alerted` 时不重复报告；新 fingerprint 重新计时。

## 并行纪律

- 同一时刻一个 tid 只有一个合法在飞 attempt；更高 attempt 只有在旧 attempt 已写入 `worker_terminal` 后才可派发。
- 发现未 terminal 即出现更高 attempt 时，保留旧、新 attempt 占槽并输出非法重叠/等待终止，不静默吞掉旧 attempt。
- conflicts_with 对端不同时启动。
- progressing、未观察、silent、待合并、待 worker 终止和 redispatch 都占槽；escalate 才释放槽。
- terminal gate 未解除时，ready 不 integrate，contract/failed 不 redispatch；blocked 可报告但不派替代 worker。
- silent hold 时不补 dispatch。
- merge 冲突待裁决期间持续占槽。
- worker 之间不通信、不互读 worktree。
- integrate 串行；主仓只由 coordinator 写。
- `reconcile` / `ps` / `observe` / `ledger` 是主仓限定命令。

## 停止条件

以下情况停止自动调度并询问用户，其余在跑 worker 不强制中断：

- `alert-silent`；
- merge 冲突无法确定语义；
- 合并后验证失败；
- task blocked；
- 失败策略达到升级条件；
- 范围扩大；
- 需要密钥、环境或产品决策；
- 主仓出现无法安全隔离的无关脏改动。

## 完成

授权范围全部终态后注销 cron，汇报授权范围、已合并 tid 与 merge commit、blocked/未跑项、主干 HEAD、遗留 worktree/分支。事实来源为 `task.py ps --all` 与 `task.py ledger tail`。
