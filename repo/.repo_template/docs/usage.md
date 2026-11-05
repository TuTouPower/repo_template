# 消费仓如何使用 repo_template

多会话接管与恢复见 `recovery.md`；模板同步、升级与降级见 `sync.md`。

## 工具链路径与写权

写权归属列声明路径的写入责任与时机；具体步骤见对应 skill 或文件内注释。

|路径|用途|写权归属|
|---|---|---|
|`.repo_template/docs/task_template/`|task 文件模板（非工作项）|只改模板本身|
|`docs/tasks_index.json` / `docs/archive/tasks_index.json`|活跃/归档 task 派生索引|工作区可由 `add`/`edit`/`drop`/`rewind`/`purge` 重建；入库 commit：维护期随操作提交；集成时由 `integrate` / `integrate-chain` 重建并与 task 目录的归档移动放入同一个 merge commit；`list` 只读，`list --rebuild` 手动重建；不进 task worktree 的实现 commit|
|`docs/archive/tasks_audit.log`|rewind/purge 审计（append-only）|仅 `.repo_template/scripts/task.py rewind` / `purge` 独占 append，禁止 agent 手动修改|
|`docs/runtime/dispatch_ledger.jsonl`|attempt 控制面（append-only；已 gitignore，仅主仓）|`run-begin` / `run-close` 记录执行，`integrate` / `integrate-chain` 写 `integrated`；`ledger tail` 只读；禁止手工编辑。身份解析与恢复命令见 `recovery.md`|
|`docs/runtime/goal_queue.json`|goal 模式冻结队列快照（已 gitignore，仅主仓）|仅 `task.py goal` 写入（首次冻结，或显式 tid / `--reset` 覆盖；无参已有快照只读）；`task.py goal-check` 只读；禁止手工编辑|
|`.repo_template/docs/review_prompts/`|review prompt 模板|改审查标准时更新|
|`.repo_template/docs/spike_report_template.md`|spike 报告模板|只改模板本身|
|`.repo_template/skills/`|skill 正文|改 skill 走文档纪律；不放业务代码|
|`.claude/skills/` / `.agents/skills/`|指向 `.repo_template/skills/` 的软链|只维护软链|
|`.opencode/commands/`|各 skill 的 opencode `/` 触发器（由 SKILL.md description 生成，调 `skill` 工具执行）|只读（改 SKILL.md 后重跑 `link-skills`）；手写命令保留|
|`.repo_template/scripts/`|模板自带 task 工具链：`task.py` 是 CLI 入口，业务实现位于 `repo_task/`，另含 pending.py/findings.py/spikes.py 等|仅模板演进时修改；复制或维护必须保留 `task.py` 与完整 `repo_task/`，并随模板复制进新项目|
|`../{repo}_{tid}/`（仓库外）|task 工作副本（git worktree）|`run-begin`（内含 start）在主仓默认分支执行，保留主仓未提交改动；实施、测试、review、finish/drop 只在自身 worktree 执行。`finish` 机器生成 `handoff.json`（不手写）并提交实现 commit；`run-close` 在主仓收尾与清理，`integrate` 负责归档；本地 `.env` 与 `.worktreeinclude` 命中的忽略路径软链回主仓|
|`.worktreeinclude`|task worktree 本地文件接管清单（gitignore 语法）|消费仓自有；初始化时随模板复制示例，同步不覆盖|

## 命令执行约定

本文与 skill 中的 `task.py`、`pending.py`、`repo_sync.py` 等均指消费仓根下 `.repo_template/scripts/` 中的同名脚本，用 `python3 .repo_template/scripts/<脚本名>` 调用；参数先查该子命令 `--help`。维护/控制面命令在主仓执行，task 实施命令在已登记 worktree 执行。`{tid}`、`{sid}`、`{slug}` 等取实际条目，不原样执行占位示例。脚本与命令示例按 POSIX 环境书写（Linux/macOS/WSL/Git Bash）；Windows 原生 shell 下 `python3`/`command -v` 等不可直接用，经 WSL/Git Bash 执行或换成等价调用（`python`、`Get-Command`）。工具链脚本本身支持 Windows（代码含 `os.name == "nt"` 分支），仅文档示例假定 POSIX。

运行前核对目标目录和 `git status --short`，保护已有改动。非零退出先检查报错与现场，不盲目重复写操作；`--write`、`apply`、merge 和删除仍受相应 skill 的授权条件约束。涉及迁移的脚本可能执行 `git mv` / 暂存，执行后同时检查工作区与暂存区。

## task 状态读取

task 状态读取优先级：登记 worktree → 未合并 task 分支 ref → 主干，`task.py effective-status` 只读输出有效状态与来源。进行中 task 的状态在其合并前不进主干；`list/show/preflight --ref` 用于只读分支快照，不能据主干旧 backlog 重复 start 或维护。

## task worktree 本地文件

`start`（含 `run-begin`）把主仓任意层级（跳过依赖、缓存与产物目录）的 `.env` 软链进 task worktree；主仓根有 `.worktreeinclude` 时，另把其命中且确被 `.gitignore` 忽略的文件或整目录软链到同相对路径，用于本地配置、证书、容器数据等不入库但测试必需的内容。未被忽略的未跟踪文件（在途工作）、已入库文件、`.git` 与 `docs/runtime/` 不链接；worktree 已有同名路径时跳过。`.gitignore` 以 `/` 结尾的规则只匹配目录、不匹配同名软链，脚本对这类链接在 Git 公共目录 `info/exclude` 的托管块追加锚定规则，保证链接在 worktree 中仍被忽略、不会被暂存；该路径在主仓本已忽略，规则不改变主仓行为。清理、drop、rewind 只删除脚本创建的软链，不触及主仓目标。

软链共享同一份内容，worktree 内的修改即修改主仓；需要隔离的配置不要列入。含 editable 安装的环境目录（如 `.venv`）会让测试实际运行主仓源码，不要列入，在 worktree 内重建（如 `uv sync`）。docker compose 默认以目录名作 project 名，worktree 会得到另一套网络、卷和容器名，数据库为空且端口可能冲突；串行验证需要复用主仓容器与数据时，在 `.env` 中固定 `COMPOSE_PROJECT_NAME`，需要隔离时按 task 设定 project 名与端口。

## 命名与格式

- `AGENTS.md`、`README.md` 是工具入口例外。
- task 编号：占位 `{tid}`，值小写 `t001`、`t042`…。目录 / 分支 / finding / worktree：`docs/tasks/{tid}_{slug}/`、`{tid}_{slug}`、`{tid}_code_fNNN`、`../{repo}_{tid}`。`{repo}` 取消费仓目录名。
- spike 编号：占位 `{sid}`，值小写 `s001`、`s003`…。目录：`docs/spikes/{sid}_{slug}/`。
- 总账编号：待办与发现均为一条目一文件，文件名 `pNNN_{slug}.md` / `dNNN_{slug}.md`，编号来自文件名。条目只经 `.repo_template/scripts/pending.py new` 与 `findings.py new` 创建——脚本在 git 公共目录的排他锁内完成「扫描全部本地分支与 worktree 取号 → 建文件」，并发执行不会撞号。`pNNN` 跨 `docs/pending/todo/`、`docs/pending/parked/`、`docs/archive/pending/` 共享全局序列，`dNNN` 在 `docs/findings/` 内递增；历史编号均不复用，不维护索引文件。spike 是目录型条目（`docs/spikes/sNNN_{slug}/`），由 `.repo_template/scripts/spikes.py new` 同法锁内分配，`sNNN` 与 `docs/archive/spikes/` 共享序列。
- AC 编号：spec 验收标准每条行为 AC 用 `AC-NNN`（三位十进制，task 内从 1 顺序编号）。编号一旦分配永久归属，删除后不复用（允许断号，不强制连续），新增用下一个编号。创建时均为未勾选（`- [ ]`），实施验证通过后由实施 Agent 就地打勾（`- [x]`），Review Agent 独立复核打勾真实性（报告「AC 复验方式」逐条判 `TICK-VALID` / `TICK-VOID`，未打勾记 `TICK-VOID`；`check_review_status.py` 门禁：任一 `TICK-VOID` 判 FAIL，AC 行与 `task.md` 编号不一致或格式错判 INCOMPLETE，完整格式见 `.repo_template/docs/review_prompts/share_prompt.txt`）。合入时门禁机械检查全部 AC 是否勾选完成——未打勾阻断合入。编号规范属 task 模板门禁，见 `.repo_template/docs/task_template/task.md`。
- 占位示例（模板、示例行）不得占用真实 `tid` / `sid` / `pNNN`，也不得当作 active 工作项执行。
- Markdown 嵌套内容缩进 4 空格，禁止 tab。
- 非归档 Markdown 统一用 md_kx 格式化（`.repo_template/scripts/md_format.py`），表用 `compact`（`|a|b|`）。md_kx 来源 [TuTouPower/md_kx](https://github.com/TuTouPower/md_kx)（PyPI 发行名 `md-kx`，命令 `md_kx`），通常已在开发机全局安装（`uv tool install md-kx`）；消费仓不逐仓安装，缺二进制时 `md_format.py` / pre-commit 会在报错里给出来源与安装入口。格式由 `.md_kx.toml` 统一，禁止 prettier / 按列 pad。commit 由 pre-commit hook 强制（`.repo_template/hooks/pre-commit`，格式化本次 staged 的 `.md` 并重新暂存；工作区与 index 不一致则拒绝），需先 `python3 .repo_template/scripts/repo_sync.py install-hooks` 启用 `core.hooksPath`（已有其它 hooksPath 须 `--force`）；临时手动格式化用 `python3 .repo_template/scripts/md_format.py --changed`，commit 前 `--check` 为绿。
- 消费仓 `prettier --check .` 豁免模板侧路径：分发静态文件（`.repo_template/scripts/package.json`、`view_static/` 看板 UI——模板自有 `2` 空格/单引号风格）、同步元数据（`.repo_template/sync_state.json`、`sync_manifest.json`、`sync_policy.json`）与 skill 注册表（`.repo_template/skills/registry.json`）、派生索引（`docs/tasks_index.json`、`docs/archive/tasks_index.json`，可重建）、任务产物（执行回执 `docs/**/handoff.json`，逐任务生成），以及本地生成的 `.opencode/package.json` / `package-lock.json`（`prettier` 不认嵌套 `.gitignore`）。模板文件不随消费仓 `tabWidth` / 引号配置重排，消费侧不手改、不逐个加 `ignore`；`.prettierignore` 机械追加与缺失项展示规则见 `sync.md`。
- front matter 注释独占整行；行内注释有解析器兜底，但勿依赖。
- 原子写：权威/派生文件与待办维护走原子写（tmp + fsync + os.replace）。公共 helper：`from repo_task.documents import atomic_write_text`。

## skill 调用

用户入口与内部流程以 `.repo_template/skills/registry.json` 为唯一角色和调用图来源。下表由 `skill_registry.py --write` 生成，无参数执行校验。内部流程不生成 OpenCode `/` 命令；宿主可能仍展示其文件，展示不代表执行授权。

<!-- skill_registry:start -->

|skill|角色|
|---|---|
|`bug-to-pending`|用户入口|
|`pending-fix`|用户入口|
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

父流程直接读取 `.repo_template/skills/{child}/SKILL.md` 执行正文，禁止通过原生 Skill 工具嵌套调用。只接受注册调用边，主路径传 tid、owner_id、写域与授权；attempt / execution_id 仅在 recovery 显式指定 exact identity 时传。父流程授权不扩大，缺少所需授权时停止；`analysis-only` 不含 task 立项或 commit，merge 必须另行授权。自然语言匹配 description 不构成显式授权。

CLI/reviewer 接口与 skill 调用图分开：创建有效性用 `preflight {tid} --creation`；执行就绪仍用 `preflight {tid} --allow-backlog`，执行期严格验证用 `preflight --require-verified --owner-id ...`。创建有效性不能替代后两者。reviewer 只读，checker 的 PASS/FAIL/INCOMPLETE 和 next_action 是机械判定，不授予写权。

多会话 owner_id、claim、接管与 `task.py recovery` 诊断、`review_limit` / `verify_limit` 上限见 `recovery.md`。

review 指纹绑定实际交付内容（包含当前 task 的 spec、新文件、mode 与软链变化），不随暂存、提交改变；`handoff.json` 的 `review_scope` 记录 finish 时的指纹，最终提交必须与之一致。cleanup/integrate 对 done 成员从最终提交读取真实报告及处置表，重算同一指纹；执行回执（`handoff.json`）的 review 摘要不能代替 PASS 证据。升级前的旧指纹不自动迁移为 PASS，须重新审阅。已提交且 review 门禁失败（stale / missing / format_error / FAIL）的修复规程见 `recovery.md`。

workflow schema 强制迁移（`repo-template-sync` 的 `apply`）见 `sync.md`。

Git 原生 integrate 的内容校验依赖 `git merge-tree --write-tree --name-only`，最低要求 Git 2.38；工具会在执行 `git merge --no-commit` 前检查，版本不足时不会留下 pending merge。

## workflow 示例

`/task-create` → `/task-schedule` → `task.py plan`（本波链）/ `view --serve` → 一个或多个会话 `/task-run`（多会话手动并发各跑一段；状态变后重跑 `plan` 得下一批）。单个 task 的 `/task-run` 主路径只传 `tid` 与 `owner_id`：主仓 `run-begin` → worktree 内 `preflight` / `render_review_prompts.py` / `check_review_status.py` / `finish` → 主仓 `run-close --result done`；merge 经授权后 `integrate {tid}` 与 `integrate {tid} --continue`。阻塞或失败用 `run-close --result blocked|failed`；`recovery` 与原语子命令只在恢复时使用。goal 模式自治跑队列：先 `task.py goal` 冻结队列并粘贴其输出的 `/goal` 行，终态以 `task.py goal-check` marker 判定。

goal 快照是主仓级单份文件；多会话不要互相覆盖队列。无参已有快照只展示；`--reset` 直接重建并覆盖，显式 tid 改变队列时需确认（`--yes` 表示已确认）。覆盖前保存需要保留的队列，成员与顺序无法确认时停止。默认新队列复用 `plan --serial` 的依赖拓扑；显式 tid 保留输入顺序。队列分链、base 与阻塞规则见[执行拓扑](architecture.md#%E6%89%A7%E8%A1%8C%E6%8B%93%E6%89%91)。`goal-check` 输出 execution_complete、integrated 和 awaiting_merge tid；COMPLETE 仅表示执行闭环，不表示已合主干。`goal-check` 的退出码为 COMPLETE=0、STOPPED=3、INCOMPLETE=2，快照错误为 1；合法停止不等于全部完成。

项目验证命令及执行边界见 [testing.md](../../docs/blueprint/testing.md)；图合并中断后的 `graph-transaction` 恢复见 `recovery.md`。

### `.repo_template/scripts/` 使用示例

```bash
python3 .repo_template/scripts/task.py --help        # 所有子命令、参数与用法
python3 .repo_template/scripts/pending.py --help     # 待办总账
python3 .repo_template/scripts/findings.py --help    # 技术发现
python3 .repo_template/scripts/spikes.py --help      # 技术 spike
python3 .repo_template/scripts/repo_state.py --help  # 完整工作树 vs baseline 取数（清洁度/deliverable 核对）
```

## 支持边界

|维度|契约|
|---|---|
|系统|macOS、Linux 的 POSIX/Bash 路径；WSL/Git Bash 须自行验证软链与 shell，原生 Windows 全流程未验证|
|基础工具|Python 3.9+、Git 2.38+、pytest、Bash；Markdown 使用 md_kx（发行名 md-kx）|
|工厂测试|Node 22；工厂全量 pytest，消费默认 contract 不运行全部 Git 集成场景|
|Claude|依赖用户 `/` 入口、文件读取与工具权限；使用 disable-model-invocation 和内部 user-invocable=false|
|Kimi Code|使用显式入口与文件读取；原生 Skill 禁用字段兼容性须按安装版本实测|
|OpenCode|使用 metadata.opencode/autoinvoke=false，公开命令由 link-skills 生成；不依赖 v2 兼容别名|
|可选能力|后台子代理不支持时顺序执行；goal 仅宿主支持时使用；hooks 需显式 install-hooks|
|网络|本地固定来源可离线同步；URL clone/fetch 与 GitHub issue 需要网络及用户现有凭据|

## 状态行 Hook 与执行范围

默认不启用。需要时执行 `python3 .repo_template/scripts/repo_sync.py install-hooks --statusline`，将 PostToolUse hook 合并到 `.claude/settings.json`，保留已有配置；JSON 格式错误时拒绝，不改 `core.hooksPath`。已有消费仓的 hook 由消费仓维护，模板同步不增删。

hook 在调度账本存在、命令包含 `run-begin` / `start` / `track_worktree` / `--tid` 时，把会话对应的 worktree 写入 `.scratch/statusline_workdir.jsonl`。显示状态行还需在全局 `statusLine.command` 配置读取该文件的渲染脚本。

## 附属生命周期闭环

pending 归档必须提供非空 `--reason`，说明处理结果、验证依据或转办/去重去向；`--fix-ref` 可选，不要求 task ID，可记录 main、commit、PR、条目或 task。默认 resolution=resolved；转办显式用 `--resolution transferred`，其他类型为 obsolete/duplicate，所有类型均只强制备注，不强制引用。存在 task 时保留 pNNN 与 tid 双向关联；直接在 main 完成时如实写备注，不伪造 task ID。小而低风险的 pending 经用户授权后由 `pending-fix` 在 main 直修，准入与流程见该 skill。

```bash
python3 .repo_template/scripts/pending.py archive p001 --reason "按用户要求在 main 修复；专项测试通过" --write
```

`docs/specs_index.md` 是唯一 spec 清单，全文只允许一张表（HTML 注释与围栏代码块内的行不参与），表头严格为 `|slug|task 清单|最后更新时间|`（单元格两端空白允许，紧凑/对齐写法均可），分隔行每格匹配 `:?-+:?`，说明性表格不得放进本文件。数据行 3 列：slug 匹配 `^[a-z][a-z0-9_]*$`、全表不重复、不加反引号，且 `docs/specs/<slug>.md` 必须存在；task 清单用半角逗号分隔，每个 token 是 `t\d{3,}` 或 `baseline`（spec 早于 task 体系、无 tid 可登记时写 `baseline`）；最后更新时间为 `YYYY-MM-DD`。finish 严格校验整张表，违规一次全部列出（带行号）并使收尾失败，没有「历史条目需维护」告警降级。当前 task 满足其一即可：当前 tid 出现在某行的 task 清单（在表即生效），或无长期 spec 变更时在 progress.md 写 `- specs_index: not_applicable: 具体原因`。历史 tid 不要求对应 task 目录仍存在。废弃 spec 先删索引行再归档。

正式 spike 的 report、finding、采纳决定与后续 tid/无需后续理由由 finish 与 archive-spike 共用检查；finding/spike 引用使用至少三位编号（如 d001/s001）或 docs 路径，代码块和普通变量不作为引用，字段支持中英文冒号及冒号后空白；一次性 scratch 实验不创建正式 sid。

Python 3.9 是模板脚本最低版本。md_kx 是 PATH 上独立工具，其自身 Python 需求由独立工具环境满足，不要求装进消费项目的 Python 3.9 环境。
