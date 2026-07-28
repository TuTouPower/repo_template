# 工作流反思：并行 task 的分支/worktree 纪律缺失

源自 2026-07-28 在 omni_media 项目并行执行 6 个 backlog task（t116–t130，pending f004–f015 拆分）时的实际阻塞事件。前序反思见 `workflow_reflection_1.md` 至 `_5.md`。

## 约定与现实的落差

并行方案约定（已写入 AGENTS.md 与 conventions.md）：每个 task 一个 `{tid}_{slug}` 分支，并行执行用 worktree（一 task 一 worktree，worktree 里检出该 task 分支），主目录只跑串行工作。

实际连续两次被打破：

1. **t129**：未开 worktree，在主目录切了 `t129_dev_experience` 分支干活。至少有分支隔离，但主目录被占导致 t128 合并阻塞。
2. **t130**：worktree、分支都没开。agent 跑了 `task.py start t130` 就直接在 **main 分支的工作区**裸写代码（红阶段写测试），t127 合并因此无法执行（merge 要求工作区干净）。

## 根因分析

1. **`task.py start` 制造「分支已建」的错觉**。它只是把 JSON 里的 `branch` 字段填成 `{tid}_{slug}`，并不执行 `git checkout -b`。工作流 Step 1 把建分支+校验交给 agent 自觉，没有任何强制。
2. **worktree 不是「切换」语义**。它是独立目录（`.claude/worktrees/xxx`），agent 必须进入该目录工作才算用上；在主目录敲命令永远在 main 上。这对人和 agent 都是易错点。
3. **`tasks_index.json` 是并行写的共享热点**。每个 task 的 start/finish/drop 都改同一个 JSON，导致：
   - 每个 task 分支都携带对它的修改，合并时**必然冲突**（本轮 4 次合并次次中）；
   - 诱发手工编辑——本次还发生了文件被清空后手工「恢复」但 status 被伪造成 active 的事故，而 `task.py` 没有 active→backlog 的回退命令，只能靠授权手修。
4. **合并带 migration 的分支有隐藏动作**：t128 合入后 4 个旧测试报 `prisma.note undefined`，原因是 prisma client 未随新 schema 重新生成，`npx prisma generate` 后恢复。该步骤不在任何清单里。

## 改进建议

1. **硬规则**（AGENTS.md）：禁止在 main 分支工作区直接改代码；Step 1 必须二选一——主目录串行则 `git checkout -b {tid}_{slug}`，并行则建 worktree 并**在 worktree 目录内**工作；`task.py start` 后必须 `git branch --show-current` 校验一致。
2. **工具强制**：`task.py start` 增加校验——当前分支与 `{tid}_{slug}` 不一致时拒绝或警告；更进一步可由脚本直接建分支/worktree，消除自觉环节。
3. **索引去共享写**：考虑每 task 一个索引条目文件（如 `docs/tasks_index.d/{tid}.json`），`list`/`finish` 由脚本汇总/移动，消除并行写冲突与手工编辑诱因。成本是脚本改造与历史迁移，需权衡。
4. **合并检查清单**：合并含 prisma migration 的分支后，把 `npx prisma generate`（必要时 `migrate deploy`）列为固定动作；merge 前确认主目录干净（无其他 task 的未提交工作）。

## 经验固化情况

- 第 1 条（并行黑盒资源派生：PW_PORT/bucket/用户前缀/migration 串行窗口）已写入 `docs/blueprint/conventions.md` 并在 AGENTS.md 加指针。
- 第 2、3 条（脚本强制与索引改造）与第 1 条的硬规则文本，本文撰写时均未落地。
