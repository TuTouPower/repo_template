---
name: task-run
description: none
disable-model-invocation: true
---

# task-run

串行执行固定队列：一次跑一个 task，完成即合并，再跑下一个。串行是并发度为 1 的调度——当前会话同时承担 coordinator 与 worker 两个角色。并行执行用 `task-dispatch`。

角色边界与合并时机见 `AGENTS.md`「执行角色与合并时机」；单 task 执行流程见 `task-work`；合并流程见 `task-integrate`。

## 会话级前置授权

用户触发本 skill 表示批准队列内全部 task 执行至执行 commit 完成。合并主干须**一次性**取得授权，之后不再逐 task 询问：

```text
准备串行执行：{tid 列表}
每个 task 完成后立即：cleanup-worktree → merge --no-ff 进本地 {主干} → 重建 index → 合并后验证 → 删除该分支
不 push，不动远程。

授权本次会话按此执行？
```

禁止进入 plan mode（`EnterPlanMode` / `ExitPlanMode`），禁止开跑前重述 spec 已写明的内容征求同意。授权后直接开跑，只在「停止条件」列举的情况停下来问用户。

## 输入与固定队列

| 用户输入 | 队列 |
|----------|------|
| 无参数 | `backlog` ∪ `active`（tid 升序）；不含 blocked / done / dropped |
| 一个或多个 `tNNN` | 严格按用户输入顺序，只跑这些（须 backlog/active；含 blocked 则停止，请用户选择加轮/dropped） |
| 状态词 `backlog` 和/或 `active` | 只跑这些状态的全部，tid 升序 |
| 写了 `blocked` | `blocked` 不入队。先呈 blocked 选项请用户决策；用户当次明确继续后，再跑其余可跑 tid |

`done` / `dropped` 永不重新入队。CLI 一次只能带一个 `--status`，默认队列由两次 list 合并去重。开始修改状态前固定 tid 与顺序。

依赖被前置 task 满足的 backlog 可入队，只要前置排在其前。队列内有 `conflicts_with` 边不影响串行执行——串行本就不并发。

## 队列循环

每个 tid 依次走完整两段，再进入下一个：

```text
task-work {tid}        → 执行至执行 commit，交出 {branch} @ {sha}
task-integrate {tid}   → cleanup-worktree → merge → 重建 index → 验证 → 删分支
```

1. 一次只跑一个 tid；禁止并行多 task（单 task 内可派 subagent）。
2. 每个 task 合并完成后播报一行 `{tid} merged @ {sha} · verify PASS`，不再询问。
3. 下一个 task 的 `start` 从合并后的主干 HEAD 扇出，因此自动继承前一个 task 的成果。
4. 当前 task `blocked` → 队列停止，不自动跳下一个（除非用户显式要求 drop/移出队列）。
5. 「循环」= 本 skill 内串行推进，不是后台常驻。

## 恢复

中断后按以下优先级判断状态：

1. 已登记 task worktree：进入该 worktree，用 `scripts/repo_template/task.py show <tid>` 读 active/blocked 与未提交证据，回 `task-work` 对应步骤。
2. 未合并 task 分支：用 `scripts/repo_template/task.py show <tid> --ref <branch>` 读分支中状态；已 `done` 则直接走 `task-integrate`。
3. 主干：尚未进入执行的 backlog task 从队列头重新开始。

已合并的 task 在主干中即 `done`，不重复执行。

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

汇报：固定队列、已完成并合并的 tid 及各自 merge commit、当前主干 HEAD、停止原因与剩余队列（若有）、遗留 worktree 与分支（若有）。
