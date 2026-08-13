# plan：goal 模式控制面 + 自治运行加固

来源：参考项目 supergoal（github.com/robzilla1738/supergoal，本地 ~/github_repo/supergoal）的机制分析。本仓已有 script 控制面（ledger / exact identity / worktree 拓扑），不引入 supergoal 的 prompt-as-program 骨架，只移植其防自欺机制，并解决 goal 模式「提示词非强约束、频繁中断等输入」的实测痛点。

> **已落地（2026-08-12 核对）**：goal 模式已实现为 `task.py goal` / `goal-check`（见 AGENTS「skill 调用」goal 模式、`task-run`「goal 模式」节）。本文为方案推导，正文保留。

## 问题

用户以 `/goal 请你按照 task run 工作流执行所有 task` 驱动队列，三个结构性弱点：

1. 终态不可判定：「按工作流执行所有 task」是过程指令，goal evaluator 无客观依据反驳中途停止。
2. 「所有 task」未冻结：跑到一半 backlog 变化，指代不明。
3. 提示词不携带授权语义与停止边界，模型自我怀疑时倾向停下问。

## 设计总览

|#|机制|落点|来源|
|---|---|---|---|
|A1|`task.py goal`：冻结队列快照 + 打印 ready-to-paste `/goal` 行（含机器终态判定）|`repo_task/goal.py`|supergoal Stage 7|
|A2|`task.py goal-check`：只读判定器，ledger + worktree 登记为权威，输出 marker|`repo_task/goal.py`|supergoal transcript marker 的机器化替代|
|A3|task-run skill「goal 模式」节|`.agents/skills/task-run/SKILL.md`|—|
|B|baseline 健康预检：跑 `{doctor_cmd}`，红则标阻塞|task-preflight skill|supergoal Stage 6.5|
|C|`repo_state.py`：baseline→完整工作树取数（added-lines / deliverable / changed-files）；task-work Step 7 清洁度 grep|`scripts/repo_template/repo_state.py`|supergoal repo-state.sh|
|D|聚焦修复轮：verify/review 达 max-1 轮时先写聚焦修复计划再执行最后一轮|task-work skill|supergoal fix-spec|
|E|task-create 落盘自检三问：AC 可证伪 / task 原子性 / 最弱依赖|task-create skill|supergoal Stage 6a|
|F|review 报告 AC 复验披露：`re_verified` / `trust_prior` 分类 + 覆盖率行 + >30% 人工抽查提示|`docs/reviews/prompts/share_prompt.txt`|supergoal audit coverage|

## A. goal 模式控制面

### A1 `task.py goal [tids...]`

- 队列规则与 task-run 一致：无参数 = `backlog ∪ active`（effective 状态，tid 升序）；显式 tids = 严格按输入顺序，须 backlog/active。
- 冻结快照写 `docs/runtime/goal_queue.json`（已随 `docs/runtime/` gitignore，仅主仓）：`{"version": 1, "created_at": ISO8601, "queue": ["t001", ...]}`。覆盖式写入（单会话声明：goal 模式同时只服务一个队列；多会话并发各跑普通 task-run，不用 goal 生成器）。
- 打印 ready-to-paste `/goal` 行，终态条件直接写明验证方式：

```text
/goal 按 task-run skill 链式串行执行冻结队列 [t001, t002]（快照 docs/runtime/goal_queue.json，禁止变更队列成员）。队列执行授权已给出：禁止逐 task 征求确认、禁止进 plan mode；停止条件仅限 task-run skill「停止条件」列举项，task blocked 属合法停止，按 skill 汇报后停。整链完成后按 skill 询问一次合并授权。终态判定：运行 python3 scripts/repo_template/task.py goal-check——输出 GOAL_QUEUE_COMPLETE 或 GOAL_QUEUE_STOPPED 即本 goal 结束；GOAL_QUEUE_INCOMPLETE 表示继续。
```

### A2 `task.py goal-check`

只读、幂等。逐 tid 判定（权威 = ledger 投影 + worktree 登记，不看 transcript）：

|状态|判据|
|---|---|
|`integrated`|主干已 done 或 attempt state=integrated|
|`closed`|terminal completed + report done + `verify_integrate_ready` ready + worktree 未登记（exact cleanup 完成）|
|`cleanup_pending`|业务闭环但 worktree 仍登记|
|`running`|current attempt state=running|
|`pending`|ledger 无记录|
|`blocked`|report status=blocked|
|`failed`|terminal failed/stopped 或 report failed|
|`dropped`|主干已 dropped（快照过期，需重新 `task.py goal`）|

总结 marker 与退出码：

- 全部 `closed`/`integrated` → `GOAL_QUEUE_COMPLETE`，exit 0
- 任一 `blocked`/`failed`/`dropped` → `GOAL_QUEUE_STOPPED: <tid>=<state> ...`，exit 3（合法停止，goal 该结束）
- 其余 → `GOAL_QUEUE_INCOMPLETE: x/y closed`，exit 2
- 快照缺失/损坏/队列空 → 错误，exit 1

合并授权不在判定范围：merge 是 goal 结束后的人工步骤，同 task-run 现行语义。

### A3 task-run skill

新增「goal 模式」节：`task.py goal` 是 goal 模式唯一入口；手写 goal 提示词是反模式（终态不可判定）；goal 会话内行为 = 本 skill 队列循环原文；三个 marker 语义表。

## B. baseline 健康预检（task-preflight）

新增步骤 3（原 3/4 顺延为 4/5）：从 `docs/blueprint/testing.md` 读 `{doctor_cmd}`（写「无」则跳过），在主干跑一次。红 → 输出表加一行 `baseline`，标「阻塞」，请用户确认先修基线或明确「队列首 task 即修复基线」后放行。保持 skill 只读语义（doctor 类命令无副作用）。

## C. 工作树取数 + 清洁度兜底

`scripts/repo_template/repo_state.py`（Python 实现，跨平台，不引 bash 依赖）：

- `added-lines <baseline>`：`git diff <baseline>`（单 ref，含 committed+staged+unstaged+deleted）的 `+` 行 + `git ls-files --others --exclude-standard` 未跟踪文件全文；baseline 不可解析退化为存在性/全量并声明降级。
- `deliverable <baseline> <path>`：`present — <evidence>`（exit 0）/ `missing`（exit 1）。
- `changed-files <baseline>`：变更路径清单（tracked + untracked + deleted）。

task-work Step 7 收尾前加清洁度检查：对 added-lines（排除 `docs/`、`.scratch/` 路径）grep 本会话新增的 debug prints（按 stack，Python 仓 `print(`/`pprint(`）与 `\b(TODO|FIXME|XXX)\b`；非零 → 修掉或 spec 声明 `Cleanliness override:`（报告计数不判失败）。脚本只取数，判据写在 skill，随项目 stack 调整。

## D. 聚焦修复轮（task-work）

Step 4 / Step 6 达到 `max-1` 轮仍未过时，最后一轮前必须先在 `task.md` 实施笔记写「聚焦修复计划」：失败项、根因假设、最小修复动作、禁止 scope creep；再执行。满轮仍失败照旧 `block`。把「盲目重试」升级为「带假设的最后一击」。

## E. task-create 自检三问

第 5 步自检扩充为三问，结论随统一提交询问列出：

1. **AC 可证伪**：每条 AC 是 yes/no 可判；出现「可用/合理/正常/完好」类不可测词 → 就地改写。
2. **task 原子性**：标题含「和/与/并」或一个 task 两个独立验收面 → 拆。
3. **最弱依赖**：哪个 task 失败级联最多下游 → 评估是否降扇出或重排。

## F. review AC 复验披露（share_prompt.txt）

报告结论段新增强制小节「AC 复验方式」：逐条 AC 标 `re_verified`（reviewer 独立重跑命令/查证代码）或 `trust_prior`（无法独立复验，依赖实施侧证据），附覆盖率行 `coverage = re_verified / total`；`trust_prior` 占比 >30% 时标注「建议人工抽查后再合并」。不进 check_review_status.py 硬解析（保持现有轮次逻辑不动），为文本级约束。

## 实施顺序与验证

1. `repo_state.py` + `tests/repo_template/test_repo_state.py`（临时 git 仓验证三种子命令与降级路径）。
2. `repo_task/goal.py` + cli.py 接线 + task.py façade 导出 + `tests/repo_template/test_goal.py`（快照写入、队列计算、三种 marker 与退出码、错误路径）。
3. skill 文档四件（task-run / task-preflight / task-work / task-create）+ share_prompt.txt。
4. AGENTS.md（`docs/runtime/` 写权行补 goal_queue.json、使用示例补 goal 命令）+ `docs/blueprint/architecture_repo_template.md`（职责分工资节补 goal 模式段落）。
5. `python3 -m pytest tests -q` 全绿；`task.py goal --help` / `goal-check --help` 冒烟。

## 明确不做

- 不引入 transcript marker 协议（ledger 已是结构化权威）。
- 不改 attempt 生命周期事件类型、不加 ledger 事件。
- check_review_status.py 不新增解析规则。
- goal 快照不支持多会话并发（单会话声明；多会话仍走普通 task-run）。
- merge / integrate 授权语义不变。
