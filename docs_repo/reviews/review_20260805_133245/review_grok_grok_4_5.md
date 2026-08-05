# 审阅报告：Grok 4.5

## 本路模型标识

Grok 4.5

## 审阅范围

- 范围：`git diff 90b2387~1..HEAD`（5 commit / 34 files）
- 提交：
  - `90b2387` feat(task): close attempt lifecycle and modularize task tool
  - `f7437a2` feat(task): unify attempt lifecycle with execution_id identity
  - `3fdd454` fix(task): address review findings across attempt lifecycle
  - `e1ea9d9` refactor(task): remove dead 'resource' fail class
  - `d927df6` refactor(task): remove model ladder and circuit breaker
- 覆盖：`scripts/repo_template/task.py` façade、`repo_task/*` 全模块、四份 skill、`AGENTS.md`/`README.md`、`docs_repo` 计划文档、相关 tests
- 方式：全量只读 diff/源码；未跑构建与测试
- 时限：15 分钟内完成

---

## 高优先级

### H1. `terminal=failed/stopped` 无 report 即可机械 redispatch，旧 identity 的 report 窗口被关闭

- **位置**：`scripts/repo_template/repo_task/attempts.py` `reserve_attempt`（retryable 条件）；`scripts/repo_template/repo_task/monitoring.py` `compute_reconcile_plan` 失败分支；对照 `docs_repo/plan_dispatch_control_plane.md` 行动表 `redispatch` 行与 cron 顺序
- **现象**：
  1. `reserve_attempt` 将 `terminal_status in {failed, stopped}` 单独视为可 retry，**不要求**已有 report。
  2. `reconcile` 对 terminal failed/stopped（无 report）直接走 `_retry_or_escalate_action`，默认 `fail_class="task"`，可输出 `dispatch` 新 attempt。
  3. 一旦新 attempt 成为 current，旧 identity 的 `report_attempt` 被 `_require_exact_current` 拒绝（测试亦覆盖该门禁）。
- **影响**：cron/多唤醒交错或 coordinator 在 terminal 后、report 前崩溃/被抢跑时：自动 redispatch 会**永久丢掉**应写在 report 的 `class`/`reason`；且与文档「先 terminal 再 report 再 reconcile」「redispatch 在 report failed 后」的纪律不完全可机械保证。控制面只靠 skill 顺序，脚本层可被计划抢先。
- **建议**：
  1. `reconcile`：terminal failed/stopped 且无 report → `await-report`（或 escalate），**禁止**自动 dispatch。
  2. 或 `reserve_attempt`：仅当 `report.status==failed` 或 `state==escalated` 才允许新 reserve；裸 terminal failed 只允许 escalate，不允许机械 retry。
  3. 补测试：terminal failed、无 report 时 plan 不得 dispatch。
- **置信度**：高
- **优先级**：高

### H2. 「待 report 占槽」在 reconcile 中无对应状态；completed 未 ready 直接 escalate 且 `used=0`

- **位置**：`monitoring.py` `compute_reconcile_plan`；skill `task-dispatch`「并行纪律」；`plan_dispatch_control_plane.md`「并发与闩锁」；测试 `test_reconcile_effective_blocked_escalates_without_retry_budget`（断言 `used==0`）
- **现象**：
  - 文档/skill 写：`reserved`/`running`/待 terminal/**待 report**/待 cleanup/待 integrate/待 retry 都占槽，**escalate 才释放**。
  - 实现：terminal 后若走 escalate 建议（blocked、completed 未 integrate-ready、额度用尽），**不增加 occupancy**；测试明确接受 `used==0`。
  - 无 `await-report`：terminal completed 且 refs 未 ready → 直接 `escalate` 而非等待 handoff/report。
- **影响**：同一 reconcile 回合可在「建议 escalate 旧 task」的同时对空槽补 `dispatch` 新 task；若 escalate 未立即执行，并发水位会短暂超过「未 escalate 前仍占槽」的语义。completed 但 handoff 稍晚可见时，易被误判为 contract escalate 而非短暂 await。
- **建议**：
  1. 明确权威语义：要么改 skill/文档为「reconcile 输出 escalate 即释放水位」，要么改实现使 terminal 未 escalate/未 integrate 前一律 `occupancy+=1`。
  2. 对 `terminal=completed` 且 `verdict=incomplete`（非 contract）保留 `await-handoff`/`await-report`，仅 contract/blocked/额度用尽才 escalate。
- **置信度**：中高（文档与测试/实现三方不一致已核实；生产是否踩坑取决于 coordinator 是否同回合执行 escalate）
- **优先级**：高（契约权威不一致，调度水位可被误解）

---

## 中低优先级

### M1. CLI 才校验 tid 存在；领域函数 `reserve_attempt` 可对任意 tid 写账本

- **位置**：`control.py` `cmd_attempt_reserve`；`attempts.py` `reserve_attempt`
- **现象**：`3fdd454` 在 CLI 层 `scan_tasks()` 拒绝孤立 tid；`reserve_attempt` 本身只校验 tid 正则与 executor，测试与库调用可向不存在 task 追加 attempt。
- **影响**：直接 import 调用或未来其他入口绕过 CLI 时，账本可堆积无对应 task 的 orphan identity；`ps`/`reconcile` 会展示幽灵 tid。
- **建议**：把「task 必须存在」下沉到 `reserve_attempt`（或共享 precheck）；CLI 只做展现。
- **置信度**：高
- **优先级**：中

### M2. 可对已归档 `done`/`dropped` task 再 reserve（仅当 current 非 integrated 或可 retry）

- **位置**：`cmd_attempt_reserve` 仅检查 tid ∈ `scan_tasks()`（含 archive）
- **现象**：无 status 门禁；对 archive 中 done 且未 integrated 的历史 identity 组合可能允许 escalate/retry 路径。
- **影响**：误操作可在无 start/worktree 流程下污染控制面；依赖 reconcile mode_probe 才暴露「无现场」。
- **建议**：reserve 默认拒绝 `status in ARCHIVED_STATUSES`，除非显式 `--force-retry` 或先 rewind。
- **置信度**：中
- **优先级**：中

### M3. Windows 账本锁：空 lock 文件上 `msvcrt.locking(..., 1)` 可能失败

- **位置**：`ledger.py` `_ledger_lock_fh` / `_with_lock`
- **现象**：`a+` 创建空 lock 文件后对 1 字节加锁；Windows 上文件长度不足时 `msvcrt.locking` 常失败。
- **影响**：Win 主仓无法原子 reserve/append；Linux/WSL 主路径不受影响。
- **建议**：加锁前 `write(b"\0"); flush` 保证至少 1 字节，或改用 portalocker/同一 JSONL 句柄锁。
- **置信度**：中高
- **优先级**：中（若目标平台含原生 Windows）

### M4. `bind` 允许省略 `host_worker_id`

- **位置**：`attempts.py` `bind_attempt`；skill 要求启动后立即 bind host
- **现象**：脚本层 host 可选；`ps`/`observe` 可出现空 host，cron「按 host 查宿主」失去句柄。
- **影响**：agent 终态只能靠人工/旁路推断，silent/failed 分诊变难。
- **建议**：agent bind 强制非空 `host_worker_id`；或 bind 后单独 `attach-host` 门禁。
- **置信度**：高
- **优先级**：中低

### M5. 模块拆分残留大量未使用 import

- **位置**：`git_ops.py` / `worktrees.py` / `documents.py` / `lifecycle.py` / `store.py` / `scheduling.py` / `context.py` 等（`argparse`/`json`/`shutil`/`Counter`/…）
- **现象**：由 monolith 机械切分带入；运行无害，增加噪声与 ruff/IDE 告警。
- **影响**：可维护性、静态检查噪音。
- **建议**：一次性清理未用 import；后续拆分用工具校验。
- **置信度**：高
- **优先级**：低

### M6. 单 task `integrate` 在「分支已是 HEAD 祖先」时用当前 HEAD 记 `merge_sha`

- **位置**：`integration.py` `cmd_integrate` 跳过 merge 分支
- **现象**：已合入时 `merge_sha = _get_head()`，不一定是历史上真正引入该分支的 merge commit。
- **影响**：账本 provenance 在手动合入/先行 merge 场景不精确；一般自动路径无感。
- **建议**：能解析则记录 `merge-base`/`--ancestry-path` 首次引入 commit；或字段改名 `recorded_at_head`。
- **置信度**：中
- **优先级**：低

### M7. `repository_fingerprint` 读取全部 untracked 文件内容

- **位置**：`monitoring.py` `repository_fingerprint`
- **现象**：对每个 untracked 常规文件 `read_bytes()` 入哈希。
- **影响**：worktree 误落大文件/数据集时 observe 内存与耗时暴涨，可能拖死 5 分钟 cron。
- **建议**：超阈值改 hash 流式读取 + 大小上限；超限记 `fingerprint-error` 并 escalate。
- **置信度**：中
- **优先级**：中低

### L1. 文档内部 redispatch 条件表述不完全统一

- **位置**：`plan_dispatch_control_plane.md` 行动表 vs「失败与重试」节；`plan_attempt_lifecycle_closure.md` reserve 规则
- **现象**：一处写「terminal 且 report failed」，一处写「terminal failed/stopped **或** report failed」；代码与测试（含 completed+report failed 可 retry）对齐后者。
- **影响**：读者/agent 按行动表实现会过严或过松。
- **建议**：全文统一为代码语义，并单独说明 completed+report failed 为合法业务失败解耦。
- **置信度**：高
- **优先级**：低

---

## 改进建议

1. **机械保证 terminal→report→retry 顺序**：把「无 report 不 redispatch」做成脚本门禁，而不是仅 skill 叙述（直接收紧 H1）。
2. **occupancy 单一权威定义**：在 `plan_dispatch_control_plane.md` 用真值表列出每种 `(state, report, verdict)` 是否占槽；`compute_reconcile_plan` 与 skill 同步（收紧 H2）。
3. **领域层门禁优先于 CLI**：tid 存在性、archived 拒绝、host 必填等放进 `attempts.py`，CLI 变薄。
4. **拆分卫生**：清理未用 import；考虑 `ruff`/`pyflakes` 进 doctor。
5. **已做得好的部分**（保持）：
   - exact identity `(tid, attempt, execution_id)` 投影与迟到事件隔离清晰；
   - ledger 锁内 allocate/batch integrated；
   - integrate-chain 事务 phase + MERGE_HEAD 恢复；
   - handoff base_sha/diff_anchor/first-parent 单执行 commit 门禁严格；
   - 删除 model ladder / `resource` fail class 后失败面更干净；
   - `3fdd454` 对 escalated 可手动 integrate、report=done 与 terminal 匹配、CLI 主仓限制等修复方向正确。

---

## 不确定项

1. **占用槽位语义是否故意在「输出 escalate」时释放**：测试 `used==0` 像是有意设计；若是，应改 skill/文档而非改代码。需产品确认。
2. **completed + report=failed 可 reserve**：测试与 `3fdd454` 明确保留；若业务上「宿主正常退出但任务失败」是主路径，则 H1 应只收紧「无 report」而非禁止该组合。
3. **多 coordinator / 双会话**是否在威胁模型内：若严格单 coordinator 单写者，H1 现实概率下降，但仍是脚本可达状态。
4. **未执行测试套件**：结论来自静态阅读；`3fdd454` 声称 376 passed，本路未复跑验证。
5. **`.old_task_for_review.py`** 仍含 resource/阶梯逻辑：若不在交付面可忽略；若会被误用为参考实现，建议标废弃或移出树。

---

## 总结

本 diff 完成 attempt 生命周期闭环 + `task.py` 模块化 + exact `execution_id` 身份模型，主路径设计完整，前轮 review 多项已修。残留风险集中在 **reconcile/reserve 对「terminal 后尚未 report」的机械缺口** 与 **占槽语义文档/实现不一致**；建议先定权威语义再改门禁与测试。
