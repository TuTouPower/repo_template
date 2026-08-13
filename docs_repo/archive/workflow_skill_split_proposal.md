# AGENTS.md 与 task skills 拆分方案

## 状态

本文仅说明候选方案，不代表当前工作流已变更。

当前仍以 `AGENTS.md` 为完整行为入口；仓库尚未创建本文所述 skills，也未修改 task 创建、执行、review、归档或提交方式。

## 目标

将当前 `AGENTS.md` 中两类内容分开：

- 始终生效的项目规则、状态机和硬约束继续留在 `AGENTS.md`。
- 仅在创建或执行 task 时需要的操作步骤移入按需加载的 skill。

建议提供两个项目级 skill：

- `/task_create`：把新需求拆成一个或多个合格 backlog task。
- `/task_run <tid>`：从仓库当前状态恢复并持续执行已有 task，直到完成或遇到必须停止的条件。

核心原则：

> `AGENTS.md` 定义必须遵守什么，skill 定义具体怎么做。

## 不解决的问题

本方案不把 skill 当作程序级状态机或强制执行机制。

- skill 是按需加载的流程指令，不是后台任务或持久循环。
- skill 不能保证每一步一定执行，也不能替代 `scripts/task.py` 的状态检查。
- `AGENTS.md` 和 skill 都属于 agent 上下文。需要确定性阻止的操作仍应由脚本校验或 hook 完成。
- `/task_run` 所称“循环执行”指在一次 task 生命周期内按状态持续推进，不表示定时轮询、后台常驻或脱离会话自动运行。

## 建议目录

```text
AGENTS.md
.claude/
└── skills/
    ├── task_create/
    │   └── SKILL.md
    └── task_run/
        └── SKILL.md
```

`CLAUDE.md` 继续指向 `AGENTS.md`，不在两处维护重复规则。

## AGENTS.md 职责

`AGENTS.md` 保留任何阶段都必须知道的规则。

### 仓库结构与权责

保留：

- task、spec、review、spike、archive 等目录用途。
- 各类文件的读取和写入责任。
- `docs/tasks_index.json`、`docs/archive/tasks_index.json` 只能由 `scripts/task.py` 修改。
- `docs/archive/tasks_audit.log` 只能由 `rewind` / `purge` 追加。

### 命名和状态模型

保留：

- `{tid}`、`{sid}`、`{slug}` 命名。
- task 状态：`backlog` / `active` / `blocked` / `done` / `dropped`。
- 合法状态转换。
- `rewind`、`purge`、`drop` 的适用边界。
- `max_verify_round`、`max_review_round` 默认值。

### 全局开发原则

保留：

- specs-driven。
- 行为 AC 只在 `spec.md` 定义。
- TDD 红绿要求。
- 测试必须触达生产逻辑。
- 固定两个独立 reviewer 并行完成代码轴和测试轴。
- critical / important 阻断，minor 必须处置但不阻断。
- 跨 task 系统性缺口只建立一个 follow-up task。
- 一个 task 对应一个 commit。

### 停止和安全规则

保留：

- 黑盒或 review 达到门禁条件后的 blocked 规则。
- blocked 后必须停止并请求用户选择。
- 不直接修改受脚本管理的索引和审计文件。
- 不破坏用户已有工作区改动。
- secret、平台、外部发布等项目级限制。

### skill 路由

增加简短入口说明：

```markdown
## Task 工作流入口

- 新需求尚无 task：调用 `/task_create`。
- 已有 tid 的实施、续作或收尾：调用 `/task_run <tid>`。
- task 状态、门禁、目录权责和硬约束以本文件为准；skill 只定义操作步骤。
```

`AGENTS.md` 可保留一张精简状态图，帮助未加载 skill 时理解全局流程；不再复制每一步命令和检查清单。

## `/task_create` 职责

### 输入

- 用户需求。
- 可选标题、slug、依赖关系或拆分偏好。

### 操作范围

1. 运行 `scripts/task.py list`，检查现有活跃和归档 task。
2. 判断是否已有等价 task，避免重复创建。
3. 将需求拆成独立可验证的 task；过大时继续拆分。
4. 对每个 task 运行 `scripts/task.py add`。
5. 创建 `docs/tasks/{tid}_{slug}/`。
6. 从模板复制并填写：
    - `spec.md`
    - `plan.md`
    - `task.md`
7. 检查行为 AC 非空、范围明确、依赖和验证方式可执行。
8. 汇报新建 tid、依赖和待执行顺序。

### 结束状态

`/task_create` 结束时 task 保持 `backlog`：

```text
task 已创建，spec/plan/task.md 已填写，尚未 start。
```

### 明确不负责

- 不创建或切换开发分支。
- 不运行 `scripts/task.py start`。
- 不编写实现或测试。
- 不执行黑盒或 review。
- 不 finish、归档或提交。

这条边界支持“只创建 task，暂不开干”。

## `/task_run <tid>` 职责

### 输入

- 已存在的 tid。
- 用户对执行范围的额外限制，例如“只做到 review”或“不要提交”。

### 首先恢复状态

每次调用都从仓库事实恢复，不依赖上一轮对话记忆：

1. 运行 `scripts/task.py show <tid>`。
2. 读取 task 目录中的 `spec.md`、`plan.md`、`task.md` 和已有 review 报告。
3. 检查当前分支、Git 工作区、`diff_anchor`、已有测试和验证记录。
4. 判断 task 当前应从哪个步骤继续。

示例：

- `backlog`：从 start 和分支准备继续。
- `active` 且尚无红灯证据：从 TDD 红灯继续。
- 黑盒已通过但没有 review：直接进入双 reviewer。
- 已有 FAIL 报告且未满轮：从 finding 处置继续。
- 已处于 `blocked`：停止自动推进，向用户呈现允许的选择。
- 已 `done` 或 `dropped`：不重复执行。

### 执行范围

`/task_run` 按 `AGENTS.md` 状态机推进：

01. start、分支和 `diff_anchor` 校验。
02. doctor 前置检查或必要 spike。
03. TDD 红灯。
04. 实现和绿灯。
05. 黑盒验证循环。
06. 固定派两个独立 reviewer 并行完成：
    - code review
    - test review
07. finding 处置。
08. 必要的修复、测试、黑盒和下一 review 轮。
09. 最终轮新 blocker 的同轮复核。
10. spec、blueprint、指南和 `task.md` 收尾。
11. `scripts/task.py finish`。
12. task 目录归档。
13. task commit。

### 停止条件

只有下列情况停止：

- task 已完成。
- task 达到 blocked 条件。
- 需要用户作不可替代的需求或风险决策。
- 环境、权限或外部依赖阻止继续。
- 用户明确限制本次执行终点。

### 循环语义

skill 内的循环必须以文件和命令结果为依据：

```text
读取状态 → 执行当前步骤 → 验证结果 → 更新记录 → 重新判断状态
```

禁止仅依赖“刚才做到哪一步”的会话记忆。这样即使会话中断、上下文压缩或稍后重新调用，也能从仓库状态继续。

## 权威来源划分

|内容|权威位置|
|---|---|
|目录用途和写入责任|`AGENTS.md`|
|task 状态和门禁语义|`AGENTS.md`|
|hard constraints 和安全规则|`AGENTS.md`|
|task 文档字段格式|`docs/blueprint/conventions.md`|
|创建 task 的操作顺序|`/task_create`|
|执行和恢复 task 的操作顺序|`/task_run`|
|task 索引实际状态|`scripts/task.py` 管理的 JSON|
|review 实际结论|`review_code.md` / `review_test.md`|
|task 过程和处置|`task.md`|

同一规则只在一个权威位置完整定义。其他位置引用稳定标题，不复制正文。

## 与当前方案对比

|维度|当前全部写在 `AGENTS.md`|拆分后的混合方案|
|---|---|---|
|初始上下文|每次加载完整操作流程|始终加载核心规则，按需加载操作步骤|
|全局状态机|集中完整|继续集中在 `AGENTS.md`|
|操作入口|需要从长文判断|`/task_create`、`/task_run` 明确|
|创建与执行边界|位于同一长流程|两个 skill 各自负责|
|中途恢复|依赖 agent 从全文判断|`/task_run` 明确定义状态恢复|
|维护风险|单文件较长|多文件可能漂移，需要权威边界|
|自动执行能力|无程序级保证|skill 同样无程序级保证|
|强制力|上下文规则|上下文规则；关键限制仍靠脚本或 hook|

## 预期收益

- `AGENTS.md` 从操作手册收敛为项目规则和状态机。
- 创建和执行 task 的入口清晰。
- 用户可以只创建 task，不触发实现。
- 已有 task 可以通过 tid 稳定续跑。
- 创建流程和执行流程可以分别演进。
- 未执行 task 工作流时，不必加载完整操作清单。

当前 `AGENTS.md` 规模不大，拆分带来的主要收益是职责清晰和恢复可靠，不是显著节省 token。

## 风险与控制

### skill 漏调用

控制方式：`AGENTS.md` 明确路由；skill description 写清适用场景。需要确定行为时由用户显式调用 slash command。

### 多处规则漂移

控制方式：状态、门禁、硬约束只在 `AGENTS.md` 定义；skill 只保留操作步骤。

### `/task_run` 假装自动循环

控制方式：每次步骤转换都重新读取仓库状态；遇到 blocked、权限或用户决策立即停止。

### skill 被误认为安全边界

控制方式：直接编辑 task JSON、审计日志等关键禁区继续由脚本约束；需要确定性阻止时再增加 hook。

## 未来实施顺序

若后续批准执行，建议按以下顺序实施：

1. 创建 `/task_create`，先迁移“新需求拆分与创建 task”。
2. 创建 `/task_run`，迁移 Step 1 至 Step 8 的操作清单和状态恢复逻辑。
3. 精简 `AGENTS.md`，保留规则、状态机、门禁、blocked 和 skill 路由。
4. 搜索并清理重复规则和矛盾表述。
5. 用临时 task 验证：
    - 只创建不执行。
    - backlog task 从 start 执行。
    - active task 中途恢复。
    - review FAIL 后续跑。
    - blocked 时停止。
6. 确认旧的 `scripts/task.py`、review prompt 和归档结构保持兼容。

## 实施判定标准

未来拆分完成需同时满足：

- 未调用 skill 时，agent 仍能从 `AGENTS.md` 理解状态、门禁和禁区。
- `/task_create` 不进入 active 或修改实现。
- `/task_run` 能从 backlog、active、review 中途和 blocked 状态正确恢复。
- 两个 skill 不复制 `AGENTS.md` 中的完整规则。
- 固定两个独立 reviewer 的要求未弱化。
- task JSON、审计 log、归档和 commit 规则未改变。
