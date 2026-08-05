---
name: task-dispatch
description: none
disable-model-invocation: true
---

# task-dispatch

以扇出 branch topology 并行调度 task。worker 在各自 worktree 执行，coordinator 是主仓唯一写者，自身不执行 task。全部拓扑共用 attempt 控制面；本 skill 只负责 `executor=agent` 的 attempt。角色边界见 `AGENTS.md`「执行角色与合并时机」，静默算法见 `docs_repo/plan_worker_silence_monitoring.md`。

前置：候选 task 的调度图已由 `task-schedule` 写入并提交。

## 会话级前置授权

启动前一次性向用户说明并取得授权，之后不再逐 task 询问：

```text
准备调度：{tid 列表}（并发上限 N，默认 3）
worker 模型阶梯：{首选}>{回退}（默认继承当前会话模型；显式 infra 失败自动降档）
观察/reconcile 节拍：每 5 分钟 cron 兜底，调度结束或静默告警时注销
每个 task：start → reserve agent attempt → 派发带 exact identity 的 Agent → bind host_worker_id；宿主终态后 terminal → report；done 后 exact cleanup-worktree → exact integrate → 重建 index → 合并后验证 → 删除该分支
{依赖 task 列表} 会在其前置合并后由 reconcile 自动解锁补位。
静默只告警，不取消、不重派。
不 push，不动远程。
```

用户指定模型后，`attempt reserve --executor agent --model <指定>` 与 Agent 调用使用同一模型；prompt 含 `model_override_authorized: 用户指定，session 授权`。继承会话模型则省略显式模型与该说明。

## Identity 与状态

exact identity 固定为 `(tid, attempt, execution_id)`：

- `attempt` 是同一 tid 的递增、非 bool 正整数轮次；`execution_id` 是本次执行 provenance，两者均由原子 `attempt reserve` 返回，禁止自行生成或推断“最新”。
- `executor=agent` 的 reserve 初态是 `reserved`；Agent 启动并取得宿主任务 ID 后，coordinator 用 exact identity 执行 `attempt bind`，状态转为 `running`。
- `host_worker_id` 仅是 Agent 宿主句柄，用于查询后台状态；它不是 execution identity，也不写入 worker handoff。
- 当前 attempt 未 terminal 时，`attempt reserve` 拒绝创建更高 attempt；不得通过 restart、resume 或迟到通知绕过。
- `terminal completed|failed|stopped` 描述 executor/宿主终态；`report done|blocked|failed` 描述业务结果，两者必须按同一 exact identity 分别记录。

## 唯一动作来源

每次被唤醒——worker 通知、5 分钟 cron、integrate 完成、用户消息——严格执行：

1. 用 `task.py ps` 与 attempt 控制面读取当前 exact identity、executor、状态和 `host_worker_id`。
2. 根据 `host_worker_id` 查询宿主后台任务状态。
3. 对宿主仍为 running 的 identity 执行：

   ```bash
   python3 scripts/repo_template/task.py observe {tid} \
     --attempt {N} --execution-id {EXECUTION_ID} --json
   ```

4. 对宿主已进入 `completed|failed|stopped` 的 identity，先记录 exact terminal：

   ```bash
   python3 scripts/repo_template/task.py attempt terminal {tid} \
     --attempt {N} --execution-id {EXECUTION_ID} \
     --status completed|failed|stopped
   ```

5. 再根据 worker handoff 与宿主结果记录 exact report：

   ```bash
   python3 scripts/repo_template/task.py attempt report {tid} \
     --attempt {N} --execution-id {EXECUTION_ID} \
     --status done|blocked|failed [--sha {BRANCH_TIP}] \
     [--class infra|contract|task] [--reason {REASON}]
   ```

6. 执行：

   ```bash
   python3 scripts/repo_template/task.py reconcile --limit N [--tids 授权范围] \
     --model-ladder "{阶梯}" --silent-minutes 30
   ```

7. 按输出顺序执行计划；integrate 完成后同回合回到第 1 步。

worker 不发送 heartbeat/progress，不调用 observe，不写 attempt 控制面。worker 只在自身分支写 `handoff.json`；宿主状态与生命周期命令只由 coordinator 处理。`ledger record` 不记录生命周期，只允许 `note` / `breaker`；`ledger tail` 只读。

## 行动执行

| 动作 | 执行 |
|---|---|
| `dispatch {tid}` | `task.py start {tid}` → `task.py attempt reserve {tid} --executor agent [--model M]`，保存返回的 `attempt` / `execution_id` → Agent prompt 必须携带 `tid`、`attempt`、`execution_id` → Agent 启动取得宿主 ID 后立即 `task.py attempt bind {tid} --attempt N --execution-id ID --host-worker-id HOST_ID`。bind 成功前该 attempt 仍为 reserved。 |
| `redispatch ... mode=resume` | 旧 identity 已 terminal 且 report failed 后，不跑 start；`attempt reserve --executor agent` 创建新 identity，派 Agent 进入原 worktree续跑，再 bind 新 `host_worker_id`。 |
| `redispatch ... mode=restart` | 旧 identity 已 terminal 且 report failed，且现场可安全重建时才重新 start；随后 reserve 新 agent identity、派发、bind。残留分支/worktree 无法安全处置时升级用户。 |
| `integrate {tid}` | 只处理 terminal `completed` 且 refs/handoff 验证通过的 current exact identity：`cleanup-worktree {tid} --attempt N --execution-id ID` → `integrate {tid} --attempt N --execution-id ID`。report 是加速线索，非完成权威；reconcile 在 terminal + refs ready 时即输出 integrate。完成后立即再次 observe/reconcile。 |
| `escalate {tid}` | `task.py attempt escalate {tid} --attempt N --execution-id ID --reason ...`，按停止条件请求用户裁决。 |
| `alert-silent {tid}` | `task.py attempt silent-alert {tid} --attempt N --execution-id ID --fingerprint FP`，再按静默报告流程处理。 |

所有 lifecycle、observation、cleanup 与 integrate 都必须显式携带同一 `(tid, attempt, execution_id)`。迟到通知只能作用于其原 identity；不得绑定 current attempt。

## 空闲许可与 cron

- 只有 reconcile plan 为空且没有 silent hold，才允许结束回合。
- 启动调度时注册一个每 5 分钟 cron；授权范围全部终态后注销。
- cron 提示语必须包含：按 `host_worker_id` 查询宿主状态 → observe running exact identities → terminal → report → reconcile → 执行计划。
- integrate 完成后不等待 cron 或通知，回合内立即再跑一轮。

## 收汇报与分诊

worker 通知到达时，通知只作为查询线索；coordinator 必须先用 identity 对应的 `host_worker_id` 确认宿主终态，再按顺序写 terminal、report 并 reconcile：

1. 宿主 `completed` 且 handoff status=`done`：terminal completed → report done；exact gate（terminal completed + handoff/refs 验证通过）全通过后 cleanup/integrate。report 是加速线索，非门禁前提。
2. 宿主 `completed` 且 handoff/status 表示 blocked：terminal completed → report blocked；随后 exact escalate，禁止派替代 worker。
3. 宿主 `failed|stopped`：写同名 terminal；检查 worktree 的 `git log -3`、`git status --short` 与 handoff，写 report failed 及分类。reconcile 再决定 resume、restart 或 escalate。
4. 宿主仍 running：不写 terminal/report，不 cleanup、不 integrate、不 reserve 新 attempt；继续占槽。

## 失败分类

| 类别 | 判定 | 自动策略 | 升级用户 |
|---|---|---|---|
| infra | API/provider/宿主错误的显式 failed | 按模型阶梯降档；有产出 resume、无产出 restart；同模型连续 2 次可记 breaker | 阶梯用尽或额度用尽 |
| contract | handoff/refs/identity 验证失败 | 同模型 resume 补交接契约 | 重犯或无现场 |
| task | 黑盒/review 等失败或 blocked | 既有 blocked 流程 | blocked 总是 |

仓库 fingerprint 长时间不变不产生 failed，不自动重派。静默 running identity 继续占槽。

## 静默告警

reconcile 输出 `alert-silent` 时：

1. 以 exact identity 和当前 fingerprint 执行 `attempt silent-alert`；
2. 向用户报告：tid、attempt、execution_id、静默时长、`host_worker_id` 与宿主状态、最后变化时间、HEAD、worktree、dirty 摘要；
3. 注销 cron，停止本轮和后续自动调度；
4. 保留 Agent、worktree、分支和 attempt 原状。

禁止取消 Agent、写 terminal/report failed、重派、reserve 新 attempt 或用空槽补新 task。等待用户决定继续观察、人工检查、显式停止或其他处置。同 identity、同 fingerprint 已记录 silent alert 时不重复报告；新 fingerprint 重新计时。

## 并行纪律

- 同一 tid 只有一个 current attempt；未 terminal 时 reserve 必然失败。
- `reserved`、`running`、silent、待 terminal、待 report、待 cleanup、待 integrate、待 retry 都占槽；escalate 才释放槽。
- conflicts_with 对端不同时启动。
- silent hold 时不补 dispatch。
- merge 冲突待裁决期间持续占槽。
- worker 之间不通信、不互读 worktree。
- integrate 串行；主仓只由 coordinator 写。
- `attempt` / `reconcile` / `ps` / `observe` / `ledger` / `cleanup-worktree` / `integrate` 是主仓限定命令。

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

授权范围全部终态后注销 cron，汇报授权范围、各 tid 的 exact identity 与业务结果、已合并 tid 与 merge commit、blocked/未跑项、主干 HEAD、遗留 worktree/分支。事实来源为 `task.py ps --all`、`task.py ledger tail`、refs 与 `handoff.json`。
