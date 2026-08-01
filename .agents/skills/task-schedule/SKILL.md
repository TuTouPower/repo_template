---
name: task-schedule
description: none
disable-model-invocation: true
---

# task-schedule

首次分析 backlog task 的依赖与并发冲突，把调度图写入 task front matter，再调用 `view` 输出全景。后续批次直接运行 `scripts/repo_template/task.py view`，无需再次调用本 skill。

## 输入

| 用户输入 | 分析范围 |
|----------|----------|
| 无参数 | 全部有效 `backlog` task |
| 一个或多个规范 `tNNN` | 重算指定 task；判冲突时仍比较全部已调度 backlog 与进行中 task |

仅接受仓库规范 tid。

## 步骤

1. **建有效状态基线**。

   ```bash
   scripts/repo_template/task.py list --status backlog
   git worktree list --porcelain
   git branch --no-merged <default> --list 't[0-9]*_*'
   ```

   `<default>` 与 `scripts/repo_template/task.py` 的 `default_branch()` 一致。状态优先级：登记 worktree → 未合并 task 分支 ref → main → archive。对登记 worktree 读取其中 task 状态与 diff；对未合并分支用 `scripts/repo_template/task.py list/show --ref {branch}` 和 Git ancestry 归并链尾。main 中已被 worktree/ref 覆盖的 backlog 不进入分析范围。

   ```bash
   git diff --name-status -M -C <default>...{branch}
   git -C {worktree} diff --name-status -M -C
   git -C {worktree} diff --cached --name-status -M -C
   git -C {worktree} ls-files --others --exclude-standard
   ```

   rename/copy 同时计源、目标路径。基线占用集 = 进行中 task 已提交 diff、worktree staged/unstaged/untracked 路径、spec 推导待改路径。无法归属的脏 worktree 按冲突处理。

2. **列候选**。无参数时取全部有效 backlog；指定 tid 时只重算指定 backlog。非 backlog 单列跳过，不修改。候选为空则报告后结束。

3. **推导改动面与依赖**。读取每个候选 `spec.md`：契约区的范围/非范围，上下文区的依赖与约束、blueprint 更新点。推导：

   | 维度 | 内容 |
   |------|------|
   | 代码路径 | 预计新增/修改的 `src/` `tests/` `scripts/` 文件或目录 |
   | 共享契约 | `schemas/`、schema/codegen 输入、`config/`、blueprint 与共享文档条目 |
   | 硬前置 | spec 明示依赖的 task；写入 `depends_on` |

   spec 太粗、存在多种合理解释或依赖无法确认时，不猜测，标记 `pending_clarification`。

4. **判冲突**。以下任一成立即写入 `conflicts_with`，不把冲突改写成依赖：
   - 代码文件相交；同目录都改结构性文件；
   - 同一 schema/codegen 输入、migration 窗口、config key、blueprint 或共享文档条目；
   - 与进行中 task 的实际/推导改动面相交。

   仅新增不同文件不算冲突。冲突存疑时保守标记。冲突边由 `task.py edit` 自动维护双向，不手工双写。

5. **落盘**。禁止直接编辑 `task.md` 或 index。对判断完整的每个候选执行一次完整覆盖：

   ```bash
   scripts/repo_template/task.py edit {tid} \
     --depends-on "t001,t003" \
     --conflicts-with "t006,t008" \
     --schedule-status scheduled
   ```

   无依赖/冲突时对应参数传空字符串。无法判断时只执行：

   ```bash
   scripts/repo_template/task.py edit {tid} --schedule-status pending_clarification
   ```

   若新冲突指向非可编辑 backlog，`edit` 会拒绝反向边写入；该候选改标 `pending_clarification`，报告阻断来源，不绕过状态机。

6. **校验并输出全景**。

   ```bash
   scripts/repo_template/task.py view
   ```

   `invalid_graph` 时按错误中 tid 修正调度字段后重跑；禁止绕过。成功时原样保留脚本固定输出，另用一句话报告本次已调度、待澄清、跳过 tid。

7. **询问提交**。列出本次改动的 `task.md`（含 peer 反向边）与两个派生 index，询问用户是否提交；同意后才 commit（维护期自成一个 commit，subject 含已写图 tid）。index 已入库且由 `edit` 重建，须随维护 commit 一起提交。用户不提交则保持工作区；但未 commit 时 `task-run` 的 `start` 会因脏工作区拒绝执行，且 worktree 从 main HEAD 创建、未 commit 的调度字段不会进入链——须先 commit 再 run。

## 边界

- 本 skill 只写 task 调度字段和脚本派生 index；不改 spec、代码、测试或 blueprint。
- 不建分支/worktree，不调用 `task-run`，不执行、finish、drop 或合并 task。
- `view` 只输出 task 全景（运行中/待运行分组/已结束），不执行 task。用户自行把同批 tid 交给多个 Agent 分别调用现有 `task-run`。
- 新增、rewind、merge 后出现 `unscheduled` / `pending_clarification` 时，用户再次调用本 skill 重算相关 task。

## 完成

报告：已写图 tid、待澄清/跳过 tid；随后原样输出 `scripts/repo_template/task.py view` 结果。
