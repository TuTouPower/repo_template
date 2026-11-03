# 审阅采纳决策（review_20261104）

- 审阅范围：`647d70133b63683bf0ae0ccc4a53a4db498c4178..d3dd4e0`（宏观改造全波次合并 diff）
- 报告来源：`deepseek.md`（26 项）、`gemini.md`（13 项）、`muse.md`（2 Blocker + 11 Major + Minor/Nit 列表）
- 裁决时间：`2026-11-04T07:46:52+08:00`
- 裁决方法：逐项回读当前 `main` 源码核实，并用 `git show 647d701:<path>` 区分本轮回归与改造前既有缺陷。本文只裁决，未改代码、未跑测试。
- 采纳口径（用户裁定）：确认存在的问题一律修复，不区分是否本轮引入。标注「既有」的项为改造前已存在、`template_sync` 拆包时原样搬入。
- 修订（`2026-11-04`，用户裁定）：撤销宏观方案 D2 的证据 commit，恢复「一 task 一 commit」，见 D01。M01、M02、M03、A01、A08、A24、R10 已按 D01 改写。

## 统计

去重后共 53 项审阅意见，另加 1 项用户裁定：

- 用户裁定：1 项（D01）
- 采纳（按审阅建议修）：26 项
- 修改后采纳（问题成立，修法或范围与建议不同）：5 项
- 拒绝：22 项

优先级：P0 = 主路径断裂或门禁可绕过；P1 = 恢复/同步路径失败或数据不一致；P2 = 诊断缺口、边界缺陷；P3 = 文档、死代码、测试补强。

## 用户裁定

### D01. 撤销证据 commit，恢复一 task 一 commit（P0）

#### 裁定理由

- 证据 commit 只处理「task 已 done 并提交之后 review 门禁失效」这一种情况。M01 修复后，这种情况基本不会发生：
    - finish 提交前已核验 review PASS、指纹、报告格式和处置表；
    - M01 保证入库内容与 review 内容一致；
    - 同步 apply 在存在 done 但未 integrate 的分支时拒绝，模板升级碰不到待合分支。
- 和 amend 相比没有实际优势：
    - 有后继分支时两者都不允许；
    - 没有后继时分支只在本地，report 尚未写入，amend 不影响任何人。
- 成本是实打实的：分支形状校验从「恰好 1 个 commit」变成沿 first-parent 逐个检查路径；M03（链连续性回归）和 M02 的一半都由它引出；recovery 多一个阶段，4 份文档、1 个测试文件专门描述它。
- D-04 里真正有价值的是 D1（归档移到 integrate）。D1 之后 amend 只改 `docs/tasks/` 里的文件，原来 archive「唯一窄例外」不再需要。保留 D1，撤销 D2。

#### 目标规则

1. 执行分支恰好 1 个实现 commit：由 `finish` 提交，非 merge commit，first parent = `diff_anchor` = `handoff.base_sha`。第二个 commit 无论内容一律拒绝。
2. `handoff.json` 新增 `review_scope` 字段，值为 finish 前置检查时算出的交付指纹（`review_scope_fingerprint(diff_anchor, task_dir)`）。`verify_integrate_ready` 要求分支 tip 重算的指纹等于该值。这一条同时做两件事：
    - 承担 M01 的提交后复核；
    - 机械约束 amend 不得改交付内容。改了交付内容必须 rewind 重做，不能 amend。
3. done 后 review 门禁失效，recovery 判 `review_repair`（原 `evidence_repair` 改名）。可修复条件：
    - 没有其它分支包含当前 tip；
    - 该 attempt 的 report 尚未写入 done。

   满足时：保留现场并报告用户；用户同意后，在原 worktree 对 tip 内容重新 review，只改 review 过程文件（`progress.md` 正文、`review_*.md`、渲染的 review prompt），`git commit --amend --no-edit` 进同一个实现 commit，worktree 干净后在主仓运行 `run-close {tid} --owner-id ... --result done`。不满足任一条件时停止并报告，不 amend。
4. 归档仍由 integrate 的 merge commit 完成（D1 不变），不恢复 archive 写权例外。
5. 不留兼容层：缺 `review_scope` 的 handoff 判 `contract`。消费仓同步本来就拒绝存在未集成 done 分支的现场，不会有存量 handoff 需要迁移；工厂内未集成的 done 分支须先 integrate 或 rewind。

#### 改动清单

代码（`repo/.repo_template/scripts/repo_task/`）：

|文件|改动|
|---|---|
|`monitoring.py`|删除 `EVIDENCE_COMMIT_FILES`、`first_parent_segment`、`execution_commits`；新增 `implementation_commit(ref, base_sha) -> str`，校验 `base..ref` 恰 1 个 commit、单 parent、parent 等于 base。`verify_integrate_ready` 改用它，形状文案改为「执行分支必须恰好一个实现 commit」，删除证据 commit 计数。`_HANDOFF_TYPES` 加 `review_scope: str`，`build_handoff` 写入；verify 比对 tip 指纹与 `review_scope`。`REVIEW_PROCESS_FILES` 保留（指纹排除仍要用）|
|`lifecycle.py`|finish 把前置检查的 scope 传给 `build_handoff`；提交后用 `ref="HEAD"` 重算并与 `review_scope` 比对（即 M01 第 2 步）；`:858` rewind 拒绝文案改指 recovery 的 review 修复|
|`recovery.py`|`:98` 改用 `implementation_commit`；`evidence_repair` 改名 `review_repair`，`_evidence_repair_action` 改名 `_review_repair_action`，文案按目标规则 3；判定条件按 A01；保留 `_tip_has_successor`；report 已 done 时返回停止诊断|
|`integration.py`|删除 `first_parent_segment` 导入；链相邻检查恢复为 `current_tip^1 == previous_tip`（M03）|
|`control.py`|`:174` suffix 改为「先按 recovery 的 review 修复处理后重跑 run-close」|
|`attempts.py`|`:279` 文案「追加证据 commit」改为「amend 补审进同一实现 commit」|

产物文档与 skill：

|位置|改动|
|---|---|
|`repo/AGENTS.md:34`|「review 失效时仅可追加……证据 commit」改为「每个 task 恰好一个实现 commit；review 失效的修复见 recovery」|
|`architecture.md:7`|删除「review 失效时可在其后追加……证据 commit」|
|`architecture.md:64`|整段改写为：一 task 一 commit；`handoff.review_scope` 绑定交付指纹；review 失效时 amend 只改过程文件，交付内容变化须 rewind；cleanup / integrate / integrate-chain / `ledger import` 校验恰 1 个 commit 且指纹一致|
|`architecture.md:79`|「前置实现 commit 与证据 commit」改为「前置实现 commit」|
|`architecture.md:89`|task-work 行删除「done 后 review 失效时追加证据 commit」|
|`usage.md:90`|「证据 commit 只改过程文件，不改变指纹」改为「`handoff.json` 的 `review_scope` 记录 finish 时的指纹，最终提交必须与之一致」|
|`recovery.md:32`|按目标规则 3 重写「review 门禁失败后的修复」|
|`recovery.md:44`|「实现 commit 与证据 commit 的形状」改为「恰好一个实现 commit」|
|`task-work/SKILL.md:36`、`task-run/SKILL.md:42`|「证据修复（重新 review 并追加……证据 commit）」改为「按 recovery 的 review 修复处理」|
|`task-integrate/SKILL.md:22`|「一个实现 commit 加仅含 review 过程文件的证据 commit」改为「恰好一个实现 commit 且交付指纹与 handoff 一致」|

测试：

|位置|改动|
|---|---|
|`factory_tests/test_evidence_commit.py`|删除，新建 `factory_tests/test_review_repair.py`|
|保留并改写为 amend 语义|stale → `review_repair` 且 cleanup 拒绝（原 `:83`）；amend 补审后 cleanup / run-close 通过（原 `:95`、`:293`）；amend 后 integrate 归档（原 `:129`）；merge commit 拒绝（原 `:245`）；amend 引入 TICK-VOID 被拦（原 `:257`）；AC 行与 task.md 不一致被拦（原 `:268`）；有后继分支时停止（原 `:347`）；reserve / rewind 文案指向 review 修复且不含「证据 commit」（原 `:378`）；ledger import 接受单 commit（原 `:357`）|
|删除（规则已删）|允许集合派生（原 `:72`）、多个 / 空证据 commit（原 `:110`、`:122`）、链前驱带证据 commit（原 `:143`，由 M03 的精确链检查测试替代）、证据 commit 越界六例（原 `:183`–`:233`）、recovery 接受证据 commit（原 `:330`）|
|新增|第二个 commit 无论内容都被 cleanup / integrate / `ledger import` / recovery 拒绝（参数化：只改 review 文件、改交付文件）；amend 改交付内容后 `review_scope` 不一致被拒；handoff 缺 `review_scope` 判 contract；report 已 done 时 recovery 停止|
|`factory_tests/test_dispatch_integration.py:358`|断言文案改为新的形状文案|
|`factory_tests/test_orchestration_docs.py:81-95`|改为：分发文本不含「证据 commit」；`amend` 只出现在 `recovery.md`「review 门禁失败后的修复」一节；保留 `handoff.json`、「后继分支」断言|
|手写 `handoff.json` 的测试夹具|实施时 `grep -rn "handoff.json" factory_tests repo/.repo_template/tests`，补 `review_scope`，或改为调用 finish 生成|

工厂笔记：

|位置|改动|
|---|---|
|`docs_repo/decision_log.md`|新增 L53「撤销 L43 的证据 commit 部分（D2），恢复一 task 一 commit，review 失效走 amend 并以 `handoff.review_scope` 约束」，出处本文 D01、用户裁定；L43 状态改为「已落地；证据 commit 部分被 L53 取代」|
|`docs_repo/plans/plan_macro_overhaul_2026_11.md`|D2 行首按 `docs_repo/README.md` 纪律加一行标注「已由 L53 撤销」，不改原文|

#### 验证

- 残留扫描为空：`grep -rn "证据 commit\|EVIDENCE_COMMIT\|execution_commits\|first_parent_segment\|evidence_repair" repo factory_tests`
- 子集：`pytest factory_tests/test_review_repair.py factory_tests/test_finish_atomic.py factory_tests/test_archive_at_integrate.py factory_tests/test_dispatch_integration.py factory_tests/test_orchestration_docs.py -q -n auto`
- 全量门禁（见文末）

## 修改后采纳

### M01. finish 内部提交被 pre-commit 改写，`finish=OK` 后 `run-close` 判 stale（P0）

- 来源：deepseek#1、gemini#2
- 位置：`repo_task/lifecycle.py` `_finish_prechecks` / `cmd_finish`；`hooks/pre-commit`；`md_format.py` `BLACKLIST_PREFIXES`
- 判断：成立。`finish` 在工作区算 review 指纹后调 `git commit`；启用 `core.hooksPath` 时 pre-commit 对 staged `.md` 跑 md_kx 并重新 `git add`。指纹覆盖 `task.md` 与交付 `.md`（只排除过程文件），任一字节被格式化，入库内容就偏离 review 内容；`finish` 不重算，照打 `finish=OK`，`run-close` 用 `ref=tip` 重算后拒绝。旧流程交付 `.md` 已有同样风险；新流程 task 目录留在 `docs/tasks/`（不在黑名单），把 `task.md` 也带进风险面。`usage.md` 已写「commit 前 `--check` 为绿」，但 finish 未机械执行。
- 与建议的差异：
    - 采纳「前置 `--check`」，再加一道提交后校验，防其它 hook 或工具改写。
    - 不采纳「hook 改写时整体回滚工作区」：前置检查通过后 pre-commit 不会再改文件，该分支不可达，不为它写回滚逻辑。模板只分发 pre-commit，没有 commit-msg hook，附加问题所述场景在默认配置下不出现。
- 修复：
    1. `_finish_prechecks` 新增零写入检查：对本次将提交的 `.md`（已暂存交付文件 + 当前 task 目录内 `.md`，扣黑名单）调用 `md_format.format_paths(..., check=True)`。有漂移时拒绝，提示先 `md_format.py --changed`，交付内容变了要重新 review。md_kx 缺失时，若 `core.hooksPath` 指向 `.repo_template/hooks` 则拒绝（反正提交会被 hook 阻断），否则跳过。
    2. `cmd_finish` 提交成功后用 `review_scope_fingerprint(diff_anchor, task_rel, ref="HEAD")` 与 `handoff.review_scope`（D01 新增，即前置指纹）比对；不一致时不打印 `finish=OK`，改报 `finish=FAIL：提交内容与 review 内容不一致`，并指向 `recovery`（与现有「HEAD 已前进不回滚」策略一致）。下游 `verify_integrate_ready` 做同一比对，漏网也会在 run-close 被拦。
    3. 测试：启用 hooksPath 且交付 `.md` 未格式化时零写入拒绝；注入改写 `.md` 的 hook 时 finish 报 FAIL，不报 OK。

### M02. `review_level` 可被降级，绕过双轴 review（P0）

- 来源：deepseek#2、muse B1
- 位置：`repo_task/monitoring.py` `EVIDENCE_COMMIT_FILES` / `execution_commits` / `verify_integrate_ready`；`review.py:381`
- 判断：成立，但根因比审阅说的宽。证据 commit 能改 `progress.md` front matter 属实；实现 commit 同样能降级：实施 agent 在 worktree 里直接把 `review_level: full` 改成 `single` 后 finish，没有任何机械检查（只有 `review-upgrade` 命令拒绝降级，文件本身可写）。指纹虽然绑定了 `review_level`，降级后补一份同指纹的 general 报告仍能 PASS。只封证据 commit 堵不住。
- 与建议的差异：
    - 证据 commit 由 D01 删除，「证据 commit 禁改 front matter」不再需要。
    - 改为在 tip 与 `diff_anchor` 之间做单调性比对，实现 commit 和 D01 的 amend 修复都经过这一道。
    - `review_limit` / `verify_limit` 不做比对：`limits` 命令按设计在 active worktree 内由用户授权提升，tip 值合法地可能更高；done 之后 `limits` 要求 active，amend 阶段也改不动它们。
- 修复：
    1. `verify_integrate_ready`：读取 `diff_anchor` 处的 `progress.md` `review_level`，tip 的值只能相同，或从 `single` 变为 `full`；否则判 `contract`。cleanup、integrate、integrate-chain、`ledger import` 都经过这里，全部覆盖。
    2. 测试：实现 commit 降级被拒；amend 降级被拒；`single -> full` 升级仍通过。

### M03. integrate-chain 连续性校验回归，可夹带未审 commit 或漏掉前驱（P0）

- 来源：deepseek#4、gemini#1
- 位置：`repo_task/integration.py` `_collect_chain`；单 task `cmd_integrate`
- 判断：成立，属本轮回归。旧检查 `current_tip^1 == previous_tip` 在「一 task 一 commit」下等价于「后继的 base 就是前驱 tip」。D2 允许证据 commit 后改成 `first_parent_segment(current_tip, previous_tip)`，只要求前驱 tip 在 first-parent 链上：
    - 前驱 tip 与后继 base 之间夹的非 task commit 会被合入，且不受任何 handoff/review 覆盖。
    - 前驱 tip 不再是链尾祖先时（如后继 fork 后前驱又追加了 commit），前驱被静默排除，其实现 commit 随链尾合入，却不归档、不写 `integrated`。
- 与建议的差异：
    - D01 恢复「恰好 1 个 commit」后，相邻检查直接恢复为旧的 `current_tip^1 == previous_tip`（此时它就等价于「后继 base 精确等于前驱 tip」），不另写新逻辑。
    - 两份审阅都只修 `integrate-chain`。单 task `integrate` 也不校验 `handoff.base_sha` 是否已在主干：对 base 为未合入前驱分支的 task 单独 integrate，会把前驱实现一起带进主干。同类缺口，一并修。
- 修复：
    1. 相邻成员：恢复 `current_tip^1 == previous_tip`（随 D01 删除 `first_parent_segment` 调用）。
    2. 链头与单 task integrate：`handoff.base_sha` 必须是 `base_ref`（`HEAD`；`--continue` 时为 merge 的 first parent）的祖先，否则拒绝，并提示先 integrate 前驱或用 `integrate-chain`。这一条同时堵住漏洞 B 的「前驱被静默排除」。
    3. 测试：gemini 的漏洞 A/B 现场各一条；单 task integrate 的 base 未合入时被拒。

### M04. 软链 `AGENTS.md`：status/plan 显示支持，apply 必然拒绝（P2，既有）

- 来源：deepseek#6、muse M3
- 位置：`template_sync/apply.py:124-128`；`repo_task/file_transaction.py:75-78`；`agents_merge.py` / `shared_files.py` 的 `resolve()` 分支
- 判断：成立，改造前已存在。apply 在 `transaction.begin` 阶段拒绝，此时还没写盘，结果是可恢复的失败；问题在于 status/plan 没提前告知。
- 与建议的差异：不让事务跟随软链。事务按设计禁止把恢复目标指到消费仓根之外，跟随软链会削弱这条保证。
- 修复：status/plan 检测到 `AGENTS.md` 是软链时明确输出「apply 不支持软链 AGENTS.md，先替换为真实文件」；`sync.md` 补一句。apply 拒绝文案同步给出这条指引。

### M05. 契约测试跳过条件改为 `verification_policy`，部分旧消费仓同步时变失败（P3）

- 来源：deepseek#13、muse M7
- 位置：`tests/test_contract_suite.py:84-88`
- 判断：成立，但影响面窄。`start` 早在 `647d701` 之前就要求 `verification_policy == project_configured`，能正常跑 task 的消费仓都已配好。受影响的只有「改了 `test_cmd` 但从未配 policy、也从未 start」的仓。
- 与建议的差异：不在 `workflow_migration` 里自动把 policy 改成 `project_configured`。这个字段代表用户确认过验证范围，脚本代填会让门禁失去意义。
- 修复：`sync.md` 加一句迁移说明；该测试断言失败信息补上「自定义命令须先把 `verification_policy` 改为 `project_configured` 并填写 `verification_notes`」。

## 采纳

### A01. `run-close` 门禁失败后 recovery 判错阶段，恢复死循环（P0）

- 来源：deepseek#3、gemini#3
- 位置：`repo_task/recovery.py:108-119, 136-150`；`control.py:174`；`recovery.md:24,32`
- 判断：成立。`run-close --result done` 在写 terminal 之前调 `require_report_done_ready`，失败时 attempt 仍是 `running`；`recovery_state` 只在 `terminal completed` 时检查 review gate，于是返回 `committed_unreported`，提示「补 terminal/report」；照做重跑 `run-close` 又被同一门禁拦下。`_evidence_repair_action` 的收尾命令写「exact cleanup」，terminal/report 缺失时也必失败。
- 修复（阶段名与修复动作按 D01）：
    1. 已提交实现 commit、report 尚未写入 done，且 `state == running` 或 `terminal completed` 时，都检查 review gate，失败即返回 `review_repair`；report 已 done 时返回停止诊断。
    2. `_review_repair_action` 收尾写为「amend 补审进同一实现 commit 后运行 `run-close {tid} --owner-id ... --result done`」（重入安全，补齐 terminal/report/cleanup）；`control.py:174` 的 suffix 去掉「仍卡在 committed_unreported」。
    3. `recovery.md:24,32` 同步。
    4. 测试：run-close 门禁失败后 recovery 判 `review_repair`，amend 后 `run-close` 一次完成（并入 D01 的 `test_review_repair.py`）。

### A02. `goal <tids...>` 不校验显式队列拓扑（P2）

- 来源：deepseek#9、gemini#5
- 位置：`repo_task/goal.py:60-78`
- 判断：成立。`goal t002 t001`（t002 依赖 t001）能冻结成功，`task-run` 第一步 `plan --serial` 随即报 `invalid_queue`。第 76 行「已归档」在 D1 后不准确：done 可能还在 `docs/tasks/`。
- 修复：显式队列冻结前调 `build_execution_plan(mode="serial", queue=queue)`；文案改为「已是终态」。

### A03. finish 不检查归档目标是否已被占用（P3）

- 来源：deepseek#10、gemini#7
- 位置：`repo_task/lifecycle.py` `_close_task`（`archive = status != "done"`）
- 判断：成立。done 跳过 `_require_archive_target_free`，残留目录要到 integrate 的 `_stage_archive_moves` 才报冲突，那时实现 commit 已入库。
- 修复：done 也做只读占用检查（不移动），放在零写入阶段。

### A04. finish 门禁失败不输出 AC 复验细节（P2）

- 来源：deepseek#11、gemini#6
- 位置：`repo_task/lifecycle.py:725-731`
- 判断：成立。`TICK-VOID` 导致 FAIL 时只看到 `overall=FAIL`。
- 修复：补 `ac_review` 与 `ac_review_detail`，与 `verify_integrate_ready` 输出一致。

### A05. `--message-file` 放在 task 目录内会被一起提交（P2）

- 来源：deepseek#12、muse M2
- 位置：`repo_task/lifecycle.py` `_finish_prechecks`、`cmd_finish` 的 `git add -A -- task_rel`
- 判断：成立。`_unstaged_outside` 豁免整个 task 目录，`git add -A` 会把 message 文件带进实现 commit。
- 修复：`message_path.resolve()` 落在当前 task 目录内时零写入拒绝。

### A06. 同步迁移 task 文件后不重建派生 index（P2）

- 来源：deepseek#7、gemini#4
- 位置：`template_sync/workflow_migration.py:440-487`；`apply.py:124-126`
- 判断：成立。迁移改了文件名（`note.md` → `progress.md`）和状态（`blocked` → `active`），但两个 `tasks_index.json` 不在事务里也没重建，同步 commit 带着过期索引入库，下一次 `task.py` 写操作再冒出无关 diff。
- 修复：两个索引路径加入 apply 事务；有实际迁移写入时调用 `repo_task.store.rebuild_index()`（`REPO_ROOT` 指向消费仓），并加入 `changed`。

### A07. 旧格式 `AGENTS.md` 导致 `plan` 直接退出（P1，既有）

- 来源：deepseek#5、muse B2
- 位置：`template_sync/status_plan.py:136,154`；`agents_merge.py:27-33`；`sync.md:15`
- 判断：成立，改造前已存在。`agents_section_status` 不捕获 `_agents_parts` 抛出的 `SyncError`，模板源或消费仓缺标题时 `plan` exit 1。`sync.md` 承诺的旧逻辑回退只对模板源生效。
- 修复：
    1. `agents_section_status` 捕获 `SyncError` 返回 `None`；plan 走已有的「未识别完整标题协议」分支，并说明是哪一侧缺标题。
    2. 消费仓缺标题时，apply 的拒绝文案改为可操作指引（「按模板补齐 `## 目录与读写规则`、`## 开发原则` 后重跑」）。
    3. `sync.md:15` 改为准确描述：旧逻辑回退只适用于模板源缺标题；消费仓缺标题时 apply 拒绝并给出指引。

### A08. 「执行 commit」与「实现 commit / 证据 commit」术语分裂（P3）

- 来源：deepseek#15、muse M9
- 位置：`task-run/SKILL.md:21,34`、`task-work/SKILL.md:3,12,47`、`task-create/SKILL.md:21`、`usage.md:12`
- 判断：成立。
- 修复：D01 删除证据 commit 后只剩一种 commit。finish 提交的统一叫「实现 commit」，所在分支叫「执行分支」（恰好一个实现 commit），清除所有「执行 commit」。`task-work` description 改动后重跑 `skill_registry` 生成，确认 opencode 命令无漂移（task-work 是内部流程，预期不生成命令）。

### A09. skill 与 usage 文档残留旧口径（P3）

- 来源：deepseek#16、muse M1、muse M10、gemini#9
- 位置与修复：
    - `task-run/SKILL.md:36`：「即其 `base.branch`」改为「即当前成员 `plan` 输出的 `base.branch`」。
    - `usage.md:22` 与 `architecture.md:14`：「`start` 在主仓默认分支执行」改为「`run-begin`（内含 start）在主仓默认分支执行」。
    - `task-preflight/SKILL.md:22`：「读取 spec、task」改为「读取 `task.md`、`progress.md`」。

### A10. skill 调用契约要求透传 attempt/execution_id，与主路径矛盾（P3）

- 来源：deepseek#17、muse M11
- 位置：`usage.md:84`；`task-work/SKILL.md:16`
- 判断：成立。主路径（`task-run:28`）只传 `tid` + `owner_id`。
- 修复：`usage.md:84` 改为「主路径传 tid、owner_id、写域与授权；attempt / execution_id 仅在 recovery 显式指定 exact identity 时传」；`task-work:16` 去掉未定义的「identity」。

### A11. `status` 与 `plan` 对 unmanaged opencode 命令口径不一致（P3）

- 来源：deepseek#21、muse
- 位置：`template_sync/status_plan.py:83` vs `:188`
- 判断：成立。同一消费仓，status 报漂移，plan 报一致。
- 修复：status 也排除 `unmanaged`，按 plan 的「非本机制管理，不碰」口径。

### A12. issue-handler 验证命令未随 WP11 加 `-n auto`（P3）

- 来源：deepseek#23、gemini#10
- 位置：`.agents/skills/issue-handler/SKILL.md:86,89`
- 修复：命令与工厂 `AGENTS.md` 一致（加 `-n auto`，说明需 pytest-xdist）；耗时描述按 L52 实测更新。

### A13. `test_check_review_status.py` 注释与现状相反（P3）

- 来源：gemini#11
- 位置：`repo/.repo_template/tests/test_check_review_status.py:857`
- 判断：成立。`4438f85` 起 integrate 门禁临时目录已写入 `task.md`。
- 修复：删除括号说明，改为描述被测场景本身（checker 单独运行、目录缺 `task.md`）。

### A14. `decision_log` 导言与表格不一致（P3）

- 来源：deepseek#18、gemini#12
- 位置：`docs_repo/decision_log.md:7`
- 判断：成立。`d43deba` 删除了 L49（D-10 dogfood）行，导言仍写「L40–L52（D-11 撤销）」；现行权威文档列表漏了 `recovery.md`、`sync.md`。
- 修复：导言改为「L40–L52（D-10/L49 于 `d43deba` 移除，D-11 撤销）」，权威列表补 `recovery.md`、`sync.md`。不恢复 L49 行内容（见 R02）。

### A15. `--decision` 重复时静默以后者为准，非法值跳过后 exit 0（P2，既有）

- 来源：muse
- 位置：`template_sync/cli.py:20` `_parse_decisions`；`shared_files.py` `_apply_shared_unit`
- 判断：成立。写错 disposition 时该单元不处理，apply 仍报成功。
- 修复：`_parse_decisions` 遇重复 unit、未知 unit 或未知 disposition 直接抛 `SyncError`，在事务开始前拒绝。

### A16. `--source` 覆盖被 `prompt` 写进 state（P2，既有）

- 来源：muse
- 位置：`template_sync/state.py:29-31` `read_state`；`cmd_prompt` 的 `write_state(data)`
- 判断：成立。`repo_sync.py --source X prompt add ...`（local scope）会把 X 持久化为 `template_source`，而 `--source` 的帮助写的是「本次来源覆盖」。
- 修复：`read_state` 读入时记下原始 `template_source`，`cmd_prompt` 持久化前还原；只有 `apply`、`init` 按设计写来源。补测试。

### A17. `install-hooks` 未加消费仓锁，与 `apply` 并发写 `.claude/settings.json`（P3，既有）

- 来源：muse
- 位置：`template_sync/hooks.py:130` `cmd_install_hooks`
- 判断：成立。`apply`、`init`、`prompt`、`link-skills` 都有 `@consumer_locked`，唯独它没有，并发时可能丢失更新。
- 修复：加 `@consumer_locked`。

### A18. plan 软链表只查 `.claude/skills`，漏 `.agents/skills`（P3，既有）

- 来源：muse
- 位置：`template_sync/skills_links.py:285` `symlink_status`
- 判断：成立。`link-skills` 管理两个宿主目录（`:64`），plan 只报一个，`.agents/skills` 缺链或坏链看不见。
- 修复：`symlink_status` 遍历两个宿主目录，输出带宿主前缀。

### A19. `.gitignore` 合并把 `!` 取反规则当 ignore 行删除（P2，既有）

- 来源：muse
- 位置：`template_sync/shared_files.py:22-47` `merge_gitignore`
- 判断：成立。prompt 拦截 `.env` 时，`!.env.example` 含该子串会被删，结果是 ignore 更多文件，与拦截意图相反。
- 修复：删除与跳过判定都只针对非 `!` 行；`!` 行原样保留，模板侧的 `!` 规则照常追加。

### A20. MCP URL 端口非法时抛裸 `ValueError`（P3，既有）

- 来源：muse
- 位置：`template_sync/shared_files.py:131`（`parts.port`）
- 判断：成立。端口非数字或越界时 `urlsplit(...).port` 抛 `ValueError`，用户看到的是 traceback 而不是 `SyncError`。
- 修复：捕获后转 `SyncError`，指明 MCP 文件与 server 名，不回显 URL 原文（可能含凭据）。

### A21. workflow 迁移不跟踪代码围栏（P2，既有）

- 来源：muse
- 位置：`template_sync/workflow_migration.py:40` `_guide_blocks_by_heading`、`:66` `_replace_guide_blocks`、`:135` `_rename_legacy_title`
- 判断：成立。`_replace_guide_blocks` 把用户 `task.md` 代码块里的 `### ` 行当作小节标题，可能把规范块插进代码示例；`_rename_legacy_title` 遇 ` ``` ` 或 `~~~` 都翻转状态，混用两种围栏时判断出错。迁移会改写用户数据，必须修。
- 修复：抽一个围栏跟踪函数（按开围栏的字符与长度匹配闭围栏），三处共用；围栏内的行不识别为标题或规范块。补测试：代码块内含 `### 验收标准`、混合围栏。

### A22. `sync.md:21` 两代 `task.md` 同名，表述有歧义（P3）

- 来源：muse
- 修复：写明「最早布局 `spec.md`（规格）+ `task.md`（过程总账），中间布局 `task.md`（规格）+ `note.md`（过程总账）」。

### A23. 死代码与重复定义（P3）

- 来源：muse Nit
- 判断与修复：
    - `template_sync/context.py:42` `AGENTS_INTRO_HEADING`：全仓无引用，删除。
    - `template_sync/assets.py:90` `sync_skill`：产品代码无调用，只有 `factory_tests/test_repo_sync.py:288` 在测；连同该测试删除。
    - `shared_files.py:273-275` 第二次 `_apply_agents_sectioned`：首次调用已返回 False，第二次必然也是 False，删除。
    - `workflow_migration.py:229` `_TASK_BRANCH_RE`：改用已导入模块里的 `repo_task.context.TASK_BRANCH_RE`。

### A24. review 修复测试缺「禁止 rewind / reserve」断言（P3）

- 来源：muse
- 位置：`factory_tests/test_evidence_commit.py:83-92`
- 判断：成立。删掉的 `test_evidence_repair.py` 之后，新测试只断言「证据 commit」和「无 amend」。
- 修复：该文件随 D01 删除。新的 `test_review_repair.py` 断言 `review_repair` 的 action 含「禁止 rewind、reserve」「amend」「run-close」，且不含「证据 commit」。

### A25. `.prettierignore` 旧标题按原行精确匹配（P3，既有）

- 来源：muse
- 位置：`template_sync/shared_files.py:79-82`
- 判断：成立。旧标题行带首尾空白时不被替换，有新增规则时会再追加一份新标题，变成两份。
- 修复：按 `strip()` 后比较定位旧标题。

### A26. `goal-check` 用当前主干重算分链，不用冻结时的图（P3，既有）

- 来源：muse
- 位置：`repo_task/goal.py:315-377`；`_write_snapshot`
- 判断：成立。快照只存 `queue`；`task-run` 规定「队列启动后沿用该结果」。goal 进行中有人改了 `depends_on` / `conflicts_with`，goal-check 的链归属与 STOPPED 判定就会和执行时不一致。
- 修复：`goal` 冻结时把队列内边写入快照，`goal-check` 用快照边。按「不留兼容层」原则，旧快照缺边时报错并提示 `goal --reset`，不回退到重算。

## 拒绝

### R01. 恢复工厂 CI（deepseek#8、muse M8）

`d43deba` 是用户提交的有意删除（commit 标题即「remove dogfood acceptance and GitHub CI」），工厂 `AGENTS.md` 已写明改模板必须跑全量。是否恢复 CI 由用户决定，不作为缺陷处理。decision_log 的记录问题由 A14 处理。

### R02. 恢复被改写的归档审阅原文（deepseek#19）

`docs_repo/archive/review_20261024.md` 的 M-07 和 `reviews/2026-07/grok.md` 是用户在 `d43deba` 中有意改写的，属于用户内容，不回退。注意：这与 `docs_repo/README.md`「结论被后续文档修正时在原文段落首行标注，不静默保留多版本」的纪律冲突。如需保留追溯性，由用户决定是否在原段落补一行注记。

### R03. review prompt 的 AC 示例移出代码块（deepseek#14、muse M5）

整个「输出格式」本来就是一个 ` ```markdown ` 报告模板，`## 结论`、`verdict:` 行都在同一块里。reviewer 照模板输出时写的是块内容，不是围栏本身；如果照抄围栏，`verdict` 行同样失效。示例位置没有引入额外风险。

### R04. `view_server` 保留 `doc=note` 别名（deepseek#20、muse）

`board.html` 已改用 `doc=progress`；view server 是本机临时进程，带会话 token，不存在需要兼容的外部书签。按「不留兼容层」原则不加别名。

### R05. 测试替身改为模块级 monkeypatch（deepseek#22、gemini#8、muse 测试补丁项）

`test_git_timeout_is_reported_as_sync_error` 用 try/finally 恢复；`builtins.input`、`chdir`、`Path.read_text` 都走 `monkeypatch`，测试结束自动恢复。xdist 每个 worker 是独立进程，不存在同进程并发。属风格不统一，不是缺陷。

### R06. 修正 `plan_macro_overhaul_2026_11.md` 的 C3 签名与 G1 守卫描述（deepseek#24、gemini#13）

该文首部已声明「下文保留改造时的历史方案与基线」。C3、G1 是当时的方案文本，G1 的守卫后来被同文 H3 撤销，改写会抹掉推导过程。

### R07. `cmd_finish` 加 `@control_locked`（deepseek#25、muse）

finish 只写自身 worktree 与本地 index，不改主仓控制面；读 ledger 已在 `ledger_read` 的文件锁内完成。加控制面锁会在最长 600 秒的 `git commit` 期间阻塞主仓所有控制操作，代价大于收益。两份审阅也都认为影响小。

### R08. `"1 skipped"` 断言脆弱（deepseek#26、muse）

契约套件全仓只有一处 `pytest.skip`（`test_contract_suite.py:87`），导出产物与插件由工厂测试自己控制。该断言就是为了确认「配置后恰好跳过这一项」，是有意的精确断言。

### R09. `start` 对依赖调用 `verify_integrate_ready`（muse M4）

依赖要变成 done，必须经过 finish 的 review 门禁；done 之后变 stale 的情况由 A01 / D01 的 review_repair 处理，integrate-chain 也会逐成员复验（M03 之后更严格）。在 `start` 再做一遍完整复验，只是把同一检查提前，不堵新的缺口；`architecture.md` 的「done 即满足」是既定口径。

### R10. 证据 commit 允许集三处文档各说各话（muse M6）

裁决时三处语义一致（允许集相同，`task.md` 属于「其它路径 / 交付」）。D01 删除证据 commit 后，这三处整段重写，本项不再适用。

### R11. `store.py` 改按目录前缀分组（muse）

归档组本来就按 `startswith("docs/archive/tasks/")` 划分；活跃组用 `not in archived`，不同目录的记录 `dir` 字段不同，不会相等误判。没有缺陷。

### R12. `documents.py:72` 解析错误写死 `progress.md`（muse）

`parse_front_matter*` 的所有调用方都在解析 `progress.md`，`source` 参数已带真实路径。文案准确。

### R13. `plan.py` 的 `chains` 字段恒为单链（muse）

`task-run/SKILL.md:25` 明确要求读 `members[]` 与 `queue_chains[]`；`chains` 是串行全序，`goal` 默认队列用的正是这个全序。字段含义与用途一致。

### R14. `track_worktree.py:166` 子串匹配（muse）

这是 hook 的廉价前置过滤，随后由 `extract_tid` 严格解析、`resolve_worktree` 核实存在才写记录。误命中只会多走一次解析。

### R15. `task-integrate/SKILL.md:49` 与归档移动矛盾（muse）

`_prepare_native_merge` 遇冲突在 `_stage_archive_moves` 之前就退出（`integration.py:539-551`），所以冲突解决时 task 目录确实还在 `docs/tasks/` 原位，文档正确。

### R16. `task-work/SKILL.md:47`「task 目录由 finish 自行处理」不明（muse）

同一句已写明 agent 只 `git add` 交付文件；finish 自己暂存 task 目录。表述清楚。

### R17. `task_flow_support.py:60` 路径可预测、隐式 `sys.path`（muse）

message 文件位于每个测试独有的 `tmp_path` 下；`sys.path` 由 `factory_tests/conftest.py` 显式注入（G1 设计）。没有缺陷。

### R18. `progress.md` 模板占位符未标注必须替换（muse）

「待执行时填写」本身就是说明；finish 遇占位会拒绝并给出明确错误（`monitoring.py:359` `HANDOFF_PLACEHOLDERS`）。

### R19. `task-run:33` 缺宾语（muse）

回读该行，句子完整，没找到缺陷。

### R20. `share_prompt.txt:117` 引用 `.agents/skills/` 改 canonical（muse）

消费仓 `.agents/skills/*` 是指向 `.repo_template/skills/*` 的受管软链，路径可达，也是宿主读取入口。没有缺陷。

### R21. 中英空格全仓扫描、「整链/队列」统一（muse）

没有给出具体位置；「链」与「队列」是两个不同概念（队列分多条链），不应合并。

### R22. `template_sync/__init__.py` 英文 docstring、`AGENTS:11` 与 `testing.md:5` 括号体例（muse Nit）

仓内已有多处英文 docstring（如 `monitoring.py`），没有中文强制约定；括号体例差异不影响含义。不是问题。

## 实施顺序

每步完成后跑对应子集，最后跑全量。

1. D01 先行：删除证据 commit、恢复单 commit 校验、`handoff.review_scope`、review_repair 阶段与对应文档/测试。M01–M03、A01 都建立在它之上 → 验证：D01「验证」一节的残留扫描与子集
2. P0 其余：M01、M02、M03、A01 → 验证：新增回归测试 + `pytest factory_tests/test_finish_atomic.py factory_tests/test_review_repair.py factory_tests/test_archive_at_integrate.py -q -n auto`
3. P1/P2：A07、A06、A02、A04、A05、A15、A16、A19、A21 → 验证：`pytest factory_tests/test_repo_sync.py -q -n auto` 及相关新增测试
4. P3：其余代码项（A03、A11、A17、A18、A20、A23、A25、A26、M04、M05）→ 验证：对应测试 + 契约子集
5. 文档：A08–A14、A22、A24，以及 M04 / M05 的文档部分、D01 的 decision_log L53 与方案标注；`task-work` description 改动后重跑 registry 生成 → 验证：`python3 repo/.repo_template/scripts/md_format.py --check`、registry 一致性测试
6. 全量门禁：`pytest repo/.repo_template/tests factory_tests -q -n auto -p no:cacheprovider --basetemp "$(mktemp -d)"`
