---
name: task-run
description: none
---

# task-run

按用户给定顺序串行执行固定 task 队列。单 task 实施由 `task-work` 完成，合并由 `task-integrate` 完成。

## 授权与队列

触发本 skill 即批准队列成员执行到各自执行 commit、attempt report 和 exact cleanup；不逐 task 询问 commit。merge 不在启动授权内，整链完成后只询问一次。

- 无参数：有效 backlog 与 active，tid 升序。
- 指定 tid：严格按输入顺序。
- done/dropped 不作为新执行成员。
- 队列启动后成员和顺序固定；依赖必须位于依赖者之前。

## 执行

对每个 tid：

1. 先观察 effective status、`task.py recovery`、worktree 和 ledger。已有现场就恢复，不重复 start 或 reserve。
2. 新 task：`start`，随后 `attempt reserve --executor inline`，保存 exact `(attempt, execution_id)`。
3. 在登记 worktree 调用 `task-work`，原样传递 identity。
4. 完成后在主仓写同一 identity 的 terminal completed、report done，并 exact cleanup。
5. 后继 task 以刚完成的前一分支为 `--base`。

阻塞时 task 保持 active：实施笔记记录原因和恢复入口，当前 attempt 写 terminal stopped + report blocked，保留 worktree并停止队列。用户补齐条件后直接 reserve 新 attempt继续；新 attempt不重置 review/verify 历史。用户批准追加轮次时先用 `task.py limits` 增加绝对上限。

恢复细节以 `task.py recovery {tid}` 和脚本门禁为准，不在 skill 复制每个中断阶段。current attempt 尚未 terminal 时不得 reserve 新 attempt。

## 合并

所有成员完成并 cleanup 后询问一次是否 merge。用户同意后调用 `task-integrate` 合链尾；不同意则保留分支。不 push。

## 停止

遇到以下情况保留现场并报告已完成、当前问题和剩余队列：

- preflight、测试、黑盒或 review 门禁无法闭环；
- 需要密钥、账号、外部环境或产品决策；
- merge 冲突无法可靠裁决；
- 工作区所有权或无关脏改动冲突；
- 用户指定停止。
