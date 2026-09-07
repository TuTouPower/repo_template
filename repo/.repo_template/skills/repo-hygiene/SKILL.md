---
name: repo-hygiene
description: none
disable-model-invocation: true
---

# repo-hygiene

整理已闭环或过时的文档状态，迁入 `docs/archive/`；涉及迁移和删除时先 dry-run，拿不准就报告用户。

## 处理对象

1. **pending**：只归档已有明确处理引用的 todo；parked 保留。归档 task 处置表有遗留但无 fix_ref 时补 pending。
2. **handoff**：保留当前有效一节，其余追加到 archive；脚本保留文件中最后一个二级标题节，不按日期排序；迁移前先确认最后节就是应保留的节。
3. **spike**：报告完成且结论已进入 findings 后才归档。
4. **review**：用户确认过时后整目录归档，保留报告和 `_meta/`。
5. **其它文档**：仍有效则保留；明确过时且有历史价值则镜像归档；无价值草稿只有用户确认后删除。
6. **task/index**：active task 目录只由 lifecycle 命令处理；派生 index 与主干不符时可重建，不用未合并分支状态覆盖主干。

具体命令以 `repo_hygiene.py --help`、`pending.py --help` 和 usage 为准；脚本支持的迁移先 dry-run 再 `--write`；其它文档由 Agent 提交路径清单供确认，不虚构通用归档子命令。迁移前保存未提交正文；部分迁移失败时停止，核对源/目标和暂存区后恢复，不覆盖已存在的归档文件。

## 保护

不动 `.repo_template/docs/task_template/`、review prompt、spike template、skill/AGENTS 正文、业务源码和 `docs_repo/`。archive 保持追加历史，不覆盖同名目标。

## 完成

报告迁入 archive、保留、跳过和仍需用户决定的项目；按用户要求提交一个 hygiene commit或保留工作区。
