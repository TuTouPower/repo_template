---
name: task-merge
description: 把范围重叠或拆得过细的 backlog task 合并为一个，保留来源历史。用户要求合并重复或碎片化 backlog task 时调用。
disable-model-invocation: true
metadata:
  opencode/autoinvoke: "false"
---

# task-merge

将范围重叠或拆得过细的 backlog task 合并为一个，保留来源历史。

## 组合契约

仅用户显式授权入口或 registry.json 已登记父流程可执行；作为子流程时由父流程直接读取本正文，原样传递参数、identity、owner_id 与授权，不扩大授权。完整规则见 `.repo_template/docs/usage.md`「skill 调用」。

## 流程

1. 用户点名 tid；未点名时只提出候选并等待确认，不自行决定合并范围。
2. 用 effective status 确认所有来源都在 main backlog，且没有 worktree、未合并分支或执行历史。
3. 选择仍贴合语义的来源 tid 作为目标；都不贴合时用 `task.py add` 建新目标。
4. 合并 spec 的范围、非范围、AC 和上下文。AC 取可独立验证的并集；矛盾或范围取舍必须询问用户。
5. 用 `task.py edit` 记录来源和 review_level；用 `task.py merge-backlog {目标tid} --sources {来源tid逗号列表}` 一次改写全图并归档非目标来源。来源的外部前置和冲突并入目标，下游硬依赖与冲突改指目标并去重；组内边收缩，不生成自依赖。任何环或不可编辑下游均整批拒绝，禁止删除硬依赖来通过门禁。
6. 核对非目标来源已归档且 tid 不复用。图改写与归档处于同一持久事务；中断用 graph-transaction 恢复，不手动补 drop。
7. 创建有效性、`task.py plan --serial --json` 图校验和合并后依赖/冲突语义复核全部完成才算合并完成。无需新增调度状态；有业务取舍时询问用户，不能只口头建议重排。列出改动并按已有授权提交或询问是否提交。

## 边界

只处理 backlog，不实现、不 start。不得静默丢 AC，不直接编辑 front matter 或 index。
