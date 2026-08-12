# 代码审阅报告

## 本路模型标识

Kimi (kimi-code/k3-256k)

## 审阅范围

2026-08-05 全部 5 个 commit（`git diff 90b2387~1..HEAD`，34 个文件，约 +7810/-4421 行）：

- 90b2387 feat(task): close attempt lifecycle and modularize task tool
- f7437a2 feat(task): unify attempt lifecycle with execution_id identity
- 3fdd454 fix(task): address review findings across attempt lifecycle
- e1ea9d9 refactor(task): remove dead 'resource' fail class
- d927df6 refactor(task): remove model ladder and circuit breaker

覆盖全部模块：`repo_task/`（attempts/ledger/store/lifecycle/integration/git_ops/worktrees/monitoring/scheduling/control/cli/context/documents/__init__）、`task.py` façade、`tests/repo_template/` 全部测试、4 个 skill 文档、AGENTS.md、README.md、docs_repo/ 4 个 plan 文档。全量审阅，未抽样；源文件只读；未跑构建/测试。

总体评价：模块化拆分忠实度高（lifecycle/store/documents 与旧 task.py 逐字等价，仅 `ctx.` 前缀改名）；exact identity `(tid, attempt, execution_id)` 状态机内部自洽，写路径锁内重校无明显漏洞；model ladder / circuit breaker / resource fail class 移除彻底，无残留引用。主要风险集中在：integrate-chain 崩溃恢复路径、向后兼容缺口、以及文档术语与实现的漂移。

## 高优先级

### H1. integrate-chain 在「分支部分删除后」永久卡死，无恢复路径

- 位置：`scripts/repo_template/repo_task/integration.py:413`（`_delete_chain_branches`）与 `integration.py:343-360`（`awaiting_verification` 的 `--continue` 路径）的交互
- 现象：`--continue` 恢复路径先跑 `_validate_tx_members`（对每个 member 调 `_verify_exact_handoff` → `verify_integrate_ready`），再跑 `_delete_chain_branches`。`_delete_chain_branches` 对已删分支有容错（跳过），但 `_validate_tx_members` 没有：member 分支一旦已删，`_task_branch_names` 返回空，`verify_integrate_ready` 返回 `("incomplete", "无本地 task 分支")` 直接 raise。若上一轮 `_delete_chain_branches` 删了部分分支后进程被杀，下次 `--continue` 永远过不了校验，transaction 文件无法清除，链卡死。
- 影响：正是 transaction 机制要保护的「崩溃后可恢复」场景反而不可恢复，需人工编辑/删除 transaction 文件并手核账本，违背设计意图。
- 建议：`awaiting_verification` 恢复路径中，对「已 integrated 且分支已不存在」的 member 跳过 handoff/分支存在性校验（该 member 已终态完成）；或 `_validate_tx_members` 对 `allow_integrated=True` 的 member 在分支已删时只校验 integrated 事件与 merge_sha。
- 置信度：高；优先级：高

## 中低优先级

### M1. `report_attempt` 允许空 `terminal_status` 写 `report=done`

- 位置：`attempts.py:301`
- 现象：`if status == "done" and terminal_status not in ("completed", "")` 放行空串。正常路径 terminal 事件必有合法 status，空串只可能来自手工编辑账本/畸形数据/旧数据迁移——此时 status 未知的 terminal 被当作 completed，可能错误进入 integrated 流程。且无测试覆盖该分支。
- 影响：对畸形账本数据过度宽容；防御性分支语义是否有意为之无法验证。
- 建议：收窄为 `terminal_status != "completed"`；补一个畸形 terminal 事件的测试锁定预期语义。
- 置信度：中；优先级：中

### M2. 旧账本事件无 `execution_id` 被静默丢弃（向后兼容）

- 位置：`attempts.py:36-42`（`project_attempts`）
- 现象：缺 `execution_id` 的事件全部跳过。旧账本的 dispatch/integrated/escalated 事件升级后 attempt 记录整体消失，`reconcile`/`in_flight_attempts` 不再追踪。同样地，`cleanup-worktree`/`integrate` 新增 `handoff.json` exact identity 依赖（`integration.py:187-189` → `monitoring.py:269-273`），迁移前已 done 的旧 task 无法 cleanup/integrate，且无迁移逻辑。
- 影响：任何从旧版升级的部署丢失运行中 attempt 状态；存量旧 task 工作区无法清理。
- 建议：若为有意破坏性升级，在 README/changelog 明确迁移边界（清空账本/补写 handoff.json）；否则提供旧事件合成占位 execution_id 或一次性迁移脚本。
- 置信度：高；优先级：中

### M3. CLI 破坏性变更：`--stall-minutes` → `--silent-minutes`、identity 强制化

- 位置：`cli.py:194,200-201`（`ps`/`reconcile` 参数）、`cli.py:169-179`（`cleanup-worktree`/`integrate` 强制 `--attempt` + `--execution-id`）
- 现象：旧 `--stall-minutes`（默认 20）改名 `--silent-minutes`（默认 30）且 `reconcile` 移除 `--model-ladder`；旧参数调用直接 argparse 报错；默认阈值放宽改变调度行为且无告警。`cleanup-worktree TID` / `integrate TID` 旧调用方式直接失败。
- 影响：外部调用方（agent prompt、CI 脚本、文档）未同步则硬失败。identity 强制属设计意图，但参数改名和默认值变化属可缓和的破坏。
- 建议：加 `--stall-minutes` deprecated alias 过渡；默认值变化写入 commit message/changelog；确认上游调用方已同步。
- 置信度：高；优先级：中

### M4. `cmd_integrate` / `cmd_integrate_chain` 丢失 tid 格式校验

- 位置：`integration.py:170`、`integration.py:370`
- 现象：旧版首行 `TID_RE.fullmatch(args.tid)` 校验被移除，`cmd_cleanup_worktree` 却保留了。非法 tid（含通配符如 `t*`）直接流入 `git branch --list f"{tid}_*"` 当 glob 解析。
- 影响：可匹配非预期分支，错误信息不直观，行为不一致。
- 建议：两命令入口恢复 `ctx.TID_RE.fullmatch` 校验。
- 置信度：中；优先级：中

### M5. `_record_index_phase` 恢复路径在「index 无变化 + HEAD 已推进」时卡死

- 位置：`integration.py:445-455`
- 现象：`_commit_index` 因无变化跳过时以 `index_sha = merge_sha` 记录。最终 `--continue` 检查 `_get_head() == index_sha`；若期间其他 integrate/维护操作推进了主干 HEAD，严格相等检查失败，chain 分支无法清理且无自愈路径。
- 影响：低概率但后果为操作死锁。
- 建议：跳过时记录 `"index_skipped": True`；恢复路径区分两种情形，后者改用 `merge-base --is-ancestor` 等方式确认。
- 置信度：中；优先级：中

### M6. running + verdict(ready/contract) 跳过 silent 监控，worker 挂死永久占槽

- 位置：`monitoring.py:522-532`
- 现象：running attempt 若 `verdict in ("ready","contract")` 直接 `await-terminal` + `continue`，后续 observation/silent 检查不执行。分支被外部推到终态后 worker 崩溃的场景下，既无 silent alert 也不释放槽位。
- 影响：最坏情况并发槽被永久占用且无告警。
- 建议：await-terminal 分支内做轻量 observation 检查；至少在 verdict==contract 且超 silent 阈值时升级 alert。
- 置信度：中；优先级：中

### M7. `compute_ps_rows` 可见性收窄：tid 来源仅限 project_attempts

- 位置：`monitoring.py:339-340`
- 现象：旧版 `ledger_tids | active_tids` 合并 effective 中 active/blocked 的 tid；新版只含有 `attempt_reserved` 事件的 tid。frontmatter 被手工改 active/blocked 但未走 reserve 的脏状态，`task ps` 完全不显示。
- 影响：状态不一致被掩盖（旧版会显示并标注）。
- 建议：确认是否有意收窄；若有意，在 `cmd_ps`/reconcile 增加「active 但无 reserve」一致性告警。
- 置信度：中；优先级：中

### M8. 文档术语 `redispatch` 与实现不符

- 位置：`.agents/skills/task-dispatch/SKILL.md:85-86`、`docs_repo/plans/plan_dispatch_control_plane.md:35`
- 现象：文档把 `redispatch mode=resume|restart` 列为独立 action；实现（`monitoring.py:461-462`）只输出 `action: "dispatch"` 带 `mode` 字段，从不输出 `"redispatch"`。
- 影响：coordinator 按 skill 表匹配 action 名永远命不中，可能把重试误当新 dispatch 或漏处理。
- 建议：文档改为「`dispatch` 动作 + `mode=resume|restart` 字段」。
- 置信度：高；优先级：中

### M9. 测试缺口：`fail_class="contract"` + resume 的自动重试路径无覆盖

- 位置：`monitoring.py:451-459`；`tests/repo_template/test_dispatch_control.py`
- 现象：`_retry_or_escalate_action` 中 `contract` + `mode=="resume"` 的同模型 resume-dispatch 分支（reason 含「补交接单」）无任何测试。旧测试被替换为只验证 completed attempt 走 escalate，但 terminal_status=failed + class=contract 时该路径仍可达。
- 影响：该路径是有实际后果的自动重试动作，出 bug 不会被捕获。
- 建议：补 `terminal failed` + `class=contract` + `mode=resume` 的 reconcile 测试，断言 dispatch + 同模型 + reason。
- 置信度：高；优先级：中

### M10. late-event 隔离测试缺 escalate action 的 identity 断言

- 位置：`test_dispatch_control.py:1135-1158`（`test_late_attempt_one_events_do_not_end_or_latch_attempt_two`）；另见 `:859-876`
- 现象：断言 escalate 出现但未验证其 `execution_id`/`attempt`。同类测试（:629、:801）都显式断言 identity。late events 正是 identity 混淆高风险路径。
- 影响：若 escalate 错引 attempt 1 的 execution_id，测试不会发现。
- 建议：补 `action["attempt"] == 2`、`action["execution_id"] == _eid("t001", 2)` 断言；同步补齐 :859-876 的断言。
- 置信度：高；优先级：中

### L1. 静默告警连带冻结全部并发 worker 的观察（文档未提示副作用）

- 位置：`.agents/skills/task-dispatch/SKILL.md` 静默告警第 3 步
- 现象：任一 identity 静默告警即注销 cron、停止全部自动调度，其余健康 running identity 此后不再被 observe。
- 影响：并发场景单 task 静默连带冻结全部监控；实现层 `silent_hold` 只阻补位，cron 注销是 skill 层额外动作，文档未说明该副作用。
- 建议：文档显式说明，或改为仅停补位、保留对其他 running identity 的 observe。
- 置信度：中；优先级：中（文档层）

### L2. 冗余 projection / 性能

- 位置：`attempts.py:428-437`（`append_integrated_batch` 同 identity 锁内两次 `_require_exact_current`）；`attempts.py:456-468`（`in_flight_attempts` 每 tid 全量重投影，O(N×E)，reconcile 高频调用）；`monitoring.py:331,344`（`compute_ps_rows` 的 `effective` 参数与 `effective_status` 为死代码，调用方白扫一次磁盘）
- 影响：无功能错误；数据量增大时性能下降，死代码误导维护者。
- 建议：投影一次后按 tid 分组；移除 `effective` 死参数或恢复其在 blocked 判定的使用。
- 置信度：高；优先级：低

### L3. 死代码 / 搬运残留

- 位置：`lifecycle.py:3-12`、`scheduling.py:3-13`、`context.py:3-12`（各自 7-12 个未使用 stdlib import，机械复制自旧单文件）；`control.py:144`（`args.event` 二次校验不可达，argparse choices 已拦截）；`attempts.py:326-335`（`escalate_attempt` 不校验 reason 非空）；`integration.py:243`（`_commit_index` 不检查 `git add` 返回码）
- 影响：无运行时 bug（`_commit_index` 例外：权限/磁盘满边界下 add 失败会被 `diff --cached --quiet` 静默吞掉，产生索引与账本不一致）；主要是卫生与严格度不一致问题。
- 建议：清理未使用 import；删 `control.py` 死分支；`escalate_attempt` 加 reason 非空校验；`_commit_index` 检查 add 返回码。
- 置信度：高；优先级：低

### L4. 文档细节漂移

- `docs_repo/plans/plan_worker_silence_task_modularization.md:196` 写 `dispatched(未观察)` vs 权威 `plans/plan_worker_silence_monitoring.md:76` 写「未观察」 vs 实现 `monitoring.py:383` 为 `running(未观察)`（实施计划为历史文档，影响有限）。置信度：高；优先级：低
- `docs_repo/plans/plan_worker_silence_task_modularization.md:26-41` 目录树漏列实际存在的 `attempts.py`。置信度：高；优先级：低
- `.agents/skills/task-run/SKILL.md` 恢复指引默认走 cleanup，但 cleanup 只接受 terminal completed；terminal=failed/stopped 的中断场景指引不可达，未覆盖该分支。置信度：中；优先级：低
- `.agents/skills/task-dispatch/SKILL.md:113` infra 行「不降档」相对已删除的 model ladder 无操作含义。置信度：中；优先级：低
- `test_dispatch_control.py:894-903` 把同 identity 重复 reserved 事件固化为期望的 overlap；建议加注释标明这是保守降级设计。置信度：中；优先级：低

## 改进建议

1. 优先修 H1（integrate-chain 恢复路径校验墙），这是 transaction 机制核心价值的缺口。
2. 统一向后兼容策略：M2/M3 一批破坏性变更（账本 execution_id、handoff.json 依赖、CLI 改名、identity 强制）建议集中写一份迁移说明，能加 alias/迁移脚本的加上。
3. 补两类测试：contract+resume 自动重试路径（M9）、escalate action 的 identity 断言（M10）——前者丢的是整段可达逻辑，后者守的是本次重构的核心不变量。
4. 统一 reconcile action 术语：实现已用 `dispatch+mode`，把文档的 `redispatch` 改掉（M8）。
5. 清一批搬运残留：未使用 import、`control.py` 死分支、`compute_ps_rows` 死参数（L2/L3），让「未使用 import」类检查在包内重新有效。

## 不确定项

- M1 空 `terminal_status` 放行、M2 旧账本静默丢弃、M7 ps 可见性收窄、M6 running+verdict 跳过 silent 检查：均可能是有意设计（破坏性升级/保守 await），需作者确认意图；若有意，建议以注释/文档锁定语义而非依赖口头约定。
- `integrate-chain` 不要求调用方提供 identity、从 ledger current record 推导（`integration.py:388-391`）：与 `integrate` 的 exact-identity 强制不对称，若链中成员并发 reserve 了新 attempt，合并所用 identity 是否符合预期未验证。
- `task.py:171-176` façade `__getattr__` 用 `dir(_context)` + `isupper()` 导出常量：当前 exports 均为 context 自有常量，未来 context.py 新增大写 import 会意外泄漏，风险低但值得留意。
- `monitoring.py:91-92` `repository_fingerprint` 全量读 untracked 文件内容：旧版同样如此，非本次回归，未列为问题。
- 时限内未执行任何测试/构建，测试缺口类结论（M9/M10）基于静态比对实现分支与测试断言，未实际运行验证。
