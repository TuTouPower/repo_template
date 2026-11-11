# 模板仓库执行架构

task 工具链的执行拓扑、attempt、review 和合并授权。项目业务架构不写在这里。

## 执行拓扑

每个 task 使用独立分支和 worktree；每个 task 形成一个实现 commit（含 `handoff.json`）。`task-run` 按用户给定顺序串行执行队列，并按队列内成员间的关系分链：两个成员之间存在 `depends_on`（任一方向）或 `conflicts_with`（任一方声明）即相连，连通的成员属同一条链，只看队列内成员。链内保持队列顺序，首个成员以默认分支为 base，其后每个成员以链内紧邻前一成员的分支为 `--base`；无关成员各自从默认分支起步：

```text
main ─┬─ t001 ── t003 ── t004   链 1：三者经依赖或冲突相连
      └─ t002                   链 2：与其它成员无关
```

链内后继因此继承前置成果。每个成员的链编号与 base 由 `task.py plan --serial --json [tid ...]` 的 `members[]` 给出，默认队列与显式 tid 队列共用同一计算；队列中依赖排在依赖者之后时拒绝。队列外依赖须在 `start` 前已 done（未合入主干时须位于单条祖先链，或显式以链尾分支为 `--base`）；在主干新建或更新调度图并已提交的 backlog task 可直接以未合并链尾分支为 `--base` 起步，`start` 从主干权威同步该 task 目录到新 worktree。`run-begin`（内含 start）同时检查：

- `depends_on` 的有效状态均为 done；
- 实际 base 包含每个依赖的实现 SHA；
- 分支和 worktree 没有被其它会话占用。

成员阻塞（`report=blocked`）只停所在链：同链排在它之后的成员全部跳过，其它链继续执行。队列结束时报告已完成成员、阻塞点和被跳过成员；`goal-check` 存在阻塞成员时输出 `stopped_at=` 行列出阻塞点与被跳过成员；其它链仍有可继续成员时为 INCOMPLETE，无可继续成员时才为 STOPPED。合并阶段一次性列出全部待合分支：单成员链 `integrate {tid}`，多成员链 `integrate-chain {tail_tid}`，仍须用户单独授权。

多会话并发由用户手动启动多条独立队列。主仓写入不另建调度服务；Git 冲突由 Git 暴露并交用户或 Agent 处理。

## Attempt

执行实例身份为 `(tid, attempt, execution_id)`。task 状态描述业务生命周期，attempt 描述某次执行：

```text
run-begin(start → reserve → claim) → task-work(finish) → run-close(terminal → report → cleanup)
```

主路径只传 `tid` 与 `owner_id`：`run-begin` 在控制面锁内完成前三步并输出 identity，`run-close` 按 done/blocked/failed 写 terminal 与 report（done 另 exact cleanup），已写事件不重复写。attempt、execution_id 由脚本从 ledger（主仓）或 `repo-task-execution.json`（worktree 私有 Git 目录）解析，显式提供须与解析结果一致。`attempt reserve/claim/terminal/report` 与 `cleanup-worktree` 是 recovery 用的低层原语；ledger 事件类型不因编排收敛而改变。

执行权由 ledger 的 execution_claimed 事件表达，在 ledger 锁内比较并交换；owner_id 属于会话，identity 属于 attempt，两者不可混用。没有自动过期；跨会话接管须确认原会话停止并提供旧 owner。start、图维护、集成和模板同步共用 Git common dir 控制面锁，避免互相读写半途状态。

正式 task 状态只有 `backlog / active / done / dropped`。遇到阻塞时 task 保持 active，实施笔记记录原因；当前 attempt 写 `terminal=stopped` 和 `report=blocked`。条件补齐后 reserve 新 attempt 继续原 worktree。

`review_limit` 和 `verify_limit` 跨 attempt 持久化，新 attempt 不清零历史。用户批准追加轮次时，只能通过 `task.py limits` 增加绝对上限并记录原因。这是 agent 必须遵守的流程约束：`--reason` 由调用方填写，代码只核对记录与上限变化一致，不核验批准来源。

## 调度图与计划

- `depends_on`：真实硬前置；有环或引用缺失时拒绝。
- `conflicts_with`：并发提示；声明可单向，读取时按无向关系处理。
- backlog 之间的冲突不按 tid 强行裁决先后；`plan` 将其分到不同建议链。
- 与 active task 冲突的 backlog 不作为当前并发链首。
- 串行执行不受 conflicts 限制。

backlog 合并通过 merge-backlog 收缩子图：外部前置并集进入目标，下游依赖改指并去重，组内边消除；先验证整图与可编辑性，再在持久文件事务中批量写入。中断用 graph-transaction 查询/恢复。start 拒绝实际 base 与当前任务图不一致，不能借未提交图变更绕过依赖。默认队列与 goal 共用 plan 的拓扑，串行本身满足冲突，不把冲突提示转为反向硬边。

`task.py view` 展示当前有效状态，依赖已满足但彼此冲突的 task 会同时列出并明确提示不要并行；`task.py plan` 负责把它们分到不同建议链。状态变化后按当前 front matter 重新计算；调度字段只有 `depends_on` 与 `conflicts_with`，不要求声明冲突反向边。

## Task 存储架构与核心资产保护

### 固定路径契约

Task 终生在 `docs/tasks/{tid}_{slug}/` 原位流转，生命周期业务状态为 `backlog / active / done / dropped`，不进行物理搬迁归档：

- `add` 创建固定目录，状态为 backlog；
- `start` / `run-begin` 保持同一相对路径并在所属 worktree 激活；
- `finish` 验证后原位写 done，生成 `handoff.json` 并提交实现 commit；
- `drop` 原位写 dropped 并记录原因；
- `merge-backlog` 改写依赖图并将非目标来源原位写 dropped；
- `integrate` 保持所有成员目录在原位，主干记录 `integrated` 事件。

Pending 终生在 `docs/pending/` 原位流转（`todo / parked / closed`）；Spike 终生在 `docs/spikes/` 原位流转（`active / completed / superseded`）；交接快照固定在 `docs/handoff.md` 单文件维护，review 以最近 5 个为保留上限（pre-commit 在新增 review 时检查），且不归档。所有查询和恢复工具均基于规范固定路径，不做多目录或位置推断。

### 核心资产所有权

跨 task 写权以文件为边界。每个 task 目录内的 `task.md`、`progress.md`、`handoff.json`、`review_*.md`（含子目录同名文件）属于受保护核心资产：

- 其他 task 的核心资产即使只改正文、空白或文件模式，即使 review 已经 PASS，也不得由非 owner 的 task 改写；
- 共享校验函数 `monitoring.validate_task_core_asset_changes(before_entries, after_entries, owner_names)` 贯穿 `finish` 前置检查、`verify_integrate_ready`、单任务与整链集成准备及 `--continue` 门禁；
- 校验输入使用未经过 review 排除规则过滤的完整路径清单（包含 Git mode 与 Blob OID），对任何跨 task 篡改、移动、重命名、目录整体替换或借 symlink 覆盖核心资产的行为严格抛出异常并阻断；
- 核心资产纠错必须走所属实体明确授权的维护或恢复流程，禁止通过后继 task 交付。

## Review 与验证

项目验证策略、命令及执行边界见 [testing.md](../../docs/blueprint/testing.md)；verification_policy=project_configured 后才可 start/preflight。single 任务风险扩大时通过 review-upgrade 单调升 full，评审级别参与内容指纹，旧 PASS 失效。

执行者必须在 finish 前运行并通过：

1. 项目相关测试；
2. 项目定义的黑盒验证，无黑盒时必须由项目策略明确选择并提供替代验收；
3. 按 `review_level` 执行的独立 review。

Review 使用 finding ID、PASS/FAIL/INCOMPLETE、处置表、fix_ref 和 scope fingerprint。最终交付内容变化会使旧 PASS 失效。review scope 指纹对交付文件全量哈希（单文件上限 50MB，超限 fail-closed），不存在大文件截断降级。

执行分支恰好 1 个实现 commit；`handoff.json` 的 `review_scope` 绑定 finish 时的交付指纹。done 后 review 失效时，用户同意后对 tip 重新 review，只补 review 过程文件并并入同一实现 commit；交付内容变化不能并入同一实现 commit：attempt 为 running、无后继分支且 report 未写入 done 时用 `task.py finish-unwind` 撤销实现 commit 回到 active 重做，否则停止并报告。cleanup、integrate、integrate-chain 与 `ledger import` 校验分支恰 1 个 commit 且 tip 重算指纹与 `review_scope` 一致。task 目录始终位于 `docs/tasks/`，`finish` 与 `integrate` 均原位保留目录，不进行物理搬迁。

## 合并

merge 必须获得用户明确授权。内容门禁要求 Git 2.38 或更高版本，并在创建 pending merge 前预检能力。合并事务保持实体目录原位，不设搬迁归档专用豁免。单 task 和链式合并均先完成 exact gate，然后：

```text
git merge --no-ff --no-commit <branch-or-tail>
→ 成员 task 目录保持在 docs/tasks/ 原位
→ 运行合并后验证
→ 通过：--continue 校验核心资产所有权与内容一致性，拒绝非冲突文件偏离，随后创建 merge commit、写 integrated、删除分支
→ 失败：git merge --abort
```

Git 的 `MERGE_HEAD`、index 和 `git status` 是合并事务唯一真相。链式只 merge 链尾；Git ancestry 自然包含全部前置实现 commit，全部成员目录保留在 `docs/tasks/` 原位。最终 staged 检查严格验证两层不变量：非成员核心资产相对当前 HEAD 完全不变；成员核心资产必须严格等于该成员已验证实现 tip 中的相应条目。

## Goal

`task.py goal` 冻结 goal 队列并打印宿主可用的 `/goal` 行；`goal-check` 根据 task、attempt、执行回执和 worktree 输出 COMPLETE、STOPPED 或 INCOMPLETE。COMPLETE 仅表示 execution complete，awaiting_merge 和 integrated 单独输出。goal 只包装 task-run，不改变执行和 merge 授权语义。

## 写域

|阶段|写域|职责|
|---|---|---|
|task-work|当前 task worktree|实现、测试、黑盒、review、文档；`finish` 校验、原位写 done、机器生成执行回执并提交实现 commit（失败整体回滚）|
|task-run|主仓控制面|`run-begin`、`run-close`（attempt lifecycle、exact cleanup）、队列推进|
|task-integrate|主仓 main|经授权准备 Git merge、保持成员 task 目录在 docs/tasks/ 原位、合并后验证、commit、integrated 和分支清理|

## 模板分发与升级

初始化只从指定 Git commit 的产品树导出，拒绝 tracked secret 与本机状态；dirty 工作区不参与分发。

同步器直接从固定来源运行，consumer 显式指定，core 直接读取。准备阶段只写来源缓存；不自覆盖、不 prep。apply 在控制面锁内保存全部受管资产的 before-image 和持久日志，schema、入口、共享裁定、项目策略校验、模板契约测试与 state 推进纳入同一事务。apply 始终校验 testing.md 存在性、策略配置与命令格式，--skip-tests 仅跳过模板契约测试；accept 每次重读策略，契约测试按未验证或受管资产变化决定是否复跑。同步不执行项目 doctor/test/blackbox。失败恢复原始内容且保留备份；恢复失败不得吞错。成功后仍处于 applied，批准提交后 accept，放弃则 rollback。AGENTS 两区由同步器整块替换模板区，项目区原样保留；`.gitignore` 的 `# repo-template:begin/end` 托管块整块替换，块外规则保留。

sync_manifest 是可入库版本基线；sync_policy 是显式共享保护决策；sync_state 只承载本机来源和私人 prompt。新 clone 依据 manifest 重新接入，本机 ledger 丢失仅从严格验证的 done Git/执行回执重建，active 不猜补。支持范围见 `usage.md`，灾难恢复步骤见 `recovery.md`，同步与降级步骤见 `sync.md`。

## 权威分工

AGENTS 保留长期不变量和写权；本文解释机制与取舍；usage 说明日常接口（路径写权、命令约定、命名格式、skill 调用、支持边界）；recovery 说明多会话接管与状态恢复；sync 说明模板同步、升级与降级；SKILL 说明阶段动作、授权和停止；registry 是角色/调用图唯一事实源。持久事务日志和脚本检查决定机械状态，测试验证这些语义；派生入口与表格由生成器更新。

## 实体协议与交付验证

实体协议边界由 `.repo_template/entity_schema.json` 表达。运行期只理解当前布局；旧正文转换集中在 `template_sync.legacy_ledgers`，仅 apply 内置迁移导入。取号扫描当前协议的分支/worktree，合并入口验证协议与编号唯一性。版本标记不能替代源身份校验、目标模型验收或业务证据核验。

交付隐私检查使用同一 snapshot 比较器；完整 blob 解码后比较新增文本行，检查点的输入分别是 index、候选 worktree 覆盖或确定 tree/commit。全量审计与交付门禁分离。具体策略见本文「路径隐私」。

handoff 结构由 `repo_task.handoff` 单点校验。依赖来源查询保留缺失、坏数据、Git 查询失败与来源歧义诊断，不把查询失败改写成缺证据。工作区核心资产比较使用 Git 按路径执行 clean/EOL 规范化后的 blob OID。

## 原子写

- 写权威状态数据（`progress.md` front matter、`docs/pending/`、review prompt 文件等）必须走 tmp 文件 + fsync + `os.replace` 原子写，防中断/掉电产生半写状态。派生 task 索引默认不落盘；需要机器可读导出时用 `task.py list --json` 输出到 stdout。
- 公共 helper：`from repo_task.documents import atomic_write_text`。

## 路径隐私

- 提交与合并门禁只阻断本次新增文本行中的开发者家目录绝对路径；使用仓库相对路径或 `<worktree>`。未修改历史行留给显式审计处理，不阻断无关交付。
- 交付比较器为 `repo_task.privacy.scan_privacy_changes`。finish 候选比较 HEAD 与 index，并覆盖本次将暂存的 task 文件；merge 比较第一父提交与目标 tree；tip 核对比较第一父提交与实际提交。review 报告作为完整新增证据扫描。
- 比较前先严格解码完整 blob，再按文本行计算差异。支持 UTF-8、UTF-16/32（含 BOM 与可判定的无 BOM 文本）、GBK；无法解码的 Git 二进制对象或含 NUL 二进制跳过，其他编码错误阻断。不按扩展名豁免文本；所有交付入口使用同一二进制策略。
- 全量审计命令为 `python3 .repo_template/scripts/task.py privacy-audit --ref HEAD`，在消费仓根执行；只读扫描非归档文本，发现泄漏退出 1。无泄漏输出 `privacy-audit=PASS`。全量 index/tree 库函数仅供显式审计，不能接入日常提交门禁。
- 非个人环境固定路径只能在 `repo_task/privacy.py` 的 `FIXED_PATH_EXCEPTIONS` 中登记有依据的完整路径，不按用户名或容器身份全局豁免。隐私范围仍排除 `docs/archive/` 与 `.scratch/`。
