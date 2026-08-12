# 审阅结果决策

## 目录
docs_repo/reviews/review_20260805_133245

## 报告来源
- 已读：review_claude_haiku, review_claude_sonnet, review_grok_grok_4_5, review_kimi_kimi_code_k3_256k
- 缺失：review_kimi_gpt_5_6_luna, review_kimi_gemini_3_6_flash（selection 期待 6 份，实际产出 4 份）

## 统计
- 采纳：26 项
- 不采纳：8 项
- 待决定：0 项

## 采纳项

### A1. 重叠 attempt 的 invalid 标记永久残留
- 来源：review_claude_haiku, review_claude_sonnet
- 位置：attempts.py:121-139（overlapping_attempts）
- 优先级：HIGH
- 详细判断理由：invalid 集合只在 attempt_reserved 时累加，attempt_terminal 只 pop open_identities，从不清理 invalid。两个已 terminal 的重叠 attempt 仍留在 invalid，require_exact_terminal 永久拒绝、in_flight_attempts 永久占槽。真实残留缺陷。
- 修复说明：attempt_terminal 关闭 identity 后，若该 attempt 参与的重叠对中所有 open identity 均已 terminal，从 invalid 移除该 attempt 号；补测试「重叠后全 terminal 不再阻塞」。

### A2. compute_reconcile_plan 运算符优先级缺括号
- 来源：review_claude_sonnet
- 位置：monitoring.py:569-572
- 优先级：HIGH
- 详细判断理由：`report and report.get("status") == "blocked" or effective_tasks.get(...) == "blocked"` 当前行为正确（and 优先于 or），但缺括号是易触雷代码气味，后续维护加第三项即出错。
- 修复说明：补括号为 `(report and report.get("status") == "blocked") or (effective_tasks.get(tid, {}).get("status") == "blocked")`。

### A3. escalated→integrate 语义未文档化锁定
- 来源：review_claude_sonnet
- 位置：attempts.py:82-87（project_attempts 状态覆盖）；integration.py:120-133
- 优先级：HIGH
- 详细判断理由：已决策「escalate = 暂停自动处置，非终态覆盖；escalated+completed 可手动 integrate（reconcile 不自动输出）」。代码行为正确，但 project_attempts 中 integrated 事件覆盖 escalated state、escalated 事件字段保留的投影语义无文档无注释，reviewer 误判为状态机缺陷。
- 修复说明：在 attempts.py project_attempts 与 task-integrate SKILL 中明确「escalate 是暂停标记，integrated 到达后 state 归 integrated，escalated 事件保留可追溯；手动 integrate escalated+completed 视作用户已裁决」。

### A4. integrate-chain 分支部分删除后永久卡死
- 来源：review_kimi_kimi_code_k3_256k
- 位置：integration.py:428-439（_validate_tx_members）
- 优先级：HIGH
- 详细判断理由：`--continue` 恢复路径先跑 _validate_tx_members，对每个 member 校验分支存在（branches != [branch] 即 raise）和 handoff；若上一轮 _delete_chain_branches 删了部分分支后进程被杀，下次 --continue 永远过不了校验，transaction 无法清除。正是 transaction 机制要保护的场景反而不可恢复。
- 修复说明：awaiting_verification 恢复路径中，对 allow_integrated 且分支已不存在的 member 跳过分支存在性/handoff 校验（该 member 已终态完成），只校验 integrated 事件与 merge_sha；补测试「部分分支已删后 --continue 可完成」。

### A5. repository_fingerprint 全量读 untracked 大文件
- 来源：review_claude_sonnet, review_grok_grok_4_5
- 位置：monitoring.py:61-104（repository_fingerprint）
- 优先级：MEDIUM
- 详细判断理由：untracked 常规文件用 path.read_bytes() 全量入哈希，worktree 误落大文件时 observe 内存与耗时暴涨，可能拖死 5 分钟 cron。两路独立报告同一问题。
- 修复说明：对超过阈值（如 1 MB）的 untracked 文件，digest 只记 (size, mtime_ns, 前 N KB 内容)，不读全量；SKILL 注明静默检测对大文件精度降级。

### A6. Windows 账本锁空文件加锁可能失败
- 来源：review_claude_sonnet, review_grok_grok_4_5
- 位置：ledger.py:17-22（_ledger_lock_fh）
- 优先级：MEDIUM
- 详细判断理由：a+ 创建空 lock 文件后对 1 字节加锁，Windows 上 msvcrt.locking 对长度不足文件可能失败；且 msvcrt.locking 进程级非线程安全。Linux/WSL 主路径不受影响。
- 修复说明：加锁前 `write(b"\0"); flush()` 保证至少 1 字节；注释说明「非线程安全，task.py 为 CLI 单进程独占」。

### A7. worktree task.md 损坏导致 ps/reconcile 全崩
- 来源：review_claude_sonnet
- 位置：store.py:271-275（discover_effective_tasks）
- 优先级：MEDIUM
- 详细判断理由：遍历登记 worktree 时任一 task.md 损坏（agent 写一半崩溃）则整个 ps/reconcile/view 全部失败，无法用脚本观察其余 task 去救援，单点故障扩大。
- 修复说明：ps/reconcile 路径对损坏 worktree 单独 try/except 标记并继续，其他 task 状态仍可观察。

### A8. reserve_attempt 领域层不校验 tid 存在与归档状态
- 来源：review_grok_grok_4_5
- 位置：attempts.py reserve_attempt；control.py cmd_attempt_reserve
- 优先级：MEDIUM
- 详细判断理由：tid 存在性只在 CLI 层校验，领域函数可直接 import 调用对不存在 task 写账本；且无 status 门禁，可对已归档 done/dropped task 再 reserve。
- 修复说明：把「tid 存在且非 ARCHIVED_STATUSES」下沉到 reserve_attempt 领域层；CLI 层保留但不再重复。

### A9. bind 允许省略 host_worker_id
- 来源：review_grok_grok_4_5
- 位置：attempts.py bind_attempt
- 优先级：MEDIUM
- 详细判断理由：脚本层 host 可选，ps/observe 出现空 host，cron「按 host 查宿主」失去句柄，agent 终态只能靠人工推断。
- 修复说明：agent executor 的 bind 强制非空 host_worker_id，省略即拒绝。

### A10. report_attempt 允许空 terminal_status 写 report=done
- 来源：review_kimi_kimi_code_k3_256k
- 位置：attempts.py:301
- 优先级：MEDIUM
- 详细判断理由：`terminal_status not in ("completed", "")` 放行空串；正常路径 terminal 事件必有合法 status，空串只来自手工编辑/畸形数据，此时被当作 completed 可能错误进入 integrated 流程。
- 修复说明：收窄为 `terminal_status != "completed"`；补畸形 terminal 事件测试锁定语义。

### A11. cmd_integrate/cmd_integrate_chain 丢失 tid 格式校验
- 来源：review_kimi_kimi_code_k3_256k
- 位置：integration.py:170, 370
- 优先级：MEDIUM
- 详细判断理由：旧版首行 TID_RE.fullmatch 校验被移除（cmd_cleanup_worktree 保留了），非法 tid 会流入 `git branch --list f"{tid}_*"` 当 glob 解析。
- 修复说明：两命令入口恢复 `ctx.TID_RE.fullmatch` 校验。

### A12. _record_index_phase 恢复路径 HEAD 推进时卡死
- 来源：review_kimi_kimi_code_k3_256k
- 位置：integration.py:445-455
- 优先级：MEDIUM
- 详细判断理由：_commit_index 因无变化跳过时记录 index_sha=merge_sha；最终 --continue 检查 _get_head()==index_sha 严格相等，若期间其他操作推进了主干 HEAD 则链无法清理且无自愈路径。
- 修复说明：跳过时记录 `index_skipped: true`；恢复路径区分两种情形，后者改用 `merge-base --is-ancestor` 确认。

### A13. running + verdict(ready/contract) 跳过 silent 监控
- 来源：review_kimi_kimi_code_k3_256k
- 位置：monitoring.py:522-532
- 优先级：MEDIUM
- 详细判断理由：running attempt 若 verdict 为 ready/contract 直接 await-terminal + continue，后续 observation/silent 检查不执行；分支被外部推到终态后 worker 崩溃场景下无告警且永久占槽。
- 修复说明：await-terminal 分支内执行轻量 observation，超 silent 阈值时升级 alert-silent。

### A14. ps 可见性收窄掩盖脏状态
- 来源：review_kimi_kimi_code_k3_256k
- 位置：monitoring.py:339-340（compute_ps_rows）
- 优先级：MEDIUM
- 详细判断理由：新版 ps 只含 attempt_reserved 事件的 tid，frontmatter 被手工改 active/blocked 但未走 reserve 的脏状态完全不显示，状态不一致被掩盖。
- 修复说明：恢复 active/blocked tid 合并显示，未 reserve 的标注「无 reserve」一致性告警。

### A15. contract + resume 自动重试路径无测试覆盖
- 来源：review_kimi_kimi_code_k3_256k
- 位置：monitoring.py:451-459；test_dispatch_control.py
- 优先级：MEDIUM
- 详细判断理由：_retry_or_escalate_action 中 contract + mode=resume 的同模型 resume-dispatch 分支（reason 含「补交接单」）无任何测试，是可达且有实际后果的自动重试动作。
- 修复说明：补 terminal failed + class=contract + mode=resume 的 reconcile 测试，断言 dispatch + 同模型 + reason。

### A16. late-event 测试缺 escalate action identity 断言
- 来源：review_kimi_kimi_code_k3_256k
- 位置：test_dispatch_control.py:1135-1158, 859-876
- 优先级：MEDIUM
- 详细判断理由：late events 是 identity 混淆高风险路径，但 escalate action 的 execution_id/attempt 未断言；若 escalate 错引旧 attempt 身份测试不会发现。
- 修复说明：补 `action["attempt"] == 2`、`action["execution_id"] == _eid("t001", 2)` 断言。

### A17. 文档 redispatch 术语与实现不符
- 来源：review_kimi_kimi_code_k3_256k, review_grok_grok_4_5
- 位置：task-dispatch/SKILL.md:85-86；plan_dispatch_control_plane.md:35
- 优先级：MEDIUM
- 详细判断理由：文档把 redispatch mode=resume|restart 列为独立 action，实现只输出 action="dispatch" 带 mode 字段；coordinator 按 skill 表匹配 action 名永远命不中。Grok 另指出文档内部 redispatch 条件表述不完全统一。
- 修复说明：文档统一改为「dispatch 动作 + mode=resume|restart 字段」；redispatch 条件统一为代码语义（terminal failed/stopped 或 report failed）。

### A18. 清理搬运残留的未使用 import 与死代码
- 来源：review_grok_grok_4_5, review_kimi_kimi_code_k3_256k
- 位置：git_ops.py/worktrees.py/documents.py/lifecycle.py/store.py/scheduling.py/context.py 未使用 import；control.py:144 死分支；attempts.py escalate_attempt 不校验 reason；integration.py _commit_index 不检查 add 返回码；monitoring.py compute_ps_rows effective 死参数
- 优先级：LOW
- 详细判断理由：机械切分带入的未使用 import 增加噪声；_commit_index 的 git add 失败会被 diff --cached --quiet 静默吞掉，产生索引与账本不一致（唯一运行时风险）。
- 修复说明：一次清理未使用 import；删 control.py 死分支；escalate_attempt 加 reason 非空校验；_commit_index 检查 add 返回码；compute_ps_rows 移除 effective 死参数。

### A19. ps 表格 execution_id 全宽输出
- 来源：review_claude_haiku
- 位置：control.py:219-227（cmd_ps）
- 优先级：LOW
- 详细判断理由：32 位 hex 全量对齐列宽，多 task 时表格被拉得极宽；只读展示不便，无正确性影响。
- 修复说明：ps 截断 execution_id 前 8 位显示；ledger tail 保留全量。

### A20. 自动重试额度按 attempt 号推算偏移
- 来源：review_claude_haiku
- 位置：monitoring.py:441（parent_attempt - 1 >= max_auto_retries）
- 优先级：MEDIUM
- 详细判断理由：attempt 1 被 escalate（非 failed 重试通道）后 attempt 2 才首次失败，2-1=1 已计为一次重试，实际额度被占用。escalate 与 failed-retry 混用时自动重试次数比配置少一次。
- 修复说明：重试计数改用该 tid 在 exact identity 前的 terminal failed/stopped 或 report failed 事件数，而非 attempt 号差。

### A21. 静默告警连带冻结全部并发 worker 观察
- 来源：review_kimi_kimi_code_k3_256k
- 位置：task-dispatch/SKILL.md 静默告警第 3 步
- 优先级：MEDIUM
- 详细判断理由：任一 identity 静默告警即注销 cron、停止全部自动调度，其余健康 running identity 不再被 observe；实现层 silent_hold 只阻补位，cron 注销是 skill 层额外动作，文档未说明副作用。
- 修复说明：文档显式说明该副作用；或改为仅停补位、保留对其他 running identity 的 observe（选后者需同步实现）。

### A22. link_local_env glob 只覆盖一级嵌套
- 来源：review_claude_sonnet
- 位置：worktrees.py:22
- 优先级：MEDIUM
- 详细判断理由：`glob(".env") + glob("*/.env")` 只匹配根与一级子目录，apps/api/.env 不会被软链，monorepo 场景 worktree 缺 .env 导致 agent 跑测试失败。
- 修复说明：改用 rglob(".env") 并跳过 .scratch/、node_modules/ 等 gitignore 路径。

### A23. blocked 放行路径与 attempt 控制面脱节
- 来源：review_claude_haiku
- 位置：attempts.py:206-216（retryable 判定）；lifecycle.py cmd_resume
- 优先级：HIGH
- 详细判断理由：blocked task 的 attempt 在 reserve 检查中 retryable=False，唯一放行通道是 escalate 后由 escalate 态允许 reserve；但 cmd_resume（blocked→active）只改 front matter 不写 attempt 事件，协调器 resume 后直接 reserve 会报错。skill 与代码依赖协调器记住隐式顺序。
- 修复说明：在 task-dispatch/task-run SKILL 明确「blocked 放行 = escalate → 用户裁决 → 新 reserve」；reconcile 对 blocked 的 escalate 动作附提示，避免直接 resume 后 reserve 撞错。

### A24. terminal failed/stopped 无 report 禁止自动 redispatch
- 来源：review_grok_grok_4_5
- 位置：attempts.py reserve_attempt（retryable 条件）；monitoring.py compute_reconcile_plan 失败分支
- 优先级：HIGH
- 详细判断理由：当前实现把 `terminal_status in {failed, stopped}` 单独视为可 retry，不要求已有 report。reconcile 对裸 terminal failed（无 report）直接输出 dispatch，默认 fail_class="task"。一旦新 attempt 成为 current，旧 identity 的 report 被 `_require_exact_current` 拒绝，report 中的 class/reason 永久丢失。这是 terminal→report→retry 顺序的机械缺口。用户决策：选 A（收紧）。
- 修复说明：reconcile 对 terminal failed/stopped 且无 report → 输出 `await-report`，禁止 dispatch；coordinator 写完 report 才进入 retry/escalate；补测试「terminal failed 无 report 时 plan 不得 dispatch」。

### A25. escalate 输出即释放并发槽（文档对齐实现）
- 来源：review_grok_grok_4_5
- 位置：monitoring.py compute_reconcile_plan；测试 test_reconcile_effective_blocked_escalates_without_retry_budget
- 优先级：HIGH
- 详细判断理由：文档/skill 写「escalate 才释放槽」，实现是 reconcile 输出 escalate 建议时该 tid 不占槽（测试固化 used==0），同轮可对空槽补位新 dispatch。用户决策：选 A（文档对齐实现）。escalate 后该 tid 已无在飞 worker，等待用户裁决期间占用并发槽没有收益，反而阻塞其他可跑 task。
- 修复说明：task-dispatch/SKILL.md 与 plan_dispatch_control_plane.md 明确「reconcile 输出 escalate 即释放水位，等待用户期间不阻塞其他 task」；不修改实现与测试。

### A26. reserved 悬挂超时后 escalate
- 来源：review_claude_haiku
- 位置：monitoring.py reconcile reserved 分支；attempts.py terminal_attempt（拒绝未 bind 的 agent attempt）
- 优先级：HIGH
- 详细判断理由：agent attempt reserve 后若宿主启动失败/从未 bind，attempt 永久停在 reserved，reconcile 只输出 await-bind 且无超时回收，每轮占槽直到用户手工介入。用户决策：选 A（超时后 escalate）。与静默告警哲学一致（无动作就报告用户看），不引入合成账本事件。
- 修复说明：reconcile 对 reserved 超过阈值（默认 30 分钟，与 silent_minutes 一致）输出 escalate，报告用户处理；reserved 不无限 await-bind。

## 不采纳项

### R1. 旧账本无 execution_id 事件的迁移兼容
- 来源：review_kimi_kimi_code_k3_256k
- 位置：attempts.py:36-42（project_attempts）
- 优先级：MEDIUM
- 详细判断理由：旧账本 dispatch/integrated 事件无 execution_id 被静默丢弃。但本次重构已明确为破坏性升级（gitignored 运行态账本，无迁移层），用户此前明确「不需要兼容，要最干净的架构」。模板场景无存量部署。README 已声明账本可清空重启；不设计迁移脚本。

### R2. CLI --stall-minutes deprecated alias
- 来源：review_kimi_kimi_code_k3_256k
- 位置：cli.py:194,200-201
- 优先级：MEDIUM
- 详细判断理由：建议加 --stall-minutes alias 过渡。但用户明确「不需要兼容旧流程」，参数改名与 identity 强制均为有意的破坏性升级；模板无外部调用方需要过渡。保持 --silent-minutes 唯一命名。

### R3. 单 task integrate 已合入时 merge_sha 精确化
- 来源：review_grok_grok_4_5
- 位置：integration.py cmd_integrate 跳过 merge 分支
- 优先级：LOW
- 详细判断理由：已合入时 merge_sha 记录当前 HEAD 而非真实 merge commit。正常自动路径无感（跳过 merge 只出现在重入/手工合入），且精确解析首次引入 commit 成本高收益低。之前审阅已接受该幂等设计。保留现状。

### R4. cmd_view 错误信息包装简化
- 来源：review_claude_sonnet
- 位置：control.py:82-86
- 优先级：LOW
- 详细判断理由：startswith 判断冗余但无害，删除需要核对 compute_schedule 全部错误前缀，改动风险大于收益。保持现状。

### R5. scheduling DFS 递归改迭代
- 来源：review_claude_sonnet
- 位置：scheduling.py:20-46
- 优先级：LOW
- 详细判断理由：Python 栈深 1000，task 实际数量 < 100 依赖链短，不会爆栈；递归可读性更好。保持递归。

### R6. documents.py 行内注释剥离优化
- 来源：review_claude_sonnet
- 位置：documents.py:55-58
- 优先级：LOW
- 详细判断理由：值含 ` #` 会被截断，但约定所有含特殊字符的值都加引号（加引号不进该分支），实际触发概率极低。保持现状，模板约定已覆盖。

### R7. archived task 直接采信 main_statuses
- 来源：review_claude_haiku
- 位置：monitoring.py:355-357（compute_ps_rows）
- 优先级：LOW
- 详细判断理由：归档 task 的残余 attempt 事件不再被观察是合理展示语义；archived 但 attempt 未 terminal 的冲突场景属低风险，无实际破坏。保持现状。

### R8. 冗余 projection 性能优化
- 来源：review_kimi_kimi_code_k3_256k
- 位置：attempts.py:428-437, 456-468
- 优先级：LOW
- 详细判断理由：同 identity 锁内两次 _require_exact_current、in_flight_attempts 每 tid 全量重投影 O(N×E)，数据量小（task 数十级）无实际影响；优化会牺牲可读性。保持现状，数据量增大时再优化。
