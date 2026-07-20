---
tid: T001
slug: example_slug
diff_anchor: "<SHA>"
branch: task_T001_example_slug
owner: ""
status: backlog
---

# Task {TID}_{slug}

owner 过程总账。reviewer **只写** `review_code.md` / `review_test.md`，不改本文件。  
front matter 的 `tid` **取值与 `{TID}` 相同、一律大写**（`T001`，禁止 `t001`）。改 `diff_anchor` 时只改 front matter。

## 过程记录

只记有追溯价值的进展、踩坑、中途决策、偏离 plan、关键验证；不写命令流水账。

- 无事项时写：无

## Review 处置

逐条处置 `review_code.md` / `review_test.md` 的 finding。严格模式：`已修` / `遗留` / `撤回`，无静默忽略。

- `已修`：本 task 内修复
- `遗留`：本 task 无法解决；可随 done（exception）；须在「收尾报告」列出并口头报告
- `撤回`：原 reviewer 在对应 review 报告追加撤回记录后

### Round 1 零 finding

若 Round 1 两轴均 0 finding：写「Round 1 零 finding，未进处置」，不必建表。

### Round N (YYYY-MM-DD HH:MM UTC+8)

| finding_id | severity | status | rationale | fix_ref |
|------------|----------|--------|-----------|---------|
| {TID}_code_f001 | critical/important/minor | 已修 | {一句话} | {文件:行} |

## 收尾报告

本 task 所在 commit 即 task commit，SHA 由 `git log --grep {TID}` 查，不在此记。

### 验收标准勾选

- [ ] {从 spec.md 复制逐条}

### Reviewer verdict

- Round 1 code：PASS / FAIL
- Round 1 test：PASS / FAIL
- Round 2 code：N/A / PASS / FAIL
- Round 2 test：N/A / PASS / FAIL
- （有 exception 时不改写 review 文件中的 verdict）

### Exception / 遗留

- 无
- 或：`{finding_id}`：原因；后续计划

### 结果摘要

- {一句话；无额外说明可写「见上」}
