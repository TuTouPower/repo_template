# {project_name}

{一句话介绍：这个项目是什么、给谁用。}

本文件是 agent 行为入口，包含工作流规则与按需导航。只读取当前任务需要的文档，禁止无目的全量加载。

## 按需阅读

| 文档 | 内容 | 何时读 |
|------|------|--------|
| `docs/tasks/index.md` | task ID、状态、owner、branch | 接到新需求或流转状态时 |
| `docs/tasks/TNNN_slug/spec.md` `plan.md` | 范围与验收标准；步骤、风险、blueprint 更新清单 | 执行或审阅 task 时 |
| `docs/tasks/TNNN_slug/log.md` | 进展、偏离、决策和关键验证 | 接手或排查 task 时按需读 |
| `docs/tasks/TNNN_slug/reviews/` | task review 报告和 adoption | review 环节 |
| `docs/handoff.md` | 项目级交接 | 接手工作时第一个读 |
| `docs/blueprint/conventions.md` | 内容字段、命名、task/review 格式 | 写代码或文档前 |
| `docs/blueprint/architecture.md` | 当前模块划分、数据流、进程和 seam | 修改跨模块行为前 |
| `docs/blueprint/domain.md` | 当前领域概念、术语和命名 | 接触新业务概念时 |
| `docs/blueprint/decisions.md` | 已确认的非显然决策 | 需要理解历史取舍时 |
| `docs/reviews/RNN_slug/` | 当前独立 review | 评审非 task 对象时 |
| `docs/spikes/SNN_slug/report.md` | 实验问题、证据和结论 | 技术选型或未知风险验证时 |
| `docs/templates/` | task / review / spike 模板 | 创建对应工作项时复制 |
| `docs/guides/` | 给人看的使用指南 | 按需 |
| `docs/archive/` | 完结或终止的历史记录 | 追溯历史时 |

## 目录与写入规则

| 路径 | 用途 | 写入规则 |
|------|------|----------|
| `docs/blueprint/` | 当前长期真相：架构、领域、约定、决策 | finalization 阶段更新；实施和 review 期间不写入未稳定结论 |
| `docs/tasks/index.md` | task ID、状态、owner、branch | 新需求和状态流转时更新 |
| `docs/tasks/TNNN_slug/` | active task 工作区 | owner 写 task 文档；reviewer 只写自己的 review 报告 |
| `docs/reviews/RNN_slug/` | 非 task 对象的独立评审 | 作者管理 adoption；reviewer 只写自己的报告 |
| `docs/spikes/SNN_slug/` | 当前 spike | `report.md` 必需；有实验代码时再建 `code/` |
| `docs/templates/` | task、review、spike 模板 | 复制使用，不代表 active 数据 |
| `docs/guides/` | 给人看的使用指南 | 不承载 agent 行为规则 |
| `docs/handoff.md` | 项目级交接 | 只追加，不删改历史 |
| `docs/archive/` | 完结或终止的 task、review、spike | 镜像原路径，只进不出 |
| `src/` `tests/` `scripts/` `assets/` | 源码、测试、脚本、静态源 | 正常开发 |
| `artifacts/` `data/` `.scratch/` | 产物、运行数据、一次性草稿 | 不入库；临时日志放 `.scratch/` |

## 开发原则

- specs driven：spec 和 plan 先行，一起写完交用户一次性审核；用户明确不审则跳过。
- TDD：开发循环内可测试部分先写失败测试（红），再实现到通过（绿）。

## task 生命周期

状态只使用：`backlog`、`active`、`done`、`dropped`。

### 新需求

1. 读 `docs/tasks/index.md`，按 tasks 与 archive 中最大 ID 加一分配 TNNN。
2. 暂不开始：登记为 `backlog`，不建目录。
3. 开始执行：登记为 `active`，填写 owner 和 branch，创建 `docs/tasks/TNNN_slug/`。
4. 从 `docs/templates/task/` 创建 `spec.md`、`plan.md`、`log.md`。
5. spec 和 plan 按开发原则审核通过后，进入开发循环。

一个 task 对应一个独立、可验证的结果和一个工作分支，分支推荐 `task_tnnn_slug`。多个互不依赖的验收结果应拆成多个 task。

### 开发循环

一个 task 内部可分为多个 commit，每个循环产出一个独立 commit：

1. 可测试部分先写红。
2. 实现变绿。
3. review：派 sub agent 评审当前未提交改动。
4. 黑盒验证：运行项目黑盒测试命令，具体命令不在本文件规定。
5. 更新 task 文档（`log.md`、验收标准勾选等）。
6. 独立 commit：本循环的代码与文档更新作为一个内聚 commit。

实施时任务量不大由自己完成，任务量大可派 sub agent；review 一律派 sub agent 执行。不考虑多 agent 并行协作，只有自己按需派 sub agent 一种情况。

### 完结

所有开发循环完成、adoption 决策落地后，用一个收尾 commit 原子完成剩余文档更新：

1. 将 adoption 中已落地项从 `pending` 更新为此前存在的 `commit:<sha>`。
2. 更新受影响的 blueprint。
3. 非显然决策追加到 `docs/blueprint/decisions.md`。
4. 将任务目录移入 `docs/archive/tasks/`。
5. 将 index 状态改为 `done`。

### dropped

- backlog 被放弃：index 改为 `dropped`，备注原因；无目录可归档。
- active 被放弃：在 `log.md` 记录终止原因，确保半成品不留在目标分支，将目录移入 `docs/archive/tasks/`，index 改为 `dropped`。
- 恢复需求：新建新 ID，并在新旧任务备注中互相引用。

## review

- review 在开发循环内、commit 前进行，派 sub agent 评审当前未提交改动；每个循环创建新一轮报告，不改写旧报告。
- task review：在 task 下创建 `reviews/`，从 `docs/templates/review/` 复制模板。
- 独立 review：创建 `docs/reviews/RNN_slug/`，使用同一模板；RNN 取 `docs/reviews/` 与 `docs/archive/reviews/` 中最大 ID 加一。
- reviewer 对评审对象只读，只能创建自己的 review 报告；不得修改被评审对象、`adoption.md`、他人报告或历史记录。
- 作者填写 `adoption.md`：先记录 decision、rationale 和 `pending`，经用户审阅后再落地采纳项。
- 落地 commit 已存在后，finalization 阶段补写 `commit:<sha>`。禁止让 adoption 引用包含自身修改的 commit。
- 独立 review 完成后移入 `docs/archive/reviews/`；task review 随 task 归档。

## handoff

- 只有项目级交接，追加到 `docs/handoff.md`；不设 task 内交接。
- 交接者只追加新段落，不删改历史；接手者先读 `docs/handoff.md`。
- 交接记录必须包含 branch 和交出时已存在的 head_commit。

## spike

- spike 非必需，仅在技术选型或未知风险需要实验验证时创建。
- 创建 `docs/spikes/SNN_slug/`，从 `docs/templates/spike/` 复制 `report.md`；SNN 取 `docs/spikes/` 与 `docs/archive/spikes/` 中最大 ID 加一。
- 有实验代码时再创建 `code/`；代码入库保留，但不代表可用于生产。
- 结论被采纳后新建正式 task，在 `src/` 重新实现，不直接搬运实验代码。
- 得出结论并决定是否采纳后，将 spike 移入 `docs/archive/spikes/`。

## 硬约束

- {密钥规则、禁写路径、平台限制等项目特有约束，按需填写。}
