---
name: task-run
description: none
disable-model-invocation: true
---

# task-run

串行执行固定队列（链式拓扑）。合并时机与分支形态见 `AGENTS.md`「职责分工与合并时机」。

单 task 执行流程见 `task-work`；合并流程见 `task-integrate`。

## 会话级授权

用户触发本 skill 即批准队列内全部 task 执行至各自执行 commit、terminal/report 与 exact cleanup 完成；不得逐 task 询问 commit。主干合并授权可在启动时一并取得，也可延后到整条链已完成时取得：

- 启动时已授权合并：最终直接执行 `integrate-chain`，不重复询问。
- 启动时尚未授权合并：先完整跑完链上所有 task 和 cleanup；仅在最终首次调用 `integrate-chain` 前询问一次。
- 未获合并授权时保留已清理的链分支，不得提前 merge；授权只覆盖本次固定队列的一次链尾合并与后续可恢复 finalize。

启动时需要说明固定队列、每 task 一个执行 commit、链式 `--base` 继承、最终只合链尾一次、不 push。若当时请求合并授权，可使用：

```text
准备串行执行：{tid 列表}（链式）。每个 task 一个执行 commit；中间只 exact cleanup；整链完成后 integrate-chain 一次 merge --no-ff 链尾，不 push。
是否同时授权本次会话最终合并主干？
```

禁止进入 plan mode（`EnterPlanMode` / `ExitPlanMode`），禁止开跑前重述 spec 已写明的内容征求同意。执行授权已由 skill 调用给出；只在最终尚缺合并授权或「停止条件」列举的情况停下来问用户。

## 输入与固定队列

| 用户输入 | 队列 |
|----------|------|
| 无参数 | `backlog` ∪ `active`（tid 升序）；不含 blocked / done / dropped |
| 一个或多个 `tNNN` | 严格按用户输入顺序，只跑这些（须 backlog/active；含 blocked 则停止，请用户选择加轮/dropped） |
| 状态词 `backlog` 和/或 `active` | 只跑这些状态的全部，tid 升序 |
| 写了 `blocked` | `blocked` 不入队。先呈 blocked 选项请用户决策；用户当次明确继续后，再跑其余可跑 tid |

`done` / `dropped` 永不重新入队。CLI 一次只能带一个 `--status`，默认队列由两次 list 合并去重。开始修改状态前固定 tid 与顺序。

依赖被前置 task 满足的 backlog 可入队，只要前置排在其前。队列内有 `conflicts_with` 边不影响串行执行——链式拓扑本就不并发。

## 链式拓扑

task 按执行顺序成链：

```text
主干 ── t001 ── t002 ── t003 ──► 全部完成后 merge 链尾
```

每个 task 从上一个已完成 task 的分支创建（`--base`），因此自动继承前一个 task 的成果。`depends_on` 边要求被依赖者排在依赖者之前。多会话手动并发时各跑独立链，无自动调度器。

## 队列循环

每个 tid 依次走一次队列循环。`attempt reserve` 返回的整数 `attempt` 与字符串 `execution_id` 是本次执行的 exact identity，必须原样传给 `task-work`、terminal、report 与 cleanup：

```text
t001: start t001
      → attempt reserve t001 --executor inline
      → task-work(t001, attempt, execution_id)
      → attempt terminal ... --status completed|failed|stopped
      → attempt report ... --status done|blocked|failed
      → cleanup-worktree t001 --attempt N --execution-id ID（分支保留）
t002: start t002 --base t001_分支
      → reserve inline → task-work(identity) → terminal → report → cleanup exact
t003: start t003 --base t002_分支
      → reserve inline → task-work(identity) → terminal → report → cleanup exact
   ↓ 全部成员完成且已 cleanup；若尚无 merge 授权，此时只询问一次
integrate-chain t003 → aggregate gate → 一次 merge 链尾 → 重建 index → exact integrated 原子批量写入
   ↓ transaction=awaiting_verification，分支保留
执行合并后验证 → integrate-chain t003 --continue → 删整条链分支并清除 transaction
```

命令顺序固定：

1. 首 task 执行 `task.py start {tid}`；后继执行 `task.py start {tid} --base {前一 task 分支}`。
2. 紧接着执行 `task.py attempt reserve {tid} --executor inline`。reserve 原子返回 identity，inline attempt 直接进入 `running`；当前 attempt 未 terminal 时禁止再次 reserve。
3. 调用 `task-work` 时 `attempt` 与 `execution_id` 均必填。每个 task 只产生一个执行 commit。
4. `task-work` 返回后先写 executor 终态：正常返回（包括业务 `blocked`）写 `terminal --status completed`；执行器/环境失败写 `failed`，用户或宿主停止写 `stopped`。再以同一 identity 写 `report --status done|blocked|failed`。
5. 只有 `terminal completed` 且业务 `report done` 的成员才执行 `cleanup-worktree {tid} --attempt {N} --execution-id {ID}`。中间只 cleanup，**不合并**；分支保留并成为下一个 task 的 `--base`。
6. 队列全部成员完成后，确认已有会话级 merge 授权；若启动时未取得，只在此处询问一次。随后调用 `integrate-chain {链尾 tid}`。它从控制面聚合各成员 exact identity，不接受 `--attempt` / `--execution-id`，主干只进一次链尾 merge commit；命令完成 index 与幂等批量 integrated 后停在 `awaiting_verification`，保留分支和 transaction。
7. 执行合并后验证。通过后调用同一 `integrate-chain {链尾 tid} --continue`，删除整条链分支并清除 transaction；验证失败则停止，保留可恢复证据，不调用最终 continue。
8. 当前 task `blocked` → 队列停止，不 cleanup、不自动跳下一个；保留现场等待用户决定。
9. 「循环」= 本 skill 内串行推进，不是后台常驻。

## 恢复

中断后先用 `task.py ps --all` 与 `task.py ledger tail --tid <tid>` 恢复该 task 的 current exact identity，再按以下优先级判断仓库状态：

1. 当前 identity 为 `running` 且已登记 task worktree：进入该 worktree，用 `scripts/repo_template/task.py show <tid>` 读 active/blocked 与未提交证据，以原 `attempt` / `execution_id` 回 `task-work` 对应步骤；禁止另行 reserve。
2. task 分支已有执行 commit 与 `handoff.json`，但 terminal/report/cleanup 未闭环：核对 handoff identity 后，按原 identity 补 `terminal → report → cleanup-worktree`，不创建新 attempt。
3. 未合并 task 分支已 `done` 且 exact cleanup 完成：记录其分支为下一个 `--base`，继续队列下一个 backlog。
4. `.git/repo-task/integrate-chain.json` 存在：读取 `phase`。`prepared` 且有冲突时先解决并 `git add`；`merged` / `indexed` 表示 merge 已发生但收尾未闭环；以上均以原 tail 执行一次 `integrate-chain {链尾 tid} --continue` 恢复到 `awaiting_verification`。`awaiting_verification` 必须先完成合并后验证，验证通过后再执行一次同命令删除分支并清除 transaction。
5. 主干中尚未进入执行的 backlog task：从队列头执行 `start → reserve inline`。

已合并的 task 在主干中即 `done`，不重复执行。current attempt 未 terminal 时绝不 reserve 新 attempt。

**view 的主干视角限制**：`task.py view` 的 `done_set` 用 `main_done_set`（已合入主干才算 done）。链上已完成但未合 main 的前置在 view 中显示为「依赖阻塞」，对链式恢复无意义——链式恢复时按**分支 tip 与 exact attempt 闭环**判依赖（前置分支 done 且已 cleanup 才可作 `--base`），不依赖 view 的解锁判断。

## 停止条件

停止条件仅限本节列举的可验证事件，不得自行扩充（如 task 规模大、SPIKE 多、主观估计上下文将耗尽）。是否继续以系统提供的上下文占用客观数据为准，不以主观工作量感推断；数据不明确时默认继续——中断代价确定，耗尽风险由系统压缩与中间态恢复兜底。

遇任一即停，不自动跳当前 task 跑下一个：

- `preflight` FAIL 且无法在本 task 内修复。
- 当前 task `blocked`（呈加轮 / dropped 选项）。
- merge 冲突需用户裁决。
- 合并后验证失败——停止队列后续全部执行。
- 需用户提供密钥、环境、产品决策等不可替代输入。
- 环境/权限/外部依赖阻断；基础设施连续失败 → `block --reason infra`。
- 用户限制本次终点。
- 工作区有与本队列冲突的无关脏改动且无法安全隔离。

停止时保留当前 worktree 与分支；汇报已完成 tid、当前阻塞、剩余队列与恢复入口。

## 完成

汇报：固定队列；各 tid 的 `(attempt, execution_id)` 与执行 commit；逐成员 cleanup 结果；链尾分支与 HEAD；`integrate-chain` 事务结果、main merge commit、各成员 exact integrated 与 index 维护结果；删除的链分支；停止原因与剩余队列（若有）；遗留 worktree、分支或事务 snapshot（若有）。
