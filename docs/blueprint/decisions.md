# 决策记录（ADR）

只记录已经确认、影响后续工作的非显然决策。追加新条目，不重写历史；决策被替代时，新条目通过“替代”字段引用旧编号。

每条结构：`## NNN 标题（YYYY-MM-DD）`，下接 `- 背景` / `- 选项` / `- 结论` / `- 替代` 四项；替代填旧编号，无则写「无」。

## 001 task 批次采用链式分支（2026-07-29）

- 背景：逐 task 从 main 分叉并立即合并会反复修改主仓，无法在整批任务完成后统一审阅和决定是否合并。
- 选项：每个 task 独立从 main 分叉并逐个合并；所有 task 共用一个分支；每个 task 从上一 task 分支创建并形成线性祖先链。
- 结论：采用链式 task 分支。首 task 基于批次开始时 main，后续 task 基于上一已完成分支；每个 task 保持独立执行 commit，commit 后清理 worktree并保留分支。整批完成后一次询问，只合并链尾分支；Git ancestry 是链关系权威，不增加 parent 或 batch 元数据。派生 task index 在链尾合并后统一重建并独立提交。
- 替代：无

## 002 取消批次期间 main 冻结约束（2026-07-29）

- 背景：001 的链式分支设计成立，但「批次执行期间 main 冻结」的隐含假设与单人多 session 并行模式冲突--链上跑 task 同时 main 上并行做建 task、docker fix、pending 维护是正常流。原约束把合并阶段的简洁性提前到执行阶段：`start` 的 main 祖先校验在 main 推进后阻断合法链式继续；`tasks-run` 把 main 推进列为整批硬停止。
- 选项：保留 main 冻结并加逃生口；完全取消冻结、合并阶段接受三方 merge；改用每个 task 独立从 main 分叉。
- 结论：取消 main 冻结。`start --base <task分支>` 只校验该分支是已完成且清理 worktree 的合法 task 分支，不再要求它以当前 main 为祖先。批次期间允许 main 并行推进，链与 main 的对齐推迟到合并阶段。合并用 `git merge --no-ff`（不 rebase）：链尾与 main 分叉时为三方 merge，冲突走 git 标准流程，`git merge --abort` 可干净回退。不重写历史，`diff_anchor` 与 task 分支引用保持稳定。
- 替代：无（001 链式拓扑仍成立，仅放松 main 冻结约束）
