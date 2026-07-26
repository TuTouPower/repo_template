# 约定（内容细节）

行为规则和工作顺序见 `AGENTS.md`。本文只定义各类文档字段、命名和记录格式；流程不再重复，需要时引用 `AGENTS.md` 对应小节或 `.agents/skills/tasks-run/SKILL.md` 对应 Step。

## 命名与格式

- 普通变量、函数、文件、目录和 slug 使用小写 `snake_case`。
- `AGENTS.md`、`CLAUDE.md`、`README.md` 是工具入口例外。
- task 编号：占位 `{tid}`，值小写 `t001`、`t042`…。目录 / 分支 / finding：`docs/tasks/{tid}_{slug}/`、`{tid}_{slug}`、`{tid}_code_fNNN`。
- spike 编号：占位 `{sid}`，值小写 `s001`、`s003`…。目录：`docs/spikes/{sid}_{slug}/`。
- Markdown 嵌套内容缩进 4 空格，禁止 tab。
- 时间戳统一使用中国时间，格式 `YYYY-MM-DD HH:MM UTC+8`。
- 例外：`docs/archive/tasks_audit.log` 使用 ISO8601 带时区（`2026-07-22T15:30:00+08:00`），机器 grep 友好；由 `scripts/task.py rewind`/`purge` 自动写入。
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

## 模板与工作项隔离

模板就近放在领域目录下，**不是** active 工作项：

| 模板 | 路径 |
|------|------|
| task 文件 | `docs/tasks/task_template/`（`spec.md` / `plan.md` / `task.md` / `review.md`） |
| spike 报告 | `docs/spikes/report_template.md` |
| 双审 prompt | `docs/reviews/prompts/`（`code_prompt.txt` / `test_prompt.txt` / `share_prompt.txt`） |

占位示例不得占用真实 `tid` / `sid`，也不得当作 active 工作项执行。`docs/tasks/task_template/` 不得出现在 `tasks_index.json`。

## task 文件模板

| 文件 | 谁写 | 是否必有 |
|------|------|----------|
| `spec.md` | 实现侧 | 是（验收标准非空） |
| `plan.md` | 实现侧 | 是 |
| `task.md` | 实现侧 | 是（过程总账：front matter + 过程记录 / Review 处置 / 收尾报告） |
| `review_code.md` | code reviewer | 进入 review 后 |
| `review_test.md` | test reviewer | 进入 review 后 |

不再使用独立的 `log.md` / `adoption.md` / `task_report.md`。

### `task.md` front matter

```yaml
---
tid: t001          # 键 tid；值小写 t001
slug: example_slug
diff_anchor: "<SHA>"
branch: t001_example_slug
# spec_path: 可选，默认 <task_dir>/spec.md
---
```

- task 状态（`backlog` / `active` / `blocked` / `done` / `dropped`）的权威在 `docs/tasks_index.json`（通过 `scripts/task.py` 操作），不在 front matter。
- `scripts/render_review_prompts.py --task-dir ...` 读 `tid` / `slug` / `diff_anchor`（及可选 `spec_path`）生成两份 review prompt。
- 正文结构见 `docs/tasks/task_template/task.md`。

## review 报告字段

`review_code.md` / `review_test.md` 以 `scripts/render_review_prompts.py` 渲染结果为准。

- 提示词正文存于 `docs/reviews/prompts/`（`code_prompt.txt` / `test_prompt.txt` / `share_prompt.txt`），由 `scripts/render_review_prompts.py` 读取并填占位符。
- 用法：`scripts/render_review_prompts.py --task-dir docs/tasks/{tid}_{slug} --out-dir .scratch/review_prompts`
- 产物：`.scratch/review_prompts/code_review_prompt.md`、`test_review_prompt.md`；固定派两个独立 reviewer 并行完成代码轴、测试轴。
- `docs/tasks/task_template/review.md` 仅空骨架。

## Review 处置字段（写在 `task.md`）

| finding_id | severity | status | rationale | fix_ref |
|------------|----------|--------|-----------|---------|
| t001_code_f001 | critical/important/minor | 已修 | … | 文件:行 |
| t001_test_f001 | … | 遗留 | … | - |
| t001_code_f002 | … | 撤回 | … | review 追加位置 |

- `status`：`已修` / `遗留` / `撤回`
- critical / important 是 blocking；minor 非阻断，但仍须处置。minor 遗留写 rationale；已有 follow-up task 时 tid 写入 `fix_ref`。
- `blocked`：见 `AGENTS.md`「blocked」；跑 `scripts/task.py block <tid> --reason blackbox|review`

## specs_index 字段

`docs/specs_index.md` 是当前生效 spec 清单；每个 task **`tasks-run` Step 7 收尾**累积更新。

| slug | task 清单 | 最后更新时间 |
|------|----------|--------------|
| `<slug>` | t001, t002 | YYYY-MM-DD |

- 表内 = 生效；废弃时整行删除。
- 历史在 `docs/archive/specs/`。
- 替代旧需求可在备注 `supersedes: <old_slug>`。

## spike 文件模板

`report.md` 包含：问题；成功判据；尝试；证据；结论；是否采纳；后续 `tid`。

实验代码存在时创建 `code/`。实验代码入库保留，仅作为验证材料。

## 编码与测试

- 命名、格式、lint 规则以项目实际工具为准，并在本文件记录项目级例外和原因。
- 日志优先，禁止把 `print` / `console.log` 调试输出留在生产代码。
- 修 bug 时在对应测试层补回归用例，文件名带 `tid`，如 `tests/unit/parser/t042_empty_token.test.ts`。
- 文件过大、圈复杂度默认阈值见 `docs/reviews/prompts/code_prompt.txt`。项目覆盖写在本小节。
