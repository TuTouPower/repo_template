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
start → reserve → claim(owner_id) → task-work → terminal → report → cleanup
```

执行权由 ledger 的 execution_claimed 事件表达，在 ledger 锁内比较并交换；owner_id 属于会话，identity 属于 attempt，两者不可混用。没有自动过期；跨会话接管须确认原会话停止并提供旧 owner。start、图维护、集成和模板同步共用 Git common dir 控制面锁，避免互相读写半途状态。

正式 task 状态只有 `backlog / active / done / dropped`。遇到阻塞时 task 保持 active，实施笔记记录原因；当前 attempt 写 `terminal=stopped` 和 `report=blocked`。条件补齐后 reserve 新 attempt 继续原 worktree。

`review_limit` 和 `verify_limit` 跨 attempt 持久化，新 attempt 不清零历史。用户批准追加轮次时，只能通过 `task.py limits` 增加绝对上限并记录原因。

## 调度图与计划

- `depends_on`：真实硬前置；有环或引用缺失时拒绝。
- `conflicts_with`：并发提示；声明可单向，读取时按无向关系处理。
- backlog 之间的冲突不按 tid 强行裁决先后；`plan` 将其分到不同建议链。
- 与 active task 冲突的 backlog 不作为当前并发链首。
- 串行执行不受 conflicts 限制。

backlog 合并通过 merge-backlog 收缩子图：外部前置并集进入目标，下游依赖改指并去重，组内边消除；先验证整图与可编辑性，再在持久文件事务中批量写入。中断用 graph-transaction 查询/恢复。start 拒绝实际 base 与当前任务图不一致，不能借未提交图变更绕过依赖。默认队列与 goal 共用 plan 的拓扑，串行本身满足冲突，不把冲突提示转为反向硬边。

`task.py view` 展示当前有效状态，依赖已满足但彼此冲突的 task 会同时列出并明确提示不要并行；`task.py plan` 负责把它们分到不同建议链。状态变化后按当前 front matter 重新计算；调度字段只有 `depends_on` 与 `conflicts_with`，不要求声明冲突反向边。

## Review 与验证

testing.md 是项目验证命令与显式策略唯一权威；verification_policy=project_configured 后才可 start/preflight。模板 contract 不代表业务验证。single 任务风险扩大时通过 review-upgrade 单调升 full，评审级别参与内容指纹，旧 PASS 失效。

每个 task 在 finish 前必须通过：

1. 项目相关测试；
2. 项目定义的黑盒验证，无黑盒时必须由项目策略明确选择并提供替代验收；
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

`task.py goal` 冻结 goal 队列并打印宿主可用的 `/goal` 行；`goal-check` 根据 task、attempt、handoff 和 worktree 输出 COMPLETE、STOPPED 或 INCOMPLETE。COMPLETE 仅表示 execution complete，awaiting_merge 和 integrated 单独输出。goal 只包装 task-run，不改变执行和 merge 授权语义。

## 写域

|阶段|写域|职责|
|---|---|---|
|task-work|当前 task worktree|实现、测试、黑盒、review、文档、handoff、finish、一个执行 commit|
|task-run|主仓控制面|start、attempt lifecycle、exact cleanup、队列推进|
|task-integrate|主仓 main|经授权准备 Git merge、合并后验证、commit、integrated 和分支清理|

## 模板分发与升级

初始化只从指定 Git commit 的产品树导出，拒绝 tracked secret 与本机状态；dirty 工作区不参与分发。

同步器直接从固定来源运行，consumer 显式指定，core 直接读取。准备阶段只写来源缓存；不自覆盖、不 prep。apply 在控制面锁内保存全部受管资产的 before-image 和持久日志，schema、入口、共享裁定、项目门禁与 state 推进纳入同一事务。失败恢复原始内容且保留备份；恢复失败不得吞错。成功后仍处于 applied，批准提交后 accept，放弃则 rollback。语义 AGENTS 合并先写 scratch，通过 agents-file 进入事务。

sync_manifest 是可入库版本基线；sync_policy 是显式共享保护决策；sync_state 只承载本机来源和私人 prompt。新 clone 依据 manifest 重新接入，本机 ledger 丢失仅从严格验证的 done Git/handoff 重建，active 不猜补。支持范围与灾难恢复步骤见 usage。

## 权威分工

AGENTS 保留长期不变量和写权；本文解释机制与取舍；usage 说明接口与恢复；SKILL 说明阶段动作、授权和停止；registry 是角色/调用图唯一事实源。持久事务日志和脚本检查决定机械状态，测试验证这些语义；派生入口与表格由生成器更新。
