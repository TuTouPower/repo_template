# 审阅结果决策

## 目录

factory_docs/reviews/review_20261110_203124

## 报告来源

- 已读：review_intensive.md
- 缺失：无 bundle_*.md，以聚合 review_intensive.md 为准（该次审阅为单报告形态，未产出 bundle）
- 合并说明：报告 13 项问题 → 12 个决策项。问题 2、3 同根（specs 迁移逐 spec 目标值推导在校验与写入两处重复且口径错误），合并为 A2；其余一一对应。各项均已回到 HEAD（ad93d4b）源码复核；问题 7 事实描述部分不准（审计已有复跑段），已在 A7 据实修正。
- 决策准则：按用户 2026-11-10 指示的架构铁律裁定——架构至上、不计改动量；拒绝局部补丁，从根因修正；零兼容负担。初版中 D1（specs 补齐）、D2（空 decisions.md）、D3（install-hooks 退出码）据此转为采纳并按根因重写修复方案；初版 R1（ADR 命名）据此重开为待决定 D1，用户裁定选 B，转为 A11。

## 统计

- 采纳：11 项
- 不采纳：1 项
- 待决定：0 项

## 待决定项（请先决策）

无。

## 采纳项

### A1. sync.md 与 recovery.md 仍承诺 repo-cleanup 遇未结束事务跳过 .scratch（问题 1）

- 来源：review_intensive
- 位置：`repo/.repo_template/docs/sync.md:95`、`repo/.repo_template/docs/recovery.md:64`
- 优先级：HIGH
- 详细判断理由：已复核。`repo_cleanup.py:552-566` 只在 scratch 类别时取控制面锁，锁忙或取锁失败整体失败；不检查事务状态。事务已迁入 Git common dir，.scratch 只剩来源缓存，代码是正确设计，文档过期。根因在文档，不恢复跳过逻辑。
- 修复说明：sync.md:95 删去「此时 repo-cleanup 会跳过 scratch 并提示先 transaction accept 或 transaction rollback，其它类别照常处理」，改为「事务记录在 Git common 目录，repo-cleanup 不读取也不清理」，保留控制面锁一句；recovery.md:64 删去「存在未结束文件事务或事务记录损坏时，repo-cleanup 跳过 .scratch/，其它类别照常清」，改为事务按 `transaction status` / `graph-transaction status` 处理（事务数据不在 .scratch），保留控制面锁一句。

### A2. specs 迁移重构为单一计划函数：缺字段按索引补齐，索引缺行沿用 front matter（问题 2、3 合并）

- 来源：review_intensive
- 位置：`repo/.repo_template/scripts/template_sync/specs_migration.py:46-118`；计划 WP3.2（:456）
- 优先级：HIGH
- 详细判断理由：已复核。根因是逐 spec 目标值（status/tasks/body）的推导在 `_validate_before_write` 与 `migrate_specs_layout` 各写一遍，且两处共同错误：已有 front matter 一律走严格 `load_spec`，缺 `status`/`tasks` 即停（问题 2，great_websites 32 份必中）；索引缺行默认 `[baseline]` 再与完整 front matter 比较，误报冲突（问题 3）。局部改两处比较是补丁，按铁律重构为「先算完整迁移计划、再整体写入」，校验即计划本身。计划 WP3.2 已规定补齐语义，实现对齐计划。
- 修复说明：
  - 新增纯函数 `_plan_specs(sync, index) -> list[SpecPlan]`（`SpecPlan` 含源路径、目标路径、slug、status、tasks、body、报告行），一次遍历 active 与 archive，所有校验与冲突检查在此完成，任一失败抛 SyncError、零写入；删除 `_validate_before_write`。
  - 已有 front matter 用宽松解析（`parse_front_matter_text`，不走 `load_spec`）逐字段合成：`slug` 存在则须等于文件名，否则停止；`status` 缺失补 `active`（archive 来源固定 `retired`），存在则须合法；`tasks` 缺失时取索引行，无索引行取 `[baseline]` 并报告人工确认；`tasks` 存在且有索引行时须一致，冲突停止；`tasks` 存在且无索引行时沿用并报告；其余字段（如 `tid`）列入报告后丢弃。
  - 无 front matter 的 spec 维持现规则（索引行或 `[baseline]`）。
  - `migrate_specs_layout` 只做：取索引 → `_plan_specs` → dry-run 返回报告 / write 按计划落盘、删索引、删空 archive → `load_specs` 终检。
  - 工厂测试覆盖：只含 slug、slug+tid、完整字段+有索引一致、完整字段+有索引冲突、完整字段+无索引行、slug 与文件名不一致六种形态；全部失败路径断言零写入。

### A3. ADR 迁移校验 NNN 唯一（问题 4）

- 来源：review_intensive
- 位置：`repo/.repo_template/scripts/template_sync/adr_migration.py:22-54`、`:111`
- 优先级：HIGH
- 详细判断理由：已复核。`by_nnn` 字典推导静默覆盖重复编号；计划 WP3.3 以「NNN 保证唯一」为前提，工具不校验，great_websites `044`、omni_panel `053` 会带重复完成迁移，`supersedes` 指向不确定。
- 修复说明：`_entries` 为每条记录起始行号，返回前检查 NNN 唯一，重复抛 SyncError「decisions.md 编号 NNN 重复（第 x、y 行）；拒绝迁移，请先改号」；`adr_migration_status` 经现有 except 自动报告。工厂测试：重复 NNN 时 status 报告、migrate 失败且零写入。

### A4. ADR 源文件语法统一：前言只许 H1 与正文，零条目即空文件退役（问题 5）

- 来源：review_intensive
- 位置：`repo/.repo_template/scripts/template_sync/adr_migration.py:22-54`、`:96-134`；`repo/.repo_template/docs/sync.md:57`
- 优先级：HIGH
- 详细判断理由：已复核。零条目抛错 → status 恒非空 → apply 永久阻断，`migrate adr` 同样失败，haiwa_bot 命中。只给零条目开特例是补丁；根因是 `decisions.md` 语法只定义了 H2 规则，前言区可出现什么没有定义，所以「空文件」无法与「H3 条目形态」（kkmail，计划要求停止）区分。按铁律把语法补全：围栏外任何 H2 必须是 `## NNN`（现有）；首条 `## NNN` 之前的前言区只允许 H1 与正文，出现 H3–H6 停止；条目内部 H3+ 属条目正文。在此语法下零条目是合法输入——只有前言，按计划「前言进报告、迁移后移除」处理即可，不再需要特例。
- 修复说明：`_entries` 删除零条目抛错；前言区遇围栏外 `^ {0,3}#{3,6}[ \t]` 抛 SyncError「第 N 行前言出现 H3+ 标题，ADR 条目须为 `## NNN 标题`」。`migrate_adr` 零条目时不建目标目录、只返回前言报告并删除 `decisions.md`；`adr_migration_status` 零条目时报告「decisions.md 无条目，迁移将移除（前言见报告）」。sync.md:57 adr 段补「前言只许 H1 与正文；零条目时只移除文件」。工厂测试：仅模板前言 → 文件删除、无 ADR 生成、报告含前言；前言含 H3 → 停止零写入。

### A5. install-hooks 退出码表达真实结果，bootstrap 以退出码为唯一判据（问题 8）

- 来源：review_intensive
- 位置：`repo/.repo_template/scripts/template_sync/hooks.py:193-211`；`repo/.repo_template/scripts/export_template.py:125-151`；`factory_tests/sync/test_repo_sync_hooks.py:123`；`repo/.repo_template/docs/usage.md:57`；`repo/README.md:18`
- 优先级：MEDIUM
- 详细判断理由：已复核。未安装返回 0 是静默降级；bootstrap 因此不得不另写 `_hooks_installed` 读 git config 二次判定，「是否安装」有两个真相源。按铁律修根因：命令退出码即结果契约，删掉二次判定。零兼容，不保留旧退出码语义。
- 修复说明：`cmd_install_hooks` 已有其它 hooksPath 且未 `--force` 时返回 `2`（`0` 已安装、`1` 错误、`2` 未安装需人工接入），脚本 docstring 写明三值契约。`bootstrap` 去掉 install-hooks 的 `check=True`，按返回码判定：`0` 已装、`2` 记未完成继续 init、其它抛错；删除 `_hooks_installed`。测试断言改 `== 2`；usage.md:57、README:18 写明退出码 2 含义。

### A6. 就绪审计 CBrowser ADR 条目与 HEAD 不符，按厂内纪律标注（问题 6）

- 来源：review_intensive
- 位置：`factory_docs/audits/consumer_readiness_20261110.md:68`
- 优先级：LOW
- 详细判断理由：已复核。`_entries:41-45` 对围栏外任意非 NNN H2 抛错（8f1d8cd），审计所述「不会主动拒绝」在 HEAD 不成立；`factory_docs/README.md:31` 要求在原文段落首行标注。阻断结论仍成立（迁移前须补编号），只是机制描述过期。
- 修复说明：:68 条目首行插入标注「8f1d8cd 后 `_entries` 对围栏外非 NNN H2 一律停止，本条行为描述已过期；阻断结论不变，迁移前须补编号。见 reviews/review_20261110_203124/adoption_decision.md A6」。若 A3/A4 落地，同文件 :81、:95、:149、:205-207 涉及 044/053 重复与 haiwa_bot 空文件的行为描述同步标注新行为（重复 → 停止；空文件 → 迁移移除）。

### A7. pilot 演练审计结论标注后续状态（问题 7，事实已修正）

- 来源：review_intensive
- 位置：`factory_docs/audits/pilot_agent_rank_20261110.md:10`、`:203`
- 优先级：LOW
- 详细判断理由：复核发现报告事实部分不准：审计 :180 起已有复跑段，记录 791dcbb 修复生效。但 :10 首段结论未标注；:203 复跑结论要求「迁移 → 逆序回滚 → 再次迁移」演练，已被 `decision_log.md` L71 否决；「旧 applied 事务前置处理」已在 sync.md:97 写明阻断与处置。两处现状判断与裁决相反，违反 README:31 纪律。
- 修复说明：:10 结论段首行标注「阻断已由 791dcbb 修复，见本文复跑段」；:203 复跑结论首行标注「回滚再迁移演练已被 decision_log L71 否决，证据边界为代码路径与工厂 rollback 用例；旧 applied 事务的前置阻断与处置见 sync.md」。

### A8. 删除 agents_merge 无调用方的兼容垫片（问题 9）

- 来源：review_intensive
- 位置：`repo/.repo_template/scripts/template_sync/agents_merge.py:99-115`；`factory_tests/sync/test_repo_sync*.py` 共 9 个文件导入行
- 优先级：LOW
- 详细判断理由：已复核。`_agents_parts`、`_replace_agents_force` 无生产调用，测试仅导入不调用；零兼容负担。
- 修复说明：删除两函数；9 个测试文件导入行去掉 `_replace_agents_force`。删除后全仓 grep 两符号为零。

### A9. 恢复 test_lock_errors_become_cleanup_errors 的 TaskDataError 覆盖（问题 10）

- 来源：review_intensive
- 位置：`repo/.repo_template/tests/test_repo_cleanup.py:527`；生产 `repo/.repo_template/scripts/repo_cleanup.py:563`
- 优先级：LOW
- 详细判断理由：已复核。用例只构造 `OSError("busy")`，`except (TaskDataError, OSError)` 的 TaskDataError 支（`git_common_dir` 失败的真实路径）无守护。
- 修复说明：用例改为对 `OSError("busy")` 与 `TaskDataError(...)` 参数化，TaskDataError 按该测试文件现有导入方式引入。

### A10. test_spikes.py 夹具删除已退役 specs_index 写入（问题 11）

- 来源：review_intensive
- 位置：`repo/.repo_template/tests/test_spikes.py:37-40`
- 优先级：LOW
- 详细判断理由：已复核。spike 相关脚本不读 `docs/specs_index.md`，夹具写入为残留，随产物分发会误导维护者。
- 修复说明：删除「准备 specs_index」注释及其后三行写入；跑 test_spikes.py 确认全绿。

### A11. ADR 文件名统一为单一语法（问题 12，用户裁定 D1 选 B）

- 来源：review_intensive
- 位置：`repo/.repo_template/scripts/template_sync/adr_migration.py:81-82`（`_filename`）、`:111-120`（`supersedes` 解析）；计划 `factory_docs/plans/plan_state_ownership_and_workflow.md` WP3.3；`repo/.repo_template/docs/sync.md:57`；`repo/AGENTS.md:33`（已是目标格式，不改）
- 优先级：MEDIUM
- 详细判断理由：报告建议给新建 ADR 补 NNN，方向错误：计划 :59 规定新建 ADR 用 `YYYYMMDD_<slug>.md` 是为并行链追加不撞号，且 `supersedes` 引用文件名而非 NNN。但「同目录两套命名语法」成立：迁移产物 `YYYYMMDD_NNN_<slug>.md` 与新建 `YYYYMMDD_<slug>.md` 并存，文件名中的 NNN 只是旧编号遗留语义。初版判不采纳，按架构铁律重开为 D1，用户裁定选 B：统一为单一语法，旧编号只保留在正文 H1。代价是旧文档「见 ADR 044」只能按 H1 检索（`grep -rl '^# 044 '`），不能按文件名 glob。
- 用户决策：B
- 修复说明：
  - `_filename` 改为 `YYYYMMDD_<slug>.md`，去掉 NNN 段；正文 H1 维持 `# NNN 标题`（现有行为）。
  - 同名冲突（同日期同 slug，含与目标目录已存在文件冲突）按新建 ADR 同规则追加 `_2`、`_3`…，按 NNN 升序确定性分配；分配在写入前完成，任何冲突无法分配时停止零写入。
  - `supersedes` 仍经 NNN（A3 保证唯一）解析到最终文件名。
  - 计划 WP3.3 迁移文件名条目、`supersedes` 示例 `YYYYMMDD_NNN_<slug>.md` 与「迁移 ADR 依赖 NNN 保证唯一」改为单一语法；该段首行按 README:31 标注被本决策发展。sync.md:57 adr 段写明文件名规则与 H1 保留旧编号。
  - 工厂测试：迁移产物文件名不含 NNN；同日期同 slug 两条得到 `_2` 后缀且顺序确定；`supersedes` 指向带后缀的正确文件。

## 不采纳项

### R1. repo_cleanup issue-pair 用例一次不可复现失败（问题 13）

- 来源：review_intensive
- 位置：`repo/.repo_template/tests/test_repo_cleanup.py` issue-pair 用例
- 优先级：LOW
- 详细判断理由：报告作者自述首跑失败发生在 512M tmpfs 残留大量临时仓、全量进程刚被终止后，未抓到断言原文。本机串行复跑 `test_repo_cleanup.py` 三次均 28 passed；无可定位缺陷，不立项改代码。落地采纳项时全量测试会再次覆盖该文件。
