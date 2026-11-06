# repo_template 宏观改造方案

> **状态：本次代码改造已实施并合入 `main`。** 下文保留改造时的历史方案与基线，已实施内容见文末；现行规则见产物文档和 `decision_log.md`。
>
> 编写时间：2026-11-03T21:57:11+08:00。证据基线：工厂 `main` @ `9514f00`。
>
> 范围：工厂仓根、`docs_repo/`、产物 `repo/`（含 `.repo_template/` 工具链）以及消费仓迁移路径。裁决后按 `docs_repo/README.md` 纪律在 `decision_log.md` 追加 L40 起的行；本文只保留推导与实施范围，不作现行操作手册。

## 1. 结论

工具链正确性良好：工厂全量测试全绿，GitHub issue 全部关闭，导出产物可通过消费仓契约测试。主要问题不在缺陷，而在复杂度结构：

- agent 承担了本应由脚本完成的状态机编排，单 task 主路径需要约 15 次脚本调用并手写一份 JSON；
- 「一 task 一 commit」与「finish 在执行分支内归档」叠加，派生出 amend 例外、路径无关指纹和 archive 写权例外；
- 常驻与按需加载的规则文本过长，同一段组合契约在 13 个 skill 中重复；
- 产物向消费仓分发了大量消费仓永不运行的工厂测试，以及默认启用但缺渲染端的状态栏 hook；
- 文档与实现之间已有多处可核实的漂移；

改造目标是在**不削弱现有门禁语义**（严格模板、TDD、黑盒、独立 review、merge 单独授权、claim 防并发）的前提下，把编排下沉到脚本、收敛状态与文档、瘦身分发，完善本地测试。

## 2. 证据基线

| 项 | 数值 | 来源 |
| --- | --- | --- |
| 工具链规模 | 约 1.49 万行；`repo_sync.py` 2378 行 / 101KB；`lifecycle.py` 877 行；`integration.py` 773 行 | `wc` |
| CLI 面 | `task.py` 26 个子命令；`attempt` 4 个子命令；skill 15 个 | `task.py --help`、`registry.json` |
| 提交构成 | 276 个 commit 中 95 个为 `fix`；2026-10-25 单日 18 个；`#16`–`#83` 区间均为加固修复 | `git log` |
| 热点 | 触及 `scripts/` 的 54 个 commit 中 25 个触及 `repo_sync.py` | `git log -- <path>` |
| issue | 关闭 83，开放 0 | `gh issue list` |
| 工厂全量测试 | 691 passed、1 skipped，串行 10 分 28 秒 | 本机 `pytest repo/.repo_template/tests -q` |
| 消费仓契约 | 从 HEAD 导出后 174 passed（518 deselected），3.1 秒 | `export_template.py` → `pytest .repo_template/tests -q -m contract` |
| 文档体量 | `repo/AGENTS.md` 5.4KB；`usage.md` 22.8KB；`architecture.md` 6.6KB | `wc -c` |
| 分发测试 | 37 个测试相关文件、692 个用例；契约模块 9 个 | `test_contract_suite.py` 的 `CONTRACT_MODULES` |
| 工厂临时文件 | `.scratch/` 2.9GB（`review_fixes` 1.6GB 等历次 basetemp 残留） | `du` |

已有脚本级端到端测试覆盖 start → finish → cleanup → integrate、链式合并、恢复阶段与产物导出（`test_workflow_contracts.py`、`test_design_regressions.py::test_actual_product_export_is_clean_and_passes_contract`）。

## 3. 目标、非目标与成功判据

### 目标

| 指标 | 当前 | 目标 |
| --- | --- | --- |
| 单 task 主路径 agent 需执行的脚本调用 | 约 15 次，另手写 `handoff.json` | ≤ 9 次，`handoff.json` 不手写 |
| 跨 skill 手动传递的身份字段 | `tid`、`attempt`、`execution_id`、`owner_id` | `tid`、`owner_id`；其余由脚本解析并回显 |
| 执行分支历史例外 | done 后 review 失效须 amend 原执行 commit；archive「唯一窄例外」 | 无 amend、无 archive 写权例外 |
| 分发到消费仓的测试 | 692 用例 | 仅契约用例（当前 174） |
| 消费仓默认 hook 进程 | 每次 Bash 调用 1 个 python3 | 0（状态栏改为显式启用） |
| `usage.md` 日常部分 | 22.8KB 单文件 | 按读者拆分为 usage / recovery / sync；日常文档不保留恢复与同步规程（原定 ≤ 10KB 按字符估算，UTF-8 中文每字 3 字节，不可达；WP3 实测降至 17.1KB） |
| 已知文档漂移 | 8 处（见 A 组；A6 不处理） | 其余 7 处清零 |
| AC 复核 | reviewer 判定只在报告正文 | 每条 AC 带 `TICK-VALID` / `TICK-VOID`，由脚本门禁 |
| 工厂全量测试耗时 | 串行约 9.5 分钟（10 核只用 1 核） | 并行运行约 2 分钟（估算，待 H3 实测） |

### 非目标

- 不改变 review 判定标准、finding 阈值、黑盒与测试门禁的语义。
- 不引入调度服务、后台进程或跨机器协作。
- 不改变 merge 必须单独授权的规则。
- 不为旧 schema 增加运行时兼容分支；消费仓通过 `repo-template-sync` 一次性迁移。

## 4. 问题与改造项映射

| 编号 | 问题 | 改造项 |
| --- | --- | --- |
| P1 | 复杂度增长快于价值，修补循环 | H1 |
| P2 | 一 task 一 commit 派生多层例外 | D1、D2 |
| P3 | 状态分散，`handoff.json` 与 `note.md` 重复；状态文件名与内容不符 | C3、D3、D4 |
| P4 | 「handoff」一词三义 | F4 |
| P5 | agent 手工编排状态机 | C1–C5 |
| P6 | 线性链拓扑放大阻塞与合并面 | E1、E2 |
| P7 | 上下文成本高、样板重复 | F1–F3 |
| P8 | 分发内容过多，hook 半成品 | G1、G2 |
| P10 | 文档漂移 | A1–A8 |
| P11 | 工厂仓卫生 | B1–B3 |
| P12 | review FAIL 后 AC 打勾不撤回；reviewer 的 AC 判定不进机器门禁 | J1–J4 |
| P13 | 工厂全量测试只能串行，单次约 9.5 分钟，拖慢每个工作包的验证 | H3 |

## 5. 改造项

每项按「现状 → 改造 → 影响面 → 验收」描述。标注「需裁决」的项改变已有裁决或授权语义，未经批准不实施。

### A. 文档漂移修复（无需裁决）

| 编号 | 位置 | 现状 | 改造 |
| --- | --- | --- | --- |
| A1 | `README.md`「复制新项目」 | 要点写 `rsync -a --ignore-existing`，与 `repo/README.md`「初始化」第 01 步的 `export_template.py` 相反；H-01 已裁决替代 rsync | 改为一句摘要：从指定 Git commit 用 `export_template.py` 导出，目标须不存在或为空，失败即停；细节链接 `repo/README.md` |
| A2 | `repo/.repo_template/docs/usage.md`「状态恢复与升级」 | `ledger import` 描述为校验「AC evidence」；`4e4772f` 已废弃该字段，实现经 `verify_integrate_ready` 校验 `task.md` AC 全部勾选 | 改为「task.md AC 全部勾选」 |
| A3 | `repo/.repo_template/skills/task-work/SKILL.md` 实施第 8 步 | 含变更说明「（不再包含 ac_evidence）」 | 删除括号内容；若采纳 C3 则整步改写 |
| A4 | `docs_repo/decision_log.md` L6、L7、L10、L13 | 落点列仍用 `35d298b` 改名前的文件名（`spec.md` 模板、`task.md` 实施笔记），L10 读起来自相矛盾 | 落点列改为现行路径；表头增加一行文件名映射说明（`spec.md` → `task.md`，原 `task.md` → `note.md`），裁决列作为历史保留 |
| A5 | `docs_repo/decision_log.md` L15 | 写「禁止开跑前二次征求同意」；`task-run` 现行规定无参数调用须先列清单确认 | 状态列补 successor 说明：显式 tid 与 goal 队列仍不二次确认，无参数调用改为先确认 |
| A6 | `docs_repo/decision_log.md` L39 与「123.md 核验与处理」 | 引用的 `123.md` 不在仓内 | 不处理：原报告已删除，用户确认无需追溯 |
| A7 | `docs_repo/decision_log.md` 表头 | 核对日期仍为 2026-09-07，表内已有 2026-10-24 条目 | 更新核对日期 |
| A8 | `.agents/skills/issue-handler/SKILL.md` | 写域只含 `repo/.repo_template/` 与 `repo/AGENTS.md`，覆盖不到 `repo/README.md`、`repo/.claude/settings.json`、`repo/docs/blueprint/` 等分发文件；提到不存在的 `hooks/*.py`（实际只有 `hooks/pre-commit`） | 写域改为「`repo/` 产物树内的模板文件」；修正 hook 路径 |

验收：

- `grep -rn "rsync" README.md` 无命中；
- `grep -rn "AC evidence\|ac_evidence" repo --include='*.md'` 只剩测试 fixture；
- 工厂全量测试通过。

### B. 工厂仓卫生

| 编号 | 现状 | 改造 | 裁决 |
| --- | --- | --- | --- |
| B1 | `.scratch/` 2.9GB，来自手工 `--basetemp .scratch/...` 的历次测试现场 | 移入废纸篓清空。工厂 `AGENTS.md` 增加一条：全量测试使用系统临时目录，需保留现场时用后即清 | 需确认（删除操作） |
| B2 | `docs_repo/demos/app/`（93 个文件的 React/Vite mock）与 zip 入库；现行看板为 `view_static/` 原生 JS，链式规划已由 `chain_plan.js` 实现 | 删除 `app/` 与 zip（git 历史保留，`0ec0e59` 可追溯）；`demos/plan.md` 迁入 `docs_repo/archive/` 并在首行注明 successor 为 `view_static/`；同步删除根 `.gitignore` 中 `docs_repo/demos/app/node_modules/` | 需裁决 |
| B3 | 根 `README.md` 与工厂 `AGENTS.md` 的测试命令在 G1 后会变化 | 随 G1 更新 | 随 G1 |

### C. Agent 编排收敛（需裁决：D-01–D-03）

现状：

- `task-run` 每个 task 依次执行 `start`、`attempt reserve`、`attempt claim`；
- `task-work` 执行 2–3 次 `preflight`、渲染 prompt、`check_review_status`、`git add -N`、手写 `handoff.json`、`finish`、再查 review、`git commit`；
- 回到主仓后执行 `attempt terminal`、`attempt report`、`cleanup-worktree`、`integrate`、`integrate --continue`；
- 每一步都要原样传递 `(attempt, execution_id, owner_id)`。

| 编号 | 改造 | 说明 |
| --- | --- | --- |
| C1 | 新增 `task.py run-begin {tid} --owner-id <id> [--base <branch>]` | 在一次控制面锁内完成 start → reserve → claim，输出 JSON identity。现场已存在时拒绝并给出 `recovery` 结论，不做隐式恢复 |
| C2 | 新增 `task.py run-close {tid} --owner-id <id> --result done\|blocked\|failed` | `done` 写 terminal completed + report done（`--sha` 取执行 commit）并 exact cleanup；`blocked` 写 terminal stopped + report blocked，保留 worktree；`failed` 写 terminal failed + report failed。ledger 事件类型不变，无需迁移 ledger |
| C3 | `finish` 原子化：`task.py finish {tid} --message-file <path>` | 依次执行严格 preflight、AC 全勾校验、review 门禁、写 done、**机器生成** `handoff.json`，再提交执行 commit。要求交付文件已暂存；存在未暂存或未跟踪的非忽略文件时 fail-closed。消除「finish 已写、commit 未做」这一恢复阶段 |
| C4 | 删除 `attempt reserve --executor` | 唯一取值 `inline` 是 dispatch 退役（L35）残留；ledger 仍写 `executor: inline` |
| C5 | 身份默认解析 | worktree 内命令从 `docs/runtime/execution_context.json` 取 identity，主仓命令从 ledger 当前 attempt 取 identity；两处都以 ledger claim 的 `owner_id` 为准核对。显式 `--attempt/--execution-id` 保留，供 recovery 指定 exact identity，提供时必须与解析结果一致 |

原语子命令（`attempt reserve/claim/terminal/report`、`cleanup-worktree`）保留，作为 recovery 的低层接口，skill 主路径不再直接调用。

C3 中 `handoff.json` 的字段来源：

| 字段 | 来源 |
| --- | --- |
| `tid` `attempt` `execution_id` `status` `branch` | execution context 与 ledger |
| `base_sha` | 执行分支 first parent（实现已强制两者相等，见 `monitoring.py` 对 `first_parent` 的校验） |
| `pending` `findings` | 执行分支相对 base 新增或归档的 `docs/pending/`、`docs/findings/` 条目 |
| `tests` `blackbox` `review` | 读取 `note.md`「收尾报告 / 验收与验证」中对应行；仍为模板占位时 fail-closed |

结果是 agent 只在 `note.md` 写一次验证摘要，`handoff.json` 不再与它重复维护。已归档的旧 `handoff.json` 字段是新 schema 的超集，读取不受影响。

影响面：

- `repo_task/cli.py`、`attempts.py`、`lifecycle.py`、`integration.py`、`recovery.py`；
- `task-run`、`task-work`、`task-integrate` 三个 skill；
- `usage.md`「工具链路径与写权」。

验收：

- 新增测试覆盖三类场景：composite 命令在每个中间步骤失败时都能由 `recovery` 判定到正确阶段；`run-close` 重入安全；`finish --message-file` 遇未暂存文件拒绝且零写入；
- 现有 691 个用例通过；
- 产物导出后契约通过；
- 按第 3 节指标数一遍 skill 主路径的脚本调用次数。

### D. 执行历史与状态模型（需裁决：D-04）

现状因果链：

1. `finish` 在执行分支内把 `docs/tasks/{tid}_{slug}/` 移入 `docs/archive/tasks/`（`lifecycle.py` `_close_task`）；
2. 所以 review 指纹必须把两处路径规范化为同一逻辑路径（`monitoring.py` `_review_logical_path`）；
3. done 之后 review 失效时，补审证据只能 amend 原执行 commit；
4. 所以 `AGENTS.md` 中 archive「只准新增」需要「唯一窄例外」，`usage.md`、`task-run`、`task-work` 也各写一段 amend 规程。

| 编号 | 改造 | 说明 |
| --- | --- | --- |
| D1 | 归档时机从 `finish` 移到 `integrate` | `finish` 只在原位写 `status: done`。`integrate` / `integrate-chain` 在 `git merge --no-commit` 之后，把成员 task 目录移入 archive，并与派生 index 一起暂存进同一个 merge commit。`_validate_merge_staged_scope` 中的两项校验（staged 路径白名单、非冲突文件不得偏离 Git 自动合并结果）都把豁免集合从「派生 index」扩展为「派生 index + 成员 task 目录的归档 rename」。指纹的双路径规范化随之删除 |
| D2 | **已由 L53 撤销。** 修订 L14：执行分支 = 1 个实现 commit + 0..n 个证据 commit | 证据 commit 只能修改当前 task 目录内的 review 过程文件（与 `REVIEW_PROCESS_FILES` 同源）。这些文件本已排除在指纹之外，追加证据不会改变指纹。done 后 review 失效时，补审改为追加证据 commit，删除 amend 规程与 archive 例外。「一个工程意义明确的实现 commit」不变 |
| D3 | 依赖实现 SHA 查找适配 | `_dependency_implementations` 现按归档路径查找添加 `handoff.json` 的 commit。D1 后新 task 的 `handoff.json` 添加于 `docs/tasks/` 路径。查找改为用一条 pathspec 同时覆盖该 task 的 `docs/tasks/` 与 `docs/archive/tasks/` 两个位置，旧 task 与新 task 走同一逻辑 |
| D4 | `note.md` 改名 `progress.md` | 文件承载状态 front matter、实施笔记、Review 处置与收尾报告，从 backlog 存续到归档；`progress.md` 与 `task.md` 构成「做什么 / 做到哪、怎么做的」，并消除 `note.md` 内 `note` 字段的撞名。模板标题改为「Task 进展」。先把文件名收敛为 `repo_task/context.py` 的单一常量，再机械替换脚本、模板、`AGENTS.md`、`usage.md`、review prompt、skill、看板与测试。消费仓迁移沿用 `35d298b` 先例：同步 apply 时对 `docs/tasks/` 与 `docs/archive/tasks/` 一次性改名。D4 在 D1–D3 与 E 组合入后单独执行，本文其余各项在 D4 之前沿用 `note.md` 名称 |

影响面：

- `lifecycle.py`、`integration.py`、`monitoring.py`、`recovery.py`、`ledger import`；
- `AGENTS.md`「目录与读写规则」archive 行和 `docs/tasks/` 行，以及「开发原则」的 commit 条；
- `architecture.md`「执行拓扑」「合并」；
- `task-work`、`task-run`、`task-integrate`。

验收：

- 新增测试覆盖：证据 commit 只改过程文件时 cleanup/integrate 通过；证据 commit 触及交付文件时拒绝；integrate 后归档目录与 index 同在 merge commit；链式合并时多个成员一次归档；依赖查找对新旧两种历史都返回正确 SHA；
- 删除 amend 恢复相关测试并写明原因（规则删除，不是放宽断言）。

备选：维持现状（L14 原样），只做 C、E、F 组。代价是 amend 例外与指纹双路径继续存在。

### E. 执行拓扑（需裁决：D-05）

现状：`task-run` 让后继以前一 task 分支为 base，与依赖关系无关；最后只合链尾。阻塞即停止整条队列。

| 编号 | 改造 | 说明 |
| --- | --- | --- |
| E1 | 按关系选择 base | 某成员与队列内任一前序成员有 `depends_on` 或 `conflicts_with` 关系时，以紧邻前一成员为 base（现行链式）；否则以主干为 base。合并阶段向用户一次性列出全部待合分支（独立分支逐个 `integrate`，链用 `integrate-chain`），授权语义不变 |
| E2 | 阻塞后继续独立成员 | 成员阻塞时，跳过所有以它为 base 或传递依赖它的后续成员，继续执行其余成员；结束时报告已完成、阻塞点和被跳过成员 |

影响面：

- `task-run` skill；
- `architecture.md`「执行拓扑」；
- `plan --serial` 输出：需要标注每个成员的 base 来源；
- `goal-check`：被跳过成员的终态归类，STOPPED 与 INCOMPLETE 的区分需复核。

验收：

- 新增测试覆盖：独立成员从主干起步，`start` 依赖门禁照常生效；冲突成员仍走链式；独立分支按完成顺序合并（已有 `test_two_independent_tasks_integrate_in_completion_order` 可复用）；阻塞后的跳过集合计算正确。

### F. 上下文与文档结构

| 编号 | 改造 | 说明 | 裁决 |
| --- | --- | --- | --- |
| F1 | 拆分 `usage.md` | `usage.md` 保留日常内容：路径写权、命令约定、命名格式、skill 调用（`skill_registry.py --write` 的生成区不动）、workflow 示例、门禁执行边界、附属生命周期、支持边界。新建 `recovery.md`：多会话 claim/接管、`recovery`、ledger import、迁移与备份。新建 `sync.md`：AGENTS 分区协议、同步事务、manifest/policy/state、降级、prettierignore 机械追加。`architecture.md`「权威分工」同步更新 | 无需 |
| F2 | 收敛 13 个 skill 的「组合契约」样板 | 每个 skill 只保留一句安全核心：「仅显式入口或已登记父流程可执行；作为子流程时原样传递 identity 与授权，不扩大授权」。完整规则只在 `usage.md`「skill 调用」定义。保留这一句的理由：宿主单独加载 skill 时不会跟随链接，授权边界必须留在必然加载的位置 | 无需 |
| F3 | 精简 `AGENTS.md`「开发原则」 | 只留不变量；把「状态读取优先级」与 `list/show/preflight --ref` 的用法移到 `usage.md`。该区每次同步都会强制更新，消费仓自动获得 | 无需 |
| F4 | 统一「handoff」术语 | `docs/handoff.md` 称「项目交接记录」；`docs/tasks/*/handoff.json` 称「执行回执」，并在 `AGENTS.md` 目录表 `docs/tasks/` 行写明由 `finish` 生成。不改文件名，避免消费仓历史与 prettierignore 规则漂移 | 无需 |

验收：

- 拆分后用 `grep` 检查所有引用；当前只有 `usage.md`「skill 调用」一个章节锚点被外部引用；
- `skill_registry.py` 校验通过；
- 契约测试通过；
- 记录拆分前后各文件体积。

### G. 分发瘦身

| 编号 | 改造 | 说明 | 裁决 |
| --- | --- | --- | --- |
| G1 | 工厂测试移出产物 | 把 9 个契约模块以外的测试模块、fixtures、`test_chain_plan_cases.js` 与 `tests/package.json` 移到工厂根 `factory_tests/`。`review_support.py` 被契约模块引用，留在产物内；工厂测试经 `factory_tests/conftest.py` 把 `repo/.repo_template/tests` 与 `scripts` 加入 `sys.path`。工厂根新增仅工厂使用的 `pytest.ini`，登记 marker 与 `testpaths`，`factory_tests/conftest.py` 保留 xdist 守卫。工厂命令改为 `pytest repo/.repo_template/tests factory_tests -q`。`contract` marker 与 `-m contract` 保留，因为消费仓 `testing.md` 归消费仓所有、不随同步更新，去掉 marker 会让既有命令收集 0 条并以退出码 5 失败。消费仓下次同步时，`sync_dir` 的镜像删除会自动移除这些文件。`PRETTIERIGNORE_TEMPLATE_RULES` 删除两条测试路径 | D-06 |
| G2 | 状态栏 hook 改为显式启用 | `repo/.claude/settings.json` 不再默认带 PostToolUse。`repo_sync.py install-hooks --statusline` 写入消费仓 settings（沿用原缩进），`usage.md` 说明需配合全局 `statusLine.command`。已有消费仓的 settings 归消费仓所有，不自动删除。这是对 L36「保留状态栏 hook」的默认启用方式修订，不删除能力 | D-07 |

验收：

- 产物导出后契约通过；
- 用旧版产物建一个消费仓并同步到新版：工厂测试被移除，契约门禁通过；
- `git ls-files repo/.repo_template/tests` 只剩契约模块与支持文件；
- 新导出消费仓执行普通 Bash 时不再启动 `track_worktree.py`。

### H. 工具链可维护性

| 编号 | 改造 | 说明 | 裁决 |
| --- | --- | --- | --- |
| H1 | `repo_sync.py` 拆包 | 仿照 `repo_task/` 拆为 `template_sync/`（不用 `repo_sync/`，避免与入口 `repo_sync.py` 同名冲突）：`cli`、`source`（固定 checkout / URL 缓存）、`assets`（硬同步与镜像）、`agents_merge`、`shared_files`（gitignore / prettierignore / MCP）、`transaction`、`gates`、`hooks`、`workflow_migration`。入口 `repo_sync.py` 只做路径注入与转发，路径不变，来源运行器与既有 skill 调用不受影响。纯重构，行为不变 | 无需 |
| H3 | 测试并行化 | 现状：全量 692 个用例串行约 9.5 分钟；16 处测试直接改写进程级 `os` / `shutil` / `subprocess` 属性，两处 `conftest.py` 因此拒绝 pytest-xdist；工厂集成测试多数新建真实 git 仓库与 worktree。改造：把进程级改写收敛为作用域受控的替换（只影响当前测试）；去掉 xdist 禁令并以 `-n auto` 运行；可选为 git 测试仓库建一次模板、每个测试复制一份。工厂命令改为并行。在 D4 合入后执行，避免与第 2–4 波的测试改动冲突 | D-14 |

### J. AC 打勾复核闭环（D-13）

现状：

- 创建期禁止预先打勾（`documents.py`，仅 `preflight --creation`）；执行 Agent 验证通过后打勾，打勾在派 review 之前；finish 与 integrate 要求 AC 全部打勾。
- 三份 review prompt 要求逐条复核已打勾 AC，打勾不实出 blocking finding。`share_prompt.txt`「AC 复验披露」要求每条 AC 标注 `re_verified` / `trust_prior`。

缺口：

- review FAIL 后错误的勾不会撤回，修复后「已打勾」不再表示「在最终交付内容上验证过」；
- reviewer 对每条 AC 的判定不进机器门禁，PASS 是否覆盖全部 AC 无从校验。

| 编号 | 改造 | 说明 |
| --- | --- | --- |
| J1 | FAIL 后全部撤回打勾 | review 判 FAIL 且随后要修改交付内容时，执行 Agent 先把 `task.md` 全部 AC 撤回为 `- [ ]`，修复后重跑测试与黑盒，逐条重新打勾，再派下一轮 review。仅补报告或处置表、不改交付内容的 INCOMPLETE 不撤回。reviewer 保持只读，不改 `task.md` |
| J2 | AC 判定标签 | 「AC 复验方式」小节每行格式为 `- AC-NNN：TICK-VALID，re_verified。证据` 或 `- AC-NNN：TICK-VOID，trust_prior。原因`。`TICK-VALID` 表示勾选成立；`TICK-VOID` 表示勾选作废。未打勾的 AC 一律记 `TICK-VOID`。`Round N`（N≥2）小节须重新列出全部 AC 行。`full` 级的 code 与 test 报告都须包含该小节 |
| J3 | 机器门禁 | `check_review_status` 读取每份报告最后一轮的 AC 小节。PASS 有效的条件：AC 行集合与 `task.md` 的 AC 编号集合一致，`task.md` 中全部已打勾，且全部为 `TICK-VALID`。任一行为 `TICK-VOID` 时判 FAIL；缺行、多行或格式错误时判 INCOMPLETE，`next_action` 为补全 AC 复验方式 |
| J4 | 模板规范块 | `task.md` 验收标准规范块补充 J1 规则。存量 task 的规范块由同步器现有的规范块强制迁移更新 |

影响面：

- `review_prompts/` 四个文件、`check_review_status.py`、`repo_task/review.py`、task 模板、`tests/review_support.py` 与 review 相关测试；
- `task-work`「独立 review」段（由 WP4 改写）。

验收：

- 新增测试覆盖：全部 `TICK-VALID` 时 PASS；含 `TICK-VOID` 时 FAIL；缺 AC 行、多出未知 AC、`task.md` 未打勾时不得 PASS；多轮报告只取最后一轮；
- 既有 PASS 场景的测试输入补齐 AC 行后保持 PASS（这是补输入，不是改预期）。

## 6. 评估后不建议改动

| 候选 | 不改理由 |
| --- | --- |
| `tasks_index.json` 改为 gitignore | L1 已裁决入库；merge 冲突已由集成时重建解决 |
| 用 commit trailer 取代 `handoff.json` | 更彻底，但依赖查找需重写，历史 commit 无法补 trailer。C3 已消除手写与重复，收益已覆盖主要痛点 |
| 合并或改名 skill（如 `repo-hygiene` / `repo-cleanup`） | 职责不同，改名会给消费仓软链与习惯带来迁移成本 |
| 删除 `view --serve` 看板或 goal 模式 | 用户已在 L35 / L36 裁决保留 |
| `repo/.gitignore` 中的 `.commandcode/` | 本人工具在消费仓普遍使用，保留成本可忽略 |

## 7. 实施阶段

| 阶段 | 内容 | 前置 | 验证 |
| --- | --- | --- | --- |
| 0 | A1–A8（A6 不处理），B1、B2；B3 随 G1 | 无 | A 组 grep 判据；工厂全量测试 |
| 1 | C1–C5，改写 `task-run` / `task-work` / `task-integrate` 与 `usage.md` 对应段 | D-01–D-03 | C 组验收；导出契约 |
| 2 | H1（纯重构，放在任何同步迁移代码改动之前）、F1–F4、G1、G2、J1–J4 | D-06、D-07、D-13 | F/G/J 组验收；旧版消费仓同步到新版的迁移测试 |
| 3 | D1–D3、E1–E2，以及同步器的工作流迁移逻辑；合入后单独执行 D4 改名 | 阶段 1–2；D-04、D-05、D-12 | D/E 组验收；含存量 done task 与旧文件名的消费仓迁移测试 |
| 4 | H3 测试并行化 | D4 合入后 | 并行与串行结果一致，记录实测耗时 |

每个阶段单独提交、单独可回退。阶段内按「先测试后实现」推进，旧测试语义失效时整体删除并写明理由，不就地改预期。

## 8. 消费仓迁移

适用于阶段 1–3 发布后，消费仓通过 `/repo-template-sync` 升级。

### 前置条件

- 没有已登记 task worktree（`apply` 已强制）；
- 所有 done task 已 integrate（D 组新增；`apply` 发现未集成的 done 分支时拒绝，并列出分支）；
- 按 `recovery.md`「状态恢复与升级」备份 Git common dir、`docs/runtime/`、`.repo_template/sync_state.json`，并在隔离副本核对可读。

### 数据影响

| 数据 | 影响 |
| --- | --- |
| ledger | 事件类型不变（C2 只组合调用），无需转换 |
| 已归档 `handoff.json` | 字段是新 schema 的超集，原样保留；archive 只增不改 |
| 存量 task 文档 | 字段无变化；`docs/tasks/` 与 `docs/archive/tasks/` 下的 `note.md` 一次性改名为 `progress.md`（D4）；`task.md` 验收标准规范块按新模板强制更新（J4） |
| 已归档 review 报告 | 不补 `TICK-*` 行，也不重新校验；前置条件已要求全部 done task 先集成 |
| `.repo_template/tests/` | 镜像同步自动移除工厂测试 |
| `.claude/settings.json` | 不自动修改；需要状态栏的消费仓执行 `install-hooks --statusline` |
| 消费仓 `testing.md` | 不变，`-m contract` 继续有效 |

### 步骤

1. 执行 `repo_sync.py plan`，核对受管资产差异。
2. 执行 `apply`。原方案要求同步事务运行契约测试与项目 doctor/test/blackbox；其中同步执行项目门禁部分已被 [L55](../decision_log.md) 取代。
3. 检查 `AGENTS.md`「目录与读写规则」的语义合并结果，确认项目介绍未改动。
4. 用户批准后提交同步 commit，再执行 `transaction accept`。

### 回退

- `accept` 前：`transaction rollback --confirm-restore`；
- 提交后：`git revert <同步commit>`，恢复对应 state 基线，并重跑项目门禁。

## 9. 待裁决清单

裁决时间：2026-11-03，用户批准全部推荐项。

| 编号 | 议题 | 推荐 | 裁决 |
| --- | --- | --- | --- |
| D-01 | 新增 `run-begin` / `run-close`，身份默认解析（C1、C2、C5） | 采纳 | 采纳 |
| D-02 | `finish --message-file` 原子提交（C3 前半） | 采纳 | 采纳 |
| D-03 | `handoff.json` 机器生成，验证摘要只写 `note.md`（C3 后半） | 采纳 | 采纳 |
| D-04 | 归档移到 integrate，L14 修订为「1 实现 commit + 证据 commit」（D 组） | 采纳 | 采纳 |
| D-05 | 队列按关系选择 base（E1）；阻塞后继续独立成员（E2） | E1 采纳；E2 视使用习惯 | E1、E2 均采纳 |
| D-06 | 工厂测试移出产物（G1） | 采纳 | 采纳 |
| D-07 | 状态栏 hook 改为显式启用（G2，修订 L36 的默认启用方式） | 采纳 | 采纳 |
| D-08 | 删除 `docs_repo/demos/app/` 与 zip，`plan.md` 入 archive（B2） | 采纳 | 采纳 |
| D-09 | 清空工厂 `.scratch/`（B1） | 采纳 | 采纳 |
| D-11 | `123.md` 原报告位置（A6） | 由用户提供 | 撤销：原报告已删除，A6 不处理 |
| D-12 | `note.md` 改名 `progress.md`（D4），随阶段 3 迁移一次完成 | 采纳 | 采纳 |
| D-13 | AC 打勾复核闭环：FAIL 后全部撤回打勾，reviewer 用 `TICK-VALID` / `TICK-VOID` 逐条判定，脚本门禁（J 组） | 采纳 | 采纳 |
| D-14 | 测试并行化（H3），D4 合入后执行 | 采纳 | 采纳 |

## 10. 裁决后落点

- 每个采纳项在 `decision_log.md` 追加一行（L40 起），落点列写最终文件与命令。D-04 记为「修订 L14」，D-07 记为「修订 L36 默认启用方式」，并在原行状态列标注 successor。
- 本文首行状态随实施进度改为「部分实施」或「已实施」，并注明对应 commit。

## 11. 并发执行编排

### 分支与 worktree

- 集成分支 `overhaul/macro_2026_11`，从 `main` @ `9514f00` 切出；本文提交在该分支。
- 每个工作包（WP）使用分支 `overhaul/wpN_<slug>` 与 worktree `~/kar/code/repo_template_wt/wpN_<slug>/`，从当时集成分支 HEAD 切出。
- 工作包只在自身 worktree 内写入，提交到自身分支；不 push，不改 `main`。
- 主会话负责审阅 diff、合入集成分支（`git merge --no-ff`）、解决冲突并跑全量测试。一波全部合入且全量通过后，才开下一波。
- 合入后用 `git worktree remove` 与 `git worktree prune` 清理，并删除已合入的 WP 分支。
- 全部完成后，集成分支合入 `main` 需用户另行批准。

### 波次与文件归属

同一波内各 WP 的写入文件互不重叠。例外是同一文件的不同章节，在下表单独注明，由主会话合入时解决。

| 波次 | WP | 改造项 | 写入范围 |
| --- | --- | --- | --- |
| 1 | `wp1_docs_factory` | A4、A5、A7、B2 | `docs_repo/decision_log.md`、`docs_repo/README.md`、`docs_repo/demos/`、`docs_repo/archive/`（新增 demo 计划归档）、根 `.gitignore` |
| 1 | `wp2_test_split` | G1、A1、A8、B3 | `repo/.repo_template/tests/`、`factory_tests/`、根 `README.md`、根 `AGENTS.md`、`.agents/skills/issue-handler/SKILL.md`、`repo/docs/blueprint/testing.md`、`repo_sync.py` 中 `PRETTIERIGNORE_TEMPLATE_RULES` 的两条测试路径 |
| 1 | `wp3_docs_product` | F1–F4、A2 | `repo/.repo_template/docs/` 下的 `usage.md`、`architecture.md` 与新建的 `recovery.md`、`sync.md`；各 `SKILL.md` 的「组合契约」段；`repo/AGENTS.md`；脚本中指向被迁移 `usage.md` 章节的提示字符串 |
| 1 | 主会话 | B1 | 工厂 `.scratch/`（不入 git） |
| 2 | `wp4_orchestration` | C1–C5、A3、J1 | `repo_task/` 的 `cli`、`attempts`、`lifecycle`、`integration`、`recovery`、`monitoring`、`control`；`task-run`、`task-work`、`task-integrate` 三个 skill；`usage.md` / `recovery.md` 的对应段；新增测试 |
| 2 | `wp5_sync_refactor` | H1、G2 | `repo_sync.py` → `template_sync/` 包；同步相关测试；`repo/.claude/settings.json`；`usage.md` 状态行 hook 段（与 WP4 同文件不同章节） |
| 2 | `wp10_ac_review_gate` | J2–J4 | `review_prompts/` 四个文件；`check_review_status.py`；`repo_task/review.py`；task 模板 `task.md`；`tests/review_support.py` 与 review 相关测试 |
| 3 | `wp6_archive_at_integrate` | D1–D3 | `lifecycle`、`integration`、`monitoring`、`recovery`；`repo/AGENTS.md` 的 archive 行、`docs/tasks/` 行与 commit 条；`architecture.md`「合并」；三个 task skill 与 `recovery.md` 中的 amend 规程；同步器工作流迁移的前置检查 |
| 3 | `wp7_queue_topology` | E1、E2 | `task-run` 的「授权与队列」「执行」「停止」段；`plan.py`；`goal.py`；`architecture.md`「执行拓扑」（与 WP6 同文件不同章节） |
| 4 | `wp8_rename_progress` | D4 | 全仓 `note.md` 引用、task 模板、同步器迁移、相关测试 |
| 5 | `wp11_test_parallel` | H3 | `repo/.repo_template/tests/`、`factory_tests/`、两处 `conftest.py` 与 `pytest.ini`、根 `AGENTS.md` / `README.md` 的测试命令 |

### 工作包通用约束

- 遵守工厂 `AGENTS.md`。改模板必须跑全量测试：G1 合入前用 `pytest repo/.repo_template/tests -q`，合入后用 G1 定义的工厂命令。`--basetemp` 指向系统临时目录（`"$(mktemp -d)"`），不用仓内 `.scratch/`。
- 新行为先写失败测试。旧测试语义失效时，整体删除并在 commit 中写明理由，禁止就地修改预期。
- `repo/` 下的 Markdown 用 md_kx 格式化：在 `repo/` 目录执行 `python3 .repo_template/scripts/md_format.py <路径>`。
- 不做范围外修改；发现的问题写进完成报告。
- commit message 遵循仓库现有风格。

### 每波验收

- 本波全部 WP 合入集成分支后，全量测试通过（含产物导出后的契约测试）；
- `repo/` 下本波改动的 Markdown 通过 `md_format.py --check`；
- 主会话复核各 WP 报告中的范围外发现，决定登记或纳入后续波次。

## 12. 实施记录

| 范围 | 提交与证据 |
| --- | --- |
| WP1–WP7、WP10 | 合入至 `e80128e`；当时全量 890 passed、1 skipped |
| WP8 | 常量 `76bfc19`、改名 `b87161f`、迁移 `b5cd712`，合入 `829d7dd` |
| WP11 | 隔离测试替身并启用 xdist `9769df2`；消费默认 contract 命令不变 |
| 缺陷修复 | `14a0105`、`aec606f`：创建超时后核实资源，未知状态不误报零写入；`224ad1c`：按 verification_policy 识别定制项目，避免 worktree 缺少本机 state 时误判 |
| 集成 | `1c05bae`；产物树 `c482c2827313acc8104a3d534b03d8d4ddfffa4a` 与 `224ad1c:repo` 一致 |
| 最终全量 | 900 passed、1 skipped，2 worker，437.11 秒；Python 3.14.7、pytest 9.2.0、pytest-xdist 3.8.0；未达到原两分钟估算 |
| 其它检查 | Markdown、skill registry、CLI help、git diff --check 通过；本机未跑 Python 3.9 |
| 主路径调用 | 正常 task 的状态与审查脚本共 8 次，加队列 plan 为 9 次；项目测试与按需恢复另计 |
