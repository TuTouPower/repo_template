# 生命周期实体稳定存储与历史文稿归档重构方案

> 编写时间：2026-11-08T13:02:49+08:00。分析基线：`3037526d73833e5f76922515757974011be7e629`。
>
> 目标原则依据用户本次明确要求：tasks、pending、spikes 终生使用固定路径，状态由元数据表达；Archive 保存冻结的历史文稿，本机操作日志放在 runtime。本文定义目标契约、破坏性变更、迁移步骤与验收条件，代码改造尚未启动。治理记录仍以[裁决总账](../decision_log.md)为准，实施后再同步正式契约与落地状态。
>
> 适用范围：模板产物 `repo/`、工厂集成测试及必要的同步迁移代码。本文面向模板工具链维护者；工厂笔记留在 `docs_repo/`，不分发到消费仓。文中新增命令均为目标接口，不能直接当作当前版本操作命令执行。

## 目标与范围

实体身份、存储地址、业务状态和展示视图分别承担独立职责。实体创建后，ID 与规范路径固定；状态变化只更新元数据；CLI 与看板从元数据生成视图。归档工具只处理已经退出运行时依赖的历史文稿，不再承担结构化实体的生命周期迁移。

本次不是目录改名工程。读取、状态迁移、取号、合并校验、闭环检查、同步迁移、Agent 指令和测试必须同时收敛到固定路径契约，删除旧目录选择、移动动作及其专用豁免。

|对象|目标位置|状态与归档规则|
|---|---|---|
|Task|`docs/tasks/{tid}_{slug}/`|原位表达 `backlog / active / done / dropped`，不进 Archive|
|Pending|`docs/pending/{pid}_{slug}.md`|原位表达 `todo / parked / closed`，不进 Archive|
|Spike|`docs/spikes/{sid}_{slug}/`|原位表达 `active / completed / superseded`，不进 Archive|
|Finding|`docs/findings/{did}_{slug}.md`|保持事实库定位和现行正文结构，原位修订，不进 Archive|
|任务内审查与执行证据|所属 task 固定目录|与实体一起保留，继续参与 review、finish、cleanup、integrate 和恢复校验|
|独立历史评审包|`docs/archive/reviews/review_*/`|闭环、冻结、退出运行时依赖后，整包归档|
|项目交接快照|`docs/handoff.md`|只维护当前交接上下文，历史由 Git 保存，不生成 Archive 副本|
|过时方案与历史设计|消费仓 `docs/archive/` 或工厂 `docs_repo/archive/`|退出当前契约后保留历史版本，更新当前导航|
|任务操作日志|`docs/runtime/tasks_audit.log`|本机操作留痕，不入 Git，不承担状态、执行权或恢复权威|

`tid / pid / sid / did` 分别为 `tNNN / pNNN / sNNN / dNNN`，数字至少三位，沿用现有编号规则。`slug` 为创建时确定的 snake_case 标识；显示标题可以修改，slug 和规范路径不能随标题变化。

本次保留 C90 的固定规格来源、C91 的显式环境资产边界、一个实现 commit、attempt 身份与 owner、现有测试及 review 门禁。Git Tree 审查快照升级、attempt 状态机重写、通用文档数据库和新的调度服务不在范围内。

目标系统只有一套规范路径、实体 schema 和来源验证算法。旧数据在切换前一次性转换为目标格式；日常运行不识别旧格式，不区分迁移记录与新建记录，不依赖迁移生成的持久映射表。备份、dry-run 清单和事务 journal 只服务本次升级及故障恢复，不进入业务读取链路。

## 现状与问题边界

以下为分析基线中的实现事实，与后文目标契约区分使用。

|入口|当前行为|本次需要消除的耦合|
|---|---|---|
|`repo_task/store.py`|同时扫描 `docs/tasks/` 与 `docs/archive/tasks/`，校验状态与所在目录关系|实体读取依赖生命周期目录分类|
|`repo_task/lifecycle.py`|finish 原位写 done；drop 移到 Archive；finish 还检查归档目标占用|状态变化依赖额外目标目录|
|`repo_task/integration.py`|merge 后移动成员目录，并校验移动是否等价于自动合并结果|合并内容与归档路径转换交织|
|`repo_task/graph.py`|merge-backlog 将来源 task 标为 dropped 并归档，事务同时覆盖源、目标目录|图改写承担实体搬运|
|`pending.py`|todo、parked、archived 由三个目录表达，支持按 slug 重命名文件|状态、身份地址与文件操作绑定|
|`spikes.py`、`repo_hygiene.py`|按活跃或归档目录列举 spike，完结后整目录移动|缺少可独立校验的生命周期元数据|
|`repo_task/finalization.py`|task、spike 引用同时查询活跃与归档路径|闭环校验依赖多位置查找|
|同步迁移模块与消费仓指令|包含双目录扫描、移动与归档约定|升级后容易重新生成旧布局|

Issue #92 暴露了路径变换与门禁不一致：任务侧修改旧路径，Git 合并跟随重命名写入归档路径，范围门禁与自动合并结果校验发生矛盾。固定路径消除任务归档造成的地址变换；一般代码重命名、真实内容冲突和未经审查改写仍须校验。

Issue #88、#90 涉及代码基座、规格来源和链式启动；#91 涉及环境资产与交付集合。本次保留其已有改造和回归，不把固定路径描述成这些问题的统一解法。基线已有 C90、C91 合并提交；是否完成全量验收按对应实施记录核实，不以计划文档旧状态或 commit 标题代替验证结果。

## 架构不变量

1. **规范地址固定。** 实体创建后，所有正式生命周期状态使用同一文件或目录。状态转换不得调用 `git mv`、`rename`、复制后删除或软链别名实现迁址。文件原子替换中的临时文件不改变规范地址。
2. **身份固定。** ID、创建时 slug、规范路径相互一致。删除 `pending.py rename`、`findings.py rename`；task 与 spike 同样不提供 slug 改名接口。显示标题与正文按各自写权原位维护。
3. **状态显式。** 缺字段、非法状态、重复 ID、身份不符均明确失败。运行时不根据目录名、正文关键词或文件时间猜状态，不给损坏记录默认状态。
4. **版本副本不等于多地址。** 不同 Git commit、branch、worktree 可以保存同一实体的不同版本；同一来源中，一个 ID 只能对应一个规范地址。所有读取明确工作区或确定 SHA，不通过 Archive 补找遗漏实体。
5. **每项事实有确定权威。** Task 业务状态、attempt 执行身份、Git 合并事实保持分工。列表和看板是投影，不能回写成第二份状态账本。
6. **终态保留身份。** done、dropped、closed、completed、superseded 记录继续参与编号占用与引用解析，不因默认视图隐藏而消失。
7. **历史证据保留。** 终态记录通过追加勘误、后继引用或受审查修订维护，不能以清理目录为由删除证据、重写旧提交或伪造历史 PASS。
8. **Archive 只保存冻结文稿。** 结构化实体、执行权账本、持续追加的操作日志及仍被门禁读取的证据不存放于 Archive。本机操作日志与执行账本统一放在既有 runtime 目录，不新增审计目录。

稳定地址约束正式实体生命周期。现有 `purge` 仅取消从未入库的误建 task，其授权、引用保护和删除边界不扩大；不把 purge 用于已入库终态实体，也不借本次迁移清理数据。该误建取消事务使用的可恢复临时位置不承担状态视图。

## 目标布局与元数据

```text
docs/
├── tasks/
│   └── t001_example/
│       ├── task.md
│       ├── progress.md
│       ├── review_code.md
│       ├── review_test.md
│       └── handoff.json
├── pending/
│   └── p001_example.md
├── spikes/
│   └── s001_example/
│       ├── report.md
│       └── code/
├── findings/
│   └── d001_example.md
├── runtime/
│   ├── dispatch_ledger.jsonl
│   └── tasks_audit.log
└── archive/
    └── reviews/
```

示例 ID 仅说明布局；文件是否存在由实际任务与审查级别决定。`code/` 是可选实验目录；single task 使用 `review_general.md`。`runtime/` 沿用本机数据不入库、单独备份的约定。ledger 承担执行身份与 owner 控制，操作日志只供人工排障，两者不互相替代。旧日志若被显式跟踪，迁移预检须报告，并在保存完整历史后明确取消跟踪，不能通过忽略规则假装已经取消跟踪。

三类生命周期实体分别在 task 的 `progress.md`、pending 文件和 spike 的 `report.md` front matter 保存规定字段。所有记录只接受目标 schema；旧记录在一次性迁移时补齐，不在运行时自动补字段。不增加逐实体格式版本字段或按来源分派的解析器；切换门禁校验工具链一致性、规范路径和字段完整性。

|实体|机器字段|说明|
|---|---|---|
|Task|现有严格字段|保留 `diff_anchor`、`spec_source_sha`、review、依赖、分支与 worktree 等现有契约|
|Pending|`pid`、`slug`、`status`、`resolution`、`reason`、`fix_ref`|H1 继续承担显示标题，正文保留问题、来源、证据和处理历史|
|Spike|`sid`、`slug`、`status`、`superseded_by`、`reason`|报告正文保留实验、证据、结论、findings 与后续 task|

复用现有 front matter 解析和原子写入机制，增加各实体严格字段与状态校验。Pending、spike 不另建通用实体框架，不为三个有限类型引入 ORM 或索引数据库。机器字段作为当前状态唯一入口；正文历史中的“已归档”“处理”等旧记录保留审计含义，不再作为状态读取来源。

Pending 的 todo、parked 不保存当前闭环 resolution 与 fix_ref，parked 必须有非空 reason；closed 必须有合法 resolution 与非空 reason。revive 清空当前暂搁字段，历史原因保留在正文记录。Spike 的 active、completed 不得填写当前 superseded_by；superseded 必须同时填写合法后继与非空 reason。重复字段和非法组合一并拒绝，不能只检查 status 枚举值。

## Task 生命周期与视图

业务状态保持 `backlog / active / done / dropped`。以下写入位置保持现有主干与分支分工，只取消物理归档。

|操作|元数据与 Git 行为|路径行为|
|---|---|---|
|add|在主干创建 backlog 定义|创建固定目录|
|start / run-begin|从主干固定 `spec_source_sha`，在所属 worktree 激活|保持同一相对路径，不改前驱分支|
|finish|验证后原位写 done，生成 handoff，形成一个实现 commit|不检查或创建归档目标|
|run-close / cleanup|按 exact identity 写回执、清理 worktree|不移动 task 文档|
|integrate / integrate-chain|验证并合并实现，主干获得终态，补记 integrated|所有成员保留固定目录|
|drop|在现有状态与权限门禁下原位写 dropped、记录原因|不移动目录|
|merge-backlog|改写依赖图，来源原位写 dropped 并记录目标 tid|事务只覆盖实际修改路径|
|rewind / finish-unwind|沿用现有恢复条件、身份和证据规则|恢复元数据，不恢复旧目录布局|

`done` 表示实施完成，`integrated` 表示对应实现已经合入主干。UI 和 CLI 不把“位于 tasks”解释为尚未完成，也不把“done”直接解释为已经合并。集成事实继续由明确 merge SHA、Git 血缘和可核对的 integrated 回执表达。

Task 列表统一使用现有 `--status`、`--ref` 与 JSON 入口，运行中筛选使用 `--status active`，其他状态使用对应枚举值，不增加同义参数。看板默认展示待处理与运行中集合，可切换已完成、已放弃、待合并和全部；默认过滤不影响 ID 查找、调度与历史核验。

当前来源和有效状态来源继续区分：主干、指定 ref、所属 worktree 各有明确读取语义；有效状态汇总保留来源说明。固定路径不能成为删除必要跨分支查询的理由，也不允许读取最新主干覆盖 C90 固定初始规格。

## Pending 状态与接口

Pending 的状态转换为 `todo → parked → todo`，以及 `todo → closed`。closed 为终态；parked 条目仍须先 revive，再闭环，沿用当前行为边界。

|目标接口|语义|
|---|---|
|`pending.py new`|在固定路径创建 `status: todo`|
|`pending.py list --state STATE`|STATE 为 todo、parked、closed 或 all；默认 todo|
|`pending.py close pNNN ... --reason ... [--fix-ref ...] [--resolution ...] [--write]`|原位写 closed 与闭环依据，默认 dry-run|
|`pending.py park pNNN ... --reason ... [--write]`|原位写 parked 与理由|
|`pending.py revive pNNN ... [--write]`|原位恢复 todo，保留历史处理记录|

旧 `archive` 命令、`--state archived`、`rename` 命令直接移除，不保留别名或自动跳转。旧单文件总账转换从普通 pending 命令中退出；早期数据通过明确版本的离线迁移处理。

闭环仍强制非空 `--reason`，`--fix-ref` 保持可选，不要求伪造 tid；`resolution` 沿用 `resolved / transferred / obsolete / duplicate`，默认 resolved。`transferred` 表示转办，不表示修复完成。批量操作先验证全部成员，再在现有锁与持久文件事务中更新；dry-run 零写入，部分失败可恢复原文件和原暂存状态，不留下部分 closed。

取消目录移动后，pending 状态命令不再自动 `git add`，只更新目标元数据和本次处理记录，index 保持原样，由提交者明确暂存。保留目标文件中既有正文及未提交修改；锁内核对输入摘要，不能覆盖并发改写。Spike 状态命令采用相同写入边界，不连带暂存实验代码。

取号扫描所有状态的规范文件。扫描所有本地分支与登记 worktree 的既有并发防重号边界保留；取消对 todo、parked、archive 三种目录分别枚举。不同来源中的同一历史实体副本可以存在，同一来源中的同号不同规范路径仍须拒绝。

## Spike 状态与闭环

Spike 创建时写 `status: active`，不再通过是否位于 Archive 推导状态。

|目标接口|进入条件与结果|
|---|---|
|`spikes.py new`|按现有锁内取号创建固定目录与 active 报告|
|`spikes.py list --state STATE`|STATE 为 active、completed、superseded 或 all；默认 active|
|`spikes.py complete sNNN [--write]`|通过报告与 findings 闭环校验后原位写 completed|
|`spikes.py supersede sNNN --by sNNN --reason ... [--write]`|后继证据有效后原位写 superseded，记录替代关系与原因|

complete 复用并强化现有 `validate_spike()`：报告无占位符，包含结论、采纳决定、真实 findings 引用，以及后续 tid 或无需后续理由。写状态与校验使用同一份已锁定输入，校验失败零写入。

supersede 可以处理 active 或 completed 报告，但替代目标必须是另一条 completed spike，且其证据通过校验；不得自引用、引用缺失或形成循环。原报告与已有结论保留，不把“被替代”伪装成原实验验证成功。后续替代形成链时，引用解析须检查整条关系并落到有效 completed 证据。

Task finalization 只从固定路径解析 sid。引用 active spike 不能通过收尾；引用 completed 时校验该报告；引用 superseded 时同时保留原引用并核对替代链最终证据。Finding 仍记录可复用事实，spike 完结不会把报告从原址移走。

删除 `repo_hygiene.py archive-spike` 及 skill 中对应动作，不保留“逻辑归档”的同名兼容命令。实验代码与报告一起留在固定目录，不能只留下空壳指针再把正文搬走。

## 历史文稿归档边界

归档资格取决于对象职责和依赖关系，不能只按“有无 front matter”或“文件是否很旧”判断。具有编号并参与生命周期的实体不进入 Archive；已退出当前协议的整份历史文稿可以归档。

独立评审包归档前必须满足：评审闭环并明确冻结；不是 task 目录内证据；无当前 finish、integrate、recovery 或调度器读取依赖；已检查当前正文和导航引用；没有仍在登记工作区或未合并本地分支中继续修改同一包的现场。无法确认就保留原址，不能通过强行移动解除检查。

`repo_hygiene.py archive-review` 继续保留为独立冷文稿操作，限定 `docs/reviews/review_*/` 到 `docs/archive/reviews/` 的整包迁移，不扩展成任意路径搬运器。保留报告与 `_meta/`，先 dry-run，目标已存在时拒绝覆盖；需要更新当前导航的变更与迁移一并审阅。冻结后采用追加勘误或新评审包，不在多个分支继续修改已冻结包。归档命令仍遵循现有明确授权与提交边界。

`docs/tasks/tNNN_slug/review_*.md`、`handoff.json`、规格附件和 spike 报告均不适用上述归档入口。

项目交接只维护固定位置 `docs/handoff.md` 中的当前快照，历史由 Git 提交保存。退役 `archive-handoff` 命令及其段落切分、摘要标记、归档追加和中断恢复逻辑，不再创建或追加 `docs/archive/handoff.md`。交接快照提供当前焦点、未完成事项和下一步等人类上下文，不替代 task 状态、attempt ledger 或机器执行证据。任务内 `handoff.json` 保留原职责与保护规则。

切换前确认旧 `docs/archive/handoff.md` 全部内容已进入可恢复的 Git 历史；未提交、未跟踪内容必须先完整保存并验证，不能假设 Git 已经记录。具备该前提后，显式迁移才从当前目录移除旧归档文件并更新引用，不再生成另一份历史汇总。冻结 reviews 等其他冷文稿保持原样。

过时方案与架构说明归档时，先确认当前契约已由明确 successor 承接，再更新当前入口的路径引用。历史证据中的原路径保留原意，不批量改写引用或格式化冷文稿。工厂 `docs_repo/archive/` 与消费仓 Archive 相互独立，消费仓 hygiene 不写工厂笔记。

`tasks_audit.log` 一次性迁到既有 `docs/runtime/tasks_audit.log`，新命令只写新位置，旧日志按原始字节保存；目标已存在时明确阻断，不覆盖或自动拼接。它记录 rewind、finish-unwind、purge 等操作，供人工排查未入库操作，不作为依赖校验、执行权或自动恢复的数据源。整个 runtime 目录不入 Git，日志沿用本机备份约定；正式任务证据仍在实体目录和 Git 中。

## 合并与审查门禁

取消 `_stage_archive_moves()`、`_archive_move_deviations()`、`_sync_archive_after_existing_merge()` 及归档目标占用检查。合并事务不再重建归档目录，不为实体目录移动设置豁免；当前 handoff 只从固定路径读取，实现来源统一按 Git 对象和提交血缘定位，不通过历史目录名称选择算法。

`_expected_auto_merge()` 与 staged 范围、内容保护继续存在，按固定路径收敛调用。门禁仍须验证目标 tip、exact handoff、代码基座、依赖实现、Git 自动合并内容，以及人工冲突解决的现有验证条件。不得把它们替换成单一“staged 路径属于已审路径”的子集判断。

### 核心资产所有权

跨 task 写权以文件为边界，不只比较 front matter 中几个字段。其他 task 的 `progress.md`、`task.md`、`handoff.json`、`review_*.md` 全部属于受保护核心资产；即使只改正文、空白或模式位，即使本次 review 已经 PASS，也不得由后继 task 改写。匹配对象为规范 task 目录内的这些文件名，子目录中的同名文件同样保护，不能移动到子目录绕过。

其他 task 目录中只允许本任务已审查的非核心普通补充文件变更，并继续接受范围、内容与模式检查；不允许借 symlink、gitlink 或替换实体目录重定向核心文件。核心资产纠错必须走所属实体明确授权的维护或恢复流程，不通过后继 task 交付。生命周期命令仍按原有写权修改自身实体，不受此规则错误阻断。

新增共享校验函数 `monitoring.validate_task_core_asset_changes(before_entries, after_entries, owner_names)`。这是目标接口，实施时放入现有 monitoring 模块，由 lifecycle 与 integration 共用，避免两套保护名单。输入是未经过 review 排除规则过滤的完整路径清单，条目包含 Git mode 与 Blob OID；owner_names 由已验证任务身份产生，不接受用户传入任意目录。校验使用规范 POSIX 路径及完整 task 目录身份，不使用有前缀碰撞风险的字符串匹配。

算法遍历 before 与 after 路径并集，将新增、删除、内容或模式差异视为变更。若路径属于非 owner 的 task 且命中核心资产名，立即抛出 `TaskDataError`，输出执行 tid、被保护 tid、路径与变更类型；读取失败、身份歧义或不支持的文件类型同样阻断。不能依赖字段解析成功才保护文件，也不能使用排除了 progress、review、handoff 的 review scope 清单代替完整输入。

树间差异使用确定 SHA 或 tree OID、NUL 分隔及 `--no-renames`，因此 rename/copy 不产生权限豁免：跨实体移动核心文件至少表现为受保护源删除或目标新增。目录整体删除、改 slug、用链接替换目录也会改变完整清单中的核心路径，必须被拒绝。

### 校验调用与合并判定

|入口|比较对象与授权集合|拒绝时行为|
|---|---|---|
|`lifecycle._finish_prechecks()`|diff_anchor 与本次完整交付候选；owner 只能是当前 tid 对应目录。候选包含将提交的文件，并核对工作区未提交核心改动，不为检查临时暂存用户文件|在写 done、生成 handoff 和实现 commit 前失败|
|`monitoring.verify_integrate_ready()`|每个实现提交的 first parent 与其实现 tree；owner 只能是该实现所属 tid|手工提交、伪造 PASS 或绕开 finish 也不能通过 handoff、cleanup、import 与集成就绪门禁|
|单任务及 `_preflight_chain()`|对每个成员分别完成上述实现校验，再准备 merge|拒绝前不产生 pending merge；不能用整个链成员集代替单成员写权|
|`integration._validate_merge_staged_scope()`|先复核成员提交，再比较固定主干 HEAD tree 与当前 `write-tree`；owner 为本次已验证集成成员集合|在 `--continue` 创建 merge commit、写 integrated 或删分支前失败，保留现场供恢复或 abort|

整链 A→B 的权限仍是“A 只拥有 A 核心资产，B 只拥有 B 核心资产”。即使 A、B 都在本次 integrate-chain 成员中，B 的 first-parent diff 触及 A 核心文件也必须拒绝，不能因为 A 恰好是本批成员就放行。

最终 staged 检查分两层：非成员核心资产相对当前 HEAD 的 mode 与 Blob OID 必须完全不变；成员核心资产必须等于该成员已验证实现 tip 中的相应条目，缺失也视为条目差异。后者不能只与链尾比较，否则会认可后继覆盖前驱；原始冲突路径也不豁免核心资产所有权或已验证内容约束。成员自身的规格演进、review 升级和状态变化在所属实现提交中完成，不能在 merge 期间临时补写核心文件。

Task 主干 backlog 与分支 done 的既有自动收敛只能选择已验证成员终态。若主干有需要保留的独立核心修改，不能靠覆盖为分支内容绕过现有三方保护；必须先显式解决规格与证据冲突，再取得有效交付与验证。允许修改非核心补充文件也不代表允许覆盖主干独立内容。

所有比较使用本轮锁内固定的主干和成员 SHA；准备与 continue 之间主干或目标身份改变则重新校验或拒绝，不复用过期结果。核心保护不能仅置于普通路径白名单的 unexpected 分支：即使路径已经属于任务侧 diff，也必须执行所有权检查。

review scope 不增加对全部终态 task 的排除规则。当前 task 的过程文件仍按既有规则处理，其他任务资料被本次修改时仍须进入相应交付校验。稳定路径不赋予跳过 review 的权限。

本次不改变环境链接政策，也不将 Git Tree 误当成无法保存软链的存储。C91 的显式环境边界与真实交付链接测试保留；C90 的固定规格来源在 finish、cleanup、单任务及整链集成、恢复中继续使用。

## 一次性迁移与切换

### 迁移入口与源版本

复用现有 `repo_sync.py migrate` 和 `FileTransaction`，增加目标类型 `entity-storage`，提供默认 dry-run 与显式写入。具体目标调用形式为 `repo_sync.py migrate entity-storage [--write]`；新类型的计划、执行、验证、恢复复用同一转换定义，不另建独立迁移框架。

迁移器根据切换前实际数据清单，一次性把路径与字段转换到唯一目标格式，覆盖范围在执行前明确核实。不能机械串联历代迁移步骤，不能把旧解析器挂进 store、finalization 或依赖验证。未识别的数据先完成针对性转换设计和验证，再执行切换；不能通过保留旧运行路径完成升级。迁移工具属于本次升级工具，正常业务命令不导入、不依赖它，不维护永久的历代格式兼容流水线。

### 维护窗口与备份

1. 确认默认分支及全部执行会话，停止实体写入、hygiene、同步与集成。迁移持有现有控制面锁与 ID 分配锁，按调用图确认固定加锁顺序，避免与分配命令形成等待环。
2. 清点所有登记 worktree、本地分支、未完成 attempt、pending merge、文件事务和未合并 done 链。必须先用旧工具完成、恢复或明确终止这些现场，不能在迁移中重写执行分支或伪造完成回执。
3. 普通旧分支若仍带有实体变更，先在旧协议下完成整合；仅为保留历史的旧分支经明确确认后移出当前分支扫描集合，保存为只读历史引用或外部备份。迁移器不得自动删分支、改 tip 或忽略未合并工作。
4. 主工作区和 index 必须干净。备份包含完整 Git 对象与 refs、文稿、未跟踪实验附件、本机 ledger、审计和相关同步状态；单独 `git bundle` 不覆盖未提交与忽略文件，不能作为唯一备份。
5. 在隔离目录完成恢复演练，验证 refs、文件摘要、ledger identity 和附件可恢复。未通过恢复验证，禁止写入迁移。

备份写入位置由实施时明确指定，遵守工作区范围和数据保存约定；本方案编写不执行真实消费仓备份、迁移或分支清理。

### 预检与确定映射

|源数据|目标与转换依据|
|---|---|
|`docs/tasks/tNNN_slug/`|保留原路径，校验身份与当前状态，一次性补齐目标字段|
|`docs/archive/tasks/tNNN_slug/`|迁到 `docs/tasks/tNNN_slug/`；只接受有明确终态证据的 done、dropped|
|`docs/pending/todo/pNNN_slug.md`|迁到扁平目录，写 `status: todo`|
|`docs/pending/parked/pNNN_slug.md`|迁到扁平目录，写 `status: parked`，保留暂搁依据|
|`docs/archive/pending/pNNN_slug.md`|迁到扁平目录，写 `status: closed`；解析并保留原闭环依据|
|`docs/spikes/sNNN_slug/`|保持路径；未明确完成者按源协议 active 迁移，不通过正文猜 completed|
|`docs/archive/spikes/sNNN_slug/`|闭环证据验证通过后迁回固定目录，写 `status: completed`|
|`docs/archive/tasks_audit.log`|一次性迁到 `docs/runtime/tasks_audit.log`，保持原始内容|

同号多处、目标占用、身份不符、源状态与正文矛盾、归档 spike 缺 findings、旧 pending 缺闭环依据，全部列为阻断项。即使两个文件字节相同，也不擅自决定哪份应删除。需要人工裁定时，先在源版本形成可审阅修复，再重新生成迁移计划。

迁移计划包含源路径、目标路径、输入摘要、字段转换、当前引用改写及附件清单。旧交接归档退役另列文件内容已完整入库的证据与移除动作，纳入同一备份和事务保护。引用改写限定当前维护文档中的真实链接与路径字段，使用确定的旧新路径映射；不能全仓字符串替换历史报告、代码示例、日志或 URL。

### 实现来源统一校验

任务依赖需要确定实现 SHA，但不需要增加一份迁移来源表。当前 `_dependency_implementations()` 将来源定位绑定于历史文件路径，本次从根因改为按 Git 对象与血缘查证，所有完成任务采用同一算法。

1. 从确定来源 SHA 中的规范 task 目录读取 `handoff.json`，验证其目标 schema、tid、execution identity 和 base_sha，取得其 Blob OID。
2. 在该确定来源的提交历史中，按 Blob OID 定位引入该对象的候选实现提交，不限制历史路径，也不猜 commit 标题。Git 原生 `--find-object` 可用于候选检索，但对象出现次数变化只提供候选，不能直接等同于实现来源。[Git log 官方说明](https://git-scm.com/docs/git-log/2.28.0.html)
3. 对候选验证：恰好一个非 merge 实现 commit、first parent 等于 handoff.base_sha、提交包含完全一致的 handoff 对象及执行身份。只接受唯一匹配；缺失、歧义、缺对象或结构不符明确失败。
4. 按现有依赖规则验证实现 SHA 位于实际选用 base 的祖先链。主干已包含实现，不能代替实际 base 包含实现的检查。

一次性迁移保持合法执行回执内容与其 Git 对象身份，不复制为另一份来源账本；实体路径与状态字段按目标契约转换。迁移前后必须用上述同一算法复验每条 done task 的实现关联。若实际旧数据缺少目标回执所需事实，先补齐可验证数据或明确处理缺陷，不能伪造 PASS、默认 done，或留给运行时按旧格式猜测。

Git 中原有提交是历史证据，不是第二套活跃存储 schema。新运行时只读取当前规范回执，再查询对象和血缘；不按“迁移前／迁移后”分流，不扫描旧目录，不要求正常业务调用隔离旧版工具。新 clone 即使没有本机 runtime，也能用同一算法核验已完成任务的依赖来源。

### 执行与恢复

写入前在锁内重新核对默认分支 SHA、index、输入摘要和分支/worktree 清单。任一变化使 dry-run 计划失效，重新预检，不把旧计划直接重放。

迁移在一个受持久文件事务保护的写入阶段完成实体路径转换、元数据补齐、当前引用更新和操作日志迁址。先验证完整输出、实现来源与日志内容，再清理本次源路径及空旧目录。迁移中的一次性移动是存储协议切换，不是实体生命周期命令；旧布局解析仅存在于本次升级的显式转换工具。

失败时核对事务、源、目标和 index 后恢复。自动回滚只覆盖本次事务拥有且仍符合预期的内容；现场被外部修改或回滚失败时保留 journal、备份与逐路径诊断，停止自动写入，禁止盲目重试。校验为完整目标状态时再次执行应零写入；半完成状态只能走事务恢复。

验收通过后，将目标工具链、数据和当前指令作为一致版本切换。新 clone 从已迁移提交获得固定布局；所有新任务分支必须从已切换主干或同版本已完成前驱创建。未完成切换的执行分支不能直接进入新 integrate；新工具对工具链不一致、旧目录或目标字段缺失的候选来源明确拒绝。

Git 历史不重写。已经 integrated 的实现、review、handoff 和 ledger 回执保持真实历史，不通过迁移重新签发 PASS、修改 SHA 或冒充新执行。新主干中的全部实体均按目标模型读取和验证，不保留历史实体专用运行入口。备份中的旧工具仅供整体回退，不成为新系统正常验证的前置条件。

### 同步与回退

`repo_sync status/plan/apply` 和初始化导出必须认识目标契约。发现旧数据时，正常 apply 报告迁移要求，不先覆盖工具链和文档再把消费仓留在混合状态。候选版本预检和显式迁移入口须通过现有同步启动流程加载，保留旧工具快照用于恢复；旧启动器无法加载候选模块时明确阻断并给出受控自举步骤。

迁移事务快照必须覆盖新增目标路径、旧路径、审计、本轮实际修改的同步文件及待退役的旧交接归档；已冻结的独立 reviews 和其他冷文稿不进入重写集合。更新 `workflow_migration.py`、同步 rollback 路径清单及初始化产物校验，防止下一次同步重新生成旧目录或交接归档文件。

提交切换后、尚无新业务写入时，可在停止所有会话后用已验证备份恢复旧工具链、文稿、refs 和 runtime 的同一检查点。已有新协议写入时禁止仅降级脚本或直接反向搬目录；先保存新数据并设计显式恢复或前向修复，不覆盖用户新增工作。不执行强推或历史改写。

## 实现落点与文档权威

以下为直接受影响入口，不作为封闭文件白名单；实施时按调用关系核对同类位点，不重构无关模块。

|落点|必要改造|
|---|---|
|`repo_task/context.py`、`documents.py`、`store.py`|固定路径、唯一 schema 与状态校验，移除正常扫描的双目录与位置推断|
|`repo_task/lifecycle.py`、`graph.py`|drop、finish、merge-backlog 原位收敛；更新相关事务范围与操作日志路径|
|`repo_task/integration.py`|删除归档移动与专用豁免；逐成员实现与最终 staged tree 执行核心资产保护，保留范围、内容、血缘与恢复门禁|
|`repo_task/monitoring.py`、`finalization.py`、`specs_index.py` 及 renderer|共享核心资产校验与就绪门禁；固定引用路径、历史来源边界、spike 状态与替代链校验|
|`_dependency_implementations()` 及来源核验调用方|统一按当前 handoff 对象定位实现，移除历史路径依赖，不增加来源映射表；保留实际 base 血缘门禁|
|`repo_task/attempts.py`、`recovery.py`、ledger import 相关入口|核对调用方不再依赖归档路径；不重写 attempt 模型|
|`pending.py`、`spikes.py`、`findings.py`、`_id_scan.py`|显式状态、固定地址、删除改 slug 入口、全状态编号占用与跨来源检查|
|`repo_hygiene.py`|删除 spike 与 handoff 归档及其专用辅助逻辑，仅保留独立冷文稿动作与范围保护|
|`repo_cleanup.py`|更新日志路径与 runtime 清理保护，不把执行账本作为缓存清理|
|`repo_task/cli.py`、view 与静态前端|状态筛选、终态可查、done 与 integrated 分开呈现|
|`template_sync/migrate.py`、`workflow_migration.py`、`apply.py` 及相关预检|显式迁移、旧布局阻断、事务覆盖与自举验证|
|`repo/docs/`、`.gitignore`、task 与 spike 模板|移除旧实体目录骨架与 handoff 归档模板，更新当前交接说明、实体元数据与日志位置；保留历史包与 runtime 边界|
|`repo/AGENTS.md`|更新实体固定路径、正式状态及写权不变量|
|`repo/.repo_template/docs/usage.md`|CLI、数据迁移、工具链写权及 skill 调用的唯一用法定义|
|`architecture.md`、`recovery.md`、`sync.md`|分别维护机制、故障恢复及升级切换规则|
|`pending-record`、`pending-fix`、`task-from-pending`、`bug-to-pending`、`task-work`、`task-integrate`、`repo-hygiene` 等 skill|改用新状态接口，删除搬目录剧本，不扩大授权|
|registry 与宿主生成入口|从实际生成源更新 description、调用约定与导航，重新生成并核对 diff|

查找不到的模块名以实际调用链定位，不为与表格对齐创建空模块。旧 `ARCHIVED_STATUSES` 若表达任务终态，改为准确的终态名称；不得把终态判断与归档目录继续绑在一起。

工厂本轮新增计划进入 `docs_repo/plans/` 导航。实施时在裁决总账追加对旧路径决策的 successor 记录，保留原历史裁决；正式契约在对应权威位置一次替换，skill 不复制整份状态机。现有生成文件必须改源后生成，不手工修补多个宿主副本。

## 实施工作包与性能计划关系

|阶段|实施内容|退出条件|
|---|---|---|
|S0 契约与回归|固定状态表、唯一 schema、规范路径、冷文稿条件与迁移输入；保留 #88–#92 场景和现有安全断言|目标行为、源数据差异及失败条件可复核|
|S1 实体读取与状态|实现严格元数据、单路径扫描、pending/spike 状态命令和编号保护|解析、状态转换、CLI 及故障恢复测试通过|
|S2 Task 与合并|改 finish/drop/graph/integrate/历史 handoff 读取；落实共享核心资产校验、逐成员写权与 staged 内容约束；删除归档专用流程|真实单任务、整链、主干独立改动和核心资产保护回归通过|
|S3 迁移与同步|一次性迁移、dry-run、备份恢复、旧分支阻断、同步自举与回退|源版本到目标版本的隔离消费仓演练通过|
|S4 视图与文稿|更新列表、看板、Archive 操作、正式文档、skill 和生成入口|状态筛选与写权一致，冷文稿与运行时边界检查通过|
|S5 全量验收|执行完整测试、导出后消费契约和旧目录残留审计|全部必要检查通过，迁移演练与恢复结果归档|
|S6 基准重建|固定最终正确性版本和用例集合，记录性能及 Git I/O|形成可与后续优化对照的受控基线|

S0–S5 在隔离开发分支形成一致版本，中间提交不作为可发布混合协议。可以按明确模块边界安排开发，涉及相同 store、integration、monitoring 或同步事务的修改串行整合，不让多个 Agent 同时改同一批文件。

已有其他 Agent 执行性能计划，默认以其当前工作包完成并通过全量后的提交作为交接点，本方案编写不改变其执行范围。实施者在交接时核对实际 HEAD、已完成工作包与工作区归属，再按下面依赖安排剩余工作，不直接从本文分析基线覆盖后续成果。

与[测试性能优化与工作流基线重构方案](plan_test_suite_performance.md)的关系如下：

- 保留已经进入代码的 C90、C91 及独立测试基础设施优化，不重复实现或回退。
- Arrange、私有种子仓隔离和运行器测量等独立工作可继续；fixture 最终输入须更新为目标布局。
- P2 的 store 批量读取与本方案单路径读取重叠，先完成一个可验证整合点，再继续性能改造，不能同时维护两套扫描模型。
- 若原性能计划先完成，本方案作为下一轮正确性重构执行；若本方案先被安排，先完成 S0–S5，再重建性能基准并继续受影响工作包。
- 不把减少目录移动或新增校验造成的总耗时变化直接记为原性能优化收益。不同版本、不同用例集合分别报告，不拼接 A/B 数据。

不存在必须“旧计划全做完，再把旧方案原样重做一遍”的依赖。依赖来自相同模块与测试语义，最终验收始终基于同一正确性版本。

## 验收矩阵

|类别|必须覆盖的行为|
|---|---|
|固定身份|全部状态切换前后路径不变；标题变化不改路径；身份字段不符、缺必填字段、同源重复 ID 明确拒绝|
|Task 生命周期|add/start/finish/run-close/integrate/drop/rewind/merge-backlog 保持路径；done、dropped 在主干与指定 ref 均可查|
|合并血缘|单任务、连续链、已合入前驱、保留分支重入及 continue 幂等；一个实现 commit 与 first parent 约束保留|
|#92|后继已审修改前驱普通资料，在前驱已 integrated 后正确合并；准备与 continue 均无任务目录归档移动、无相反修复提示|
|内容保护|新增无关 staged 文件、已授权文件内任意改写、抹除主干独立改动、篡改其他 task 身份与证据均被拒绝；合法自动合并通过|
|非成员核心资产|参数化覆盖 progress、task、handoff、所有 review 文件的内容、正文、空白、模式、新增、删除、跨目录移动和目录替换；已审路径、PASS 与冲突路径均不豁免|
|整链成员所有权|A→B 同批合并时，B 修改 A 核心文件在逐成员 first-parent 校验失败；最终 staged 成员核心内容偏离各自已验证 tip 同样失败|
|拒绝时序|finish 拒绝前不写 done 或实现 commit；preflight 拒绝前不准备 merge；continue 拒绝不推进 HEAD、不写 integrated、不删分支，保留可恢复现场|
|合法跨实体补充|其他 task 非核心普通说明或附件的已审变更通过；主干独立核心维护不被误算为任务侧修改，也不能被合并覆盖|
|#88 与 C90|主干新建后继从未合并链尾启动；未提交规格明确拒绝；固定 `spec_source_sha` 不随主干变化漂移|
|C91|环境链接创建、保留、清理不改变交付边界；实际交付链接的目标、模式、删除及提交一致性仍受审查|
|Pending|全部允许转换、非法转换、闭环理由和 resolution；多条批量失败恢复；暂存与未暂存内容保留；旧命令退出|
|Spike|完成报告、缺证据拒绝、后继引用、被替代状态、自引用与循环拒绝；task 收尾解析真实完成证据|
|取号|终态记录继续占号；并发进程与 worktree 不撞号；旧格式来源阻断且报出位置，Git 查询失败不降级放行|
|视图|默认过滤不改变 ID 解析、依赖检查和全量结果；状态与集成状态分别输出，指定 ref 不偷读当前主干|
|冷文稿与本机日志|独立 review 整包迁移、附件保留、目标冲突拒绝；任务 review、spike、ledger 与操作日志不进 Archive；日志迁到 runtime 后内容不变、不入 Git、不参与业务状态推导|
|项目交接|当前快照保持固定位置；新模板不含交接归档文件，旧命令不可用；旧内容未完整入库时拒绝移除，迁移后历史仍可恢复；任务 handoff.json 门禁不变|
|数据迁移|全部路径映射、闭环字段转换、重复与缺证据拒绝、旧新引用核对、目标重跑零写入|
|统一依赖来源|历史文件发生移动、复制或同 Blob 多处出现时，仍按唯一算法核验来源；歧义、身份或 first parent 不符、缺实现的 base、缺对象均拒绝；新 clone 无 runtime 可验证已完成依赖|
|故障恢复|每个写入阶段异常、Git 操作失败和进程中断；完整回滚或保留可恢复现场，无部分成功冒充完成|
|同步切换|旧布局正常 apply 阻断；候选自举与显式迁移可完成；新 clone、旧消费仓升级和无新写入时恢复旧检查点|
|历史证据|原提交与合法回执不被改写；不伪造 review PASS；迁移后所有实体通过同一目标模型与来源算法验证|
|运行依赖|正常命令不加载迁移工具，不读取迁移映射表，不按格式版本或迁移身份选择解析器；操作日志与 ledger 沿用本机备份及清理保护|

契约测试留在 `repo/.repo_template/tests/`，覆盖固定路径、字段、状态、CLI 路由与权威约定；真实 Git、worktree、迁移、同步和进程中断测试放在工厂 `factory_tests/`。不把工厂 fixture 和性能 harness 分发到消费仓。

现有 `test_task_archive_dir.py`、`test_archive_at_integrate.py`、pending 与 hygiene 测试中直接验证搬目录的部分，按已变更契约明确替换或整组退役；保留其承载的内容保护、闭环、错误处理和恢复意图，并用新场景重新覆盖。不能只改路径字符串或删除失败断言，使门禁看似通过。

模板改造每个可验收整合点运行工厂全量。以下命令在工厂根执行，前提为 pytest 与 pytest-xdist 可用；临时产物使用系统临时目录，成功判据为全部必要用例通过，失败则停止推进并保留失败定位：

```bash
pytest repo/.repo_template/tests factory_tests -q -n auto \
  -p no:cacheprovider --basetemp "$(mktemp -d)"
```

最终还须从固定 Git 提交导出独立消费仓，运行其默认契约门禁，并在独立演练仓验证完整迁移与恢复。消费仓门禁在导出仓根执行；导出与演练方式沿用工厂测试工具，不直接操作真实消费仓：

```bash
pytest .repo_template/tests -q -m contract
```

## 完成标准与停止条件

完成需同时满足：固定布局和状态契约已进入模板；运行时没有旧实体路径 fallback、目录状态推断和生命周期搬运；同步不会重建旧结构；独立冷文稿仍可按约定归档；全量测试、消费契约、迁移和恢复演练通过；当前文档、skill、生成入口与实际行为一致。

架构验收还须核对：没有迁移专用的永久来源表，没有按迁移来源或旧格式选择运行路径的分支，没有逐实体版本解析器，没有仅为延续旧行为而保留的命令别名或路径豁免。迁移转换、备份和事务恢复不替代正常实体模型，也不成为日常命令的隐式依赖。

旧路径字符串允许出现在明确隔离的源布局检测、一次性迁移、失败诊断、迁移测试和历史文稿中。验收按调用职责分类，不以全仓搜索零命中替代架构检查。保留的 `git mv` 只能用于显式存储迁移或冷文稿归档，不能由业务状态转换触发。

以下情况停止对应写入阶段：备份不可恢复；执行现场未清；源状态有歧义；目标路径冲突；输入在预检后变化；无法加载一致工具链；校验失败或回滚不完整。报告实际路径、已完成动作、剩余状态及恢复入口，不以静默忽略、默认状态、自动删分支或路径兼容层继续执行。

本方案实施收益以正确性和职责收敛验收。性能变化在最终版本重新测量；不预先承诺 Git 调用降幅或全量提速比例。
