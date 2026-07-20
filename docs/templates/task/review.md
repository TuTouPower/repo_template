# Task review {TID}（reviewer_focus: {代码/测试}）

字段骨架参考。正式报告结构以 `review_prompt_code.md` / `review_prompt_test.md` 输出格式为准。

- task：`{TID}_slug`
- spec：`spec.md`（同目录相对路径）
- diff_anchor：`<SHA>`
- target：`git diff <diff_anchor>`
- round：{1/2}
- reviewed_at：{YYYY-MM-DD HH:MM UTC+8}

## Findings

### {TID}_code_f001 - {标题}

- 严重度：{critical / important / minor}
- 位置：`path:line` 或测试名
- 问题：{可复现或可验证的问题}
- 建议：{最小修复方向}

## 结论

- 前轮 finding 复核（Round 2 才写）：{逐条说明已修 / 未修 / 修不彻底 / 撤回}
- 本轮新发现：{N 条}
- 总体判断：{一句话}

verdict: FAIL
