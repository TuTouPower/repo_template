# Task spec

## 背景

另一个 agent 未先 `task.py list`，直接 `start t038` 误把用户已有的 `persist_deleted_connector_tombstones` 拉成 active，又误建 t041（upcoming_reset_threshold）。修复被迫绕过 task.py 直接改 JSON + `git branch -d`，违反「JSON 只由 task.py 改」硬约束闭环。

根因：`scripts/task.py` 状态机只能前进（`backlog→active→blocked→done/dropped`），无回退、无误建清理。误操作不可用 task.py 自身修正，只能越界改 JSON。

## 范围

- 加 `rewind`：active→backlog、blocked→active（或 blocked→backlog 跨步），带审计；仅作用于 active 文件。
- 加 `purge`：误建彻底删除（仅 backlog 无 task 目录），审计留快照。
- 加 `docs/archive/tasks_audit.log`（append-only，task.py 独占写）。
- 同步 AGENTS.md（目录表/mermaid/硬约束/状态说明）与 conventions.md（时间戳例外）。

## 非范围

- 不加测试体系（模板仓 tests/ 空骨架，三个脚本均无测试；为单改动建测试栈不划算，TDD 留给派生项目）。
- 不加 purge tid 占位（首版文档警告，不做 tombstone）。
- DEFAULT_BRANCH 硬编码 `main`。

## 验收标准

- [ ] `rewind` 默认撤一步（active→backlog、blocked→active）；`--to` 跨步（blocked→backlog）。
- [ ] `rewind` 对 archive（done/dropped）报错引导；forward 方向（含同态）报错。
- [ ] `rewind` 回 backlog 清空 branch；回 active 保留 branch。
- [ ] `rewind` 目标 backlog 且 branch 有未合并 commit 时 warn + stdin 确认。
- [ ] `purge` 仅 backlog 无 task 目录、无未合并 commit 时通过；从 active JSON 删除，不进 archive。
- [ ] `purge` 非 backlog / 有 task 目录 / 有未合并 commit 时报错不改动。
- [ ] rewind/purge 各 append 一行到 `docs/archive/tasks_audit.log`；文件不存在自动创建。
- [ ] AGENTS.md 与 conventions.md 同步；`{test_cmd}` 占位不动。
- [ ] 修复事故全程用 task.py，不违反「JSON 只由 task.py 改」硬约束。

## 依赖与约束

- 硬约束：`docs/tasks_index.json` 与审计 log 只由 task.py 改。
