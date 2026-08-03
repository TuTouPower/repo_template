---
name: task-dispatch
description: none
disable-model-invocation: true
---

# task-dispatch

并行调度 task：启动多个 worktree 交给 worker agent，接收完成汇报后立即合并，解锁的 task 立即补位。本 skill 是 **coordinator 角色**：主仓唯一写者，自身不执行 task。角色边界见 `AGENTS.md`「执行角色与合并时机」。

前置：候选 task 的调度图已由 `task-schedule` 写入并提交。

## 会话级前置授权

启动前**一次性**向用户说明并取得授权，之后不再逐 task 询问：

```text
准备调度：{tid 列表}（并发上限 N）
每个 task 完成后立即：cleanup-worktree → merge --no-ff 进本地 {主干} → 重建 index → 合并后验证 → 删除该分支
{依赖 task 列表} 会在其前置合并后自动解锁补位。
不 push，不动远程。

授权本次会话按此执行？
```

授权后只在「停止条件」列举的情况停下来问用户。

## 输入

| 用户输入 | 调度范围 |
|----------|----------|
| 无参数 | `task.py view` 输出的全部可跑 task，随解锁持续补位 |
| 一个或多个 `tNNN` | 只跑这些及其中被解锁的，跑完即止 |

并发上限默认 3，用户可指定。

## 调度循环

```text
view → 取可跑 task → start（并发上限内）→ 交 worker
                 ↑                              ↓
                 └── integrate ← 收到 {tid}: {branch} @ {sha}
```

1. **算可跑集**：

   ```bash
   scripts/repo_template/task.py view
   ```

   取「待运行」分组中无依赖阻塞、无冲突阻塞的 task。`view` 用主干视角判 done，合并即时发生，因此解锁判据准确。

2. **启动**（主仓，并发上限内）：

   ```bash
   scripts/repo_template/task.py start {tid}
   ```

   逐个 start，记录每个 tid 的 worktree 路径。`start` 恒从当前主干 HEAD 扇出。

3. **派发**。每个 task 交一个 worker agent，只传：tid、worktree 绝对路径、调用 `task-work`。worker 边界由 `task-work` 自身声明，派发消息不复述。

4. **收汇报**。worker 交回 `{tid}: {branch} @ {sha}` 后立即进入第 5 步；不等其他 worker。

5. **合并**。调用 `task-integrate {tid}`。完成后播报一行：

   ```text
   {tid} merged @ {sha} · verify PASS
   ```

6. **补位**。回第 1 步重算可跑集；有新解锁且未达并发上限则继续 start。全部 task 终态且无可跑项时结束。

## 并行纪律

- 同一时刻一个 tid 只有一个 worker。
- 存在 `conflicts_with` 边的 task 不同时启动。
- worker 之间不通信、不互相读 worktree。
- 合并串行：一次只处理一个 integrate，不并发写主干。
- 某个 task `blocked` 只影响它自己与其 `depends_on` 下游；其余继续调度。

## 停止条件

遇任一即停下询问用户，其余在跑的 worker 不强制中断（等其自然完成或由用户决定）：

- merge 冲突需用户裁决（`task-integrate` 已尝试语义解决仍无法确定）。
- 合并后验证失败——停止全部后续 integrate 与 start。
- task `blocked`（呈加轮 / dropped 选项）。
- 要跑授权范围之外的 task（范围扩大须重新授权）。
- 需用户提供密钥、环境、产品决策等不可替代输入。
- 主仓出现与调度无关的脏改动且无法安全隔离。

## 完成

汇报：授权范围、已合并 tid 及各自 merge commit、blocked 或未跑 tid 与原因、当前主干 HEAD、遗留 worktree 与分支（若有）。
