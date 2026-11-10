# 四线合并审阅处置

- 评估时间：2026-11-10T03:30:04+08:00。
- 代码基线：`1471b132031d80e3cc0c041ff6430ef47e8ff919`，评估开始时工作区干净。
- 来源：[四线合并审阅报告原文](combined.md)。原文未修改；下文「第 n 项」按原文出现顺序编号 1–41。
- 本文状态：处置裁决与实施方案。F01–F14 已于 2026-11-10 实施并合入 main；工厂全量 `1610 passed, 1 skipped`。各包实现与验证记录见 git 历史及本文件验收项。
- 用户裁决：第 8 项选「维持现状，只修措辞」；第 14 项采用评估建议；第 33 项不做。其余项为技术裁决。
- 文档边界：现行契约仍以 `repo/AGENTS.md`、`repo/.repo_template/docs/`、`repo/docs/blueprint/` 为准；跨文档治理状态见 [decision_log.md](../../decision_log.md) L69–L71。

## 最终结论

41 项中采纳 25 项（其中第 8、14、18 项为修改后采纳），拒绝 16 项。采纳项合并为 14 个修改包 F01–F14。

原报告有以下需要校正的地方：

1. 第 6 项不成立：`repo_task/cli.py:347` 已统一捕获 `OSError`，不会出现裸 traceback。
1. 第 10 项不成立：`tests/test_task_core_asset_guard.py:99-109` 已有 gitlink（160000）用例。
1. 第 20 项推测不成立：`SequenceMatcher` 的 equal 段只覆盖两侧完全相同的行，autojunk 只影响对齐质量，不会把新增文本归入 equal 段。
1. 第 8 项定性需修正：合并检查中的 note 子串匹配不是授权边界。`task.py limits` 由 agent 在自身 worktree 调用，`--reason` 由调用方自填，front matter 中的上限字段也没有门禁保护。「用户批准后才能调大轮次」是流程约束，代码层面从未提供机械保证；把授权记录迁到主仓审计日志不能弥补这一点。
1. 计数错误：第四组标题写 9 项，实际列 12 项；第 29 项所称「与第 31 项同源」对不上，第 31 项讲的是 hook 被绕过，不是编号扫描。

优先级：P0＝存在越权删除或隐私门禁绕过路径；P1＝违反 fail-closed，或存在静默降级；P2＝措辞、口径与回归用例。优先级只决定实施顺序，不缩小采纳范围。

## 采纳项与修改方案

以下路径除 `factory_tests/`、`docs_repo/`、`repo/docs/` 外，均相对 `repo/.repo_template/scripts/`。

### F01. 破坏性 worktree 操作强制校验 tid（P0，第 30 项）

**依据**：`repo_task/lifecycle.py:1396` 的 purge 调用 `remove_worktree` 时没有传 `tid`；同类破坏性调用 `lifecycle.py:597`（close）和 `lifecycle.py:1190`（rewind）都传了。不传 `tid` 时，`repo_task/worktrees.py:346-371` 的 `_check_worktree_ownership` 只比对路径和分支是否一致；docstring 写明这是「保持旧调用兼容」，对同源篡改的 `worktree`/`branch` 防不住。

**修改方案**：`remove_worktree`、`discard_worktree`、`_check_worktree_ownership` 的 `tid` 改为必填关键字参数，删除 `tid is None` 时的弱校验分支及「保持旧调用兼容」注释；purge 传入 `tid=args.tid`。只补传参数不够，以后新增调用照样可能漏传，所以由签名强制。

**验收**：构造 backlog task A，把其 front matter 的 `worktree` 和 `branch` 一致地改为指向 task B 的 worktree；`purge A` 必须拒绝，B 的 worktree 和分支保持不变。所有调用点在缺 `tid` 时报类型错误。

### F02. 宽字符解码统一，门禁、审计、候选三入口结论一致（P0，第 15、16、23 项）

**依据**：已复现。无 BOM 的 UTF-16LE 文本以 CJK 为主、内嵌家目录路径时，NUL 占比 2.4%，低于 `decode_candidate_bytes` 的 `len(data) // 4` 阈值，因此不尝试 UTF-16。随后 UTF-8 解码失败，又因含 NUL 跳过 GBK，最终抛出 `PrivacyScanError`。同一份字节在不同入口结果不同：`_text_or_binary` 遇到含 NUL 的数据返回 `None`，门禁和审计静默跳过该文件；`scan_candidate_bytes` 则直接报错阻断，合法的 UTF-16 文件在 finish 候选路径会被误拦。

**修改方案**：无 BOM 时取消 UTF-16 尝试前的 NUL 占比门槛：数据含 NUL 且长度为偶数，就按 NUL 分布决定先试 LE 还是 BE，能否当作文本由解码结果和可读性检查决定。UTF-32 试探维持现有逻辑。`_text_or_binary` 与 `scan_candidate_bytes` 共用同一判定，同一份字节在门禁、显式审计、finish 候选三个入口只有一种结论：要么作为文本扫描，要么作为不可读二进制跳过。

**验收**：无 BOM 的 UTF-16 LE/BE、以 CJK 为主且内嵌家目录的样本，在三个入口都报出泄漏；同类但不含泄漏的文件在 finish 候选路径不被误拦；UTF-32 BE 有 BOM 与无 BOM 各一条用例；PNG、zip 等常见二进制样本仍归为二进制。

### F03. 提交消息纳入 commit-msg hook 扫描（P0，第 17、13 项）

**依据**：`hooks/commit-msg` 只做合并来源与编号校验，只捕获 `IdScanError`；全仓只有 `repo_task/lifecycle.py:708` 的 finish 路径扫描提交消息。`repo/docs/blueprint/conventions.md:34` 写的是「提交消息作为本次新增文本单独扫描」，没有限定只在 finish 生效。因此裸 `git commit` 只要把家目录路径写进提交消息，就能穿过全部 hook。

**修改方案**：以文档口径为准，补实现。commit-msg hook 读取 `argv[1]` 指向的消息文件，截掉 scissors 行（`git commit -v` 附带的 diff）之后的内容，剔除注释行（遵循 `core.commentChar`），再交给 `scan_text_privacy_leaks` 扫描；普通提交和 merge 提交都覆盖。hook 对 `TaskDataError`、`PrivacyScanError`、`OSError` 分别给出可读报错，仍以非零退出（fail-closed）；编号校验失败与隐私扫描失败的报错文案分开。

**验收**：普通提交和 merge 提交的消息含家目录时被阻断；`git commit -v` 附带的 diff 中的存量泄漏不触发阻断；上下文解析失败时输出可读错误，不打印 traceback。

### F04. 识别路径中段的家目录（P0，第 18 项，修改后采纳）

**依据**：已复现。当前规则要求家目录位于 token 起点，以下四种形态全部漏检：`/System/Volumes/Data/Users/<user>`、`\\server\Users\<user>`、`//server/Users/<user>`、`C:Users\<user>`。

**修改方案**：只采纳前两种。macOS 数据卷前缀 `/System/Volumes/Data` 在真实工具输出中会出现；UNC 反斜杠形态是 Windows 专有写法，误报面小。`//server/Users/<user>` 拒绝：它与 URL 路径（如 `https://host/Users/name`）重叠，误报代价高于收益。盘符相对写法 `C:Users\<user>` 极少见，同样拒绝。

**验收**：前两种形态被识别，`https://host/Users/name` 维持现行结果；现有 A08 用例全部保持原结论。

### F05. finish 恢复快照双向校验，回滚中断后可续滚（P1，第 1、2、3、4 项）

**依据**：`repo_task/commit_txn.py:175-186` 写入 journal 的摘要是对源文件计算的，快照副本落盘后没有回读校验；`commit_txn.py:452-470` 回滚时从快照目录拷回，不与 journal 摘要比对；回滚过程不写 journal，中途再中断时，judge 只能判 unknown。现有测试中没有找到「提交成功但调用超时」和「回滚再次中断」两类用例。

**修改方案**：

1. 快照副本落盘并 fsync 后回读计算摘要，与源文件摘要不一致就中止 begin，此时尚未写任何业务文件。
1. 回滚前先逐个校验快照副本摘要；任一不符则拒绝回滚，保留 journal，判 unknown 交人工处理。
1. 回滚开始前在 journal 中原子写入 `rolling_back` 阶段标记。judge 识别该标记后判为可续滚，重跑 finish 时从快照幂等重做整个回滚，不再判 unknown。
1. 回滚完成后，task 目录的哈希集合必须等于 journal 记录的原始集合，index tree 必须等于 journal 记录的原始 index tree，两者都通过后才执行 `finalize`。

**验收**：补四条注入用例：提交已生成 commit 后调用超时，判 committed，不回滚；提交前超时，判 rollback_safe；回滚在替换 index 后、文件拷回途中被中断，重跑 finish 能完成回滚，现场与事务前一致；快照副本被篡改，回滚拒绝执行，并保留 journal。

### F06. A06 自动解冲突在 merge-base 端解析失败时 fail-closed（P1，第 9 项）

**依据**：`repo_task/integration.py:838-848` 中，merge-base 命令失败，或 merge-base 处的 `progress.md` 解析失败，都会把 `base_fm` 置为 `None`，后续「主干未相对 merge-base 改写」类检查全部跳过，退化为 head 与 branch 两方比较。

**修改方案**：区分三种情况：merge-base 处文件不存在，保持现行规则 6 的处理；merge-base 命令失败；文件存在但读取或解析失败。后两种直接返回 `False`，交人工解决冲突。

**验收**：merge-base 处的 `progress.md` 被写成不可解析内容时，冲突不被自动消解；分叉后才由主干新建该文件的场景，行为不变。

### F07. 消除剩余的隐式仓库上下文与静默回退（P1，第 24、25 项）

**依据**：`repo_task/store.py:714` 的 `resolve_baseline_spec_text` 在没有传入 `repo` 时以 CWD 作为仓库根；`repo_task/review_status.py:124` 的入口在找不到 `docs/tasks` 时同样退回 CWD；`repo_task/context.py:21-37` 的 `_git_common_root` 在 `.git` 存在、但 git 命令失败或超时时，静默把 `code_root` 当作主工作区根。

**修改方案**：`resolve_baseline_spec_text` 的 `repo` 改为必填，删除 `root` 与 CWD 回退；`review_status` 入口找不到仓库根时报错，并提示使用 `--root`。`_git_common_root` 仅在 `.git` 不存在时返回 `code_root`；`.git` 存在而 git 调用失败、超时或输出为空时，抛出 `TaskDataError`。

**验收**：库函数缺少 `repo` 时报类型错误；在 linked worktree 中模拟 git 失败时，构造 `RepoContext` 显式失败，不把 ledger 或 audit 路径指向 worktree 本地目录。

### F08. 主干探测失败时报错，不兜底返回 main（P1，第 26 项）

**依据**：`repo_task/git_ops.py:274` 在全部探测落空后无条件返回 `"main"`，与 A04「主干配置无法解析时失败」的要求不符。

**修改方案**：末端改为抛出 `TaskDataError`，报错中列出已尝试的探测来源，并给出修复命令（`git remote set-head origin --auto` 或 `git config init.defaultBranch <name>`）。

**验收**：仓库中只有 task 分支且没有任何可用主干配置时，报错内容可以定位问题，不再落到「main 不存在」这类含糊的后续错误。

### F09. added-lines 失败时零输出（P1，第 27 项）

**依据**：`repo_state.py:183-227` 中，tracked diff 的新增行和未跟踪文件内容都是边读边打印；有文件读取失败时，已打印的部分输出留在 stdout，最后才以退出码 1 返回。

**修改方案**：先把全部输出收集到内存，确认无失败后再一次性写出；失败时 stdout 为空、退出码为 1，与 changed-files 子命令一致。

**验收**：未跟踪文件在读取途中消失时，stdout 为空，stderr 给出路径，退出码为 1。

### F10. legacy-state 迁移纳入文件事务（P1，第 28 项）

**依据**：`template_sync/state.py:180-189` 直接 `write_state` 写入新 state，再 `unlink` 旧文件，没有备份快照，也没有写后验证；它是七类写迁移中唯一不走事务的一类。

**修改方案**：改用 `template_sync/transaction.py` 的 `FileTransaction`，与其余六类迁移一致：备份新、旧两个 state 路径，写入新 state，回读并核对合并结果，删除旧文件，最后接受事务；任一步失败就回滚。

**验收**：在写入后、删除旧文件前注入失败，回滚后现场与迁移前逐字节一致；迁移成功后，新 state 回读结果与合并预期一致。

### F11. 事务目录缺少 journal 时按损坏处理（P1，第 40 项）

**依据**：`repo_task/file_transaction.py:21-43` 的 `open_transactions` 在 `transaction.json` 不存在时视为没有事务，事务目录中残留的备份会被 scratch 清理按「无主」删除。这与 `ff7b5c4`「未知事务状态按损坏处理」的原则不一致。

**修改方案**：事务目录存在且非空、但缺少 journal 时，报告为 `corrupt`；scratch 清理随之跳过，并给出处理提示。

**验收**：手工删除 `transaction.json`、保留备份文件后执行 `repo_cleanup`，scratch 被跳过且备份保留；事务目录为空或不存在时，行为不变。

### F12. A06 授权检查改正措辞，写明信任模型（P2，第 8 项，用户选维持现状）

**依据**：见「最终结论」第 4 条。`review_level` 只允许从 single 升到 full，伪造只会让审查更严，降级方向已被代码拒绝；轮次上限调大缺的是「用户批准」这一步的机械保证，问题不在合并检查本身。

**修改方案**：不改行为。`repo_task/integration.py:793-806` docstring 中的「经授权」改为「经 CLI 记录」，说明该检查只确认上限变化带有 CLI 留下的记录，是一致性检查，不是授权证明。`repo/.repo_template/docs/architecture.md:38` 与 `repo/.repo_template/docs/recovery.md:36` 写明：「用户批准后调大上限」是 agent 必须遵守的流程约束，`--reason` 由调用方填写，代码不核验批准来源。

**不采纳**：交互式 TTY 确认、上限迁入主仓审计日志等机械强制方案。这类方案要同时改 CLI、review 门禁、integration 和 skill 文档，工作流也随之改变；用户已裁定当前不需要。

### F13. 隐私门禁存量口径定稿（P2，第 14 项，用户采用建议）

**依据**：`repo/docs/blueprint/conventions.md:33` 规定门禁只阻断本次新增文本行，未修改的历史行交给显式审计处理；[review_20261107 A07](../review_20261107/adoption.md) 的验收原文则要求覆盖「非本次变更的 tracked 泄漏」。两份文档冲突，R01 统一比较器时有意做了收窄。

**修改方案**：以 `conventions.md` 为准。门禁只阻断新增行，避免历史泄漏阻断无关交付；存量泄漏由显式 `task.py privacy-audit` 负责。在 review_20261107 的 A07 节首行追加后续修订说明，原文不改写。不在 repo_sync 或发布流程中加入自动存量审计门禁：那样会让消费仓的历史泄漏阻断同步，正是本次收窄要避免的无关阻断。

**验收**：A07 节有指向本文的后续修订说明；decision_log L70 记录该裁决。

### F14. 小修与回归用例补齐（P2，第 19、21、22、32、34 项）

| 原项 | 修改 | 验收 |
|---|---|---|
| 19 | 命中的 token 去掉结尾的 `.`（已复现：`/home/<user>.` 会把句末点号算进用户名） | 句末路径上报的 token 不含尾随点，阻断结论不变 |
| 21 | 补比较器层重命名用例：在 `--no-renames` 语义下，按新路径全文作为新增内容扫描 | 重命名且内容含泄漏时被阻断 |
| 22 | 补 pre-commit 用例，分别断言编号校验失败与隐私扫描失败两种报错文案 | 两种失败文案可区分 |
| 32 | `_id_scan.py:667` 的 `slug_from_title` 移出「旧单文件总账解析（迁移用）」小节，docstring 写明用于新建条目 | 注释与实际用途一致 |
| 34 | 补 R04 用例：扁平结构、完全没有 front matter 的 active spike，status 报待迁移 | 用例通过 |

## 拒绝项及理由

| 原项 | 理由 |
|---|---|
| 5 | 判断子进程是否结束只能借助锁文件推断，这是 Git 机制固有的限制；残留锁会导向 unknown，方向是 fail-closed，没有更强且可落地的判定手段 |
| 6 | 报告不成立：`repo_task/cli.py:347` 统一捕获 `OSError`；journal 保留，recovery 可以接续 |
| 7 | committed 不会让后续判定短路（`repo_task/recovery.py:60-70`），而是继续进入 committed_unreported 等 phase；所有清理路径都会再走 `_accept_finish_txn` 复验，不是放行漏洞 |
| 10 | 报告不成立：`tests/test_task_core_asset_guard.py:99-109` 已覆盖 gitlink（160000） |
| 11 | 冲突文件本来就不可能与 merge-tree 的自动合并结果一致，这是 docstring 明示的放行，并有核心资产、隐私、编号三道侧栏约束；对冲突解决内容做内容级复核超出机器判定能力 |
| 12 | 前缀匹配只用于定位候选提交，命中后仍须通过全套内容认领校验 |
| 20 | 推测不成立，理由见「最终结论」第 3 条 |
| 29 | 有意降级；旧协议分支在合并边界由 `validate_merge_sources` 阻断 |
| 31 | `--no-verify` 是 Git 本身的边界，提交边界另有 `validate_snapshot_ids` 兜底；hook 不可能防住主动绕过 |
| 33 | 用户裁定不做隔离消费仓的迁移恢复演练；迁移的备份与恢复以代码路径和工厂 rollback 用例为证据边界，引用「迁移已验收」时保留这一限定 |
| 35 | 被替换的字符串正是迁移已移走的目录；迁移后旧路径不再存在，改写可以保持引用有效；范围限于两份当前文档，不涉及历史报告 |
| 36 | 0.5 秒 TTL 是 Issue #48 的并发平抑取舍，注释已有说明 |
| 37 | 性能计划已声明种子仓随模板版本变化、历史基准不可跨版本对照 |
| 38 | 审阅者自述只是抽查，没有提出反例，无法据此行动 |
| 39 | plan_scratch_cleanup §11 已接受「条目内容即声明」的信任模型；伪造只能保住被点名的路径，不构成越权写 |
| 41 | 流程事实，无需行动；review_20261109 已分开记录修复时的状态与 11-10 全量结果 |

## 原报告逐项覆盖

核实方式：复现＝在基线上调用函数实测；读码＝在基线上阅读源码确认；检索＝在测试目录中按关键词检索；采信＝采纳审阅者自述，未独立核实。

|原项|摘要|核实|处置|
|---|---|---|---|
|1|快照副本不复验|读码|采纳 F05|
|2|回滚不可续滚|读码|采纳 F05|
|3|提交成功但超时无测试|检索|采纳 F05|
|4|回滚再次中断无测试|采信|采纳 F05|
|5|子进程结束靠锁文件推断|采信|拒绝|
|6|run_git_commit 的 OSError 裸抛|读码反证|拒绝|
|7|recovery 展示 committed|读码|拒绝|
|8|A06 授权靠 note 子串|读码|修改后采纳 F12|
|9|merge-base 解析失败跳过检查|读码|采纳 F06|
|10|gitlink 无测试|检索反证|拒绝|
|11|冲突文件豁免期望树|采信|拒绝|
|12|认领候选含 subject 前缀匹配|采信|拒绝|
|13|commit-msg hook 报错形态|读码|采纳 F03|
|14|存量泄漏不阻断|读码|修改后采纳 F13|
|15|UTF-16 漏检|复现|采纳 F02|
|16|两入口失败姿态相反|读码|采纳 F02|
|17|提交消息未在 hook 扫描|读码|采纳 F03|
|18|中段家目录漏检|复现|修改后采纳 F04|
|19|token 带尾随点|复现|采纳 F14|
|20|difflib autojunk|推理反证|拒绝|
|21|重命名无比较器用例|采信|采纳 F14|
|22|R15 报错分离无用例|检索|采纳 F14|
|23|UTF-32 BE 无用例|采信|采纳 F02|
|24|store 用 CWD 作缺省根|读码|采纳 F07|
|25|_git_common_root 静默退回|读码|采纳 F07|
|26|default_branch 兜底返回 main|读码|采纳 F08|
|27|added-lines 部分输出|读码|采纳 F09|
|28|legacy-state 迁移不走事务|读码|采纳 F10|
|29|scan_max_id 静默排除旧分支|采信|拒绝|
|30|purge 未传 tid|读码|采纳 F01|
|31|hook 可被绕过|采信|拒绝|
|32|slug_from_title 注释口径|读码|采纳 F14|
|33|迁移恢复无真实演练|—|拒绝（用户裁定）|
|34|R04 用例未定位|检索|采纳 F14|
|35|引用改写用全串替换|读码|拒绝|
|36|看板缓存陈旧窗口|采信|拒绝|
|37|种子仓随版本漂移|采信|拒绝|
|38|P1 等价性只抽查|采信|拒绝|
|39|scratch 保护信号可伪造|采信|拒绝|
|40|缺少 journal 时备份被清|读码|采纳 F11|
|41|R01–R17 验证时间差|采信|拒绝|

## 实施顺序与验证

1. P0：F01 → F02 → F03 → F04。F01 与 F03 改动小、风险高，可以先单独提交。
1. P1：F05 → F06 → F07 → F08 → F09 → F10 → F11。
1. P2：F12 → F13 → F14。

每个修改包附带其「验收」所列的回归用例。工厂改模板必须跑全量：

```bash
pytest repo/.repo_template/tests factory_tests -q -n auto -p no:cacheprovider --basetemp "$(mktemp -d)"
```

## 证据与验证边界

- 评估阶段的复现项（15、18、19）在基线上直接调用 `repo_task.privacy` 的函数取得结果，当时没有写入测试；实施后已补回归。
- 评估阶段的读码项依据基线源码行号，实施时行号已偏移。
- 实施与验证（2026-11-10）：F01–F14 已合入 main；工厂全量 `1610 passed, 1 skipped`。评估时「未运行测试」的边界不适用于实施后的验证结果。
