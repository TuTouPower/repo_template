# 代码与文档审阅报告 (repository)

## 本路模型标识

Grok grok-4.5

## 模块 slug

repository

## 审阅范围

全量审阅 `/home/karon/karson_ubuntu/repo_template` 当前 git tracked 文件（约 103 项）。重点：

- 状态机与写域：`scripts/repo_template/task.py`、`AGENTS.md` / `CLAUDE.md`
- 并行/串行工作流：`.agents/skills/task-dispatch`、`task-run`、`task-work`、`task-integrate`、`task-schedule`
- worktree 生命周期、完成即合并、并发补位、链尾合并、依赖/冲突/`view`
- 失败 / blocked / rewind / purge / index 重建
- 配套脚本：`pending.py`、`findings.py`、`spikes.py`、`_id_scan.py`、`check_review_status.py`、`render_review_prompts.py`、`.claude/hooks/merge_guard.py`
- 测试：`tests/repo_template/*`；模板与 prompt：`docs/tasks/task_template/`、`docs/reviews/prompts/`、`docs/blueprint/*`

未跑构建/测试；结论来自源码与文档交叉阅读。

---

## 高优先级

### 1. 串行链式 `--base` 在 skill 层断裂：`task-work` 固定从主干 start

- **位置**: `.agents/skills/task-work/SKILL.md` Step 1；`.agents/skills/task-run/SKILL.md`「链式拓扑 / 队列循环」；对照 `scripts/repo_template/task.py` `cmd_start` / `resolve_start_base`
- **现象**: `task-run` 规定链上后继须 `start --base <上一 task 分支>` 以继承成果；队列图写 `task-work（--base t001）`。但 `task-work` 正文规定：无 worktree 时在主仓执行 `task.py start <tid>`，并写明「基于当前主干 HEAD」。skill 不接受/不转发 `--base`。`task-dispatch` 由 coordinator 先 `start` 再派 worker（可规避）；串行会话若严格按 `task-work` 字面开干，后继 task 会扇出到当前主干而非链前缀。
- **影响**: 串行拓扑失去继承；链尾 `integrate --chain` 时祖先关系可能不成立或丢提交；与 `AGENTS.md`「串行=链式」权威描述冲突。属流程级正确性风险，非单测能拦住（脚本支持 `--base`，skill 未接线）。
- **建议**: 二选一写死并统一：**(A)** 串行由 `task-run`/coordinator 在调用 `task-work` 前完成 `start --base`，`task-work` 禁止自行 start（与 dispatch 对齐）；或 **(B)** `task-work` 增加可选 `--base`，串行必传。删除「恒从主干」的绝对表述。
- **置信度**: 高
- **优先级**: 高

### 2. `AGENTS.md` worktree 行写「恒基于当前主干 HEAD」，与串行 `--base` 矛盾

- **位置**: `AGENTS.md`（及软链 `CLAUDE.md`）目录表 `../{repo}_{tid}/` 行；同文件「串行=链式」段
- **现象**: 表称 start「恒基于当前主干 HEAD」；同文件后文与 `task.py` 明确串行从上一 task 分支建 worktree。
- **影响**: agent 读表优先时会忽略 `--base`，放大问题 1。
- **建议**: 改为「并行：主干 HEAD；串行：`--base` 上一已完成 task 分支（须先 cleanup-worktree）」。
- **置信度**: 高
- **优先级**: 高

### 3. skill 路由表把 `task-run` 写成「逐个执行并合并」，合并时机错误

- **位置**: `AGENTS.md` skill 调用表 `task-run` 行；对照 `task-run` / `task-integrate` 与 `integrate --chain`
- **现象**: 表写「并发度 1 的调度：逐个执行并合并」。实现与 skill 正文是：每 task 仅 `cleanup-worktree`，**全部完成后**一次性 `integrate --chain` 合链尾；并行才是「完成即合并」。
- **影响**: coordinator 可能在链中途对中间节点 `integrate`，破坏「主干只进一次 merge」约定，或与下游未完成 worktree 交错。
- **建议**: 改为「链式串行：逐个执行+cleanup；链尾一次 merge」。
- **置信度**: 高
- **优先级**: 高

### 4. `start` / `integrate` 不强制调度图与依赖门禁，并发安全完全靠 agent

- **位置**: `cmd_start`、`cmd_integrate`；对照 `cmd_view`、`task-dispatch`、`task-schedule`
- **现象**: `view` 正确用 `main_done_set` 解依赖、冲突与「下一批」择优；`start` 只校验 backlog + 文档门禁 + 分支/路径空闲，**不查** `schedule_status=scheduled`、`depends_on`、`conflicts_with` 与当前 active 集。`integrate` 只校验 tip 终态与 worktree 已清理。绕过 `view` 可同时 start 互斥 task，或未满足依赖就开干。
- **影响**: 并行补位若 agent 漏跑 `view` 或误读「待运行」全文（而非「下一批可跑」），可制造写域冲突与无意义三方 merge。脚本层无硬护栏。
- **建议**: 可选 `--enforce-schedule`（dispatch 默认开）：start 前要求 scheduled、依赖均在 `main_done_set`、conflicts peer 非 active/blocked/未合 main 的 done。integrate 可只做软警告。
- **置信度**: 高
- **优先级**: 高

### 5. `integrate --chain` 只校验链尾祖先，误合中间节点时不拦下游在飞 task

- **位置**: `scripts/repo_template/task.py` `_resolve_chain`、`cmd_integrate`；测试 `test_chain_integrate_rejects_mid_chain_undone` 仅覆盖「同 tip 未提交 active」巧合路径
- **现象**: 链收集条件为「分支 tip 是链尾祖先且未入主干」。下游 task 是**子孙**，不进入 chain。若链上 t001 已 done+cleanup、t002 已在 t001 之上产生独立 commit 且仍 active，对 t001 执行 `integrate` 或 `integrate --chain` 会成功把前缀并入主干，留下基于旧历史的下游 worktree。
- **影响**: 串行误操作或中途「先合一部分」导致历史分叉；恢复成本高。问题 3 的错误文案会抬高触发概率。
- **建议**: `--chain` 时额外扫描：是否存在以目标 tip 为祖先、尚未终态/未 cleanup 的 task 分支或 worktree；有则拒绝。文档写明「只允许对当前链尾 tid 调用」。
- **置信度**: 高
- **优先级**: 高

### 6. 生效 review prompt 仍引用已更名 skill `tasks-run`

- **位置**: `docs/reviews/prompts/share_prompt.txt`（`tasks-run`、`.agents/skills/tasks-run/SKILL.md`）；对照现网 `.agents/skills/task-run`、`task-work`
- **现象**: 共享 reviewer 指令指向不存在路径；`max_review_round` 与 Step 6 处置实际在 `task-work`。
- **影响**: 全量/single 审阅 subagent 按过时路径理解门禁与轮次，处置与 blocked 边界易偏。
- **建议**: 统一改为 `task-work`（轮次/处置）与必要时 `task-run`（队列）；删 `tasks-run` 字面。
- **置信度**: 高
- **优先级**: 高

---

## 中低优先级

### 7. 派生 index「仅 integrate 重建并 commit」表述不完整

- **位置**: `AGENTS.md` `docs/tasks_index.json` 写权行；`task.py` `rebuild_index` 调用点（`add`/`edit`/`rewind→backlog`/`purge`/`list --rebuild`/`_commit_index`）
- **现象**: 多条命令会**写工作区** index；仅 `integrate`（及手工 `list --rebuild` + 人工 commit）把 index **单独 commit**。`task-schedule` / `task-create` 要求维护 commit 带走 index，与表「仅 integrate」易打架。
- **影响**: agent 可能漏提交 edit 后的 index，或误以为 worktree 内不可/不应出现 index 脏文件。
- **建议**: 表改为「工作区可由 add/edit/… 重建；入库 commit：维护期随操作提交，合并后由 integrate 单独 chore commit；执行 commit 不带 index」。
- **置信度**: 高
- **优先级**: 中

### 8. `view` 依赖解锁仅认 main done，串行链中途全景「假阻塞」

- **位置**: `cmd_view` `done_set = main_done_set`；`task-run` 链上多 tip done、末合 main
- **现象**: 对并行「完成即合并」正确。串行链推进中，已 done 未合 main 的前置在 `view` 里仍阻塞 depends_on 下游，并可能把未合 main 的 done 算进冲突占用。
- **影响**: `task-run` 默认不靠 `view` 解锁（用户队列），直接危害低于 dispatch；但排障/混用 view 时误导「不能继续」。
- **建议**: 输出区分「main 可跑集」与「链上已完成未合入」；或 `--topology chain` 认祖先 tip done。
- **置信度**: 高
- **优先级**: 中（串行可视化）；对纯 dispatch 为低

### 9. front matter 解析三处副本，转义语义不一致

- **位置**: `task.py` `parse_front_matter_text`（完整 `_unquote`）；`check_review_status.py` / `render_review_prompts.py` 简化版（`strip('"')` 级）
- **现象**: 注释已要求三处同步；含转义引号/反斜杠的 title、note 时审阅脚本与状态机可能读到不同值。
- **影响**: 边界标题/note 下 review 渲染或 status 检查异常；维护成本高。
- **建议**: 抽公共模块，二脚本 import `task.parse_front_matter`。
- **置信度**: 高
- **优先级**: 中

### 10. `docs/blueprint/testing.md` 未给出可执行 `{doctor_cmd}` / `{test_cmd}` / `{blackbox_verify}`

- **位置**: `docs/blueprint/testing.md`；被 `task-work` Step 1–4、`task-integrate` 合并后验证引用
- **现象**: 模板只列类别与占位说明，无项目级命令。新复制项目未填则红/绿/黑盒/合并后验证无机械锚点。
- **影响**: 门禁 degenerates 为 agent 自拟；模板自测 `tests/repo_template` 与业务门禁脱节。
- **建议**: 模板至少填「本仓库：`pytest tests/repo_template`」类默认；Schema 段保持「无」。
- **置信度**: 高
- **优先级**: 中

### 11. merge 授权仅覆盖 Claude Code Bash hook；`task.py integrate` 子进程不经 token

- **位置**: `.claude/hooks/merge_guard.py`、`.claude/settings.json`；`cmd_integrate` 直接 `git merge`
- **现象**: 会话级授权写在 skill；脚本 merge 不走 merge-token。其他 agent 宿主无等价 PreToolUse。
- **影响**: 设计上可接受（integrate 即授权执行点），但「所有 merge 必须 token」与脚本路径不一致；非 Claude 环境零拦截。
- **建议**: 文档标明「skill 会话授权 + Claude Bash 双层；脚本 integrate 视为已授权入口」。可选 env `REPO_TEMPLATE_MERGE_OK` 供 CI。
- **置信度**: 高
- **优先级**: 低–中

### 12. `view` 冲突分组标签「被 active 冲突阻塞」语义过窄

- **位置**: `cmd_view` 输出；逻辑含 active/blocked、未合 main 的 done、序号更小的 backlog peer
- **现象**: backlog↔backlog 互斥与 unmerged done 也进同一分组标题。
- **影响**: 排障时误判 peer 一定在跑。
- **建议**: 拆标签或在行内标注 peer 状态。
- **置信度**: 高
- **优先级**: 低

### 13. `_resolve_chain` 列表顺序为分支名排序，非祖先拓扑序

- **位置**: `_resolve_chain` + `_local_task_branches`
- **现象**: 合并正确（只合链尾），打印/删除循环顺序未必是链顺序。
- **影响**: 日志可读性；删除仍用 `merge-base --is-ancestor` 守卫，功能风险低。
- **建议**: 按 `rev-list --ancestry-path` 或 commit 时间排序后输出。
- **置信度**: 高
- **优先级**: 低

### 14. `check_review_status` finding_id 仅 `gen`，与报告文件名 `review_general.md` 易混

- **位置**: `FINDING_RE`；`docs/reviews/prompts/general_prompt.txt`（正确要求 `{tid}_gen_fNNN`）
- **现象**: 规范前缀是 `gen`；若 reviewer 写 `general` 会 `ReviewDataError`。测试覆盖 `gen`。
- **影响**: 误填时 Step 6 中断；按 prompt 则无事。
- **建议**: 文档加粗对照表；或正则兼容 `general` 并规范化。
- **置信度**: 高
- **优先级**: 低

### 15. 调度纯函数测试过薄

- **位置**: `tests/repo_template/test_task_scheduling.py`（仅 `_dependency_cycle`）；大量 view/冲突场景在 `test_task_start_flow.py`
- **现象**: 无独立单测覆盖「下一批贪心」「backlog 序号优先」「main_done vs effective_done」纯函数抽取。
- **影响**: 回归依赖重型 git fixture；重构 view 成本高。
- **建议**: 抽出纯函数并补表驱动单测。
- **置信度**: 高
- **优先级**: 低

### 16. `share_prompt` / 部分文案仍写 `scripts/task.py`（缺 `repo_template/`）

- **位置**: `docs/reviews/prompts/share_prompt.txt`「系统性缺口去重」
- **现象**: 路径与仓库实际 `scripts/repo_template/task.py` 不符。
- **影响**: reviewer 只读探测 follow-up 时命令失败。
- **建议**: 改绝对相对路径。
- **置信度**: 高
- **优先级**: 低

### 17. `cmd_add` tid 分配只扫当前工作区，不扫未合并分支/其他 worktree

- **位置**: `cmd_add`；对照 `_id_scan.allocate` 的全局扫描
- **现象**: pending/findings/spikes 防并发撞号；task tid 仅 `scan_tasks()`。创建约定只在主仓，通常安全。
- **影响**: 极端：未合并分支已占更高 tid、主仓滞后时可能复号（需异常操作史）。
- **建议**: add 时并入 `scan_tasks_at_ref` / 分支枚举取 max；或文档钉死「只在主干创建且创建后立刻 commit」。
- **置信度**: 中
- **优先级**: 低

### 18. preflight 对「无关脏文件」仅 WARN

- **位置**: `cmd_preflight` foreign dirty
- **现象**: 不 FAIL；`task-work` 停止条件写「无法安全隔离」才停，依赖 agent 判断。
- **影响**: 脏主仓/混 worktree 下仍可能继续，污染执行 commit。
- **建议**: active 且 foreign tracked dirty 时升 FAIL，或 `--strict`。
- **置信度**: 中
- **优先级**: 低

---

## 改进建议

1. **统一拓扑入口清单**（一页）：并行 `view → start → work → integrate`；串行 `start[--base] → work → cleanup → … → integrate --chain`。每步唯一负责角色与命令，消除 task-work/task-run/AGENTS 三处漂移。
2. **`start` 可选调度硬门禁**（见问题 4），dispatch 默认开启，run 可关。
3. **index 写权**按「工作区重建 / 维护 commit / merge chore commit」三态改表（问题 7）。
4. **prompt 与 skill 更名扫尾**：`tasks-run` → `task-work`/`task-run`；`scripts/task.py` → `scripts/repo_template/task.py`。
5. **testing.md 模板默认值**：指向 `pytest tests/repo_template`，并注明业务项目必须覆盖改写。
6. **解析器单点化**；`view` 纯逻辑单测化。
7. **`docs_repo/`**：计划仍写 `next-batch`/旧 skill 名，README 已要求复制时排除；可在 `docs_repo/README.md` 置顶「历史设计，以 AGENTS + skills 为准」，降低误读（非功能缺陷）。
8. **正向肯定（保持）**：
   - worker/coordinator 写域在脚本层大体落实：`require_primary_worktree` / `require_own_task_worktree`；finish 不重建 index；integrate 后 index chore commit。
   - start 失败 `rollback_start` 归属校验谨慎；cleanup 校验终态+干净+分支归属。
   - pending/findings/spikes 公共目录锁 + 跨 worktree 扫号，适合并行 worker。
   - `view` 对环、dropped 引用、单向冲突无向化、backlog 序号优先有测试。
   - 并行完成顺序 integrate 与 `--chain` 链尾合并有真实 git 集成测。

---

## 不确定项

| 项 | 说明 |
|----|------|
| 多 host agent 并行 `edit` 主仓 | 无文件锁；依赖「主仓唯一 coordinator」纪律。未验证两进程同时 edit 的丢失更新。 |
| `integrate` 合并后验证失败的主干回滚策略 | skill 要求报告、不盲目回退；是否应用 `git reset` 由用户会话决定，仓库无标准 runbook。 |
| Grok/其他宿主是否加载 `.claude/hooks` | merge_guard 覆盖面依赖宿主；本审阅未验证非 Claude 环境。 |
| 超大 DAG / 上百 backlog 时 `discover_effective_tasks` 性能 | 每分支 `scan_tasks_at_ref`，未做压力数据。 |
| antigravity 报告称 `FINDING_RE` 不认 `general` | 与现行 `general_prompt`（`gen`）一致；是否算 bug 取决于是否要兼容误写，本报告作低优先级易混项（§14）。 |

---

## 结论摘要

状态机与 worktree/merge 脚本主体扎实，并行扇出与串行 `--chain` 在 `task.py` + 集成测上可验证。当前最大风险在 **skill/AGENTS 与脚本的串行接线**（`--base`、合并时机文案）以及 **调度图不进 start 硬门禁**、**review prompt 旧名**。优先修文档与 skill 接线，再考虑 `start --enforce-schedule` 与 `--chain` 下游在飞检测。
