# Review rNN — reviewer / focus

- reviewer：{reviewer}
- focus：{security / correctness / performance / ...}
- target：`{被评审路径或范围}`
- target_owner：{owner}
- branch：`{branch}`
- base_commit：`{sha；本循环起点}`
- head：`{sha；开发循环内评审写“工作区”}`
- reviewed_at：{YYYY-MM-DD HH:MM UTC+8}

reviewer 对 target 只读，只能写本报告。结论仅适用于上述改动快照。

## Findings

### rNN_f001 — {标题}

- 严重度：{critical / high / medium / low / suggestion}
- 位置：`path:line`
- 问题：{可复现或可验证的问题}
- 建议：{最小修复方向}

## 结论

{总体判断。}
