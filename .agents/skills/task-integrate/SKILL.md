---
name: task-integrate
description: none
disable-model-invocation: true
---

# task-integrate

把已完成 task 的分支合并进本地主干。本 skill 是 **coordinator 角色**：主仓唯一写者。角色边界见 `AGENTS.md`「执行角色与合并时机」。

## 前提

- 用户已给出会话级合并授权（`task-run` / `task-dispatch` 在启动时取得；单独调用本 skill 时视为用户当次授权）。
- task 在自身分支 tip 中为 `done` 或 `dropped`。
- 该 task 的 worktree 已清理。

## 输入

一个或多个 `tNNN`。多个时按给定顺序逐个处理，前一个失败即停，不继续后续。

## 步骤

1. **清理 worktree**（尚未清理时）：

   ```bash
   scripts/repo_template/task.py cleanup-worktree {tid}
   ```

2. **合并**：

   ```bash
   scripts/repo_template/task.py integrate {tid}
   ```

   脚本按序执行：校验分支 tip 终态与 worktree 已清理 → `merge --no-ff` → `list --rebuild` → 单独提交两个派生 index → 删除已完全合入的分支。已合入的分支跳过 merge，幂等。

3. **冲突处置**。脚本停在冲突处并列出文件时，按语义解决——不是取一侧了事：

   | 冲突位置 | 处置 |
   |----------|------|
   | `docs/blueprint/` | 真语义冲突。读双方意图后合成一致表述，不叠加两段 |
   | `docs/specs_index.md` | 两侧各加一行；保留双方，按 slug 排序 |
   | `docs/tasks_index.json` | 派生缓存。取任一侧解决冲突即可，第 2 步会重建覆盖 |
   | 源码 / 测试 | 读双方改动意图；无法确定时停止，报告给用户 |

   条目化账本（`docs/pending/`、`docs/findings/`）为一条目一文件，正常不冲突；若出现同号不同文件，说明取号锁被绕过，停止并报告。

   解决并 `git add` 后：

   ```bash
   scripts/repo_template/task.py integrate {tid} --continue
   ```

   放弃合并用 `git merge --abort`，分支与主干均不变，报告给用户裁决。

4. **合并后验证**。执行 `docs/blueprint/testing.md` 声明的合并后动作与验证。失败则停止后续全部 integrate，报告主干实际状态（已含 merge commit 与 index commit）交用户裁决，不盲目重试或回退。

## 边界

- 只写主仓，只在主干分支执行。
- 不修改 task 内容、spec、代码或测试；发现需改动时停止并报告。
- 不 `git push`、不动远程分支。
- 不启动 task、不执行 task。

## 完成

逐个报告：`{tid}` → merge commit、index commit、分支删除或保留、合并后验证结果。有冲突时报告冲突文件与处置方式。
