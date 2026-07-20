# 决策记录（ADR）

只记录已经确认、影响后续工作的非显然决策。追加新条目，不重写历史；决策被替代时，新条目通过“替代”字段引用旧编号。

条目格式：

```markdown
## NNN 标题（YYYY-MM-DD）

- 背景：为什么需要决策
- 选项：考虑过什么
- 结论：选了什么，为什么
- 替代：旧决策编号；无则写“无”
```

## 001 review 证据源用 working tree 相对 diff_anchor（2026-07-21）

- 背景：单 task 单 commit 且 review 在 commit 前时，`git diff <diff_anchor>...HEAD` 常为空，导致虚假 clean review。
- 选项：A working tree `git diff <diff_anchor>`；B task 内 WIP commit + squash；C commit 后再 review。
- 结论：A。保留「一 task 一 commit + commit 前 review」，只改证据源。
- 替代：无

## 002 backlog 拆分即建目录并填写 spec/plan（2026-07-21）

- 背景：AGENTS 要求拆分建目录，tasks_index 曾写 backlog 不建目录，冲突。
- 选项：A 拆分即建目录；B active 才建目录；C 建目录不强制填。
- 结论：A，并要求验收标准非空；未填模板的 dropped backlog 可不归档。
- 替代：无

## 003 严格模式误报经原 reviewer 撤回（2026-07-21）

- 背景：无「无需修改」时误报会迫使改正确实现。
- 选项：A owner 举证 + 原 reviewer 撤回；B 保持全必修无出口；C owner 不采纳须用户批。
- 结论：A。默认仍须处置；撤回或用户裁决后可不改代码。
- 替代：无

## 004 用户 exception 不改写 reviewer verdict（2026-07-21）

- 背景：遗留/降级后若把 FAIL 改成 PASS，质量门禁语义崩溃。
- 选项：A verdict 不动 + tasks_index/task_report 记 exception；B 新 PASS_WITH_EXCEPTION；C 改写报告为 PASS。
- 结论：A。技术结论与风险接受分离。
- 替代：无
