# 模板同步与升级

`repo-template-sync` 的配置与恢复步骤。日常任务用法见 `usage.md`，执行恢复见 `recovery.md`。

## AGENTS.md 同步分区协议

`AGENTS.md` 用 `<!-- repo-template:begin -->` / `<!-- repo-template:end -->` 分两区：`begin` 之前的项目区（介绍与项目专属规则）原样保留；`begin`/`end` 之间的模板区（目录与读写规则、开发原则）由同步整块替换；`end` 之后不得有内容。缺少完整标记时 `status`/`plan` 列入迁移待办，`apply` 拒绝并提示 `repo_sync.py migrate agents-zones --write`；`apply` 不支持软链 `AGENTS.md`。消费仓项目专属规则写入项目区，不放模板区。迁移读取历史模板基线时，先取 `.repo_template/sync_manifest.json` 的 `resolved_commit`，缺失则回退 `sync_state.json` 的 `last_synced_commit`，再从来源仓用 `git show <commit>:repo/AGENTS.md`（兼容 `AGENTS.md`）读取；两者都缺失或读取失败即停止，不按当前模板或工作区内容猜测分区。

`.gitignore` 使用 `# repo-template:begin` / `# repo-template:end` 托管块：`apply` 整块替换块内模板规则，块外消费仓规则保留；缺托管块时在文件末尾补建，原规则不删除。

## 数据迁移

消费仓旧布局或旧 schema 升级须通过显式迁移命令完成；日常 `apply` 遇到旧布局/旧 schema 会直接阻断并拒绝写入。迁移从固定来源 product 运行**来源脚本**并指定 `--consumer`/`--source`/`--checkout` 固定 pin，默认 dry-run，`--write` 落盘：

`python3 <product>/.repo_template/scripts/repo_sync.py --consumer <消费仓路径> --source <来源身份> --checkout <product> migrate <类型> --write`

单次单项迁移在持久文件事务内执行，失败只回滚本项；`migrate all` 按 `workflow-schema → pending/findings → entity-storage → state-root → conventions → agents-zones → specs-layout → reviews → adr → retired-hooks` 固定顺序逐项提交，已完成步骤保留，失败仅阻断当前类型。`--write` 落盘，不修改 Git index。各类型要求见下文。

## entity-storage 一次性实体存储迁移

日常 `apply` 遇到旧实体存储布局时会先阻断，并提示从固定来源 product 运行**来源脚本**（带 `--consumer`/`--source`/`--checkout` 固定 pin）执行 `migrate entity-storage --write`。该一次性迁移把历史归档收敛至固定规范路径契约：

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

### 迁移维护窗口与规程

1. **准备与清点**：
    - 停止所有执行会话、hygiene、同步与集成；
    - 清点所有登记 worktree、本地分支、未完成 attempt、pending merge；先在旧工具链下完成或清理；
    - 确保主工作区与暂存区干净；备份 Git 对象、refs、Git common dir 状态及未跟踪附件。
2. **拟定计划（dry-run）**：
    - 执行 `python3 <product>/.repo_template/scripts/repo_sync.py --consumer <消费仓路径> --source <来源身份> --checkout <product> migrate entity-storage`；
    - 检查打印的实体映射清单与校验状态，确认无异常。
3. **正式执行落盘**：
    - 执行 `python3 <product>/.repo_template/scripts/repo_sync.py --consumer <消费仓路径> --source <来源身份> --checkout <product> migrate entity-storage --write`；
    - 迁移器在排他锁与持久文件事务 `FileTransaction` 内原子执行；单次迁移内任一步骤校验失败，整体回滚原状态（作用域限单次迁移内部；`migrate all` 按类型逐项提交，已完成步骤保留）。
4. **提交与恢复正常工作**：
    - 检查 `git status` 与 diff，提交迁移 commit；
    - 随后可正常执行 `repo_sync.py apply` 或日常开发流程。

## workflow schema 强制迁移

日常 `apply` 只接受当前 schema：检测到旧 task 布局、旧 conventions 写法、旧 specs_index 布局、AGENTS 无两区标记、模板已废弃 hook 残留、旧实体存储布局时阻断，并提示 `repo_sync.py migrate all`（或按类型单项迁移）。旧布局 state（`legacy-state`）由 `status`/`plan`/`apply`/`link-skills` 入口直接阻断，不在「迁移待办」中展示。`status` / `plan` 的「迁移待办」段落列出与 `apply` / `migrate` 完全相同的可处理集合（workflow-schema、conventions、agents-zones、specs-layout、reviews、adr、retired-hooks、entity-storage、state-root 等），并给出带 `--consumer`/`--source`/`--checkout` 固定 pin 的可直接执行迁移命令，避免 plan 显示一致但 apply 阻断。

显式迁移把主仓 `docs/tasks/` 下的 task 文档迁移为 `task.md`（需求规格）与 `progress.md`（进展），保留实施记录和 review 处置，更新模板规范块、旧 `status: blocked` 与轮次上限字段。最早布局 `spec.md`（规格）+ `task.md`（过程总账），中间布局 `task.md`（规格）+ `note.md`（过程总账），均一次迁移；`note.md` 与 `progress.md` 并存时整批拒绝迁移，不合并两份记录。迁移在持久文件事务内执行：单次单项迁移内失败回滚本项（作用域限单次迁移内部；`migrate all` 按 `workflow-schema → pending/findings → entity-storage → state-root → conventions → agents-zones → specs-layout → reviews → adr → retired-hooks` 等固定顺序逐项提交，已完成步骤保留），成功后校验目标 schema，不可机械转换的数据列出并中止。`apply` 门禁优先报告 `entity-storage` 旧布局；`migrate all` 中 `workflow-schema` 先行，`entity-storage` 后于 task/pending/finding 拆分，`state-root` 紧随其后；两类旧布局并存时按此顺序收敛。

`agents-zones`、`specs-layout`、`reviews`、`adr` 与 workflow-schema 一样在持久文件事务内执行，dry-run 零写入，`--write` 落盘且不修改 Git index。`specs-layout` 把 archive specs 迁回 `docs/specs`、写 front matter 并删除 `docs/specs_index.md`（清单改由 front matter 派生），写入前校验全部 slug、front matter 与 archive 同名冲突，任一失败不落盘。`reviews` 合并 archive 后以最近 5 个为上限：被 task/blueprint/handoff 引用或未跟踪的旧目录保留原样（含 `_meta/`）并报告；被删除的旧 review 整目录连同 `_meta/` 一起删除，dry-run 逐目录列出将删除的 `_meta/` 与未跟踪文件；已跟踪旧 review 内存在非 `_meta` 未跟踪内容时，写入前停止且不删除任何目录。`adr` 把 `decisions.md` 拆成一条一文件，按围栏外 `## NNN` 条目拆分，代码块内示例不算条目；围栏外出现非 `## NNN` 的 H2 一律停止并报告；纯文本前言不生成 ADR，只进迁移报告，迁移后随 `decisions.md` 从仓库移除，请据报告复核。

state-root 迁移把 `docs/runtime/`、`.repo_template/sync_state.json`、`.scratch` 事务目录与旧状态行迁入 `$GIT_COMMON_DIR/repo_template/`；清理 `docs/runtime/dispatch_ledger.lock`，旧状态行从 `.scratch/statusline_workdir.jsonl` 或 `docs/runtime/statusline_workdir.jsonl` 择一迁移，两处并存或 `docs/runtime` 存在未知残留时停止；要求无登记 worktree，在 entity-storage 之后执行。复制或恢复失败按项回滚：清理残缺目标、恢复源文件并保留备份；恢复未完成时报告人工处理。

自定义 `test_cmd` 的消费仓须先把 `verification_policy` 改为 `project_configured` 并填写 `verification_notes`。

存在已登记 task worktree 时拒绝迁移，先完成或 rewind；存在状态为 done 但尚未合入默认分支的 task 分支时同样拒绝，先 integrate。迁移后工具链只接受当前 schema。

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

旧布局 state（`.agents/skills/repo-template-sync/sync_state.json`）不参与运行期回退：只有旧文件时 `init` / `apply` / `status` / `plan` / `link-skills` 均明确拒绝并指向来源 product 的 `repo_sync.py migrate legacy-state --write`（带 `--consumer`/`--source`/`--checkout` 固定 pin；旧消费仓本地脚本缺 migrate 子命令，不得用本地路径）。该显式迁移保留来源、同步记录与 user_prompts，完成后删除旧文件；日常命令只读 `$GIT_COMMON_DIR/repo_template/sync_state.json`。旧格式 `docs/pending.md` / `docs/findings.md` 单文件总账同理：运行期 fail-closed 不解析正文编号，须先用完整命令显式拆分（来源仓脚本，默认 dry-run、`--write` 落盘）：`repo_sync.py migrate pending-ledger --write` 与 `repo_sync.py migrate findings-ledger --write`。执行前备份，执行后核对拆分条目编号与正文、提交迁移 commit；目标实体验收通过后写入 `.repo_template/entity_schema.json`，只有当前协议来源参与取号。历史来源保留但不参与分配；合并入口拒绝未完成协议切换的执行分支，并检查最终编号唯一性；安装模板 hooks 后，原生 merge 的自动提交与手工继续提交也会核对 MERGE_HEAD 来源。标记不能替代迁移或数据校验。

## 正式同步

正式同步要求 clean 来源，先记录来源 SHA，再从固定 commit 的本地 checkout 运行来源脚本；工厂根与产物根均可。URL 先 clone/fetch 到 `.scratch/`，解析并固定 commit；离线使用已有固定 `--checkout`，同时保留原始 URL 的 `--source` 身份，不执行 pull。dirty 来源仅显式开发验证，不推进 resolved commit，不可冒充正式升级。

## 同步准备与 apply 事务

同步准备仅改 `.scratch/` 来源缓存；同步器不自覆盖，也不执行 prep。status/plan 直接运行来源脚本并指定 `--consumer`、canonical `--source` 和固定 `--checkout`；apply 携 `--expected-commit` 核对 plan 的完整 SHA，从该 commit 的 Git archive 快照写入，才开始消费资产事务。文件、入口、schema 和 state 同一基线；内置 contract、项目验证策略校验或写盘失败时尝试自动回滚，备份保留；回滚也可能失败，须核对事务状态与实际 diff，未完整恢复时停止提交并保留现场。进程中断先 `transaction status`，保存中断后新增改动，再 `transaction rollback --confirm-restore`。成功 apply 后仍可 rollback；现场有后续改动时拒绝静默覆盖。批准并提交同步后 `transaction accept`，再开始下一轮。

apply 成功到 `transaction accept` 之前，`.scratch/` 保留 applied 事务；事务记录在 Git common 目录，`repo-cleanup` 不读取也不清理。同步命令或其它控制面命令持有控制面锁时，带 scratch 的 cleanup 立即失败、零删除。

旧版 `.scratch/repo_sync_rollback` 记录为 preparing/applying/applied/restore_failed 或损坏时，迁移与 `transaction status` 都会阻断并提示先用升级前消费仓脚本 `transaction accept`/`transaction rollback` 处理；新 transaction 只读 Git common 目录，不自动接管或清理旧事务。

同步只执行模板工具链契约测试，不执行消费仓 doctor/test/blackbox。apply 始终校验 `testing.md` 存在性、业务策略配置和命令格式；`--skip-tests` 只跳过模板契约测试，策略非法仍失败并回滚。跳过的契约测试必须在 accept 前补验。

`transaction accept` 每次都重读并校验当前 `testing.md`，即使 apply 已验证、受管资产指纹未变，也不能复用旧策略校验结果。模板契约测试仍只在 apply 未验证或受管资产指纹变化时复跑；任一校验失败均保持 applied。`testing.md` 不纳入模板事务资产，避免回滚覆盖消费仓自有配置。

## 降级与 revert

成功同步的版本与消费配置都在独立同步 commit。降级优先 `git revert <同步commit>`（先保证无 active worktree、备份本机 state），随后核对 manifest 并恢复对应 state 基线、重跑项目门禁；指定版本升级则选择该 clean commit 重新同步，不手改工具链文件。以上操作均不自动 push。

## 同步验证锁

同步验证保持事务排他锁。同线程锁可重入；验证子进程不得写同一 Git common dir 的控制面，违规立即失败并回滚，不等待父进程释放锁。需要测试 task CLI 写操作的工具链测试应创建隔离 Git 仓库；普通只读 task 命令与隔离仓库不受此限制。

## .prettierignore 机械追加

`repo_sync.py apply` 机械追加到消费仓 `.prettierignore`（消费独有规则保留，去重），`status` / `plan` 展示缺失项；豁免路径清单见 `usage.md`「命名与格式」。同步改写消费仓自有 JSON（`.claude/settings.json`、MCP）时沿用原缩进，不弄红门禁。模板不分发 `.github/workflows/repo-template-ci.yml`；残留时 `status` / `plan` / `apply` 警告，确认无消费定制后手动删除。

## 实体协议切换验收

`migrate all` 依次处理旧 state、workflow、pending/findings、entity-storage、state-root、conventions、agents-zones、specs-layout、reviews、adr、retired-hooks。每项拥有独立文件事务；失败保留之前已完成步骤，只恢复当前项，不宣称全流程原子性。单文件总账转换不修改 Git index。

entity-storage 先验证源身份与冲突，再转换实体，使用正常运行时模型验证全部目标数据，最后写 schema 标记并接受事务。迁移不复验历史实现血缘；handoff 字节保留，历史 done 被用作依赖时才查证实现来源。缺 progress.md 应先转换 workflow，不接受“搬完旧目录即成功”。

切换前停止所有会话并验证备份可恢复。旧分支保留作历史；新执行从当前协议主干或已完成前驱创建，不能仅补标记让旧分支参与。已有新协议业务写入时禁止仅降级脚本或直接反搬目录；按 recovery 保留现场并前向修复。迁移前须在隔离副本演练并核对备份可恢复；模板发布验证不替代目标仓演练。
