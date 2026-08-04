# review_repository_claude_opus

## 当前模型判断依据

主会话模型：default_haiku[1m]（会话环境明确标识）。本路按用户 my-review 授权以 opus 视角独立审阅。

## 模块 slug

repository

## 审阅范围

仓库全部 tracked 文件（103 个），重点：`scripts/repo_template/task.py`（2501 行）、`_id_scan.py` / `pending.py` / `findings.py` / `spikes.py` / `render_review_prompts.py` / `check_review_status.py`、`.claude/hooks/merge_guard.py`、12 个 `.agents/skills/*/SKILL.md`、`AGENTS.md`、task/spike 模板、review prompt 模板、`tests/repo_template/`（227 个测试，本地实跑全绿 14.6s）、`docs/blueprint/`。

审阅焦点：状态机一致性、worker/coordinator 写域、worktree 生命周期、完成即合并、并发补位、串行调度（链式 `--base` / `integrate --chain`）、依赖/冲突计算、失败与 blocked 路径、index 重建、测试覆盖、文档与脚本一致性。

## 总体结论

核心状态机实现质量高：两套拓扑（串行链式 / 并行扇出）在 task.py 内分离清晰，`resolve_start_base` / `_resolve_chain` / `discover_effective_tasks` 的校验链完整；worktree 生命周期（start → finish → cleanup-worktree → integrate）每一步都有归属校验（登记分支、slug、终态、脏改动）与失败补偿（`rollback_start`、`_close_task` 回滚）；view 的图校验（自引用、悬空、 dropped 引用、依赖环、schedule_status 合法性）与阻塞分组计算经 227 个测试覆盖，含并发取号、跨 worktree 扫描、链式合并、冲突续合等真实 git 场景。worker/coordinator 写域由 `require_primary_worktree` / `require_own_task_worktree` 强制，worker 侧无法执行 integrate / list --rebuild。

发现的问题集中在文档与脚本/测试的边角不一致，无状态机级别的正确性缺陷。

## 高优先级

### H1. share_prompt.txt 引用不存在的路径，reviewer 被指向空气

- 位置：`docs/reviews/prompts/share_prompt.txt:53,62,70,74,76`
- 现象：
  - 第 53 行 `tasks-run`（应为 `task-run`，多了 s）
  - 第 62 行 `scripts/task.py list` / `scripts/task.py show <tid>`（应为 `scripts/repo_template/task.py`）
  - 第 70/74/76 行 `.agents/skills/tasks-run/SKILL.md`（应为 `.agents/skills/task-run/SKILL.md`）
- 影响：share_prompt 注入每一路 reviewer prompt。reviewer 按「权威流程见 `.agents/skills/tasks-run/SKILL.md`」找文件必然失败，按 `scripts/task.py list` 执行「系统性缺口去重」的只读命令也必然失败——去重机制实际不可用，跨 task 重复 finding 会回升（L9/L16 落地的机制被路径错误架空）。且该错误在 skill 改名（commit 8ba36e6）后引入，说明 prompt 模板不在任何一致性校验范围内。
- 建议：改正 5 处路径；在 `tests/repo_template/` 加一个轻量测试，扫描 `docs/reviews/prompts/*.txt` 中反引号包裹的仓库内路径并断言存在（或至少断言 `scripts/repo_template/task.py` 与 `.agents/skills/task-run/SKILL.md` 字样出现、旧字样不出现）。
- 置信度：高
- 优先级：高

## 中低优先级

### M1. 权责表「index 仅由 integrate 重建」与实际行为不符

- 位置：`AGENTS.md:18`（`docs/tasks_index.json` 行）vs `scripts/repo_template/task.py` 的 `cmd_add:1511` / `cmd_edit:1701` / `cmd_rewind:2271` / `cmd_purge:2302`（均调 `rebuild_index()`）
- 现象：权责表写「仅主仓 coordinator 由 `task.py integrate` 在合并后重建并单独 commit」。实际 add / edit / rewind / purge 也在主仓重建 index 文件（不落独立 commit，由 task-schedule / task-create 的维护 commit 一并提交，task-schedule SKILL 第 7 步明确承认了这一点）。
- 影响：权威定义与实现分叉。读 AGENTS 的 agent 可能误以为 edit 后 index 未重建而手动 `list --rebuild` 或把 index 变化当成异常。属于「同一事实两处表述」中的权威定义过时。
- 建议：权责表改为「由 `task.py` 各主仓写命令重建；`integrate` 在合并后重建并单独 commit；创建/调度维护 commit 携带 add/edit 的重建结果」。
- 置信度：高
- 优先级：中

### M2. task-work 未说明串行链式下 `--base` 由谁传入

- 位置：`.agents/skills/task-work/SKILL.md:62-64`（Step 1.2）；`.agents/skills/task-run/SKILL.md:55-59`
- 现象：task-work 只接受一个 `tNNN`，Step 1.2 写「没有现成 worktree 时，在主仓默认分支执行 `task.py start <tid>`」——无 `--base` 的任何提及。task-run 队列循环注释写 `task-work（--base t001）`，但 task-work 本身无 `--base` 参数，实际是 task-run 须先自行执行 `task.py start {tid} --base {prev_branch}` 再调 task-work。这个分工只可从 task-run 的伪码推断，未在任一 skill 明写。
- 影响：串行恢复场景（task-run「恢复」节第 2 条）或 worker 独立被调用时，worker 看到「无 worktree 就 start」会直接从主干 HEAD 扇出，断掉链式拓扑——t002 丢失 t001 的成果，后续 `integrate --chain` 的祖先假设被破坏（链上分支不再是祖先，`_resolve_chain` 收不齐，合并后部分分支残留）。
- 建议：task-work Step 1.2 补一句：串行链式由调用方（task-run）先执行 `start --base` 建 worktree，本 skill 发现现成 worktree 时不得重新 start；task-run 队列循环把「coordinator 先 start --base，再调 task-work」写成显式步骤。
- 置信度：中高（行为路径经代码确认，但实际触发需要 worker 在串行队列中被单独唤起）
- 优先级：中

### M3. merge_guard 与 task.py integrate 的双通道未在任何文档说明

- 位置：`.claude/hooks/merge_guard.py`（拦截 Bash 工具的 `git merge`）；`scripts/repo_template/task.py:2135`（integrate 内部 subprocess 直接 `git merge`，不经 Bash 工具）
- 现象：agent 手动 `git merge` 会被 hook 拦截并要求一次性 token；`task.py integrate` 内的 merge 是脚本子进程，hook 拦截不到，只需 task-run / task-dispatch 的会话级前置授权。两条授权通道并存是合理设计（脚本通道 = 已授权通道），但 AGENTS.md / task-integrate / merge_guard docstring 均未说明这一分工。测试（`test_task_start_flow.py` 多个 integrate 用例）也默认 hook 不拦脚本。
- 影响：后来者可能误判（a）hook 是冗余的而删除它，或（b）integrate 绕过授权是 bug 而给脚本加 token。另外 merge_guard 对 `git merge --abort` 也拦截（`GIT_MERGE_RE` 命中），放弃冲突合并也需 token 授权——task-integrate 第 3 步教用户 `git merge --abort`，但未提示该命令同样要过 token 流程，agent 执行时会被拦一次才拿到 token，流程上多一轮无效往返。
- 建议：在 task-integrate「边界」或 AGENTS「执行角色与合并时机」补一句：脚本内 merge 由会话级授权覆盖，merge_guard 只拦脚本外的手动 merge；`git merge --abort` 同样过 token。
- 置信度：高
- 优先级：中

### M4. view 阻塞分组标题「被 active 冲突阻塞」与实际内容不符

- 位置：`scripts/repo_template/task.py:1125-1132, 1173-1179`
- 现象：`blocked_conflicts` 汇集三类：peer active/blocked、peer 未合 main 的 done、peer 是序号更小的 backlog（强制择一）。输出分组标题统一为「▸ 被 active 冲突阻塞」。第三类 peer 不是 active，标题误导；`task-schedule` SKILL 第 6 步让 agent 原样报告 view 输出，标题会进用户视野。
- 影响：低。用户看到 backlog↔backlog 的择一阻塞被标为「active 冲突」，可能误以为有 task 在跑。
- 建议：标题改「▸ 被冲突阻塞」，或拆两个分组。
- 置信度：高
- 优先级：低

### M5. 三处 front matter 解析副本的转义处理不一致

- 位置：`scripts/repo_template/task.py:_unquote`（处理 `\\` / `\"` 转义）vs `render_review_prompts.py:55`、`check_review_status.py:91`（`val.strip('"').strip("'")`，不处理转义且会破坏含引号的值）
- 现象：task.py `dump_front_matter` 对所有值双引号包裹并转义；两个简化副本对 `title: "他说"你好""` 这类值会解析出截断结果。三处同步的注释已写明，但只约束「解析规则」，未约束转义语义。
- 影响：低。render/check 只消费 tid/slug/diff_anchor/review_level/status，均为受控词汇不会含引号；title/note 不经过这两个脚本。属潜在陷阱而非现行 bug。
- 建议：把 `_unquote` 逻辑抽成共用最小实现（三脚本同目录可直接 import），或至少在简化副本注释中写明「不解析转义，仅适用受控字段」。
- 置信度：高
- 优先级：低

### M6. prompt 模板与 skill 文档的一致性无测试守护

- 位置：`tests/repo_template/`（227 个测试）vs `docs/reviews/prompts/*.txt`、`docs/tasks/task_template/*`
- 现象：H1 的过时路径能存活，根因是 prompt 文本不在任何断言内。`test_render_review_prompts.py` 只测占位符替换与结构，不断言模板内容中的路径引用有效；task 模板有 `validate_task_documents` 结构校验，review prompt 模板无等价物。
- 影响：skill 改名、脚本搬家、目录调整这类重构会持续静悄悄地使 prompt 内的操作指引失效（本次改名已发生一次）。
- 建议：加一条测试：提取 `docs/reviews/prompts/*.txt` 中全部反引号包裹、形如 `scripts/...` 或 `.agents/...` 的路径，断言在仓库内存在。
- 置信度：高
- 优先级：中（与 H1 同修）

## 改进建议

1. 文档一致性测试：除 M6 的路径断言外，可对 AGENTS.md「`task.py` 使用示例」代码块中的子命令与 `task.py` argparse 注册表做交叉断言（示例块目前与实现一致，但无守护）。
2. task-dispatch「停止条件」与 AGENTS「合并环节四种停止」措辞可统一引用：AGENTS 已声明合并环节四种，task-dispatch 额外列了执行期两种（密钥输入、主仓脏改动），当前靠读者自行归并，建议 task-dispatch 明示「前四条对应 AGENTS 合并环节，后两条属执行环节」。
3. `docs_repo/decision_log.md` 落点列多处写 `scripts/task.py`（如 L1/L5/L21/L22/L23），是历史路径。decision_log 是历史总账可不回改，但若团队以其为准查现状会踩空，可在表头加一句「落点路径为落地时快照，现状以仓库为准」。

## 不确定项

1. `test_task_start_flow.py:50-51` 的 `_valid_spec()` 补丁注明「omni_media 本地补丁」用于消化本仓库 spec 模板多出的占位符——但本仓库即模板仓，注释提到的 omni_media 与本仓关系不明，疑似从业务仓回拷测试时带入的注释。不影响测试正确性（227 全绿），但注释指向的外部上下文在模板仓不成立，建议核实并改写注释来源。
2. 串行链式下 `start --base` 要求 base 分支无登记 worktree（`resolve_start_base:1352-1357`）。task-run 队列循环在每个 task commit 后即 cleanup-worktree，正常流程满足；若用户在链中间手动保留了某 worktree，后续 task 的 start 会被拒，task-run 恢复节未覆盖该情形，行为是「报错由用户处置」，可接受，未实测。
3. `cmd_view` 对 active task 的 conflicts 展示（1148-1156 行）混入 backlog peer，仅作信息展示不影响调度计算，未逐场景验证展示完整性。
