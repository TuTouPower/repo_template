# Intensive Review：状态所有权收口与工作流换代（1471b13..947665a9）

## 范围与方法

- 起止 commit：`1471b132031d80e3cc0c041ff6430ef47e8ff919`（上次审阅止点）→ `947665a9ecc1cb4053279b5cb4fef7abbfac9253`（当前 HEAD，2026-11-10 17:57:50 +0800）。已核实 1471b13 是 HEAD 的祖先。
- 规模（`git diff --stat` 实数）：90 个提交、250 个文件、+14296/−7197。
- 主线：F01–F14 审阅修复、G1–G7/WP6 收口、状态所有权收口（运行态迁出工作区到 Git common dir）、specs_index 退役、AGENTS 两区协议、.gitignore 托管块、repo_hygiene 退役、docs_repo 改名 factory_docs、factory_tests 按主题拆分。
- 方法：按主题通读全部 diff（脚本、hooks、契约测试、消费侧模板与文档、factory_docs），每处按「改动目的 → 是否符合目的 → 漏洞/不一致/风险」核对；关键结论均回到 HEAD 源码定点复核（文件:行见正文）。
- 测试验证（本机实测）：本机无预装 pytest，在 /tmp 临时 venv 安装 pytest 后运行。全量串行运行约 30 分钟未结束（本机无 pytest-xdist、1.5 核共享 CPU；计划记录的基线为 `-n auto` 并行 495 秒），已终止，未取得全量结果。替代验证：`--collect-only` 收集 1572 个用例，与计划记录的最终全量「1571 passed, 1 skipped」计数吻合；本轮核心子集（state-root 迁移、context 缓存、迁移事务与 reviews 守卫、agents-zones、ADR、healthcheck、消费仓整链、commit-msg 隐私、pre-commit、repo_cleanup 契约）复跑 86 passed / 1 skipped。子集首跑曾出现 6 个 repo_cleanup issue-pair 用例失败，同命令复跑、单文件、逐对二分均不复现，详见问题 13。

## 问题逐项

### 1. sync.md 与 recovery.md 仍称 repo-cleanup 遇未结束事务跳过 .scratch，与本轮代码矛盾

- 位置：`repo/.repo_template/docs/sync.md:95`、`repo/.repo_template/docs/recovery.md:64`；代码 `repo/.repo_template/scripts/repo_cleanup.py`。
- 事实：sync.md 写「apply 成功到 transaction accept 之前，repo-cleanup 会跳过 scratch 并提示先 accept 或 rollback，其它类别照常处理」；recovery.md 写「存在未结束文件事务或事务记录损坏时，repo-cleanup 跳过 .scratch/，其它类别照常清」。本轮 repo_cleanup.py 已删除 `_has_unfinished_sync_transaction` 与 `transaction_incomplete` 跳过逻辑（事务已迁到 Git common dir，.scratch 不再承载事务），对应契约测试整段删除，当前代码对事务状态不作任何检查，只在控制面锁被占用时让带 scratch 的清理整体失败、零删除。
- 为什么是问题：文档承诺的保护行为已不存在。事务处于 applied 未 accept 时执行 cleanup，.scratch 里的来源缓存会被直接删除（事务数据在 common dir，不丢，但与文档承诺不符）；按 recovery.md 排查现场的人会基于错误前提判断。recovery.md 本轮还被编辑过（其他段落已更新为 common dir 口径），唯独此段漏改，属于同轮改造内的文档与实现脱节。

### 2. specs 迁移与计划 WP3.2 不一致：已有 front matter 缺字段不按索引补齐、直接停止

- 位置：计划 `factory_docs/plans/plan_state_ownership_and_workflow.md:456`（WP3.2：「已有 front matter 的字段逐项校验，缺字段用索引补齐，冲突停止」）；实现 `repo/.repo_template/scripts/template_sync/specs_migration.py` 的 `_validate_before_write` 与 `migrate_specs_layout`。
- 事实：对已有 front matter 的 spec，实现直接调用 `repo_task.specs.load_spec` 校验，缺 `status`/`tasks` 即抛 SpecError 并转 SyncError 停止迁移，没有任何「用索引补齐缺字段」的路径。就绪审计（`factory_docs/audits/consumer_readiness_20261110.md`，great_websites 节）已实测指出该仓 32 份 spec 属此情形、「与计划 WP3.2 描述不一致，需先确认修工厂还是修消费仓」。
- 为什么是问题：计划、审计、实现三方口径未收口就进入收官状态（计划已记录「最终全量结果」）。按现状执行迁移，great_websites 必然在 specs-layout 步停止，且现场报错不会提示「计划曾承诺可补齐」，处置责任被推给执行迁移的人。

### 3. specs 迁移对「已有 front matter 且不在索引」的 spec 误报 tasks 冲突

- 位置：`repo/.repo_template/scripts/template_sync/specs_migration.py`，`_validate_before_write`。
- 事实：校验逻辑为 `tasks = index.get(path.stem, ["baseline"])`，再要求已有 front matter 的 `current["tasks"] == tasks`。一份已被人工写好 front matter（例如消费仓在 988fd06 之后手工试行新格式）、tasks 为真实 tid 列表但 specs_index.md 中无对应行的 spec，会被判为「与索引 ['baseline'] 冲突」而停止迁移。
- 为什么是问题：索引缺行与 front matter 内容冲突是两类不同情形，前者应按缺行处理（或至少报错文案应指出缺行），现实现把缺行伪装成内容冲突，且阻断对象恰是已朝新格式靠拢的仓。与问题 2 同源但触发条件与代码位置不同，单列。

### 4. ADR 迁移不校验 NNN 重复，重复编号静默通过且 supersedes 任意取一

- 位置：`repo/.repo_template/scripts/template_sync/adr_migration.py:111`（`by_nnn = {entry["nnn"]: filename for ...}`），及 `adr_migration_status`。
- 事实：`decisions.md` 中出现两条同号 `## NNN` 时，字典推导静默覆盖，状态报告与迁移执行都不提示；生成文件名含日期与 slug、通常不撞名，迁移会「成功」；若某条 ADR 声明「替代：NNN」且 NNN 重复，`supersedes` front matter 指向哪一条由字典覆盖顺序决定。就绪审计已把 great_websites 的 `044` 重复、omni_panel 的 `053` 重复列为逐仓阻断项。
- 为什么是问题：审计口径认定「NNN 唯一」是迁移前提，工具链却既不检测也不报告，迁移执行者按审计清单以为会被拦下，实际会带着重复编号完成迁移，后续按编号引用 ADR（含 supersedes 链）即产生歧义。

### 5. 空 decisions.md（仅模板前言）没有任何迁移出路

- 位置：`repo/.repo_template/scripts/template_sync/adr_migration.py` 的 `_entries`（无任何 `## NNN` 条目即抛 SyncError）与 `adr_migration_status`（把该错误作为 finding 返回）。
- 事实：`docs/blueprint/decisions.md` 存在但只有模板前言、零条目时（就绪审计实测 haiwa_bot 命中），`migration_findings("adr")` 恒非空 → 日常 apply 被 `_require_current_schema` 永久阻断；`migrate adr` 又必然因同一抛错失败。工具链没有「退役空 decisions.md」的迁移或豁免路径，只能靠人手工删除该文件，而文档（sync.md 的 adr 条目说明）没有写这条处置。
- 为什么是问题：一个内容为空的模板占位文件把整仓同步永久卡死，且卡死信息（「未找到任何 ## NNN 条目」）不指向可行处置。审计把它列为消费仓阻断项，但缺口在工具链一侧。

### 6. 就绪审计中 CBrowser 的 ADR 条目与 HEAD 实现不符，且未按厂内纪律标注修正

- 位置：`factory_docs/audits/consumer_readiness_20261110.md:68`；实现 `repo/.repo_template/scripts/template_sync/adr_migration.py` 的 `_entries`。
- 事实：审计称 CBrowser 第 7 行无编号 H2「当前 adr_migration._entries 不会主动拒绝该 H2……实际行为与计划判定不一致」。HEAD 的 `_entries` 对围栏外任意位置（包括首条 `## NNN` 之前）的非 NNN H2 一律抛 SyncError（本轮提交 8f1d8cd「围栏外无编号 H2 一律停止」已修复），审计所述行为在 HEAD 不成立。审计文件未做任何后续标注。
- 为什么是问题：`factory_docs/README.md:31` 的厂内纪律是「结论被后续文档修正时，在原文段落首行标注被哪份文档发展，不静默保留多版本」。该审计是后续逐仓迁移排期的依据文件，读者按它会误判 CBrowser 的 ADR 阻断状态与处置动作（审计要求「迁移前应修正」，而实现其实已按计划停止并报错）。

### 7. pilot 演练审计结论未标注后续状态：阻断已修复、复跑已被裁决否决，原文仍写「暂不建议进入真实迁移」

- 位置：`factory_docs/audits/pilot_agent_rank_20261110.md` 结论段；修复在 `repo/.repo_template/scripts/template_sync/common.py` 的 `migration_transaction`（docstring 记录争用成因与改法）及 `factory_tests/sync/test_migration_transaction_and_reviews_guard.py` 回归用例；裁决在 `factory_docs/decision_log.md` L71 与计划第 6/7 节。
- 事实：pilot 在提交 53eabd2 上发现 entity-storage 与 state-root 争用 common dir 的 `sync_transaction` 而阻断，结论为「需先修模板并补回归用例，再重跑本演练」。HEAD 已按其要求完成修复与回归用例；同时 L71 已裁决不做真实仓备份恢复/复跑演练，证据边界为代码路径与工厂 rollback 用例。但审计原文未加任何后续状态标注，仍以「暂不建议进入真实迁移」收尾。
- 为什么是问题：同问题 6，违反 factory_docs README 的标注纪律。只读该审计的人会以为迁移仍被阻断、且欠一次复跑演练；只读计划的人则以为此事已闭环。两份同仓文档对同一事项给出相反的现状判断。

### 8. install-hooks 在模板 hooks 未安装时返回退出码 0

- 位置：`repo/.repo_template/scripts/template_sync/hooks.py`，`cmd_install_hooks`（已有 `core.hooksPath` 分支，约 191–211 行）。
- 事实：消费仓已有其它 hooksPath 且未带 `--force` 时，函数打印「已有 core.hooksPath=…，不覆盖；模板 hooks 未安装，需人工接入」并附 husky/lefthook 片段，随后 `return 0`。
- 为什么是问题：退出码语义是「成功」，实际结果是「未安装」。脚本化调用方（初始化脚本、CI 检查）若按退出码判定，会把未接入 hooks 的仓误判为已接入，后续 commit-msg/pre-commit 门禁静默缺位。`export_template.py --bootstrap` 已单独用 `_hooks_installed` 核验、healthcheck 也能检出该状态，所以影响面有限；但命令自身的退出码与结果不一致仍是工具链契约瑕疵。usage.md 已如实描述该行为，属明知保留，记录在案待裁。

### 9. agents_merge 的兼容垫片已成死代码，与工厂「破坏性升级优于保留兼容层」原则相悖

- 位置：`repo/.repo_template/scripts/template_sync/agents_merge.py:99-110`（`_agents_parts`、`_replace_agents_force`，docstring 自述「兼容旧调用」）。
- 事实：全仓检索这两个符号：`.repo_template/scripts` 内无任何生产调用方；factory_tests 的 9 个 test_repo_sync 拆分模块只在导入行出现 `_replace_agents_force`，无一处实际调用。垫片映射到的「force 字段」在两区协议下已不存在。
- 为什么是问题：工厂 AGENTS.md 修改原则明确「破坏性升级优于保留兼容层」。无调用方的兼容入口长期保留，会让后续维护者误以为 force 替换语义仍受支持、仍需维护，且测试导入行制造「该入口有覆盖」的假象。与本轮「零兼容负担」的整体取向（删 repo_hygiene、删 specs_index、删 `--agents-file` 等）不一致。

### 10. 契约测试 test_lock_errors_become_cleanup_errors 删除 TaskDataError 参数化，生产分支失去覆盖

- 位置：`repo/.repo_template/tests/test_repo_cleanup.py:527`；生产代码 `repo/.repo_template/scripts/repo_cleanup.py:563`。
- 事实：该用例原以参数化同时覆盖 OSError 与 TaskDataError 两种锁错误，本轮改为只构造 `OSError("busy")`。生产代码 `except (TaskDataError, OSError)` 仍在，且 F07 之后 `git_common_dir` 失败即抛 TaskDataError（不再静默回退），该类型恰是更可能真实命中的一支。
- 为什么是问题：行为本身正确，但契约测试对 TaskDataError → CleanupError 的转换不再有任何用例守护；后续若有人把 except 收窄为只捕 OSError，测试全绿也发现不了。属本轮测试拆分/收窄中的覆盖回退。

### 11. 产物契约测试 test_spikes.py 夹具仍创建已退役的 docs/specs_index.md

- 位置：`repo/.repo_template/tests/test_spikes.py:38` 附近（注释「准备 specs_index」，写一张空表头的 specs_index.md 到夹具仓）。
- 事实：specs_index 已于本轮整体退役（解析器只留在 `template_sync/specs_index_migration.py` 供一次性迁移），spike 校验链不再读取该文件；该夹具写入不影响测试结果，纯属残留。
- 为什么是问题：产物契约测试随模板分发到每个消费仓，死夹具会让后续维护者误以为 spikes 仍依赖 specs_index 存在，甚至照着它给新夹具继续补这份文件。退役清理在脚本、文档、registry、软链各面都做完了，唯独这处测试夹具漏网。

### 12. repo/AGENTS.md 的 ADR 文件名格式与迁移器生成格式不一致

- 位置：`repo/AGENTS.md:33`（开发原则：「写 `docs/blueprint/decisions/YYYYMMDD_<slug>.md`」）；`repo/.repo_template/scripts/template_sync/adr_migration.py` 的 `_filename`。
- 事实：迁移器生成的文件名是 `YYYYMMDD_NNN_slug.md`（日期+编号+slug），AGENTS.md 给手工新建 ADR 的命名格式里没有编号段。
- 为什么是问题：迁移后的仓里，迁移产物与按 AGENTS.md 指引手工新建的 ADR 会是两套命名；编号是 ADR 的引用主键（supersedes、文档互引都用 NNN），命名指引缺编号段会诱导新建无编号 ADR，与「一条一文件、按编号管理」的模型相冲突。

### 13. 测试观察：repo_cleanup issue-pair 用例出现一次不可复现失败；全量串行在本机未能跑完

- 位置：`repo/.repo_template/tests/test_repo_cleanup.py` 的 6 个 issue-pair 用例（test_unpublished_issue_pair_is_protected_when_recent、test_unpublished_issue_pair_expires_after_three_days、test_published_issue_pair_is_unowned_and_cleaned、test_orphan_issue_copy_is_unowned_and_cleaned、test_corrupt_issue_front_matter_is_treated_as_unpublished 等）。
- 事实：本机核心子集首跑时上述 6 个用例 FAILED（其余 80 passed / 1 skipped）；随后同文件单跑 28 passed、与每个候选文件逐对二分均 passed、同子集原样复跑 86 passed / 1 skipped。首跑失败出现在全量串行进程被终止后不久，本机 /tmp 为 512M tmpfs 且当时有大量临时仓库残留，怀疑与资源受限（ENOSPC/负载）有关，但未抓到首跑断言原文，不能确证机制。
- 为什么列出：不可复现不等于不存在。该组用例守护的是 L68 裁决的「未发布 issue 报告对在 .scratch 中受保护 3 天」行为，若在资源受限环境下真有写入/读取竞态，受损的是用户未发布的报告文件。建议在作者环境以串行全量（非 -n auto）复跑一次该文件确认。另外记录环境事实：本机无 pytest-xdist，全量串行约 30 分钟未结束即终止，本报告的测试结论以收集计数（1572，与计划基线吻合）+ 核心子集复跑绿为界，不冒充全量实测。

## 无问题主题的核查结论

- 状态所有权迁出（ledger/audit/goal_queue/statusline/sync_state/两类文件事务/execution_context 迁入 Git common dir 或 worktree 私有 gitdir）：`context.py` 的 `git_common_dir` 改为失败抛 TaskDataError（F07），消除静默回退；`state_root_migration.py` 有 worktree 登记、未知残留、statusline 双份、execution_context 残留四类前置阻断，拷贝校验、失败回滚、恢复失败保留备份并报告路径，工厂测试含故障注入用例且本机复跑通过。pilot 发现的 sync_transaction 争用已由 `migration_transaction` 的旧目录优先策略修复并有回归测试（问题 7 只是审计文档未标注，不是代码未修）。
- commit_txn journal v2→v3（新增 `original_index_tree` 与 `rolling_back`）：快照落盘后回读校验摘要、回滚前校验全部副本、回滚后复验 task 哈希与 index tree（F05），recovery.md 已同步为 v3 口径且明示旧 journal 不自动升级。
- AGENTS 两区协议与 .gitignore 托管块：apply 整块替换模板区/托管块、块外不动；`agents_zone_migration` 按 sync_manifest 的 resolved_commit（回退 sync_state 的 last_synced_commit）取历史模板做行级分类，无基线拒绝猜测；apply 前 `_require_current_schema` 对全部迁移类型（含 state-root）统一阻断，未迁移仓不存在「新 .gitignore 先落地、docs/runtime 失去忽略」的暴露窗口。迁移顺序 KINDS 与 sync.md 三处表述一致。
- commit-msg hook 改为所有提交扫描最终消息：scissors 截断要求其后确有 `diff --git`、按 core.commentChar 推断注释前缀、全程 fail-closed（F03），工厂测试覆盖宽编码与中段家目录形态。
- privacy：解码改多候选并集扫描（去无 BOM UTF-16 的 NUL 门槛）、新增 macOS `/System/Volumes/Data/Users/` 与 UNC 两种中段家目录形态（F04，测试明确不采纳 `//server/...` 与 `C:Users\...`）、rename 感知的新增行比较；privacy-audit 的排除项（终态 task 目录 + retired spec）由 cli.py 按 `--ref` 的 tree 计算，与 usage.md 描述一致。
- pre-commit：staged_md 过滤由 ACMR 改为 AM（改名 md 不自动格式化）已在 usage.md 明示为有意行为并给出手动补跑命令，不算暗改；新增的 review 保留检查（live reviews 超 5 阻断、引用保护、merge 提交跳过）与 reviews_migration 的引用扫描口径（tasks/blueprint/handoff）一致。
- 退役清理完整性：repo_hygiene（脚本、skill、registry、双宿主软链、opencode 命令、usage 表）、specs_index（脚本、产物文件、finalization 改读 front matter、render_review_prompts 注入 linked_spec_summary）、task-merge→task-combine 改名（skill/registry/软链/opencode/usage 五面同步，R052/R081 两处引用已核）、`task-integrate` 转公开入口（skill/registry/usage 口径一致）、`apply --agents-file` 删除后 sync-core skill 流程同步改写。除问题 11 的测试夹具外未发现悬空引用。
- export_template：`is_non_distributable` 收窄（删 docs/runtime 前缀与 sync_state 两条）与状态迁出方向一致，产物树本身已无这些路径；新增 `--bootstrap` 对 hooks 是否真装上单独核验并如实打印，repo/README.md 的初始化步骤已同步。
- healthcheck 与 install-hooks 的关系：healthcheck 对「hooksPath 指向别处且未人工接入」报 issue、对「未设置 hooksPath」只提示 install-hooks，与 usage.md 描述一致（问题 8 仅限 install-hooks 自身退出码）。
- factory_tests 拆分（test_repo_sync 拆 9 个主题模块、test_task_start_flow 拆分、按 docs/integration/review/state/sync/workflow/support 归目录）：拆分后导入面与种子 fixture（conftest 的 session 种子 + fast_copy_repo）自洽，本机复跑所及子集全绿；消费仓整链测试（旧布局 fixture → migrate all → apply → healthcheck）为真断言链而非占位。
- factory_docs 改名（docs_repo → factory_docs）与 archive 归位：纯改名加指向修正，工厂根 AGENTS.md/README.md 指向已同步，未发现指向旧路径的活引用。

## 总裁决

**有条件通过。** 本轮 90 个提交的主线改造（状态所有权收口、specs/AGENTS/.gitignore 三项协议换代、迁移体系补齐）在代码层成立：关键路径有真实断言的工厂测试守护，pilot 演练发现的争用阻断已修复并有回归，本机核心子集复跑全绿、全量收集计数与计划基线吻合；未发现阻断性的代码缺陷。13 项问题中无一推翻改造方向，但其中 4 项应在对存量仓执行迁移之前处理：问题 2（specs 缺字段补齐与计划不符，great_websites 必然中招）、问题 4（ADR 编号重复不检测，审计口径与工具行为相反）、问题 5（空 decisions.md 把整仓同步永久卡死且无处置路径）、问题 1（cleanup 保护承诺已失效但文档仍在承诺，recovery.md 同轮漏改）。问题 6、7 是同仓审计文档未按厂内纪律标注后续状态，会直接误导迁移排期，建议随手修正。问题 3、8、9、10、11、12 为边界与卫生问题，可列入后续 task 批量处理。问题 13 建议在作者环境补一次串行全量复跑确认。按 L71 裁决，迁移真实性证据以工厂用例为界、本报告不要求补做真实仓演练；但问题 2、4、5 涉及的三个仓（great_websites、omni_panel、haiwa_bot）在修复前不应进入迁移批次。
