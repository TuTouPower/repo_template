---
name: task-integrate
description: none
---

# task-integrate

将已完成的单 task 分支或线性链尾合入本地主干。调用本 skill 表示用户已明确批准本次 merge；不 push。

## 合并前门禁

- 当前在主仓主分支，且没有其它 merge 或已跟踪脏改动。
- 待合成员 task 为 done，worktree 已 exact cleanup。
- current attempt、terminal completed、report done、handoff identity、一个执行 commit、AC evidence 和最终 review PASS/scope 全部有效。
- 链式成员沿 first parent 线性连续，链尾包含全部成员。

脚本负责机械校验；skill 不复制 handoff schema和 transaction 内部算法。

## Git 原生事务

单 task 调用 `integrate {tid} --attempt {attempt} --execution-id {execution_id}`，identity 使用本次已 cleanup 的原值；链式只调用一次 `integrate-chain {tail_tid}`。工具执行：

```text
git merge --no-ff --no-commit <branch-or-tail>
→ 重建并暂存派生 index
→ 停在未提交 merge 状态
```

此时运行 `docs/blueprint/testing.md` 声明的合并后验证：

- 通过：以同一命令加 `--continue`；脚本重算 Git 自动合并 tree，拒绝非冲突文件在 merge 期间发生的额外内容变化，然后创建 merge commit、写 integrated 事件并删除已完全合入的分支；
- 失败：`git merge --abort`，保留 task 分支继续修复；
- 冲突：只在 Git 原始冲突路径中按双方语义解决并 `git add`，无法判断时停止请用户裁决；不要在主仓顺手修改非冲突文件。解决后先验证，再 `--continue`。

中断后用 `git status` 判断 pending merge；不再维护模板自研 transaction phase 或 transaction JSON。

## 常见冲突

- blueprint：合成一致语义，不机械拼接。
- task active/archive rename：保留归档侧。
- 派生 index：由脚本重建。
- 源码和测试：理解双方意图；不确定则停止。
- pending/finding 同号冲突：说明 ID 锁被绕过，停止。

## 完成

报告成员、merge commit、integrated、合并后验证和删除/保留的分支。不修改 task 内容，不启动或实施 task。
