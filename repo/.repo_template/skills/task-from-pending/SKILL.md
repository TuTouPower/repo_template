---
name: task-from-pending
description: none
disable-model-invocation: true
---

# task-from-pending

将 `docs/pending/todo/` 中仍有效、值得实施的条目聚合成 backlog task，并归档已转办条目。

## 流程

1. 确定用户指定或 todo 中的候选；parked 只有用户明确复活后才处理。
2. 核实当前代码、spec 和测试：问题是否仍存在、描述是否过时、是否已有等价 effective task。不存在的条目按验证结论闭环，不建 task。
3. 普通条目按主题和交付面去重聚合。未分析的 bug 调用 `task-bug analysis-only`，取得可验证根因、同类位点和补测方向后再决定范围。
4. 范围有争议、影响很小或需要产品取舍时询问用户；“全部捞”仍表示有效且非重复的聚合结果，不是一条 pending 一个 task。
5. 调用 `task-create` 创建并验证 task，spec 写来源 `pNNN` 和当前核实结论。
6. 用 `pending.py archive {pNNN} --fix-ref {tid} --write` 归档已转 task 的条目；不存在、已解决或并入已有 task 的条目也写清处理引用。
7. task 创建 commit 与 pending 回写 commit 分开；列出 pending 迁移后询问是否提交。

## 边界

不写生产代码，不 start。已验证技术事实属于 findings，不从 pending 转 task。
