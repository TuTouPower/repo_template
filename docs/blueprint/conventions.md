# 约定（内容细节）

行为规则和工作顺序见 `AGENTS.md`。本文只定义各类文档字段、命名和记录格式。

## 命名与格式

- 普通变量、函数、文件、目录和 slug 使用小写 `snake_case`。
- `AGENTS.md`、`CLAUDE.md`、`README.md` 是工具入口例外。
- `TNNN_`、`SNN_` 是工作项类型前缀例外；前缀后 slug 仍使用小写 `snake_case`。
- Markdown 嵌套内容缩进 4 空格，禁止 tab。
- 时间戳统一使用中国时间，格式 `YYYY-MM-DD HH:MM UTC+8`。
- 语言和框架已有稳定惯例时，在本文件补充项目级例外，不强行覆盖生态要求。

## task 文件模板

所有 active task 固定使用以下文件。任务很小时内容可以简短，但不合并文件。

| 文件 | 字段 |
|------|------|
| `spec.md` | 背景；范围；非范围；验收标准；依赖与约束 |
| `plan.md` | 步骤及验证；风险与回退；完结时需更新的 blueprint 条目 |
| `log.md` | 进展；踩坑；中途决策；偏离 plan 的原因；关键验证结果 |
| `review_code.md` | task review 报告（文档+代码 agent 写） |
| `review_test.md` | task review 报告（测试 agent 写） |
| `adoption.md` | review 处置清单 |
| `task_report.md` | task 完结报告 |

- `log.md` 记录有追溯价值的事项，不写命令流水账。

## task review

task review 在单 task 流程 step 6 进行，对照 task spec 评审当前未提交改动（working tree）。

- 两个 sub agent 并行评审，各从 `docs/templates/task/review.md` 复制模板，独立成报告：
    - 文档+代码 agent：核对实现与 spec 一致性、文档真实性，写 `review_code.md`，finding 用 `TNNN_code_fNNN` 编号。
    - 测试 agent：核对测试覆盖与端到端行为，写 `review_test.md`，finding 用 `TNNN_test_fNNN` 编号。
- 续写规则：首次复制模板写入；后续局部重审在文件末尾追加 `## 局部重审 N (YYYY-MM-DD HH:MM, 触发:原因)` 小节，只写本轮新发现和复核结论；历史轮次内容保留不覆盖。finding ID 跨轮次全局续编。
- reviewer 对评审对象只读，只能创建自己的报告；不得修改被评审代码、被评审文档、`adoption.md`、他人报告或历史记录。

### review 报告字段

- task：`TNNN_slug`
- spec：`docs/tasks/TNNN_slug/spec.md`
- target：本 task 未提交改动（working tree）
- reviewed_at：`YYYY-MM-DD HH:MM UTC+8`
- findings：分类别前缀的 `TNNN_code_fNNN` / `TNNN_test_fNNN`，每条含严重度、位置、问题、建议
- conclusion：各 agent 总体判断

## task adoption

`adoption.md` 在单 task 流程 step 7 由 owner 写，逐条处置 `review.md` 的 finding。

| finding_id | decision | rationale | status |
|------------|----------|-----------|--------|
| TNNN_code_f001 | 采纳 / 不采纳 | {一句话理由} | 已修 / 遗留-原因 / 无需修改 |

字段说明：

- `decision`：采纳 / 不采纳。
- `rationale`：一句话理由。
- `status`：
    - `已修`：在本 task commit 内修复。
    - `遗留-原因`：未在本 commit 修复，原因写在 `-原因` 后。
    - `无需修改`：不采纳项专用。

处置路径：采纳且能当场修的立即修复（触代码或测试回 step 4 黑盒；仅文档改动区分笔误/事实，事实类触发局部重审）；不采纳的标 `无需修改`；不能当场修的标 `遗留-原因`。adoption 由 owner 自主决策，不经用户审阅，随 task commit 入库。

续写规则：首次复制模板写入；后续处置在文件末尾追加 `## Round N (YYYY-MM-DD HH:MM)` 小节，对应本轮 review 的 finding；同 finding 在不同轮次决策变化各占一行，保留历史。

## specs_index

`docs/specs_index.md` 是需求索引。task 黑盒验证通过后更新进度；全 task done 后状态改 `done`。

| slug | 状态 | task 清单 | spec 路径 | 归档路径 |
|------|------|----------|----------|---------|
| `<slug>` | active / done / dropped | T001, T002 | `docs/specs/<slug>.md` | `docs/archive/specs/<slug>.md` |

新需求开始时**不登记** specs_index；第一个 task 黑盒通过后才首次写入。

## blueprint 更新时机

- spec 和 plan 记录尚未确认的目标与方案。
- 实施和 review 期间不把未稳定状态写成长期真相。
- review、adoption 处置全部完成且最后一次黑盒验证通过后，在单 task 流程 step 8 更新受影响的 blueprint。
- 长工作若需要中途形成稳定长期真相，应拆成独立 task，并在该 task 完结时更新 blueprint。

## spike 文件模板

`report.md` 包含：问题；成功判据；尝试；证据；结论；是否采纳；后续 task ID。

实验代码存在时创建 `code/`。实验代码入库保留，但不代表可直接用于生产。

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
