# 约定（内容细节）

行为规则和工作顺序见 `AGENTS.md`。本文只定义各类文档字段、命名和记录格式；流程不再重复，需要时引用 `AGENTS.md` 对应小节或 `.agents/skills/tasks-run/SKILL.md` 对应 Step。

## 命名与格式

- 普通变量、函数、文件、目录和 slug 使用小写 `snake_case`。
- `AGENTS.md`、`CLAUDE.md`、`README.md` 是工具入口例外。
- task 编号：占位 `{tid}`，值小写 `t001`、`t042`…。目录 / 分支 / finding：`docs/tasks/{tid}_{slug}/`、`{tid}_{slug}`、`{tid}_code_fNNN`。
- spike 编号：占位 `{sid}`，值小写 `s001`、`s003`…。目录：`docs/spikes/{sid}_{slug}/`。
- 总账编号：`docs/pending.md` 用 `bNNN`（未修 bug）与 `fNNN`（遗留待办）两套独立递增；`docs/findings.md` 用 `dNNN`。三套均不复用已用过的号。
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
| task 文件 | `docs/tasks/task_template/`（`spec.md` / `task.md` / `review.md`） |
| spike 报告 | `docs/spikes/report_template.md` |
| 双审 prompt | `docs/reviews/prompts/`（`code_prompt.txt` / `test_prompt.txt` / `share_prompt.txt`） |

占位示例不得占用真实 `tid` / `sid`，也不得当作 active 工作项执行。`scripts/task.py` 扫描 task 目录时跳过 `task_template/`。

## task 文件模板

| 文件 | 谁写 | 是否必有 |
|------|------|----------|
| `spec.md` | 实现侧 | 是（契约区 + 上下文区；契约区验收标准非空） |
| `task.md` | 实现侧 | 是（过程总账：front matter + 实施笔记 / Review 处置 / 收尾报告） |
| `review_code.md` | code reviewer | 进入 review 后 |
| `review_test.md` | test reviewer | 进入 review 后 |

不使用 `plan.md`：实施步骤在创建期无法准确预测，改由执行期写入 `task.md` 实施笔记；reviewer 需要的决策上下文归 `spec.md` 上下文区。

### `spec.md` 两区

| 区 | 内容 | 可变性 |
|----|------|--------|
| 契约区 | 范围 / 非范围 / 验收标准 / 可测试性声明 | `task.py start` 时锁 hash，执行期不改；`preflight` 检测漂移 |
| 上下文区 | 有意不测 / 测试策略 / 未知契约清单 / 风险与回退 / 依赖与约束 / blueprint 更新点 | 执行期可补 |

两区正文由 `scripts/render_review_prompts.py` 注入 reviewer prompt。reviewer 判 AC 只看契约区，判测试覆盖核对上下文区。

需部署或人工环境才能验证的 AC 加 `[deploy]` 前缀。未核实的外部契约在「未知契约清单」标 `UNVERIFIED`。

### `task.md` front matter

```yaml
---
tid: t001              # 键 tid；值小写 t001
slug: example_slug
title: "task 标题"
status: backlog        # backlog / active / blocked / done / dropped
branch: ""             # start 时写入 {tid}_{slug}
worktree: ""           # start 时写入 ../{repo}_{tid}
review_level: full     # full / single
depends_on: ""         # 前置 tid，逗号分隔
diff_anchor: ""        # Step 1 实写当前 HEAD
contract_hash: ""      # start 时锁定 spec 契约区 hash
note: ""
# spec_path: 可选，默认 <task_dir>/spec.md
---
```

- **front matter 是 task 状态的权威**，只经 `scripts/task.py` 修改；agent 不手改。`docs/tasks_index.json` 与 archive 版由脚本扫描各 `task.md` 派生，已 gitignore，不入库。
- `scripts/render_review_prompts.py --task-dir ...` 读 `tid` / `slug` / `diff_anchor` / `review_level`（及可选 `spec_path`），连同 spec 两区正文生成 review prompt。
- 正文结构见 `docs/tasks/task_template/task.md`。

## review 报告字段

`review_code.md` / `review_test.md` 以 `scripts/render_review_prompts.py` 渲染结果为准。

- 提示词正文存于 `docs/reviews/prompts/`（`code_prompt.txt` / `test_prompt.txt` / `share_prompt.txt`），由 `scripts/render_review_prompts.py` 读取并填占位符。
- 用法：`scripts/render_review_prompts.py --task-dir docs/tasks/{tid}_{slug} --out-dir .scratch/review_prompts`
- 产物：`.scratch/review_prompts/code_review_prompt.md`、`test_review_prompt.md`。派几路由 `review_level` 决定（`full` 双审 / `single` 一路通用 reviewer）。
- 派 subagent 只传产物路径，不内联 prompt 正文。
- `docs/tasks/task_template/review.md` 只写落点，不复制格式骨架——格式唯一定义在 prompt 模板。

## Review 处置字段（写在 `task.md`）

| finding_id | severity | status | rationale | fix_ref |
|------------|----------|--------|-----------|---------|
| t001_code_f001 | critical/important/minor | 已修 | … | 文件:行 |
| t001_test_f001 | … | 遗留 | … | - |
| t001_code_f002 | … | 撤回 | … | review 追加位置 |

- `status`：`已修` / `遗留` / `撤回`
- critical / important 是 blocking；minor 非阻断，但仍须处置。minor 遗留写 rationale；已有 follow-up task 时 tid 写入 `fix_ref`。
- reviewer 标注为 spec 过时的 finding（实现合理但与 spec 描述不符），处置为改 spec 上下文区，不计 FAIL。
- `blocked`：见 `AGENTS.md`「blocked」；跑 `scripts/task.py block <tid> --reason blackbox|review|infra`

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

收尾时抽一条可复用结论进 `docs/findings.md` 拿 `dNNN`，报告全文移入 `docs/archive/spikes/`：报告是过程，findings 是结论。

## 总账与 findings

`docs/pending.md` / `docs/findings.md` / `docs/blueprint/decisions.md` 的分工与界线见 `AGENTS.md`「总账分工」。本文件不重复。

## 编码与测试

- 命名、格式、lint 规则以项目实际工具为准，并在本文件记录项目级例外和原因。
- 日志优先，禁止把 `print` / `console.log` 调试输出留在生产代码。
- 修 bug 时在对应测试层补回归用例，文件名带 `tid`，如 `tests/unit/parser/t042_empty_token.test.ts`。
- 实现变更让旧测试语义失效时：新增覆盖新语义的测试；旧测试原样保留或整体删除并写明理由。禁止就地把旧测试的预期改成当前实现的输出。
- 文件过大、圈复杂度默认阈值见 `docs/reviews/prompts/code_prompt.txt`；两者默认不阻断 review。项目覆盖写在本小节。
