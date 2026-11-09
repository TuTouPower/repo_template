# 模板同步与升级

`repo-template-sync` 的配置与恢复步骤。日常任务用法见 `usage.md`，执行恢复见 `recovery.md`。

## AGENTS.md 同步分区协议

消费仓根 `AGENTS.md` 按固定标题分为三类内容：

|区段|同步行为|消费仓可定制范围|
|---|---|---|
|项目介绍（`## 目录与读写规则` 之前）|绝不更新，始终保留消费仓内容|允许补充项目介绍和项目专属规则（如门牌、硬约束、下载约定等）|
|`## 目录与读写规则`|脚本只报告差异，不覆盖；由 Agent 对照模板智能语义合并|允许增删项目目录、写权和项目专属约定|
|`## 开发原则`|每次同步从模板强制更新至文件末尾|不允许消费仓改写；禁止在此之后追加额外标题|

`repo_sync.py plan` 会分别展示三部分状态。`apply` 只自动替换 `## 开发原则`；项目介绍和 `## 目录与读写规则` 均不由脚本覆盖。Agent 必须在同步时读取模板与消费仓差异，完成目录与读写规则的语义合并，并确认项目介绍未被改动。缺少这两个标题时分两侧处理：模板源缺标题，脚本对该旧版来源继续使用旧的整文件裁定逻辑；消费仓缺标题时 `apply` 拒绝并提示「按模板补齐 `## 目录与读写规则`、`## 开发原则` 后重跑」（`--agents-file` 语义合并同样要求消费仓标题完整）。`apply` 不支持软链 `AGENTS.md`，先替换为真实文件。

消费仓允许修改的 AGENTS.md 范围只有项目介绍和 `## 目录与读写规则`；`## 开发原则` 及其后的模板规则不应由消费仓自行改写。禁止在 `## 开发原则` 之后或 `## 目录与读写规则` 与 `## 开发原则` 之间放置额外 `##` 标题；消费仓项目专属规则（如门牌、硬约束、特有约定等）必须置于 `## 目录与读写规则` 之前的项目介绍区。若存在协议外额外标题，`plan` 将在分区表中将该标题标记为 `forbidden` 并打印告警与迁移指引，`apply` 与 `--agents-file` 候选校验将抛出 `SyncError` 拒绝同步。

## entity-storage 一次性实体存储迁移

日常 `apply` 遇到旧实体存储布局时会先阻断，并提示从固定来源 product 运行**来源脚本**（带 `--consumer`/`--source`/`--checkout` 固定 pin）执行 `migrate entity-storage --write`。该一次性迁移彻底废除物理搬迁归档与交接归档，收敛至固定规范路径契约：

- `docs/archive/tasks/tNNN_slug/` -> `docs/tasks/tNNN_slug/`（恢复原位流转，校验状态字段为 done/dropped；迁移不复验 handoff/实现血缘，使用时由业务门禁核验）；
- `docs/pending/todo/pNNN.md` -> `docs/pending/pNNN.md`（status: todo；声明 pid/slug 与文件名不符即阻断，不静默改写）；
- `docs/pending/parked/pNNN.md` -> `docs/pending/pNNN.md`（status: parked，保留 reason）；
- `docs/archive/pending/pNNN.md` -> `docs/pending/pNNN.md`（status: closed，保留 resolution 与 reason）；
- `docs/archive/spikes/sNNN/` -> `docs/spikes/sNNN/`（status: completed，校验报告与 findings 闭环证据）；
- `docs/archive/tasks_audit.log` -> `docs/runtime/tasks_audit.log`（无损保持原始字节）；
- `docs/archive/handoff.md` -> 确认内容已完整入库 Git 历史后退役移除；
- 删除已废弃派生索引 `docs/tasks_index.json` / `docs/archive/tasks_index.json`（旧仓已跟踪文件不受 `.gitignore` 影响）；
- 清理遗留空旧目录（含仅剩 `.gitkeep` / `.DS_Store` 占位文件的旧目录亦列为待清理并移除占位文件），并更新维护文档中的确定引用。

### 迁移维护窗口与规程

1. **准备与清点**：
   - 停止所有执行会话、hygiene、同步与集成；
   - 清点所有登记 worktree、本地分支、未完成 attempt、pending merge；先在旧工具链下完成或清理；
   - 确保主工作区与暂存区干净；备份 Git 对象、refs、`docs/runtime/` 及未跟踪附件。
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

日常 `apply` 只接受当前 schema：检测到旧 task 布局、旧 conventions 写法、旧 specs_index 布局、退役 hook 残留、旧实体存储布局时阻断，并提示 `repo_sync.py migrate all`（或按类型单项迁移）。旧布局 state（`legacy-state`）由 `status`/`plan`/`apply`/`link-skills` 入口直接阻断，不在「迁移待办」中展示。`status` / `plan` 的「迁移待办」段落列出与 `apply` / `migrate` 完全相同的可处理集合（workflow-schema、conventions、specs-index、retired-hooks、entity-storage 共 5 类），并给出带 `--consumer`/`--source`/`--checkout` 固定 pin 的可直接执行迁移命令，避免 plan 显示一致但 apply 阻断。

显式迁移把主仓 `docs/tasks/` 下的 task 文档迁移为 `task.md`（需求规格）与 `progress.md`（进展），保留实施记录和 review 处置，更新模板规范块、旧 `status: blocked` 与轮次上限字段。最早布局 `spec.md`（规格）+ `task.md`（过程总账），中间布局 `task.md`（规格）+ `note.md`（过程总账），均一次迁移；`note.md` 与 `progress.md` 并存时整批拒绝迁移，不合并两份记录。迁移在持久文件事务内执行：单次单项迁移内失败回滚本项（作用域限单次迁移内部；`migrate all` 按 `workflow-schema → conventions → specs-index → retired-hooks → entity-storage` 固定顺序逐项提交，已完成步骤保留），成功后校验目标 schema，不可机械转换的数据列出并中止。`apply` 门禁优先报告 `entity-storage` 旧布局；`migrate all` 中 `workflow-schema` 先行、`entity-storage` 最后，若两类旧布局并存按此顺序依次收敛。

自定义 `test_cmd` 的消费仓须先把 `verification_policy` 改为 `project_configured` 并填写 `verification_notes`。

存在已登记 task worktree 时拒绝迁移，先完成或 rewind；存在状态为 done 但尚未合入默认分支的 task 分支时同样拒绝，先 integrate。迁移后工具链只接受当前 schema。

## testing.md 单块门禁迁移

旧版允许同一门禁章节包含多个 bash/sh 代码块；新版要求 `doctor_cmd`、`test_cmd`、`blackbox_verify` 各自最多一个可执行块。此限制有意拒绝歧义，避免把示例或占位命令当作门禁。`docs/blueprint/testing.md` 属消费仓自有文件，同步不会覆盖或自动合并，必须在升级前人工收敛：

1. 在消费仓根保存现有 diff，并备份 `docs/blueprint/testing.md`。

2. 逐节核对真实门禁，把确实需要执行的命令按原顺序合入一个 bash/sh 块；示例和占位说明改为 `text` 围栏或正文。不适用的门禁整节正文写「无」，理由放入 `verification_notes`。保留已配置的项目策略。

3. 在消费仓根用本轮固定模板源校验。下列 `product` 为已核实、固定来源 SHA 的产物根绝对路径；工厂来源为 `<checkout>/repo`，产物来源为 `<checkout>`。命令只解析当前消费仓策略，不运行项目门禁；退出码 0 才可继续同步，非零则先修正文档。

    ```bash
    python3 -c 'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); from repo_task.validation import load_policy; load_policy(Path.cwd())' "$product/.repo_template/scripts"
    ```

4. 校验通过后再按正式流程执行 apply。若升级已因策略错误失败，先核对 `transaction status` 与实际 diff；存在未恢复事务时按「同步准备与 apply 事务」恢复流程处理，再修正文档并重跑。`--skip-tests` 不跳过策略校验，不能用于绕过此迁移。

## 同步版本基线

同步版本的可移植基线为 `.repo_template/sync_manifest.json`（随同步 commit 入库），只保存 resolved commit 和可共享 URL；共享保护决策通过 `prompt add --scope shared` 写入可入库的 `.repo_template/sync_policy.json`；本机来源路径、缓存与私人 user_prompts 留在忽略的 sync_state 中，迁机须备份。新 clone 无 state 时读取 manifest，路径源用 `init --source <新机器路径>` 重新接入，保留版本基线；私人保护策略从备份恢复后再同步。不要把含凭据 URL 或本机路径写进 manifest。

旧布局 state（`.agents/skills/repo-template-sync/sync_state.json`）不参与运行期回退：只有旧文件时 `init` / `apply` / `status` / `plan` / `link-skills` 均明确拒绝并指向来源 product 的 `repo_sync.py migrate legacy-state --write`（带 `--consumer`/`--source`/`--checkout` 固定 pin；旧消费仓本地脚本缺 migrate 子命令，不得用本地路径）。该显式迁移保留来源、同步记录与 user_prompts，完成后删除旧文件；日常命令只读当前 `.repo_template/sync_state.json`。旧格式 `docs/pending.md` / `docs/findings.md` 单文件总账同理：运行期 fail-closed 不解析正文编号，须先用完整命令显式拆分（来源仓脚本，默认 dry-run、`--write` 落盘）：`python3 .repo_template/scripts/pending.py migrate-legacy --write` 与 `python3 .repo_template/scripts/findings.py migrate-legacy --write`。执行前备份，执行后核对拆分条目编号与正文、提交迁移 commit；其它分支/worktree 残留旧总账时编号分配继续阻断。

## 正式同步

正式同步要求 clean 来源，先记录来源 SHA，再从固定 commit 的本地 checkout 运行来源脚本；工厂根与产物根均可。URL 先 clone/fetch 到 `.scratch/`，解析并固定 commit；离线使用已有固定 `--checkout`，同时保留原始 URL 的 `--source` 身份，不执行 pull。dirty 来源仅显式开发验证，不推进 resolved commit，不可冒充正式升级。

## 同步准备与 apply 事务

同步准备仅改 `.scratch/` 来源缓存，取消 prep 与覆盖同步器自举。status/plan 直接运行来源脚本并指定 `--consumer`、canonical `--source` 和固定 `--checkout`；apply 携 `--expected-commit` 核对 plan 的完整 SHA，从该 commit 的 Git archive 快照写入，才开始消费资产事务。文件、入口、schema 和 state 同一基线；内置 contract、项目验证策略校验或写盘失败时尝试自动回滚，备份保留；回滚也可能失败，须核对事务状态与实际 diff，未完整恢复时停止提交并保留现场。进程中断先 `transaction status`，保存中断后新增改动，再 `transaction rollback --confirm-restore`。成功 apply 后仍可 rollback；现场有后续改动时拒绝静默覆盖。批准并提交同步后 `transaction accept`，再开始下一轮。

同步只执行模板工具链契约测试，不执行消费仓 doctor/test/blackbox。apply 始终校验 `testing.md` 存在性、业务策略配置和命令格式；`--skip-tests` 只跳过模板契约测试，策略非法仍失败并回滚。跳过的契约测试必须在 accept 前补验。

`transaction accept` 每次都重读并校验当前 `testing.md`，即使 apply 已验证、受管资产指纹未变，也不能复用旧策略校验结果。模板契约测试仍只在 apply 未验证或受管资产指纹变化时复跑；任一校验失败均保持 applied。`testing.md` 不纳入模板事务资产，避免回滚覆盖消费仓自有配置。

## 降级与 revert

成功同步的版本与消费配置都在独立同步 commit。降级优先 `git revert <同步commit>`（先保证无 active worktree、备份本机 state），随后核对 manifest 并恢复对应 state 基线、重跑项目门禁；指定版本升级则选择该 clean commit 重新同步，不手改工具链文件。以上操作均不自动 push。

## 同步验证锁

同步验证保持事务排他锁。同线程锁可重入；验证子进程不得写同一 Git common dir 的控制面，违规立即失败并回滚，不等待父进程释放锁。需要测试 task CLI 写操作的工具链测试应创建隔离 Git 仓库；普通只读 task 命令与隔离仓库不受此限制。

## .prettierignore 机械追加

`repo_sync.py apply` 机械追加到消费仓 `.prettierignore`（消费独有规则保留，去重），`status` / `plan` 展示缺失项；豁免路径清单见 `usage.md`「命名与格式」。同步改写消费仓自有 JSON（`.claude/settings.json`、MCP）时沿用原缩进，不弄红门禁。`.github/workflows/repo-template-ci.yml` 已下线（`b3e5c8f` 起不再分发），残留时 `status` / `plan` / `apply` 警告，确认无消费定制后手动删除。
