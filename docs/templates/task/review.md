# Task review TNNN — reviewer / focus

- task：`TNNN_slug`
- spec：`docs/tasks/TNNN_slug/spec.md`
- target：本 task 未提交改动（working tree）
- reviewed_at：{YYYY-MM-DD HH:MM UTC+8}

reviewer 对 target 只读，只能写本报告自己的章节。两个 agent 的评审内容写进同一份（owner 负责合并）。所有 finding 须对照 task spec 判断代码、文档、测试是否仍满足最初需求。

## 文档+代码 agent

核对实现与 spec 是否一致、文档是否真实反映代码状态。

### TNNN_f001 — {标题}

- 严重度：{critical / high / medium / low / suggestion}
- 位置：`path:line`
- 问题：{可复现或可验证的问题}
- 建议：{最小修复方向}

## 测试 agent

核对测试覆盖与端到端行为是否对应 spec 验收标准。

### TNNN_f001 — {标题}

- 严重度：{critical / high / medium / low / suggestion}
- 位置：`path:line` 或测试名
- 问题：{覆盖缺口或端到端行为偏离 spec}
- 建议：{最小修复方向}

## 结论

{各 agent 总体判断。}
