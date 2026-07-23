---
tid: t001
slug: example_slug
diff_anchor: "<SHA>"
branch: t001_example_slug
---

# Task {tid}_{slug}

过程总账。reviewer **只写** `review_code.md` / `review_test.md`，不改本文件。

## 过程记录

只记有追溯价值的进展、踩坑、中途决策、偏离 plan、关键验证；不写命令流水账。

- 无事项时写：无

## Review 处置

本文件本小节 = 处置表唯一落点。review 结束后在此追加轮次小节与表格；不要写到 `review_code.md` / `review_test.md`，也不要另建其他文件。

逐条对应两份 review 的 finding。`status` 只许：`已修` / `遗留` / `撤回`（全处理，不静默丢 finding）。

- `已修`：本 task 内已按 finding 改完
- `遗留`：本 task 不处理；critical / important 遗留仍阻断，minor 遗留不阻断且须写 rationale；已有 follow-up task 时将 tid 写入 `fix_ref`
- `撤回`：误报；须原 reviewer 在对应 `review_*.md` 末尾追加撤回记录后，再在本表标 `撤回`

### Round 1 场景

- **无 finding**：写「Round 1 零 finding，未进处置表。」
- **仅有 minor（无 critical / important）**：仍建表，逐条处置 minor。
- **有 critical / important**：建表，逐条填 status（不得留空）。

### Round N (YYYY-MM-DD HH:MM UTC+8)

（有 finding 时用本表；每条 finding 一行。）

| finding_id | severity | status | rationale | fix_ref |
|------------|----------|--------|-----------|---------|
| {tid}_code_f001 | critical/important/minor | 已修 | {一句话} | {文件:行} |

## 收尾报告

本 task 所在 commit 即 task commit，SHA 由 `git log --grep {tid}` 查，不在此记。

### 验收

- spec：[`spec.md`](spec.md)
- 结果：全部满足 / 未满足
- 证据：{测试、黑盒或人工检查结果；按需引用 AC 编号，不复制 AC 正文}

### Reviewer verdict

取自 `review_code.md` / `review_test.md` 各文件**最后一条** `verdict:`（多轮追加时以末轮为准）。未开的轮次写 N/A。

- Round 1 code：PASS / FAIL
- Round 1 test：PASS / FAIL
- Round 2 code：N/A / PASS / FAIL
- Round 2 test：N/A / PASS / FAIL

### 遗留

- 无
- 或：`{finding_id}`：原因；后续计划

### 结果摘要

- {一句话；无额外说明可写「见上」}
