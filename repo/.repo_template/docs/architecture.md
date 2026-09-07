# 模板仓库执行架构

task 工具链的执行拓扑、attempt、review 和合并授权。项目业务架构不写在这里。

## 执行拓扑

每个 task 使用独立分支和 worktree；每个 task 最终形成一个执行 commit。`task-run` 按用户给定顺序串成线性分支：

```text
main ── t001 ── t002 ── t003
```

后继以紧邻前一 task 分支为 `--base`，因此继承前置成果。`start` 同时检查：

- `depends_on` 的有效状态均为 done；
- 实际 base 包含每个依赖的实现 SHA；
- 分支和 worktree 没有被其它会话占用。

多会话并发由用户手动启动多条独立链。主仓写入不另建调度服务；Git 冲突由 Git 暴露并交用户或 Agent 处理。

## Attempt

执行实例身份为 `(tid, attempt, execution_id)`。task 状态描述业务生命周期，attempt 描述某次执行：

```text
start → reserve → task-work → terminal → report → cleanup
```

正式 task 状态只有 `backlog / active / done / dropped`。遇到阻塞时 task 保持 active，实施笔记记录原因；当前 attempt 写 `terminal=stopped` 和 `report=blocked`。条件补齐后 reserve 新 attempt 继续原 worktree。

`review_limit` 和 `verify_limit` 跨 attempt 持久化，新 attempt 不清零历史。用户批准追加轮次时，只能通过 `task.py limits` 增加绝对上限并记录原因。

## 调度图与计划

- `depends_on`：真实硬前置；有环或引用缺失时拒绝。
- `conflicts_with`：并发提示；声明可单向，读取时按无向关系处理。
- backlog 之间的冲突不按 tid 强行裁决先后；`plan` 将其分到不同建议链。
- 与 active task 冲突的 backlog 不作为当前并发链首。
- 串行执行不受 conflicts 限制。

`task.py view` 展示当前有效状态，依赖已满足但彼此冲突的 task 会同时列出并明确提示不要并行；`task.py plan` 负责把它们分到不同建议链。状态变化后按当前 front matter 重新计算；调度字段只有 `depends_on` 与 `conflicts_with`，不要求声明冲突反向边。

## Review 与验证

每个 task 在 finish 前必须通过：

1. 项目相关测试；
2. 项目定义的黑盒验证，未定义时明确记录；
3. 按 `review_level` 执行的独立 review。

Review 使用 finding ID、PASS/FAIL/INCOMPLETE、处置表、fix_ref 和 scope fingerprint。最终交付内容变化会使旧 PASS 失效。门禁不含 `withdraw_rate` 与自动 prompt hint。

## 合并

merge 必须获得用户明确授权。内容门禁要求 Git 2.38 或更高版本，并在创建 pending merge 前预检能力。单 task 和链式合并均先完成 exact gate，然后：

```text
git merge --no-ff --no-commit <branch-or-tail>
→ 重建并暂存派生 index
→ 运行合并后验证
→ 通过：--continue 重算自动合并 tree；仅原始冲突路径可含人工解决内容，随后创建 merge commit、写 integrated、删除分支
→ 失败：git merge --abort
```

Git 的 `MERGE_HEAD`、index 和 `git status` 是合并事务唯一真相。链式只 merge 链尾；Git ancestry 自然包含全部前置执行 commit。

## Goal

`task.py goal` 冻结 goal 队列并打印宿主可用的 `/goal` 行；`goal-check` 根据 task、attempt、handoff 和 worktree 输出 COMPLETE、STOPPED 或 INCOMPLETE。goal 只包装 task-run，不改变执行和 merge 授权语义。

## 写域

|阶段|写域|职责|
|---|---|---|
|task-work|当前 task worktree|实现、测试、黑盒、review、文档、handoff、finish、一个执行 commit|
|task-run|主仓控制面|start、attempt lifecycle、exact cleanup、队列推进|
|task-integrate|主仓 main|经授权准备 Git merge、合并后验证、commit、integrated 和分支清理|
