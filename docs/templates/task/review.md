# Task review TNNN（reviewer_focus: {代码/测试}）

- task：`TNNN_slug`
- spec：`spec.md`（同目录，随归档移动仍有效）
- diff_anchor：`<SHA>`
- target：`git diff <diff_anchor>...HEAD`
- round：{1/2}
- reviewed_at：{YYYY-MM-DD HH:MM UTC+8}

流程（两 agent 并行、续写规则、权限）见 AGENTS.md step 5。两 agent 各自从对应提示词文件注入：`代码` -> `docs/templates/task/review_prompt_code.md` / 输出 `review_code.md` / 前缀 `code`；`测试` -> `docs/templates/task/review_prompt_test.md` / 输出 `review_test.md` / 前缀 `test`。

## Findings

### TNNN_<前缀>_f001 - {标题}

- 严重度：{critical / important / minor}
- 位置：`path:line` 或测试名
- 问题：{可复现或可验证的问题}
- 建议：{最小修复方向}

## 结论

- 前轮 finding 复核（Round 2 才写）：{逐条说明已修 / 未修 / 修不彻底}
- 本轮新发现：{N 条}
- 总体判断：{一句话}

verdict: FAIL
