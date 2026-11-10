# 647d7013..HEAD 合并 Diff 审阅（deepseek）

- 范围：`647d70133b63683bf0ae0ccc4a53a4db498c4178..d3dd4e0`，225 文件，+8222/-24597，未按 commit 拆分。
- 方法：全量 diff + 关键实现回读。按用户要求未执行测试，以下结论均来自静态审阅。
- 结论：方向正确，但 `finish → run-close` 主路径存在会被 `pre-commit` 直接打断的缺陷，证据 commit 允许面过宽，恢复路径有死循环；同步器还存在旧 AGENTS 崩溃与索引不迁移问题。文档层术语和落点有十余处漂移。

## P0 / Blocker

### 1. `finish` 的提交会被 pre-commit 改写，产生 `finish=OK` 但 review scope 已失效的提交

- 位置：`repo/.repo_template/scripts/repo_task/lifecycle.py:719-731, 800-824`；`repo/.repo_template/hooks/pre-commit:70-85`；`repo/.repo_template/scripts/md_format.py:28`
- 现象：
    - `finish` 先在**工作区**计算 `review_scope_fingerprint` 并校验 PASS，然后 `git add -A -- docs/tasks/...` 并内部执行 `git commit -F`。
    - 消费仓按 README 启用 `core.hooksPath` 后，该 `git commit` 会触发 pre-commit；hook 对本次 staged 的 `.md` 调 `md_kx` 格式化并重新 `git add`。
    - 旧流程把 task 目录移到 `docs/archive/`，该路径在 `md_format.py` 黑名单内；新流程 task 目录留在 `docs/tasks/`，`task.md`、`docs/specs/*.md`、交付 `.md` 都会被格式化。
    - 只要格式化改动了一个字节，入库内容就不再是 review 时看到的内容。`finish` 不重算指纹，仍打印 `finish=OK`；随后 `run-close` 用 `ref=tip` 重算得到 `review_scope=stale` 并拒绝，主路径当场断掉。
- 附加问题：若 commit-msg hook 在 pre-commit 格式化之后拒绝提交，`_rollback_finish` 只恢复 index 到 finish 前快照，不撤销 hook 对工作区 `.md` 的格式化。此时工作区与 index 不一致，`finish` 报“已全部回滚”，但直接重跑会因未暂存改动或 review stale 再次失败。
- 建议：`finish` 的前置检查对将提交的 `.md` 先跑 `md_format.py --check`（漂移则零写入拒绝，要求格式化后重新 review）；或提交前后各校验一次 scope，并在 hook 改写时整体回滚工作区。仅恢复 index 不足以宣称“整体回滚”。

### 2. 证据 commit 可修改 `progress.md` front matter，直接降级 review_level 绕过门禁

- 位置：`repo/.repo_template/scripts/repo_task/monitoring.py:54-65, 92-112, 465-563`；`repo/.repo_template/scripts/repo_task/review.py:381-405`
- 现象：`EVIDENCE_COMMIT_FILES` 由 `REVIEW_PROCESS_FILES` 去掉 `handoff.json` 得到，因此包含 `progress.md`。`execution_commits()` 只校验证据 commit 触碰的**路径**，不校验 front matter 字段。
- `verify_integrate_ready()` 从分支 tip 的 `progress.md` 读取 `review_level`，再按该级别选择报告。一次证据 commit 把 `review_level: full` 改成 `single` 并补一份 `review_general.md`，即可跳过原本的 code/test 双轴 review；`review_limit` 也可被顺便抬高。`diff_anchor/status/branch/tid` 因 handoff 交叉校验受保护，但 `review_level/review_limit` 没有。
- 建议：证据 commit 至少禁止改 `progress.md` front matter；或在 `verify_integrate_ready` 中比对实现 commit 与 tip 的 front matter，只允许正文变化。

### 3. `run-close` 在 review 门禁失败时尚未写 terminal，`recovery` 无法判到 `evidence_repair`，形成恢复死循环

- 位置：`repo/.repo_template/scripts/repo_task/control.py:303-310`；`repo/.repo_template/scripts/repo_task/recovery.py:108-119, 136-150`
- 现象：`run-close --result done` 在写 `attempt_terminal` **之前**调用 `require_report_done_ready`。门禁失败时进程退出，ledger 中 attempt 仍是 `running`。
- `recovery_state()` 只在 `record.state == "terminal" && terminal_status == "completed"` 时检查 review gate 并返回 `evidence_repair`（`recovery.py:108`）。run-close 这条现场因此落入 `committed_unreported`，提示“补 terminal/report”；但补 report 仍会触发同一门禁，形成循环。
- 另外 `_evidence_repair_action()`（`recovery.py:149`）统一提示“提交证据 commit 后按原 identity exact cleanup”，而 `cleanup-worktree` 要求 terminal completed + report done。terminal 或 report 缺失时照做必失败。
- 建议：recovery 在已提交实现 commit 且 review gate 失败时，无论 state 是 running 还是 terminal completed 都返回 `evidence_repair`；动作按缺失状态给出 `run-close --result done` 或补齐 terminal/report 的具体顺序。

## P1 / 高

### 4. `integrate-chain` 会静默排除非祖先前驱，前驱可能未归档、未写 integrated

- 位置：`repo/.repo_template/scripts/repo_task/integration.py:788-824`；`repo/.repo_template/scripts/repo_task/monitoring.py:68-89`
- 现象：`_collect_chain` 只收集“分支 tip 是链尾祖先”的成员。若前驱在后继 fork 之后被追加/改写 commit（正常 recovery 会因 `_tip_has_successor` 拒绝，但手工或异常现场可发生），前驱新 tip 不再被链尾包含，会被静默排除。
- 此时 `integrate-chain` 只验证并归档剩余成员；链尾历史中仍包含前驱的旧实现 commit，于是代码被合入，但前驱目录留在 `docs/tasks/`、ledger 没有 `integrated` 事件、归档缺失。
- 同时，相邻成员的校验从“后继实现 commit 的 first parent 精确等于前驱 tip”退化为 `first_parent_segment(current_tip, previous_tip)`，只要求前驱 tip 在 first-parent 链上。链头也不校验其 base 已包含在 `base_ref`。
- 建议：恢复“后继实现 commit 的 base_sha 精确等于前驱分支 tip”的检查；若链头非空的 `base_sha` 不在 `base_ref` 祖先中则拒绝，而不是静默缩小成员集合。

### 5. 旧格式 AGENTS.md 会让 `plan` 直接失败，与 `sync.md` 的 fallback 承诺不符

- 位置：`repo/.repo_template/scripts/template_sync/status_plan.py:136, 154`；`repo/.repo_template/scripts/template_sync/agents_merge.py:27-33, 52-58`；`repo/.repo_template/docs/sync.md:15`
- 现象：`agents_section_status()` 无保护地调用 `_agents_parts()`。模板源或消费仓 `AGENTS.md` 缺少 `## 目录与读写规则` / `## 开发原则` 时抛 `SyncError`。`cmd_plan` 两处调用都没有 try，命令直接 exit 1。
- `sync.md` 明确写“缺少这两个标题时，脚本对旧版模板继续使用旧的整文件裁定逻辑”。实际只有 `_apply_agents_sectioned()` 在**模板源**旧格式时回退；消费仓旧格式、或 `plan/status` 路径仍会抛错。升级旧消费仓时 `plan` 不可用，`apply` 也会在 `_replace_agents_force` 处失败回滚。
- 建议：`agents_section_status` 捕获 `SyncError` 返回 `None`；`apply` 对消费仓缺标题给出明确迁移指引或旧的整文件裁定，而不是中途抛错。

### 6. 软链 `AGENTS.md` 在 status/plan 被显示支持，`apply` 事务必然拒绝

- 位置：`repo/.repo_template/scripts/template_sync/apply.py:124-128`；`repo/.repo_template/scripts/repo_task/file_transaction.py:75-78`；`repo/.repo_template/scripts/template_sync/agents_merge.py:57, 78, 86`
- 现象：`shared_status/agents_section_status` 都专门 `resolve()` 软链，说明协议支持软链 AGENTS；但 `FileTransaction.begin()` 对 `paths` 中的 `AGENTS.md` 直接判定“事务资产根是软链”并拒绝。结果 status/plan 正常，apply 必失败。
- 建议：要么事务快照解引用软链目标，要么在 status/plan 阶段明确标记软链不支持并给出解链指引。

### 7. 同步迁移 task 文件后不重建派生 index

- 位置：`repo/.repo_template/scripts/template_sync/workflow_migration.py:440-487`；`repo/.repo_template/scripts/template_sync/apply.py:124-126`；`repo/.repo_template/scripts/repo_task/store.py:154-175`
- 现象：迁移把 `note.md` 改成 `progress.md`、把 `blocked` 改成 `active`，但不重建 `docs/tasks_index.json` / `docs/archive/tasks_index.json`，这两个文件也不在 apply 事务路径内。同步提交后索引的 `authority` 仍是旧文件名，状态可能与迁移结果不一致。
- 索引可重建，但作为随同步 commit 入库的派生文件，它现在处于“已知过期”状态；后续任意 `list --rebuild` 会把额外改动带出同步事务。
- 建议：发生实际迁移时调用 `rebuild_index()`，并把两个索引路径加入事务。

### 8. 工厂 CI 完全移除，交付门禁退回纯手工纪律

- 位置：`d43deba`（删除 `.github/workflows/template_factory.yml`）；根 `AGENTS.md` 测试命令
- 现象：原来覆盖 Python 3.9/3.14、Node 22、contract + factory 全量的 CI 被整体删除；没有替代 workflow。`AGENTS.md` 只剩本地命令，无法防止未跑全量的提交进入 main。
- 同一次 commit 还改写了 `docs_repo/archive/review_20261024.md` 的 M-07，把“缺少工厂 CI”的原始发现从归档审阅中删掉。若这是用户批准的收缩范围，应在 decision_log 记录；否则建议恢复最小 CI 或明确声明手跑是唯一门禁。
- 建议：至少保留 contract job；是否跑全量可另开手动 workflow。

## P2 / 中

### 9. `goal <tids...>` 不校验显式队列拓扑，可冻结出 `plan --serial` 立即拒绝的队列

- 位置：`repo/.repo_template/scripts/repo_task/goal.py:60-78`；`repo/.repo_template/scripts/repo_task/plan.py:479-487`
- 现象：`plan --serial` 会校验依赖顺序，违反时抛 `invalid_queue`；`goal` 的显式队列只检查存在性、重复和终态。`t002 depends_on t001` 时 `task.py goal t002 t001` 会成功冻结，随后 `task-run` 按 skill 第一步运行 `plan --serial --json t002 t001` 立即失败。
- 建议：显式队列也调用 `build_execution_plan(mode="serial", queue=...)` 做拓扑校验。第 76 行“已归档”也应改为“已是终态”，因为 done 可能仍在 `docs/tasks/`。

### 10. `finish` 不检查归档目标已被占用，失败推迟到 integrate

- 位置：`repo/.repo_template/scripts/repo_task/lifecycle.py:558-578`（`archive = status != "done"` 与条件调用 `_require_archive_target_free`）
- 现象：done 不再提前检查 `docs/archive/tasks/{tid}_{slug}`。若该目录有残留，`finish` 成功提交，直到 `integrate` 的 `_stage_archive_moves` 才报“同时存在”数据冲突。
- 建议：done 也做只读占用检查；冲突在零写入阶段暴露。

### 11. `finish` review 门禁失败不输出 AC 复验细节

- 位置：`repo/.repo_template/scripts/repo_task/lifecycle.py:725-731`
- 现象：`verify_integrate_ready` 已经输出 `ac_review` / `ac_review_detail`，`finish` 的拒绝信息只给 `overall/review_scope/missing_disposition/next_action`。因 `TICK-VOID` 失败时看不出是哪份报告、哪个 AC。
- 建议：补齐 `ac_review` 与 `ac_review_detail`。

### 12. `--message-file` 放在 task 目录内会被静默提交

- 位置：`repo/.repo_template/scripts/repo_task/lifecycle.py:703-711, 810`
- 现象：`_unstaged_outside` 豁免整个 task 目录，`git add -A -- task_rel` 会把 task 目录内的 message 文件一起提交。文案只说“放仓外或 `.scratch/`”，没有校验。
- 建议：拒绝 `message_path` 落在当前 task 目录内。

### 13. contract 测试跳过条件变更会阻断部分旧消费仓同步

- 位置：`repo/.repo_template/tests/test_contract_suite.py:84-88`
- 现象：旧逻辑只要有 `sync_state.json` 就跳过 `test_testing_md_default_commands_use_contract_marker`；新逻辑只有 `verification_policy == project_configured` 才跳过。已有消费仓若自定义了 `testing.md` 命令但 policy 仍是模板默认，同步 apply 跑 contract 时会失败并整体回滚。
- 建议：在 `sync.md` 增加迁移说明，或在 workflow migration 中提示先把 policy 改为 `project_configured`。

### 14. review prompt 示例把 AC 行放在代码块里，照抄会触发自己的门禁

- 位置：`repo/.repo_template/docs/review_prompts/code_prompt.txt:164-172`、`test_prompt.txt`、`general_prompt.txt` 同段；`share_prompt.txt:49-50`
- 现象：示例 `### AC 复验方式` 位于报告模板的 fenced code block 内；`share_prompt.txt` 又规定 AC 行不得写进代码块、引用块或表格，否则机器不读。reviewer 若把示例块原样落入报告，`check_ac_review` 会判缺行。
- 建议：示例移出代码块，或加粗注明“仅示格式，写入报告时必须是普通 `-` 列表行”。

## P3 / 低与文档

### 15. 术语仍分裂：执行 commit / 实现 commit / 证据 commit

- 位置：`repo/.repo_template/skills/task-run/SKILL.md:21,34`、`task-work/SKILL.md:3,12`、`task-create/SKILL.md:21`、`repo/.repo_template/docs/usage.md:12` vs `repo/AGENTS.md:34`、`architecture.md:7,64`、`recovery.md:24,32`、`task-integrate/SKILL.md:22`
- 现象：主路径文档一侧已改成“实现 commit + 证据 commit”，另一侧仍写“执行 commit”。建议全仓统一，避免 agent 把证据 commit 当成额外执行 commit 或反之。

### 16. `task-run` 的 `--base` 说明指代错误

- 位置：`repo/.repo_template/skills/task-run/SKILL.md:36`
- 原文：“同链后继的 `--base` 是刚完成的前一成员分支（即其 `base.branch`）”。括号里的“其”若指前一成员，则取到的是前一成员的祖先分支，不是它自身。应写“即当前成员的 `base.branch`”。
- 同类：`usage.md:22` 仍写“`start` 在主仓默认分支执行”，主路径已是 `run-begin`；`task-preflight/SKILL.md:22` 仍用旧称“读取 spec、task”，现行应为 `task.md`、`progress.md`。

### 17. skill 调用契约仍要求透传 attempt/execution_id，与主路径矛盾

- 位置：`repo/.repo_template/docs/usage.md:84`；`repo/.repo_template/skills/task-work/SKILL.md:16`
- 现象：主路径已明确只传 `tid` + `owner_id`，attempt/execution_id 由脚本解析；usage 仍写“原样传递 tid、attempt、execution_id、owner_id”，task-work 的组合契约则写了一个未定义的 `identity`。
- 建议：改成“主路径传 tid + owner_id；attempt/execution_id 仅在 recovery 显式指定 exact identity 时传”。

### 18. `decision_log` 断号与 D-10 缺失

- 位置：`docs_repo/decision_log.md:7, 63-69`
- 现象：导言称追加 D-01–D-14 对应 L40–L52（D-11 撤销），但表中 L48 后直接跳到 L50，缺少 L49；D-10（dogfood 消费仓，已在 `d43deba` 移除）没有像 D-11 一样注明撤销。第 7 行“现行行为以 usage/architecture 为准”也未补 `recovery.md`、`sync.md`。
- 建议：补 L49 或明确说明断号原因；补 D-10 状态；补齐现行权威文档列表。

### 19. 归档审阅被改写，历史追溯性受损

- 位置：`docs_repo/archive/review_20261024.md`（M-07 由“缺少工厂 CI、宿主测试和支持矩阵”改为“缺少宿主行为测试”，并删除工厂 CI 建议）、`docs_repo/reviews/2026-07/grok.md`（删除推荐阅读顺序）
- 现象：归档与历史审阅应保留原判断；修现状应在 decision_log 记“已解决/已撤销”，而不是回写原文。现在无法从归档分辨该建议是“当时没提”还是“后来删了”。
- 建议：恢复原文并追加勘误注；或至少记录改写原因与 commit。

### 20. `view_server` 的 `doc=note` 直接失效

- 位置：`repo/.repo_template/scripts/repo_task/view_server.py:36, 108, 139`；`view_static/board.html:128`
- 现象：`_DOC_NAMES` 删除了 `note` 别名，只保留 `progress`。旧书签、外部脚本或已缓存页面请求 `doc=note` 会 404。若是破坏性变更，应在 usage/architecture 明示；否则保留 `note` 别名指向 `progress.md` 成本更低。

### 21. `plan` 与 `status` 对 unmanaged opencode 命令口径不一致

- 位置：`repo/.repo_template/scripts/template_sync/status_plan.py:83 vs 188`
- 现象：`status` 把 `unmanaged` 列入漂移项，`plan` 的过滤条件排出 `unmanaged`。同样一份消费仓，status 报需处理，plan 报齐全一致。
- 建议：统一是否把“非本机制管理”的命令算作漂移。

### 22. `test_repo_sync.py` 仍有一处进程级 `subprocess.run` 替换

- 位置：`factory_tests/test_repo_sync.py:1845-1856`
- 现象：WP11 的其余测试都改成 `monkeypatch.setattr("<module>.subprocess", ...)`，此处仍直接改全局 `subprocess.run` 并在 finally 恢复。xdist 下是独立进程所以暂不致命，但与“替身隔离到被测模块”的目标不一致，遇到同进程并发或线程会污染。
- 建议：改为 `monkeypatch.setattr("template_sync.common.subprocess", SimpleNamespace(...))`。

### 23. 根 `AGENTS.md` 要求 `-n auto`，issue-handler 验证命令未同步

- 位置：`.agents/skills/issue-handler/SKILL.md` 第 7 节验证命令；根 `AGENTS.md` 命令块
- 现象：工厂根命令已改用 `-n auto` 并声明需要 pytest-xdist；issue-handler 仍是串行命令且写“10 分钟以上”。不是功能 bug，但会误导修复者。
- 建议：统一命令与耗时描述。

### 24. `plan_macro_overhaul` 历史方案局部未同步

- 位置：`docs_repo/plans/plan_macro_overhaul_2026_11.md:122, 222`
- 现象：C3 的 finish 签名仍为 `finish {tid} --message-file <path>`，漏 `--owner-id`；G1 仍写 `factory_tests/conftest.py 保留 xdist 守卫`，实际 conftest 已无守卫。该文首部已声明是历史方案，但这两处描述会让追溯者误判最终实现。
- 建议：在实施记录里补勘误，或直接修正签名与守卫描述。

### 25. `finish` 未加控制面锁

- 位置：`repo/.repo_template/scripts/repo_task/lifecycle.py:783`
- 现象：`cmd_finish` 无 `@control_locked`。它虽然只写 worktree 与本地 index，但会读主仓 ledger、审查状态；与 `run-close`、`attempt` 或同步并发时缺少统一串行化。实际影响小，但建议加锁或注释说明为何安全。

### 26. `test_repo_sync` 与 `test_design_regressions` 对 `"1 skipped"` 的断言脆弱

- 位置：`factory_tests/test_design_regressions.py:468`
- 现象：断言导出产物 contract 输出包含 `"1 skipped"`。pytest 版本、插件或环境导致额外 skip 时误报；输出文本不是稳定契约。
- 建议：解析 returncode 或使用 `-ra` 后的结构化摘要，至少断言“包含 skipped 但 contract 用例 0 failed”。

## 已核验无问题

- `run-begin` 的 start → reserve → claim 锁内组合与中途失败保留现场；`run-close` 的 terminal/report/cleanup 重入。
- 归档从 finish 移到 integrate 后的 merge commit 归档、`--continue` 重放、成员目录纯移动校验及额外路径拒绝。
- `PROGRESS_FILE` / `PROGRESS_TITLE` 收敛、`note.md` 与 `progress.md` 并存拒绝、旧布局迁移逻辑。
- `store` 允许 done 在活跃目录、按目录分组重建索引；`_task_record` 仍拒绝 dropped 在活跃目录、backlog 在归档目录。
- `plan.group_queue` / `compute_queue_topology` 的分链与 base 选择；`goal-check` 只停所在链的 skipped 归属。
- `template_sync` 拆包后 apply 事务、dirty 来源、expected-commit、manifest 脱敏、旧 state 迁移的主干行为等价。
- Python 3.9 注解 future 检查覆盖工具链脚本；`ledger.py` 已补延迟注解。
