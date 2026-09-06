---
name: task-merge
description: none
disable-model-invocation: true
---

# task-merge

将范围重叠或拆得过细的 backlog task 合并为一个，保留来源历史。

## 流程

1. 用户点名 tid；未点名时只提出候选并等待确认，不自行决定合并范围。
2. 用 effective status 确认所有来源都在 main backlog，且没有 worktree、未合并分支或执行历史。
3. 选择仍贴合语义的来源 tid 作为目标；都不贴合时用 `task.py add` 建新目标。
4. 合并 spec 的范围、非范围、AC 和上下文。AC 取可独立验证的并集；矛盾或范围取舍必须询问用户。
5. 用 `task.py edit` 记录来源和 review_level。扫描全部 backlog task，逐个用 `--depends-remove` / `--conflicts-remove` 清除对被合并来源的引用；`--conflicts-remove` 会同时清掉可编辑对端的声明，确保无向读取模型中关系真正消失。任一引用未清理时 `drop` 会拒绝，必须先处理。
6. 用 `task.py drop` 归档非目标来源，tid 不复用。
7. 运行创建有效性和图校验，并口头提示重新运行 `task-schedule` 评估合并后的依赖/冲突；不写不存在的“待重新调度”字段。列出改动并询问是否提交。

## 边界

只处理 backlog，不实现、不 start。不得静默丢 AC，不直接编辑 front matter 或 index。
