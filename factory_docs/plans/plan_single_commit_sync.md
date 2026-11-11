# 单事务单提交同步方案

> 编写时间：2026-11-11T17:32:46+08:00。
>
> 文档性质：待实施的工厂改造方案。依据用户 2026-11-11 裁决：每次同步不论迁移什么、同步什么，全部进入一个 commit 并保留 diff 待审；未经用户允许不得 commit；失败或拒绝时整体回滚，不保留部分结果。
>
> 适用范围：`repo/.repo_template/scripts/template_sync/`、`repo/.repo_template/scripts/repo_task/file_transaction.py`、同步相关 skill 与产物文档、`factory_tests/`。消费仓迁移不在本方案范围。

## 1. 裁决

|项|裁决|
|---|---|
|提交粒度|一次同步 = 一个 `FileTransaction` = 一个 commit；迁移与模板同步不拆分|
|提交授权|写盘后保留未提交 diff；每次同步由用户单独明确批准后 agent 才 commit；不复用批量授权|
|起始工作区|同步开始时工作区与暂存区必须完全干净；commit 只含同步结果|
|迁移数据阻断|agent 只报告并给修复方案；用户批准后修复，修复单独提交（同样须批准），再同步|
|旧归档目录非实体项|旧归档目录中凡不是规范实体的项（任意文件、目录、隐藏项、命名不合规的形似实体项）一律不阻断；迁移在同一事务内删除，并逐个列入报告，必须报告给用户|
|`migrate` 子命令|整体删除；`apply` 是唯一写入口，迁移明细由 `status` / `plan` 展示|
|回滚窗口|仅提交前：`transaction rollback` 整体恢复工作区文件与 common 状态；提交后只能 `git revert`，common 状态不随之恢复，由用户知情承担|

## 2. 现状缺陷

|#|缺陷|位置|
|---|---|---|
|1|`migrate all` 逐项开事务并立即 `applied` + `accept`，失败只回滚当前项，已完成项保留|`template_sync/migrate.py` `_transactional`、`cmd_migrate`|
|2|state-root、entity-storage 各自开事务并立即 accept|`state_root_migration.py`、`entity_storage_migration.py`|
|3|迁移与同步两个命令、两个 commit，规程先提交迁移再 apply|`docs/sync.md`「迁移维护窗口与规程」|
|4|迁移提交无审批要求；同步提交允许复用批量授权|`skills/repo-template-sync-core/SKILL.md` 第 6 步|
|5|apply 事务刻意排除 `docs/`，迁移改动不在同步事务内|`template_sync/apply.py` 事务路径|
|6|pre-commit 在提交时对 `.md` 跑 md_kx，可能改写文件，审阅 diff 与提交内容不一致|`hooks/pre-commit`|
|7|迁移从 state 读来源，legacy-state 须先单迁，形成顺序特例|`template_sync/migrate.py` `_resolve_source`|
|8|旧归档目录中任何非实体文件（如说明 `README.md`）一律报「非规范项或未知散落文件（阻断）」，文档与 skill 均未给处理规则；agent 只能自行挪走（kkmail 曾把两份归档 README 改名移入 `docs/guides/`）|`entity_storage_migration.py` status 段与 `_check_no_unknown_entries`|

## 3. 不变量

- **I1**：一次同步只开一个事务，事务覆盖本次同步可能写入的全部消费仓路径与 common 状态路径。
- **I2**：脚本从不 commit、从不 accept 自己开的同步事务；commit 只由 agent 在用户逐次批准后执行。
- **I3**：applied 之前任一步失败自动整体回滚；applied 之后用户拒绝则 `transaction rollback` 整体回滚；回滚后工作区文件与 common 状态逐字节等于同步前基线。
- **I4**：用户审阅的工作区 diff 即最终 commit 内容；提交时 hooks 不改写任何文件。

## 4. 设计

### 4.1 单一写入流程

`apply` 唯一写入口，固定流程：

1. **preflight**（只读，全部通过才开事务）：
    - 来源 clean、固定 SHA 与 `--expected-commit` 一致；
    - 工作区与暂存区完全干净（`git status --porcelain` 为空，忽略文件除外）；
    - 无未结束同步 / 图事务；旧 `.scratch` 事务未结束或损坏即阻断；
    - 无登记 worktree；md_kx 可用；
    - 各迁移类型的数据阻断项（如 ADR 编号重复、无编号 H2、spec slug 冲突）全部为空，否则列出阻断项与修复方案后停止。
2. **开事务**：路径 = 各迁移类型声明路径 ∪ 模板同步路径；`state_paths` = 各迁移类型声明的 common 状态路径 ∪ `sync_state.json`。
3. **迁移**：按固定顺序 `legacy-state → workflow-schema → pending-ledger → findings-ledger → entity-storage → state-root → conventions → agents-zones → specs-layout → reviews → adr → retired-hooks`，只执行有待办的类型。
4. **模板同步**：现有硬同步、共享裁定、命令薄层、入口、manifest、state 推进。
5. **格式化**：对事务路径内本次新增或修改的 `.md` 运行 md_kx 写回。
6. **后置校验**：全部迁移检测为空；`validate_specs` 与实体契约通过；项目验证策略通过；模板契约测试通过；变更 `.md` 通过 `md_format --check`。
7. **applied**：输出完整变更清单与事务状态，停止等待审批。

第 1–6 步任一异常自动整体回滚；回滚失败置 `restore_failed` 并报告人工恢复条件。

### 4.2 迁移器接口

每个迁移类型改为纯声明 + 执行，不开事务、不 accept：

|成员|职责|
|---|---|
|`ROOTS`|静态声明可能写入的消费仓路径|
|`STATE_ROOTS`|静态声明可能写入的 common 状态路径（仅 legacy-state、state-root）|
|`findings(sync, src)`|只读：是否有待迁移遗留物（常驻门禁，保持单调）|
|`blockers(sync, src)`|只读：需人工修复的数据问题|
|`execute(sync, src)`|在编排器的事务内写盘，返回报告行|

现有声明路径收敛表：

|类型|`ROOTS`|`STATE_ROOTS`|
|---|---|---|
|legacy-state|`.agents/skills/repo-template-sync/sync_state.json`|`sync_state.json`|
|workflow-schema|`docs/tasks`、`docs/archive/tasks`|—|
|pending-ledger / findings-ledger|`docs/pending.md`、`docs/pending`、`docs/findings.md`、`docs/findings`|—|
|entity-storage|`docs/tasks`、`docs/archive/tasks`、`docs/pending`、`docs/archive/pending`、`docs/spikes`、`docs/archive/spikes` 及其派生索引|—|
|state-root|`docs/runtime`、`.repo_template/sync_state.json`、`.scratch/statusline_workdir.jsonl`、`.scratch/repo_sync_rollback`、`.scratch/task_graph_transaction`|`ledger.jsonl`、`tasks_audit.log`、`goal_queue.json`、`statusline_workdir.jsonl`、`sync_state.json`|
|conventions|`docs/blueprint/conventions.md`|—|
|agents-zones|`AGENTS.md`|—|
|specs-layout|`docs/specs`、`docs/archive/specs`、`docs/specs_index.md`|—|
|reviews|`docs/reviews`、`docs/archive/reviews`|—|
|adr|`docs/blueprint/decisions.md`、`docs/blueprint/decisions`|—|
|retired-hooks|`.claude/hooks`、`.claude/settings.json`|—|

声明为静态全集，不随现场变化；编排器只对存在待办的类型并入路径。

**旧归档目录非实体文件**（entity-storage）：

- 适用目录：`docs/archive/tasks`、`docs/archive/spikes`、`docs/archive/pending`、`docs/pending/todo`、`docs/pending/parked`。
- 判定：目录顶层凡不是规范实体的项均为非实体项。规范实体只有两种：tasks/spikes 目录中匹配 `TASK_DIR_RE` / `SPIKE_DIR_RE` 的目录；pending 目录中匹配 `pNNN_slug.md` 的普通文件。其余一切都算：任意名称与类型的文件、非实体目录、软链、隐藏项（含 `.gitkeep` / `.DS_Store`）、命名不合规的形似实体项（如 `p1_x.md`、`t13_x/`）。
- 处理：不进 `blockers`；`execute` 在同一事务内删除。每个项生成报告行「删除旧归档目录非实体项：`<路径>`（文件 / 目录 / 软链）」；目录项附带其内全部文件的相对路径清单。删除随整体事务回滚恢复，提交前在工作区 diff 中可见。
- 规范实体内部的问题（front matter 非法、编号冲突等）不属本规则，仍按数据阻断项处理。迁移不再从 state 读取来源，统一使用 apply 的 `--source` / `--checkout` 固定 pin，legacy-state 顺序特例随之消失。

### 4.3 删除项

- `migrate` 子命令（含 dry-run）及 `cmd_migrate`、`_STEPS`、`migrate_command`。
- `_transactional`、`migration_transaction`、`_resolve_source`。
- state-root、entity-storage 内部事务与 accept；entity-storage 的 `require_clean` 分支（由 preflight 统一）。
- `sync.md`「迁移维护窗口与规程」与各处「逐项提交、已完成步骤保留」表述。
- sync-core skill「批准的批量 commit 授权可复用」。
- preflight 报错中的迁移命令引导，改为「数据阻断项 + 修复方案」或「可直接 apply」。

### 4.4 审阅、提交与回滚

|阶段|动作|
|---|---|
|applied|agent 列出来源 SHA、全部变更路径、迁移报告、校验结果、state/manifest，询问是否 commit；迁移删除的旧归档非实体项必须逐个单列，不得并入汇总|
|用户批准|`git add -A` 后单个 commit（hooks 正常运行，禁止 `--no-verify`），随后立即 `transaction accept`；accept 要求事务路径全部已提交且 common 状态指纹未变|
|用户拒绝|`transaction rollback`；现场在 applied 后被改动时须先备份新增改动，再 `--confirm-restore`|
|提交后放弃|只能 `git revert <同步 commit>`；common 状态（sync_state、ledger 等）不随之恢复，须按 `sync.md` 降级段人工核对|

`accept` 之后事务备份可在下次同步开始时清除。

### 4.5 文档与 skill

|文件|改动|
|---|---|
|`docs/sync.md`|重写迁移段为「apply 内置迁移」；删除维护窗口规程；事务段写明单事务、整体回滚、仅提交前可回滚|
|`docs/recovery.md`|对齐回滚窗口|
|`docs/usage.md`、`repo/README.md`、`repo/AGENTS.md`|删除 `migrate` 命令引用|
|`skills/repo-template-sync-core/SKILL.md`|流程改为：核验 → status/plan → 裁定 → apply → 审阅 → 逐次批准 commit → accept；拒绝即 rollback；数据阻断项先报告修复方案并单独批准|
|`skills/repo-template-sync/SKILL.md`|同步引用|

## 5. 实施步骤

1. 迁移器接口重构（4.2），逐类型去事务化 → 验证：每类型 `execute` 只写声明路径内文件（工厂测试对路径外指纹断言不变）。
2. 编排器落入 `apply`（4.1），合并路径与 `state_paths`，加入格式化与后置校验 → 验证：旧布局一次 apply 后 HEAD 不变、全部改动在工作区。
3. 删除 4.3 所列入口与特例 → 验证：全仓 grep 无残留引用。
4. 文档与 skill（4.5） → 验证：文档引用的命令均存在。
5. 工厂全量测试 → 验证：`pytest repo/.repo_template/tests factory_tests -q -n auto -p no:cacheprovider --basetemp "$(mktemp -d)"` 全绿。

## 6. 工厂测试

|用例|断言|
|---|---|
|单提交|旧布局消费仓一次 apply：HEAD 不变；迁移与同步改动全部在工作区 diff；批准后一个 commit 包含全部改动|
|整体回滚（注入）|在每个迁移类型、模板同步、格式化、后置校验处注入失败：工作区文件与 common 状态逐字节等于基线，事务 `rolled_back`|
|拒绝回滚|applied 后 `transaction rollback`：等于基线|
|脏工作区|preflight 拒绝，零写入|
|数据阻断|ADR 重复编号等：preflight 列出阻断项，零写入|
|旧归档非实体项|各旧归档目录放入 `README.md`、任意二进制文件、非实体目录（含嵌套文件）、软链、隐藏文件、`p1_x.md`、`t13_x/`：apply 不阻断；全部被删除，每项一条报告行，目录项列出内部文件；rollback 后逐字节恢复|
|提交无改写|批准后提交：hooks 运行后工作区与 index 无新差异|
|accept 门禁|未提交事务路径、或 common 状态被改动时拒绝 accept|
|真实迁移链|从 988fd06 布局一次 apply 到当前版本，单 commit|
