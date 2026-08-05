# Worker 静默监控权威设计

本文是并行 worker 静默监控的唯一权威设计。调度控制面的总体 reconcile 架构见 [`plan_dispatch_control_plane.md`](plan_dispatch_control_plane.md)；实现唯一落点是 `scripts/repo_template/repo_task/monitoring.py`。

## 目标与边界

- coordinator 每 5 分钟观察仍在运行的 attempt 的仓库可见状态。
- worker 不发送 heartbeat 或 progress，也不写调度账本。
- fingerprint 连续 30 分钟无变化时只告警，不取消 worker、不标记失败、不重派。
- 显式 `failed --class resource` 仍使用既有重试策略；静默本身不转换为 resource failure。
- `task.py` 不推断宿主 agent 是否存活。宿主状态由 coordinator 根据 dispatch 事件中的 `worker_id` 查询。

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
- 账本不保存文件内容，只保存 fingerprint 及诊断元数据：HEAD、worktree、dirty 摘要。

## 账本事件

| event | 必需字段 | 语义 |
|---|---|---|
| `dispatch` | `tid`, `attempt`, `model?`, `worker_id?` | coordinator 已派发宿主 worker；`worker_id` 供后续查询宿主状态 |
| `observation` | `tid`, `attempt`, `fingerprint`, `head`, `worktree`, `dirty` | 该 attempt 首次观察，或 fingerprint 相比上条 observation 发生变化 |
| `silent_alerted` | `tid`, `attempt`, `fingerprint` | coordinator 已把该 fingerprint 的静默告警报告给用户 |

`observation` 只由 `task.py observe` 写入。相同 fingerprint 再次观察不追加事件，原 observation 的 `ts` 始终表示本轮 fingerprint 最后变化时间。

## observe 命令

```bash
python3 scripts/repo_template/task.py observe TID --attempt N [--json]
```

执行前校验：

1. 命令运行于主仓主干；
2. 指定 `(tid, attempt)` 存在 dispatch 且仍是当前在飞 attempt；
3. 对应 worktree 存在并已登记；
4. 登记分支、worktree 当前分支和 start 事件均归属该 tid。

成功输出 fingerprint、是否变化、最后变化时间、静默分钟数、`worker_id`、HEAD、worktree 和 dirty 摘要。首次或变化时追加 `observation`；未变化时只输出，不写账本。

## ps 状态

`task.py ps --silent-minutes N` 默认 `N=30`：

- 当前 attempt 无 observation：`dispatched(未观察)`；
- 最新 observation 未超过阈值：`progressing`；
- 最新 observation 超过阈值：`silent?`；
- `last_activity` 是最新 fingerprint 变化时 observation 的 `ts`；
- 表格展示 dispatch 保存的 `worker_id`。

refs ready、blocked、显式 failed、reported 等既有高优先级状态仍优先于静默显示。

## reconcile 语义

`task.py reconcile --silent-minutes N` 默认 `N=30`。对每个在飞 attempt 按以下顺序处理：

1. 当前 attempt 已有匹配 `worker_terminal` 且 refs/handoff ready → `integrate`；
2. worker 未 terminal → `await-worker-terminal`，无论 refs ready、contract 或 failed 都不 integrate、不 redispatch、不 cleanup；
3. terminal 后 handoff/refs contract 缺陷 → 既有 contract 重试或升级；
4. terminal 后 blocked → `escalate`；
5. terminal 后显式 `failed` / `report status=failed` → 既有失败分类策略；
6. observation 超过静默阈值 → `alert-silent`；
7. 其余 attempt 继续占槽。

`alert-silent` 示例：

```text
ALERT-SILENT t272 attempt=1 worker_id=task-abc — 连续 34 分钟无仓库可见变化，worker 可能出现问题
```

约束：

- `alert-silent` 不生成 failed/escalated/redispatch。
- attempt 继续占用并发槽。
- 本轮存在静默条件时不补新的 dispatch；即使同 fingerprint 已记 `silent_alerted` 而不重复输出告警，也保持 silent hold。
- 同 attempt、同 fingerprint 已有 `silent_alerted` 时不重复告警。
- 新 observation fingerprint 会重新建立变化时间；若之后再次超过阈值，可产生新告警。

## coordinator 5 分钟节拍

每次 worker 通知、cron 到点、integrate 完成或用户消息唤醒时：

1. 从 dispatch 事件读取各在飞 attempt 的 `worker_id`，查询宿主后台任务状态；
2. 对宿主仍为 running 的 attempt 执行 `task.py observe TID --attempt N --json`；
3. 执行 `task.py reconcile ... --silent-minutes 30`；
4. 执行普通 dispatch/redispatch/integrate/escalate 计划；
5. 遇 `alert-silent` 时记录同 fingerprint 的 `silent_alerted`，向用户报告并注销 cron，停止自动调度。

告警报告至少包含 tid、attempt、静默时长、宿主状态、最后变化时间、HEAD、worktree 和 dirty 摘要。报告后保留 worker 与 worktree 原状，等待用户决定；不得取消、重派或启动同 tid 新 attempt。
