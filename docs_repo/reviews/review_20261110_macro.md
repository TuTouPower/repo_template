# 宏观审阅与改进方案：工作流、目录、分发与工厂治理

- 审阅时间：2026-11-10T08:36:12+08:00。
- 代码基线：`7dcb9193e59197a3c6b27139b483e1349920f8b3`，审阅期间工作区干净。文件阅读主要发生在 `1081146`；其后 6 个 commit 经 diff 核对不影响下文结论，量化数据均按基线重测。
- 审阅方：Claude（Fable 5）。应用户要求从宏观设计角度审阅，不评估代码细节。
- 本文状态：审阅意见与改进提议。第 4–6 节的全部提议**未裁决、未实施**。裁决后在 [decision_log.md](../decision_log.md) 加行，本文不承载裁决状态。
- 复核修订：2026-11-10T09:02:32+08:00。依据独立复核补充 S1 现状/目标口径与预算拆分、S3 阶段一耦合、S4 状态迁移、S6 两阶段拆分、S7 门禁配置保护；按现场校正在途 worktree 数量。修订不改变原文的总体判断。
- 文档边界：现行契约仍以 `repo/AGENTS.md`、`repo/.repo_template/docs/`、`repo/docs/blueprint/` 为准。
- 证据边界：
    - 本次审阅未运行测试。全量结果引自 [四线合并审阅处置](review_20261110/adoption.md)：`1610 passed, 1 skipped`。
    - 未逐行审计脚本。涉及门禁内部语义的提议（S4 第 4 点）标注了“需设计核对”。
    - 消费仓数据来自本机只读清点。
    - 改进方案中的删减行数按现有模块规模给出，是上限估计，不是实测。
- 编号：问题为 M01–M16，改进方案为 S1–S9，待裁决项为 Q-01–Q-18。

## 1. 结论

工具链的正确性不是当前的主要问题。问题在结构：四个根因持续制造复杂度，工作流缺口与目录问题多数是它们的下游。

1. **分发采用整树复制（M01）**。同步引擎、迁移和消费仓契约测试都为维护副本而存在。
2. **加固没有准绳（M02）**。没有成文的信任模型，审阅采纳范围和代码量持续扩张。
3. **本机状态分散（M03）**。状态放在五处，多个权威互相校验，恢复流程因此膨胀。
4. **所有权边界穿过文件内部（M04）**。模板内容和项目内容混在同一个文件里，同步只能靠语义合并和专门迁移。

时机上有一个窗口。本机 12 个消费仓全部停在 `988fd06`（2026-11-06），落后基线 173 个 commit，都未执行 entity-storage 迁移。布局类改动现在并入同一次迁移，每个消费仓只需要一次静默窗口（无在途 worktree、无 done 未合并分支的时段）。等 entity-storage 铺开后再改，每个仓要迁两次。

## 2. 证据基线

|项|数值|取得方式|
|---|---|---|
|工具链脚本|22,182 行；其中 `template_sync/` 4,954 行，迁移相关 6 个模块 2,358 行|`git ls-files` 加 `wc -l`|
|同口径历史值|14,852 行（消费仓内 `988fd06` 的副本）|同上|
|产物树|197 个文件，约 1.45MB；契约测试 6,584 行|`git ls-files repo`|
|CLI 面|`task.py` 32 个子命令（[11-04 宏观方案](../archive/plan_macro_overhaul_2026_11.md)记录 26 个）；skill 16 个|`task.py --help`、`registry.json`|
|常驻文档|`usage.md` 26.4KB（宏观方案记录拆分前 22.8KB、拆分后 17.1KB）；`architecture.md` 13.3KB（宏观方案记录 6.6KB）|`wc -c`|
|提交构成|共 531 个 commit，无 tag；2026-11-04 起 183 个，其中 78 个 `fix`|`git log`、`git tag`|
|工厂测试|59 个测试文件，21,481 行；全量 `1610 passed, 1 skipped`（宏观方案记录 691）|`wc -l`；结果引自 adoption.md|
|消费仓版本|12 个，全部 `988fd06`，落后 173 个 commit，均无 `entity_schema.json`|读取各仓 `sync_manifest.json` 或 `sync_state.json`|
|消费仓规模|`docs/archive/tasks/` 最多 594 个目录（omni_panel），其次 470（great_websites）；spec 最多 93 份；findings 最多 109 条|目录清点|
|在途工作|omni_panel 1 个、CBrowser 1 个已登记的 task worktree（复核时现场：`omni_panel_t600`、`CBrowser_t049`）|`git worktree list`|
|路径字面量|脚本中 `docs/tasks` 等实体路径约 230 处，测试中约 850 处|`grep -F` 计数|

## 3. 问题清单

每项给出现象、证据与后果。改进方向见第 4 节对应方案。

### 3.1 根因级

#### M01 分发模型：整树复制是同步、迁移、契约测试的共同来源

- **现象**：每个消费仓携带完整工具链副本。同步引擎、8 类迁移、消费仓契约测试，都是为维护这份副本而存在。
- **工具在行为上已经外置**：`repo-template-sync` 规定同步命令全部从来源脚本运行；pending、spikes、findings 等入口已支持显式 `--root`（L63）。分发方式却仍是复制。
- **后果**：
    1. 版本滞后。12 个消费仓全部落后 173 个 commit。
    2. 版本错位。task worktree 检出的是分支内的脚本副本，主仓与 worktree 可能用不同版本操作同一个 ledger。现行对策是同步前清空 active worktree（`repo-template-sync-core` 第 1 步），于是有在途 task 的仓连纯缺陷修复也拿不到。
    3. 迁移只增不删。迁移随产物永久分发，而迁移命令按规程从来源运行，消费仓内的副本没有调用场景。
    4. 环境耦合。契约测试进入消费仓只为校验副本，同时把 pytest 变成非 Python 项目的硬依赖，并让消费仓默认 `test_cmd` 测的是模板而不是项目。

#### M02 加固没有准绳，复杂度持续增长

- **趋势**：11-04 宏观方案把“复杂度增长快于价值、修补循环”列为第一个问题（P1）。此后指标继续上升：
    - 脚本行数 4 天内增加 49%（14,852 → 22,182）。
    - 子命令 26 → 32。
    - `usage.md` 超过瘦身前的体积。
    - 用例数 691 → 1,611。
    - 基线前最后 6 个 commit 中，finish journal 又从 v2 升到 v3。
- **根因**：多路审阅以“fail-closed 缺口”为产出单位，仓库却没有成文的信任模型与保证等级。采纳范围因此扩展到快照介质损坏、回滚进行中再次中断后的续滚、无 BOM 且 CJK 主导的 UTF-16 文本这类场景。本工具的使用形态是单用户、单机，行为主体是用户自己的 agent。
- **现状**：L69、L70、L71 已经出现逐项否决，但每一轮都要重新论证边界。

#### M03 本机状态分散在五处，多个权威互相校验

- **位置**：
    - `docs/runtime/`：attempt ledger、goal 队列、审计日志；task worktree 内还有 `execution_context.json`。
    - `.scratch/`：图事务、同步事务及其备份、来源缓存、状态行绑定、review prompt。
    - `.repo_template/sync_state.json`。
    - Git common dir：编号锁、控制面锁、`info/exclude` 托管块。
    - worktree 私有 Git 目录：finish 事务 journal。
- **后果**：
    - 导出、清理、gitignore、worktree 链接和迁机备份清单各自硬编码这些位置。
    - 持久事务放在定义为“一次性草稿”的 `.scratch/`，L68 只能让 cleanup 识别并避让事务。
    - 机器状态位于 `docs/` 之下。
- **权威重叠**：task 完成这一事实同时记录在 `progress.md`、ledger、`handoff.json` 和 Git 历史。integrate 把本机 ledger 事件当作硬门禁，尽管 done 分支上的回执、单一实现 commit 和 review 指纹都能从 Git 验证。`ledger import` 和 recovery 的多个阶段因此而存在。

#### M04 所有权边界穿过文件内部

- **`AGENTS.md`**：`## 目录与读写规则` 一张表里混着模板自有行（tasks、pending、findings、spikes、`.repo_template/`、`.scratch/`）和项目自有行（`src/`、`schemas/`、`config/`）。这一区在同步时由 agent 做语义合并，模板每改一条路径规则，每个消费仓都要合并一次。同形的表在 `usage.md`「工具链路径与写权」再出现一次，多行重叠而措辞不同。
- **`docs/blueprint/testing.md`**：归消费仓所有，却承载模板的机器契约（H2 章节名锚点、bash 围栏解析、单块规则）。契约变化只能人工迁移，见 `sync.md`「testing.md 单块门禁迁移」。
- **`docs/blueprint/conventions.md`**：归消费仓所有，却包含模板内部实现说明（隐私比较器、`FIXED_PATH_EXCEPTIONS`、原子写 helper），更新要靠专门的迁移模块。
- **配置寄生在 Markdown 里**：门禁命令从散文中解析；`specs_index.md` 是带严格 schema 的 Markdown 表。

### 3.2 工作流

#### M05 门禁严谨度倒挂

- 三道硬门禁中，项目测试和黑盒验证的证据是实施 agent 写在 `progress.md` 的摘要行。`finish` 据此生成回执，只能检查该行是否已填写。
- `repo_task/validation.py` 严格解析 `test_cmd` 等命令，但仓内没有执行方，所有调用点只做格式校验。
- 对照之下，软链 mode 位、blob OID、提交消息中的家目录路径都是机械 fail-closed。工程量集中在记账一致性上，最关键的质量证据依赖自觉与 reviewer 复跑。
- “验证通过后代码再变须重跑”只有文字约束，没有与交付指纹绑定。

#### M06 合并环节缺入口，命名误导

- **缺入口**：merge 是流程中唯一强制人工授权的节点，但 `task-integrate` 是内部 skill，只挂在 `task-run` 队尾。会话在合并前结束时（goal 模式夜间跑完、多会话各自收尾、用户想先看结果），没有公开入口可以回来合并。
    - 对已 done 的 tid 调用 `/task-run`，`plan --serial` 会以 `invalid_queue` 拒绝。
    - 剩下的途径是自然语言，而 `usage.md` 规定自然语言不构成显式授权。
- **命名**：`/task-merge` 的实际职责是合并重复的 backlog task（CLI 为 `merge-backlog`），真正的分支合并叫 integrate。想合并分支的用户最容易想到的命令名指向另一件事。
- **关卡位置**：链式栈叠加上延后合并，使人工关卡位于整条链之后。中间成员需要返工时已有后继分支，`recovery.md` 规定此时只能停止并报告。

#### M07 spec 只闭了半环；共享追加文件在并行链下冲突

- **只写不读**：`docs/specs/<slug>.md` 由 finalization 写入、由 finish 强制登记，但流程内没有读取方。`task-create` 不查现行 spec，review prompt 不注入，spec 也没有模板。消费仓已累积到 93 份。
- **共享追加点**：`docs/specs_index.md`（单表追加）与 `docs/blueprint/decisions.md`（单文件追加）在并行链下会冲突，性质与 L1 处理过的 `tasks_index.json` 相同。pending、findings、spikes 已改为一条目一文件，这两处没有跟上。

#### M08 hooks 独占 `core.hooksPath` 且为可选安装

- 直接提交路径上的隐私扫描、编号校验和 merge 来源校验由 hooks 承担。
- hooks 不调用项目自己的 hook。使用 husky、lefthook 等管理器的项目只能二选一。
- 未安装或使用 `--no-verify` 时，这些检查不存在。

#### M09 初始化是 11 步手工流程

- `export_template.py` 只负责导出。`git init`、`install-hooks`、来源登记、验证策略切换都要手工执行。
- 没有单一命令判定“初始化完成”。
- 来源路径在导出时已知，第 10 步却要再输入一次。

### 3.3 目录与命名

#### M10 `docs/` 混装四类内容，冷热不分

- **四类内容**：长期知识（blueprint、specs、guides、findings）、流程实体与过程记录（tasks、pending、spikes、reviews、`handoff.md`）、机器运行态（runtime）、归档（archive）。
- **规模**：L59 的原位流转消除了 rename 冲突，方向正确，但没有配套的规模策略。omni_panel 迁移后 `docs/tasks/` 将有约 600 个目录平铺，且只增不减。迁移前可以整体排除 `docs/archive/`，迁移后无法按路径把历史 task 排除在全仓检索之外。
- **归档语义不对称**：task、pending、spike 原位表达状态，spec 与 review 仍靠搬迁归档。`repo-hygiene` 作为一个公开 skill 加 225 行脚本，只做 review 目录的一次移动。

#### M11 命名

|名称|问题|
|---|---|
|`handoff`|`docs/handoff.md` 是项目交接记录，`handoff.json` 是执行回执，两者无关|
|`review`|task 目录内的 `review_*.md` 与 `docs/reviews/review_*/` 是两种产物|
|`repo`|`repo/`（产物）、`.repo_template/`（工具链）、`docs_repo/`（工厂笔记）、`repo_*.py`（脚本）四种含义|
|`dispatch_ledger.jsonl`|dispatch 已在 L35 退役，名称残留|
|`scripts/`|仓根 `scripts/`（项目脚本）与 `.repo_template/scripts/`（工具链）同名两层|
|`task-merge`|见 M06|

#### M12 task worktree 位于仓外同级目录（低）

- `../{repo}_{tid}` 与真实项目并列，按父目录通配的工具会把 worktree 当成项目；写域越出仓根。
- 本机父目录下的两个 task worktree 均已登记；复核时未见未登记目录。原记录中的 `omni_panel_t599` 已不在现场。

### 3.4 工厂治理

#### M13 没有发布边界

- `.github/workflows/` 为空（`d43deba` 移除 CI），仓库没有 tag。main 即发布通道，消费仓可以同步任意 commit。
- 2026-11-09 的一批修复在未运行测试的状态下进入 main，次日才补跑全量（见 [四线合并审阅原文](review_20261110/combined.md)末项）。
- decision_log 的 L60–L63、L65、L66 状态至今仍是“已实现，未运行测试”。

#### M14 文档债

- **变更史写进现行规则**：`usage.md`、`architecture.md`、`sync.md` 中“彻底废除、不再、取消、退役、旧”等措辞出现 28 处；“原位”在 `usage.md` 重复 11 次。消费仓 agent 每个会话都会加载这些文档，需要的只是现状。
- **表述依赖审阅轮次**：`recovery.md` 与 `sync.md` 中有“本轮只做静态核对”“本轮未运行迁移演练”。未验证这一事实应当保留，但读者不知道“本轮”是哪一轮。
- **悬空引用**：`repo/docs/blueprint/decisions.md` 指向消费仓内不存在的 `docs_repo/decision_log.md`。
- **位置不当**：`usage.md`「数据迁移」只对升级中的旧仓有意义，却常驻日常文档。
- **工厂笔记过期**：
    - decision_log 已从“多源议题裁决表”变成实施记录，单行数百字并含用例数，6 行状态过期。
    - `docs_repo/README.md` 写的 `reviews/2026-07/` 实际为 `reviews/review_202607/`。
    - `plans/` 下两份方案自述已实施，按 README 规则应在 `archive/`。

#### M15 工厂测试按来源命名、平铺

- 59 个测试文件平铺在 `factory_tests/`，部分按审阅轮次或 finding 编号命名，例如 `test_review_20261110_f01.py`、`test_legacy_fix2.py`、`test_worktree_view_P2.py`。
- `test_repo_sync.py` 与 `test_task_start_flow.py` 各约 100KB。
- 想找“某个模块的测试”没有路径可循，重复也难发现。

#### M16 工厂不使用自己的流程

- 工厂用 plans、reviews、adoption、F 编号这一套流程开发工具链，dogfood 验收已在 `d43deba` 移除。
- 流程体验的反馈只来自消费仓 issue。M13 的“未验证即入 main”正是产品流程要防的事。

## 4. 改进方案

每个方案给出目标、做法、验收和影响。全部为提议。

### S1 保证等级与复杂度预算（对应 M02）

**目标**：给审阅采纳和新增机制一条成文准绳。零代码，优先级最高。

**做法**：

1. 新增 `docs_repo/assurance_model.md` 作为唯一权威，工厂 `AGENTS.md`「修改原则」加一句指针。建议初稿如下，由用户修订：
    - 使用形态：单用户、单机、单主 clone；行为主体是用户自己的 agent。
    - 对手模型：会出错、会跳步、会中途崩溃的 agent，不是恶意者。有仓库写权限的人可以直接改脚本或使用 `--no-verify`，针对蓄意伪造的防御不在范围内。
    - 保证等级：

        |类别|承诺|示例|
        |---|---|---|
        |A 不可逆损失|机械阻断，必须有测试|删除含未提交改动的 worktree、覆盖用户文件、purge|
        |B 交付完整性|当前机械校验“合入内容等于已评审内容”；S7 落地后才承诺“已验证内容”|合入内容等于已评审内容；S7 后要求验证记录指纹一致|
        |C 崩溃一致性|检测并停止，给出可读诊断；单次中断可回滚|不承诺回滚过程再次中断后的自动续滚；不防介质损坏|
        |D 流程约束|只记录，不证明|用户批准调高轮次上限、merge 授权|
        |E 隐私扫描|以 `conventions.md`「路径隐私」声明的范围为准，范围外尽力而为|不再为罕见编码扩展范围|

    - B 的现状与目标必须分开。`testing.md` 明确状态机不代为运行命令，`finish` 只校验收尾报告文字摘要，因此 S1 不能把 B 写成已成立的完整保证；S7 是 B 从“内容一致”升级到“执行已验证”的前置条件。
    - finding 准入：属于 A 或 B，且可由“出错的 agent”或“单次崩溃”触发的，采纳。需要恶意者或双重故障才能触发的，默认拒绝并引用本模型。文档与实现矛盾的，照常修正。
2. 复杂度预算：
    - 分三个指标冻结：产物携带脚本行数（当前 22,182 行，S3 阶段一后应减少约 4,954 行）、工厂专属脚本行数、常驻文档字节数（`AGENTS.md` 加 `usage.md` 当前 32.4KB）；`task.py` 子命令与 skill 数单独记录。
    - 新增机制须在对应指标内删除等量内容，或在 decision_log 记录调高预算的裁决；缺陷修复不计入。
    - 预算数值存于单一文件，由一个工厂测试断言超限失败。
    - S3–S6 落地后按实测重设基线，不把“移出产物”误记为工具链瘦身。

**验收**：

- 模型文档经用户确认。
- B 的当前/目标口径写入模型文档；S7 前明确 B 不覆盖项目门禁执行。
- 预算测试在基线通过，人为超限时失败；三个指标分别可测。
- 下一轮审阅的处置文档逐项引用保证等级。

### S2 发布边界（对应 M13）

**目标**：把 main 的提交频率与消费仓收到的版本解耦，并保证发布版本经过验证。

**做法**：

1. 发布等于带注释的 tag。打 tag 的前提是该 commit 工厂全量通过，且导出产物的契约测试通过。打 tag 由一个工厂脚本完成，条件不满足时不打。
2. `repo-template-sync` 的正式同步只接受 tag 对应的 commit。其它 commit 归入 `sync.md` 已有的“开发验证”类别，不推进版本基线。URL 来源缓存在同步前显式 `git fetch --tags`；本地来源用 `git describe --exact-match` 核对 top commit 是 tag，而不是只看 SHA。
3. 每个 tag 附面向消费仓的发布说明：变更摘要、是否需要迁移、是否要求静默窗口、人工步骤。
4. 恢复 CI 不是前提，是否恢复另行决定。

**验收**：

- 对非 tag commit 执行正式同步被拒绝。
- 新 tag 在 URL 缓存拉取后可见，并按 tag 解析。
- 发布脚本在测试失败时不打 tag。

### S3 分发模型（对应 M01）

分两个阶段。阶段一是阶段二的子集，不会返工。

**阶段一：同步引擎与迁移移出产物（建议直接采纳）**

- 依据：同步与迁移命令已经全部从来源运行。消费仓内的 `template_sync/` 副本，本地调用仅见于初始化（`install-hooks`、`init --source`）、`link-skills` 与事务查询，这些同样可以从来源运行。
- 做法：
    1. `template_sync/`、`repo_sync.py`、`export_template.py` 迁到工厂根，不再进入产物。
    2. 来源只保留工厂仓（配合 S2 的 tag），不再支持以产物根作为来源。
    3. 迁移模块设退役规则：全部已知消费仓越过某个 schema 后，下一次发布删除对应迁移。
- 复核补充，阶段一不是纯移动：
    - `template_sync` 大量 import `repo_task`（`transaction.py`、`entity_storage_migration.py`、`specs_index_migration.py` 等）。移动后来源脚本必须从 `<product>/.repo_template/scripts` 显式加载 `repo_task`，不能依赖同目录 import。
    - `repo/.repo_template/tests/test_contract_suite.py` 直接 import `template_sync`；该测试须随同步引擎迁入 `factory_tests/`，否则产物契约测试会在移除后失效。
    - 12 个消费仓中 6 个的 `sync_state.template_source` 指向产物根。阶段一后该路径不再有 `repo_sync.py`，必须迁移 state 或让 launcher 从产物根回退到工厂根。
    - `repo/README.md` 的 `init` 与 `install-hooks` 仍调用消费仓本地 `repo_sync.py`，随入口迁移改写为来源脚本。
- 效果：产物携带脚本最多减少 4,954 行；消费仓内不再有用不到的迁移代码。工具链总行数不会因此下降。

**阶段二：工具链外置（需裁决，先做 spike）**

- 目标形态：工具链作为可安装的命令行工具发布。脚本只依赖标准库，可以用 `uv tool install` 分发，与 md_kx 的现行方式一致。消费仓只保留数据、`AGENTS.md`、skill 入口、模板文档和一个版本标记。
- 同步收缩为一条本地命令：升级工具后，由工具把自带的 skill、文档与 AGENTS 模板区写入仓库，并执行数据迁移。来源身份、checkout 固定、manifest、policy、state、产物导出和消费仓契约测试都不再需要。
- 同一仓库的主仓与所有 worktree 由同一个已安装版本操作，版本错位消失；纯缺陷修复不需要静默窗口。
- spike 要回答的问题：
    1. 版本绑定粒度：单机单版本，还是每仓固定版本。前者简单，但 schema 升级会同时影响所有仓。
    2. schema 升级时，在途 task 如何收尾。
    3. skill 与文档留在仓内时，分支内旧 skill 文本与新工具的兼容规则。
    4. hooks 的落点。
    5. 离线可用性，以及解除 Python 3.9 下限后的收益。
- 没有 spike 结论就不承诺阶段二。阶段一的收益不依赖它。

**验收（阶段一）**：

- 导出产物不含 `template_sync/`。
- 从工厂 tag 对一个旧版消费仓副本执行 status、plan、apply 全流程通过。
- 对一个来源仍指向产物根的旧版消费仓副本执行同步通过（state 迁移或 launcher 回退有测试）。
- `template_sync` 契约测试迁入 `factory_tests/` 后通过。
- 产物契约测试通过。

### S4 本机状态根（对应 M03）

**目标**：本机状态只有一个位置；完成与合入的事实只有一个权威。

**做法**：

1. 主仓级状态全部放入 `$GIT_COMMON_DIR/repo_template/`：ledger（改名 `ledger.jsonl`）、审计日志、goal 队列、状态行绑定、同步本机状态、图事务、同步事务及其备份、两把锁。
2. worktree 级状态（`execution_context.json`、finish journal）放入各 worktree 的私有 Git 目录。
3. `.scratch/` 只保留草稿与可重建的缓存：review prompt 渲染结果、来源缓存、issue 草稿。
4. 删除随之失效的特例：
    - `docs/runtime/` 与 `sync_state.json` 的 gitignore 行。
    - 导出时的不可分发路径判定。
    - worktree 链接对 `docs/runtime/` 的排除。
    - cleanup 对未结束事务的识别，以及为此持有的控制面锁。
5. 权威收敛（需设计核对）：task 完成与合入的事实只认已提交证据（分支 tip 的回执、单一实现 commit、review 指纹）和 Git 状态。ledger 只承担运行期互斥（claim）与 blocked、failed 审计。integrate 不再以 ledger 的 terminal、report 事件为前提，`ledger import` 与对应的 recovery 阶段可以删除。
    - 复核补充：`cmd_integrate` 目前从 ledger 解析 identity，`_preflight_chain` 逐成员读取 current attempt，`cleanup-worktree` 也依赖 terminal 事件。设计须明确 identity 改从 `handoff.json` 解析、保留 active claim 检查，并以“无登记 worktree、分支 tip 未变化”作为可合并前提；在替代校验完成前不得删除 `ledger import`。

**状态迁移（复核补充）**：迁移窗口内须把 `docs/runtime/` 的 ledger、审计日志、goal 队列，worktree 的 `execution_context.json`，`.scratch/` 的图事务、同步事务及其备份，以及 `.repo_template/sync_state.json` 的来源身份迁入新根。`sync_state` 含来源身份，不能靠重新 init 隐式恢复；审计日志与事务备份须保留原字节。迁移前按 `sync.md` 备份 Git common dir 与原状态目录，并在副本核对。

**理由**：Git common dir 被所有 worktree 共享，不入库、不随导出、不被 cleanup 触及，迁机时备份一处即可。现有的两把锁已经放在这里。

**验收**：

- 主仓 `git status --ignored` 不再出现 `docs/runtime/`。
- 同步事务未结束时用 `repo-cleanup` 清理 `.scratch/`，事务仍可恢复。
- 删除本机 ledger 后，一个 done 且未合并的分支仍能通过 integrate 的全部校验（对应第 5 点）。
- 状态迁移后 ledger、goal 队列、审计日志、sync state 与迁移前逐项一致；事务备份可恢复。

**风险**：

- `git worktree remove` 会连带删除 worktree 私有目录中的状态，`run-close` 的清理顺序需要确认。
- 第 5 点触及 integrate 的门禁语义。复核已核对 `integration.py` 的 identity、chain preflight 与 cleanup 调用点，仍建议单独立设计后再裁决，不与第 1–4 点同批。

### S5 文件级所有权与结构化配置（对应 M04）

**原则**：一个文件要么归模板（同步时整文件覆盖），要么归消费仓（同步永不触碰）。

**做法**：

1. 门禁配置迁出 Markdown。
    - 新增消费仓自有的结构化文件，建议 `.repo_template/project.json`，与 `sync_manifest.json`、`sync_policy.json` 同为该目录下的消费仓文件；也可放仓根，由用户定。现行 Python 3.9 下限没有内置 TOML 解析，故用 JSON。
    - 字段为 `verification_policy`、`verification_notes`、`doctor_cmd`、`test_cmd`、`blackbox_verify`。每个门禁是一条命令或空值，多步逻辑由项目自己的脚本承载。
    - `testing.md` 退回为纯说明文档；模板的“门禁类别清单”移入模板文档。
    - 围栏解析器、单块规则、占位符回显检测随之删除。
    - `project.json` 必须加入同步 `PROTECT_NAMES`，从模板硬同步中排除；其门禁字段变更须进入 review scope。`.repo_template/` 根由模板管理，消费仓自有文件需要显式所有权标记，不能靠目录名推定。
2. `AGENTS.md` 改为两区。
    - 项目区：项目介绍与项目自有目录规则，归消费仓。
    - 模板区：模板工作流目录规则与开发原则，同步时强制替换。
    - agent 语义合并与 `--agents-file` 候选校验随之删除。
    - `usage.md`「工具链路径与写权」只保留 `AGENTS.md` 未覆盖的工具链内部路径，重叠行删除。
3. `conventions.md` 中的「原子写」「路径隐私」移入模板文档，该文件只留项目约定。`conventions_migration.py` 在铺开后删除。
4. `.gitignore` 的模板规则改为带起止标记的托管块，整块替换。S4 之后所需规则更少。现有 `user_prompts` 保护语义需先核对。

**验收**：

- 改动模板区一条规则后，同步一个消费仓副本不需要任何人工或 agent 合并。
- `project.json` 缺字段或类型错误时，start 与 finish 拒绝并给出字段名。
- `project.json` 被修改但未进入 review scope 时，finish 拒绝；`pending-fix` 不得静默弱化门禁字段。
- 消费仓任意改写 `testing.md` 不影响门禁。

**影响**：

- 一次性迁移需要把各消费仓 `testing.md` 中的命令和 `AGENTS.md` 的项目行抽出。这一步仍需 agent 参与，但只发生一次。
- 消费仓此前对模板行的定制需要改写到项目区。

### S6 目录布局与命名（对应 M07 的索引部分、M10、M11）

**复核结论：拆为两阶段。** S6a 只做 task/pending/spike 根迁移与路径单点化，保留 `docs/reviews`、`docs/archive`、`specs_index.md` 与 ADR 单文件；S6b 再做 review 目录移动、取消归档、spec front matter、派生索引和 ADR 拆分。S6a 先在 1–2 个仓副本 pilot，再铺开；S6b 等 S6a 稳定后再做。理由：取消归档与移动 reviews 会同时改变隐私扫描、Markdown 格式化、review scope 排除和外部评审工具输出，影响面远超路径改名。

**目标布局（消费仓，S6b 完成后）**：

```text
docs/              长期知识：blueprint/、specs/、guides/、findings/
work/              流程实体与过程记录：tasks/、pending/、spikes/、reviews/、handoff.md
.repo_template/    模板工具链与模板文档
.scratch/          草稿与缓存
```

S6a 只把 `tasks/`、`pending/`、`spikes/` 与 `handoff.md` 从 `docs/` 移到 `work/`；`docs/reviews/`、`docs/archive/`、`specs_index.md` 与 `decisions.md` 暂留，形态不变。

**做法**：

1. 先把实体路径收敛为单一定义。脚本与测试都从一处取路径，之后再改值。路径字面量在脚本中约 230 处、测试中约 850 处，直接替换风险高。
2. 按性质分根。流程实体迁到 `work/`（目录名由用户定，避免与消费项目已有目录冲突），`docs/` 只留长期知识，`docs/runtime/` 按 S4 移走。实体仍然终生原位，L59 的路径稳定性不变，只是根不同。
3. 取消归档目录（S6b）。
    - spec 增加 front matter（`slug`、`tasks`、`updated`、`status`），废弃时原位标 `retired`。
    - review 会话产物原位保留。
    - `docs/archive/`、`repo-hygiene` skill 与 `repo_hygiene.py` 删除。
    - 这一点修订 L59 中“archive 保留 reviews 与 specs”的边界。
4. 索引改为派生（S6b）。删除 `specs_index.md`，由 spec front matter 派生清单。finish 的登记检查改为“当前 tid 出现在某份 spec 的 `tasks` 中，或 `progress.md` 声明不适用”，语义不变。
5. ADR 一条一文件（S6b）：`docs/blueprint/decisions/<日期>_<slug>.md`，以文件名互相引用，不需要取号。
6. 命名：
    - `dispatch_ledger.jsonl` 随 S4 改为 `ledger.jsonl`。
    - `handoff.json` 改为 `receipt.json`。文件在第 2 点中本来就要移动，改名不增加迁移次数。
    - `docs_repo/` 可改为 `factory_docs/`，与 `factory_tests/` 对称。仅涉及工厂，可选。

**复核补充，S6b 的跨模块影响**：取消归档不是删除一个目录，至少同时触及：

- `repo_task/privacy.py` 的 `PRIVACY_EXCLUDE_PREFIXES`：历史 review/spec 将进入显式审计范围。
- `md_format.py` 的 `docs/archive/` 黑名单：历史文稿会被格式化。
- `monitoring.py` 的 `SCOPE_FINGERPRINT_EXCLUDES`：review scope 的排除边界。
- `_id_scan.py` 的 archive 布局校验。
- `repo_cleanup.py` 对 `docs/runtime` 与旧 archive 审计日志的硬保护。
- `.gitignore` 的 `_meta/` 规则、`export_template.py` 的不可分发路径、外部评审工具默认写入 `docs/reviews/` 的路径。

上述任一项未同步修改，S6b 都会产生静默行为变化。S6b 的验收必须包含行为测试，不能只 grep 路径。

**规模策略**：分根之后，历史 task 仍与在途 task 同目录。不建议再引入冷热搬迁，那会带回 L59 消除的多位置推断。建议改为验证规模下的可用性：在一个约 600 个 task 的仓副本上测量 `view`、`plan`、`effective-status` 与取号的耗时，写入发布说明。超出可接受范围时，再考虑创建时即确定的编号分桶路径。L71 否决的是备份恢复演练；这里是性能测量与 dry-run 核对，是否在真实仓副本上执行由用户定。

**验收**：

- S6a：迁移 dry-run 在全部 12 个消费仓输出完整映射，零冲突；先在一个仓副本完成实际迁移与回滚演练。
- S6a：迁移后 `task.py list`、`view`、`recovery` 在副本上的结果与迁移前一致。
- S6b：`privacy-audit`、`md_format`、review scope、`_id_scan` 在取消归档后按新边界工作；历史泄漏不会无提示改变门禁结论。
- S6b：并行两条链各自更新不同 spec 并新增 ADR，integrate 无冲突。
- 产物内 grep `docs/archive`、`specs_index` 无现行引用，作为辅助检查而非唯一验收。

**影响**：

- 这是破坏性布局变更，取代尚未铺开的 entity-storage 迁移目标，见第 5 节。S6a 与 S6b 可以在同一次迁移窗口内连续执行；拆分是为了分离设计与验收，不是承诺两次窗口。若 S6b 未准备好，可先铺开 S6a，之后再对 spec/review 做一次范围更小的冷文稿迁移。
- `repo/AGENTS.md` 提到的外部评审工具写入 `docs/reviews/`，其输出路径需要同步调整；不能把工具默认路径变化留到运行时发现。
- 现有 `decisions.md` 内容拆成一条一文件、spec 增加 front matter 属于内容迁移，不是机械改名；需要独立迁移脚本与逐项核对。

### S7 门禁执行与 spec 读取（对应 M05、M07 的读取部分）

**做法**：

1. 新增验证命令（例如 `task.py verify {tid}`）。它在 task worktree 内执行 S5 配置中的 `test_cmd` 与 `blackbox_verify`，把退出码、命令摘要和当时的交付指纹写入 worktree 级状态。指纹复用 review scope 的现有计算。
2. `finish` 要求存在通过的验证记录，且记录指纹等于当前交付指纹。不相等时提示交付内容在验证后发生变化。记录随后写入回执。
    - `blackbox_verify` 为空值时按策略跳过。
    - `[deploy]` 类 AC 等人工验证仍为文字证据，并在回执中标明是人工证据。
3. integrate 的合并后验证采用同一机制，记录绑定待提交的合并 tree。
4. spec 读取：
    - task 模板上下文区增加“关联 spec”一行。
    - `task-create` 在拆分前读取相关 spec 并填写。
    - `render_review_prompts.py` 把关联 spec 的路径提供给 reviewer，用于核对是否破坏既有行为。
    - 新增最小 spec 模板。

**依据与边界**：`testing.md` 现行表述是“状态机只校验格式，不代为运行命令”。本方案改变这一契约，需要裁决。L55 的裁决范围是同步不执行项目门禁，不涉及 finish。

**门禁配置保护（复核补充）**：`project.json` 归消费仓所有，实施 agent 也可能修改。必须把它纳入 review scope 或受保护核心资产，`pending-fix` 不得静默改 `verification_policy`、`test_cmd`、`blackbox_verify`；否则 B 级保证可被“把 test_cmd 改成 true”绕过。验证记录与交付指纹绑定后，`project.json` 的变化同样应使旧记录失效。

**代价**：门禁命令必须是确定的、能在 worktree 内完整执行的命令。套件很慢的项目需要自行定义较快的 `test_cmd`。integrate 的合并后验证会重复执行项目测试，慢套件需要在策略中定义合并后验证范围或明确的替代证据；不能默认无限等待。

**验收**：

- 测试失败时 finish 拒绝。
- 验证通过后修改任一交付文件，finish 拒绝并要求重跑。
- 验证通过后修改 `project.json` 门禁字段，旧验证记录失效。
- 回执中的验证字段可由 integrate 复核。

### S8 入口、hooks 与初始化（对应 M06、M08、M09）

**做法**：

1. `task-integrate` 改为公开入口。无参时只读列出全部待合并的单体与链尾（复用 plan 的分链结果）并请求授权。`task-run` 队尾改为转入同一流程。
2. `task-merge` 改名，建议 `task-combine`。不把 `task-merge` 这个名称复用给分支合并，避免旧习惯触发新语义。L67 已有改名先例及迁移做法。
3. hooks：
    - `install-hooks` 检测到已有 `core.hooksPath` 时，不再要求 `--force` 覆盖，而是输出把模板检查接入现有 hook 管理器的片段。
    - 模板 hook 提供可被调用的单一入口。
    - 按 S1 的等级，完整性由 finish 与 integrate 保证，hook 定位为提前反馈。
4. 初始化：
    - 合并为一条命令：导出、`git init`、安装 hooks、登记来源一次完成，并输出剩余的人工步骤。
    - 初始化命令须对非空目标、已有 `sync_state` 与已有 `core.hooksPath` 做幂等检查；不默认 `--force`，不覆盖未确认内容。
    - 另提供只读的初始化检查命令，覆盖软链有效性、hooks、来源登记、md_kx、验证策略、占位符残留。
    - `repo/README.md`「初始化」缩减为调用这两条命令加人工步骤。

**验收**：

- 新会话中对一个 done 未合并的 task 执行 `/task-integrate`，可以完成合并。
- 在装有 husky 的示例仓中，模板检查与项目 hook 同时生效。
- 从空目录执行初始化命令后，检查命令除人工步骤外全部通过。
- 对非空目录、已有 state 或已有 hooksPath 重跑初始化时不覆盖现场，给出明确下一步。

### S9 文档与工厂卫生（对应 M14–M16）

**做法**：

1. 产物文档改写为只描述现状。
    - 删除变更史措辞。
    - “原位流转”只在 `architecture.md`「固定路径契约」完整定义一次，其余位置链接。
    - `usage.md`「数据迁移」移入 `sync.md` 或发布说明。
    - 未验证的事实保留，但改为不依赖审阅轮次的表述。
    - 删除对 `docs_repo/` 的引用。
    - 改写后重新测量常驻文档体积，据此下调 S1 预算。
2. 工厂笔记。
    - decision_log 每行只写议题、裁决、落点、状态；实施细节与用例数留在方案文档和 Git 历史。
    - 过期的 6 行状态由维护者依据 [adoption.md](review_20261110/adoption.md) 的验证记录更新。
    - 修正 `docs_repo/README.md` 的过期路径，把已实施的两份方案移入 `archive/`。
3. 工厂测试按被测模块分目录并改名，finding 编号只留在用例 docstring；拆分两个约 100KB 的文件。安排在 S3–S6 之后，届时相关测试会先经历一轮删减。
4. 自用：S3 阶段二落地后，工厂仓可以作为普通仓库由已发布版本的工具管理。在此之前不要求。S2 的发布门禁先落实“未验证不发布”。

**验收**：

- 产物文档中 grep “本轮” 无命中，review prompt 的轮次字段除外。
- decision_log 无“未运行测试”状态。
- `factory_tests/` 无以审阅日期或 finding 编号命名的文件。

## 5. 实施顺序与消费仓迁移

|阶段|内容|产出|
|---|---|---|
|0 治理|S1 修正版、S2|模型文档、预算测试、首个 tag|
|1 裁决与 spike|第 6 节各项裁决；S3 阶段二 spike；S4 第 5 点设计|裁决记录|
|2 工厂实现|S3 阶段一、S4 第 1–4 点、S5；每项独立提交并跑全量|main 上的同步与状态瘦身|
|3 布局 pilot|S6a 在一个仓副本实际迁移、回滚与规模测量|pilot 报告|
|4 合并迁移|S6a 全量迁移；S6b 就绪则同窗口连续执行，发布 tag|发布说明|
|5 铺开与收尾|12 仓同步；S7、S8；S9；删除无消费方的迁移模块|版本一致、预算下调|

**一次迁移直达**：12 个消费仓都还在 entity-storage 之前的布局。阶段 4 的迁移应当从该布局直接迁到目标布局，例如 `docs/archive/tasks/` 直接到 `work/tasks/`，而不是先到 `docs/tasks/` 再迁一次。现有 `entity_storage_migration.py` 的源布局识别与校验可以复用，目标路径改变。复核已确认本机 12 个仓就是当前全部消费方；铺开前仍应以只读清点复核一次。

**S6b 不阻塞 S6a**：若取消归档、spec front matter、ADR 拆分尚未完成设计与验收，阶段 4 先交付 S6a，S6b 另做一次只涉及 spec/review 的小迁移。不要为了“一次迁移”把未设计完的 S6b 硬塞进同一窗口。

**铺开前置条件**：沿用 `sync.md`「迁移维护窗口与规程」。停止所有会话，无已登记 worktree，无 done 未合并分支，工作区干净并完成备份。omni_panel、CBrowser 当前有在途 worktree，须先收尾。

**回退**：沿用 `sync.md`「降级与 revert」。S4 会移动本机状态，迁移前须额外备份 Git common dir 与原 `docs/runtime/`。

## 6. 待裁决清单

|编号|议题|推荐|涉及的既有裁决|
|---|---|---|---|
|Q-01|保证等级模型与复杂度预算（S1）|采纳；先修正 B 的现状/目标口径与预算拆分|无|
|Q-02|正式同步只接受 tag（S2）|采纳|无|
|Q-03|同步引擎与迁移移出产物；来源只保留工厂仓（S3 阶段一）|采纳；补 state 迁移、loader 与契约测试搬迁|L39 的来源形式|
|Q-04|工具链外置（S3 阶段二）|先 spike，再裁决|L39、L45|
|Q-05|本机状态迁入 Git common dir（S4 第 1–4 点）|采纳；补状态迁移与备份|L68 的事务避让|
|Q-06|完成与合入事实只认已提交证据（S4 第 5 点）|暂缓，单独立设计|L39 的 ledger import|
|Q-07|门禁配置迁出 Markdown；AGENTS 两区；conventions 拆分（S5）|采纳；`project.json` 纳入保护|L55 的单块规则；AGENTS 三分区协议|
|Q-08|流程实体迁到 `work/`（S6a）|采纳，目录名由用户定|L59 的路径，原则不变|
|Q-09|取消归档目录与 `repo-hygiene`（S6b）|暂缓，先完成跨模块影响设计与行为测试|L59 的 archive 边界|
|Q-10|spec front matter、派生索引、ADR 一条一文件（S6b）|采纳，但并入 S6b，先写内容迁移设计|#85 的 specs_index 严格 schema|
|Q-11|`handoff.json` 改名 `receipt.json`（S6 第 6 点）|仅在 Q-08 采纳时顺带|宏观方案 F4|
|Q-12|finish 与 integrate 执行门禁命令（S7 第 1–3 点）|采纳；先补门禁配置保护与慢套件策略|`testing.md` 的“不代为运行命令”|
|Q-13|spec 读取闭环（S7 第 4 点）|采纳|无|
|Q-14|`task-integrate` 公开；`task-merge` 改名（S8 第 1–2 点）|采纳|宏观方案第 6 节的“不改名”|
|Q-15|hooks 接入现有管理器（S8 第 3 点）|采纳|无|
|Q-16|初始化命令与检查命令（S8 第 4 点）|采纳；补非空目标与幂等检查|无|
|Q-17|文档与工厂卫生（S9）|采纳|无|
|Q-18|阶段 3 用一次迁移取代 entity-storage 迁移|采纳；先 pilot，12 仓已核实为全部消费方|L59、L62 的迁移路径|

## 7. 不建议改动

|项|理由|
|---|---|
|实体终生原位流转（L59）|消除了 rename 冲突。S6 只换根，不恢复搬迁|
|一 task 一 commit（L53）|已多轮裁决，本次未发现新证据|
|Git 原生 merge 事务；merge 单独授权|合并状态只有 Git 一个真相；授权点清晰|
|registry 单一调用图；禁止模型自动触发 skill（L33、L39）|调用关系与授权边界可机械校验|
|手动并发、看板与 goal（L35、L36）|已裁决保留|
|task worktree 位于仓外同级（M12）|放进仓内会让文件监听、测试收集和构建上下文误入嵌套 worktree；同级目录的代价主要是父目录观感。若要治理，可统一放入父目录下的单一容器目录|
|`repo/` 目录名|旧消费仓的 skill 文本与导出脚本依赖它定位产物根|
|备份恢复的真实仓演练（L71）|已否决，本文不重提|

## 8. 复核修订记录

- 时间：2026-11-10T09:02:32+08:00。
- 范围：核对证据数据、关键调用点与消费仓现场，未运行全量测试。修订不改变 M01–M04 的根因判断，也不改变“S1/S2/S3 阶段一/S4 第 1–4 点/S5 优先”的总体结论。
- 数据校正：在途 task worktree 现场为 2 个（`omni_panel_t600`、`CBrowser_t049`），不是原文的 3 个；未发现未登记的 `omni_panel_t599`。
- 口径修订：
    1. S1 的 B 级区分现状与目标，S7 是“执行已验证”的前置条件；复杂度预算拆为产物携带行数、工厂专属脚本行数、常驻文档字节数，缺陷修复不计入。
    2. S2 补 URL 缓存的 tag 拉取与本地 tag 精确核对。
    3. S3 阶段一补 `template_sync` 对 `repo_task` 的 import、契约测试搬迁、6 个产物根来源 state 迁移、初始化文档改写。
    4. S4 补状态迁移步骤；第 5 点明确 identity 解析、active claim 与 worktree 前提，暂不删除 `ledger import`。
    5. S5 补 `project.json` 的同步保护与 review scope 约束。
    6. S6 拆为 S6a（根迁移）与 S6b（取消归档、spec front matter、ADR 拆分）；补隐私、格式化、review scope、编号扫描、cleanup、外部评审工具的影响面。
    7. S7 补门禁配置防自弱化与 integrate 重复验证成本。
    8. S8 补初始化命令的非空目标与幂等检查。
- 裁决建议修订：Q-06、Q-09、Q-10 暂缓或并入后续阶段；Q-18 先 pilot。详见第 5、6 节。
