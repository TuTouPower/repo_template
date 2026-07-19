# 约定（内容细节）

行为规则和工作顺序见 `AGENTS.md`。本文只定义各类文档字段、命名和记录格式；流程不再重复，需要时引用 AGENTS.md 对应 step。

## 命名与格式

- 普通变量、函数、文件、目录和 slug 使用小写 `snake_case`。
- `AGENTS.md`、`CLAUDE.md`、`README.md` 是工具入口例外。
- `TNNN_`、`SNN_` 是工作项类型前缀例外；前缀后 slug 仍使用小写 `snake_case`。
- Markdown 嵌套内容缩进 4 空格，禁止 tab。
- 时间戳统一使用中国时间，格式 `YYYY-MM-DD HH:MM UTC+8`。
- 语言和框架已有稳定惯例时，在本文件补充项目级例外，不强行覆盖生态要求。

## task 文件模板

所有 active task 固定使用以下文件。任务很小时内容可以简短，但不合并文件。创建与使用流程见 AGENTS.md 单 task 流程。

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

## review 报告字段

`review_code.md` / `review_test.md` 共用以下字段；流程（两 agent 并行、续写规则、权限）见 AGENTS.md step 6。

- task：`TNNN_slug`
- spec：`docs/tasks/TNNN_slug/spec.md`
- target：本 task 未提交改动（working tree）
- reviewer_focus：`文档+代码` / `测试`
- reviewed_at：`YYYY-MM-DD HH:MM UTC+8`
- findings：分类别前缀的 `TNNN_code_fNNN` / `TNNN_test_fNNN`，每条含严重度、位置、问题、建议
- conclusion：本 agent 总体判断

`reviewer_focus` 与 finding 前缀映射：`文档+代码` → `code`，`测试` → `test`。

## adoption 字段

`adoption.md` 字段表；处置流程见 AGENTS.md step 7。

| finding_id | decision | rationale | status |
|------------|----------|-----------|--------|
| TNNN_code_f001 | 采纳 / 不采纳 | {一句话理由} | 已修 / 遗留 / 无需修改 |

字段说明：

- `decision`：采纳 / 不采纳。
- `rationale`：一句话理由；`遗留` 项在此写未修原因。
- `status`：
    - `已修`：在本 task commit 内修复。
    - `遗留`：未在本 commit 修复。
    - `无需修改`：不采纳项专用。

## specs_index 字段

`docs/specs_index.md` 字段表；首次写入规则与状态流转见 AGENTS.md。

| slug | 状态 | task 清单 | spec 路径 | 归档路径 |
|------|------|----------|----------|---------|
| `<slug>` | active / done / dropped | T001, T002 | `docs/specs/<slug>.md` | `docs/archive/specs/<slug>.md` |

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
