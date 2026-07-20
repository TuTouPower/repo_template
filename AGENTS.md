# {项目名}

{一句话介绍：这个项目是什么、给谁用。}

本文件是 agent 行为入口，包含工作流规则与按需导航。只读取当前任务需要的文档，禁止无目的全量加载。

## 目录与读写规则

| 路径 | 用途 | 读取规则 | 写入规则 |
| ---- | ---- | -------- | -------- |
| `docs/specs_index.md` | 当前生效 spec 清单（在表即生效） | 追溯已固化需求时 | 每个 task **收尾（step 7）**写入/更新对应行（须已过黑盒）；废弃时删除行 |
| `docs/specs/<slug>.md` | 需求级 spec（按已完成 task 累积） | 追溯需求实现与验收时 | 每个 task **收尾（step 7）**写入或累积更新（须已过黑盒）；废弃时移入 `docs/archive/specs/` |
| `docs/tasks_index.md` | task ID、状态、owner、branch | 接到新需求或状态流转时 | 新需求和状态流转时更新 |
| `docs/tasks/{TID}_slug/` | task 工作区（含开发中 spec；backlog 起即存在） | 执行或审阅 task 时 | owner 写 `spec.md` `plan.md` `task.md`；reviewer 写 `review_code.md` `review_test.md`（不改 `task.md`） |
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

- 一个**需求**拆成 N 个 **task**。任务 ID **一律大写** `{TID}`（如 `T001`），目录 `docs/tasks/{TID}_{slug}/`，分支 `task_{TID}_{slug}`（如 `task_T001_foo`），finding `{TID}_code_f001`。一个 **task** = 一个 **commit**。需求过大就拆细 task，不在 task 内拆 commit。
- **循环执行所有 task**，每个 task 走一遍「单 task 流程」。
- `tasks_index` 状态：`backlog` / `active` / `done` / `dropped`。有遗留时备注 `done_with_exception` 及 finding ID。

### 新需求拆分与创建 task

1. 读 `docs/tasks_index.md` 全部行（含 backlog），取最大 ID 加一分配 `{TID}`。需求拆分时一次分配多个 ID。
  - 单个 task 必须结果独立可验证，有工程意义。
  - 需求过大就拆细 task，不在 task 内拆 commit。
2. 循环每个 task，为每个 task 一次性完成：
  - 登记 `docs/tasks_index.md`（标 `backlog`）；
  - 创建 `docs/tasks/{TID}_slug/`；
  - 从 `docs/templates/task/` 复制并 **填写** `spec.md`（验收标准非空）、`plan.md`、`task.md`（front matter 填 `tid`/`slug`/`status: backlog`；`diff_anchor` 可先占位，step 1 再写实值）。

### 单 task 流程

一个 task 产出一个 commit。过程总账在 **`task.md`**（YAML front matter + 正文：过程记录 / Review 处置 / 收尾报告）。review 默认最多 2 轮：

```
step 1–4  分支 / 红绿 / 黑盒
    ↓
step 5    Round 1 双轴 review
    ├─ 两路均 PASS ──→ step 7 收尾 → step 8 commit
    └─ FAIL → step 6 处置（写入 task.md）→ Round 2 → …
```

**verdict: PASS**：本轮 0 finding，且前轮均已修或已撤回。完整判定见 `docs/templates/prompts/share_review_prompt.md`。

**exception**：`task.md` Review 处置中有 `遗留` 仍可 `done`；不改写 `review_*` 的 verdict/finding；在 `task.md` 收尾报告写清并口头报告。

步骤：

1. 创建并切换分支 `task_{TID}_{slug}`；校验当前分支与 `tasks_index.branch` 一致。登记 active + owner + branch。在 `task.md` front matter 写入实值 `diff_anchor`（当前 HEAD）、`branch`、`owner`、`status: active`。校验 `spec.md` 验收标准非空。
2. 可测试部分先写红（`{test_cmd}`）。
3. 实现变绿（`{test_cmd}`）；可派 sub agent。
4. 黑盒（`{blackbox_cmd}`）。通过后进入 review / 收尾。
5. review Round 1：派 code reviewer 与 test reviewer 并行。
    - 生成 prompt（从 `task.md` front matter 读 tid/slug/diff_anchor，无需手填）：
      ```bash
      scripts/render_review_prompts.sh \
        --task-dir docs/tasks/{TID}_{slug} \
        --out-dir .scratch/review_prompts
      ```
    - 两份全文分别作 code / test reviewer 的 prompt；报告写到 `review_code.md` / `review_test.md`。
    - 两路 PASS → 跳过 step 6 进 step 7；任一路 FAIL → step 6。
    - 默认 finding 必须处置；误报走 step 6 争议。
6. owner 处置 + 修复：
    - 读两份 review，在 `task.md` 的 **Review 处置** 追加 `### Round N (...)` 表（或注明零 finding）。
    - status：`已修` / `遗留` / `撤回`（撤回须原 reviewer 在 `review_*` 追加记录）。
    - 触代码或测试 → step 3 → step 4。
    - 文档笔误 → 继续；文档事实类 → 进后续 Round（改 spec/AGENTS/blueprint/AC → 两路；仅实现 → code；仅测试 → test）。
    - 处置完 → Round 2；PASS → step 7；FAIL → 未修项标完后进 step 7（默认可不再 Round 3）。
7. 收尾：
    - 更新 `docs/specs/<slug>.md` 与 `docs/specs_index.md`（须已过黑盒）。
    - 更新 blueprint/guides/README 等受影响文档。
    - 写全 `task.md` **收尾报告**（验收勾选、verdict、exception）；front matter `status: done`。
    - `tasks_index` → `done`；有遗留则备注 `done_with_exception` + finding ID。
    - 后置 task 受影响则改其 spec/plan。
    - 目录移入 `docs/archive/tasks/`。
    - 有遗留则口头逐条说明。
8. 一个 commit 含本 task 全部改动。subject 含 `{TID}`。只在 `task_{TID}_{slug}` 上工作。

### review target

- review 用 `git diff <diff_anchor>`（相对工作区；`diff_anchor` 以 `task.md` front matter 为准）。
- 不用 `git diff <diff_anchor>...HEAD` 作唯一证据源。
- 同步主线时更新 front matter 的 `diff_anchor`，并在「过程记录」记一笔。

### dropped

- task 级放弃：
    - backlog：`tasks_index` → `dropped`，备注原因；目录**一律**进 `docs/archive/tasks/`。
    - active：在 `task.md` 过程记录写终止原因；front matter `status: dropped`；不把半成品合入默认分支；目录进 archive。若已写过 `docs/specs/`，撤销本 task 对 specs 的增量。
- 需求级废弃（已固化的 spec 被替代或停用）：
    - 把 `docs/specs/<slug>.md` 移入 `docs/archive/specs/<slug>.md`。
    - 从 `docs/specs_index.md` 删除对应行。
    - 新替代需求的 spec 引用旧 slug；`specs_index` 备注可记 `supersedes: <old_slug>`（不修改 archive 内旧文件）。
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
