---
name: tasks-parallel
description: none
disable-model-invocation: true
---

# tasks-parallel

只读，不修改任何文件。只回答两件事：**当前第一批哪些 backlog task 可以同时开**，以及**后续 task 的显式依赖关系**。

## 输入

| 用户输入 | 候选范围 |
|----------|----------|
| 无参数 | 全部 `backlog` |
| 一个或多个 `tNNN` | 只分析这些；非 backlog 的记「跳过（已在基线）」或「跳过（已归档）」 |

基线固定为**进行中或尚未合并**：登记 worktree 中的 `active` / `blocked` task，以及尚未合并默认分支的 task 分支链。main 上的 task 状态可能落后于链尾，须以 worktree、`list/show --ref` 和 Git ancestry 为准。

## 步骤

1. **建基线**：

   ```bash
   scripts/task.py list --status backlog
   git worktree list --porcelain
   git branch --no-merged <default> --list 't[0-9]*_*'
   ```

   `<default>` 为主干分支名，与 `scripts/task.py` 的 `default_branch()` 口径一致：origin/HEAD → init.defaultBranch → 探测 main/master。main 的 list 只表示已合并状态与尚未进入链的 backlog。对每个登记 worktree直接读其 task 状态；对每条未合并 task 分支用 `scripts/task.py list --ref {branch}` 读累计状态，并用 `git merge-base --is-ancestor` 归并为链、找链尾。

   ```bash
   git diff --name-status -M -C <default>...{branch}
   git -C {worktree} diff --name-status -M -C
   git -C {worktree} diff --cached --name-status -M -C
   git -C {worktree} ls-files --others --exclude-standard
   ```

   rename/copy 同时计源路径与目标路径。基线占用集 = 各进行中 task 的已提交 diff ∪ worktree staged/unstaged/untracked 路径 ∪ spec 推导的待改路径。找不到归属的脏 worktree 单列并按冲突处理。

   分支已合并默认分支且 main 中对应 task 已归档，标「已完成保留分支」，不占用；分支未合并且其 ref 中能解析 task 状态，标「未合并批次链」，按链尾累计 diff 占用；ref 与 worktree 都找不到 task 归属才记「孤儿分支」。

2. **列候选**。从 main 的 backlog 集合中剔除 worktree或未合并链尾 ref 中已 active/blocked/done/dropped 的 tid；用户点名 tid 时也按该优先级判状态。候选为空则回复「当前没有 backlog task 可分析」，结束。

3. **推导每个候选的改动面**。读 `docs/tasks/{tid}_{slug}/spec.md` 契约区（范围、非范围）与上下文区（依赖与约束、blueprint 更新点），推导：

   | 维度 | 内容 |
   |------|------|
   | 代码路径 | 预计新增/修改的 `src/` `tests/` `scripts/` 文件或目录 |
   | 共享契约 | `schemas/`、schema/codegen 输入、`config/`、`docs/blueprint/` 与其他共享文档中会动的条目 |
   | 顺序依赖 | spec 上下文区写明「依赖 tNNN」「在 X 之后」的前置 |

   spec 范围写得太粗、推不出改动面的，判为**待澄清**，不放进可并发组。

   `docs/tasks_index.json` 与 `docs/archive/tasks_index.json` 是主仓协调点更新的 tracked 派生缓存；task worktree 的执行 commit 不修改它们。task 状态各写各的 `task.md`，不计入共享契约。

4. **判冲突**。任一成立即冲突：

   - 代码路径相交（同文件必冲突；同目录且都改结构性文件视为冲突，仅新增互不相干的文件不算）
   - 共享契约相交（同一 schema/codegen 输入、同一 migration 窗口、同一 config key、同一 blueprint 或共享文档条目）
   - 存在顺序依赖（被依赖方未 `done`）
   - 一方是**待澄清**

5. **选第一批**。先剔除与基线冲突、存在未满足依赖或待澄清的候选；从剩余候选中只选一个组，组内两两不冲突。
   - 多个组合都成立时，优先纳入能解锁更多后续 task、处于更长依赖链上游的 task。
   - 在不影响上述优先级的前提下，尽量增加首批并发数；仍并列时按 tid 升序。
   - 不把全部 backlog 拆成 A/B/C 多组，不预测第二批冲突分组；首批完成后应按最新 diff 重算。

6. **输出**。默认最多四段，每段一至数行；无内容的段落省略，不输出空表、改动面分解或逐项判定过程：

   ```markdown
   第一批并发：t005, t007, t009（基于 spec 估计）

   后续依赖：
   - t011 ← t005
   - t012 ← t011
   - t013 ← t005 + t012

   暂缓：t006（与首批 t005 冲突）；t008（spec 改动面待澄清）

   跳过：t001（done）
   ```

   `后续依赖` 只列 spec 明示的 task 依赖，使用 `下游 ← 前置`。没有显式依赖时省略该段。`暂缓` 只用一句话列出未进首批且不能用依赖关系表达的基线冲突、首批冲突或待澄清项，不展开文件路径与推导过程。用户追问理由时再展开。

## 边界

- 只读：不改代码、测试、文档、JSON；不建分支、不建 worktree、不切分支、不执行 task。
- 不自动调用 `tasks-run`；只输出第一批与依赖关系，由用户决定怎么跑。
- 推导基于 spec 文字，属**估计**；只在第一批行标一次，不重复解释。spec 范围不明确时判待澄清，不猜。
- 冲突存疑时按冲突处理（保守），不为提高并行度放宽判定。
- 默认不输出基线为空、分支为空、候选总数、文件改动面、分组依据、组间冲突矩阵、执行方式说明；用户追问时再展开。

## 完成

输出：第一批并发 tid；后续显式依赖；必要时一行暂缓与跳过。
