{一句话介绍：这个项目是什么、给谁用。} 这是代码仓库的仓库模板，用户创建代码库时复制这个作为起点。

本文件是 agent 行为入口：目录权责、状态机、门禁、硬约束与 skill 路由。只加载当前任务所需文档。

**操作步骤**见 skill（正文在 `.agents/skills/`；`.claude/skills/*` 为软链）。本文件不展开逐步清单。

### skill 触发（强制）

下列全部 skill（路由表见「Task 工作流入口」）：

- **允许**：用户主动斜杠（`/tasks-run` 等）；或**另一个已获合法调用的 skill** 在正文中明确要求接着执行某 skill（链式调用）。
- **禁止**：模型仅凭对话语义、goal 模糊匹配、「推进项目/有 backlog」等自行加载或执行上述 skill。
- 各 skill frontmatter 设 `disable-model-invocation: true`（宿主支持时生效）；未支持该字段的宿主仍须遵守本条。
- 用户未触发时：不创建/执行/整理 task 流程；需要时可提示对应斜杠，不得擅自开跑。

非 Claude 宿主：直接读 `.agents/skills/<name>/SKILL.md`。规则冲突时以本文件为准。

## 命名（本文件内只在此定义）

- `{tid}`：task 编号，形如 `t001`、`t042`（小写 `t` + 数字）。
- `{sid}`：spike 编号，形如 `s001`、`s003`（小写 `s` + 数字）。
- `{slug}`：小写 `snake_case`。

## 目录与读写规则

| 路径                                         | 用途                                      | 读取规则                                                                                                                          | 写入规则                                                                                     |
| -------------------------------------------- | ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `docs/specs_index.md`                      | 当前生效 spec 清单（在表即生效）          | 追溯已固化需求时                                                                                                                  | task**收尾**时更新；废弃时删除行                                                       |
| `docs/specs/<slug>.md`                     | 需求级 spec（按已完成 task 累积）         | 追溯需求实现与验收时                                                                                                              | task**收尾**时累积更新；废弃时移入 `docs/archive/specs/`                             |
| `docs/tasks_index.json`                    | 活跃 task：tid、状态、branch              | 接到新需求或状态流转时                                                                                                            | **只能通过 `scripts/task.py` 操作**；禁止直接编辑                                    |
| `docs/archive/tasks_index.json`            | 归档 task（`done` / `dropped`）       | 追溯历史 tid 时                                                                                                                   | `scripts/task.py finish` / `drop` 自动移入；禁止直接编辑                                 |
| `docs/archive/tasks_audit.log`             | rewind/purge 审计（append-only）       | 追溯状态撤回与误建删除时                                                                                                          | `scripts/task.py rewind` / `purge` 独占 append；禁止编辑或截断                          |
| `docs/tasks/{tid}_{slug}/`                 | task 工作区（backlog 起即存在）           | 执行或审阅 task 时                                                                                                                | 实现侧：`spec.md` `plan.md` `task.md`；reviewer：`review_code.md` `review_test.md`；`finish`/`drop` 时由脚本移入 `docs/archive/tasks/` |
| `docs/archive/tasks/{tid}_{slug}/`         | 已归档 task 工作区                        | 追溯历史 task 文档时                                                                                                              | 仅由 `scripts/task.py finish` / `drop` 从 `docs/tasks/` 移入；内部文件只准新增        |
| `docs/handoff.md`                          | 项目级交接（仅最新）                      | 接手工作时第一个读                                                                                                                | 只保留当前有效一节；过时段落由 `repo-hygiene` 迁入 `docs/archive/handoff.md`           |
| `docs/bugs.md`                             | 已知**未修** bug                          | 追溯未修 bug 时                                                                                                                   | 发现不立即修复的 bug 追加新条目；`tasks-run` 收尾写 `修复：tXXX` 并整条迁 `docs/archive/bugs.md`；漏迁由 `repo-hygiene` 补迁 |
| `docs/archive/handoff.md`                  | 历史交接                                  | 追溯旧交接时                                                                                                                      | 只追加；禁止截断或改写已归档段落                                                         |
| `docs/archive/bugs.md`                     | 已修 bug 历史                             | 追溯已修 bug 时                                                                                                                   | 只追加；禁止截断或改写已归档条目                                                         |
| `docs/blueprint/`                          | 当前长期真相：架构、领域、约定、决策      | 改跨模块行为前读`architecture.md`；写代码或文档前读 `conventions.md`；新业务概念读 `domain.md`；历史取舍读 `decisions.md` | finalization 时更新；实施与 review 期间仅写入已稳定结论                                      |
| `docs/reviews/review_<TS>/`                | 独立 review：多模型报告 + adoption 决策   | 审阅全代码 / diff / 指定范围时                                                                                                    | 用户命令后生成                                                                               |
| `docs/spikes/{sid}_{slug}/`                | 当前 spike                                | 技术选型或未知风险验证时                                                                                                          | `report.md` 必需；有实验代码再建 `code/`                                                 |
| `docs/templates/`                          | task / spike 等模板                       | 创建工作项时复制                                                                                                                  | 复制使用，不代表 active 数据                                                                 |
| `.agents/skills/`                          | 项目 skill 正文                           | 创建或执行 task 时按路由加载                                                                                                      | 改 skill 走文档/模板纪律；不在此放业务代码                                               |
| `.claude/skills/`                          | 指向 `.agents/skills/` 的软链             | Claude Code 发现 skill                                                                                                            | 只维护软链，不复制正文                                                                     |
| `docs/guides/`                             | 给人看的使用指南                          | 按需                                                                                                                              | 给人读，不写入 agent 行为规则                                                                |
| `docs/archive/`                            | 完结或终止的历史                          | 追溯历史时                                                                                                                        | 镜像原路径；内部文件只准新增                                                                 |
| `schemas/`                                 | 跨服务接口契约                            | 实现或消费服务前                                                                                                                  | 改契约走 task 流程；类型落点见`docs/blueprint/conventions.md`                              |
| `config/`                                  | 配置（默认 + 环境覆盖 +`.env.example`） | 部署、调试、新增服务时                                                                                                            | 仅`.env.example` 入库；真值写本地 `.env`                                                 |
| `src/` `tests/` `scripts/` `assets/` | 源码、测试、脚本、静态源                  | 正常开发                                                                                                                          | 仅在 **task 执行期**（`tasks-run`）按 spec 修改；debug 复现不得写入                 |
| `artifacts/` `data/` `.scratch/`       | 产物、运行数据、一次性草稿                | —                                                                                                                                | 运行与草稿；debug 复现/实验代码**只许** `.scratch/`（已 gitignore）                  |

## 开发原则

- specs driven：先拆 task 并填写 spec/plan（行为验收标准须非空）；版本号、底层库选型、目录结构等不写进行为 AC。
- TDD：可测部分先红后绿；测试须触达生产逻辑。
- 双审：代码轴与测试轴固定由两个独立 reviewer 并行完成；critical / important 阻断，minor 须处置但不阻断。
- **未经用户明确允许，绝不准手动直接更改未被 gitignore 的代码文件**（含 `src/`、`tests/`、入库脚本等）。明确允许包括：用户点名授权改路径，或用户触发的实施类 skill（如 `tasks-run`）在其流程内按 spec 修改。已 gitignore 路径（如 `.scratch/`）不受本条限制。

## Task 工作流入口

仅在用户斜杠或其它 skill 链式调用时进入；**禁止**自行进入。

| 用户意图 | skill | 职责 |
|----------|-------|------|
| 待做 task 还缺我什么 | `tasks-preflight` | 只读汇总缺口 |
| 哪些 backlog task 能并发 | `tasks-parallel` | 只读；以进行中 task 与已存在分支为基线出并发分组 |
| 修 bug / 复现 / 根因立项 | `task-bug` | 复现/根因（仅 `.scratch/`）→ 建修复 task + 补测分析 → commit 创建物 |
| 新需求拆 task | `task-create` | 按**需求**拆建 backlog task；可一批 commit 创建物 |
| 捞遗留 / 技术债建 task | `task-debt` | 去重建 follow-up task |
| 多个 backlog task 合并成一个 | `tasks-merge` | 仅 backlog；并 spec/plan/task → `edit` 目标 → `drop` 源 |
| 串行跑完待做 task | `tasks-run` | **串行**执行；每 task 执行期一个 commit |
| 整理 handoff/bugs/过时文档 | `repo-hygiene` | 迁 archive；不手改 task JSON |
| 清理缓存/无用文件 | `repo-clean` | 默认 dry-run |

状态、门禁、目录权责、硬约束以**本文件**为准；skill 只定义操作步骤，不覆盖本文件。

## 命名与状态

- task 目录：`docs/tasks/{tid}_{slug}/`；分支：`{tid}_{slug}`。
- finding：`{tid}_code_fNNN` / `{tid}_test_fNNN`（本 task 内跨轮累计递增）。
- 门禁默认：`max_verify_round = 5`（黑盒）；`max_review_round = 4`（双审）。
- 状态：`backlog` / `active` / `blocked` / `done` / `dropped`。
- 状态撤回（审计写入 `docs/archive/tasks_audit.log`）：
  - `rewind`：`active->backlog`、`blocked->active`（或 `blocked->backlog`）；仅 active 文件；archive 不可 rewind。
  - `purge`：`backlog->deleted`（不进 archive）；仅从未开干（无 task 目录、无未合并 commit）的误建。有 commit 用 `drop`。

## commit 策略

- **创建期**（`task-create` 等创建类 skill）：一次需求拆出的多个 task 目录 + index **可以同一 commit**；创建 commit 不含生产实现；commit 前须先向用户列出创建物并获同意。
- **执行期**（`tasks-run`）：**一个 task 一个 commit**（该 task 从开干到 `finish` 的全部改动）；subject 含 `{tid}`。
- `blocked` 未放行前：不把该 task 当 done 提交。
- **维护期**（`tasks-merge` / `repo-hygiene` 等）：自成一个 commit，不与创建期、执行期 commit 混。
- **`repo-clean`**：默认不 commit。仅当清理产生**可跟踪** diff（如误提交的 `__pycache__`）且用户同意时再单独 commit；纯 gitignore 产物清理不 commit。

## debug

- 复现与探索性实验代码**只许**写在 `.scratch/`；**禁止**写入未被 gitignore 的路径（含 `src/` / `tests/` / `scripts/` 等）。
- 根因确认后**必须**经 task 修复；debug 阶段不做生产修复。
- 流程：`task-bug` 建修复 task（含根因与补测分析）→ **commit 创建物** → 用户再 `tasks-run` 实施修复。

## 执行门禁（规则）

单 task 逐步流程与示意图见 skill **`tasks-run`**。本文件只定门禁与 blocked：

- 黑盒轮次达 `max_verify_round` 仍失败，或双审 `overall=FAIL` 且 `round ≥ max_review_round` → **`blocked`**，停止自动推进。
- 禁止同一 review round 内临时修复后翻 PASS；须完整下一轮双审。
- `finish`：条目进 `docs/archive/tasks_index.json`，目录进 `docs/archive/tasks/`（由 `task.py` 完成）。

### blocked

| 触发                                                  | 动作                                                                                                                          |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| 黑盒轮次达`max_verify_round` 仍未通过               | 过程记录写明原因与轮次；`scripts/task.py block <tid> --reason blackbox`；`task.md` 过程记录同步说明；口头说明；停自动推进 |
| 双审`overall=FAIL` 且 `round ≥ max_review_round` | 处置表填完；`scripts/task.py block <tid> --reason review`；`task.md` 过程记录同步说明；口头说明；停自动推进               |

进入 blocked 后，agent **必须停下来向用户请求选择**（不得自行决定下一步，不得自动推进）：

- **加轮**：用户指定新 `max_verify_round` / `max_review_round` → `scripts/task.py resume <tid>`；过程记录写新上限；**计数累计不清零**。黑盒加轮从绿/黑盒续；双审加轮从双审续（见 `tasks-run`）。
- **dropped**：
  - backlog：`scripts/task.py drop <tid> --reason "..."`（JSON 与目录一并归档）；做一个提交。
  - active / blocked：过程记录写终止原因后 `drop`；半成品保留在 task 分支；**必须提交**（含 task 文档、JSON、归档移动、半成品），否则易丢。

## handoff

- 仅项目级；`docs/handoff.md` 只保留**最新**有效交接。
- 接手先读 `docs/handoff.md`；追溯旧交接读 `docs/archive/handoff.md`。
- 记录须含 branch 与交出时已有的 head_commit。
- 写新交接或 `repo-hygiene`：旧段落整段迁入 `docs/archive/handoff.md`（append），再写/替换 active 最新节。

## bugs

- `docs/bugs.md` 只保留**未修**条目。
- 新未修：追加。
- 修复闭环：`tasks-run` 收尾时写 `修复：{tid}` 并将**整条**迁入 `docs/archive/bugs.md`；漏迁时由 `repo-hygiene` 补迁。
- 已归档条目只追加、不改写；立即修完的 bug 走 task 流程，可不登记。

## spike

- 用于必须靠实验确认的事项：新 major、非标准 provider、协议兼容、平台差异、性能或工具行为；非默认必做。
- 建 `docs/spikes/{sid}_{slug}/`，复制 `docs/templates/spike/report.md`；`sid` 取 spikes 与 archive 中最大编号加一。
- 有实验代码再建 `code/`；可入库，仅作验证材料。
- 结论后移入 `docs/archive/spikes/`。
- **执行期** spike 在 `tasks-run` 中按 plan 进行；创建期只把 spike 需要写进 plan，不提前写生产代码。

## 硬约束

- `docs/tasks_index.json` 与 `docs/archive/tasks_index.json` **只能由 `scripts/task.py` 修改**。agent 禁止直接编辑这两个 JSON。脚本失败必须停下提示用户，禁止在未告知用户的情况下手工修 JSON。
- `docs/archive/tasks_audit.log` **只能由 `scripts/task.py rewind` / `purge` 以 append 模式写入**。agent 禁止编辑、截断或删除。rewind/purge 失败必须停下提示用户，禁止不告知用户就手工修审计 log。
- 未经用户明确允许，禁止直接改未被 gitignore 的代码文件（见「开发原则」）。
- debug / 探索代码不得写入未被 gitignore 的路径；生产改动只在用户授权的 task 执行期进行。
- {密钥规则、禁写路径、平台限制等项目特有约束，按需填写。}
- `{doctor_cmd}`：可选环境前置检查；只检查运行时、包管理器、命令、env key 存在性、服务健康与平台条件；不自动安装或升级，不做破坏性写入。
- `{test_cmd}`：日常测试（单测/集成/单文件）；**红** / **绿** 使用。命令多时改为指向 `docs/guides/testing.md`。
- `{blackbox_cmd}`：黑盒验证；**黑盒** 使用。
- 测试规范见 `docs/blueprint/conventions.md`「编码与测试」。

## 文档修改规范

- 结构或语义变化时，先确定最终表述，修改最小完整语义块，禁止逐句打补丁。
- 同一事实、规则或结论只保留一个权威定义；其他位置使用稳定标题或标识引用，避免复制正文和可能失效的编号引用。
- 文档正文直接陈述事实，禁止元引用：不嵌入决策/spike/ticket 编号（`(D24-N3)` `(S15)`）、来源或实现位置标注（括注如 `(根据 D24 决定)` `(D25 wrapper)` `(impl at ts/X.ts)`，叙述如"本节根据 X 决定 Y"）。
- 存在多种合理理解时，先澄清再做跨文档修改。
- 优先使用正向描述；仅安全、不可逆操作、明确禁区三类场景使用否定句。
- 完成后检查：旧表述、重复内容、矛盾结论、失效引用、遗漏同步、元引用残留。
