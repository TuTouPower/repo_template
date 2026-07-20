# Adoption TNNN

逐条处置 `review_code.md` 和 `review_test.md` 的 finding。流程见 AGENTS.md step 6。

**严格模式**：所有 finding 必须采纳修复（`已修`），无"无需修改"出口。"遗留"仅限实现层无法在本 task 解决、需拆新 task 的情况。

## Round N (YYYY-MM-DD HH:MM UTC+8)

对应本轮 review 的 finding 处置。同 finding 在不同轮次决策变化各占一行，保留历史。

| finding_id | severity | status | rationale | fix_ref |
|------------|----------|--------|-----------|---------|
| TNNN_code_f001 | critical/important/minor | 已修 | {一句话说明} | {文件:行 或 commit} |
| TNNN_test_f001 | critical/important/minor | 遗留 | {需拆新 task 的依据} | - |

字段说明：

- `severity`：原 finding 严重度（critical / important / minor）。
- `status`：
    - `已修`：在本 task commit 内修复。
    - `遗留`：实现层无法在本 task 解决（需拆新 task）；task 不得 done 直到用户显式批准。
    - （无"无需修改"出口，严格模式下 finding 必须采纳。）
- `rationale`：`已修` 写一句话说明修复要点；`遗留` 写无法当场修的依据和后续 task 计划。
- `fix_ref`：`已修` 指向修复位置（`文件:行` 或 commit SHA）；`遗留` 填 `-`。
