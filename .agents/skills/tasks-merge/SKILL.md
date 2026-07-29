---
name: tasks-merge
description: none
disable-model-invocation: true
---

# tasks-merge

把范围重叠或拆得过细的 **backlog** task 合并成一个。

## 前置

- 源 task **全部**须为有效状态 `backlog`，且未进入任何登记 worktree或未合并 task 分支链。main 中仍显示 backlog 但 worktree/链尾 ref 已为 `active` / `blocked` / `done` / `dropped` 时直接拒绝。
- `active` / `blocked` 已有分支与可能的 commit，提示用户先 `scripts/task.py rewind`（或明确 drop）。
- `done` / `dropped` 在 archive，不可合并。

## 步骤

1. **确认范围**。用户点名 tid 时用其列表；未点名时 `scripts/task.py list --status backlog`，读各 `docs/tasks/{tid}_{slug}/spec.md` 找范围重叠候选，向用户提出合并建议并等确认。不得自行决定合并哪些。

2. **校验有效状态**。先查 `git worktree list --porcelain` 与 `git branch --no-merged <default> --list 't[0-9]*_*'`；对未合并链用 `scripts/task.py show {tid} --ref {chain_tail}`。仅无 worktree/链覆盖且 main 中 `scripts/task.py show {tid}` 为 `backlog` 时合规。任一不合规即停止，报告 tid、有效状态与来源，不做部分合并。`<default>` 口径同 `default_branch()`。

3. **定目标**：

   | 情况 | 目标 |
   |------|------|
   | 合并后语义仍贴合某个源 task | 该 tid（默认取 tid 最小者）；保留其目录与 slug |
   | 合并后是新的、源 slug 都不贴切的范围 | `scripts/task.py add --title "..." --slug "..."` 新建，全部源 task 都作为被合并方 |

4. **合并文档**（写入目标 task 目录 `spec.md`）：
   - **契约区**：范围、非范围逐节合并去重；**验收标准取并集**，逐条保持可独立验证；可测试性声明合并。矛盾的 AC 停下问用户，不自行取舍。
   - **上下文区**：有意不测、测试策略、未知契约清单、风险与回退、依赖与约束、blueprint 更新点各取并集去重。未知契约保留 `UNVERIFIED-BLOCKING` / `UNVERIFIED-SPIKE` 分类；发现裸 `UNVERIFIED` 时停止，先补分类。
   - `task.md` 正文不写源 tid；合并来源只通过第 5 步 front matter `note` 记录。
   - 新建目标时由 `scripts/task.py add` 自动复制模板，不手工拷贝。
   - 合并后 `review_level` 优先取 `full`（任一源为 `full` 即 `full`），用 `scripts/task.py edit {目标tid} --review-level ...` 设置。

5. **更新目标条目**：

   ```bash
   scripts/task.py edit {目标tid} --title "{合并后标题}" --note-append "merged from t00X,t00Y"
   ```

   标题仍贴切时可只 `--note-append`。

6. **处置源 task**（目标之外的每个）：

   ```bash
   scripts/task.py drop {源tid} --reason "merged into {目标tid}"
   ```

   目录进 `docs/archive/tasks/`，状态留在其 `task.md` front matter，可追溯；tid 不复用（例外：`purge` 释放的误建 tid 可回收——从未开干、无历史，与 merge/drop 的「曾真实存在」不同）。

7. **自检**。目标 `spec.md` 契约区的 AC 覆盖全部源 AC 且无重复；上下文区无矛盾条目；无残留 `{...}` 占位符；`scripts/task.py list --status backlog` 只剩目标条目。

8. **询问提交**。列出目标目录改动、归档移动与两个派生 index，询问用户是否提交；同意后才 commit（维护期自成一个 commit，subject 含目标 tid 与源 tid）。index 已入库且由本流程重建，须随维护 commit 一起提交。用户不提交则保持工作区。

## 边界

- 只处理 `backlog`；不 `start` / `finish` / 实施。
- 不删源 task 目录（一律 `drop` 归档，不用 `purge`）。
- 源 AC 矛盾或范围疑似不该合并时停下问用户，不静默丢弃需求。

## 完成

汇报：目标 tid、被合并 tid 列表、合并后 AC 条数、下一步 `tasks-run`（或先 `tasks-preflight`）。
