---
name: tasks-parallel
description: none
disable-model-invocation: true
---

# tasks-parallel

只读，不修改任何文件。回答「已经在跑这些的前提下，backlog 里还有哪些能同时开」。

## 输入

| 用户输入 | 候选范围 |
|----------|----------|
| 无参数 | 全部 `backlog` |
| 一个或多个 `tNNN` | 只分析这些；非 backlog 的记「跳过（已在基线）」或「跳过（已归档）」 |

基线固定为**进行中**：全部 `active` + `blocked` task，以及仓库里已存在的 task git branch 及 worktree。

## 步骤

1. **建基线**：

   ```bash
   scripts/task.py list --status active
   scripts/task.py list --status blocked
   git branch --list 't[0-9]*_*'
   ```

   对每个已存在的 task 分支与登记 worktree 取实际占用文件。主干名取仓库默认分支（`main` / `master`，由 `scripts/task.py default_branch()` 探测；非 main 时替换）：

   ```bash
   git diff --name-status -M -C main...{branch}
   git -C {worktree} diff --name-status -M -C
   git -C {worktree} diff --cached --name-status -M -C
   git -C {worktree} ls-files --others --exclude-standard
   ```

   rename/copy 同时计源路径与目标路径。基线占用集 = 各进行中 task 的已提交 diff ∪ worktree staged/unstaged/untracked 路径 ∪ spec 推导的待改路径。找不到归属的脏 worktree 单列并按冲突处理。

   分支不在活跃索引时，先查归档 task 与合并状态：对应 task 已归档且分支已合并默认分支，则标「已完成待清理分支」，不算孤儿、不占用；否则记为「孤儿分支」，列出并当作占用。

2. **列候选**。`scripts/task.py list --status backlog`；用户点名 tid 时以点名为准。候选为空则回复「当前没有 backlog task 可分析」，结束。

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

5. **分组**。先剔除与基线冲突的候选；余下按两两冲突关系分组，同组内互不冲突。组内成员数以确实无冲突为准，不为凑并行强行分组。

6. **输出**：

   ```markdown
   ## 并发分析

   基线（进行中，已占用）：
   | tid | 状态 | 分支 | 占用范围 |
   |-----|------|------|----------|
   | t002 | active | t002_xxx | src/a/**, schemas/user.json |

   孤儿分支：无 / t009_yyy（索引与归档中无对应 task）
   已完成待清理分支：无 / t003_done（已归档且已合并，不占用）

   ### 可并发组

   | 组 | tid | 与基线冲突 | 组内互斥 |
   |----|-----|-----------|----------|
   | A | t005, t007 | 无 | 无 |

   ### 不可并发

   | tid | 原因 |
   |-----|------|
   | t006 | 与 t002 同改 schemas/user.json |
   | t008 | spec 范围未写清改动面，待澄清 |

   跳过：
   - t001：status=done，已归档

   结论：可并发组 A（t005, t007）；执行仍为每个 tid 各自 /tasks-run。`task.py start` 为每个 task 创建独立 worktree（`../{repo}_{tid}`）；并发 task 必须在各自 worktree 内实施。本 skill 不代为创建。
   ```

## 边界

- 只读：不改代码、测试、文档、JSON；不建分支、不建 worktree、不切分支、不执行 task。
- 不自动调用 `tasks-run`；只输出分组，由用户决定怎么跑。
- 推导基于 spec 文字，属**估计**，须在输出中标明；spec 范围不明确时判待澄清，不猜。
- 冲突存疑时按冲突处理（保守），不为提高并行度放宽判定。

## 完成

输出基线表、可并发组、不可并发原因、跳过列表与结论。
