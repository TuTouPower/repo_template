# repository 模块审阅报告

## 当前模型判断依据

会话环境标识为主会话模型 `default_haiku[1m]`;本报告由 sonnet 视角产出(用户授权 my-review 调用 sonnet)。

## 模块 slug

`repository` —— 覆盖 `repo_template` 仓库全部 tracked 文件,重点放在 task 并发(task-dispatch)与串行(task-run)工作流,以及与其交互的脚本、测试、模板、索引和规范。

## 审阅范围

`git ls-files` 共 103 项,本次全量阅读:

- 模板正文与全局约定:`AGENTS.md`、`CLAUDE.md`(项目级)、`README.md`、`docs/blueprint/{architecture,conventions,decisions,domain,testing}.md`。
- 工具链脚本:`scripts/repo_template/{_id_scan.py,check_review_status.py,findings.py,pending.py,render_review_prompts.py,spikes.py,task.py}`。
- Hooks 与 settings:`.claude/hooks/merge_guard.py`、`.claude/settings.json`。
- Skill 正文:`.agents/skills/{task-create,task-dispatch,task-from-pending,task-integrate,task-merge,task-preflight,task-run,task-schedule,task-work,task-bug,repo-cleanup,repo-hygiene}/SKILL.md`。
- 测试:`tests/repo_template/{test_check_review_status,test_merge_guard,test_pending,test_render_review_prompts,test_task_archive_dir,test_task_document_validation,test_task_save,test_task_scheduling,test_task_start_flow,test_task_unverified}.py` 共 227 用例。
- 模板与 prompts:`docs/tasks/task_template/{spec,task}.md`、`docs/spikes/report_template.md`、`docs/reviews/prompts/{code,general,share,test}_prompt.txt`、`docs/{tasks_index,archive/tasks_index}.json`。
- 复盘笔记(只读参考):`docs_repo/{workflow_reflection_5.md,workflow_reflection_6.md,analysis_omni_gate_gaps_2026_07.md,plan_task_batch_scheduling.md}`。

本地 `pytest -q` 227 用例全过。源文件未修改,未运行破坏性命令。

## 高优先级

### H1 `task.py` 默认队列与 `task-run` 输入表不一致,自动续跑范围模糊

- 位置:`scripts/repo_template/task.py:2306-2336` `cmd_list`;`.agents/skills/task-run/SKILL.md:28-36`「输入与固定队列」。
- 现象:`task-run` 规定「无参数队列 = `backlog` ∪ `active`(tid 升序)」,且 `CLI 一次只能带一个 --status,默认队列由两次 list 合并去重`;但 `task.py list` 的 CLI(`scripts/repo_template/task.py:2479-2482`)确实只允许单个 `--status`。问题是 `task-run` skill 把「合并两次 list」作为恢复期「确定队列」的机械步骤,而 `list` 默认只读不写、不返回「待合并去重」的稳定结构 — skill 文本要求 agent 自行两次调用并在内存合并,没有对应脚本辅助,容易在恢复期漏捞 active 或重复纳入已被 worktree 覆盖的旧 backlog。
- 影响:中断恢复时队列口径漂移,可能漏跑或重跑;`task-run` 的「开始修改状态前固定 tid 与顺序」靠 agent 自觉,没有脚本校验兜底。
- 建议:在 `task.py` 增 `queue` 子命令(或 `list --todo` 语义),一次性输出 `backlog ∪ active` 的有效状态视图(用 `discover_effective_tasks()`),消除「两次 list 合并」的人工拼接;或在 `task-run` skill 显式说明「以 `task.py view` 的 `[待运行]` 为准,再与 `list --status active` 求并集」,并要求把最终队列写进 `task.md` 实施笔记。
- 置信度:中。
- 优先级:高。

### H2 `merge_guard` hook 与 `task.py integrate` 的相互作用未在任何文档说明

- 位置:`.claude/hooks/merge_guard.py:1-213`;`.claude/settings.json:1-16`;`scripts/repo_template/task.py:2075-2165` `cmd_integrate`;`.agents/skills/task-integrate/SKILL.md:29-55`。
- 现象:`merge_guard` 拦截 `Bash` 工具中所有 `git merge` / `gh pr merge`,要求 token 授权;但 `task.py integrate` 在 Python 内部用 `subprocess.run(["git", ..., "merge", "--no-ff", ...])` 直接调 git(`task.py:2135`),**不经 Bash 工具,hook 不会触发**。也就是说,coordinator 走 `task.py integrate` 完全绕过 token 授权,而 worker 或用户若手敲 `git merge`(例如测试、修复冲突时手动验证)会被拦截。这套设计本身合理(脚本受信任入口),但:
  - `.claude/settings.json` 仅注册 PreToolUse hook,没有文档说明「`task.py integrate` 内部 merge 不被 hook 拦截是预期」。
  - `task-integrate` skill 的「冲突处置」要求用 `task.py integrate --continue`,但若 agent 在解决冲突过程中临时用 `git merge --abort` 或重新 `git merge` 试验,会触发 hook 拦截且需要 token — 而任何 token 都因 `cmd_hash` 绑定(`merge_guard.py:147-167`)只能用一次,试错式 merge 多次会被反复拦。
  - `merge_guard.py:99-103` `detect_merge` 用正则扫命令;`git merge --abort`、`git merge --continue` 也会被识别为 merge(取后续第一个非选项 token 作为 target,例如 `--abort` 被当 branch?— 实际 `_git_merge_target` 跳过 `-` 开头 token,会返回 `"unspecified"`)。
- 影响:冲突解决过程中 agent 误用手敲 merge 命令会被 hook 反复拦;用户若不理解「task.py 内部 merge 不走 hook」,可能误以为 token 授权对 integrate 生效而困惑。`--abort`/`--continue` 被识别为 merge 但 target 为 `unspecified`,会签发无意义 token。
- 建议:
  1. 在 `task-integrate` skill 显式注明「`task.py integrate` 内部 merge 不经 merge_guard;解决冲突时如需手动试验,须按 merge_guard 流程取 token,且 `git merge --abort` 同样会触发拦截」。
  2. `merge_guard.detect_merge` 对 `git merge --abort/--continue/--no-commit` 等子选项场景给一个更明确的 target(或直接放行 `--abort`),减少 `unspecified` token 噪音。
- 置信度:中。
- 优先级:高。

### H3 `task-dispatch` 的「补位」依赖 `view` 主干视角,但 `view` 在并发合并窗口期读不到刚 done 的状态

- 位置:`.agents/skills/task-dispatch/SKILL.md:39-71`;`scripts/repo_template/task.py:999-1207` `cmd_view`。
- 现象:`task-dispatch` 步骤 1 用 `task.py view` 算可跑集,步骤 5 完成即合并,步骤 6 回步骤 1 重算。`view` 的 `main_done_set` 来自 `scan_tasks()`(读当前工作区 = 主干视角,`task.py:1062-1064`)。问题在:`view` 的「有效 done」与「main done」分离 — `tasks` 来自 `discover_effective_tasks()`(含未合并分支的 done),但 `done_set = main_done_set`(`task.py:1072` 注释「调度判断用 main 视角」)。这意味着:
  - 并发场景下,worker A 完成 t001 但还没 integrate,worker B 完成 t002 已 integrate;此时 t001 在未合并分支中 done,`view` 会把它列入「未入 main」(`task.py:1195-1200`),`done_set` 不含 t001,下游依赖 t001 的 task 仍被阻塞。
  - `task-dispatch` 的「补位」语义是「完成即合并 → 解锁下游」,但 **`view` 只在 integrate 完成后才更新 main_done_set**,所以 worker B 看到 `view` 输出里 t001 在「未入 main」时,coordinator 必须先 integrate t001 才能继续 — 这是设计意图(串行合并),但 skill 文本没有说明该等待条件。
- 影响:`task-dispatch` skill 步骤 6「回第 1 步重算可跑集;有新解锁且未达并发上限则继续 start」隐含「合并已发生」,但未约束「下游 task 必须等依赖 integrate 后才能 start」— 实际依赖 `view` 的 main 视角天然保证,但 agent 读 skill 可能误解为「依赖一旦在分支 done 就能跑」。
- 建议:在 `task-dispatch` skill 的「补位」段或「并行纪律」加一句:「`view` 以主干视角判 done,依赖 task 必须已 `integrate` 进主干才解锁下游;worker 报告 done 但未 integrate 不算解锁」。
- 置信度:中。
- 优先级:高。

### H4 `task-bug` 与 `task-from-pending` 链式调用 `task-create` 时的索引重建归属不清

- 位置:`.agents/skills/task-bug/SKILL.md:34-43`;`.agents/skills/task-from-pending/SKILL.md:40-46`;`.agents/skills/task-create/SKILL.md:50`「全部 task 落盘自检后统一询问提交」;`AGENTS.md:18` 目录权责表(`docs/tasks_index.json` 仅由 `integrate` 在合并后重建)。
- 现象:`task-create` 第 6 步「本次所有 task 完成第 3-5 步落盘与自检后,一次性列出全部新建 task 目录与本次重建的两个 index,询问用户是否提交」;`task.py add` 内部调 `rebuild_index()`(`task.py:1511`)会写两个 JSON。`task-bug` 第 7 步链式调用 `task-create`,第 8 步「询问提交 bug 总账」说「已批准立项:`task-create` 已批量提交 task 目录与派生 index;这里一并列出,用户同意后提交 bug 登记」— 但 `task-bug` 第 7 步进入 `task-create` 时,`task-create` 会**自己**询问一次提交(第 6 步),`task-bug` 第 8 步又问一次 bug 总账提交,两层 commit 边界都在,但 skill 文本相互引用「已批量提交」会让 agent 困惑:到底 bug 总账 commit 是含 task 目录,还是另起?
  - 更具体:`task-from-pending` 第 5 步明确「按 `task-create` 流程,链式调用」,第 7 步「`task-create` 已批量提交 task 目录与派生 index;这里只列出本次迁移的条目文件,询问用户是否提交」。这暗示 task-create 的 commit 已经发生,from-pending 只剩 pending archive 回写。但 `task-bug` 第 8 步写「已批准立项:`task-create` 已批量提交 task 目录与派生 index;**这里一并列出**,用户同意后提交 bug 登记」— 「一并列出」含混,可能让 agent 把 task 目录 + bug 条目合并进一个 commit,违反「task 创建 commit 与 bug 总账分开」。
- 影响:bug 立项场景的 commit 边界容易被 agent 合并,违反「每个 commit 独立可验证,有工程意义」(`AGENTS.md:51`)。
- 建议:
  1. 在 `task-bug` 第 8 步显式注明:「task 目录与 index 由 `task-create` 第 6 步独立 commit;bug 条目文件单独 commit,不与 task 目录合并」。
  2. `task-from-pending` 第 7 步已较清晰,可作模板。
- 置信度:中。
- 优先级:高。

### H5 `discover_effective_tasks` 对「rewind 保留的旧分支」判定有边角缺口

- 位置:`scripts/repo_template/task.py:928-967` `discover_effective_tasks`;`task.py:881-914` `task_effective_state`;`tests/repo_template/test_task_start_flow.py:967-983` `test_rewind_backlog_not_covered_by_stale_branch`。
- 现象:`discover_effective_tasks` 的注释(`task.py:944-945`)说「rewind 保留的分支状态过时:main 已显式回 backlog 时,分支 active/blocked 不覆盖」。逻辑是:`main_task.status == "backlog" and branch_task.status in ("active","blocked")` 时 `continue`,跳过分支覆盖。这正确处理了「rewind 后 main=backlog,旧分支=active」的情况。但:
  - 如果 rewind 时分支有 own commit 被保留(`test_rewind_keeps_branch_with_own_commits_and_guides`),分支状态可能是 `done`(rewind 前刚 finish 但未 integrate),而 main 是 backlog — 此时 `branch_task.status == "done"` 不满足 `in ("active","blocked")`,分支 done 会**覆盖** main backlog,`effective[owner_tid] = branch_task`(`task.py:951`)。这意味着一个已 rewind 回 backlog 的 task,如果旧分支恰好是 done(被 rewind 保留),会被 view 当作「有效 done」 — 但 rewind 语义是「撤回」,不该当 done。
  - `task_effective_state`(edit/drop 用)只检查 active/blocked(`task.py:898-913`),不检查 done,所以 edit/drop 在 main=backlog + 旧分支 done 场景下会认为「无覆盖」,允许操作 main 副本 — 但 view 又把该 task 当 done。两个函数对同一状态的判定不一致。
- 影响:rewind 后保留的 done 分支会让 view 显示该 task 为 done(而非 backlog),与 main 真实状态不符,可能误导调度或让 agent 误以为 task 已完成。实际命中概率低(rewind 通常发生在 active/blocked,done 状态会先 integrate),但逻辑上存在缺口。
- 建议:`discover_effective_tasks` 的「rewind 过时分支」判定扩展为「main=backlog 时,任何非 backlog 的分支状态都视为过时」(`branch_task.status != "backlog"`)或至少把 `done` 纳入;同步 `task_effective_state` 的判定。
- 置信度:中。
- 优先级:高。

## 中低优先级

### M1 `cmd_view` 的 `dropped` 校验只覆盖 `depends_on`/`conflicts_with` 引用,不区分有效与归档

- 位置:`scripts/repo_template/task.py:1027-1035`;`task.py:449-465` `task_schedule_references`(drop/purge 用,跳过归档)。
- 现象:`view` 在图校验时对 `dropped` 引用直接报 `invalid_graph`(`task.py:1029-1034`),不区分被引用的 dropped task 是活跃目录还是归档目录。`task_schedule_references` 注释说明「只扫活跃目录:归档 task 的历史边无脚本清理途径...若计入会永久锁死被引用 task 的 drop」(`task.py:453-455`)。两个函数对 dropped 边的语义不同:view 视任何 dropped 引用为非法;task_schedule_references 只看活跃。实际测试 `test_drop_ignores_archived_task_historical_edges`(`test_task_start_flow.py:1535-1563`)确认归档 done task 的历史边不锁死 drop。但 view 如果读到归档 task(done)被某活跃 task 当依赖,会报错吗?— `view` 把 done 也当 `main_done_set` 解依赖(`task.py:1062-1068`),dropped 才报错。问题在:如果活跃目录里某 task 的 `depends_on` 指向一个**已归档的 dropped** task,view 会报 `invalid_graph: ... 引用 dropped task`(`task.py:1031-1034`),而该 dropped 已在归档目录、无脚本清理途径 — 与 task_schedule_references 的「归档历史边不锁死」语义冲突。
- 影响:历史归档 dropped task 的悬空边在 view 中报错,但 drop 已无法清理(对方已归档),需要手改 front matter。实际命中概率低(归档 dropped 通常是被 drop 而非被依赖),但语义不一致。
- 建议:`view` 对「引用的 dropped task 已在归档目录」的情况,要么静默跳过(类似 task_schedule_references),要么显式提示「历史悬空边,需手改」。
- 置信度:中。
- 优先级:中。

### M2 `check_review_status.py` 的 `regression_rounds` 在「报告被重建(无历史 FAIL 行)」场景回退到 Round 标题最大值,边界宽松

- 位置:`scripts/repo_template/check_review_status.py:95-110`;`tests/repo_template/test_check_review_status.py:84-107`。
- 现象:`regression_rounds` 取「FAIL 计数 + 1」与「`## Round N` 标题最大值」的较大者。如果 reviewer 重建报告时把历史 FAIL verdict 都删了(只保留最新 PASS),`best` = 0,但 `max_header` 可能仍读到 Round 5 标题 — 返回 5。注释说这是「兼顾 reviewer 报告被重建的场景」,但实际放宽了 round 计数,可能让「首轮 PASS 但报告里有 Round 5 标题残留」的情况被计为 round=5,触发 `withdraw_rate` 与 `max_review_round` 比较时的误判。
- 影响:`round` 字段被 task-work Step 6 用来判「FAIL 且 round < max」;如果 round 虚高,可能让本该继续回归的 task 误判为「满轮 blocked」。实际影响有限(task-work 还看 overall),但是计数不严谨。
- 建议:文档或注释显式说明「round 取 max(verdict 推断, 标题)」的权衡;或分离两个字段(`verdict_rounds` / `header_rounds`)让 task-work 自行决策。
- 置信度:低。
- 优先级:中。

### M3 `task.py _close_task` 在「in_own_worktree 且 move 失败」时不回滚归档目录已存在校验

- 位置:`scripts/repo_template/task.py:1876-1933` `_close_task`;`tests/repo_template/test_task_archive_dir.py:184-199` `test_close_task_rolls_back_when_move_fails`。
- 现象:`_close_task` 先 `write_front_matter` 写 done/dropped,再 `shutil.move`;move 失败时 `write_front_matter(path, orig_fm, body)` 回滚 front matter(`task.py:1927-1931`)。测试覆盖了这个路径。但 `dst.exists()` 校验(`task.py:1903-1904`)在 move 之前,如果两个并发 finish/drop 同时操作同一 tid(理论上不会发生,因为 worktree 互斥),`dst` 可能在校验后、move 前被另一方创建 — 此时 move 会失败但 orig_fm 已写,回滚正确。逻辑成立,但 `dst.exists()` 与 `shutil.move` 之间存在 TOCTOU 窗口。实际单 worktree 互斥保证下不会触发。
- 影响:理论上的并发 race,实际被 worktree 互斥消除。
- 建议:无需改动,记录为已知安全边界。
- 置信度:高。
- 优先级:低。

### M4 `render_review_prompts.py` 与 `task.py` 各有 front matter 解析副本,「改规则需三处同步」靠注释提醒

- 位置:`scripts/repo_template/task.py:386-395` `parse_front_matter`;`render_review_prompts.py:38-56`;`check_review_status.py:72-92`。
- 现象:三处简化副本,各自注释「改解析规则需三处同步」(task.py:390、render_review_prompts.py:39、check_review_status.py:73)。三处行为基本一致(剥引号、剥行内注释),但:
  - task.py 的 `_unquote` 支持转义还原(`task.py:345-361`);两份简化版用 `strip('"').strip("'')` 不还原转义。
  - 实际 front matter 经 `dump_front_matter` 写出时所有值双引号包裹并转义(`task.py:398-404`),读回时 task.py 正确还原,另两处简化版不还原 — 如果 title/note 含 `\"`,review prompt 渲染与 check_review_status 会读到带反斜杠的字面值。
- 影响:含转义字符的 front matter 字段在 review/检查链路上显示不正确,但不影响调度(调度用 task.py)。
- 建议:把 front matter 解析抽成公共模块(如 `scripts/repo_template/_frontmatter.py`),三处共用;或至少让两份简化版复用 task.py 的 `parse_front_matter_text`。
- 置信度:中。
- 优先级:中。

### M5 `task-work` Step 1 「`{doctor_cmd}` 无则实施笔记写『无』」与门禁逻辑脱节

- 位置:`.agents/skills/task-work/SKILL.md:62-64`;`docs/blueprint/testing.md:5-6`。
- 现象:testing.md 定义 `{doctor_cmd}` 为「环境前置检查」,task-work Step 1 第 1 条「有 `{doctor_cmd}` 则跑;无则实施笔记写『无』」。但 testing.md 模板里 `{doctor_cmd}` 是占位符,项目复制后需填写;如果项目没填,task-work 默认写「无」即跳过 — 没有门禁强制项目必须填。`preflight` 也不检查 `{doctor_cmd}` 是否为占位符。
- 影响:新项目复制模板后若忘记填 testing.md 的三个命令占位符,所有 task 的 doctor/test/blackbox 都成「无」,门禁形同虚设。
- 建议:`task.py add` 或 `preflight` 加一条占位符检查:testing.md 中 `{doctor_cmd}`/`{test_cmd}`/`{blackbox_verify}` 未填时 warn(不阻塞,但提示项目方初始化)。
- 置信度:中。
- 优先级:中。

### M6 `repo-cleanup` 的 `scratch` 类别跳过逻辑依赖 agent 正确解析 spec/笔记中的 `.scratch/` 路径

- 位置:`.agents/skills/repo-cleanup/SKILL.md:83-87`;`AGENTS.md:40`(目录权责)。
- 现象:`repo-cleanup apply scratch` 要「读各有效来源中的 `spec.md` 上下文区与 `task.md` 实施笔记,收集提及的 `.scratch/` 相对路径 → 跳过不删」。这是纯 agent 推断,没有脚本辅助;如果 spec/笔记里写的路径不完整(如只写 `.scratch/foo` 而实际是 `.scratch/foo/bar.py`),agent 可能漏判删掉在用文件。
- 影响:scratch 清理误删风险,但用户场景低(scratch 默认 gitignore,通常 agent 自清理)。
- 建议:`repo-cleanup` 边界明确「无法解析引用 → 不删 `.scratch/`」(已有),并可加一条「建议 agent 用 `rg '\.scratch/[^\s)\"\']+'` 列候选再人工核对」。
- 置信度:中。
- 优先级:低。

### M7 `docs_repo/plan_task_batch_scheduling.md` 描述的 `next-batch` 子命令与最终实现的 `view` 不一致

- 位置:`docs_repo/plan_task_batch_scheduling.md:90-186`(描述 `next-batch`);`scripts/repo_template/task.py:999-1207`(实现为 `view`)。
- 现象:该计划文档(2026-07-30)描述的子命令叫 `next-batch`,支持 `--done` 宽松解析;最终实现改为 `view`,无 `--done` 参数(状态完全来自仓库)。文档是 `docs_repo/`(模板仓设计笔记,非业务文档,不参与 task 状态机),但仍是 tracked 文件,可能误导阅读者。
- 影响:`docs_repo/` 复制新项目时本就不该带入(`AGENTS.md:34` 注明),影响有限。
- 建议:在 `docs_repo/decision_log.md` 或该文件顶部加「实施时改为 `view`,详见 task.py cmd_view」的落地说明;或直接 archive 该计划文档。
- 置信度:高。
- 优先级:低。

### M8 `share_prompt.txt` 与 task 模板里的路径引用 `.agents/skills/tasks-run/SKILL.md` 是正确的,但「L11/L2/L14」等内部分类码未在仓库定义

- 位置:`docs/reviews/prompts/share_prompt.txt:74,76`;`docs_repo/analysis_omni_gate_gaps_2026_07.md:151-153`(L11/L2/L14 出处)。
- 现象:`share_prompt.txt` 引用 `.agents/skills/tasks-run/SKILL.md`(路径正确),但 `docs_repo/analysis_omni_gate_gaps_2026_07.md` 提到的「L11 TDD 顺序违规」「L2 finding 锚 AC」「L14 一 task 一 commit」是 omni_media 项目的本地裁决码,在 `repo_template` 仓库里没有定义。`share_prompt.txt` 本身没引用这些码,只是 `docs_repo/` 复盘笔记提到,不影响模板使用。
- 影响:无实际影响,仅记录。
- 建议:无需改动。
- 置信度:高。
- 优先级:低。

## 改进建议

按优先级汇总可执行改动:

1. **H1 队列口径**:在 `task-run` skill 显式写明「默认队列 = `task.py view` 的 `[待运行]` 与 `list --status active` 的并集,开始前写入实施笔记」;或 `task.py` 加 `queue` 子命令固化。低成本优先选前者。
2. **H2 merge_guard 文档**:`task-integrate` skill 加一段「`task.py integrate` 内部 merge 不经 hook;冲突解决时手动 `git merge` 试验需按 merge_guard 流程」。
3. **H3 view 解锁时机**:`task-dispatch` skill「补位」段加一句「下游 task 必须等依赖 `integrate` 进主干才解锁,未合并的 done 不算」。
4. **H4 bug 立项 commit 边界**:`task-bug` 第 8 步显式区分「task 目录 commit(task-create 第 6 步)」与「bug 条目 commit(本步)」两个独立 commit。
5. **H5 rewind 后 done 分支判定**:`discover_effective_tasks` 与 `task_effective_state` 的「rewind 过时分支」判定扩展为「main=backlog 时,任何非 backlog 分支状态都视为过时」。需补测试覆盖「main=backlog + 分支 done」场景。
6. **M4 front matter 解析去重**:抽 `_frontmatter.py` 共用模块,三处 import;或至少简化版调用 task.py 的 `parse_front_matter_text`。
7. **M5 testing 占位符门禁**:`task.py add` 或 `preflight` 加 testing.md 占位符 warn。
8. **M1 view dropped 历史边**:对归档 dropped 的悬空边静默跳过或显式提示。

## 不确定项

- **H5 的实际命中概率**:rewind 通常发生在 active/blocked,done 状态会先 integrate;但「finish 后未 integrate 就 rewind」的路径(`cmd_rewind` 接受 effective in STATUS_ORDER = backlog/active/blocked,done 不在 rewind 范围)实际上**不会**保留 done 分支 — 因为 finish 后 status=done,要 rewind 得先... 实际 `cmd_rewind` 对 `effective not in STATUS_ORDER` 直接拒绝(`task.py:2195-2199`)。所以「main=backlog + 分支 done」场景要靠人工构造:finish+commit+cleanup-worktree(分支保留 done)→ 手动把 main front matter 改回 backlog。这种状态在正常流程中**不会出现**,H5 的实际风险低于描述,但逻辑上 `discover_effective_tasks` 仍多了一层冗余判定。审阅置信度下调为低。
- **merge_guard 对 `git merge --abort` 的实际行为**:正则 `git\s+merge(?![-])` 否定前瞻排除 `merge-base`,但 `merge --abort` 的 `--abort` 以 `-` 开头,会被 `(?![-])` 通过(即识别为 merge);`_git_merge_target` 跳过 `-` 开头 token 返回 `unspecified`。该判断基于代码静态阅读,未在真实 hook 环境实跑验证。
- **H3 的并发窗口**:实际 `task-dispatch` 强制「合并串行:一次只处理一个 integrate」(`task-dispatch` 并行纪律段),所以「worker A 完成 t001 但未 integrate,worker B 已 integrate t002」的窗口里,view 输出 t001 在「未入 main」是正确的;coordinator 会立即 integrate t001 再继续补位。H3 的影响主要是 skill 文本未明确该等待条件,而非逻辑错误。审阅置信度维持中。
