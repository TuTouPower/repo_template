# Adoption {TID}

逐条处置 `review_code.md` 和 `review_test.md` 的 finding。流程见 AGENTS.md step 6。

**严格模式（默认）**：finding 必须处置为 `已修` / `遗留` / `撤回`。无静默忽略。

- `已修`：本 task 内修复。
- `遗留`：无法在本 task 解决，需用户批准 exception 后才能 done。
- `撤回`：owner 举证后由 **原 reviewer** 确认误报并追加撤回记录，或用户明确裁决不成立。

## Round N (YYYY-MM-DD HH:MM UTC+8)

对应本轮 review 的 finding 处置。同 finding 在不同轮次决策变化各占一行，保留历史。

| finding_id | severity | status | rationale | fix_ref |
|------------|----------|--------|-----------|---------|
| {TID}_code_f001 | critical/important/minor | 已修 | {一句话说明} | {文件:行 或路径} |
| {TID}_test_f001 | critical/important/minor | 遗留 | {需拆新 task 的依据} | - |
| {TID}_code_f002 | critical/important/minor | 撤回 | {撤回依据摘要} | review 追加位置 |

字段说明：

- `severity`：原 finding 严重度（critical / important / minor）。
- `status`：`已修` / `遗留` / `撤回`（见上）。
- `rationale`：一句话说明。
- `fix_ref`：`已修` 指向修复位置；`遗留`/`撤回` 填 `-` 或记录位置。
