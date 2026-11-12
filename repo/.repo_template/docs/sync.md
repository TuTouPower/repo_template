# 模板同步与升级

`repo-template-sync` 的配置与恢复步骤。日常任务用法见 `usage.md`，执行恢复见 `recovery.md`。

## AGENTS.md 同步分区协议

`AGENTS.md` 用 `<!-- repo-template:begin -->` / `<!-- repo-template:end -->` 分两区：`begin` 之前的项目区（介绍与项目专属规则）原样保留；`begin`/`end` 之间的模板区（目录与读写规则、开发原则）由同步整块替换；`end` 之后不得有内容。缺少完整标记时 `status`/`plan` 列入迁移待办，`apply` 在统一事务内转换；`apply` 不支持软链 `AGENTS.md`。消费仓项目专属规则写入项目区，不放模板区。迁移读取历史模板基线时，先取 `.repo_template/sync_manifest.json` 的 `resolved_commit`，缺失则回退 `sync_state.json` 的 `last_synced_commit`，再从来源仓用 `git show <commit>:repo/AGENTS.md`（兼容 `AGENTS.md`）读取；两者都缺失或读取失败即停止，不按当前模板或工作区内容猜测分区。

`.gitignore` 使用 `# repo-template:begin` / `# repo-template:end` 托管块：`apply` 整块替换块内模板规则，块外消费仓规则保留；缺托管块时在文件末尾补建，原规则不删除。

## apply 内置迁移

`apply` 是同步与迁移唯一写入口。从固定来源 product 运行来源脚本，指定 `--consumer`、`--source`、`--checkout`；先 `status` / `plan` 只读展示迁移明细、删除清单与数据阻断项，再运行：

```bash
python3 "$product/.repo_template/scripts/repo_sync.py" --consumer "$consumer" --source "$source" --checkout "$checkout" apply --expected-commit "$commit"
```

一次同步只开一个 `FileTransaction`，覆盖全部待迁移路径、模板同步路径与 common 状态文件。顺序固定为 `legacy-state → workflow-schema → pending-ledger → findings-ledger → entity-storage → state-root → conventions → agents-zones → specs-layout → reviews → adr → retired-hooks`，随后同步模板、格式化本次变更 Markdown、校验迁移收敛、spec 与实体契约、项目验证策略和模板契约测试。任一步失败整体回滚，不保留已完成迁移。

开始前工作区与暂存区必须完全干净，未跟踪文件也须处理，忽略文件除外；来源 clean 且 SHA 与 plan 一致，无登记 worktree、无未结束同步或图事务，md_kx 可用。数据阻断项只报告与给出修复方案；用户批准后修复，修复单独批准并提交，再同步。迁移不从旧 state 推断来源，始终使用本轮固定来源。

## entity-storage 一次性实体存储迁移

`apply` 在同一事务内将历史归档收敛至固定规范路径：

- `docs/archive/tasks/tNNN_slug/` -> `docs/tasks/tNNN_slug/`（恢复原位流转，校验状态字段为 done/dropped；迁移不复验 handoff/实现血缘，使用时由业务门禁核验）；
- `docs/pending/todo/pNNN.md` -> `docs/pending/pNNN.md`（status: todo；声明 pid/slug 与文件名不符即阻断，不静默改写）；
- `docs/pending/parked/pNNN.md` -> `docs/pending/pNNN.md`（status: parked，保留 reason）；
- `docs/archive/pending/pNNN.md` -> `docs/pending/pNNN.md`（status: closed，保留 resolution 与 reason）；
- `docs/archive/spikes/sNNN/` -> `docs/spikes/sNNN/`（status: completed，校验报告与 findings 闭环证据）；
- `docs/archive/tasks_audit.log` -> `docs/runtime/tasks_audit.log`（无损保持原始字节，state-root 再迁入 Git common dir）；
- `docs/archive/handoff.md` -> 内容已完整入库 Git 历史后移除；
- 删除已废弃派生索引 `docs/tasks_index.json` / `docs/archive/tasks_index.json`（旧仓已跟踪文件不受 `.gitignore` 影响）；
- 清理遗留空旧目录（含仅剩 `.gitkeep` / `.DS_Store` 占位文件的旧目录亦列为待清理并移除占位文件），并更新维护文档中的确定引用。

迁移同时转换仅有旧 active spike 的仓库；归档与当前 task 均校验 tid/slug 与目录一致、全状态编号唯一。

旧目录 `docs/archive/tasks`、`docs/archive/spikes`、`docs/archive/pending`、`docs/pending/todo`、`docs/pending/parked` 中，顶层非规范实体项均在本次事务内删除，不作为数据阻断项。规范实体仅指 tasks/spikes 中名称匹配当前规范的真实目录，或 pending 中匹配 `pNNN_slug.md` 的普通文件。其余文件、目录、软链、隐藏项与命名不合规的形似实体项均删除；每项单列路径与类型，目录附内部全部文件路径，必须报告给用户。规范实体内部 front matter 非法、编号冲突等仍阻断。拒绝同步时整体回滚，恢复删除项原始字节与软链。

## 其它内置迁移

workflow 将 `spec.md` + `task.md` 或 `task.md` + `note.md` 转为 `task.md` + `progress.md`，保留实施记录与 review 处置，更新模板规范块、旧 `status: blocked` 与轮次上限字段。`note.md` 与 `progress.md` 并存时阻断，不猜测合并。预检按转换后的 progress 验证实体，避免依赖顺序误报。

`agents-zones` 按历史模板基线拆分 AGENTS；`specs-layout` 把 archive specs 迁回 `docs/specs`、补 front matter 并删除 `docs/specs_index.md`，预检全部 slug、front matter 与目标冲突；`reviews` 合并 archive 后只保留最近 5 个，超额目录一律删除（含 `_meta/`、被引用或忽略附件），在 plan 与执行报告逐目录展示。

`adr` 按围栏外 `## NNN 标题` 将 `decisions.md` 拆为一条一文件；重复编号、无编号 H2、正文 H1 等非法数据阻断。纯文本前言不生成 ADR，只进入报告，源文件随迁移移除。文件名为 `YYYYMMDD_<slug>.md`，同名追加 `_2`、`_3`；旧编号只保留正文 H1。

state-root 在 entity-storage 之后，将 `docs/runtime/` 已识别状态、`.repo_template/sync_state.json` 与旧状态行迁入 `$GIT_COMMON_DIR/repo_template/`；清理旧 ledger lock 与已结束 `.scratch` 同步/图事务。状态行两处并存、runtime 未知残留、common 目标冲突均阻断。旧事务未结束或损坏时保留原备份，须先用对应旧版本恢复，当前恢复入口不接管旧 journal。

存在状态为 done 但未合入默认分支的 task 分支时，先在旧工具链 integrate；迁移后只接受当前 schema。自定义 `test_cmd` 须先选定 `verification_policy: project_configured` 并填写 `verification_notes`。

## testing.md 单块门禁迁移

旧版允许同一门禁章节包含多个 bash/sh 代码块；新版要求 `doctor_cmd`、`test_cmd`、`blackbox_verify` 各自最多一个可执行块。此限制有意拒绝歧义，避免把示例或占位命令当作门禁。`docs/blueprint/testing.md` 属消费仓自有文件，同步不会覆盖或自动合并，必须在升级前人工收敛：

1. 在消费仓根保存现有 diff，并备份 `docs/blueprint/testing.md`。

2. 逐节核对真实门禁，把确实需要执行的命令按原顺序合入一个 bash/sh 块；示例和占位说明改为 `text` 围栏或正文。不适用的门禁整节正文写「无」，理由放入 `verification_notes`。保留已配置的项目策略。

3. 在消费仓根用固定模板源校验。下列 `product` 为已核实、固定来源 SHA 的产物根绝对路径；工厂来源为 `<checkout>/repo`，产物来源为 `<checkout>`。命令只解析当前消费仓策略，不运行项目门禁；退出码 0 才可继续同步，非零则先修正文档。

    ```bash
    python3 -c 'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); from repo_task.validation import load_policy; load_policy(Path.cwd())' "$product/.repo_template/scripts"
    ```

4. 校验通过后再按正式流程执行 apply。若升级已因策略错误失败，先核对 `transaction status` 与实际 diff；存在未恢复事务时按「同步准备与 apply 事务」恢复流程处理，再修正文档并重跑。`--skip-tests` 不跳过策略校验，不能用于绕过此迁移。

## 同步版本基线

同步版本的可移植基线为 `.repo_template/sync_manifest.json`（随同步 commit 入库），只保存 resolved commit 和可共享 URL；共享保护决策通过 `prompt add --scope shared` 写入可入库的 `.repo_template/sync_policy.json`；本机来源路径、缓存与私人 user_prompts 留在忽略的 sync_state 中，迁机须备份。新 clone 无 state 时读取 manifest，路径源用 `init --source <新机器路径>` 重新接入，保留版本基线；私人保护策略从备份恢复后再同步。不要把含凭据 URL 或本机路径写进 manifest。

旧布局 state（`.agents/skills/repo-template-sync/sync_state.json`）不参与运行期回退。固定来源 `status` / `plan` 展示待办，`apply` 保留来源记录与 user_prompts 后删除旧文件；`init` / `link-skills` 仍要求先完成同步。旧 `docs/pending.md` / `docs/findings.md` 在同一事务内拆分，运行期编号扫描不解析旧正文。拆分后核对编号与正文，通过目标实体验收才写入 `.repo_template/entity_schema.json`。只有当前协议来源参与取号；合并及 hooks 检查来源协议与最终编号唯一性，schema 标记不能代替数据校验。

## 正式同步

正式同步要求 clean 来源，先记录来源 SHA，再从固定 commit 的本地 checkout 运行来源脚本；工厂根与产物根均可。URL 先 clone/fetch 到 `.scratch/`，解析并固定 commit；离线使用已有固定 `--checkout`，同时保留原始 URL 的 `--source` 身份，不执行 pull。dirty 来源仅显式开发验证，不推进 resolved commit，不可冒充正式升级。

## 同步准备与 apply 事务

status/plan 直接运行固定来源脚本，不提前覆盖本地同步器。apply 从固定 commit 的 Git archive 快照读取，来源、文件、入口、schema 与 state 共享同一版本。同步脚本不 commit，也不自行 accept；成功后事务保持 applied，保留完整未提交 diff 待用户审阅。

agent 报告来源完整 SHA、全部变更路径、迁移报告、校验结果及 state/manifest；删除旧归档非实体项必须逐个单列，目录附内部文件，不能并入汇总。每次同步须用户单独明确批准 commit，不复用批量授权。批准后 `git add -A`，单个 commit 包含迁移与同步全部结果；hooks 正常运行，禁止 `--no-verify`。pre-commit 只读检查 Markdown，格式化已在 apply 审阅前完成，审批 diff 与最终提交内容一致。提交后立即 `transaction accept`；要求事务路径已提交、父提交等于同步前 HEAD，文件与 common 状态指纹不变。同一来源重复 apply 若工作树相对 HEAD 无交付差异（common state 的 `last_synced_at` 变化不算），脚本恢复本轮 common state 写入并把事务收尾为 `no_op` 终态，打印「无变更，事务已收尾」并返回 0，不产生 commit、不要求 accept，下一轮同步不被阻断。

**回滚窗口仅限提交前。** 用户拒绝时 `transaction rollback` 整体恢复工作区、index 与 common 状态；applied 后现场发生变化，须先备份新增改动，再 `transaction rollback --confirm-restore`。已产生同步 commit 后禁止文件 rollback，只能按下节 revert；common 状态不会随 revert 恢复，须明确告知用户并人工核对。提交后 accept 失败或用户放弃、已 revert 同步 commit 并人工从备份恢复 common 状态到基线后，用 `transaction cancel --confirm-post-commit` 把 applied 事务收尾为 `cancelled` 终态：只读核验事务路径工作区干净、受管路径在 HEAD 与 `base_head` 内容一致、common 状态等于备份基线，并记录同步 commit 与时间；`--confirm-post-commit` 必填。cancel 不覆盖文件、不删 journal、不放宽指纹、不自动接受改写结果。`cancelled` 与 `no_op` 均为已结束终态，不阻断下一轮同步。

文件事务记录与备份在 `$GIT_COMMON_DIR/repo_template/sync_transaction/`，图事务在同级 `graph_transaction/`。未结束或损坏事务阻断新同步；进程中断先 `transaction status`，备份新增改动后恢复。恢复失败标记 `restore_failed`、保留备份并报告人工恢复条件，不能据退出码宣称完整恢复。accept 后备份可在下轮同步清除；repo-cleanup 不读取或清理这些记录。图事务由 `task.py graph-transaction status` / `rollback` 管理。

同步只运行模板契约测试，不执行消费仓 doctor/test/blackbox；始终校验项目 testing.md 存在性、策略配置与命令格式。`--skip-tests` 仅供开发验证，不跳过数据、策略和 Markdown 检查；accept 前须补验模板契约。accept 始终重读当前项目策略，未验证时补跑契约；指纹变化直接拒绝接受。`testing.md` 不属于模板事务资产，避免恢复覆盖项目配置。

## 降级与 revert

成功同步的版本与消费配置都在独立同步 commit。降级优先 `git revert <同步commit>`（先保证无 active worktree、备份本机 state），common 状态不会随 git revert 恢复，须人工核对 sync_state、ledger 等并从备份恢复对应基线、重跑项目门禁；指定版本升级则选择该 clean commit 重新同步，不手改工具链文件。以上操作均不自动 push。

## 同步验证锁

同步验证保持事务排他锁。同线程锁可重入；验证子进程不得写同一 Git common dir 的控制面，违规立即失败并回滚，不等待父进程释放锁。需要测试 task CLI 写操作的工具链测试应创建隔离 Git 仓库；普通只读 task 命令与隔离仓库不受此限制。

## .prettierignore 机械追加

`repo_sync.py apply` 机械追加到消费仓 `.prettierignore`（消费独有规则保留，去重），`status` / `plan` 展示缺失项；豁免路径以根锚定 `/.repo_template/` 整目录为边界，清单见 `usage.md`「命名与格式」。apply 在提交前用 git 的真实 gitignore 引擎校验模板自有 JSON 全部被有效忽略（保留规则顺序、否定、目录与根锚定语义，不复刻语法）：必需规则缺失、被消费仓否定规则抵消、或根锚定失效导致嵌套同名目录被误豁免，均整体回滚（此时尚未提交，可回滚）。模板自有资产不受消费仓 prettier 重排，审批 diff 与提交内容一致。

**边界**：上述校验只覆盖 `.repo_template/` 内模板自有资产。目录外的同步写入（`.mcp.json`、`.cursor/mcp.json`、`.vscode/mcp.json`、`.claude/settings.json`、Markdown）仍受消费仓自身 formatter 管理；模板沿用消费仓原缩进，但不宣称其字节与消费仓 formatter 输出逐字节一致。若消费仓 commit hook 会改写这些文件，提交内容仍可能偏离 apply 指纹，`transaction accept` 会拒绝。根治需产品裁决：允许同步在事务内执行消费仓 formatter 做格式化与只读复验，或要求消费仓把提交 hook 改为只读检查、自行把这些路径纳入 `.prettierignore`。模板不分发 `.github/workflows/repo-template-ci.yml`；残留时 `status` / `plan` / `apply` 警告，确认无消费定制后手动删除。

## 实体协议切换验收

apply 先验证源身份与冲突，再按固定顺序转换并用运行期模型验收全部目标实体，最后校验整轮结果。迁移不复验历史实现血缘，handoff 字节保留；历史 done 用作依赖时才核验实现来源。全部迁移与同步共享单事务，提交前整体恢复。

切换前停止所有会话并验证备份可恢复。旧分支保留作历史；新执行从当前协议主干或已完成前驱创建，不能仅补标记让旧分支参与。已有新协议业务写入时禁止仅降级脚本或直接反搬目录；按 recovery 保留现场并前向修复。迁移前须在隔离副本演练并核对备份可恢复；模板发布验证不替代目标仓演练。
