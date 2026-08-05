# Worker 静默监控权威设计

本文是 agent executor 静默监控的当前权威设计。调度控制面的总体 reconcile 架构见 [`plan_dispatch_control_plane.md`](plan_dispatch_control_plane.md)；attempt identity 与生命周期见 [`plan_attempt_lifecycle_closure.md`](plan_attempt_lifecycle_closure.md)。

## 目标与边界

- coordinator 每 5 分钟观察 current running agent identity 的仓库可见状态。
- worker 不发送 heartbeat/progress，不调用 observe，也不写 attempt 控制面。
- fingerprint 连续 30 分钟无变化时只告警，不取消 Agent、不写 terminal/report failed、不重派。静默本身不转换为 failure。
- `task.py` 不推断宿主 Agent 是否存活。coordinator 根据 `host_worker_id` 查询宿主状态；该字段仅是宿主句柄，不是 execution provenance。

## 观察 identity

静默状态精确归属：

```text
(tid, attempt, execution_id, fingerprint)
```

`attempt` / `execution_id` 来自 `attempt reserve`，`execution_id` 是 provenance。`host_worker_id` 不参与 observation identity。旧 attempt 的 observation 或告警不得刷新、压制或终结 current attempt 的静默计时。

## 仓库状态指纹

`repository_fingerprint(worktree)` 计算稳定 SHA-256，输入依次为：

1. `HEAD` commit；
2. `git diff --binary --cached --no-ext-diff --full-index` 的原始 bytes；
3. `git diff --binary --no-ext-diff --full-index` 的原始 bytes；
4. `git ls-files --others --exclude-standard -z` 给出的非 ignored untracked 条目，按原始路径 bytes 排序后逐项加入路径、类型、mode 与内容。

规则：

- mtime 不参与。
- ignored/cache/log/build 文件由 Git ignore 规则排除。
- regular file 内容按 bytes 哈希。
- symlink 只哈希 link target，不跟随目标，不读取仓库外内容。
- 删除、重命名、chmod 由 binary diff 或 untracked mode 表达。
- 每段使用长度前缀分帧，避免不同字段拼接产生碰撞歧义。
- 控制面不保存文件内容，只保存 fingerprint 及诊断元数据：HEAD、worktree、dirty 摘要。
- 超过 1 MB 的 untracked regular file 不全量读入：只哈希 `size + mtime_ns + 前 8 KB 内容`，避免 observe 被大文件拖慢。静默检测对该类文件的精度降级——仅在大小/mtime/内容前缀变化时可感知，超大文件中部变化可能漏报。

## Observation 与 silent alert

仓库观察只通过：

```bash
python3 scripts/repo_template/task.py observe TID \
  --attempt N --execution-id ID [--json]
```

执行前校验：

1. 命令运行于主仓主干；
2. exact identity 是该 tid 的 current running attempt；
3. executor=agent 且已 bind；
4. 对应 worktree 存在并已登记；
5. 登记分支、worktree 当前分支与 tid ownership 一致。

成功输出 fingerprint、是否变化、最后变化时间、静默分钟数、`host_worker_id`、HEAD、worktree 和 dirty 摘要。首次观察或 fingerprint 变化时追加精确绑定 identity 的 `observation`；未变化时只输出，不追加。

静默告警只通过：

```bash
python3 scripts/repo_template/task.py attempt silent-alert TID \
  --attempt N --execution-id ID --fingerprint SHA256
```

该命令只记录 current identity 的这个 fingerprint 已报告，不改变 running 状态，不写 terminal/report/escalated。

`ledger record` 不能写 observation 或 silent alert；它只允许 `note`。

## ps 状态

`task.py ps --silent-minutes N` 默认 `N=30`：

- current running identity 无 observation：`未观察`；
- 最新 observation 未超过阈值：`progressing`；
- 最新 observation 超过阈值：`silent?`；
- `last_activity` 是该 identity 最新 fingerprint 变化时 observation 的时间；
- 表格展示 exact attempt/execution_id、executor 与 agent 的 `host_worker_id`。

reserved、terminal、reported、待 cleanup/integrate 等生命周期状态由 attempt 控制面优先表达。静默只适用于 current、已 bind 的 agent running identity；inline running 显示为 `running(inline)`，不调用 observer、不触发 silent hold。

## Reconcile 语义

`task.py reconcile --silent-minutes N` 默认 `N=30`。对每个 current identity 按以下边界处理：

1. `reserved`：等待 bind，继续占槽；不 observe。
2. agent 宿主仍 running：执行 exact observe；超过阈值时输出 `alert-silent`，否则继续占槽。
3. 宿主进入 `completed|failed|stopped`：coordinator 先执行 exact terminal，再根据 handoff/宿主结果执行 exact report。
4. terminal completed + refs/handoff ready：输出 exact cleanup/integrate；正常流程已在前一步写入 report done。
5. report blocked：输出 exact escalate，不派替代 worker。
6. report failed：按失败分类决定新 reserve 或 escalate；新 attempt 只能在旧 attempt terminal 后创建。

`alert-silent` 示例：

```text
ALERT-SILENT t272 attempt=1 execution_id=0123456789abcdef0123456789abcdef host_worker_id=task-abc — 连续 34 分钟无仓库可见变化，Agent 可能出现问题
```

约束：

- `alert-silent` 不生成 terminal、report failed、escalated 或 redispatch。
- attempt 继续占用并发槽。
- 本轮存在静默条件时不补新的 dispatch；即使同 fingerprint 已告警而不重复输出，也保持 silent hold。
- 同 identity、同 fingerprint 已记录 silent alert 时不重复告警。
- 新 observation fingerprint 重新建立变化时间；之后再次超过阈值可产生新告警。

## Coordinator 5 分钟节拍

每次 Agent 通知、cron 到点、integrate 完成或用户消息唤醒时：

1. 从 current agent identity 读取 `host_worker_id`，查询宿主后台任务状态；
2. 对宿主仍 running 的 identity 执行 `task.py observe TID --attempt N --execution-id ID --json`；
3. 对宿主 terminal 的 identity 依次执行 exact `attempt terminal`、`attempt report`；
4. 执行 `task.py reconcile ... --silent-minutes 30`；
5. 执行普通 dispatch/redispatch/integrate/escalate 计划；
6. 遇 `alert-silent` 时执行 exact `attempt silent-alert`，向用户报告并注销 cron，停止自动调度。

告警报告至少包含 tid、attempt、execution_id、静默时长、`host_worker_id` 与宿主状态、最后变化时间、HEAD、worktree 和 dirty 摘要。报告后保留 Agent、worktree、分支和 attempt 原状，等待用户决定；不得取消、重派、写失败或 reserve 同 tid 新 attempt。

## 与链式 inline 的关系

attempt 控制面服务所有 topology，但静默监控只适用于 executor=agent 的 running identity。`task-run` 的 executor=inline，不存在 `host_worker_id` 或后台 Agent 静默判断；它仍使用完整 `(tid, attempt, execution_id)` 完成 terminal、report、cleanup 与链式 aggregate integrate。
