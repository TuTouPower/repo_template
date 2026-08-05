# 审阅报告 — Claude Haiku

## 本路模型标识

Claude Haiku（`default_haiku[1m]`）

## 审阅范围

2026-08-05 五个 commit（`90b2387..HEAD`），34 文件，+7810/−4421。核心为 attempt 生命周期统一（exact identity `(tid, attempt, execution_id)`）、task.py 模块化拆分（`scripts/repo_template/task.py` 3575 行 → `repo_task/` 11 模块）、dispatch/run/integrate 控制面重写。全量审读：`attempts.py`、`control.py`、`ledger.py`、`lifecycle.py`、`integration.py`、`monitoring.py`、`worktrees.py`、`scheduling.py`、`context.py`、`cli.py`、`store.py`（diff 部分）、`task.py`（façade 头部）、task-dispatch/task-work SKILL.md、`plan_attempt_lifecycle_closure.md`。未跑构建/测试。

## 高优先级

### H1. 重叠 attempt 的 invalid 标记永久残留，无自愈路径

- **位置**：`scripts/repo_template/repo_task/attempts.py:121-139`（`overlapping_attempts`）
- **现象**：`invalid` 集合只在 `attempt_reserved` 事件时累加，`attempt_terminal` 仅 `pop` 掉 `open_identities` 的 identity，从不从 `invalid` 移除。两个已 terminal 的重叠 attempt 仍会留在 `invalid` 集合。
- **影响**：一旦某 tid 出现过重叠（无论是否都已关闭）：`require_exact_terminal`（attempts.py:184-185）永久拒绝该 attempt 的 integrate/escalate；`in_flight_attempts`（456-469）把已 terminal 的重叠 record 永久算在飞占槽；`compute_reconcile_plan` 输出 `await-terminal` 且占用不释放，协调器无法推进，只能手工清账本。测试 `test_dispatch_without_prior_terminal_is_illegal_overlap` 只覆盖"活跃重叠"，未覆盖"重叠后全 terminal"的残留行为。
- **建议**：若非有意为之，`attempt_terminal` 时若某 identity 关掉后 `open_identities` 为空且该 attempt 曾只发生过一次重叠，应从 invalid 移除；或文档明示"重叠是非法态冻结，须手工清账本"，并在 reconcile 输出中给出明确的恢复指引。
- **置信度**：中（可能为防御性设计，但 plan 文档未声明该恢复边界）
- **优先级**：高

### H2. `reserved` 状态无超时回收，bind 缺失导致永久 `await-bind`

- **位置**：`scripts/repo_template/repo_task/monitoring.py:512-521`（reconcile reserved 分支）、`attempts.py:264-265`（terminal 拒绝未 bind 的 agent attempt）
- **现象**：agent attempt reserve 后若宿主启动失败/Agent 从未 bind，attempt 停在 `reserved`。`terminal_attempt` 对 `reserved` 拒绝（"尚未 bind，不能 terminal"），reconcile 只输出 `await-bind` 且无超时/回收逻辑，每轮占槽。
- **影响**：调度器对该 tid 永久挂起一个槽位，直至用户手工介入。`bind` 虽允许任意 `host_worker_id`，但协调器无法机械判定"何时该放弃"，reconcile 无该动作输出。
- **建议**：为 `reserved` 增加超时（如 `--bind-timeout`）后自动 `bind`（可空 id）→ `terminal stopped` → `report failed class=infra` → 走 retry/escalate 通道；或在 plan 文档中明确"reserved 悬挂须人工 escalate"，并让 reconcile 在超时后输出 escalate 而非 await-bind。
- **置信度**：中
- **优先级**：高

### H3. blocked 放行路径与 attempt 控制面脱节，resume 不解除 reserve 拒绝

- **位置**：`scripts/repo_template/repo_task/attempts.py:206-216`（retryable 判定）、`lifecycle.py:377-383`（cmd_resume）
- **现象**：blocked task（terminal completed + report blocked）的 attempt 在 reserve 检查中 `retryable=False`（非 escalated、非 failed/stopped、report 非 failed），新 reserve 被拒；唯一放行通道是 `escalate` 后由 escalate 态允许 reserve。但 `cmd_resume`（blocked→active）只改 front matter，不写 attempt 事件；若协调器在用户 resume 后直接 reserve，将报"尚待 integrate 或 escalate"。
- **影响**：blocked 重新调度必须先走 `attempt escalate` 才可新 reserve，与用户直觉的"resume 即可续跑"不一致；skill 与代码依赖协调器记住这条隐式顺序，出错时无机械校验。
- **建议**：文档（skill 或 plan）明确"blocked 放行 = escalate → 用户裁决 → 新 reserve"；或让 reconcile 对 blocked 输出的 escalate 动作附上"用户放行后须 escalate 才能重新 reserve"的提示，避免协调器直接 resume 后 reserve 撞错。
- **置信度**：中
- **优先级**：高

## 中低优先级

### M1. 自动重试额度按 `attempt - 1` 计数，escalate 轮次算入重试

- **位置**：`scripts/repo_template/repo_task/monitoring.py:441`（`parent_attempt - 1 >= max_auto_retries`）
- **现象**：重试次数用 attempt 号减 1 推算。若 attempt 1 被 escalate（非 failed 重试通道）后 attempt 2 才首次失败，`2-1=1` 已计为一次重试，实际重试额度被占用。
- **影响**：escalate 与 failed-retry 混用时，自动重试次数比配置少一次，提前 escalate；非致命但偏离配置语义。
- **建议**：重试计数用 `terminal failed/stopped` 或 `report failed` 事件在 exact identity 前的出现次数，而非 attempt 号差。
- **置信度**：低
- **优先级**：中

### M2. `ps` 表格 execution_id 全宽输出，可读性差

- **位置**：`scripts/repo_template/repo_task/control.py:219-227`
- **现象**：`execution_id` 为 32 位 hex，`cmd_ps` 用全量字符串对齐列宽，多 task 时表格被拉得极宽。
- **影响**：只读展示不便，无正确性影响。
- **建议**：ps 截断 execution_id 前 8 位（`tail` 保留全量）。
- **置信度**：高
- **优先级**：低

### M3. `_retry_or_escalate_action` contract fail 且 mode=restart 直接 escalate，无重试

- **位置**：`scripts/repo_template/repo_task/monitoring.py:451-457`
- **现象**：`fail_class == "contract"` 且 `mode == "restart"` 时直接 escalate（"无现场可续"）。contract 类失败（如 handoff 缺字段、base_sha 漂移）在 restart 模式下不给重试。
- **影响**：一次性 contract 配置错误（如模型/参数传递问题）会跳过自动重试直接上抛用户，调度吞吐降低；保守但可接受。
- **建议**：确认该语义是否应为"contract 且 restart 也允许同参数重试一次"；若是有意，文档注明。
- **置信度**：低
- **优先级**：低

### M4. `compute_ps_rows` 对 archived task 直接采信 main_statuses，跳过 attempt 校验

- **位置**：`scripts/repo_template/repo_task/monitoring.py:355-357`
- **现象**：`main_statuses[tid] in ARCHIVED_STATUSES` 时直接输出 state=main_statuses，不经过 verifier。
- **影响**：归档 task 的残余 attempt 事件不再被观察，合理；但若 archived 状态与 attempt 生命周期冲突（如 archived 但 attempt 未 terminal）时无告警。属低风险展示语义。
- **置信度**：低
- **优先级**：低

## 改进建议

1. `overlapping_attempts`（H1）与 `reserved` 超时（H2）建议补测试：分别覆盖"重叠全 terminal 后 invalid 应清除或给出恢复指引""reserved 悬挂超时自动回收"。
2. `cmd_integrate` 的 `record` 在 merge 前捕获，`append_integrated` 依赖 `record["state"] != "integrated"`；若 `_commit_index()` 成功而 `append_integrated` 失败，二次 integrate（不带 --continue）走 is-ancestor 分支自愈，已确认幂等，可保留但建议加注释。
3. skill 文档（task-dispatch/task-run）建议与 H2/H3 的恢复语义同步，避免协调器在文档未覆盖的悬挂态下机械等待。
4. `attempts.py:29` project 中 `"reserved": event` 直接引用原事件对象，跨事件复用时可读性尚可；如后续有事件字段扩展，注意 `model` 可空字段的兼容。

## 不确定项

1. `documents.py`（348 行）与 `store.py` 的 `discover_effective_tasks` / `rebuild_index` 仅 diff 抽查，未逐行核验 front matter 校验与索引重建的原子性。
2. 未运行任何测试（审阅要求不跑）；`tests/repo_template/test_dispatch_integration.py`（+750 行）与 `test_task_modularization.py` 未逐条核对断言与 H1/H2 疑点是否已有覆盖。
3. `integration.py:368-371` `_collect_chain` 按"被其他成员祖先数"排序：当链成员含分支点（非纯线性）或存在同 tid 多分支残留时行为未验证，实际依赖 `_resolve_integrate_branch` 前置拒绝多分支。
4. 并发 start 撞车场景下 `rollback_start` 的"未登记分支但 worktree 存在"分支路径（worktrees.py:141）依赖 `worktree prune` 的时序，未实测。
5. ledger 手工损坏（JSON 断行）时 `_read_unlocked` 跳过并告警，但 `ledger_next_attempt` 基于存活事件计算，损坏行可能导致 attempt 号回退重用，未确认是否有意容错。
