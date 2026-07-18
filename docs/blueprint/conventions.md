# 约定（内容细节）

行为规则和工作顺序见 `AGENTS.md`。本文只定义各类文档字段、命名和记录格式。

## 命名与格式

- 普通变量、函数、文件、目录和 slug 使用小写 `snake_case`。
- `AGENTS.md`、`CLAUDE.md`、`README.md` 是工具入口例外。
- `TNNN_`、`RNN_`、`SNN_` 是工作项类型前缀例外；前缀后 slug 仍使用小写 `snake_case`。
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

- `log.md` 记录有追溯价值的事项，不写命令流水账。
- task review 发生时再创建 `reviews/`，无需预建空目录。

## review 文件模板

task review 和独立 review 共用 `docs/templates/review/`。

### review 报告

文件名：`rNN_<reviewer>_<focus>.md`。rNN 在所属评审集合内递增唯一：task review 在该 task 的 `reviews/` 目录内编号，独立 review 在对应 `RNN_slug/` 目录内编号。

必填字段：

- reviewer
- focus
- target
- target_owner
- branch
- base_commit
- head（开发循环内评审时为工作区未提交改动）
- reviewed_at
- findings
- conclusion

finding 使用稳定 ID，如 `r01_f001`。review 结论只适用于记录的 base_commit 到 head 的快照；进入下一开发循环后创建新一轮报告，不改写旧报告。

reviewer 对评审对象只读，只能创建自己的 review 报告；不得修改被评审代码、被评审文档、`adoption.md`、其他 reviewer 报告或历史记录。

### adoption

`adoption.md` 使用以下字段：

| finding_id | decision | rationale | resolution | verification |
|------------|----------|-----------|------------|--------------|

`resolution` 取值：

- `pending`：决策已记录，尚未落地。
- `commit:<sha>`：已由此前存在的 commit 落地。
- `not_required`：拒绝，或无需修改被评审对象。

流程分两阶段：作者先记录 decision 和 rationale，用户审阅后再落地采纳项；落地 commit 已存在后，finalization 阶段补写 SHA。禁止引用包含当前 adoption 修改的 commit。

## blueprint 更新时机

- spec 和 plan 记录尚未确认的目标与方案。
- 实施和 review 期间不把未稳定状态写成长期真相。
- review、adoption 和验证完成后，在 finalization 阶段更新受影响的 blueprint。
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
