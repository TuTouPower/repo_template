# 约定（内容细节）

行为规则和工作顺序见 `AGENTS.md`。本文只定义各类文档字段、命名和记录格式；流程不再重复，需要时引用 AGENTS.md 对应 step。

## 命名与格式

- 普通变量、函数、文件、目录和 slug 使用小写 `snake_case`。
- `AGENTS.md`、`CLAUDE.md`、`README.md` 是工具入口例外。
- 任务 ID **一律大写** `{TID}`（`T001`、`T042`…）。目录 `docs/tasks/{TID}_{slug}/`，分支 `task_{TID}_{slug}`，finding `{TID}_code_f001`。不用小写 `t001`，不用 `TNNN`。
- `SNN_` 是 spike ID 前缀例外；前缀后 slug 仍使用小写 `snake_case`。
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

| 文件 | 谁写 | 是否必有 |
|------|------|----------|
| `spec.md` | owner | 是（验收标准非空） |
| `plan.md` | owner | 是 |
| `task.md` | owner | 是（过程总账：front matter + 过程记录 / Review 处置 / 收尾报告） |
| `review_code.md` | code reviewer | 进入 review 后 |
| `review_test.md` | test reviewer | 进入 review 后 |

不再使用独立的 `log.md` / `adoption.md` / `task_report.md`。

### `task.md` front matter

```yaml
---
tid: T001          # 与 {TID} 同形，始终大写 T001，禁止 t001
slug: example_slug
diff_anchor: "<SHA>"
branch: task_T001_example_slug
owner: ""
status: backlog   # backlog | active | done | dropped
# spec_path: 可选，默认 <task_dir>/spec.md
---
```

- front matter 键名 `tid` 仅作 YAML 字段名；**取值必须是大写 `{TID}` 字符串**。
- `scripts/render_review_prompts.sh --task-dir ...` 读 `tid` / `slug` / `diff_anchor`（及可选 `spec_path`）生成两份 review prompt。
- 正文结构见 `docs/templates/task/task.md`。
## review 报告字段

`review_code.md` / `review_test.md` 以渲染后的 prompt 输出格式为准。模板源：

- `docs/templates/prompts/code_review_prompt.md`
- `docs/templates/prompts/test_review_prompt.md`
- `docs/templates/prompts/share_review_prompt.md`（共享规则、严重度、PASS）

owner 用 `scripts/render_review_prompts.sh --task-dir docs/tasks/{TID}_{slug}` 生成 prompt。`docs/templates/task/review.md` 仅空骨架。

## Review 处置字段（写在 `task.md`）

| finding_id | severity | status | rationale | fix_ref |
|------------|----------|--------|-----------|---------|
| T001_code_f001 | critical/important/minor | 已修 | … | 文件:行 |
| T001_test_f001 | … | 遗留 | … | - |
| T001_code_f002 | … | 撤回 | … | review 追加位置 |

- `status`：`已修` / `遗留` / `撤回`
- exception（有遗留）：不改写 `review_*`；`task.md` 收尾报告写清；收尾口头报告；`tasks_index` 可备注 `done_with_exception`

## specs_index 字段

`docs/specs_index.md` 是当前生效 spec 清单；每个 task **step 7 收尾**累积更新。

| slug | task 清单 | 最后更新时间 |
|------|----------|--------------|
| `<slug>` | T001, T002 | YYYY-MM-DD |

- 表内 = 生效；废弃时整行删除。
- 历史在 `docs/archive/specs/`。
- 替代旧需求可在备注 `supersedes: <old_slug>`。

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
- 文件过大、圈复杂度默认阈值见 `docs/templates/prompts/code_review_prompt.md`。项目覆盖写在本小节。
