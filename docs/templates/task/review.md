# Task review {tid}（reviewer_focus: {代码/测试}）

字段骨架参考。正式报告结构以 `scripts/render_review_prompts.py` 渲染产物（`.scratch/review_prompts/*`）中的输出格式为准。


- task：`{tid}_slug`
- spec：`spec.md`（同目录相对路径）
- diff_anchor：`<SHA>`
- target：`git diff <diff_anchor>`
- round：{1/2}
- reviewed_at：{YYYY-MM-DD HH:MM UTC+8}

## Findings

### {tid}_code_f001 - {标题}

- 严重度：{critical / important / minor}
- 位置：`path:line` 或测试名
- 问题：{可复现或可验证的问题}
- 建议：{最小修复方向}

## 结论

- 前轮 finding 复核（Round 2 才写）：{逐条说明已消除 / 仍存在 / 修不彻底 / 同意撤回；以 diff 为准}
- 本轮新发现：{N 条}
- 总体判断：{一句话；只有未解决 critical / important 时 FAIL，仅有 minor 时可 PASS}
- 系统性 follow-up：{已有 tid，或建议标题与 slug；无则写“无”}

verdict: FAIL
