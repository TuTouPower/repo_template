# 本路模型标识

Muse Spark (muse-spark-1.3-contributor)

# 审阅范围

合并审阅 `647d70133b63683bf0ae0ccc4a53a4db498c4178..HEAD`（`HEAD=d3dd4e0`），不按 commit 拆分。

`git diff --stat`：225 文件，+8222/-24597。核心是三件事：

1. `repo_sync.py` 拆分为 `template_sync/` 包（约 -2350 行搬移 + 后续迁移特性）；
2. 任务生命周期语义变更：归档从 `finish` 移到 `integrate`（D1）、证据 commit 取代 `amend`（D2）、`SHA` 查找新旧历史（D3）、`goal-check` 按关系分链（E1/E2）、`plan --serial` 分链、`start` 超时保留未确认资源、`PROGRESS_FILE` 收敛为 `progress.md`；
3. 测试重组：`repo/.repo_template/tests/` 部分移入工厂根 `factory_tests/`，`pytest.ini`、门禁、CI 随之变更；文档技能同步更新。

审阅方法：全量 `diff --stat` + 四个维度并行深审（同步重构 / 任务生命周期 / 文档技能 / 测试基建），高风险结论已回读源码复核（`monitoring.py`、`lifecycle.py`、`agents_merge.py`、`status_plan.py`、`store.py`、`documents.py`、`locks.py`、`task-run/SKILL.md`）。

# 总体结论

拆分行为等价，归档/证据/分链方向正确，无数据丢失主路径。但合并后存在 2 个 Blocker、11 个 Major，其余 Minor/Nit 见下。文档技能与脚本行为多处分裂，需统一术语（实现 commit / 证据 commit / 执行回执）。

# Blocker

## B1 证据 commit 可改 `progress.md` front matter，降级 `review_level`

- 位置：`repo/.repo_template/scripts/repo_task/monitoring.py:54-65` + `92-112`，`review.py:381`，`monitoring.py:465,538-552`
- 现象：`EVIDENCE_COMMIT_FILES` 含 `progress.md`；`execution_commits` 只验路径。`verify_integrate_ready` 从分支 tip 读 `fm`，`evaluate_review` 按 tip `review_level` 选报告集。`full->single` 后只需 `general` 即可 PASS，指纹跟 tip 走，无法检出。
- 修复：证据 commit 禁 `progress.md` front matter 变更，或至少禁 `review_level/diff_anchor/branch/status/tid`；`verify` 比对实现 commit 与 tip 的 front matter。

## B2 `plan` 在旧格式 AGENTS 下直接崩溃

- 位置：`repo/.repo_template/scripts/template_sync/status_plan.py:136,154`，根因 `agents_merge.py:29-30`
- 现象：`cmd_plan` 两次无保护调 `agents_section_status`，缺标题即抛 `SyncError`。`sync.md:15` 承诺旧模板沿用整文件裁定，`status` 不调此函数故正常，`plan` 直接 exit 1。
- 修复：`cmd_plan` 内 `try/except SyncError -> None`，走现有“未识别完整标题协议”分支。

# Major

## M1 `task-run:36` 同链后继 `--base` 写错

- 位置：`repo/.repo_template/skills/task-run/SKILL.md:36`
- 现象：“刚完成的前一成员分支（即其 `base.branch`）”。前一成员的 `base.branch` 是其祖先，不是它自身。`architecture.md:7` 表述正确，`task-run:33` 取值正确。
- 修复：删括号，改“即刚完成的前一成员分支名，`plan` 输出的该成员对应 `base.branch` 即此值”。

## M2 `--message-file` 在 task 目录内会被静默提交

- 位置：`repo/.repo_template/scripts/repo_task/lifecycle.py:677,704-710,810`
- 现象：`_unstaged_outside` 豁免整个 `task_rel`，`git add -A -- task_rel` 会把目录内 message file 一并提交。文案只说“放在仓外或 `.scratch/`”无校验。
- 修复：`_finish_prechecks` 拒绝 `message_path` 落在 `task_rel` 内（`is_relative_to`）。

## M3 软链 `AGENTS.md` 永远无法 `apply`

- 位置：`template_sync/apply.py:124-126` vs `file_transaction.py:78`、`agents_merge.py:57,78,86`
- 现象：事务路径含 `AGENTS.md`，遇软链资产根抛 `ValueError` 转 `SyncError`。解引用分支在 `apply` 路径不可达，`status/plan` 却显示支持。
- 修复：`apply` 前对软链 AGENTS 友好拒绝并指引解链，或快照跟随解引用；`sync.md` 补一句。

## M4 `start` 依赖只验 `status==done`，未调 `verify_integrate_ready`

- 位置：`repo/.repo_template/scripts/repo_task/integration.py:171-178,238-248`
- 现象：未过 review/坏 handoff 的 done 分支仍可作 `--base`，下游继承坏代码，`integrate-chain` 才拦。
- 修复：对未合入分支调 `verify_integrate_ready` 或至少验 `handoff base_sha` 可解析。

## M5 review prompt 示例位置违反机器门禁

- 位置：`docs/review_prompts/code_prompt.txt:167-175`（`test/general` 同式），`share_prompt.txt:50`
- 现象：`### AC 复验方式` 示例写在报告模板代码块内；`share:50` 明确行不得写进代码块/引用块/表格。reviewer 照抄必判 INCOMPLETE。
- 修复：示例移出代码块，或加粗注“仅示格式，落报告须为普通 `-` 列表行”。

## M6 证据允许集三处枚举不一致

- 位置：`architecture:64`、`recovery:32`、`usage:90`
- 现象：对“只改过程文件”范围各说各话（`task.md`/`handoff.json`/其它路径取舍不一）。
- 修复：三处统一为“仅当前 task 目录内 `progress.md`、`review_*.md`、渲染 prompt；禁 `task.md`、`handoff.json`、交付文件、其它 task、其它路径”，注明与指纹排除清单同源。

## M7 老定制仓 `contract` 门禁由 skip 变 fail，无迁移说明

- 位置：`repo/.repo_template/tests/test_contract_suite.py:86`
- 现象：跳过条件由 `sync_state.json` 存在改为 `verification_policy==project_configured`。老仓改了 `test_cmd` 未写 policy 即 fail。
- 修复：`sync.md` 加迁移节或 `workflow_migration` 自动置 policy。

## M8 CI 删除无替代

- 位置：`d43deba` 删 `.github/workflows/template_factory.yml`
- 现象：contract+factory_full（3.9/3.14、node22、`-n auto --basetemp`）全删，`AGENTS.md` 仅剩手跑。机械门禁回到纪律。
- 修复：恢复最小工厂 CI，或明示手跑为唯一门禁。

## M9 “执行 commit”残留与“实现 commit+证据 commit”分裂

- 位置：`task-run:21,34`、`task-work:3,12,47`、`task-create:21`、`usage.md:12` vs `AGENTS:34`、`architecture:7,64`、`recovery:24,32,44`、`task-integrate:22`
- 修复：全仓统一“实现 commit（含 `handoff.json`）+证据 commit”。

## M10 `usage:22`、`architecture:14` 写权仍写“`start` 在主仓默认分支执行”

- 现象：主路径已是 `run-begin`（内含 start/reserve/claim）。
- 修复：改“`run-begin` 在主仓默认分支执行”。

## M11 skill 传参契约与主路径矛盾

- 位置：`usage.md:84`、`task-work:16`
- 现象：主路径只传 `tid`+`owner_id`（`task-run:29`），`usage:84` 仍写原样传递 `attempt/execution_id`，`task-work:16` 的 `identity` 未定义。
- 修复：改“主路径只传 `tid`+`owner_id`；`attempt/execution_id` 仅 recovery 显式 exact identity 时传”。

# Minor

- `store.py:157-162` 按 `dict` 相等分组非按 `dir` 前缀。改 `startswith("docs/archive/tasks/")`。
- `documents.py:72` 通用解析错误硬编码 `progress.md 必须…`。改通用文案。
- `lifecycle.py:783` `cmd_finish` 缺 `@control_locked`：经核影响小（worktree 独立 index，done 不写共享索引），建议加锁或注释说明。
- `view_server.py:36,108` + `board.html:128` `doc=note` 直接 404。保留 `note` 别名或注明破坏性。
- `goal.py:315-351` 分链按当前主干重算，非冻结时图。快照存边或注明以当前主干为准。
- `plan.py:569-648` `chains` 恒单链全量，真正分组在 `queue_chains/members`。明确权威为 `members[].base`。
- `shared_files.py:131` 坏端口 MCP URL 抛裸 `ValueError`；`22-47` `!` 否定行参与拦截；`79-82` 旧头尾空格版 `.prettierignore` 永不迁移。
- `workflow_migration.py:40,66,135` 围栏跟踪缺失，混合围栏误切换、代码示例节误触发。
- `cli.py:20` 重复 `--decision` 静默后胜，非法 disposition 仅打印跳过 exit 0。至少 WARNING。
- `state.py:29` `--source` 覆盖被 `prompt` 持久化。剥离或文档明确。
- `status_plan.py:83 vs 188` `status` 含 unmanaged，`plan` 藏 unmanaged。统一口径。
- `hooks.py:77` `install-hooks --statusline` 与 `apply` 并发写 `.claude/settings.json` 竞态。加 `consumer_locked`。
- `task-integrate:49` “保留在 `docs/tasks/` 原位”与先 `git mv` 到 archive 矛盾。改“在 `git status` 显示位置就地解决”。
- `task-work:47` “task 目录由 `finish` 自行处理”不明。明确 agent 只 `add` 交付文件。
- `skills_links.py:285` `plan` 软链表只查 `.claude/skills`，漏 `.agents/skills`。
- `track_worktree.py:166` 子串匹配；`test_task_start_flow:1060` `builtins.input`、`test_repo_state:187` `chdir`、全局 `Path.read_text/open` 补丁，xdist 下暂安全，改模块替身。
- `test_design_regressions:459` `"1 skipped"` 脆弱；`task_flow_support.py:60` 路径可预测；跨文件 import 依赖隐式 sys.path。
- `test_evidence_repair.py` 删除无覆盖缺口，但旧 `reserve/rewind` 文案无等价断言。补“追加证据 commit、禁 rewind/reserve、不含 amend”断言。
- 文档：`task-run:33` 缺宾语；`progress.md:47-49` 占位符即禁用值未标注必替换；`sync.md:21` 两代 `task.md` 同名易误读；`share_prompt:117` 引 `.agents/skills/` 改 canonical；中英空格全仓扫一次；“整链/队列”统一为队列。

# Nit

- `template_sync/context.py:42` 死常量 `AGENTS_INTRO_HEADING`；`assets.py:90` `sync_skill` 产品零调用；`shared_files.py:267` 重复调 `_apply_agents_sectioned`；`workflow_migration.py:229` 正则与 `repo_task/context.py:32` 重复；`template_sync/__init__.py:1` 英文 docstring；`AGENTS:11` vs `testing.md:5` 括号体例。

# 已核验无问题

- 拆分等价（apply 事务/dirty/expected-commit/manifest 脱敏/`--skip-tests` 语义一致）；双名冲突拒绝先于写盘、旧进展迁移、未集成 done 拒绝与 `status/apply` 同口径。
- 归档双存拒缺源报、`--continue` 幂等；`start` 超时残留保留、`rollback_start` 仅删 `HEAD==base`；`finish` 零写入预检→写→提交失败回滚。
- `note.md` 仅 `sync.md:21` 残留（迁移语义必需）；`amend` 产品零残留；归档职责/分链契约三文档一致。
