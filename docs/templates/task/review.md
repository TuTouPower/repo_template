# Task review TNNN

- task：`TNNN_slug`
- spec：`docs/tasks/TNNN_slug/spec.md`
- target：本 task 未提交改动（working tree）
- reviewer_focus：{文档+代码 / 测试}
- reviewed_at：{YYYY-MM-DD HH:MM UTC+8}

所有 finding 须对照 task spec 判断代码、文档、测试是否仍满足最初需求。两 agent 各自从本模板复制为 `review_code.md`（focus=文档+代码）或 `review_test.md`（focus=测试），独立成报告。

续写规则：首次复制本模板写入；后续局部重审在文件末尾追加 `## 局部重审 N (YYYY-MM-DD HH:MM, 触发:原因)` 小节，只写本轮新发现和复核结论；首次及历史轮次内容保留不覆盖。finding ID 跨轮次全局续编（如 `TNNN_code_f003` 接上次最大号）。

## Findings

### TNNN_<focus>_f001 — {标题}

- 严重度：{critical / high / medium / low / suggestion}
- 位置：`path:line` 或测试名
- 问题：{可复现或可验证的问题}
- 建议：{最小修复方向}

## 结论

{本 agent 总体判断。}
