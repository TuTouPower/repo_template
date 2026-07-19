{一句话介绍：这个项目是什么、给谁用。}

本文件是 agent 行为入口，包含工作流规则与按需导航。只读取当前任务需要的文档，禁止无目的全量加载。

## 按需阅读

| 文档                                         | 内容                                           | 何时读                   |
| -------------------------------------------- | ---------------------------------------------- | ------------------------ |
| `docs/specs_index.md`                      | 需求 slug、状态、task 进度                     | 追溯已验证的需求时       |
| `docs/specs/<slug>.md`                     | 需求已验证的实现与验收（累积）                 | 追溯需求时按需读         |
| `docs/tasks_index.md`                      | task ID、状态、owner、branch                   | 接到新需求或流转状态时   |
| `docs/tasks/TNNN_slug/spec.md` `plan.md` | 范围与验收标准；步骤、风险、blueprint 更新清单 | 执行或审阅 task 时       |
| `docs/tasks/TNNN_slug/log.md`              | 进展、偏离、决策和关键验证                     | 接手或排查 task 时按需读 |
| `docs/tasks/TNNN_slug/task_report.md`     | 处置摘要、遗留、commit 清单                   | task 完结后审阅          |
| `docs/tasks/TNNN_slug/review.md` `adoption.md` | review 报告 + adoption 处置清单            | review 环节              |
| `docs/handoff.md`                          | 项目级交接                                     | 接手工作时第一个读       |
| `docs/blueprint/conventions.md`            | 内容字段、命名、task/review 格式               | 写代码或文档前           |
| `docs/blueprint/architecture.md`           | 当前模块划分、数据流、进程和 seam              | 修改跨模块行为前         |
| `docs/blueprint/domain.md`                 | 当前领域概念、术语和命名                       | 接触新业务概念时         |
| `docs/blueprint/decisions.md`              | 已确认的非显然决策                             | 需要理解历史取舍时       |
| `docs/reviews/review_<TS>/`                | 独立 review（多模型报告 + adoption 决策）       | 审阅全代码 / diff / 指定范围时 |
| `docs/spikes/SNN_slug/report.md`           | 实验问题、证据和结论                           | 技术选型或未知风险验证时 |
| `docs/templates/`                          | task / task review+adoption / spike 模板       | 创建对应工作项时复制     |
| `docs/guides/`                             | 给人看的使用指南                               | 按需                     |
| `docs/archive/`                            | 完结或终止的历史记录                           | 追溯历史时               |

## 目录与写入规则

| 路径                                         | 用途                                 | 写入规则                                                  |
| -------------------------------------------- | ------------------------------------ | --------------------------------------------------------- |
| `docs/blueprint/`                          | 当前长期真相：架构、领域、约定、决策 | finalization 阶段更新；实施和 review 期间不写入未稳定结论 |
| `docs/specs_index.md`                      | 需求 slug、状态、task 进度           | task 黑盒验证通过后更新；全 task done 后状态改 done       |
| `docs/specs/<slug>.md`                     | 需求 spec：已验证的实现与验收（累积） | task 黑盒验证通过后累积；全 task done 后随归档            |
| `docs/tasks_index.md`                      | task ID、状态、owner、branch         | 新需求和状态流转时更新                                    |
| `docs/tasks/TNNN_slug/`                    | active task 工作区                   | owner 写 task 文档；reviewer 只写自己的 review 报告       |
| `docs/reviews/review_<TS>/`                | 独立 review：多模型报告 + adoption 决策 | 由 `/multi-model-review` 和 `/multi-model-adoption` skill 生成；落地拆 task |
| `docs/spikes/SNN_slug/`                    | 当前 spike                           | `report.md` 必需；有实验代码时再建 `code/`            |
| `docs/templates/`                          | task、task review+adoption、spike 模板 | 复制使用，不代表 active 数据                              |
| `docs/guides/`                             | 给人看的使用指南                     | 不承载 agent 行为规则                                     |
| `docs/handoff.md`                          | 项目级交接                           | 只追加，不删改历史                                        |
| `docs/archive/`                            | 完结或终止的 spec、task、review、spike | 镜像原路径，只进不出。绝不准修改内部文件，只能新增文件  |
| `src/` `tests/` `scripts/` `assets/` | 源码、测试、脚本、静态源             | 正常开发                                                  |
| `artifacts/` `data/` `.scratch/`       | 产物、运行数据、一次性草稿           | 不入库；临时日志放`.scratch/`                           |

## 开发原则

- specs driven：spec 和 plan 先行，一起写完交用户一次性审核；用户明确不审则跳过。
- TDD：开发循环内可测试部分先写失败测试（红），再实现到通过（绿）。

## 开发工作流

### 总览

**需求 / task / commit**

- 一个**需求**拆成 N 个 **task**（TNNN，独立分支 `task_tnnn_slug`，独立可验证结果）。需求过大就拆细 task，不在 task 内拆 commit。
- 一个 **task** = 一个 **commit**。
- **循环执行所有 task**，每个 task 走一遍"单 task 流程"。

**需求完整周期**

```
[新需求]
  → 拆 N 个 task，登记 `docs/tasks_index.md`
  → 循环每个 task：
      单 task 流程
  → 所有 task 完成，需求 spec 状态改 `done`
  → `docs/specs/<slug>.md` 移入 `docs/archive/specs/`
```

状态只使用：`backlog`、`active`、`done`、`dropped`。

### 新需求拆分与创建 task

1. 读 `docs/tasks_index.md`，按 tasks 与 archive 中最大 ID 加一分配 TNNN。需求拆分时一次分配多个 ID。
2. 暂不开始：登记为 `backlog`，不建目录。
3. 开始执行：登记为 `active`，填写 owner 和 branch，创建 `docs/tasks/TNNN_slug/`。
4. 从 `docs/templates/task/` 创建 `docs/tasks/TNNN_slug/spec.md`、`docs/tasks/TNNN_slug/plan.md`、`docs/tasks/TNNN_slug/log.md`。
5. 进入"单 task 流程"。

### 单 task 流程

一个 task 产出一个 commit，步骤：

1. 写 `docs/tasks/TNNN_slug/spec.md` + `docs/tasks/TNNN_slug/plan.md`。
2. 可测试部分先写红。
3. 实现变绿，任务量不大由自己完成，任务量大可派 sub agent。
4. agent-verify 黑盒验证：运行项目黑盒测试命令，具体命令不在本文件规定。
5. 更新受影响文档（仅本 task 黑盒验证已通过的部分）：`docs/blueprint`、`docs/guides`、`docs/blueprint/decisions.md`(非显然决策)、`docs/specs/<slug>.md`（累积本 task 已验证的实现与验收）、`docs/specs_index.md`（同步需求状态与 task 进度）、`README.md` 等；不含 `docs/tasks/` 进度记录。
6. review：派两个 sub agent 并行评审当前未提交改动，对照 task spec 判断代码、文档、测试是否仍满足最初需求。
  - owner 派两个 sub agent 并行评审，均对照 task spec 判断代码、文档、测试是否仍满足最初需求。
    - 文档+代码 agent：核对实现与 spec 是否一致、文档是否真实反映代码状态。
    - 测试 agent：核对测试覆盖与端到端行为是否对应 spec 验收标准。
  - 报告文件：`docs/tasks/TNNN_slug/review.md`，
    - 文件不存在的话从 `docs/templates/task/review.md` 复制 task review 模板；
    - 文件已存在则追加，禁止覆盖。
    - 两 agent 的评审内容写进同一份（owner 负责合并）。
  - 权限：reviewer 对评审对象只读；只能写自己的 review 内容，不得修改被评审代码、`adoption.md`、他人内容。
7. owner adoption: 读 `docs/tasks/TNNN_slug/review.md`，评估是否采纳审阅意见。
  - 报告文件：`docs/tasks/TNNN_slug/adoption.md`，
    - 文件不存在的话从 `docs/templates/task/adoption.md` 复制 task adoption 模板。
    - 文件存在则追加，禁止覆盖。
  - 采纳且能当场修的立即修复——触代码或测试回到步骤 4 重新黑盒验证，仅文档则直接继续；
  - 不采纳的只记 `rationale`；不能当场修的 `status` 标 `遗留-原因`。
8. 收尾
  - 更新 `docs/tasks/TNNN_slug/log.md`：追加本 task 进展、决策与关键验证，勾选验收标准。
  - 写 `docs/tasks/TNNN_slug/task_report.md`：对照 spec 验收标准逐条勾选；adoption 处置摘要（N 条采纳 / M 条不采纳，每条一行）；遗留问题（若有，注明原因）。
  - 更新 `docs/tasks_index.md`：本 task 状态改 `done`。
  - 归档：将 `docs/tasks/TNNN_slug/` 移入 `docs/archive/tasks/`。
9. commit：本 task 所有改动（代码、测试、文档、log、adoption、task_report、index 更新、归档移动）作为一个 commit。

### dropped

- backlog 被放弃：index 改为 `dropped`，备注原因；无目录可归档。
- active 被放弃：在 `docs/tasks/TNNN_slug/log.md` 记录终止原因，确保半成品不留在目标分支，将目录移入 `docs/archive/tasks/`，index 改为 `dropped`。
- 恢复需求：新建新 ID，并在新旧任务备注中互相引用。

## handoff

- 只有项目级交接，追加到 `docs/handoff.md`；不设 task 内交接。
- 交接者只追加新段落，不删改历史；接手者先读 `docs/handoff.md`。
- 交接记录必须包含 branch 和交出时已存在的 head_commit。

## spike

- spike 非必需，仅在技术选型或未知风险需要实验验证时创建。
- 创建 `docs/spikes/SNN_slug/`，从 `docs/templates/spike/` 复制 `report.md`；SNN 取 `docs/spikes/` 与 `docs/archive/spikes/` 中最大 ID 加一。
- 有实验代码时再创建 `docs/spikes/SNN_slug/code/`；代码可入库保留，仅作为验证材料。
- 得出结论并决定是否采纳后，将 spike 移入 `docs/archive/spikes/`。

## 硬约束

- {密钥规则、禁写路径、平台限制等项目特有约束，按需填写。}
