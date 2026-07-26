{一句话介绍：这个项目是什么、给谁用。} 这是代码仓库的仓库模板，用户创建代码库时复制这个作为起点。

本文件是 agent 行为入口：目录权责、状态机、门禁、硬约束与 skill 路由。只加载当前任务所需文档。

**操作步骤**见 skill（正文在 `.agents/skills/`；`.claude/skills/*` 为软链）。本文件不展开逐步清单。

### skill 触发（强制）

`.agents/skills/` 下**全部** skill，含日后新增的（路由表见「Task 工作流入口」）：

- **允许**：用户主动斜杠（`/tasks-run` 等）；或**另一个已获合法调用的 skill** 在正文中明确要求接着执行某 skill（链式调用）。
- **禁止**：模型仅凭对话语义、goal 模糊匹配、「推进项目/有 backlog」等自行加载或执行任何 skill。
- frontmatter 固定两行：`description: none` 与 `disable-model-invocation: true`。`description` 留 none 是为了不给模型语义匹配的抓手；`disable-model-invocation` 在宿主支持时生效。新增 skill 照此办理。
- 用户未触发时：不创建/执行/整理 task 流程；需要时可提示对应斜杠，不得擅自开跑。

非 Claude 宿主：直接读 `.agents/skills/<name>/SKILL.md`。规则冲突时以本文件为准。

## 命名

- `{tid}`：task 编号，形如 `t001`、`t042`（小写 `t` + 数字）。
- `{sid}`：spike 编号，形如 `s001`、`s003`（小写 `s` + 数字）。
- `{slug}`：小写 `snake_case`。
- task 目录：`docs/tasks/{tid}_{slug}/`；分支：`{tid}_{slug}`；worktree：`../{repo}_{tid}`。
- finding：`{tid}_code_fNNN` / `{tid}_test_fNNN`（本 task 内跨轮累计递增）；每条标 `category`：`bug` / `spec_drift` / `duplicate` / `nitpick` / `coverage_gap`。

## 目录与读写规则

写入规则列只声明**谁有权写**；具体怎么写见对应 skill 或文件内注释。

| 路径 | 用途 | 写权归属 |
|------|------|----------|
| `docs/specs_index.md` | 当前生效 spec 清单（在表即生效） | task 收尾时更新；废弃删除行 |
| `docs/specs/<slug>.md` | 需求级 spec（按已完成 task 累积） | task 收尾时累积更新；废弃移入 `docs/archive/specs/` |
| `docs/tasks/{tid}_{slug}/` | task 工作区兼**状态权威**（backlog 起即存在） | `spec.md` / `task.md` 正文由实现侧写；`task.md` front matter 只经 `scripts/task.py`；reviewer 写 `review_code.md` / `review_test.md`；`finish`/`drop` 由脚本移入 archive |
| `docs/tasks/task_template/` | task 文件模板（非工作项） | 只改模板本身 |
| `docs/archive/tasks/{tid}_{slug}/` | 已归档 task 工作区 | 仅由 `scripts/task.py finish` / `drop` 从 `docs/tasks/` 移入；内部文件只准新增 |
| `docs/tasks_index.json` / `docs/archive/tasks_index.json` | 活跃/归档 task 派生索引（已 gitignore） | 由 `scripts/task.py` 自动重建；不入库、不手改 |
| `docs/archive/tasks_audit.log` | rewind/purge 审计（append-only） | 仅 `scripts/task.py rewind` / `purge` 独占 append |
| `docs/handoff.md` | 项目级交接（仅最新一节） | 见「总账分工」 |
| `docs/pending.md` | 待办总账：未修 bug + 遗留待办 | 见「总账分工」 |
| `docs/findings.md` | 已验证的技术发现（跨 task 复用） | 见「总账分工」 |
| `docs/archive/{handoff,pending}.md` | 对应文件的已闭环/过时历史 | 只追加；由对应 skill 在用户调用时迁入 |
| `docs/blueprint/` | 当前长期真相：架构、领域、约定、决策 | finalization 时更新；写代码或文档前读 `conventions.md`，改跨模块行为前读 `architecture.md`，历史取舍读 `decisions.md` |
| `docs/reviews/prompts/` | review prompt 模板 | 改审查标准时更新 |
| `docs/spikes/report_template.md` | spike 报告模板 | 只改模板本身 |
| `docs/spikes/{sid}_{slug}/` | 当前 spike（`report.md` 必需；有实验代码建 `code/`） | 见「spike」 |
| `.agents/skills/` | 项目 skill 正文 | 改 skill 走文档纪律；不放业务代码 |
| `.claude/skills/` | 指向 `.agents/skills/` 的软链 | 只维护软链 |
| `docs/guides/` | 给人看的使用指南 | 给人读，不写 agent 行为规则 |
| `docs/archive/` | 完结或终止的历史 | 镜像原路径；内部文件只准新增 |
| `docs_repo/` | **仅本模板仓**的设计笔记/复盘（非业务） | 不参与 task 状态机；**复制新项目时不得带入** |
| `schemas/` | 跨服务接口契约 | 改契约走 task 流程 |
| `config/` | 配置（默认 + 环境覆盖 + `.env.example`） | 仅 `.env.example` 入库；真值写本地 `.env` |
| `src/` `tests/` `scripts/` `assets/` | 源码、测试、脚本、静态源 | 仅在 task 执行期按 spec 修改；debug 复现不得写入 |
| `artifacts/` `data/` `.scratch/` | 产物、运行数据、一次性草稿 | 运行与草稿；debug 复现/实验代码**只许** `.scratch/`（已 gitignore） |
| `../{repo}_{tid}/`（仓库外） | task 工作副本（git worktree） | 由 `scripts/task.py start` 建、`finish`/`drop`/`rewind` 移除；本地 `.env` 软链回主仓；不手工创建或删除 |

## 开发原则

- specs driven：先拆 task 并填写 `spec.md`（契约区行为 AC 须非空）；版本号、底层库选型、目录结构不写进行为 AC，需要长期约束的写 `docs/blueprint/decisions.md`。
- TDD：可测部分先红后绿；测试须触达生产逻辑。实现变更让旧测试语义失效时，新增覆盖新语义的测试；旧测试原样保留或整体删除并写明理由，**禁止就地把旧测试的预期改成当前实现的输出**。
- 双审：代码轴与测试轴由两个独立 reviewer 并行完成；critical / important 阻断，minor 须处置但不阻断。派几路、blocking 阈值、finding 分类由 review prompt 与 `review_level` 定义。
- 工作区隔离：`task.py start` 默认为每个 task 建 git worktree（`../{repo}_{tid}`）。分支只隔离历史不隔离文件，未提交改动跟工作目录走。只有用户明确指令才用 `--no-worktree`。
- **未经用户明确允许，绝不准手动直接更改未被 gitignore 的代码文件**（含 `src/`、`tests/`、入库脚本等）。明确允许包括：用户点名授权改路径，或用户触发的实施类 skill 在其流程内按 spec 修改。已 gitignore 路径（如 `.scratch/`）不受本条限制。

## Task 工作流入口

仅在用户斜杠或其它 skill 链式调用时进入；**禁止**自行进入。

| 用户意图 | skill | 职责 |
|----------|-------|------|
| 待做 task 还缺我什么 | `tasks-preflight` | 只读汇总缺口 |
| 哪些 backlog task 能并发 | `tasks-parallel` | 只读；以进行中 task 与已存在分支为基线出并发分组 |
| 修 bug / 复现 / 根因立项 | `task-bug` | 复现/根因（仅 `.scratch/`）→ 建修复 task + 补测分析 → commit 创建物 |
| 新需求拆 task | `task-create` | 按**需求**拆建 backlog task；可一批 commit 创建物 |
| 把遗留待办转成 task | `pending-to-task` | 从 `docs/pending.md`「遗留待办」去重建 task 并回写归档 |
| 多个 backlog task 合并成一个 | `tasks-merge` | 仅 backlog；并 spec/task → `edit` 目标 → `drop` 源 |
| 串行跑完待做 task | `tasks-run` | **串行**执行；每 task 一个交付单元 |
| 整理 handoff/pending/过时文档 | `repo-hygiene` | 迁 archive；不手改 task 状态 |
| 清理缓存/无用文件 | `repo-clean` | 默认 dry-run |

状态、门禁、目录权责、硬约束以**本文件**为准；skill 只定义操作步骤，不覆盖本文件。

## 状态与门禁

- 状态：`backlog` / `active` / `blocked` / `done` / `dropped`。状态权威 = `task.md` front matter。
- 门禁默认：`max_verify_round = 5`（黑盒）；`max_review_round = 4`（双审）。`max_review_round` 计**回归轮次**——上一轮判 FAIL、修完重审才消耗一次；首轮不计；上一轮 PASS 后因新增改动再审不计。
- 状态撤回（审计写入 `docs/archive/tasks_audit.log`）：
  - `rewind`：`active->backlog`、`blocked->active`（或 `blocked->backlog`）；仅活跃目录；archive 不可 rewind。
  - `purge`：`backlog->deleted`（不进 archive）；仅从未开干（无 task 目录、无未合并 commit）的误建。有 commit 用 `drop`。
- `finish`：目录进 `docs/archive/tasks/`，worktree 移除，派生索引重建（均由 `task.py` 完成）。

### blocked

触发条件与放行入口见下表；具体处置步骤见 `tasks-run`。

| 触发 | `block --reason` |
|------|------------------|
| 黑盒轮次达 `max_verify_round` 仍未通过 | `blackbox` |
| 双审 `overall=FAIL` 且 `round ≥ max_review_round` | `review` |
| 基础设施连续失败（503 / 网络 / subagent 启动失败） | `infra` |

进入 blocked 后，agent **必须停下来向用户请求选择**（不得自行决定下一步，不得自动推进）：

- **加轮**：用户指定新 `max_verify_round` / `max_review_round` → `scripts/task.py resume <tid>`；**计数累计不清零**。
- **排除阻塞**（`--reason infra`）：外部依赖恢复后 `scripts/task.py resume <tid>`，从中断的 Step 续跑。
- **dropped**：backlog 由 `drop` 归档；active/blocked 写明终止原因后 `drop`，半成品保留在 task 分支并**必须提交**。

## commit 策略

- **创建期**（`task-create` 等创建类 skill）：一次需求拆出的多个 task 目录**可以同一 commit**；创建 commit 不含生产实现；commit 前须先向用户列出创建物并获同意。
- **执行期**（`tasks-run`）：一个 task = 一个**可 review 的交付单元**，允许拆多个原子 commit，每个 subject 都含 `{tid}`。合并回主干用 `--no-ff` 保留 task 边界。
- **维护期**（`tasks-merge` / `repo-hygiene` 等）：自成一个 commit，不与创建期、执行期 commit 混。
- 派生 index JSON 已 gitignore，任何阶段都不进 commit。
- `blocked` 未放行前：不把该 task 当 done 提交。

## debug

- 复现与探索性实验代码**只许**写在 `.scratch/`；**禁止**写入未被 gitignore 的路径（含 `src/` / `tests/` / `scripts/` 等）。
- 根因确认后**必须**经 task 修复；debug 阶段不做生产修复。
- 立项与补测分析流程见 `task-bug`。

## 总账分工

四个总账各管一类，界线如下。具体格式见各文件内注释，操作步骤见对应 skill。

| 文件 | 记什么 | 与其他的界线 |
|------|--------|--------------|
| `docs/pending.md` | 待做**事项**：未修 bug（`bNNN`）、遗留待办（`fNNN`） | 与 findings：pending 是待做，findings 是已知事实。与 decisions：pending 是待决定/做的，decisions 是已决定 |
| `docs/findings.md` | 已验证**事实**（`dNNN`）：spike 结论、工具行为、平台差异、依赖坑 | 与 decisions：findings 记外部世界是什么样，decisions 记我们选了什么 |
| `docs/blueprint/decisions.md` | 已做出的**决定**及理由 | 长期约束（技术选型、架构取舍）写这里，不写进 spec AC |
| `docs/handoff.md` | 项目级交接（仅最新一节） | 记录须含 branch 与交出时 head_commit；过时内容在 `repo-hygiene` 被调用时迁 archive |

- pending/findings/decisions 同一件事跨多个文件时各记各的，互相引用编号，不复制正文。
- 推测与待确认的内容不进这三个文件，写进对应 task 的 spec 上下文区并标 `UNVERIFIED`。

### pending

只保留**未闭环**条目，两套编号独立递增不复用。立即修完的 bug 走 task 流程，可不登记。

闭环迁移与漏迁补迁的步骤见 `tasks-run` 收尾与 `repo-hygiene`。

### findings

只追加与就地修订，不迁 archive——发现是长期资产，无「闭环」。失效时改写「现状」并注明日期与原因，不删条目。

### spike

用于必须靠实验确认的事项：新 major、非标准 provider、协议兼容、平台差异、性能或工具行为；非默认必做。

- 建 `docs/spikes/{sid}_{slug}/`，复制 `docs/spikes/report_template.md` 为 `report.md`；`sid` 取 spikes 与 archive 中最大编号加一。
- 有实验代码再建 `code/`；可入库，仅作验证材料。
- 结论抽一条进 `docs/findings.md`，报告全文移入 `docs/archive/spikes/`。
- **执行期** spike 在 `tasks-run` 中进行；创建期只把 spike 需求写进 spec 上下文区，不提前写生产代码。

## 硬约束

- **task 状态只能由 `scripts/task.py` 修改**：状态权威是 `task.md` front matter，agent 禁止直接编辑该 front matter（正文可写）。两个 index JSON 是脚本重建的派生缓存，已 gitignore，禁止手改也不入 commit。脚本失败必须停下提示用户。
- **禁止 plan mode**：用户触发实施类 skill（`tasks-run`）即代表队列内 task 已批准。不得进入 plan mode、不得开跑前重述计划征求同意、不得把 spec 已写明的内容再问一遍。只在「整批停止条件」或 preflight FAIL 时停。
- `docs/archive/tasks_audit.log` **只能由 `scripts/task.py rewind` / `purge` 以 append 模式写入**。agent 禁止编辑、截断或删除。
- debug / 探索代码不得写入未被 gitignore 的路径；生产改动只在用户授权的 task 执行期进行。
- {密钥规则、禁写路径、平台限制等项目特有约束，按需填写。}
- `{doctor_cmd}`：可选环境前置检查；只检查存在性，不自动安装或升级。
- `{test_cmd}`：日常测试；**红** / **绿** 使用。命令多时改为指向 `docs/guides/testing.md`。
- `{blackbox_cmd}`：黑盒验证；**黑盒** 使用。

## 文档修改规范

- 结构或语义变化时，先确定最终表述，修改最小完整语义块，禁止逐句打补丁。
- 同一事实、规则或结论只保留一个权威定义；其他位置使用稳定标题或标识引用，避免复制正文和可能失效的编号引用。
- 文档正文直接陈述事实，禁止元引用：不嵌入决策/spike/ticket 编号（`(D24-N3)` `(S15)`）、来源或实现位置标注（括注如 `(根据 D24 决定)` `(D25 wrapper)` `(impl at ts/X.ts)`，叙述如"本节根据 X 决定 Y"）。
- 存在多种合理理解时，先澄清再做跨文档修改。
- 优先使用正向描述；仅安全、不可逆操作、明确禁区三类场景使用否定句。
- 完成后检查：旧表述、重复内容、矛盾结论、失效引用、遗漏同步、元引用残留。
