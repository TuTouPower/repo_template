# Task plan

## 步骤与验证

1. 改 `scripts/task.py`：常量 + 辅助函数（`_get_head_short`/`has_unmerged_commits`/`append_audit`）+ `cmd_rewind` + `cmd_purge` + argparse 注册 + docstring → 验证：`python3 scripts/task.py --help` 列出 rewind/purge。
2. smoke 各路径（临时数据，跑完还原）→ 验证：spec 验收标准逐条。
3. 同步 AGENTS.md（目录表/mermaid/硬约束/状态说明）+ conventions.md（时间戳例外）→ 验证：grep 确认无遗漏落点。
4. 收尾：specs_index + task.md 收尾报告 + `task.py finish t001` + 移入 archive + 提交。

## 风险与回退

- 风险：`has_unmerged_commits` 在非 git 仓库或分支不存在时 fatal → `_get_head_short` 已 try/except；`has_unmerged_commits` 先 `rev-parse --verify --quiet` 确认存在，失败返回 False + warn。
- 风险：purge 删最大 tid 后被 add 重用（git log 混淆）→ 文档警告；有 commit 的误建走 drop。
- 回退：本 task 单 commit，`git revert` 或删分支。

## Finalization 时更新的 blueprint

- 无。task.py 是工作流基础设施，blueprint 不描述其内部状态机。
