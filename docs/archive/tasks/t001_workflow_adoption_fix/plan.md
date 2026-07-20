# Task plan

## 步骤与验证

1. 登记 tasks_index active，记 diff_anchor → 验证：log 首字段与 branch 名
2. 重写 AGENTS.md 工作流（diff 源、Round 状态机、spec 填写、需求完结、分支、严格模式、exception）→ 验证：rg 无矛盾关键字
3. 更新 conventions.md、decisions.md、tasks_index 头部、README → 验证：与 AGENTS 对齐
4. 更新/删除 task 模板（log、task_report、review、adoption、prompts；删 review_prompt.md）→ 验证：无旧路径引用、无 `...HEAD` review target
5. 一致性黑盒检索 → 验证：见 log 命令清单
6. review Round 1（对照 spec）→ 有 finding 则 adoption 修复后再 Round 2
7. 写 task_report、归档、commit

## 风险与回退

- 风险：AGENTS 大段改写遗漏交叉引用
- 回退：丢弃本分支未合并改动

## Finalization 时更新的 blueprint

- `docs/blueprint/conventions.md`：review target、verdict 公式、严重度、adoption 状态、log.diff_anchor、adoption 可选
- `docs/blueprint/decisions.md`：4 项产品决策（diff 源、backlog 目录、争议路径、exception 语义）
