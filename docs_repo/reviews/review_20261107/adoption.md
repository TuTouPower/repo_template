# 审阅意见采纳评估与修改方案

- 评估时间：2026-11-07T14:04:06+08:00。
- 代码基线：`0ab5aef0c83bf9764821091e7a3b69ddea0b869f`；开始评估时，工作区仅有本轮审阅目录未跟踪。
- 来源：[DeepSeek](ds.md)、[Gemini](gemini.md)、[GPT](gpt.md)。保留三份原始报告，不修改历史证据。
- 本文状态：本轮技术裁决与待实施方案。采纳代表建议进入修改范围，不代表已实现或已验证；目标里程碑为下一轮模板架构修正，版本号待定。本轮只新增本文。
- 裁决依据：用户明确要求架构至上、拒绝妥协、零兼容性负担。开发难度、改动量、兼容成本均不构成保留设计缺陷的理由。数据备份、迁移与恢复仍须完整。
- 文档边界：现行契约仍见 `repo/AGENTS.md` 与 `.repo_template/docs/`；[decision_log.md](../../decision_log.md) 继续承担跨文档治理状态。本文不把待实施方案冒充现行规则，实施时同步相关裁决与契约。

## 最终结论

采纳或修改后采纳 18 个去重议题；拒绝 4 类具体主张。部分原意见拆成“问题采纳、修法拒绝”，因此两组数量不对应原报告条数。

优先处理提交事务、同步 schema 门禁、检查失败语义与自动解冲突的数据保护；随后完成上下文、派生索引、迁移边界和展示模型收敛。既有缺陷同样修复，不以“不是本轮引入”排除。

审阅不能直接照单实施，主要修正如下：

1. 显式、一次性、可恢复迁移不等于运行期兼容；不能因存在旧格式转换代码，就把迁移模块全部删除。
1. spike 已复用公共 ID 分配器，task 已共用公共锁。“三套独立扫描与锁体系”不符合当前源码。
1. `review.py` 吞解析错误成立，但现有处置表仍会检查 tid；不能据此声称已复现损坏元数据绕过 review PASS。
1. `/home/root/private/` 会被 `/root/` 子串规则命中；报告所述“root 两种路径必然一放一拦”不准确。
1. Markdown 表面写法多样不等于旧版本兼容；应约束语义与歧义，而非把只接受一种排版当作架构目标。

下文优先级：P0＝提交结果未知时可能错误恢复或破坏现场；P1＝状态、门禁、定义或检查结论不可信；P2＝架构边界与确定性收敛；P3＝无效防御和诊断清理。优先级只决定实施顺序，不减少采纳范围。

## 采纳项与修改方案

以下路径除 `factory_tests/`、`docs_repo/` 外，均相对 `repo/.repo_template/scripts/`。

### A01. finish 必须成为可恢复的持久提交事务（P0，采纳 GPT #1）

**依据与判断**：`repo_task/lifecycle.py::cmd_finish` 仅把过程文件和 Git index 快照保存在内存；`repo_task/git_ops.py::_git` 把超时包装为 `TaskDataError`，异常路径直接调用 `fail()` 恢复文件与 index。只有普通非零返回路径才检查 HEAD 是否前进。内存故障注入得到 `rollback_called=True、head_checked=False`，报告核心缺陷成立。现有 recovery 能识别部分中断阶段，但不能替代持久 before-image 与提交结果判定。

**修改方案**：

1. 在当前 worktree 的 Git 私有目录保存事务 journal 和经过完整性校验的恢复快照。记录事务身份、task/attempt/execution、原 HEAD、原 index、将修改文件及原存在性、预期交付指纹和阶段；快照持久化完成后才能写业务文件。
1. 将异常、超时、普通非零、进程中断统一纳入提交结果判定。先确认 Git 子进程及 hook 已结束，再解析实际 HEAD 和提交形状；HEAD 未前进且现场符合事务预期时才允许恢复。HEAD 已前进时验证提交与指纹，不覆盖已提交结果。
1. HEAD 无法读取、现场被外部修改或子进程状态未知时，保留 journal 与现场，明确输出“结果未知”，禁止自动回滚和盲目重试。恢复入口使用同一判定逻辑，恢复动作必须幂等。
1. 复用现有 `file_transaction.py` 中适用的文件快照能力，但提交 ref、worktree 私有 index、Git 进程结果必须由提交事务负责；不能直接把普通文件回滚套在 Git commit 外层。
1. 保留“一 task 一实现 commit”和 `review_mismatch` 约束。恢复事务不得偷偷追加 commit、amend 或撤销已确认提交。

**验收**：在写 done、写 handoff、暂存、提交前后、journal 推进前后注入中断；覆盖“实际提交成功但调用超时”、HEAD 查询失败、恢复再次中断、用户后续修改。提交已发生时不得恢复旧 index；未发生时精确恢复 finish 自身改动，保留原有暂存与未暂存内容。

### A02. 同步完成必须以目标 schema 合法为前提（P1，采纳 GPT #2）

**依据与判断**：`template_sync/apply.py::_apply_sync_write` 对 `specs_index_report()` 仅打印问题，后续仍能写 state 并标记事务 applied。`factory_tests/test_repo_sync.py::test_apply_reports_remaining_specs_index_violations` 明确要求该场景成功；测试用了 `skip_tests=True`，不能把它表述成正常全量门禁的运行复现。不过通用契约测试主要验证隔离夹具，并不能代替对当前消费仓数据的直接校验。

**修改方案**：同步分为计划、备份、写入/迁移、目标数据验证、工具契约测试、推进 state。直接调用统一 schema 校验器检查消费仓目标数据；残留违规抛出 `SyncError`，进入现有文件事务恢复。`--skip-tests` 只能跳过测试运行，不能跳过 schema 校验。status/plan 提前列出不可机械迁移的数据和停止原因；禁止猜测删列、丢行或把真实数据改成占位值。

**验收**：分别覆盖有真实 `supersedes` 数据、额外列、坏 slug、无损可迁移布局；失败时业务文件、工具链、manifest、state 均恢复；修正数据后才能完成同步。

### A03. repo_state 删除基线失效后的成功降级（P1，采纳 GPT #3）

**依据与判断**：`repo_state.py::cmd_changed_files`、`cmd_added_lines` 在 baseline 无效时只处理未跟踪文件，仍可退出 0；stderr 有降级警告，因此不是完全无声，但成功码无法证明检查完整。两个 CLI 的无效基线行为已只读复现。`factory_tests/test_repo_state.py::test_added_lines_baseline_unavailable_only_untracked` 还固化了该语义。

**修改方案**：需要 baseline 的命令先解析为确定 commit，失败立即非零退出，不输出可被当作完整检查结果的部分数据。`deliverable` 同步删除隐式存在性降级。只有调用方确实需要纯存在性判断时，才提供独立、名称明确的接口；不保留 `no-git` 兼容分支。检查所有调用方对退出码和管道失败的处理。

**验收**：无效 ref、非仓库、Git 故障均失败；有效 baseline 完整包含 tracked、staged、unstaged、deleted、untracked 改动。确认调用方无法把非零退出后的空输出解释为“零问题”。

### A04. backlog 契约基线只保留一个解析权威（P1，采纳 DS #1、#6 基线项）

**依据与判断**：`render_review_prompts.py::_resolve_baseline_spec_show` 硬编码 `main`，并用 `"status: backlog" in stdout` 判断状态；`repo_task/store.py::load_task_backlog_baseline` 使用 `default_branch()` 和结构解析。两条路径已分歧。受控调用显示 renderer 只尝试 main 与 anchor，不会使用 master。store 又把 `merge-base` 所有非零都当“不为祖先”，并吞掉主干或 anchor 的 `TaskDataError` 后换源。

**修改方案**：

1. 提取共享基线解析服务，以显式仓库上下文、tid、anchor 为输入，返回来源 commit、task 路径与解析数据；renderer、start 和监控统一调用。先把分支解析成 commit，保证同次读取一致。
1. `merge-base --is-ancestor` 严格区分 0、1、其他错误；仅 1 表示非祖先。严格区分“该 ref 确实不存在此 task”与“Git 失败、数据损坏”。仅契约明确允许的真实缺失才可换源。
1. 使用统一 front matter 解析，删除正文子串判定。合法主干名不是兼容层，不得用强制 main 掩盖问题；主干配置无法解析时失败。
1. renderer 不再把基线读取错误变成正常渲染成功。真实无历史契约的情况须有明确业务结果和提示，不能与检查失败共用空字符串。

**验收**：main/master/自定义主干、旧链分叉、anchor 缺 task、正文伪造 `status: backlog`、损坏 front matter、Git 返回 128。渲染与监控必须选同一基线；错误不得降级为“无 drift”。

### A05. 缺失、损坏、观察异常必须分离（P1，修改后采纳 DS #2、Gemini 三-3）

**依据与判断**：`store.py::_discover_effective_entries` 捕获全部 `TaskDataError` 后跳过分支；受控注入损坏 front matter 后，该 task 回落为 backlog。`review.py::parse_front_matter` 也把读取与解析错误转成 `{}`。不过 `_parse_disposition_rows` 随后要求合法 tid，因此尚无证据证明该包装已使损坏文件获得 PASS。

**修改方案**：底层明确区分 `TaskNotFound`、非法数据与 Git/I/O 错误，保留 ref、路径和原始原因。只有“新分支 tree 尚未包含 owner task”这一已确认情形允许继续。状态观察入口可展示带错误详情的不可用记录，不能伪装为正常 backlog 或正常 active；调度和写操作遇不可用状态必须阻断。review 删除 `{}` 降级包装，在 CLI 边界输出原始诊断。保留工作区局部损坏时继续观察其他 task 的能力，但使用显式异常状态，不把可观察性等同于执行放行。

**验收**：区分目录真缺失、已有目录缺 progress、非法/重复 front matter 键、tid 与目录不符、Git 故障。损坏分支不得从 effective 结果静默消失，review 不得返回 PASS；合法未提交新分支仍可观察。

### A06. 自动解冲突必须证明定义与过程内容没有被覆盖（P1，修改后采纳 DS #4）

**依据与判断**：`integration.py::_is_pure_backlog_to_done_member_conflict` 只比较 tid、slug、依赖与冲突字段，就允许对整个 progress 文件执行 `checkout --theirs`。title、review_level、轮次上限、note 和正文变化均未充分保护。报告成立，但“比较除四个运行字段外全部字段”仍不够：合法 review 升级、用户批准的 limits 变化也会发生，正文也可能丢失。

**修改方案**：按字段职责区分任务定义与执行状态，结合 merge-base、主干、执行分支作三方比较。只有主干没有独立定义/正文修改、分支变化符合已授权状态演进时，才能自动采用终态。定义变化、未知字段、独立正文编辑或无法证明来源时保留原生冲突，要求显式处理。task.md 继续只允许已有契约明确认可的 checkbox 差异；禁止把整文件 `theirs` 当作通用合并策略。

**验收**：主干改 review_level、title、note、limits 或正文时不被丢弃；合法单调升级和批准的 limits 变化可按规则合并；新增未知字段不得绕过比较。最终提交同时满足 review/handoff 校验。

### A07. 隐私门禁覆盖范围与读取版本统一（P1，修改后采纳 DS #3、#6 重复取数项）

> 后续修订（2026-11-10）：验收中「非本次变更的 tracked 泄漏」一项已被 R01 有意收窄。门禁只阻断本次新增文本行，存量由显式 `task.py privacy-audit` 处理，现行口径以 `repo/docs/blueprint/conventions.md` 为准。见 [review_20261110 F13](../review_20261110/adoption.md)。

**依据与判断**：`repo/docs/blueprint/conventions.md` 约束已跟踪非归档文本文件；pre-commit 只取 staged Markdown，finish 只取 staged docs 和当前 task 目录。缩小文档范围会弱化现行契约，因此拒绝。两处 staged 查询的 pathspec 不同，并非完全相同命令，但可以共享候选清单。`.scratch/` 排除在只枚举 docs 的查询中确实无效。

**修改方案**：建立公共“候选文件枚举＋内容扫描”入口，将隐私扫描与 Markdown 格式化选文件彻底分开。提交门禁扫描整个 index 中非归档文本，使用 index blob，避免工作区干净内容掩盖 staged 泄漏；finish 检查预期提交快照并在提交后核对实际快照。当前 task 尚未暂存但将由 finish 纳入的文件也必须覆盖。保持现行契约覆盖全体已跟踪文件，不能仅改成检查本次变更文件。无法读取候选内容必须显式失败，不能沿用 `scan_path_privacy` 的 OSError 后继续。门禁切换前检查既有受跟踪内容；测试所需路径使用运行时构造，公开示例使用占位符，不给测试目录开整片豁免。

**验收**：覆盖根目录 `.txt`、`src/*.py`、JSON、无扩展名文本、非本次变更的 tracked 泄漏、部分暂存、删除/重命名、二进制和归档排除；“index 有泄漏、工作区已修正”仍应阻断。格式化仍只操作 Markdown。

### A08. 隐私路径识别统一边界与例外策略（P2，部分采纳 DS #5）

**依据与判断**：`repo_task/privacy.py` 的 POSIX/Windows 规则要求用户名后还有分隔符，裸家目录会漏报；已用纯函数调用验证。`CONTAINER_USERS` 又按用户名全局豁免，不能证明路径不含开发者信息。另经验证，`/home/root/private/` 被 ROOT 规则以子串 `/root/private` 命中，而 `/home/root/` 未命中；原报告将其概括为一致豁免不准确。

**修改方案**：按完整路径 token 识别 macOS、Linux、Windows 家目录，支持路径终点和标点边界；ROOT 规则不能从另一条路径中间误起匹配。取消仅凭用户名认定容器路径的全局放行。确有非个人环境固定路径需求时，在单一策略中列出有依据、边界明确的完整路径例外，并统一用于所有门禁；没有环境依据不新增白名单。当前 Chrome 例外也须按该规则复核。

**验收**：裸家目录、子路径、Markdown 标点、单双斜杠形式、相似前缀、嵌套路径均得到确定结果；同一输入在 review、finish、hook 中结论一致。报告中的示例与测试夹具必须使用占位用户名。

### A09. task 目录物化不得改变 Git 对象类型（P2，采纳 DS #6 类型项）

**依据与判断**：`integration.py::_materialize_task_dir_from_ref` 仅处理可执行位，其他条目均按普通文件写入；symlink blob 会变成存放链接目标字符串的文件。

**修改方案**：先检查完整 tree 清单，只接受当前 task 文档契约明确支持的普通文件模式 `100644/100755`；symlink、gitlink 等不支持类型在删除或写入目标目录前拒绝，报告路径与 mode。当前没有必须支持 symlink 附件的需求，不扩大类型能力。

**验收**：普通和可执行文件字节/权限正确；120000、160000 在零目标写入前失败，start 不残留新建 branch/worktree。

### A10. 迁移逻辑退出日常同步与修复路径（P2，修改后采纳 Gemini 一-2、一-4、一-5）

**依据与判断**：`workflow_migration.py`、`conventions_migration.py`、`specs_index_migration.py` 执行旧布局转换；`skills_links.py::repair_symlinks` 内嵌退役 merge_guard 清理。它们不应因行数或含旧名称就判为无价值，问题在于旧版本识别、转换、当前格式更新和日常同步揉在同一路径。

**修改方案**：

1. 保留现有有价值的无损转换逻辑，移入显式、版本范围明确的迁移入口。迁移计划须列输入、目标 schema、写/删路径、不可转换数据；完成备份并验证可恢复后才能执行。
1. 普通读取和普通 apply 只接受当前 schema；发现旧布局直接说明不支持及所需迁移，不自动尝试多代格式。当前模板规范块更新若仍属于同步职责，应与历史布局转换分离。
1. 退役 Hook 清理归入明确迁移步骤；仅删除已确认由模板管理的链接和配置，保留消费方自定义 Hook。日常 `repair_symlinks` 只管理当前 skill 链接。
1. 迁移仍须检查活跃 worktree、未集成分支及身份约束；这些是数据安全条件，不因删除兼容路径而取消。迁移完成后严格验证目标数据，再更新版本标记。
1. 不为本轮引入通用插件式迁移框架；使用明确的迁移步骤与目标验证即可。

**验收**：两代 workflow 布局均可在独立消费仓中显式迁移，业务正文和自定义配置保留；重名/并存文件、无法无损转换、活跃现场均拒绝。恢复测试证明原数据可还原；日常 apply 不再执行旧格式转换。

### A11. 删除运行期旧 state 与旧总账读取（P2，采纳 Gemini 一-1、一-3、GPT #5 对应部分）

**依据与判断**：`template_sync/state.py::read_state` 自动回退旧路径，init 又承担旧 state 合并；`_id_scan.py::_merge_legacy_body_numbers` 在正常编号分配中读取旧单文件总账。均属于运行期双轨。

**修改方案**：state 仅保留当前本地状态读取；旧 state 转换和冲突裁决进入 A10 显式迁移，移除普通 init 的隐式合并。旧路径、新路径并存时禁止猜测覆盖。注意 `sync_manifest.json` 的便携来源恢复是当前分发设计，不应因同属 fallback 就误删。

编号扫描只读当前文件/目录结构，删除旧正文编号解析。移除前必须处理仍参与分配的本地分支/worktree：旧活动数据先显式迁移或隔离出当前操作集合，不能直接删除旧扫描后造成重号。历史提交无需重写，也不因零兼容而删除旧分支数据。

**验收**：只有旧 state 时普通命令明确拒绝；显式迁移保留来源、prompts 与同步记录；坏 state 不回退；旧格式分支不能被正常分配流程悄悄忽略。迁移后仍验证跨分支/worktree 编号唯一性。

### A12. review 状态入口收敛并删除废弃参数（P2，采纳 Gemini 一-6、三-1、GPT #5 参数项）

**依据与判断**：`check_review_status.py` 是独立 CLI，`--max-review-round` 只读兼容参数可以让输出上限小于持久化上限。参数不能提高已批准上限，也不修改持久状态，因此不应描述为已复现提权。独立入口本身不是逻辑错误，但该能力属于 task 的 review 领域，统一入口更清晰。

**修改方案**：新增 `task.py review-status`，复用 `repo_task.review` 的业务逻辑；删除独立脚本和 `--max-review-round`，不留转发壳或参数别名。输出只来源于当前持久化 limits；提高上限仍走已授权 `limits` 流程。同步所有 skill、文档、测试和调用者，不以保留旧调用方便为由留下双入口。

**验收**：新入口与 finish/integrate 共用同一评估器；输出上限与持久值一致；旧脚本与旧参数引用仅允许出现在历史记录，当前产物中清零。

### A13. 创建 task 删除 schedule_status 防御清理（P3，采纳 Gemini 一-7）

**依据与判断**：`lifecycle.py::cmd_add` 仍执行 `fm.pop("schedule_status", None)`，但当前字段集已移除该状态。把旧模板字段悄悄删掉，使错误来源不容易被发现。

**修改方案**：删除该清理；创建前验证模板符合当前字段契约，过期模板明确拒绝。仅在显式旧数据迁移中处理该字段，不在新 task 创建时修补旧输入。字段验证应按模板自身契约处理，不借此删除消费项目合法扩展元数据。

**验收**：当前模板正常创建；含过期 schedule_status 的模板在建目录前失败；源码创建路径不再认识旧字段。

### A14. 派生 task 索引退出 Git 与生命周期写事务（P2，采纳 Gemini 二-1）

**依据与判断**：`store.py::rebuild_index_at` 写两份 JSON，多个写操作重建，脏改动门禁与格式化配置存在专门例外。其权威明确是 progress front matter，但派生产物仍参与提交。该设计不是“双权威读取”事故，却制造无业务必要的持久同步与冲突状态。[decision_log.md](../../decision_log.md) L1 目前明确选择入库，本轮建议以新裁决取代该部分，不静默改写历史。

**修改方案**：以 task 文件为唯一持久数据源，列表、调度、看板直接调用统一查询层；默认不生成磁盘缓存。删除两份受跟踪索引及重建写入、副作用补偿、脏改动特判、同步快照项和配置例外。需要机器可读导出时提供显式只读 JSON 输出，不再自动落盘。不为没有性能证据的需求新建缓存失效系统。

**验收**：从完全不存在索引的消费仓运行 add/edit/list/start/finish/integrate/recovery；查询和调度结果正确，不自动生成索引。删除索引前证明其可由权威文件完整重建；有独立手工信息或无法重建时先保存证据并停止删除。当前文档、脚本、测试夹具中不再依赖索引文件。

### A15. 仓库身份改为显式不可变上下文（P2，采纳 Gemini 二-2、GPT #7）

**依据与判断**：`repo_task/context.py` 由源码位置计算全局 root；`template_sync/context.py::configure_consumer` 改写整组全局路径；`rebuild_index_at(root)` 已说明两者目标错位问题。尚未复现实际写错仓库，但隐藏目标和跨调用状态属于真实架构缺陷，不能仅逐函数补可选 root。

**修改方案**：入口解析并校验目标后构造不可变仓库上下文，派生路径由 root 计算；所有文件/Git 操作显式接收对应上下文。同步中同时持有来源仓与消费仓上下文，不靠修改全局变量切换。业务常量与路径身份分离，删除可变 configure 和“缺 root 就用模块全局”的双轨。A14 完成后不必把待删除的索引 API 一并迁入新架构。

**验收**：同进程交错操作两个消费仓，分别读取和写入正确目标；源仓/消费仓物理分离时同步不写源仓；测试不再通过批量 monkeypatch 全局路径构造仓库身份。

### A16. 状态行绑定改用显式身份与结构化数据（P2，修改后采纳 Gemini 二-3）

**依据与判断**：`track_worktree.py::extract_tid` 解析任意 Bash 命令文本，writer 只凭关键词决定处理；多处 I/O 异常直接忽略。`template_sync/hooks.py` 提供可选安装入口，不能据此声称所有消费仓都已启用。其用途是状态行展示，故障不应阻断 task 提交，但必须可诊断。

**修改方案**：使用现有 attempt ledger 作为 worktree/终态权威，删除命令文本嗅探和无写入 marker 模式。宿主适配层显式建立 session 与 execution/owner 的绑定，核心命令只接收经过解析的身份，状态行从绑定和 ledger 读取当前 worktree。宿主 session_id 留在适配层，不把 Claude 专属字段灌入核心生命周期，也不再创建另一套任务事件总账。更新绑定或读取失败时输出可定位诊断；展示失败可降级为空，但不能伪造成功记录或阻断业务事务。

**验收**：无关命令含 `start`、`--tid` 不触发绑定；不同 session/attempt 隔离；失败 start、结束 attempt、删除 worktree 后不显示旧位置；损坏 payload、ledger、写入失败可观察。宿主实际联调尚未执行，实施时补齐。

### A17. 看板删除客户端停止原因推导（P2，采纳 GPT #6）

**依据与判断**：`repo_task/plan.py::compute_batch_plan` 输出 stop_reason；`view_server.py::_build_model` 注入后端 plan；`view_static/board.js` 仍以 JS `chainStopInfo` 兜底，`chain_plan.js` 再次分析依赖图和汇合点，重复业务推导成立。

**修改方案**：后端保证完整展示模型，删除 JS `chainStopInfo` 及仅供其使用的算法。前端区分字段缺失与合法空字符串：缺字段显示接口错误，空字符串按正常值渲染，不用 `||` 补算。只保留纯展示辅助函数，不把问题扩大为“前端不能有任何逻辑”。

**验收**：覆盖 DAG 末端、汇合等待、冲突暂缓、接续链与缺字段；CLI 计划与看板显示同源。测试通过服务端实际模型验证渲染结果，不继续维护 Python/JS 算法对照测试。

### A18. 格式化失败统一阻断，创建条目完整发布（P2，采纳 GPT #4）

**依据与判断**：`md_format.py::format_new_file` 明写为 CI 缺二进制保留警告后继续；pending/findings/spikes 创建侧忽略布尔返回值。问题成立，不能只改注释或在调用方多加一次检查。

**修改方案**：统一库函数失败语义：缺格式化器、执行失败抛明确异常，由 CLI 转换非零退出；生产代码不因测试环境缺依赖而降级。创建条目应在公共 ID 锁覆盖范围内完成“取号、生成最终内容、格式化、验证、原子发布”，格式化失败不发布正式条目，不输出创建成功。目录条目在临时目录准备完整后发布；编号分配继续以正式资产为准。创建路径结合 A15 显式传 root，避免格式化另一个仓库。

`lifecycle.py::_check_markdown_format` 还存在未启用模板 hook 时缺 md_kx 即跳过的同类路径，应一并删除，格式检查契约不取决于是否安装 hook。测试显式提供可控格式化器或在测试边界注入替身，另用真实依赖验证接口；不得只把原测试期望改成新的成功码。

**验收**：缺依赖、格式化非零、中途失败、进程中断均不得留下未完成正式条目；并发分配不重号；合法创建得到格式化后的完整内容。

## 拒绝项及理由

### R01. 因包含旧布局或代码量大而删除全部迁移代码

- 来源：Gemini 一-2、一-4、一-5 中把迁移本身认定为兼容违规的部分。
- 拒绝理由：schema 破坏性升级需要明确转换与恢复，迁移负责消除旧状态，不是让新运行时永久接受旧状态。直接删迁移会使旧数据无法进入新契约，或诱发人工丢失业务内容。518 行、正则或围栏状态机均不能单独证明架构错误。
- 替代方案：A10 将旧格式转换隔离到显式迁移，日常路径只认当前格式；一次迁完，不留运行期双轨。

### R02. 为“纯粹”而一律禁止 Markdown 修饰符和中英文标点

- 来源：Gemini 二-4。
- 拒绝理由：这些是当前人类可读报告的表示语法，不是旧版本协议兼容。现有测试明确覆盖 backtick、加粗和列表形式；现有检查还要求指纹匹配、AC 集合完整、TICK-VOID 阻断。报告未给出表示差异导致语义误判或门禁绕过的证据。删除表示能力不自动使领域模型更纯粹。
- 处理：保留有明确语法的等价表示。解析错误传播按 A05 修复；未来若发现歧义，收紧对应语法并补行为证据，不以全面强制单一排版代替问题定位。

### R03. 扩大容器用户名或 /root 固定路径白名单来消除误报

- 来源：DS #5 中“白名单过窄”的修法暗示；原文未提供其他固定路径证据。
- 拒绝理由：用户名、root 身份和运行在容器中均不能证明内容不含个人路径。扩大豁免会让隐私门禁对同形真实路径失效；且 root 示例的当前行为与报告描述不完全一致。
- 替代方案：A08 先统一 token 边界和单一策略，仅对有环境依据的完整固定路径设置例外。

### R04. 把四类 ID 强行改成同一套实体分配规则

- 来源：Gemini 三-2。
- 拒绝理由：事实前提不成立。pending/findings 使用 `_id_scan.allocate`，spike 使用同模块 `allocate_dir`，均通过 `id_lock` 复用 `git_common_lock`；task 也使用同一个 `TASK_ID_LOCK_NAME`。不存在报告所述 spike 独立扫描器或各自独立锁实现。
- 领域差异：task 由 `cmd_add` 限定主仓主干创建，并须检查 slug 与 task 文档；pending/findings/spike 允许多个分支与 worktree 创建，需要跨来源扫描。统一锁原语合理，强制相同分配域不能由“都有编号”推出。
- 处理：保留已有公共锁与条目扫描器；旧正文扫描按 A11 删除。当前证据不支持新增统一实体框架，也未复现 task 主干分配重号。

## 原报告逐项覆盖

以下标签按原报告顺序定位；Gemini 的“一/二/三”分别对应兼容、局部修补、边界割裂三组。

| 来源 | 原意见 | 裁决落点 |
| --- | --- | --- |
| DS #1 | renderer baseline 重复、main 硬编码、子串状态判断 | A04 |
| DS #2 | effective 分支吞 TaskDataError | A05 |
| DS #3 | 隐私门禁范围与文档不符、死排除条件 | A07；拒绝通过缩文档弱化门禁 |
| DS #4 | 伪冲突字段比较不全 | A06；进一步覆盖正文和合法执行变化 |
| DS #5 | 裸家目录漏报、root 策略、容器白名单 | A08、R03；修正 root 行为描述 |
| DS #6-a | materialize 丢失 symlink 类型 | A09 |
| DS #6-b | merge-base 错误与 baseline 读取被吞 | A04 |
| DS #6-c | staged 路径重复读取 | A07；两处原查询范围并不相同 |
| DS #6-d | 非 main 基线、坏 progress 负向测试缺口 | A04、A05 的验收项 |
| Gemini 一-1 | ID 扫描旧单文件正文 | A11 |
| Gemini 一-2 | workflow 多代迁移引擎 | A10、R01 |
| Gemini 一-3 | 旧 state 回退与合并 | A11 |
| Gemini 一-4 | conventions/specs_index 文本迁移 | A10、R01 |
| Gemini 一-5 | 退役 Hook 清理 | A10、R01 |
| Gemini 一-6 | max-review-round 兼容参数 | A12 |
| Gemini 一-7 | schedule_status 防御清理 | A13 |
| Gemini 二-1 | 派生索引入库 | A14 |
| Gemini 二-2 | 全局上下文与局部 root 参数 | A15 |
| Gemini 二-3 | 状态行命令嗅探 | A16 |
| Gemini 二-4 | Markdown 宽容匹配 | R02 |
| Gemini 三-1 | review 状态入口独立 | A12 |
| Gemini 三-2 | 多套 ID 体系 | R04；运行期旧格式问题另归 A11 |
| Gemini 三-3 | review front matter 返回空字典 | A05；未复现 PASS 绕过 |
| GPT #1 | finish 内存补偿、提交超时结果未知 | A01 |
| GPT #2 | 同步 schema 违规只提示 | A02 |
| GPT #3 | baseline 失效仍返回成功 | A03 |
| GPT #4 | 格式化为测试环境保留降级 | A18 |
| GPT #5 | state、ID、review 参数兼容残留 | A11、A12 |
| GPT #6 | 前后端重复 stop_reason 推导 | A17 |
| GPT #7 | 仓库身份依赖模块全局变量 | A15；属已核实设计债，未复现写错仓事故 |

## 实施顺序与验证

| 阶段 | 修改范围 | 完成判据 |
| --- | --- | --- |
| 1 | A01、A02、A03、A06 | 提交结果未知不误恢复；同步不接受坏 schema；检查失败不报成功；自动解冲突不吞定义 |
| 2 | A15、A14 | 文件/Git 目标显式；派生索引不入库、不自动落盘；同步不修改来源仓 |
| 3 | A04、A05、A07、A08、A09、A18 | 基线/错误/隐私扫描单一语义；对象类型不失真；创建发布完整 |
| 4 | A10、A11、A12、A13、A16、A17 | 显式迁移与运行时分离；旧入口清零；状态行和看板不重复推导业务 |

每阶段作为完整语义变更实施，不先引入长期并存的兼容入口。阶段间可能需要调整依赖顺序，但不得以“后续再改”为由把已删除权威的调用方留在旧路径。破坏性数据操作先验证备份可恢复；本文未授权操作真实消费仓数据。

实施须同步受影响的 skill、prompt、usage、architecture、recovery、sync、契约测试、工厂集成测试和 decision_log。L1 等旧裁决采用 successor 记录，保留历史事实；不批量重写旧 review。

工厂模板每阶段改动完成后，在工厂根执行全量门禁。前提是当前项目环境已安装 pytest 与 pytest-xdist；以下 `--basetemp` 使用项目明确要求的系统临时目录：

```bash
pytest repo/.repo_template/tests factory_tests -q -n auto -p no:cacheprovider --basetemp "$(mktemp -d)"
```

成功条件：全量退出 0，并逐项通过上述故障/数据边界验收；失败停止进入下一阶段，保留现场定位。涉及同步、迁移和上下文时，另在隔离消费仓验证目标数据及源仓不变。旧测试若明确固化已废弃降级契约，应说明替换依据，删除对应旧测试并新增目标行为测试，不批量改断言把新实现“调绿”。

## 本轮证据与验证边界

已回读相关实现、调用方、现行约定、decision_log 及关键测试。执行了不落盘的 Python 故障注入/纯函数探测与只读 CLI：

| 检查 | 观察结果 | 证据边界 |
| --- | --- | --- |
| finish 提交调用抛 TaskDataError | 调用 rollback，未调用 HEAD 检查 | 内存替身，不是真实超时 commit；实际异常包装已由源码核对 |
| effective owner 读取报损坏 | task 回落为 backlog | 内存替身，确认 catch 路径 |
| renderer 基线解析 | 查询 main 和 anchor，未查询 master | 受控 Git 返回值，确认选择路径；非 main 真实消费仓集成待实施时补齐 |
| store 的祖先查询/Git 与主干读取报错 | 仍从 anchor 返回基线 | 内存替身，确认错误被降级 |
| repo_state 无效 baseline | changed-files/added-lines 均退出 0，stderr 声明覆盖降级 | 在当前 Git 仓执行真实只读 CLI |
| 隐私识别 | 裸 POSIX/Windows 家目录漏报；root 子串行为与 DS 描述不同 | 直接执行当前纯函数 |
| 同步 schema | 当前实现只打印违规；既有测试要求此时成功 | 源码与测试交叉核对，本轮未执行同步 |

本轮未修改模板代码、未执行迁移、未安装 Hook、未提交或发布；未跑工厂全量测试。原因是本轮交付为评估文档，模板修复尚未实施。GPT 原报告的“1021 passed、1 skipped”属于其历史报告，本轮未复跑，不能作为本文方案已验证的证据。
