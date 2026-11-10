# 四线合并审阅报告（原文）

> 用户于 2026-11-10 粘贴的审阅原文，按原样保存，未修改。处置见 [adoption.md](adoption.md)。

好，把四条线报上来的问题全部摊开，一共 41 项，不分级、不合并省略。按线分组，每项写清是什么、在哪、后果是什么。

一、提交事务与集成线（13 项）

恢复快照只登记摘要、不复验（commit_txn.py:180-195、458-481）：journal 的 files 摘要是对源文件算的；快照拷贝完成后不校验副本摘要，rollback 从 files/ 拷回时也不和 journal 摘要比对。快照拷贝中途崩溃或介质损坏，会把截断内容当作 before-image 恢复并报告成功。A01 方案写的「经过完整性校验的恢复快照」只兑现了写入侧登记，读取侧没兑现。
rollback 不分步持久化、中断后不可续滚（commit_txn.py:450-481）：回滚顺序是先替换 index、再逐文件拷回，过程不落 journal。中途再中断，现场哈希集既非事务期望也非原始状态，judge 只能判 unknown 交人工。A01 验收明列了「恢复再次中断」这一项；当前靠 fail-closed 兜底是安全的，但达不到「恢复幂等续行」的字面要求——重跑 finish 的幂等只覆盖 rollback_safe 状态，不覆盖回滚进行时。
「提交成功但调用超时」没有测试：A01 验收明列的注入点，四个 finish 测试文件里 grep 超时/timeout 零命中。这条路径目前只有 run_git_commit 把超时包装转成失败结果交 judge 判定的代码阅读证据，没有用例钉住。
「恢复再次中断」没有测试：没有在 rollback 中途注入失败或杀进程、然后重跑判定的用例，与第 2 项互为印证——设计上最难的两条路径恰好都没测试。
「子进程已结束」只是锁文件代理判定：judge 靠 index.lock/HEAD.lock/shallow.lock/MERGE_HEAD 是否存在推断 Git 子进程结束。git 被强杀残留的 index.lock 会永久判 unknown、必须人工清锁（方向是 fail-closed）；反过来，hook 的孙进程还活着但不持锁的窗口，这个判定区分不出来。是近似，不是证明。
run_git_commit 的异常处理不完整（commit_txn.py:487-494）：只吞 TaskDataError（超时包装），git 可执行文件缺失这类 OSError 会裸抛 traceback。journal 还在、事后能走 recovery 判定，只是报错形态不统一。
recovery 展示的 committed 不含 tree 校验（recovery.py:66-76）：judge 的 committed 结论本身不验落盘 tree（docstring 明示要由 accept 验证），实际清理路径都补走了 _accept_finish_txn 复验，所以不是放行漏洞；但 recovery_state 对 committed 只置标志就展示，只看 recovery 输出的人会把「committed」误读成已经全验通过。
A06 的授权证据是 note 子串匹配（integration.py:855-879）：review_level 升级和 limits 调大靠分支 note 里出现 "review single -> full:"、field: old->new 这类字样认定「已授权」，不校验来源和时序。防手滑够用，防不住有人在 note 里预埋字样冒充授权。和「证明已授权」的字面强度有差。
merge-base 解析失败时 A06 检查整段跳过（integration.py:841-850）：base 的 front matter 解析失败时 base_fm=None，「主干未相对 base 改写」类检查全部跳过，退化成 head↔branch 两方比较。head/branch 本身解析失败会先返回 False，但 base 单独损坏（比如旧格式）时检查明显变弱。这是审阅者的推测路径，未构造样本实证。
gitlink（160000）没有专门测试：A09 验收把 120000 和 160000 并列点名，测试只覆盖了 symlink。代码走同一个 mode 判断分支，实际风险小，但验收点名的用例缺一半。
冲突路径豁免期望树一致性（integration.py 的 _validate_claimed_merge_commit 及 --continue 路径）：冲突文件在认领和续行两条路上都豁免 merge-tree 自动合并一致性比对，只受核心资产、隐私、编号三道侧栏约束，非核心文件的冲突解决内容没有任何内容级复核。这是 docstring 明示的显式放行、不是暗坑，但「期望树重算」这道防线对冲突文件确实不设防。
认领候选定位含 subject 前缀匹配（integration.py:598-625）：_find_merge_commit 能被同 subject 前缀、同 second parent 的手工 merge 命中。命中后仍须过全套内容认领校验，实际绕过面小，但定位环节本身不是强身份判定。
commit-msg hook 的报错形态：对非 IdScanError 的异常（如上下文解析失败）以 traceback 非零退出。效果仍是 fail-closed 阻断，只是报错不友好、定位成本高。

二、隐私门禁线（10 项）

门禁只阻断新增行，既存泄漏不拦：scan_privacy_changes 是 base→index/tree 的 diff 比较器，未变更文件、以及变更文件中未改动的旧泄漏行都不阻断，只有显式 privacy-audit 能看见。A07 验收原文要求覆盖「非本次变更的 tracked 泄漏」、且明说「不能仅改成检查本次变更文件」，HEAD 与之不符。conventions.md:33-34 已把契约改写为「只阻断本次新增文本行」，属于 R01 统一后的有意收窄、落了纸，但按 A07 验收文本只能记部分达成——存量泄漏在被修改到之前会一直躺在库里。
UTF-16 编码漏检（实测复现）：无 BOM、中文占绝大多数的 UTF-16 文本里 NUL 占比约 4.5%，低于 25% 的启发阈值，解码判定失败；在 blob 路径上 _text_or_binary 随即服从 Git 的二进制分类（numstat 为 - -）静默跳过。实测样本：UTF-16LE 的 CJK 重文本内嵌 /Users/alice/proj，scan_privacy_changes 和显式审计 scan_git_index_privacy 双双返回空。触发条件特定（无 BOM + CJK 主导），但这是一个完整的编码类绕过面，连审计都看不见。
同一编码在两入口失败姿态相反：第 15 项的同一内容走 scan_candidate_bytes（finish 候选路径）是 fail-closed 报错阻断。也就是说这类文件在门禁/审计侧被静默放行、在候选侧被阻断；反过来，合法的同类 UTF-16 文件在 finish 候选路径会被误拦。根源是「解码启发阈值 + blob 路径服从 Git 二进制分类」这个组合。
commit message 只在 finish 一处被扫：全仓只有 lifecycle.py:708 一处传 message=。pre-commit/commit-msg hook 不扫提交消息（commit-msg 只做合并来源与编号校验），tip 与 merge 的事后复验也不取提交消息。裸 git commit 把家目录只写进提交消息，能穿过全部 hook 和事后核对。而 conventions.md:34 写的是「提交消息作为本次新增文本单独扫描」、没有限定只适用于 finish，文档口径与实现不一致。
家目录中段形式漏检（实测）：token 识别要求家目录段位于 token 起点，前导边界同时挡掉了中段形式：/System/Volumes/Data/Users/alice（macOS firmlink 数据卷路径，真实工具输出里会出现）、UNC \\server\Users\alice、//server/Users/alice、盘符相对 C:Users\alice 全部漏检。A08 验收的「嵌套路径均得到确定结果」只覆盖了 /home/root/... 这一类。
上报 token 带尾随点：/home/alice. 的命中 token 会把结尾的点算进用户名（用户名字符集含 .）。不影响阻断，只影响报告文本准确性。
difflib autojunk 理论漏检面（未实证）：_scan_text_change 对中间段用 difflib.SequenceMatcher 且默认 autojunk 开启，变更段超过 200 行且大量重复行时，理论上存在新增行被折进 equal 段、不被扫描的可能。审阅者没有构造出实际漏检样本，仅为代码阅读层面的推测。
重命名在比较器层无回归测试：重命名因 --no-renames 按新路径全文当新增扫描（保守方向，行为是对的），但比较器层面没有专门用例，后续改动若引入 rename 检测可能静默改变口径。
R15 报错分离无回归测试：pre-commit 已把 IdScanError（编号校验失败）和 PrivacyScanError（隐私扫描失败）分开捕获分别报错，但 factory/契约测试里找不到断言这两种文案分离的用例，后续重构误合并时没有测试拦截。
UTF-32 BE 用例缺失：UTF-16 LE/BE、UTF-32 BOM、UTF-32 无 BOM 都有用例，UTF-32 BE 的 BOM 与无 BOM 两种组合没有单独立用例。

三、同步与上下文线（6 项）

store 库函数留 CWD 缺省根回退（store.py:714）：resolve_baseline_spec_text 里 repo = repo or ctx.RepoContext.for_root(root or Path.cwd())。当前仓内调用方都显式传参，所以是潜伏面不是活路径，但与 A15「库调用显式传递、删除缺 root 就用全局的双轨」差最后一格。review_status.py:124 的 CLI 入口有同型 CWD 回退（入口层，危害更小）。
_git_common_root 失败时静默退回（repo_task/context.py）：git 命令失败或超时时静默拿 code_root 当 primary_root。linked worktree 且 git 损坏时，ledger/audit 路径会静默指向 worktree 本地目录。下游有 require_primary_worktree 等门禁复核，未发现实际写错仓的路径，但这是一处有意且无声的降级，与全仓「消除静默降级」的口径不一致。
default_branch 末端无条件返回 main（git_ops.py:274）：origin/HEAD、init.defaultBranch、main/master 探测、唯一非 task 分支全部落空时，不报错、直接返回 "main"，与 A04「主干配置无法解析时失败、不得用强制 main 掩盖」字面不符。实际危害有限：落到兜底时 main 必然不存在，后续 git 操作会以较含糊的错误失败，而不是静默选错主干——但错误信息会误导定位。
added-lines 部分输出风险（repo_state.py）：个别未跟踪文件在读取中途消失时，先输出其余文件的行、最后以退出码 1 收口并打 WARNING。不看退出码、只消费 stdout 的管道调用方仍可能把部分输出当完整结果。当前调用契约（文档 + 测试）都以退出码为准，属调用方纪律问题，但脚本侧没有做到「失败即零输出」与 changed-files 子命令一致。
legacy-state 迁移不走事务（template_sync/state.py:180-189）：七类写迁移里唯一例外——migrate_legacy_state(write=True) 直接 write_state 然后 unlink 旧文件，没有 FileTransaction 的备份快照、没有迁移后 verify。失败窗口是「新 state 已写、旧文件未删」时两份并存；后续入口会被 require_no_legacy_state 以 fail-closed 阻断且内容不丢、能人工收口，但与 A10「完成备份并验证可恢复后才能执行」的验收口径相比，这类明显弱于其余六类。
scan_max_id 对旧协议分支静默排除（与第 31 项同源，同步线亦确认）：编号扫描只统计声明了当前 entity_schema 的分支/worktree，旧分支不参与占号且不报提示。靠合并边界 validate_merge_sources 阻断旧分支合入来兜底，属于有门禁的有意降级，但排除动作本身无声。

四、实体、视图与性能线（9 项）

purge 未传 tid，可删他人 worktree（lifecycle.py:1396）：purge 调用 remove_worktree(fm.worktree, expected_branch=fm.branch) 时手里有 args.tid 却没传，只走「相对路径与分支一致性」弱校验。worktrees.py 自己的 docstring 明写：破坏性入口必须传 tid，否则同源篡改的 front matter 可借此删除其他 task 的 worktree。purge 同时删 worktree 和 task 目录、正是破坏性入口；它的其余门禁只约束被 purge 的 task 本身，不保护受害方。若某个 backlog task 的 fm 里 worktree 与 branch 被一致地改指向另一 task，purge 会把对方 worktree 移除。修法明确：补传 tid=args.tid 一行。
编号唯一性的前提是 hook 不被绕过：防撞号靠「扫描当前协议分支占号 + 合并边界阻断旧分支」，但 hook 可以被 git commit --no-verify 绕过；真绕过时，编号唯一性只剩 validate_snapshot_ids 在提交边界一层兜底。这是计划明示的隔离设计的边界，不是实现漏检，但唯一性的强度上限就在这里。
slug_from_title 的注释口径不准（_id_scan.py、pending.py:327）：该函数挂在「旧单文件总账解析（迁移用）」小节下，但 pending 的 new 命令也用它从标题派生 slug。它是新建条目的标题→slug 生成、不构成运行期兼容路径，只是注释会让后续维护者误判它的用途范围。
迁移备份恢复没有真实演练证据（流程缺口）：代码层备份/恢复路径存在且自洽（事务 journal + 快照 + rollback 分支齐全、工厂测试含 rollback 用例），但 review_20261109.md 自述「迁移的备份恢复与零写入演练仍需在隔离消费仓执行」——真实消费仓布局下的恢复演练在本 diff 内没有任何证据。引用「迁移已验收」时必须带这个限定。
R04 专项回归用例未定位到：test_entity_storage_migration.py 覆盖了「归档 spike 声明 active 的矛盾阻断」和状态冲突，但其 e2e 夹具里的 active spike 本就带完整 front matter；「扁平 active spike 完全无 fm → status 报待迁移」这条 R04 核心路径是靠读代码确认机制成立的，没有同名用例实证。审阅者未全仓穷举，不排除用例在其他文件里。
引用改写用全串替换（entity_storage_migration.py:855-868）：迁移时对 docs/handoff.md、docs/specs_index.md 做全串 str.replace 改写旧路径。范围按计划限定在这两份当前文档、不是全仓，但文档内的代码示例、历史引文若含旧路径字符串会被一并改掉，与计划「不能全仓字符串替换历史报告、代码示例」的精神在这两份文件内仍有张力。
看板缓存 0.5 秒陈旧窗口：view_server 的 _BoardCache TTL 0.5s，状态行与看板可显示半秒内的旧状态。注释明示是 Issue #48 的并发平抑取舍，非缺陷，仅口径记录。
种子仓内容随模板版本漂移：P3 的种子构建把当前模板 scripts 复制进种子仓，种子每次现建（隔离没问题），但性能计划 §2 的历史基准数据因此不可跨版本直接对照。计划本身已声明此口径。
P1 等价性只抽查未全量证明（审阅者自陈）：P1「Arrange 改走领域 API」涉及面广，本次只抽查了 diff 中的 skip/xfail 与种子/batch 两块，没有逐个 helper 核对语义等价性。未发现反例，但这部分不构成全量证明。
scratch 保护信号可伪造（设计内）：任一未关闭的 pending 条目只要在正文写一行 .scratch/xxx、并让目标文件 3 天内被 touch，就能保住该路径不被清理。这是计划 §11 明示接受的规则（延长保留即修改文件、属于规则本身），伪造只能保住被点名的具体路径、条目关闭即失效、不构成越权写；但要清楚它的信任模型就是「条目内容即声明」，没有额外核验。
transaction.json 被单独删掉时备份会被清：若事务目录里的 transaction.json 被人为单独删除（备份还在），open_transactions 视为无事务，残留备份会按「无主」被 scratch 清理删掉。正常流程不会产生此状态（journal 先于备份写入、accept/rollback 都保留 journal 至终态），需要手工破坏才触发。
R01–R17 的验证时间差（流程事实）：11-09 那批修复按当时要求只做了静态检查、没有跑测试，逐项表里大量「回归已写，未运行」；工厂全量 1519 passed 是 11-10 才补跑的。也就是说修复合入与验证通过之间隔了一夜，逐项的「修复当时状态」和现在的「全量已过」是两个时点的事实，引用时不能混。

以上就是全部。需要的话我可以把这 41 项整理成一份文件存进仓库审阅目录，或者按你的裁决逐项立 pending——你点哪些我立哪些，不自作主张全立。
