# 代码与文档审阅报告 (repository)

## 本路模型标识
Antigravity gemini-3.6-flash-high

## 模块 slug
repository

## 审阅范围
审阅 `/home/karon/karson_ubuntu/repo_template` 仓库全量 tracked 文件，包括：
- `scripts/repo_template/task.py` (task 状态机权威入口、worktree 管理、调度与合并)
- `scripts/repo_template/pending.py` (待办事项生命周期管理)
- `scripts/repo_template/findings.py` (技术发现条目管理)
- `scripts/repo_template/spikes.py` (Spike 探针实验管理)
- `scripts/repo_template/_id_scan.py` (跨 worktree/分支的互斥取号器)
- `scripts/repo_template/check_review_status.py` (Review 报告与处置判定)
- `scripts/repo_template/render_review_prompts.py` (Review Prompt 渲染器)
- `.claude/hooks/merge_guard.py` (Merge 拦截钩子)
- `AGENTS.md` (Agent 架构规则、读写权属与角色边界)
- `.agents/skills/` 下全量调度与工作流 skills (`task-dispatch`, `task-run`, `task-integrate`, `task-work`, `task-schedule` 等)
- `tests/repo_template/` 下全量测试套件

重点验证：状态机一致性、worker/coordinator 写域、worktree 生命周期、完成即合并、并发补位、串行调度、依赖/冲突计算、失败与 blocked 路径、index 重建、测试覆盖、文档与脚本一致性。

---

## 高优先级

### 1. `check_review_status.py` 中的 `FINDING_RE` 正则表达式无法匹配 `single` 审核级别下的 `general` 前缀
- **位置**: `scripts/repo_template/check_review_status.py#L33`
- **现象**: `FINDING_RE` 定义为 `re.compile(r"^(t[0-9]+)_(?:code|test|gen)_f[0-9]+$")`。但在 `single` 审查级别 (`review_level=single`) 下，对应报告文件为 `review_general.md`，Reviewer 在处置表中常习惯填写 `t001_general_f001` 作为 `finding_id`。由于正则只匹配 `code`、`test`、`gen`，遇到 `general` 会判定为非法 `finding_id` 并抛出 `ReviewDataError`。
- **影响**: 当任务配置为 `review_level=single` 且 Reviewer 按规范写入 `t001_general_f001` 时，`check_review_status.py` 报错中断，阻断 Step 6 处置判定流程。
- **建议**: 修改 `FINDING_RE` 增加对 `general` 前缀的匹配：`re.compile(r"^(t[0-9]+)_(?:code|test|gen|general)_f[0-9]+$")`。
- **置信度**: 高
- **优先级**: 高

### 2. `task.py` `cmd_view` 在串行链式 (task-run) 推进过程中将未合并但已完成的 `done` 节点判定为未解依赖，导致调度全景图误报依赖阻塞
- **位置**: `scripts/repo_template/task.py#L1071-L1132`
- **现象**: `cmd_view` 计算调度图时，区分了 `main_done_set`（已真正合入主干的 done）和 `effective_done_set`（含未合并分支的 done）。计算依赖解冻时强制使用 `done_set = main_done_set`。在并行扇出模式 (`task-dispatch`) 下“完成即合并”，前置完成立刻入主干，逻辑正常；但在串行链式模式 (`task-run`) 下，全链任务逐个在各自分支 tip 标记为 `done`，待整条链完成后才统一调用 `integrate --chain` 合入主干。在此期间若运行 `task.py view`，已被链上标记为 `done` 的前置任务不在 `main_done_set` 中，导致下游节点在全景图中被误判为“被依赖阻塞” (`waiting_deps`) 或“被 active 冲突阻塞” (`blocked_conflicts`)。
- **影响**: 破坏了串行链式模式下 `task.py view` 可视化全景图的准确性，可能误导 Agent 或用户认为下游任务处于阻塞状态而中断流程。
- **建议**: 调度图中应识别串行链上 Ancestor 分支已处于 `done` 状态的拓扑，在判定依赖解冻时允许认领同一链上合法的分支 `done` 状态，或在 `view` 视图中明确区分“链上已完成待合并”与“真正未完成前置”。
- **置信度**: 高
- **优先级**: 高

---

## 中低优先级

### 3. `task.py` `_resolve_chain` 按分支名字典序收集串行链分支，导致链分支清理与逻辑链顺序可能不一致
- **位置**: `scripts/repo_template/task.py#L2048-L2072`
- **现象**: `_resolve_chain` 使用 `for branch in _local_task_branches():` 遍历所有任务分支，利用 `merge-base --is-ancestor` 筛选链尾的祖先分支。由于 `_local_task_branches()` 是按分支名字符串升序排列，如果串行链的执行顺序与分支名天然字典序不一致（如非递增编号或包含不同 slug），`chain` 列表存放的顺序是字符排序而非拓扑深度顺序。
- **影响**: 尽管 `integrate --chain` 的合并与分支删除使用 `git branch -d` 在主干 HEAD 被推移后能正常通过，但输出日志播报的分支链继承顺序不符合实际提交拓扑，增加了排查维护成本。
- **建议**: 改用 `git rev-list` 或解析 commit parent 拓扑排序填充 `chain` 数组，保证顺序与 Git 提交拓扑（Ancestral Order）一致。
- **置信度**: 中
- **优先级**: 中

### 4. `task.py` `cmd_edit` 在修改 `conflicts_with` 时若关联 peer 任务处于 `active` / `blocked` 状态抛出硬阻断，缺乏引流提示
- **位置**: `scripts/repo_template/task.py#L1670-L1674`
- **现象**: 执行 `task.py edit` 修改冲突边时，脚本会同步校验并更新 peer 任务的反向 `conflicts_with` 边。若 peer 任务正处于 `active` 或 `blocked` 状态（在自己的 worktree 中），`cmd_edit` 会抛出异常 `无法维护冲突反向边：tXXX status=active，须为可编辑 backlog` 并直接退出。
- **影响**: 用户或 Coordinator 在主仓重构调度冲突图时，若已有相关任务被 `start` 激活，操作会被硬阻断，且错误提示没有明确告知正确的解决路径。
- **建议**: 优化错误提示信息，明确指明“请到该 active/blocked 任务自身的 worktree 中修改 `task.md` 反向边，或先通过 `rewind` 将该任务退回 backlog”。
- **置信度**: 高
- **优先级**: 低

### 5. `render_review_prompts.py` 过于严格地在初始化阶段要求 4 个 Prompt 模板必须全量存在
- **位置**: `scripts/repo_template/render_review_prompts.py#L180-L189`
- **现象**: 脚本在开头对 `template_paths` 中的 `code`, `test`, `general`, `share` 统一做文件存在性检查 (`is_file()`)。如果某个轻量级项目仓库选择只配置 `general_prompt.txt` 和 `share_prompt.txt` 并仅使用 `review_level=single`，脚本依然会因为缺失 `code_prompt.txt` 或 `test_prompt.txt` 而直接报错退出。
- **影响**: 降低了审查模板配置的灵活性，要求项目必须保留全套 4 个模板文件。
- **建议**: 调整为根据当前任务的 `review_level` 惰性校验所需模板文件（即 `single` 仅校验 `general` 与 `share`；`full` 仅校验 `code`、`test` 与 `share`）。
- **置信度**: 高
- **优先级**: 低

---

## 改进建议

### 6. `_id_scan.py` 在跨 Worktree 扫描未提交文件时增加对不可读软链和非文本杂物的过滤
- **位置**: `scripts/repo_template/_id_scan.py#L122-L137`
- **现象**: `_numbers_from_worktree` 使用 `rglob("*.md")` 或 `iterdir()` 检索工作区文件。如果某个 worktree 中存在未追踪的死软链或非标准命名文件，可能会抛出 OS 异常或误判编号。
- **建议**: 在遍历时增加 `path.is_file()` 和软链可达性校验，提高跨 worktree 取号锁的健壮性。
- **置信度**: 中
- **优先级**: 低

### 7. `task.py` `link_local_env` 增加对深层 Monorepo 子包 `.env` 软链的支持
- **位置**: `scripts/repo_template/task.py#L1277-L1291`
- **现象**: `link_local_env` 仅扫描主仓根目录及一级子目录下的 `.env` 文件。对于嵌套层级较深的项目结构，深层子包中的 `.env` 无法被自动软链至新建的 task worktree。
- **建议**: 允许通过配置或限定层级的递归查找识别 `.env` 文件并建立软链。
- **置信度**: 中
- **优先级**: 低

---

## 不确定项

### 8. `task.py` `cmd_start` 传入 `--base` 时对上一节点 `diff_anchor` 溯源一致性的校验边界
- **位置**: `scripts/repo_template/task.py#L1321-L1360`
- **现象**: 在串行链式模式下，下一个任务 `start --base prev_branch` 会继承 `prev_branch` 的 tip 作为基准，并将 `diff_anchor` 记录为 `prev_branch` 的 HEAD SHA。当随后运行 Reviewer Prompt 渲染时，`contract_drift_notice` 会对比 `diff_anchor` 处的契约区与当前契约区。如果 `prev_branch` 本身修改了通用契约区文件，Reviewer Prompt 会展示从 `prev_branch` 继承而来的 drift 警告。
- **不确定点**: 需明确该 drift 警告是否完全符合预期（即 Reviewer 只需审核本 Task 相对于上一 Task 分支 tip 的变更），还是在特定情况下需要追踪至主干 HEAD。
- **建议**: 在 `docs/reviews/prompts/` 的说明文档中增加串行链式模式下 `diff_anchor` 基准的明确约定。
- **置信度**: 中
- **优先级**: 低
