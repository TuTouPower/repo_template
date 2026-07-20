# {项目名}

{一句话介绍：这个项目是什么、给谁用。}

本文件是 agent 行为入口，包含工作流规则与按需导航。只读取当前任务需要的文档，禁止无目的全量加载。

## 目录与读写规则

| 路径 | 用途 | 读取规则 | 写入规则 |
| ---- | ---- | -------- | -------- |
| `docs/specs_index.md` | 当前生效 spec 清单（在表即生效） | 追溯已固化需求时 | **task 期间不写**；需求全部 task done 后由「需求完结」首次写入；废弃时删除行 |
| `docs/specs/<slug>.md` | 已固化 spec：全部 task done 后的实现与验收 | 追溯已固化需求时按需 | **task 期间不写**；仅「需求完结」写入一次；废弃时移入 `docs/archive/specs/` |
| `docs/tasks_index.md` | task ID、状态、owner、branch | 接到新需求或状态流转时 | 新需求和状态流转时更新 |
| `docs/tasks/TNNN_slug/` | task 工作区（含开发中 spec；backlog 起即存在） | 执行或审阅 task 时 | `spec.md` `plan.md` `log.md` `task_report.md` 由 owner 写；`adoption.md` 仅进入 adoption 时由 owner 写；`review_code.md` `review_test.md` 由 reviewer 写，reviewer 对他人报告只读 |
| `docs/handoff.md` | 项目级交接 | 接手工作时第一个读 | 只追加，不删改历史 |
| `docs/blueprint/` | 当前长期真相：架构、领域、约定、决策 | 修改跨模块行为前读 `architecture.md`；写代码或文档前读 `conventions.md`；接触新业务概念时读 `domain.md`；理解历史取舍时读 `decisions.md` | finalization 阶段更新；实施和 review 期间不写入未稳定结论 |
| `docs/reviews/review_<TS>/` | 独立 review：多模型报告 + adoption 决策 | 审阅全代码 / diff / 指定范围时 | 由 `/multi-model-review` 和 `/multi-model-adoption` skill 生成；本地无独立 review 模板；落地拆 task |
| `docs/spikes/SNN_slug/` | 当前 spike | 技术选型或未知风险验证时 | `report.md` 必需；有实验代码时再建 `code/` |
| `docs/templates/` | task / task review+adoption / spike 模板 | 创建对应工作项时复制 | 复制使用，不代表 active 数据 |
| `docs/guides/` | 给人看的使用指南 | 按需 | 不承载 agent 行为规则 |
| `docs/archive/` | 完结或终止的 spec、task、review、spike 等 | 追溯历史时 | 镜像原路径，只进不出；内部文件只准新增，不准修改 |
| `schemas/` | 跨服务接口契约（OpenAPI / proto / GraphQL） | 实现或消费服务前 | 改契约走 task 流程；类型落点见 `docs/blueprint/conventions.md` |
| `config/` | 配置文件（默认 + 环境覆盖 + `.env.example`） | 部署、调试、新增服务时 | 真值不入库，`.env` 由 `.env.example` 复制填写 |
| `src/` `tests/` `scripts/` `assets/` | 源码、测试、脚本、静态源 | 正常开发 | 正常开发 |
| `artifacts/` `data/` `.scratch/` | 产物、运行数据、一次性草稿 | — | 不入库；临时日志放 `.scratch/` |

## 开发原则

- specs driven：所有开发都要先拆分需求为 task，并为所有 task **填写** spec 和 plan（非空验收标准）；后置 task 的 spec/plan 随前置 task 完成而修订。
- TDD：开发循环内可测试部分先写失败测试（红），再实现到通过（绿）。
- 长期真相延后：未稳定方案留在 task；长工作需中途形成稳定长期真相时拆独立 task，在该 task 完结时更新 blueprint。

## 开发工作流

### 总览

**需求 / task / commit**

- 一个**需求**拆成 N 个 **task**（`{TID}` 如 T001，独立分支 `task_tnnn_slug`，独立可验证结果），一个 **task** = 一个 **commit**。需求过大就拆细 task，不在 task 内拆 commit。
- **循环执行所有 task**，每个 task 走一遍「单 task 流程」；全部 task done 后走「需求完结」。
- `tasks_index` 状态：`backlog` / `active` / `done` / `dropped`。备注栏可记 `done_with_exception` 及批准信息（见 step 7）。

### 新需求拆分与创建 task

1. 读 `docs/tasks_index.md` 全部行（含 backlog），取最大 ID 加一分配 `{TID}`。需求拆分时一次分配多个 ID。
  - 单个 task 必须结果独立可验证，有工程意义。
  - 需求过大就拆细 task，不在 task 内拆 commit。
2. 循环每个 task，为每个 task 一次性完成：
  - 登记 `docs/tasks_index.md`（标 `backlog`）；
  - 创建 `docs/tasks/{TID}_slug/`；
  - 从 `docs/templates/task/` 复制并 **填写** `spec.md`（背景/范围/验收标准，**验收标准非空**）、`plan.md`（主要步骤）、`log.md`（可先空记录区）。
  - 禁止以未填验收标准的空模板进入后续 active。

### 单 task 流程

一个 task 产出一个 commit。状态机摘要：

```
R1 PASS（0 finding）          → 收尾 step 7
R1 FAIL                       → adoption step 6 → R2
R2 PASS（前轮全修且无新 finding）→ 收尾 step 7
R2 FAIL                       → blocked（等用户；不自动 Round 3）
用户批准 exception / 降级     → 可收尾，reviewer verdict 不改写
```

**PASS 判定**（与 `docs/blueprint/conventions.md` 一致）：`PASS ⟺ 本轮 finding 数 = 0 ∧（无前轮 ∨ 前轮 finding 全部已修或已撤回）`。

步骤：

1. 创建并切换分支 `task_tnnn_slug`（`git checkout -b` 或切换已有分支）；校验 `git branch --show-current` 与 `tasks_index.branch` 一致。登记 `docs/tasks_index.md`（标 `active`，填 owner 和 branch）。记录 `diff_anchor`（当前 HEAD SHA）到 `docs/tasks/{TID}_slug/log.md`（标题下首行 `diff_anchor: <SHA>`），作为 review 相对基线。校验 `spec.md` 验收标准非空，否则不得继续 step 2。
2. 可测试部分先写红（运行 `{test_cmd}` 看失败）。
3. 实现变绿（运行 `{test_cmd}` 看通过），任务量不大由自己完成，任务量大可派 sub agent。
4. agent-verify 黑盒验证：运行 `{blackbox_cmd}`。
5. review Round 1：派两个 sub agent 并行评审 **相对 `diff_anchor` 的 working tree 与 index**（见下「review target」），各自对照 task spec 出 finding 清单。
    - 代码 agent：从 `docs/templates/task/review_prompt_code.md` 整体注入提示词（替换 `{TID}`/`{slug}`/`{spec_path}`/`{task_dir}`/`{diff_anchor}`），独立成报告到 `docs/tasks/{TID}_slug/review_code.md`，finding 用 `{TID}_code_fNNN` 编号。
    - 测试 agent：从 `docs/templates/task/review_prompt_test.md` 整体注入提示词，独立成报告到 `docs/tasks/{TID}_slug/review_test.md`，finding 用 `{TID}_test_fNNN` 编号。
    - 共享规则（两 agent 都遵守）：read-only、不信任 implementer 自述、Pre-Report Gate、重审追加（报告结构以对应 `review_prompt_*.md` 输出格式为准，`review.md` 仅空骨架参考；Round 2 在文件末尾追加 `## Round 2 (YYYY-MM-DD HH:MM UTC+8)` 小节，不覆盖；finding ID 跨轮全局续编）。
    - 末行 `verdict: PASS`（0 finding，**跳过 step 6 直接进 step 7**）/ `verdict: FAIL`（有 finding，进 step 6）。
    - 严重度三级定义见 `docs/blueprint/conventions.md`（本文件不重复完整示例）。**默认所有 finding 必须处置**；误报走 step 6 争议路径。
6. owner adoption + 修复：
    - 读 `review_code.md` 和 `review_test.md`，逐条处置。写 `docs/tasks/{TID}_slug/adoption.md`（文件不存在从 `docs/templates/task/adoption.md` 复制；已存在则末尾追加 `## Round N (...)` 小节，禁止覆盖）。
    - **status**：
        - `已修`：本 task 内修复；
        - `遗留`：实现层无法在本 task 解决（需拆新 task），`rationale` 含无法当场修的依据 + 后续 task 计划；task 不得 done 直到用户显式批准（见 exception）；
        - `撤回`：误报经 **受控争议** 关闭——owner 提交证据 → **原 reviewer** 在对应 review 报告追加撤回记录；撤回不算忽略；仅 reviewer 撤回或用户明确裁决后可不改代码。
    - 触代码或测试 → 回 step 3 重新跑 `{test_cmd}`，再回 step 4 黑盒验证。
    - 文档改动分类：
        - **笔误类**（纯排版、标点、不改语义的错别字）→ 直接继续；
        - **事实类**（涉及语义：名词、路径、函数名、参数、版本、状态）→ 必须进入后续 Round，按改动范围过滤审查轴——改 spec / AGENTS.md / blueprint / 验收标准 → 两路都审；仅实现 → 仅 `review_code`；仅测试 → 仅 `review_test`。
    - **不设独立「局部重审」术语**；范围过滤的重审即 Round 的一部分，计入轮次上限。
    - 处置完 → 回 step 5 触发 **Round 2**（重审）：两 reviewer 复核前轮 finding 是否真修/已撤回 + 扫新 finding。
        - `verdict: PASS` → 进 step 7；
        - `verdict: FAIL` → **blocked**，不得自动再开 Round 3；在 `task_report.md` 记 blocked 原因，等用户决策（允许额外轮次 / 拆 task / 重写 / 显式降级）。用户批准额外轮次时显式覆盖 2 轮上限，报告与 adoption 继续追加 `## Round N`。
7. 收尾（前置满足其一即可）：
    - 两路 reviewer **最新一轮**均为 `verdict: PASS`（Round 1 两轴均 0 finding 时无 Round 2、无 adoption 文件）；或
    - 用户显式批准 exception（遗留保留 / 降级）：**不改写** reviewer 报告中的 `verdict: FAIL` 与 finding 正文；在 `tasks_index` 备注栏记 `done_with_exception`、批准人/时间/finding ID；`task_report` 分栏写「reviewer 最终 verdict」与「用户处置」。
    - 更新本次 task 受影响文档：`docs/blueprint/`（含 `decisions.md` 的非显然决策）、`docs/guides/`、`README.md`、API 文档等。**不要**在本 step 写 `docs/specs/` 或 `docs/specs_index.md`（见「需求完结」）。
    - 更新 `docs/tasks/{TID}_slug/log.md`：追加本 task 进展、决策与关键验证。
    - 写 `docs/tasks/{TID}_slug/task_report.md`（从模板复制）：对照 spec 验收标准逐条勾选；adoption 处置摘要；**Round 1 verdict** 与 **Round 2 verdict（N/A | PASS | FAIL）**；遗留/exception（若有）。不记 commit SHA，本报告所在 commit 即 task commit，SHA 由 `git log --grep {TID}` 查。
    - 更新 `docs/tasks_index.md`：本 task 状态改 `done`（exception 时备注如上）。
    - 若后置 task 存在，查看本 task 结果是否修订后置 task，若影响则修订后置 task 的 `spec.md` / `plan.md`。
    - 归档：将 `docs/tasks/{TID}_slug/` 移入 `docs/archive/tasks/`。
8. commit：本 task 所有改动（代码、测试、文档、log、adoption、task_report、index 更新、归档移动）作为一个 commit。commit subject 必须含 task ID（如 `feat(T001_slug): ...`），保证 `git log --grep {TID}` 可追溯。合并进默认分支由外部流程负责；task 内必须在正确 `task_tnnn_slug` 分支工作，不把半成品直接提交到默认分支。

### review target

- 审查对象（commit 前、无中间 commit 时仍非空）：`git diff <diff_anchor>`（将该 commit 与 **当前工作区** 对比，覆盖已暂存与未暂存的本 task 改动）。
- **禁止**把 `git diff <diff_anchor>...HEAD` 当作 review 唯一证据源（单 commit 且 commit 在 step 8 时，三点 diff 常为空，会导致虚假 0 finding）。
- task 期间禁止 silent rebase 主线而不更新 `diff_anchor`；若必须同步主线，重置 `diff_anchor` 并在 `log.md` 记录。

### 需求完结

当某一需求下 **全部** 相关 task 均为 `done`（或经用户批准的 `dropped` 已处理）后，owner 执行一次需求级固化（可单独短 task，或挂在该需求最后一个 task 的 step 7 **之后**单独步骤，但不得在非末 task 写入）：

1. 将实现与验收固化为 `docs/specs/<slug>.md`；
2. 在 `docs/specs_index.md` 写入一行（slug、task 清单、固化日期）；
3. 若该需求替代旧需求：新 spec 正文引用旧 slug；`specs_index` 备注可记 `supersedes: <old_slug>`（**不修改** `docs/archive/` 内旧文件）。

### dropped

- task 级放弃：
    - backlog 被放弃：tasks_index 改为 `dropped`，备注原因；若目录仅含未实质填写的模板，**可不归档**、删除工作区目录即可；若已有实质内容，目录移入 `docs/archive/tasks/`。
    - active 被放弃：在 `docs/tasks/{TID}_slug/log.md` 记录终止原因；确保不把半成品合入默认分支；将目录移入 `docs/archive/tasks/`；tasks_index 改为 `dropped`。task 期间不碰 `docs/specs/`，无增量需撤销。
- 需求级废弃（已固化的 spec 被替代或停用）：
    - 把 `docs/specs/<slug>.md` 移入 `docs/archive/specs/<slug>.md`。
    - 从 `docs/specs_index.md` 删除对应行。
    - 新替代需求的 spec 引用旧 slug（见「需求完结」）。
    - 不动 `docs/archive/tasks/` 历史归档。
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
- `{test_cmd}`：日常测试命令（单测 / 集成 / 单文件），复制模板时填写；TDD 红/绿循环（step 2、3）调用。命令多（分层测试、E2E、CI 复现）时改写为指向 `docs/guides/testing.md` 的链接。
- `{blackbox_cmd}`：项目黑盒验证命令，复制模板时填写；单 task 流程 step 4 调用。
- 测试规范（命名、层级、回归规则）见 `docs/blueprint/conventions.md`「编码与测试」小节，不在此重复。
