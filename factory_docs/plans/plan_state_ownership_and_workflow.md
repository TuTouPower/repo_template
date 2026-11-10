# 本机状态、所有权与工作流收口方案

> 编写时间：2026-11-10T09:25:18+08:00。更新时间：2026-11-10T16:13:56+08:00。
>
> 文档性质：已实施的工厂改造方案。范围依据用户 2026-11-10 裁决，以及 [宏观审阅与改进方案](../reviews/review_20261110_macro.md) 的 S4–S9。裁决状态见 [decision_log.md](../decision_log.md)。本文只记录工厂实施与验证；消费仓由用户需要时自行迁移，模板不规定铺开流程。
>
> 适用范围：工厂仓 `repo/` 产物、`factory_tests/`、`factory_docs/` 工厂笔记，以及迁移脚本对通用旧布局的处理。模板工具链本身仍留在产物内，不做外置。
>
> 不在范围：S1 保证等级与预算、S2 tag 发布边界、S3 分发模型、S4 第 5 点权威收敛、S5 第 1 点 `project.json`、S6 第 1–2 点路径单点化与根迁移、S6 第 6 点的 `handoff.json` 改名、S9 第 4 点工厂自用。

## 1. 决策与范围

### 1.1 本批实施

|来源|内容|本方案处理|
|---|---|---|
|S4 第 1–4 点|主仓级状态迁入 Git common dir；worktree 状态迁入 worktree 私有 Git 目录；`.scratch/` 只留草稿与可重建缓存；删除由此失效的特例|WP1|
|S5 第 2–4 点|`AGENTS.md` 两区；`conventions.md` 拆分；`.gitignore` 托管块|WP2|
|S6 第 3 点|取消 specs 归档；review 只保留最近 5 个，由 pre-commit hook 超限阻断；`repo-hygiene` 删除|WP3|
|S6 第 4 点|spec front matter；删除 `specs_index.md`，清单由 front matter 派生|WP3|
|S6 第 5 点|ADR 一条一文件|WP3|
|S6 第 6 点（部分）|`dispatch_ledger.jsonl` 随 S4 改名为 `ledger.jsonl`|WP1|
|S7|spec 读取闭环；测试/黑盒仍由 task-work 与 reviewer 执行|WP4|
|S8|`task-integrate` 公开；`task-merge` 改名；husky/lefthook 接入片段；初始化与检查命令|WP5|
|S9 第 1–3 点|产物文档只描述现状；工厂笔记收敛；工厂测试按模块整理|WP6|

### 1.2 明确不做

|项|原因或后续|
|---|---|
|S1 保证等级与复杂度预算|不做成文模型和预算测试。本批不新增机械测试门禁，S7 只补 spec 读取；测试/黑盒仍由 task-work 与 reviewer 执行。|
|S2 tag 发布边界|main 仍是事实发布通道。发布前锁定一个全量通过的 commit SHA，并写发布说明；不引入 tag-only 同步。|
|S3 分发模型|`template_sync/`、`repo_sync.py`、`export_template.py` 继续随产物分发。新增迁移模块也留在产物内，删除时机另行评估。|
|S4 第 5 点|integrate 继续以 ledger 的 terminal/report 为前提；`ledger import` 保留。|
|S5 第 1 点|门禁命令继续由 `docs/blueprint/testing.md` 承载；本方案不新增 `project.json`，不改变现有校验。|
|S6 第 1–2 点|不做路径单点化重构，不引入 `work/` 根。`tasks/pending/spikes/reviews` 继续留在 `docs/`。|
|S6 第 6 点其余|`handoff.json` 不改名 `receipt.json`；`docs_repo/` 名称不属于本方案范围，后由用户 2026-11-10 裁决改名 `factory_docs/`。|
|S9 第 4 点|工厂不切换到已安装版工具自管；该目标依赖 S3 阶段二。|

### 1.3 关键取舍

1. **S4 必须按 1–4 整体实施。** 第 2–4 点都需要第 1 点提供主仓状态的新落点；只做 2–4 会留下 ledger、goal、事务无家可归的空洞。
2. **S6 不做根迁移。** 因此本方案的目标布局仍是 L59 的 `docs/tasks/`、`docs/pending/`、`docs/spikes/`，只取消 specs 的归档语义。`docs/` 冷热分根问题保留，换取迁移面显著缩小。
3. **S6.6 只做 ledger 改名。** `dispatch_ledger` 随 S4 移动，改名不增加窗口。`handoff` 改名在没有根迁移时只是额外迁移，收益低，暂缓。
4. **S7 不机械化测试。** `finish` 不执行 `test_cmd`，避免与 task-work 已有测试、reviewer 独立复跑重复。`test_cmd` 与 `blackbox_verify` 保持现有执行位置：task-work 实施阶段跑，reviewer 独立复跑，integrate 按现有 skill 做合并后验证。S7 只补 spec 关联与 review prompt 注入。
5. **review 不做 archive，也不写 prune 脚本。** 只保留最近 5 个 `docs/reviews/review_*`；pre-commit hook 在超过 5 个时阻断提交并列出应删除的旧目录。删除仍由用户/agent 手动执行 `git rm -r`，Git 历史保留。一次性迁移由同步迁移处理旧 archive。
6. **归档清理必须区分模板资产与消费仓自有文件。** 现有消费仓 `docs/archive/` 含研究、蓝图、旧计划等非模板内容，不能按“删除 archive”整目录删除。

## 2. 目标与非目标

### 2.1 目标

|目标|成功判据|
|---|---|
|本机状态有唯一落点|主仓状态在 `$GIT_COMMON_DIR/repo_template/`；worktree 状态在私有 Git 目录；`.scratch/` 不再承载持久状态。|
|文件所有权可机械判断|`AGENTS.md` 模板区同步时整块替换；`conventions.md` 只留项目约定；`.gitignore` 模板规则托管块可重复替换。|
|归档不再由模板维护|archived specs 回到 `docs/specs`；review 只保留最近 5 个；pre-commit hook 超限阻断；`repo-hygiene` 删除；消费仓自有 `docs/archive` 内容不被删除。|
|spec 有可派生索引|`docs/specs/<slug>.md` front matter 含 `slug`、`status`、`tasks`；`specs_index.md` 删除。|
|ADR 可并行追加|`docs/blueprint/decisions/YYYYMMDD_<slug>.md` 一条一文件；并行链不改同一文件。|
|spec 读取闭环|task 模板有“关联 spec”；task-create 填路径；review prompt 注入 spec 路径。|
|入口完整|done 未合并 task 可单独执行 `/task-integrate`；初始化一条命令完成导出、`git init`、hooks、来源登记。|
|文档只描述现状|产物文档不再用“彻底废除”“本轮未运行”等依赖变更史或审阅轮次的表述。|

### 2.2 非目标

- 不改变“一 task 一 commit”（L53）。
- 不改变 Git 原生 merge 事务与 merge 单独授权。
- 不改变实体终生原位流转（L59）；只取消 specs 归档，review 采用保留最近 5 个的清理策略。
- 不改变 task worktree 位于仓外同级的约定。
- 不引入新的调度服务、看板后端或跨机器状态。
- 不承诺分发瘦身、tag 发布或外部工具安装。
- 不删除或改写消费仓自有历史，除非迁移映射明确且用户批准。

## 3. 现状与约束

### 3.1 事实基线

|项|现状|
|---|---|
|本机状态|`docs/runtime/`（ledger、goal、audit）、`.scratch/`（图/同步事务、来源缓存、状态行）、`.repo_template/sync_state.json`、Git common dir（锁、`info/exclude`）、worktree 私有 Git 目录（finish journal）。|
|worktree 局部状态|`docs/runtime/execution_context.json` 当前在 worktree 工作区，随 `INCLUDE_DENY` 排除。|
|模板管理 archive|`docs/archive/specs/` 迁回 `docs/specs/`；`docs/archive/reviews/` 与 `docs/reviews/` 合并后只保留最近 5 个 review，旧目录删除；消费仓另有项目自有 archive 内容。|
|spec 索引|`docs/specs_index.md`，严格单表 schema。|
|ADR|`docs/blueprint/decisions.md` 单文件追加。|
|验证|`testing.md` 定义命令；状态机只校验格式，finish 只读 `progress.md` 摘要。|

### 3.2 不变量

1. task/pending/spike 的规范路径在本批保持不变；不得引入第二地址。
2. task 状态权威仍是 `progress.md` front matter；派生视图不改写状态。
3. 合并事实仍由 Git 与 handoff 共同证明；本批不删除 ledger 的 integrate 前提。
4. 迁移必须先备份、可回滚；不得对未备份的真实消费仓直接试跑。
5. Python 3.9 仍是最低版本；新增代码不得使用 3.10+ 语法或库。
6. 安全默认：任何无法解析、冲突或缺失的迁移输入都停止，不静默跳过。
7. 消费仓自有文件默认属用户所有；迁移脚本只能改写明确归属模板的路径。

### 3.3 本批采用的简版信任模型

- 使用形态：单用户、单机、单主 clone；行为主体是用户自己的 agent。
- 对手模型：会出错、会跳步、会中途崩溃的 agent，不是恶意者。有仓库写权限的人可以直接改脚本或使用 `--no-verify`，本批不针对蓄意伪造提供机械防御。
- 因此：
    - 不可逆文件操作必须 fail-closed 并有测试。
    - 测试/黑盒由 task-work 与 reviewer 执行；finish 不重复执行，也不新增机械测试门禁。
    - 配置变更必须可见。
    - 崩溃恢复只承诺单次中断可诊断、可人工恢复，不承诺双重故障自动续滚。
    - 隐私扫描沿用现有 `conventions.md`「路径隐私」边界；WP2 落地后该契约移到 `repo/.repo_template/docs/architecture.md`「路径隐私」，不为罕见编码继续扩张范围。

## 4. 目标架构

### 4.1 状态布局

```text
$GIT_COMMON_DIR/repo_template/
├── ledger.jsonl
├── tasks_audit.log
├── goal_queue.json
├── statusline_workdir.jsonl
├── sync_state.json
├── graph_transaction/
└── sync_transaction/

$GIT_COMMON_DIR/
├── repo_template_id.lock
└── repo_template_control.lock

<worktree gitdir>/repo-task-finish/          # 现有 finish journal
<worktree gitdir>/repo-task-execution.json   # execution_context 新位置
```

两把锁保持在 `$GIT_COMMON_DIR/` 根，不迁入 `repo_template/`，避免无收益的锁路径迁移。

`.scratch/` 只保留：

- review prompt 渲染结果；
- `repo_template_sync_src` 来源缓存；
- issue 草稿与一次性实验。

`.scratch/` 中的图事务、同步事务与备份全部迁出；`repo-cleanup` 不再需要为事务避让 scratch。

### 4.2 文件所有权

```text
AGENTS.md
├── 项目区（消费仓所有）
│   ├── 项目介绍
│   └── 项目自有目录规则
└── 模板区（同步整块替换）
    ├── 模板工作流目录规则
    └── 开发原则
```

- 模板区由 `<!-- repo-template:begin -->` 与 `<!-- repo-template:end -->` 包围。
- `conventions.md` 只保留项目语言、框架、schema 落点等约定；原子写与路径隐私说明移入 `repo/.repo_template/docs/architecture.md` 的 `## 原子写与文件事务`、`## 路径隐私` 两节。
- `.gitignore` 模板规则放入 `# repo-template:begin` / `# repo-template:end` 托管块；块外规则永不被同步删除。

### 4.3 文档与索引

```text
docs/
├── blueprint/
│   └── decisions/
│       └── 20261110_state_root.md
├── specs/
│   └── <slug>.md                  # front matter: slug/status/tasks
├── reviews/
│   └── review_*/                  # 只保留最近 5 个
├── tasks/                         # L59 原位
├── pending/                       # L59 原位
├── spikes/                        # L59 原位
├── findings/                      # L59 原位
├── handoff.md
└── archive/                       # 仅消费仓自有历史；模板不再管理
```

### 4.4 门禁与证据

- `docs/blueprint/testing.md` 仍是项目门禁唯一配置源。
- `test_cmd` 与 `blackbox_verify` 保持现有执行位置：task-work 实施阶段执行并写 `progress.md` 摘要，reviewer 独立复跑。
- `finish` 不执行 `test_cmd`，不新增验证状态文件；仍按现有 preflight、review 门禁与进度摘要收尾。
- `integrate` 按现有 skill 做合并后验证；本方案不改变其执行方式。
- 不引入独立的 `task.py verify`、`repo-task-verify.json` 或“验证指纹”。`review_scope_fingerprint` 继续只用于绑定 review 内容与最终提交。

## 5. 工作包

### WP1 S4 1–4：本机状态根

**目标**：主仓状态、worktree 状态、草稿缓存三类分离；`docs/runtime` 从工作区消失。

**改动范围**：

|模块|改动|
|---|---|
|`repo_task/context.py`|新增 `common_state_dir`、`ledger_path = common/ledger.jsonl`、`audit_path = common/tasks_audit.log`、`goal_path = common/goal_queue.json`、`statusline_path = common/statusline_workdir.jsonl`；移除 `runtime_dir` 或改为只读兼容别名。`RepoContext` 构造时解析 Git common dir。|
|`repo_task/locks.py`|保留现有 `git_common_dir()` 与两把锁；锁文件名不变，仍位于 common dir 根。|
|`repo_task/goal.py`|`queue_path()` 改用 `repo.goal_path`；提示词中的 `docs/runtime/goal_queue.json` 改为新路径。|
|`repo_task/statusline.py`、`track_worktree.py`|ledger 与 `statusline_workdir.jsonl` 改读 common 状态目录。|
|`repo_task/attempts.py`、`recovery.py`|`execution_context.json` 改读写 `<worktree gitdir>/repo-task-execution.json`；`validate_execution_context()` 与 recovery 同步。|
|`repo_task/commit_txn.py`|把私有 `_git_dir()` 提升为共享 helper（或抽到 context），供 execution context 与 finish journal 解析 worktree gitdir；finish journal 格式不变。|
|`repo_task/worktrees.py`|删除 `INCLUDE_DENY` 中的 `docs/runtime`；worktree 链接不再排除该路径。|
|`repo_task/file_transaction.py`|`SYNC_TRANSACTION_DIR`、`GRAPH_TRANSACTION_DIR` 改为 common 状态目录内的绝对路径；`open_transactions()` 随之。|
|`template_sync/context.py`、`state.py`|`sync_state.json` 路径改为 common 状态目录；从 `PROTECT_NAMES` 去掉旧路径；`init`、`write_state`、legacy-state 迁移同步。|
|`template_sync/transaction.py`、`skills_links.py`、`apply.py`|同步事务目录改用 common 状态目录；备份与回滚不依赖 `.scratch`。|
|`repo_cleanup.py`|删除 `docs/runtime` 硬保护、`docs/archive/tasks_audit.log` 保护、未结束事务控制面锁；scratch 清理不再检查事务目录。|
|`export_template.py`|删除 `docs/runtime/` 不可分发判定。|
|`.gitignore`|删除 `docs/runtime/` 与 `.repo_template/sync_state.json` 规则。|
|`.worktreeinclude`|删除对 `docs/runtime/` 的说明。|
|`repo/.repo_template/docs/usage.md`、`architecture.md`、`recovery.md`|状态位置、备份清单、恢复入口全部改为新路径。|

**实施步骤**：

1. 在 `RepoContext` 内集中定义 common 状态目录与全部派生物；先完成代码路径切换，跑现有工厂测试。此步不改变磁盘布局，测试用旧 fixture 会先失败，按新路径更新。
2. 新增 `repo_sync.py migrate state-root [--write]`：
    - 预检：主仓无活跃会话、无已登记 worktree、无 done 未合并分支；目标 common 目录不存在或为空；源 `docs/runtime`、`sync_state.json`、`.scratch` 事务目录可读。
    - 备份：`docs/runtime/`、`.repo_template/sync_state.json`、`.scratch/repo_sync_rollback/`、`.scratch/task_graph_transaction/` 逐文件复制到持久事务备份目录，保留 mode 与字节。
    - 移动：ledger 改名 `ledger.jsonl`；审计日志、goal 队列、状态行绑定、sync state、图/同步事务及备份进入 common 目录。前置已要求无 worktree，因此没有 `execution_context.json` 需要迁移；代码切换后新 worktree 直接写私有 Git 目录。若发现残留 worktree 的 `docs/runtime/execution_context.json`，报告并停止，不静默删除。
    - 校验：迁移后读取 ledger、goal、sync state，逐项比对；事务备份可打开；旧路径为空或不存在。
    - 失败：使用文件事务回滚；`.git` 内新建目录也要清理。
3. 更新 `repo_cleanup.py`：去掉事务避让和 `docs/runtime` 特例；保护清单只保留仍需保护的用户文件。
4. 更新 `sync.md`、`recovery.md`、`usage.md` 的迁移与备份说明：备份对象改为 Git common dir、worktree 私有 Git 目录、`sync_state.json`；`.scratch` 明确为可重建缓存。
5. 新增测试：
    - 迁移 dry-run 零写入；`--write` 后 ledger、goal、audit、sync state 字节一致。
    - 迁移中断在移动第 2 项时回滚，旧布局完整。
    - `repo_cleanup` 在同步事务未结束时不再依赖 `.scratch` 事务目录，仍不删除 common 状态。
    - worktree 内 `run-begin`/`finish`/`run-close` 全部使用新 execution context 与 ledger 路径。
    - 删除 `.git/repo_template/` 后 `ledger import` 仍能按 recovery 规程重建；该测试只验证现有 import，不改变其语义。

**验收**：

- `git status --ignored` 不再出现 `docs/runtime/`。
- `.scratch/` 中不存在 `repo_sync_rollback` 与 `task_graph_transaction`。
- 主仓与 worktree 执行 `task.py list`、`view`、`effective-status`、`recovery` 结果与迁移前一致。
- 同步事务中断后 `repo-cleanup` 清理 scratch，事务仍能从 common 目录恢复。
- finish 事务、run-close、integrate 在 worktree 私有状态删除后仍给出明确恢复诊断，不静默丢状态。

**风险**：

- `git worktree remove` 会删除 worktree 私有 Git 目录，其中的 `execution_context` 与 finish journal 一并消失。run-close 必须先完成 terminal/report/finish 再删除 worktree；恢复路径要明确“worktree 已删且事务未完成”时停止。
- `.git` 不随普通文件备份或 clone；迁移与迁机备份清单必须显式包含 common dir。
- `.scratch` 事务移出后，`repo-cleanup` 不再持控制面锁。若清理与同步并发，删除 `repo_template_sync_src` 可能使当前同步失败；数据不损坏，但需要重跑。若不能接受，保留只针对来源缓存的轻量锁。
- Windows 路径与锁文件行为需在 Python 3.9/3.14 下各跑一次状态迁移测试。

### WP2 S5 2–4：文件所有权

**目标**：模板内容同步时整块替换，消费仓内容不被误改；`AGENTS.md`、`conventions.md`、`.gitignore` 三处边界可机械判断。

**改动范围**：

|模块|改动|
|---|---|
|`repo/AGENTS.md`|拆为项目区与模板区；模板区用 `<!-- repo-template:begin -->` / `<!-- repo-template:end -->` 包围；保留原有项目介绍与项目目录规则。|
|`template_sync/context.py`|新增 `AGENTS_TEMPLATE_BEGIN/END` 标记常量；`AGENTS_MERGE_HEADING` / `AGENTS_FORCE_HEADING` 退役。|
|`template_sync/agents_merge.py`|删除语义合并与 `_replace_agents_force`；改为“项目区原样保留 + 模板区整块替换”；缺少标记时 fail-closed 并提示迁移。|
|`template_sync/apply.py`、`status_plan.py`|删除 `--agents-file`；status/plan 展示标记缺失、模板区差异；apply 只替换模板区。|
|`template_sync/skills_links.py`|同步入口说明同步调整。|
|`template_sync/shared_files.py`|`merge_gitignore` 改为托管块替换：块外规则保留，块内整块替换；保留 prompt 拦截与去重。prompt 拦截只作用于模板规则，不删除消费仓块外规则。|
|`repo/.repo_template/docs/sync.md`|删除语义合并协议，改为两区协议与迁移步骤。|
|`repo/.repo_template/docs/usage.md`|「工具链路径与写权」只保留 `AGENTS.md` 未覆盖的工具链内部路径；「原子写」保留 helper 一行指针；「路径隐私审计」只保留 `task.py privacy-audit --ref HEAD` 命令。|
|`repo/.repo_template/docs/architecture.md`|新增 `## 原子写与文件事务`：tmp + fsync + `os.replace`、`repo_task.documents.atomic_write_text`、必须原子写的文件与事务边界；新增 `## 路径隐私`：扫描器、编码、`FIXED_PATH_EXCEPTIONS`、finish/pre-commit/merge/审计各入口边界。|
|`repo/docs/blueprint/conventions.md`|删除「原子写」「路径隐私」两节；只留一句指针：模板实现见 `.repo_template/docs/architecture.md`，本文只写项目约定。|
|`template_sync/conventions_migration.py`|改造为一次性迁移：把旧 conventions 中的模板段落移出；消费仓完成迁移后删除模块。|

**实施步骤**：

1. 定义 AGENTS 两区标记与迁移规则。迁移脚本读取现有 `## 目录与读写规则` 表，按模板已知路径识别模板行，其余行保留到项目区；不能机械判断的行全部保留并报告人工确认。`## 开发原则` 与模板目录行进入模板区。
2. 更新 `agents_merge.py` 与 `apply`：
    - 模板区整体由模板生成；
    - 项目区从消费仓读取后原样拼回；
    - 标记缺失、重复、顺序错误均拒绝；
    - 不再接受 agent 语义合并文件。
3. 定义 `.gitignore` 托管块 `# repo-template:begin` / `# repo-template:end`。迁移脚本把当前模板规则移入块内；块外重复规则保留，不做自动删除，报告确认。
4. 拆分 `conventions.md`：完整原子写与路径隐私契约写入 `repo/.repo_template/docs/architecture.md` 对应两节；`usage.md` 只留命令与指针；消费仓文件只留项目约定。迁移脚本移出模板段落，消费仓自定义内容保留并报告。
5. 在工厂测试的消费仓副本 fixture 中检查项目介绍、项目目录行、项目自定义 conventions、gitignore 规则均未丢失。
6. 更新 contract 与 factory 测试，覆盖标记缺失、块外规则、重复应用、消费仓自定义内容。

**验收**：

- 改动模板区一条规则后，同步一个消费仓副本不需要人工或 agent 合并。
- 项目区内容、项目自有 gitignore 规则、conventions 项目约定在同步前后逐字节一致。
- `AGENTS.md` 缺标记时 sync status/plan/apply 全部拒绝并给出迁移命令。
- `--agents-file` 及语义合并测试删除；不存在旧入口残留。
- `conventions.md` 不再包含模板内部实现说明；`repo/.repo_template/docs/architecture.md` 的 `## 原子写与文件事务` 与 `## 路径隐私` 是唯一完整契约。

**风险**：

- 消费仓 AGENTS 的项目行与模板行混排，自动识别可能误判；迁移必须 agent 复核，不能只看脚本通过。
- 消费仓可能自行改过 `## 开发原则`；迁移会以模板为准，迁移报告列出被替换的旧内容。
- `.gitignore` 块外规则若与模板规则冲突，保留消费仓规则并报告，不自动删除。

### WP3 S6 3–5：归档、spec 索引与 ADR

**目标**：模板不再维护 specs 归档；review 只保留最近 5 个；spec 有 front matter 与派生清单；ADR 一条一文件，可并行追加。

**前置**：迁移脚本假定旧布局已先执行现有 `repo_sync.py migrate entity-storage`，把 `docs/archive/tasks` 等旧布局转入 L59 的 `docs/tasks`、`docs/pending`、`docs/spikes`。本方案不重复实现该迁移，只在其后追加 specs/ADR 迁移与 review 裁剪。

**WP3 内部顺序**：先完成 WP3.0 的隐私比较器与审计边界；再实现可解析 `docs/specs` 与 `docs/archive/specs` 的 specs 模块；再移动 archive specs、裁剪 review；再补 front matter 与派生索引；最后由 `privacy-audit` 编排层使用 specs 模块计算 retired spec 排除列表。不要先删除 archive 再补解析器。

#### WP3.0 隐私比较器与审计边界（S5/S6 迁移的前置）

**问题**：现有 `scan_privacy_changes` 按路径比较，`_binary_changes` 使用 `--no-renames`。文件从 `docs/archive/` 移到 `docs/tasks/`、`docs/specs/`、`docs/reviews/` 后，整份文件被当成新增内容扫描。S5/S6 迁移 commit 移回的 retired spec 与 review，以及之后任何移动历史文稿的提交都会被拦；entity-storage 移入 `docs/tasks/` 的历史 task 也会让 `privacy-audit` 不可能通过。entity-storage commit 本身在旧 hook 下提交，须单独跳过 hook 并按重命名摘要核对。

**改动**：

1. 比较器识别重命名：
    - 删除 `privacy._binary_changes` 的 `--no-renames`，改用 `-M` 分类。
    - `scan_privacy_changes` 通过 `git diff --name-status -M` 建立 `old_path -> new_path` 映射。
    - 新路径命中 rename 时，以旧路径的 blob 作为 `original` 做行级比较；只有新增/修改行进入扫描。
    - 精确移动且内容不变：0 条新增，不阻断。
    - 移动并补 front matter：只扫描新增 front matter 行。
    - rename 检测失败（内容改动过大、Git 不识别）时按新增处理；这是保守路径，不是静默跳过。
2. 全量审计在现有范围上只做排除，不改为白名单：
    - 现有范围不变：全部已跟踪的非豁免文本，包括 `src/`、`tests/`、`docs/blueprint/`、`docs/findings/`、`docs/pending/`、`docs/spikes/`、active task 目录、active specs、保留的最近 5 个 review 与仓根文档。
    - 在此基础上排除 done/dropped task 目录与 retired specs。
    - 保留 `PRIVACY_EXCLUDE_PREFIXES = ("docs/archive/", ".scratch/")`；消费仓 `_pre`、research 等冻结历史继续豁免。
    - `repo_task/privacy.py` 不 import `repo_task/specs.py` 或 `repo_task/store.py`；由 `task.py privacy-audit` 编排层计算需排除的 task/spec 路径列表，传给扫描函数。
3. 格式化：
    - `md_format.py` 继续保留 `docs/archive/` 黑名单；消费仓自有 archive 不被格式化。
    - `staged_md()` 从 `--diff-filter=ACMR` 改为 `AM`：纯 rename 与 rename+modify 都不自动格式化，被移动的历史 task/spec/review 不重排。
    - 该过滤是日常 hook 的永久行为：日常“改名并编辑”的 Markdown 也不再由 hook 格式化，需要时手动运行 `md_format.py --changed`。`usage.md`「命名与格式」写明这一缺口。
    - 原位补 front matter 的 active spec 属于修改（M），hook 会整文件格式化。迁移承诺的是“不重排被移动的历史文稿”，不是“不格式化任何既有文件”。
4. 验收口径：
    - S5/S6 迁移结果在 apply 安装的新 hook 下直接通过 pre-commit 隐私门禁。
    - 旧布局 fixture 中的 entity-storage 提交在旧 hook 下跳过提交，用重命名摘要核对只含纯移动与工具预期改写；纯移动不新增文本行，工具只写状态字段与仓内相对路径，不另做隐私扫描。
    - `privacy-audit` 不要求迁移后为 0；迁移仓可运行一次记录基线，旧布局无法计算同一范围，不与迁移前比较。
    - 工厂测试覆盖：历史 task 整目录移动 0 泄漏、移动+front matter 只报新增行、archive spec 移回同样处理、消费仓 archive 内容仍豁免、`src/` 等非 task/spec 路径仍在全量审计内。

#### WP3.1 取消 specs 归档，review 保留最近 5 个

**范围**：

- `docs/archive/specs/` -> `docs/specs/`，补 front matter，`status: retired`。
- `docs/archive/reviews/` 与 `docs/reviews/` 合并评估；只保留最近 5 个 `review_*` 目录，较旧目录从工作区删除，Git 历史保留。
- `repo-hygiene` skill 与 `repo_hygiene.py` 删除；不再提供日常 prune 脚本。同步清理以下入口与测试，不能只删脚本：
    - `repo/.repo_template/skills/registry.json` 的 `repo-hygiene` 条目与调用图引用；
    - `.opencode/commands/repo-hygiene.md` 与 `.agents/skills`、`.claude/skills` 下的软链；
    - `usage.md` 的 skill 表（由 `skill_registry.py --write` 重新生成）与 `repo-hygiene` 相关说明；
    - `factory_tests/test_repo_hygiene.py` 及相关 fixture；
    - `AGENTS.md`、`architecture.md`、`sync.md`、`recovery.md` 中指向 `repo-hygiene` 的现行引用。
- 消费仓自有 `docs/archive/` 内容（研究、蓝图、旧计划、`_pre` 等）保留原处，模板不删除。
- `AGENTS.md`、`usage.md`、`architecture.md`、`.gitignore`、`export_template.py`、`repo_cleanup.py` 更新；`docs/archive` 行改为“消费仓自有历史区，模板不管理”。

**review 保留规则（一次性迁移）**：

1. 同时扫描 `docs/reviews/review_*` 与 `docs/archive/reviews/review_*`。
2. 目录名必须含 `YYYYMMDD` 日期；解析失败的目录停止并列出，不按 mtime 猜测。
3. 按日期倒序，保留最新 5 个；同日期按目录名排序。
4. 最新 5 个必须在 `docs/reviews/`：原本在 `docs/archive/reviews/` 的移动到 `docs/reviews/`。
5. 更旧目录从工作区删除：
    - 只用文件删除，不动 Git index；迁移事务只回滚文件，不回滚 index；
    - 整目录删除，包括被忽略的 `_meta/` 过程文件；`_meta/` 从未入库，删除后无法从 Git 恢复，dry-run 报告单独列出将删除的 `_meta/` 路径供审批；
    - 已跟踪内容在提交时由用户审批后 `git add -A`/`git add -u` 暂存；
    - 未跟踪目录报告并跳过，由用户决定；
    - 删除前扫描 `docs/tasks/`、`docs/blueprint/`、`docs/handoff.md` 是否引用该路径；有引用则保留并报告。
6. 删除是迁移 commit 的一部分；一次性迁移脚本只做文件操作，不 commit、不 `git rm`。commit 前用户审批暂存。

**review 保留规则（日常）**：

1. 只检查“本次提交是否新增 `docs/reviews/review_*` 顶层目录”：取 `git diff --cached --name-only -z --diff-filter=A -- docs/reviews/review_*` 的第一层目录，去掉 HEAD 中已存在的目录（`git ls-tree --name-only HEAD docs/reviews/`；HEAD 未出生时视为空）。往已有 review 目录追加文件（如第二轮报告）不算新增。
2. 没有新增 review 顶层目录时直接放行；已有超限状态不拦无关提交、不拦 finish。
3. 存在 `MERGE_HEAD` 时跳过；integrate 的 merge commit 不受该规则阻断。
4. 按 index 计数：`git ls-files -z -- docs/reviews/review_*`，取 `docs/reviews/review_*` 的第一层目录去重；不按工作区目录计数，避免未提交目录和 `git rm` 后残留影响。
5. count `<= 5` 放行。
6. count `> 5` 时，按目录名日期倒序列出应删除的旧目录，删除建议同时清掉该目录下被忽略的 `_meta/`；每个候选先扫描 `docs/tasks/`、`docs/blueprint/`、`docs/handoff.md` 引用，被引用的目录不列入删除建议，只报告引用位置。若所有超出项都被引用，仍然阻断并说明需先处理引用。
7. 目录名不含 `YYYYMMDD` 时阻断，列出无法排序的目录，要求先改名或人工处理。
8. `_meta/` 过程文件仍由 `.gitignore` 忽略；review 目录本身参与数量统计。
9. 不检查 `docs/archive/reviews`；消费仓自有 archive 不参与日常保留规则。
10. hook 可被 `--no-verify` 绕过；这是卫生规则，不是交付正确性门禁。

**specs 迁移规则**：

1. 预检：已执行 entity-storage 迁移；`docs/archive/specs` 可读。
2. 同名 slug 已存在于 `docs/specs/`：不覆盖，停止并要求人工合并。
3. 目标不存在：移动文件，添加/保留 front matter，`status: retired`。
4. 移动后 `docs/archive/specs/` 为空才删除目录。
5. `docs/archive/handoff.md` 与 `docs/archive/tasks_audit.log` 已由 entity-storage 迁移处理；本步骤不重复。

**消费仓自有 archive**：

- 不自动删除；
- 迁移报告列出剩余路径，由用户决定保留或手工迁移；
- `docs/archive/` 目录若仍有内容则保留，`AGENTS.md` 与 `usage.md` 明确它是消费仓自有历史区。

**隐私与格式化边界（必须同时落地）**：

- 按 WP3.0：`privacy-audit` 保持现有全量范围，只额外排除 done/dropped task 目录与 retired specs；`docs/archive/` 与 `.scratch/` 继续豁免。
- 保留 `PRIVACY_EXCLUDE_PREFIXES` 中的 `docs/archive/`；消费仓 `_pre`、research 等冻结历史不进入审计。
- `repo_task/privacy.py` 保持路径/内容比较器，不 import `repo_task/specs.py`；由 `task.py privacy-audit` 编排层计算需排除的 done/dropped task 与 retired spec 路径列表，再传给扫描函数。
- 保留的 review 若含历史家目录路径，先按评审报告规范修正或删除该 review，不为历史证据新增例外。
- `md_format.py` 保留 `docs/archive/` 黑名单；hook 只格式化新增与修改（`AM`）的文件，被移动的历史 task/spec/review 不重排。
- 不把 `docs/reviews` 加入 `SCOPE_FINGERPRINT_EXCLUDES`；删除或修改 review 目录会进入 review scope，必须被评审。
- `monitoring.SCOPE_FINGERPRINT_EXCLUDES` 维持现有排除项；本方案不新增 `docs/reviews` 排除。
- `_id_scan.py` 删除 archive 布局校验与 `docs/archive` 白名单；改为扫描 `docs/tasks`、`docs/pending`、`docs/spikes`、`docs/findings`。
- `repo_cleanup.py` 删除 `docs/archive/tasks_audit.log` 保护与 archive 特例；`docs/` 下仍只允许 OS/编辑器垃圾。
- `.gitignore` 保留 `docs/reviews/review_*/_meta/` 与 `docs/archive/reviews/review_*/_meta/` 两条规则；消费仓自有 archive 仍可能存在 review 目录。
- `export_template.py` 当前不按 `docs/archive` 过滤，只排除 `docs/runtime/` 与 `sync_state`。S4 后删除 `docs/runtime/` 判定；导出源是模板仓，消费仓 archive 不进入产物。

**pre-commit hook 实现**：

- 在 `repo/.repo_template/hooks/pre-commit` 的现有 Python 入口中增加检查函数；不新增独立 CLI。
- 只在本次提交新增 HEAD 中不存在的 `docs/reviews/review_*` 顶层目录时运行；判定方法同日常规则第 1 条。
- 存在 `MERGE_HEAD` 时跳过；integrate 的 merge commit 不受阻断。
- 按 index 计数：`git ls-files -z -- docs/reviews/review_*`，第一层目录去重；不按工作区目录计数。
- 目录排序使用目录名中的 `YYYYMMDD`；不读 mtime。
- 超出 5 个时先查引用（`docs/tasks/`、`docs/blueprint/`、`docs/handoff.md`）；被引用目录不列入删除建议，只报告引用位置。
- 输出格式：
    ```text
    pre-commit: 本次新增 review 后 docs/reviews 有 7 个 review，超过 5 个。
    可删除的最旧目录：
      git rm -r -q -- docs/reviews/review_20260101_120000 && rm -rf -- docs/reviews/review_20260101_120000
      git rm -r -q -- docs/reviews/review_20260102_090000 && rm -rf -- docs/reviews/review_20260102_090000
    被引用、不能删除：
      docs/reviews/review_20260103_090000（docs/tasks/t001_foo/progress.md）
    ```
- 检查在隐私/编号校验之后、Markdown 格式化之前执行；失败直接返回 1。
- 不修改工作区、不删除目录、不自动 `git rm`。
- 不把 `docs/reviews` 加入 review scope 排除；删除 review 的提交必须经过评审。
- 消费仓已有非模板 `core.hooksPath`（如 husky/lefthook）时模板 pre-commit 不会自动运行。必须在项目 hook 中加入 WP5 的接入片段；`healthcheck` 报告未接入状态。未接入的仓不强制 review 数量，但视为 hooks 未安装。

#### WP3.2 spec front matter 与派生索引

**front matter schema**：

```yaml
---
slug: example_slug
status: active
tasks: [t001, t002]
---
```

- `slug` 必须与文件名一致，匹配 `^[a-z][a-z0-9_]*$`。kebab-case 等消费仓漂移会让迁移停止并列出，不自动改名。
- `status` 只允许 `active` / `retired`。
- `tasks` 至少一个 `tNNN` 或 `baseline`；不得重复。归档 spec 在索引中无对应行时默认 `[baseline]`。
- 不保存 `updated`，`specs list` 也不输出更新时间；没有调用方需要，避免迁移后全部变成迁移日期。
- 正文 H1 保留为显示标题。

**实现**：

- 新增 `repo_task/specs.py`（或把 `specs_index.py` 改造为 specs 模块）：
    - 扫描 `docs/specs/*.md`；
    - 解析 front matter；
    - 校验 slug 唯一、文件名一致、task token 合法；
    - 提供 `active_specs()`、`task_specs(tid)`、`validate_specs(root)`。
- `repo_task/finalization.py`：
    - 删除 `parse_specs_index()`；
    - 改为“当前 tid 出现在任一 active spec 的 `tasks` 中，或 progress.md 写 `- specs_index: not_applicable: 原因`”；
    - 错误信息列出候选 spec 路径与字段。
- `repo_task/specs_index.py` 的表格解析逻辑在删除前移入 `template_sync/specs_index_migration.py`，保证迁移仍能读取旧索引；`specs_index_migration.py` 随产物分发，删除时机另行评估。`tests` 中相关 schema 用例改为 spec front matter 用例。
- `task.py` 新增只读 `specs list --json`：
    - 默认只列 active，`--all` 含 retired；
    - 每项字段：`slug`、`path`、`status`、`tasks`、`title`；不输出 `updated`。
- `render_review_prompts.py` 注入关联 spec 路径；不注入全文，避免上下文膨胀。

**迁移**：

1. 读取现有 `docs/specs_index.md`，逐行建立 `slug -> tasks` 映射；索引中的最后更新时间忽略。
2. 对 `docs/specs/<slug>.md` 写 front matter；已有 front matter 的字段逐项校验，缺字段用索引补齐，冲突停止。
3. 索引中不存在但文件存在的 spec：标记 `status: active`，`tasks: [baseline]`，报告人工确认。
4. 已归档 spec 移回后：`status: retired`，保留原 task 列表；无索引行时 `tasks: [baseline]`。
5. 迁移后删除 `docs/specs_index.md`；旧索引进 Git 历史。

#### WP3.3 ADR 一条一文件

- 目录：`docs/blueprint/decisions/`。
- 迁移生成的 ADR 文件名：`YYYYMMDD_NNN_<slug>.md`。`NNN` 来自旧 `## NNN`，保证唯一；没有 NNN 的条目停止并报告，不猜编号。
- 无编号条目的旧布局按停止处理：迁移不猜编号，也不把 H3 日期条目自动映射为 ADR；由用户补齐 NNN 后复跑。
- 新建 ADR 文件名：`YYYYMMDD_<slug>.md`，与仓内 `YYYYMMDD` 惯例一致；slug 使用 snake_case。
- 中文标题用 `_id_scan.slug_from_title()` 派生；结果为 `entry`、空或冲突时，新建 ADR 追加 `_2`，迁移 ADR 依赖 NNN 保证唯一。
- 旧 `## NNN 标题（YYYY-MM-DD）` 的 NNN 保留在文件名、正文 H1 与可选 front matter `legacy_id`，不断引用。
- 每条 ADR 只含一个决策。front matter 只允许可选 `supersedes: [<filename>]`；ADR 是否被取代由其他文件的 `supersedes` 引用派生，不在旧文件写 `status: superseded`，也不改写旧文件。
- 旧 `替代：NNN` 字段迁移为 `supersedes: [YYYYMMDD_NNN_<slug>.md]`；无则省略。无法解析的 NNN 停止并报告。
- 日期取标题中的 `YYYY-MM-DD`；没有日期时运行 `git log --reverse -S'## NNN' --format=%ad --date=short -- docs/blueprint/decisions.md` 取首次进入 Git 的日期。取不到时停止并要求人工填写，不用迁移日期冒充。
- 迁移：
    - 读取现有 `docs/blueprint/decisions.md`，按围栏外的 `## NNN` 条目拆分。拆分识别围栏代码块（复用 `ctx.FENCE_RE`），代码块内的格式示例（如 `## NNN 标题（YYYY-MM-DD）`）不算条目；
    - 首个围栏外 H2 之前的前言不生成 ADR，内容列入迁移报告；围栏外任何不匹配 `## NNN` 的 H2 都停止；
    - 每条生成一个文件，NNN、标题、日期、正文原样保留；
    - 无法判断边界的条目停止并报告；
    - 迁移后删除 `decisions.md`，在 `architecture.md` 指向新目录。
- 更新 `AGENTS.md`、`usage.md`、`architecture.md`、review prompt 中的路径引用。

#### WP3.4 S6.6 部分

- `dispatch_ledger.jsonl` -> `ledger.jsonl`：由 WP1 状态迁移原子改名；所有读写点、测试、文档同步。
- `handoff.json` 保持原名；不创建 `receipt.json` 旧名兼容文件。
- `docs_repo/` 按用户 2026-11-10 裁决改名 `factory_docs/`；工厂笔记路径随之更新。

**验收**：

- entity-storage 迁移后追加 specs 迁移与 review 裁剪；旧布局 fixture 的 dry-run 输出完整映射。无法自动修复的输入（如 kebab-case slug、无编号 ADR）单列并停止，修复后复跑。
- `docs/archive/specs` 回到 `docs/specs`；`docs/reviews` 只保留最近 5 个 review；更旧目录从工作区删除但可从 Git 恢复；消费仓自有 archive 内容零删除。
- `docs/specs_index.md` 删除；`task.py specs list --json` 能列出 active/retired specs 与 task 关联；不输出更新时间。
- `docs/blueprint/decisions/` 每条 ADR 单独文件；并行两条链新增 ADR 无冲突。
- `privacy-audit`、`md_format`、`review_scope`、`_id_scan`、`repo_cleanup` 在 specs archive 取消与 review 裁剪后行为测试通过。
- 产物内 grep `docs/archive`、`specs_index` 无模板引用；消费仓自有 archive 路径可以出现。

**风险**：

- 旧布局的 archive 内容差异大；必须 dry-run 全量，不能按统一模板假设；不直接对未备份的真实仓试跑。
- kebab-case slug 等消费仓漂移按严格 snake_case 停止并列出，不自动改名；消费仓修复引用后复跑。
- 同名 spec 冲突需要人工合并；不能自动覆盖。
- 删除旧 review 是不可逆的工作区操作：已跟踪报告只能依赖 Git 历史恢复，`_meta/` 不在 Git 中，删除后无法恢复；必须 dry-run、列清单、用户审批，且不删除被 task/blueprint/handoff 引用的目录。
- 保留的 5 个 review 若含家目录路径，`privacy-audit` 会阻断；先修正报告或删除该 review，不为历史证据开豁免。
- ADR 拆分是内容迁移；旧 `decisions.md` 结构可能不同，迁移报告逐项列出。无编号条目按预期停止；围栏代码块内的格式示例不算条目，拆分器必须识别围栏。

### WP4 S7：spec 读取闭环

**目标**：task 创建与 review 能读到现行 spec，避免实现只满足 task AC 却破坏既有需求。

**不做**：

- `finish` 不执行 `test_cmd`；`test_cmd` 已在 task-work 实施阶段执行，reviewer 也会独立复跑，finish 再跑会重复。
- `integrate` 保持现有合并后验证方式，不新增机械执行。
- 不新增 `task.py verify`、验证状态文件或验证指纹。
- `blackbox_verify` 保持人工证据，可能是 computer use、人工观察或外部环境操作。

**task-create 与 task 模板**：

- `repo/.repo_template/docs/task_template/task.md` 的上下文区增加一行：
  ```text
  关联 spec：{spec 路径；无关联写“无”}
  ```
- `task-create` 读取 `task.py specs list --json`，选择相关 active spec；无关联时写“无”并说明原因。
- `task.py preflight --creation` 增加机械校验：该行存在；值为“无”时放行；否则路径必须存在于 `docs/specs/` 且 front matter 合法。占位符 `{...}` 由现有 preflight 占位符检查拒绝。
- 现有 backlog task 没有该行时视为“未声明”，不批量改写；`preflight --creation` 只约束新建 task。已有 task 开始执行时缺行按未声明处理，review prompt 不注入 spec。
- `task-combine` 合并后会对目标 task 跑创建有效性检查；目标缺该行时由 task-combine 补写（无关联写“无”），不放宽 `preflight --creation`。
- task 创建后不改写 spec；spec 的长期更新仍由 finalization 负责。

**review prompt**：

- `render_review_prompts.py` 从 task.md 读取“关联 spec”路径。
- prompt 增加：
    - spec 路径；
    - spec 的 H1 与 front matter 摘要；
    - 提示 reviewer 核对实现是否破坏 spec 描述的既有行为。
- 不注入 spec 全文，避免撑大上下文；reviewer 需要时自行读取路径。

**验收**：

- `task-create` 生成的 task.md 有“关联 spec”行；无关联时写“无”。
- `preflight --creation` 对缺失行、未替换占位符、路径不存在、front matter 非法机械拒绝。
- 现有 backlog 缺行按“未声明”处理，不阻断已有 task。
- `render_review_prompts.py` 对有 spec 的 task 注入路径与摘要；缺行或“无”时不注入。
- `finish` 行为不变：不执行 `test_cmd`，不新增验证字段；`task-work` 的测试/黑盒流程不变。

**风险**：

- 既有仓可能有大量 spec，task-create 需要筛选相关项；只列 active spec，retired 不作为默认候选。
- 关联 spec 写错会误导 reviewer；review prompt 同时提供路径，reviewer 可核对 front matter 与正文。
- 这不是测试机械化，不解决 `progress.md` 文字证据问题；该问题本批明确不处理。

### WP5 S8：入口与初始化

**目标**：done 未合并 task 有公开合并入口；初始化与检查各一条命令。

**改动**：

|项|改动|
|---|---|
|`task-integrate`|registry 角色从 `internal` 改为 `public`；SKILL frontmatter 改为用户可调用；无参时只读列出待合并单体与链尾，并请求授权；`task-run` 队尾直接读取同一正文。|
|`task-merge`|改名 `task-combine`：目录、registry、命令薄层、usage、测试、文档同步；不保留旧名别名。|
|`export_template.py`|新增 `--bootstrap`：目标必须不存在或为空；导出后执行 `git init`、安装 hooks、登记来源；输出剩余人工步骤。|
|`template_sync/hooks.py`、`repo_sync.py`|`install-hooks` 检测已有 `core.hooksPath` 时不覆盖，输出 husky/lefthook 接入片段；`healthcheck` 检查 `.husky/pre-commit` / `lefthook.yml` 是否调用模板 hook。|
|`repo_sync.py`|新增只读 `healthcheck` 子命令：检查软链、hooks、来源登记、md_kx、验证策略、占位符残留。|
|`repo/README.md`、`usage.md`、`sync.md`|初始化流程改为两条命令；`task-combine` 新名。|

**实施步骤**：

1. 改 `task-integrate` 角色与 SKILL。无参流程复用 `task.py plan --serial --json` 的分链结果，只列待合并成员与链尾，不自动合并；用户确认后才执行。显式 `/task-integrate {tid}` 或链尾视为对该确切范围的 merge 授权，但仍须列出分支与目标主干；branch/base 与预期不符时停止。该授权不扩大到其它 tid。
2. 改名 `task-merge` -> `task-combine`。运行 `skill_registry.py --write` 更新 usage 表格与 `.opencode/commands`；更新测试与文档；不保留旧入口。
3. `--bootstrap`：
    - 校验目标为空；
    - 调用现有导出逻辑；
    - 在目标执行 `git init`（如未初始化）；
    - 从导出产物调用 `repo_sync.py install-hooks`；已有 `core.hooksPath` 时不覆盖，输出接入片段后停止安装，由用户接入；
    - 调用 `repo_sync.py init --source <来源>`；
    - 失败时停止并输出已完成步骤与回滚指引。
4. hooks 接入片段：
    - husky `.husky/pre-commit`：
      ```sh
      python3 .repo_template/hooks/pre-commit
      ```
    - husky `.husky/commit-msg`：
      ```sh
      python3 .repo_template/hooks/commit-msg "$1"
      ```
    - lefthook `lefthook.yml`：
      ```yaml
      pre-commit:
        commands:
          repo-template:
            run: python3 .repo_template/hooks/pre-commit
      commit-msg:
        commands:
          repo-template:
            run: python3 .repo_template/hooks/commit-msg {1}
      ```
    - `install-hooks` 与 `healthcheck` 只输出片段和检查，不自动修改项目 hook 配置。
5. `repo_sync.py healthcheck`：
    - 名称不用 `doctor`，避免与 `testing.md` 的 `doctor_cmd` 混淆；
    - 只读；
    - 缺项逐条输出修复命令；
    - 不修改任何文件。

**验收**：

- 新会话对 done 未合并 task 执行 `/task-integrate` 可列出候选并完成合并。
- 空目录执行 bootstrap 后，`healthcheck` 除人工步骤外全部通过。
- 非空目录、已有 state、已有 hooksPath 重跑 bootstrap 不覆盖现场。
- 在已有非模板 `core.hooksPath` 的测试副本中，项目 hook 加入接入片段后，模板隐私/编号/review 数量检查生效；`healthcheck` 能识别未接入状态。
- `task-combine` 旧名不再出现在 registry、命令薄层和现行文档；旧任务历史不受影响。

**风险**：

- `task-integrate` 公开后，用户可能绕过 `task-run` 的队列上下文；授权仍由 SKILL 要求显式确认，脚本只做 mechanical gate。
- `git init` 在已有 `.git` 的目录要拒绝或跳过；不能重复初始化。
- 只提供 husky/lefthook 接入片段，不自动修改项目 hook 配置；用户不接入时模板 hook 不生效，`healthcheck` 必须明确报告。

### WP6 S9 1–3：文档与工厂卫生

**目标**：产物文档只描述现状；工厂笔记可读；测试按模块可发现。

**改动**：

1. 产物文档：
    - 删除“彻底废除/不再/取消/退役/旧”等变更史措辞，保留必要的历史裁决链接；
    - “原位流转”只在 `architecture.md`「固定路径契约」定义一次，其余链接；
    - `usage.md`「数据迁移」移入 `sync.md` 或发布说明；
    - “本轮只做静态核对”“本轮未运行迁移演练”改为明确日期与验证范围；
    - 删除 `repo/docs/blueprint/decisions.md` 对 `factory_docs/` 的悬空引用；
    - 改写后重新测量 `usage.md`、`architecture.md` 体积。
2. 工厂笔记：
    - `decision_log.md` 每行只写议题、裁决、落点、状态；实施细节与用例数留在方案和 Git 历史；
    - 更新 L60–L63、L65、L66 的过期状态；
    - 修正 `factory_docs/README.md` 的 `reviews/2026-07/` 路径；
    - 把 `plans/plan_test_suite_performance.md`、`plans/plan_entity_storage_and_archive.md` 移入 `archive/`；
    - 本方案完成后，把本文与宏观审阅处置状态同步记录。
3. 工厂测试：
    - 按模块分目录：`factory_tests/state/`、`sync/`、`workflow/`、`integration/`、`review/`、`docs/`、`support/`；
    - 按被测模块改名，finding/审阅日期只留 docstring；
    - 拆分 `test_repo_sync.py`、`test_task_start_flow.py` 等超大文件；
    - 更新 `pytest.ini` / conftest 的路径与 marker。
    - S9.4 不做：工厂继续使用自身 plans/reviews 流程，不切换到已安装版工具。

**验收**：

- 产物文档 grep “本轮” 无命中，review prompt 的轮次字段除外。
- `repo/docs/blueprint` 无 `factory_docs/` 引用。
- `factory_tests/` 无以审阅日期或 finding 编号命名的文件；测试仍可全量运行。
- `factory_docs/README.md` 路径与 `plans/` 归档状态正确。
- decisions 目录、specs 目录、测试目录的现行引用全部更新。

## 7. 测试策略

### 7.1 工厂全量

```bash
pytest repo/.repo_template/tests factory_tests -q -n auto -p no:cacheprovider --basetemp "$(mktemp -d)"
```

每个 WP 完成即跑全量；不因文档变更跳过。Python 3.9 至少跑一次状态迁移、lock、worktree 私有路径用例。

### 7.2 分层用例

|层|新增或修改用例|
|---|---|
|状态|common dir 路径、迁移 dry-run/write/中断、worktree 私有 execution context、cleanup 不删事务、sync state 迁移。|
|所有权|AGENTS 标记替换、项目区保留、gitignore 托管块、conventions 拆分、缺标记 fail-closed。|
|归档|specs 迁回 `docs/specs`、review 保留最近 5 个、pre-commit 超限阻断、引用保护、同名冲突阻断、消费仓 archive 零删除、privacy/md_format/scope/id_scan/cleanup 行为。|
|隐私|rename-aware 比较：整目录移动 0 新增、移动+front matter 只报新增行、rename 检测失败按新增；`privacy-audit` 只排除 done/dropped task 与 retired spec，`src/` 等其余范围仍扫描；消费仓 archive 豁免；S5/S6 迁移 commit pre-commit 通过。|
|review hook|只有本次新增 HEAD 中不存在的 review 顶层目录才检查，往已有目录追加文件不触发；按 index 计数；`MERGE_HEAD` 跳过；引用目录不提示删除；删除 review 进入 review scope。|
|spec/ADR|front matter schema、派生清单、finish 登记、ADR 并行新增、旧索引迁移；ADR 拆分识别围栏、无 NNN 条目停止、旧“替代”转 `supersedes`。|
|spec 读取|task 模板“关联 spec”、task-create 选择、review prompt 注入、路径不存在拒绝。|
|迁移链|从旧布局 fixture（含旧版 hook）依次跑 `migrate entity-storage` → `--no-verify` 提交并核对重命名摘要 → `migrate state-root` → S5/S6 apply → 新 hook 下提交 → `healthcheck`；断言审计日志路径、entity-storage commit 只含移动与预期改写、review 数量、spec/ADR 结果；另测迁移失败回滚后旧布局可复用。|
|入口|integrate 无参候选、task-combine 改名、bootstrap/healthcheck 幂等。|
|文档|路径引用、`本轮` 禁令、decision_log 状态、测试命名。|

### 7.3 迁移验收

- 旧布局 fixture dry-run 输出完整映射与冲突清单。
- 工厂整链测试：旧布局 fixture -> entity-storage -> state-root -> S5/S6 apply -> healthcheck。entity-storage 写 `docs/runtime/tasks_audit.log` 的顺序耦合必须被用例覆盖；fixture 使用旧版 hook，覆盖跳过旧 hook 提交与重命名摘要核对。
- 迁移失败时按项回滚并保留备份；恢复未完成时给出人工处理诊断。
- 不运行 L71 已否决的“真实仓备份恢复演练”；迁移恢复的证据边界为代码路径与工厂 rollback 用例，不重开该裁决。

## 8. 风险与缓解

|风险|影响|缓解|
|---|---|---|
|S4 状态迁移中断|ledger/goal/audit 丢失或重复|持久事务 + 备份 + 逐文件校验；失败不继续同步。|
|worktree 私有状态随 `git worktree remove` 删除|execution context、finish journal 丢失|run-close 顺序固定；worktree 已删且事务未完成时停止并报告。|
|S5 AGENTS 自动拆分误判|消费仓项目规则丢失|迁移报告逐条列出无法判断的行并保留；人工确认；失败即回滚。|
|S6 specs archive/review 裁剪误删|不可逆内容损失|只处理模板管理路径；review 删除走 dry-run、引用检查、用户审批与文件删除；迁移不修改 index；整仓备份。|
|迁移 commit 被隐私门禁拦|历史 task/spec 移动被当新增，无法提交|rename-aware 比较；全量审计排除 done/dropped task 与 retired spec；S5/S6 commit 走新 hook；工厂用例覆盖。|
|entity-storage commit 跑旧 hook|历史 Markdown 被整文件重排；md_kx 报错即阻断；新隐私比较器不生效|该 commit `--no-verify` 提交；`git diff -M --name-status` 核对只含纯移动与工具预期改写；核对失败不进入下一步。|
|review 数量 hook 拦 finish/integrate|实现或合并被卫生规则阻断|只在本次新增 review 目录时检查；按 index 计数；`MERGE_HEAD` 跳过；引用目录不提示删除。|
|保留 review 含家目录路径|`privacy-audit` 失败|保留的 5 个 review 先按规范修正或删除；不为历史证据开豁免。|
|spec/ADR 迁移冲突|覆盖旧文稿或索引丢失|同名冲突阻断；旧索引进 Git 历史；人工合并后才继续。|
|S7 测试证据仍为文字|`progress.md` 摘要可能失真|本批明确不机械化；task-work 测试、reviewer 独立复跑与 review 门禁仍是现有控制。|
|S7 spec 关联写错|reviewer 被误导|review prompt 提供 spec 路径；`preflight --creation` 校验路径与 front matter；已有 task 缺行按未声明处理。|
|S3 未做，迁移模块常驻产物|产物继续增长|接受；删除迁移模块的时机另行评估，不在本方案内。|
|S2 未做，main 即发布|同步到未验证提交|发布前锁定全量通过的 commit SHA + 发布说明。|

## 9. 提交与验收计划

### 9.1 提交拆分

建议按 WP 独立提交，每个提交可独立验证：

1. `feat(state): 本机状态迁入 Git common dir`（WP1）
2. `refactor(ownership): AGENTS 两区与 conventions 拆分`（WP2）
3. `refactor(specs): 取消 specs 归档并派生 spec/ADR`（WP3）
4. `feat(specs): spec 读取闭环`（WP4）
5. `feat(workflow): 公开 integrate 与初始化入口`（WP5）
6. `docs: 收敛产物文档与工厂测试`（WP6）
7. `chore(migration): 一次性旧布局迁移脚本`

迁移脚本可并入对应 WP 提交；迁移脚本随工厂测试一起验证，不单独规定消费仓迁移步骤。

### 9.2 全局验收

- 工厂全量测试通过；契约测试通过。
- 旧布局 fixture dry-run 输出完整映射；无法自动修复的输入单独列出，修复后复跑。
- 工厂整链测试 `旧布局 fixture -> entity-storage -> state-root -> S5/S6 -> healthcheck` 通过。
- `task-create` 生成“关联 spec”；review prompt 含 spec 路径与摘要。
- done 未合并 task 可通过 `/task-integrate` 合并。
- `AGENTS.md` 模板区改动同步无需人工合并；项目区不变。
- `docs/specs_index.md`、`specs_index` 模板引用清零；`docs/reviews` 只保留最近 5 个 review。
- 整链测试覆盖 entity-storage 重命名摘要核对、S5/S6 迁移后的 pre-commit 隐私门禁与 `privacy-audit` 基线计算。
- privacy、md_format、review scope、id_scan、cleanup 的行为测试通过。
- 产物文档不再依赖审阅轮次或变更史表述。

## 10. 与既有裁决的关系

|裁决|本方案处理|
|---|---|
|L53 一 task 一 commit|保留。|
|L55 同步不执行项目门禁|保留。S7 不改变测试执行位置，finish/integrate 不新增 `test_cmd` 执行。|
|L59 实体终生原位|保留；不引入 `work/` 根，不恢复任务目录搬迁。|
|L68 事务避让|S4 后事务移出 `.scratch`，避让机制不再需要；删除对应 cleanup 特例。|
|L69 授权只记录|保留。S7 不新增 TTY 确认或授权证明。|
|L70 隐私只阻断新增行|保留；`docs/archive/` 继续作为消费仓冻结历史豁免；review 保留最近 5 个并进入审计；不为保留 review 额外开豁免。|
|L71 不做真实仓备份恢复演练|保留；迁移恢复的证据边界为代码路径与工厂 rollback 用例，不重开该裁决。|

## 11. 完成后需要写入 decision_log 的条目

实施完成后按实际裁决与验证结果追加，不在本文预设编号：

1. 本机状态统一到 Git common dir；worktree 状态进私有 Git 目录；`.scratch` 仅草稿与缓存。
2. AGENTS 两区、conventions 拆分、gitignore 托管块；`--agents-file` 与语义合并退役。
3. specs 归档取消；review 保留最近 5 个；pre-commit 超限阻断并提示删除旧目录；privacy 比较器识别 rename，全量审计排除 done/dropped task 与 retired spec；spec front matter、派生索引、ADR 一条一文件。
4. S7 只补 spec 读取；`finish`/`integrate` 不执行 `test_cmd`，测试与黑盒仍在 task-work 与 reviewer 侧。
5. task-integrate 公开；task-merge 改名 task-combine；初始化与检查命令。
6. 产物文档与工厂测试卫生；本批未实施 S1/S2/S3/S4.5/S5.1/S6.1–2。

## 12. 开放项与用户确认

以下不影响开工，但需在对应阶段前确认：

1. 消费仓自有 `docs/archive` 内容是否保留原处；本方案默认保留，不自动迁移。

## 13. 实施记录

- 2026-11-10 首批工厂改造（WP1–WP5 与 WP6 部分）完成；当时记录的“WP1–WP6 工厂侧完成”不准确：WP6 尚缺测试命名收敛、超大测试拆分、工厂笔记归档与目录改名、usage 数据迁移搬迁、退役 gitignore 规则处理、产物文档变更史措辞清理和体积测量。
- WP6 收口完成：测试按被测模块命名并拆分 `test_repo_sync.py` / `test_task_start_flow.py`，新增 `factory_tests/integration/`；`docs_repo/` 改名 `factory_docs/`；两份已实施方案移入 `factory_docs/archive/`；`usage.md` 数据迁移章节迁入 `sync.md`；删除 `docs/archive/tasks_index.json` 退役规则；`docs/archive/reviews/review_*/_meta/` 按计划保留，保留 review 的 `_meta` 原样不动；产物文档去除“本轮/不再/退役”等变更史措辞。
- 2026-11-10：实施审阅项 A1–A4、B1–B5、C1–C4、C5a 已实施；B6 关闭为“工厂只保证 migrate all → apply 路径正确，不规定消费仓铺开流程”；C5b 维持拒绝。
- 消费仓迁移不在本文范围；由用户需要时自行执行，模板不规定铺开流程。
- WP6 文档收敛后测量 `repo/.repo_template/docs/usage.md` 23760 字节、`repo/.repo_template/docs/architecture.md` 15170 字节；`md_format --check` 通过。
- WP6 第 5 项曾删除 `docs/archive/reviews/review_*/_meta/` 规则并让迁移清理保留 review 的 `_meta`；该做法与计划 WP3「`_meta` 跟随 review 生命周期」和第 393 行继续保留规则冲突。现已恢复该 ignore 规则，撤销 5d7bd8b/f78b281 的保留 review `_meta` 清理与引用 fail-closed 逻辑：保留 review 不动，删除 review 整目录删除并在 dry-run 列出 `_meta`。
- `f78b281` / `57f972e` 仅保留其未跟踪 review 不得删除的边界结论；迁移实现已按上述生命周期规则收敛。
- 最终工厂全量：`pytest repo/.repo_template/tests factory_tests -q -n auto -p no:cacheprovider --basetemp "$(mktemp -d)"` → `1571 passed, 1 skipped in 495.22s`。
