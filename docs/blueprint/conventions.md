# 约定（内容细节）

行为规则和工作顺序见 `AGENTS.md`。本文只定义各类文档字段、命名和记录格式；流程不再重复，需要时引用 AGENTS.md 对应 step。

## 命名与格式

- 普通变量、函数、文件、目录和 slug 使用小写 `snake_case`。
- `AGENTS.md`、`CLAUDE.md`、`README.md` 是工具入口例外。
- `TNNN_`、`SNN_` 是工作项类型前缀例外；前缀后 slug 仍使用小写 `snake_case`。任务 ID 占位符统一写 `{TID}`（值为 T001、T042 等）。
- Markdown 嵌套内容缩进 4 空格，禁止 tab。
- 时间戳统一使用中国时间，格式 `YYYY-MM-DD HH:MM UTC+8`。
- 语言和框架已有稳定惯例时，在本文件补充项目级例外，不强行覆盖生态要求。

## schema 类型落点

按消费方决定落点，`schemas/` 只放跨服务契约。

| 类型 | 例子 | 落点 |
| ---- | ---- | ---- |
| 跨服务接口契约 | OpenAPI、gRPC `.proto`、GraphQL `.graphql`、AsyncAPI | `schemas/`，按协议分子目录：`schemas/openapi/`、`schemas/proto/`、`schemas/graphql/`；单一协议直接扁平 |
| 代码内数据契约 | Pydantic model、TS interface、Zod schema、Go struct tag | 跟模块走：`src/<module>/schemas/` 或语言惯例位置（`src/types/`、`src/models/`） |
| 数据库 schema | Alembic、Prisma schema、SQL migration、Django migration | 工具默认：`migrations/` / `prisma/` / `alembic/`，不另立目录 |
| 配置 schema | JSON Schema 校验 config、CI workflow schema、env schema | 跟配置走：`config/schemas/`，或跟消费方 |
| 文档/元数据 schema | frontmatter、Cosmjs、yaml metadata 校验 | `docs/schemas/`，或跟文档源 |

原则：

- 跨服务契约会触发上下游同步，独立根目录便于发现和工具扫描。
- 代码内契约不外露，跟源码同源，避免双份维护。
- 数据库 schema 跟 migration 工具走，工具约定优先于本文件。
- 多种类型并存时，按主消费方归类；归属不清记入 `docs/blueprint/decisions.md`。

## task 文件模板

active task 使用以下文件。任务很小时内容可以简短，但不合并文件。创建与使用流程见 AGENTS.md 单 task 流程。

| 文件 | 字段 | 是否必有 |
|------|------|----------|
| `spec.md` | 背景；范围；非范围；验收标准；依赖与约束 | 是（验收标准非空） |
| `plan.md` | 步骤及验证；风险与回退；完结时需更新的 blueprint 条目 | 是 |
| `log.md` | **diff_anchor**（标题下首行）；进展；踩坑；中途决策；偏离 plan 的原因；关键验证结果 | 是 |
| `review_code.md` | task review 报告（代码 agent 写） | 是（进入 review 后） |
| `review_test.md` | task review 报告（测试 agent 写） | 是（进入 review 后） |
| `adoption.md` | review 处置清单 | **可选**：仅 review 非零 finding / 进入 adoption 时存在 |
| `task_report.md` | task 完结报告 | 是（收尾时） |

- `log.md` 记录有追溯价值的事项，不写命令流水账。

## review 报告字段

`review_code.md` / `review_test.md` 共用以下字段；流程见 AGENTS.md step 5；完整 reviewer 提示词见 `docs/templates/task/review_prompt_code.md` 和 `docs/templates/task/review_prompt_test.md`。报告结构以 prompt 输出格式为准，`docs/templates/task/review.md` 仅空骨架参考。

- task：`{TID}_slug`
- spec：`spec.md`（同目录相对路径，随归档移动仍有效）
- diff_anchor：task 开始时的 HEAD SHA
- target：`git diff <diff_anchor>`（相对工作区；见 AGENTS.md「review target」）
- reviewer_focus：`代码` / `测试`
- round：`1` / `2`（默认同一 task 最多 2 轮；用户批准可追加）
- reviewed_at：`YYYY-MM-DD HH:MM UTC+8`
- findings：分类别前缀的 `{TID}_code_fNNN` / `{TID}_test_fNNN`，每条含严重度、位置、问题、建议；标题格式 `### {TID}_code_f001 - {标题}`（ASCII 连字符两侧空格）
- conclusion：前轮复核（Round 2 写）+ 本轮新发现数 + 总体判断
- verdict：末行 `verdict: PASS` 或 `verdict: FAIL`

**PASS 判定式**（唯一）：`PASS ⟺ 本轮 finding 数 = 0 ∧（无前轮 ∨ 前轮 finding 全部已修或已撤回）`。

- Round 1：无前轮 → 退化为 0 finding 即 PASS，可直接收尾。
- Round 2：前轮全修/撤回且本轮 0 新 finding → PASS；否则 FAIL → blocked。

`reviewer_focus` 与 finding 前缀映射：`代码` → `code`，`测试` → `test`。

### 严重度三级（唯一完整定义；AGENTS 与 prompt 引用本文）

默认所有 finding 必须处置（已修 / 遗留+用户批准 / 撤回）。严重度表示优先级，不表示可忽略。

#### critical

- **实现轴**：bug / 安全 / 数据丢失 / broken functionality
- **测试轴**：测了假行为致 AC 看似覆盖但实际未验证；删除关键 AC 的测试；mock 掉被测逻辑本身

#### important

本 task 在修复前不可信，例如：

- **实现轴**：verbatim 重复、swallowed errors、spec AC 缺失实现、违反 spec 不变量
- **测试轴**：恒真断言、弱化断言、删 expect、`.skip`、mock 误用、AC 缺测试、红灯未归因
- 危险模式扫描命中项（见 `review_prompt_test.md`）**最低 important**，不得标 minor

#### minor

风格、覆盖可更广、命名优化、注释补充、文件膨胀、测试结构清理。

## adoption 字段

`adoption.md` 字段表；处置流程见 AGENTS.md step 6。默认严格模式：finding 必须处置。误报经原 reviewer **撤回** 或用户裁决后可不改代码。

| finding_id | severity | status | rationale | fix_ref |
|------------|----------|--------|-----------|---------|
| T001_code_f001 | critical/important/minor | 已修 | {一句话说明} | {文件:行 或路径} |
| T001_test_f001 | ... | 遗留 | {需拆新 task 的依据} | - |
| T001_code_f002 | ... | 撤回 | {reviewer 撤回依据摘要} | review 追加记录位置 |

字段说明：

- `severity`：原 finding 严重度。
- `status`：
    - `已修`：在本 task commit 内修复。
    - `遗留`：实现层无法在本 task 解决（需拆新 task）；task 不得 done 直到用户显式批准 exception。
    - `撤回`：原 reviewer 确认误报并追加撤回记录，或用户明确裁决不成立。
- `rationale`：对应 status 的一句话说明。
- `fix_ref`：`已修` 指向修复位置；`遗留`/`撤回` 填 `-` 或审查记录位置。

用户批准 exception 时：**不改写** review 报告中的 `verdict` 与 finding 正文；在 `tasks_index` 备注与 `task_report` 分栏记录用户处置。

## specs_index 字段

`docs/specs_index.md` 是当前生效 spec 清单；写入规则见 AGENTS.md「目录与读写规则」与「需求完结」。

| slug | task 清单 | 最后固化时间 |
|------|----------|--------------|
| `<slug>` | T001, T002 | YYYY-MM-DD |

- 表内 = 生效；废弃时整行删除。
- 历史清单由 `docs/archive/specs/` 目录承载，不重复 index。
- task 期间不写本表；全需求 task done 后由「需求完结」首次写入。
- 替代旧需求时可在备注记 `supersedes: <old_slug>`。

## spike 文件模板

`report.md` 包含：问题；成功判据；尝试；证据；结论；是否采纳；后续 task ID。

实验代码存在时创建 `code/`。实验代码入库保留，仅作为验证材料。

## decisions.md 条目格式

```markdown
## NNN 标题（YYYY-MM-DD）

- 背景：为什么需要决策
- 选项：考虑过什么
- 结论：选了什么，为什么
- 替代：若替代旧决策，填写旧编号；否则写“无”
```

## 编码与测试

- 命名、格式、lint 规则以项目实际工具为准，并在本文件记录项目级例外和原因。
- 日志优先，禁止把 `print` / `console.log` 调试输出留在生产代码。
- 修 bug 时在对应测试层补回归用例，文件名带任务 ID，如 `tests/unit/parser/T042_empty_token.test.ts`。
