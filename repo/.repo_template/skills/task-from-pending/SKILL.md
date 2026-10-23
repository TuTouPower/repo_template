---
name: task-from-pending
description: 把 docs/pending/todo/ 中仍有效、值得实施的条目聚合成 backlog task，并归档已转办条目。用户要求从 pending 立项或批量转办待办时调用。
disable-model-invocation: true
metadata:
  opencode/autoinvoke: "false"
---

# task-from-pending

将 `docs/pending/todo/` 中仍有效、值得实施的条目聚合成 backlog task，并归档已转办条目。

## 组合契约

仅用户显式授权入口或 registry.json 已登记父流程可执行。本流程作为子流程时，由父流程直接读取本 canonical `SKILL.md` 并执行正文，不调用宿主原生 Skill 工具；原样传递参数、exact identity、owner_id 和写域。父流程授权不扩大，缺少所需授权时停止。调用关系以 `.repo_template/skills/registry.json` 为权威。

## 流程

1. 确定用户指定或 todo 中的候选；parked 只有用户明确复活后才处理。
2. 核实当前代码、spec 和测试：问题是否仍存在、描述是否过时、是否已有等价 effective task。不存在的条目按验证结论闭环，不建 task。
3. 普通条目按主题和交付面去重聚合。未分析的 bug 调用 `bug-to-pending analysis-only`，取得可验证根因、同类位点和补测方向后再决定范围。
4. 范围有争议、影响很小或需要产品取舍时询问用户；“全部捞”仍表示有效且非重复的聚合结果，不是一条 pending 一个 task。
5. 调用 `task-create` 创建并验证 task，spec 写来源 `pNNN` 和当前核实结论。
6. 用 `pending.py archive {pNNN} --resolution transferred --fix-ref {tid} --reason "已核实范围并转交该 task" --write` 归档已转 task 的条目（归档表示已转办，修复状态继续由关联 tid 表达）；不存在、已解决或重复条目分别用 `--resolution obsolete/resolved/duplicate --reason ...`，`--fix-ref` 仅作可选引用，备注必须说明依据与去向；直接在 main 完成也可无 ID 归档，不伪造 task 引用。
7. task 创建 commit 与 pending 回写 commit 分开；列出 pending 迁移后询问是否提交。

## 边界

不写生产代码，不 start。已验证技术事实属于 findings，不从 pending 转 task。
