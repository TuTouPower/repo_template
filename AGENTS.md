# {project_name}

{一句话介绍：这个项目是什么、给谁用。}

本文件是 agent 行为入口。先读本文件，再通过 `docs/index.md` 按任务需要下钻；禁止无目的全量加载文档。

## 按需阅读

1. 接到新需求：读 `docs/tasks/index.md`。
2. 接手 active task：先读该 task 的 `handoff.md`，再读 `spec.md`、`plan.md` 和必要的 `log.md`。
3. 写代码或文档：读 `docs/blueprint/conventions.md`。
4. 跨模块修改：读 `docs/blueprint/architecture.md`。
5. 接触新业务概念：读 `docs/blueprint/domain.md`。
6. 需要理解历史取舍：读 `docs/blueprint/decisions.md`。

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
| `docs/handoff.md` | 跨 task 或项目级交接 | 只追加，不删改历史 |
| `docs/archive/` | 完结或终止的 task、review、spike | 镜像原路径，只进不出 |
| `src/` `tests/` `scripts/` `assets/` | 源码、测试、脚本、静态源 | 正常开发 |
| `artifacts/` `data/` `.scratch/` | 产物、运行数据、一次性草稿 | 不入库；临时日志放 `.scratch/` |

## task 生命周期

状态只使用：`backlog`、`active`、`done`、`dropped`。

### 新需求

1. 读 `docs/tasks/index.md`，按 tasks 与 archive 中最大 ID 加一分配 TNNN。
2. 暂不开始：登记为 `backlog`，不建目录。
3. 开始执行：登记为 `active`，填写 owner 和 branch，创建 `docs/tasks/TNNN_slug/`。
4. 从 `docs/templates/task/` 创建 `spec.md`、`plan.md`、`log.md`、`handoff.md`。
5. 顺序：spec → 用户审 → plan → 用户审 → 实施 → 验证。

一个 task 对应一个独立、可验证的结果和一个工作分支。允许多个内聚 commit；每个 commit 只承担一个意图。多个互不依赖的验收结果应拆成多个 task。

### 完结

review、adoption 和验证完成后，使用 finalization commit 原子完成：

1. 将 adoption 中已落地项从 `pending` 更新为此前存在的 `commit:<sha>`。
2. 更新受影响的 blueprint。
3. 非显然决策追加到 `docs/blueprint/decisions.md`。
4. 将任务目录移入 `docs/archive/tasks/`。
5. 将 index 状态改为 `done`。

### dropped

- backlog 被放弃：index 改为 `dropped`，备注原因；无目录可归档。
- active 被放弃：在 `log.md` 记录终止原因，确保半成品不留在目标分支，将目录移入 `docs/archive/tasks/`，index 改为 `dropped`。
- 恢复需求：新建新 ID，并在新旧任务备注中互相引用。

## 多 agent 协作

- `docs/tasks/index.md` 中 owner 和 branch 表示当前归属。
- 同一 task 同一时刻只有一个 owner。交接时先追加 task `handoff.md`，再更新 index 当前 owner。
- 一个 task 使用一个工作分支，推荐 `task_tnnn_slug`。
- reviewer 针对固定 `base_commit..head_commit` 快照评审。head 变化后创建新一轮报告，不改写旧报告。
- 交接记录必须包含 branch 和交出时已存在的 head_commit。

## review

- task review：在 task 下创建 `reviews/`，从 `docs/templates/review/` 复制模板。
- 独立 review：创建 `docs/reviews/RNN_slug/`，使用同一模板；RNN 取 `docs/reviews/` 与 `docs/archive/reviews/` 中最大 ID 加一。
- reviewer 对评审对象只读，只能创建自己的 review 报告；不得修改被评审对象、`adoption.md`、他人报告或历史记录。
- 作者填写 `adoption.md`：先记录 decision、rationale 和 `pending`，经用户审阅后再落地采纳项。
- 落地 commit 已存在后，finalization 阶段补写 `commit:<sha>`。禁止让 adoption 引用包含自身修改的 commit。
- 独立 review 完成后移入 `docs/archive/reviews/`；task review 随 task 归档。

## handoff

- task 内交接：追加到 task `handoff.md`。
- 跨 task 或项目级交接：追加到 `docs/handoff.md`。
- 交接者只追加新段落，不删改历史。接手者先读对应 handoff。

## spike

- 创建 `docs/spikes/SNN_slug/`，从 `docs/templates/spike/` 复制 `report.md`；SNN 取 `docs/spikes/` 与 `docs/archive/spikes/` 中最大 ID 加一。
- 有实验代码时再创建 `code/`；代码入库保留，但不代表可用于生产。
- 结论被采纳后新建正式 task，在 `src/` 重新实现，不直接搬运实验代码。
- 得出结论并决定是否采纳后，将 spike 移入 `docs/archive/spikes/`。

## 硬约束

- {密钥规则、禁写路径、平台限制等项目特有约束，按需填写。}
