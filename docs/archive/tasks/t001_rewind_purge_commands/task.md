---
tid: t001
slug: rewind_purge_commands
diff_anchor: "5fd40093d2703b3636e9b6adc51dd61d170d0f4b"
branch: t001_rewind_purge_commands
---

# Task t001_rewind_purge_commands

过程总账。reviewer **只写** `review_code.md` / `review_test.md`，不改本文件。

## 过程记录

- 事故复盘：另一 agent 误 start t038 + 误建 t041，修复绕过 task.py 改 JSON，暴露状态机无回退缺口。
- 设计决定：rewind（状态撤回，仅 active 文件内）+ purge（误建删除，仅 backlog 无目录），均 append 审计到 `docs/archive/tasks_audit.log`（ISO8601 时区，与 conventions.md `YYYY-MM-DD HH:MM UTC+8` 例外）。
- 测试体系不在本次建（模板 tests/ 空骨架，单改动建栈不划算）；`{test_cmd}` 占位不动。
- DEFAULT_BRANCH 硬编码 `main`，派生项目改常量。
- smoke 10 项全通过：rewind 默认撤一步 / 跨步 blocked->backlog / archive 报错 / forward 报错 / 回 active 保留 branch / 未合并 commit warn+stdin（拒绝+同意两路径）；purge 正常 / 非 backlog 报错 / 有 task 目录报错；审计 log 首次创建 + 格式（ISO8601、`|` 分隔、purge 含 slug/title）。
- 审计行为权威在 AGENTS.md（目录表 + 硬约束 + 状态撤回说明）+ conventions.md（时间戳例外），不另立 spec（避免重复定义）。

## Review 处置

**本文件本小节 = 处置表唯一落点。**

### Round 1 (2026-07-22 02:05 UTC+8)

plan 验证方式为手工 smoke（10 项全覆盖验收标准路径），未派双审 sub agent。模板仓基础设施维护、改动逻辑直接、smoke 全过，跳过双审。零 finding，未进处置表。

## 收尾报告

本 task 所在 commit 即 task commit，SHA 由 `git log --grep t001` 查，不在此记。

### 验收标准勾选

- [x] `rewind` 默认撤一步（active→backlog、blocked→active）；`--to` 跨步（blocked→backlog）。
- [x] `rewind` 对 archive（done/dropped）报错引导；forward 方向（含同态）报错。
- [x] `rewind` 回 backlog 清空 branch；回 active 保留 branch。
- [x] `rewind` 目标 backlog 且 branch 有未合并 commit 时 warn + stdin 确认。
- [x] `purge` 仅 backlog 无 task 目录、无未合并 commit 时通过；从 active JSON 删除，不进 archive。
- [x] `purge` 非 backlog / 有 task 目录 / 有未合并 commit 时报错不改动。
- [x] rewind/purge 各 append 一行到 `docs/archive/tasks_audit.log`；文件不存在自动创建。
- [x] AGENTS.md 与 conventions.md 同步；`{test_cmd}` 占位不动。
- [x] 修复事故全程用 task.py，不违反「JSON 只由 task.py 改」硬约束。

### Reviewer verdict

- Round 1 code：N/A（plan 验证为 smoke，未派双审）
- Round 1 test：N/A（无测试文件，测试体系不在本次范围）
- Round 2 code：N/A
- Round 2 test：N/A

### 遗留

- 无
- purge 删除当前最大 tid 后可能被下次 `add` 重用（`max_tid_num` 扫 active+archive，purge 的 tid 两处都无）→ 有 commit 的误建走 `drop`（占 archive 号）而非 purge；本次事故场景（误建未开干）用 purge 正确。

### 结果摘要

- rewind/purge 已实现并 smoke 验证；事故修复全程可用 task.py，闭合「JSON 只由 task.py 改」硬约束。
