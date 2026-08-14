---
name: task-schedule
description: none
disable-model-invocation: true
---

# task-schedule

首次分析 backlog task 的依赖与并发冲突，把调度图写入 task front matter，再调用 `view` 输出全景。之后 `task.py view` / `task.py plan` 随状态变化持续刷新可跑集与本波链，无需再次调用本 skill（除非图本身要改）。

## 输入

|用户输入|分析范围|
|---|---|
|无参数|全部有效 `backlog` task|
|一个或多个规范 `tNNN`|重算指定 task；判冲突时仍比较全部已调度 backlog 与进行中 task|

仅接受仓库规范 tid。

## 步骤

1. **建有效状态基线**。

    ```bash
    .repo_template/scripts/task.py effective-status
    ```

    输出每 tid 的 `status` / `source`（`worktree` / `branch` / `main`）/ `read_at`（worktree 绝对路径或未合并分支名）。有效状态按 `AGENTS.md`「task 状态读取优先级」判定（archive 仅作历史回溯，不参与有效状态判定；`default_branch` 口径由脚本统一）。`source=worktree` → 在 `read_at` 目录读 task 状态与 diff；`source=branch` → 用 `task.py list/show --ref {branch}` 读取。主干中已被 worktree/ref 覆盖的 backlog 不进入分析范围。

    ```bash
    git diff --name-status -M -C <default>...{branch}
    git -C {worktree} diff --name-status -M -C
    git -C {worktree} diff --cached --name-status -M -C
    git -C {worktree} ls-files --others --exclude-standard
    ```

    rename/copy 同时计源、目标路径。基线占用集 = 进行中 task 已提交 diff、worktree staged/unstaged/untracked 路径、spec 推导待改路径。无法归属的脏 worktree 按冲突处理。

2. **列候选**。无参数时取全部有效 backlog；指定 tid 时只重算指定 backlog。非 backlog 单列跳过，不修改。候选为空则报告后结束。

3. **推导改动面与依赖**。读取每个候选 `spec.md`：契约区的范围/非范围，上下文区的依赖与约束、blueprint 更新点。推导：

|维度|内容|
|---|---|
|代码路径|预计新增/修改的 `src/` `tests/` `scripts/` 文件或目录|
|共享契约|`schemas/`、schema/codegen 输入、`config/`、blueprint 与共享文档条目|
|硬前置|spec 明示依赖的 task；写入 `depends_on`|

spec 太粗、存在多种合理解释或依赖无法确认时，不猜测，标记 `pending_clarification`。

4. **判冲突**。以下任一成立即写入 `conflicts_with`，不把冲突改写成依赖：

    - 代码文件相交；同目录都改结构性文件；
    - 同一 schema/codegen 输入、migration 窗口、config key、blueprint 或共享文档条目；
    - 与进行中 task 的实际/推导改动面相交。

    仅新增不同文件不算冲突。冲突存疑时保守标记。冲突边由 `task.py edit` 自动维护双向，不手工双写。

    禁令：与已有 `depends_on` 关系（含传递）的 task 对禁止写 `conflicts_with`——依赖已蕴含串行，冲突边冗余，`edit` 会以「冲突边与依赖路径冗余」拒绝。冲突的阻塞语义：仅当对端正在运行，或对端 backlog 且依赖已满足（dep-ready）且序号更小时，才压住本 task；被依赖阻塞的对端不构成互斥。

5. **落盘**。禁止直接编辑 `task.md` 或 index。对判断完整的每个候选执行一次完整覆盖：

    ```bash
    .repo_template/scripts/task.py edit {tid} \
      --depends-on "t001,t003" \
      --conflicts-with "t006,t008" \
      --schedule-status scheduled
    ```

    无依赖/冲突时对应参数传空字符串。无法判断时只执行：

    ```bash
    .repo_template/scripts/task.py edit {tid} --schedule-status pending_clarification
    ```

    若新冲突指向非可编辑 backlog，`edit` 会拒绝反向边写入；该候选改标 `pending_clarification`，报告阻断来源，不绕过状态机。

6. **校验并输出全景**。

    ```bash
    .repo_template/scripts/task.py view
    ```

    `invalid_graph` 时按错误中 tid 修正调度字段后重跑；禁止绕过。成功时原样报告 `.repo_template/scripts/task.py view` 输出，冲突阻塞行附带被阻塞 task 标题；另用一句话报告本次已调度、待澄清、跳过 tid。

7. **提示执行计划入口**（不手写链、不落盘）。写图与推链分离：本 skill 不生成执行命令剧本。成功校验后明确告知用户：

    ```bash
    .repo_template/scripts/task.py plan          # 本波并发链 + title + 可复制 /task-run
    .repo_template/scripts/task.py plan --copy   # 仅命令行
    .repo_template/scripts/task.py plan --serial # 全串行一条链
    .repo_template/scripts/task.py view --serve  # 看板（后端同源 plan）
    ```

    `plan` 只读、确定性、按当前状态重算：链跑完或汇流点解锁后**再跑一遍**即得下一批，无需重跑本 skill。冲突语义序（谁先建契约）若影响产物继承，须在 Step 5 写成 `depends_on`，不能只靠 plan 的序号 tie-break。

8. **询问提交**。列出本次改动的 `task.md`（含 peer 反向边）与两个派生 index，询问用户是否提交；同意后才 commit（维护期自成一个 commit，subject 含已写图 tid）。index 已入库且由 `edit` 重建，须随维护 commit 一起提交。用户不提交则保持工作区；但 worktree 从主干 HEAD 创建，未 commit 的调度字段不会进入 task 分支——须先 commit 再执行。

## 边界

- 本 skill 只写 task 调度字段和脚本派生 index；不改 spec、代码、测试或 blueprint。
- 不建分支/worktree，不调用执行类 skill，不执行、finish、drop 或合并 task。
- 执行计划唯一入口为 `task.py plan`（看板同源）；不手写链。
- `view` 输出状态全景与本波链摘要；执行由 `task-run` 承担（多会话手动并发各跑一段）。
- 新增、rewind、merge 后出现 `unscheduled` / `pending_clarification` 时，用户再次调用本 skill 重算相关 task。

## 完成

报告：已写图 tid、待澄清/跳过 tid；单列因已有依赖关系（含传递）而按 Step 4 禁令跳过的冲突对；随后原样输出 `.repo_template/scripts/task.py view` 结果，并提示用 `task.py plan` 取本波可复制命令。
