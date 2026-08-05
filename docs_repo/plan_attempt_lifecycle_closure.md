# Attempt 生命周期闭环实施计划

目标：在当前 `main` 未提交改动基础上补齐三项控制面安全保证：worker terminal gate、handoff attempt 归属、attempt 级终态/闩锁。保持串行 `task-run` 兼容，不实现 lease/fencing 或可选的 `resolve-attempt`。

## 1. 增加 worker terminal 账本事件

- 在 `repo_task/context.py` 增加 ledger event `worker_terminal` 和状态枚举 `completed|failed|stopped`。
- 扩展 `task.py ledger record`：
  - `worker_terminal` 必须显式提供 `--tid`、`--attempt`、`--worker-id`、`--status`；
  - 校验该 attempt 存在 dispatch，且 worker-id 与 dispatch 一致；
  - 事件按 `(tid, attempt)` 归属，不允许推断最新 attempt；
  - 并行 task 在前一 attempt 没有 `worker_terminal` 时，新的 `dispatch` 与 `escalated` 均拒绝写入。
- coordinator 查询宿主后台任务进入终态后，先记录 `worker_terminal`，再落 report/failed 并 reconcile。

## 2. 在 reconcile 中建立 terminal gate

- 增加按 `(tid, attempt)` 读取最新 `worker_terminal` 的纯函数。
- 当前 attempt 未 terminal 时：
  - refs ready 不输出 integrate，显示/输出 `await-worker-terminal`；
  - contract 缺陷不 redispatch；
  - failed/report failed 不 redispatch；
  - attempt 继续占槽；
  - blocked/escalate 和 silent 告警可报告用户，但不得派替代 worker。
- 只有 terminal 后，refs ready 才 integrate，contract/failed 才进入 retry/escalate。
- `ps` 增加 `ready待worker终止`、`failed待worker终止` 等可辨识状态，并显示 terminal 状态。

## 3. cleanup/integrate 命令增加并行 attempt 门禁

- `cleanup-worktree TID` 和 `integrate TID` 增加可选 `--attempt N`。
- 若该 tid 在 ledger 中有 dispatch：
  - 必须显式给 `--attempt`；
  - 必须存在匹配的 `worker_terminal`；
  - attempt 必须是当前有效 attempt；
  - 否则拒绝 cleanup/integrate。
- 无 dispatch 账本的串行 `task-run`/链式任务保持原命令兼容，不强制 attempt。
- `integrated` 自动事件在并行路径写入 attempt；串行链式无 dispatch 的历史兼容路径可不带 attempt。
- 更新 `task-integrate` 与 `task-dispatch` skill：并行调用使用 `cleanup-worktree {tid} --attempt {N}`、`integrate {tid} --attempt {N}`；串行保持原命令。

## 4. handoff 增加 attempt 并按 attempt 验证

- `task-work` 输入增加可选 attempt；task-dispatch 派发时必须把 attempt 传给 worker，task-run 可省略。
- handoff schema 增加 `"attempt": N`；并行 worker 必填，串行无 ledger 时允许 `null`/省略以兼容链式执行。
- `verify_integrate_ready(tid, attempt=None)`：
  - reconcile 的并行路径传当前 attempt，并强制 `handoff.attempt == attempt`；
  - 串行/无 dispatch 的直接链式验证保持基础 tid/branch/status 校验。
- report SHA 若存在则继续作为线索；不在 handoff 写自身 commit SHA。

## 5. attempt 级状态机和闩锁

- `_in_flight_attempts()`：
  - `integrated`、`escalated`、`worker_terminal` 只影响其显式匹配的 attempt；
  - `worker_terminal` 本身不表示 task 完成，仅表示允许后续 integrate/retry；
  - 更高 dispatch 只能在旧 attempt 已 terminal 后合法取代；发现缺 terminal 的更高 dispatch 时输出/标记 contract 异常，不静默吞掉旧 attempt。
- `_escalate_latched_attempts()` 按 `(tid, attempt)` 返回锁定集合；旧 attempt 的迟到 escalated 不得锁住新 attempt。
- `verify_integrate_ready`、reconcile、ps 和 integrate 事件统一使用同一 current attempt 推导函数。

## 6. 文档同步

- 更新 `.agents/skills/task-dispatch/SKILL.md`：查询宿主状态后记录 worker_terminal；未 terminal 禁止 integrate/redispatch/cleanup。
- 更新 `.agents/skills/task-work/SKILL.md`：并行输入 attempt，handoff 写 attempt。
- 更新 `.agents/skills/task-integrate/SKILL.md`：并行命令带 attempt；串行链式说明例外。
- 更新 `.agents/skills/task-run/SKILL.md`、`AGENTS.md`、`docs_repo/plan_dispatch_control_plane.md`、`docs_repo/plan_worker_silence_monitoring.md`，统一 terminal gate 和 attempt provenance。
- 将本计划写入 `docs_repo/plan_attempt_lifecycle_closure.md`。

## 7. 回归测试

- ledger：worker_terminal 字段必填、worker-id 匹配、显式 attempt。
- reconcile：
  - refs ready 但 worker running → await，不 integrate；
  - failed/contract 但 worker running → await，不 redispatch；
  - terminal 后恢复 integrate/retry；
  - late escalated/integrated #1 不终结或锁住 #2；
  - 无 terminal 直接 dispatch #2 被识别为非法重叠。
- handoff：attempt 缺失/不匹配时 contract；匹配时 ready；串行无 dispatch 兼容。
- cleanup/integrate：并行未带 attempt、未 terminal、attempt 不匹配均拒绝；terminal 后成功；串行旧路径保持通过。
- integrated 事件包含并行 attempt。

## 8. 验证

- 定向运行 dispatch/integration/start-flow 测试。
- 运行 `pytest -q tests/repo_template`。
- 运行 CLI help、compileall、`git diff --check`。
- 搜索确认不存在“refs ready 可在 worker running 时直接 integrate”、tid 级 terminal/escalate 消费或 handoff 无 attempt 的并行权威表述。
- 不执行 git commit、merge 或 push。
