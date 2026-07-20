# Task report {TID}

本报告所在 commit 即 task commit，SHA 由 `git log --grep {TID}` 查，不在此记录。

## spec 验收标准勾选

- [ ] {复制 `spec.md` 验收标准逐条}

## adoption 处置摘要

- Round 1 零 finding 时写：`Round 1 零 finding，未进 adoption`
- 否则：已修 N 项 / 遗留 K 项 / 撤回 M 项
- {每条一行：finding_id - 处置一句话}

## reviewer verdict

- Round 1 verdict：PASS / FAIL
- Round 2 verdict：N/A / PASS / FAIL
- （FAIL 且用户批准 exception 时 **不改写** 上方 reviewer verdict，见下节）

## 用户处置（仅 exception / 降级时填）

- 批准人 / 时间：
- finding ID：
- 处置：遗留保留 / 降级 / 拆 task / 重写 / 其他
- tasks_index 备注：`done_with_exception` 等

## 遗留问题

- {若有，注明原因和影响；无则写"无"}
