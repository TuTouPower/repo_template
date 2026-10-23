# 消费仓如何使用 repo_template

## 工具链路径与写权

写权归属列声明路径的写入责任与时机；具体步骤见对应 skill 或文件内注释。

|路径|用途|写权归属|
|---|---|---|
|`.repo_template/docs/task_template/`|task 文件模板（非工作项）|只改模板本身|
|`docs/tasks_index.json` / `docs/archive/tasks_index.json`|活跃/归档 task 派生索引|工作区可由 `add`/`edit`/`drop`/`rewind`/`purge` 重建；入库 commit：维护期随操作提交；集成时由 `integrate` / `integrate-chain` 重建并放入同一个 merge commit；`list` 只读，`list --rebuild` 手动重建；不进 task worktree 的执行 commit|
|`docs/archive/tasks_audit.log`|rewind/purge 审计（append-only）|仅 `.repo_template/scripts/task.py rewind` / `purge` 独占 append，禁止 agent 手动修改|
|`docs/runtime/dispatch_ledger.jsonl`|attempt 控制面（append-only；已 gitignore，仅主仓）|exact identity 为 `(tid, attempt, execution_id)`；生命周期只经 `task.py attempt reserve/terminal/report` 写入，`integrate` / `integrate-chain` 写 `integrated`；`ledger tail` 只读；禁止手工编辑|
|`docs/runtime/goal_queue.json`|goal 模式冻结队列快照（已 gitignore，仅主仓）|仅 `task.py goal` 写入（首次冻结，或显式 tid / `--reset` 覆盖；无参已有快照只读）；`task.py goal-check` 只读；禁止手工编辑|
|`.repo_template/docs/review_prompts/`|review prompt 模板|改审查标准时更新|
|`.repo_template/docs/spike_report_template.md`|spike 报告模板|只改模板本身|
|`.repo_template/skills/`|skill 正文|改 skill 走文档纪律；不放业务代码|
|`.claude/skills/` / `.agents/skills/`|指向 `.repo_template/skills/` 的软链|只维护软链|
|`.opencode/commands/`|各 skill 的 opencode `/` 触发器（由 SKILL.md description 生成，调 `skill` 工具执行）|只读（改 SKILL.md 后重跑 `link-skills`）；手写命令保留|
|`.repo_template/scripts/`|模板自带 task 工具链：`task.py` 是 CLI 入口，业务实现位于 `repo_task/`，另含 pending.py/findings.py/spikes.py 等|仅模板演进时修改；复制或维护必须保留 `task.py` 与完整 `repo_task/`，并随模板复制进新项目|
|`../{repo}_{tid}/`（仓库外）|task 工作副本（git worktree）|`start` 仅从主仓默认分支调用（不要求干净，主仓未提交改动保留不动）：链式拓扑以 `--base` 指向上一已完成 task 分支；active task 的实施、测试、review、finish/drop 只在自身 worktree 执行；每个 task 一个执行 commit，实施阶段写 exact identity 的 `handoff.json`，调度阶段以同一 identity 清理 worktree 并合并；本地 `.env` 软链回主仓|

## AGENTS.md 同步分区协议

消费仓根 `AGENTS.md` 按固定标题分为三类内容：

|区段|同步行为|消费仓可定制范围|
|---|---|---|
|项目介绍（`## 目录与读写规则` 之前）|绝不更新，始终保留消费仓内容|允许补充项目介绍和项目专属规则|
|`## 目录与读写规则`|脚本只报告差异，不覆盖；由 Agent 对照模板智能语义合并|允许增删项目目录、写权和项目专属约定|
|`## 开发原则`|每次同步从模板强制更新|不允许消费仓改写|

`repo_sync.py plan` 会分别展示三部分状态。`apply` 只自动替换 `## 开发原则`；项目介绍和 `## 目录与读写规则` 均不由脚本覆盖。Agent 必须在同步时读取模板与消费仓差异，完成目录与读写规则的语义合并，并确认项目介绍未被改动。缺少这两个标题时，脚本对旧版模板继续使用旧的整文件裁定逻辑；新模板必须包含这两个标题。

消费仓允许修改的 AGENTS.md 范围只有项目介绍和 `## 目录与读写规则`；`## 开发原则` 及其后的模板规则不应由消费仓自行改写。

## 命令执行约定

本文与 skill 中的 `task.py`、`pending.py`、`repo_sync.py` 等均指消费仓根下 `.repo_template/scripts/` 中的同名脚本，用 `python3 .repo_template/scripts/<脚本名>` 调用；参数先查该子命令 `--help`。维护/控制面命令在主仓执行，task 实施命令在已登记 worktree 执行。`{tid}`、`{sid}`、`{slug}` 等取实际条目，不原样执行占位示例。脚本与命令示例按 POSIX 环境书写（Linux/macOS/WSL/Git Bash）；Windows 原生 shell 下 `python3`/`command -v` 等不可直接用，经 WSL/Git Bash 执行或换成等价调用（`python`、`Get-Command`）。工具链脚本本身支持 Windows（代码含 `os.name == "nt"` 分支），仅文档示例假定 POSIX。

运行前核对目标目录和 `git status --short`，保护已有改动。非零退出先检查报错与现场，不盲目重复写操作；`--write`、`apply`、merge 和删除仍受相应 skill 的授权条件约束。涉及迁移的脚本可能执行 `git mv` / 暂存，执行后同时检查工作区与暂存区。

## 命名与格式

- `AGENTS.md`、`README.md` 是工具入口例外。
- task 编号：占位 `{tid}`，值小写 `t001`、`t042`…。目录 / 分支 / finding / worktree：`docs/tasks/{tid}_{slug}/`、`{tid}_{slug}`、`{tid}_code_fNNN`、`../{repo}_{tid}`。`{repo}` 取消费仓目录名。
- spike 编号：占位 `{sid}`，值小写 `s001`、`s003`…。目录：`docs/spikes/{sid}_{slug}/`。
- 总账编号：待办与发现均为一条目一文件，文件名 `pNNN_{slug}.md` / `dNNN_{slug}.md`，编号来自文件名。条目只经 `.repo_template/scripts/pending.py new` 与 `findings.py new` 创建——脚本在 git 公共目录的排他锁内完成「扫描全部本地分支与 worktree 取号 → 建文件」，并发执行不会撞号。`pNNN` 跨 `docs/pending/todo/`、`docs/pending/parked/`、`docs/archive/pending/` 共享全局序列，`dNNN` 在 `docs/findings/` 内递增；历史编号均不复用，不维护索引文件。spike 是目录型条目（`docs/spikes/sNNN_{slug}/`），由 `.repo_template/scripts/spikes.py new` 同法锁内分配，`sNNN` 与 `docs/archive/spikes/` 共享序列。
- AC 编号：spec 验收标准每条行为 AC 用 `AC-NNN`（三位十进制，task 内从 1 顺序编号）。编号一旦分配永久归属，删除后不复用（允许断号，不强制连续），新增用下一个编号。`handoff.json` 的 `ac_evidence` 键引用同一编号，须精确覆盖 spec 验收标准全部 AC——缺或多都阻断合入。编号规范属 spec 模板门禁，见 `.repo_template/docs/task_template/spec.md`。
- 占位示例（模板、示例行）不得占用真实 `tid` / `sid` / `pNNN`，也不得当作 active 工作项执行。
- Markdown 嵌套内容缩进 4 空格，禁止 tab。
- 非归档 Markdown 统一用 md_kx 格式化（`.repo_template/scripts/md_format.py`），表用 `compact`（`|a|b|`）。md_kx 来源 [TuTouPower/md_kx](https://github.com/TuTouPower/md_kx)（PyPI 发行名 `md-kx`，命令 `md_kx`），通常已在开发机全局安装（`uv tool install md-kx`）；消费仓不逐仓安装，缺二进制时 `md_format.py` / pre-commit 会在报错里给出来源与安装入口。格式由 `.md_kx.toml` 统一，禁止 prettier / 按列 pad。commit 由 pre-commit hook 强制（`.repo_template/hooks/pre-commit`，格式化本次 staged 的 `.md` 并重新暂存；工作区与 index 不一致则拒绝），需先 `python3 .repo_template/scripts/repo_sync.py install-hooks` 启用 `core.hooksPath`（已有其它 hooksPath 须 `--force`）；临时手动格式化用 `python3 .repo_template/scripts/md_format.py --changed`，commit 前 `--check` 为绿。
- 消费仓 `prettier --check .` 豁免模板侧路径：分发静态文件（两 `package.json`、`view_static/` 看板 UI——模板自有 `2` 空格/单引号风格、`test_chain_plan_cases.js`）、同步元数据（`.repo_template/sync_state.json`、`sync_manifest.json`、`sync_policy.json`）与 skill 注册表（`.repo_template/skills/registry.json`）、派生索引（`docs/tasks_index.json`、`docs/archive/tasks_index.json`，可重建）、任务产物（`docs/**/handoff.json`，逐任务生成），以及本地生成的 `.opencode/package.json` / `package-lock.json`（`prettier` 不认嵌套 `.gitignore`）。`repo_sync.py apply` 机械追加到消费仓 `.prettierignore`（消费独有规则保留，去重），`status` / `plan` 展示缺失项；模板文件不随消费仓 `tabWidth` / 引号配置重排，消费侧不手改、不逐个加 `ignore`。同步改写消费仓自有 JSON（`.claude/settings.json`、MCP）时沿用原缩进，不弄红门禁。`.github/workflows/repo-template-ci.yml` 已下线（`b3e5c8f` 起不再分发），残留时 `status` / `plan` / `apply` 警告，确认无消费定制后手动删除。
- front matter 注释独占整行；行内注释有解析器兜底，但勿依赖。
- 原子写：权威/派生文件与待办维护走原子写（tmp + fsync + os.replace）。公共 helper：`from repo_task.documents import atomic_write_text`。

## skill 调用

用户入口与内部流程以 `.repo_template/skills/registry.json` 为唯一角色和调用图来源。下表由 `skill_registry.py --write` 生成，无参数执行校验。内部流程不生成 OpenCode `/` 命令；宿主可能仍展示其文件，展示不代表执行授权。

<!-- skill_registry:start -->

|skill|角色|
|---|---|
|`bug-to-pending`|用户入口|
|`pending-record`|用户入口|
|`repo-cleanup`|用户入口|
|`repo-hygiene`|用户入口|
|`repo-template-sync`|用户入口|
|`repo-template-sync-core`|内部正文流程|
|`task-create`|用户入口|
|`task-from-pending`|用户入口|
|`task-integrate`|内部正文流程|
|`task-merge`|用户入口|
|`task-preflight`|用户入口|
|`task-run`|用户入口|
|`task-schedule`|用户入口|
|`task-work`|内部正文流程|
|`template-issue-report`|用户入口|

|父流程|子流程（直接读取 canonical 正文）|
|---|---|
|`task-run`|`task-work`|
|`task-run`|`task-integrate`|
|`task-from-pending`|`bug-to-pending`|
|`task-from-pending`|`task-create`|
|`pending-record`|`bug-to-pending`|
|`bug-to-pending`|`task-create`|
|`repo-template-sync`|`repo-template-sync-core`|

<!-- skill_registry:end -->

父流程直接读取 `.repo_template/skills/{child}/SKILL.md` 执行正文，禁止通过原生 Skill 工具嵌套调用。只接受注册调用边，原样传递 tid、attempt、execution_id、owner_id、写域与授权；`analysis-only` 不含 task 立项或 commit，merge 必须另行授权。自然语言匹配 description 不构成显式授权。

CLI/reviewer 接口与 skill 调用图分开：创建有效性用 `preflight --creation`，实施用 `preflight --require-verified --owner-id ...`；reviewer 只读，checker 的 PASS/FAIL/INCOMPLETE 和 next_action 是机械判定，不授予写权。

多会话仅支持同一机器、同一主 clone 的独立 task。每个执行会话生成独立 owner_id（例如 `python3 -c 'import uuid; print(uuid.uuid4().hex)'`），reserve 后必须 `attempt claim`；恢复不得复用另一个会话的 owner_id。claim 在 ledger 锁内比较并写入；无自动超时。接管须确认原会话已停止，展示 identity/worktree/owner，再用 `--replace-owner <旧owner> --confirm-stopped`。禁止在原会话存活时接管；preflight 携 owner_id，冲突即停止。

`task.py recovery {tid}` 只读输出 phase、原 identity、worktree/task_dir 和下一步；不会自动 reserve、commit 或清理。`review_limit` / `verify_limit` 在 task front matter 持久保存，旧 task 默认 5；只有用户批准后由 `limits --review/--verify ... --reason ...` 增加，新 attempt 不清零历史轮次。

创建有效性用 `preflight {tid} --creation`；执行就绪仍用 `preflight {tid} --allow-backlog`，执行期严格验证用 `--require-verified`。前者不能替代后两者。

review 指纹绑定实际交付内容（包含当前 task 的 spec、新文件、mode 与软链变化），不随暂存、提交或 finish 的目录迁移改变。cleanup/integrate 对 done 成员从最终提交读取真实报告及处置表，重算同一指纹；handoff 的 review 摘要不能代替 PASS 证据。升级前的旧指纹不自动迁移为 PASS，须重新审阅；如已提交且 review 门禁失败（stale / missing / format_error / FAIL），保留分支/证据并请用户决定恢复方式，未经用户同意不得 amend；用户同意后唯一合法修复是把补审证据 amend 进同一个执行 commit（first parent 必须仍为 diff_anchor，只改 review 过程文件，禁止第二个 commit、rewind、reserve），amend 后 worktree 干净再按原 identity cleanup。

workflow schema 不做运行时兼容。`repo-template-sync` 的 `apply` 会强制更新主仓 `docs/tasks/` 中的存量 spec/task 模板块（包括把旧 `status: blocked` 改为 `active`、补齐轮次上限字段）；存在已登记 task worktree 时拒绝 apply，必须先完成或 rewind，避免主仓与执行分支各用一套 schema。更新后工具链直接拒绝旧字段和旧状态，不保留双轨解析。

Git 原生 integrate 的内容校验依赖 `git merge-tree --write-tree --name-only`，最低要求 Git 2.38；工具会在执行 `git merge --no-commit` 前检查，版本不足时不会留下 pending merge。

## workflow 示例

`/task-create` → `/task-schedule` → `task.py plan`（本波链）/ `view --serve` → 一个或多个会话 `/task-run`（多会话手动并发各跑一段；状态变后重跑 `plan` 得下一批）。goal 模式自治跑队列：先 `task.py goal` 冻结队列并粘贴其输出的 `/goal` 行，终态以 `task.py goal-check` marker 判定。

goal 快照是主仓级单份文件；多会话不要互相覆盖队列。无参已有快照只展示；`--reset` 直接重建并覆盖，显式 tid 改变队列时需确认（`--yes` 表示已确认）。覆盖前保存需要保留的队列，成员与顺序无法确认时停止。默认新队列复用 `plan --serial` 的依赖拓扑；显式 tid 保留输入顺序。`goal-check` 输出 execution_complete、integrated 和 awaiting_merge tid；COMPLETE 仅表示执行闭环，不表示已合主干。`goal-check` 的退出码为 COMPLETE=0、STOPPED=3、INCOMPLETE=2，快照错误为 1；合法停止不等于全部完成。

图合并中断后用 `task.py graph-transaction status` 查询；保存新增改动后 `graph-transaction rollback --confirm-restore` 恢复图基线，不手删硬依赖。

### `.repo_template/scripts/` 使用示例

```bash
python3 .repo_template/scripts/task.py --help        # 所有子命令、参数与用法
python3 .repo_template/scripts/pending.py --help     # 待办总账
python3 .repo_template/scripts/findings.py --help    # 技术发现
python3 .repo_template/scripts/spikes.py --help      # 技术 spike
python3 .repo_template/scripts/repo_state.py --help  # 完整工作树 vs baseline 取数（清洁度/deliverable 核对）
```

## 状态恢复与升级

各 task worktree 的 `docs/runtime/execution_context.json` 留存原 reserve identity，ledger 丢失时用于 fail-closed 诊断，不代替 ledger、不入库。

运行态只服务本机主 clone，不提供分布式协作。迁移前停止所有会话，备份整个 Git common dir、主仓 `docs/runtime/`、`.repo_template/sync_state.json`、所有 task 分支与 worktree 未提交内容；用隔离副本核对 ledger、Git refs、handoff 和 review 可读后再迁移。不能仅复制 goal 快照或把新 clone 当作原控制面。

ledger 丢失但 done 分支和 committed handoff 完整时，运行 `task.py ledger import {tid} --confirm-stopped`。脚本验证 exact identity、一个执行 commit、base、AC evidence、最终 review 和干净 worktree，原子重建完成事件；已有该 tid 事件则拒绝覆盖。active、缺分支、缺 handoff 或坏 review 不可猜补，必须恢复备份；`recovery` 给出停止诊断。goal 快照可用 `goal --reset` 从有效任务重新冻结。

同步版本的可移植基线为 `.repo_template/sync_manifest.json`（随同步 commit 入库），只保存 resolved commit 和可共享 URL；共享保护决策通过 `prompt add --scope shared` 写入可入库的 `.repo_template/sync_policy.json`；本机来源路径、缓存与私人 user_prompts 留在忽略的 sync_state 中，迁机须备份。新 clone 无 state 时读取 manifest，路径源用 `init --source <新机器路径>` 重新接入，保留版本基线；私人保护策略从备份恢复后再同步。不要把含凭据 URL 或本机路径写进 manifest。

正式同步要求 clean 来源，先记录来源 SHA，再从固定 commit 的本地 checkout 运行来源脚本；工厂根与产物根均可。URL 先 clone/fetch 到 `.scratch/`，解析并固定 commit；离线使用已有固定 `--checkout`，同时保留原始 URL 的 `--source` 身份，不执行 pull。dirty 来源仅显式开发验证，不推进 resolved commit，不可冒充正式升级。

同步准备仅改 `.scratch/` 来源缓存，取消 prep 与覆盖同步器自举。status/plan 直接运行来源脚本并指定 `--consumer`、canonical `--source` 和固定 `--checkout`；apply 携 `--expected-commit` 核对 plan 的完整 SHA，从该 commit 的 Git archive 快照写入，才开始消费资产事务。文件、入口、schema 和 state 同一基线；任何内置 contract、项目 doctor/test/blackbox 或写盘失败全部恢复，备份保留。进程中断先 `transaction status`，保存中断后新增改动，再 `transaction rollback --confirm-restore`。成功 apply 后仍可 rollback；现场有后续改动时拒绝静默覆盖。批准并提交同步后 `transaction accept`，再开始下一轮。

成功同步的版本与消费配置都在独立同步 commit。降级优先 `git revert <同步commit>`（先保证无 active worktree、备份本机 state），随后核对 manifest 并恢复对应 state 基线、重跑项目门禁；指定版本升级则选择该 clean commit 重新同步，不手改工具链文件。以上操作均不自动 push。

## 支持边界

|维度|契约|
|---|---|
|系统|macOS、Linux 的 POSIX/Bash 路径；WSL/Git Bash 须自行验证软链与 shell，原生 Windows 全流程未认证|
|基础工具|Python 3.9+、Git 2.38+、pytest、Bash；Markdown 使用 md_kx（发行名 md-kx）|
|工厂测试|Node 22；工厂 CI 全量 pytest，消费默认 contract 不运行全部 Git 集成场景|
|Claude|依赖用户 `/` 入口、文件读取与工具权限；使用 disable-model-invocation 和内部 user-invocable=false|
|Kimi Code|使用显式入口与文件读取；原生 Skill 禁用字段兼容性须按安装版本实测，未宣称认证最低版本|
|OpenCode|使用 metadata.opencode/autoinvoke=false，公开命令由 link-skills 生成；不依赖 v2 兼容别名|
|宿主认证|静态结构测试不等于宿主 E2E；版本变更后按下述清单认证，未实测版本标记未认证|
|可选能力|后台子代理不支持时顺序执行；goal 仅宿主支持时使用；hooks 需显式 install-hooks|
|网络|本地固定来源可离线同步；URL clone/fetch 与 GitHub issue 需要网络及用户现有凭据|

宿主版本发布前记录版本号并逐项实测：自然语言不自动执行入口；显式 task-run 可读取 work/integrate 正文且原样传递 identity/owner；merge 保持单独授权；同步从来源读取 core；同 tid 第二会话 claim 被拒绝。未完成该清单不宣称该宿主版本端到端受支持。

## 附属生命周期闭环

pending 归档必须提供非空 `--reason`，说明处理结果、验证依据或转办/去重去向；`--fix-ref` 可选，不要求 task ID，可记录 main、commit、PR、条目或 task。默认 resolution=resolved；转办显式用 `--resolution transferred`，其他类型为 obsolete/duplicate，所有类型均只强制备注，不强制引用。存在 task 时保留 pNNN 与 tid 双向关联；直接在 main 完成时如实写备注，不伪造 task ID。

```bash
python3 .repo_template/scripts/pending.py archive p001 --reason "按用户要求在 main 修复；专项测试通过" --write
```

finish 仅强校验包含当前 tid 的 specs_index 行、对应 spec 与当前 task 引用；历史 task 目录缺失不阻断当前收尾，其他历史索引异常仅告警。当前 task 必须被索引覆盖；无长期 spec 变更时在 task.md 写 `- specs_index: not_applicable: 具体原因`。废弃 spec 先删索引行再归档。正式 spike 的 report、finding、采纳决定与后续 tid/无需后续理由由 finish 与 archive-spike 共用检查；finding/spike 引用使用至少三位编号（如 d001/s001）或 docs 路径，代码块和普通变量不作为引用，字段支持中英文冒号及冒号后空白；一次性 scratch 实验不创建正式 sid。

同步验证保持事务排他锁。同线程锁可重入；验证子进程不得写同一 Git common dir 的控制面，违规立即失败并回滚，不等待父进程释放锁。需要测试 task CLI 的项目测试应创建隔离 Git 仓库；普通只读 task 命令与隔离仓库不受此限制。

Python 3.9 是模板脚本最低版本，CI 覆盖 3.9 与 3.14。md_kx 是 PATH 上独立工具，其自身 Python 需求由独立工具环境满足，不要求装进消费项目的 Python 3.9 环境。
