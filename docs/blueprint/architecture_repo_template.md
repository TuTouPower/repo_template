# 模板仓库执行架构

task 工具链的执行拓扑、attempt 生命周期与合并授权。项目自身模块架构见 `architecture.md`。

## 执行拓扑

链式（`task-run`）描述 **branch topology**；并发只发生在用户维度——多会话各自跑一条链，无自动调度器。attempt 控制面与 exact identity `(tid, attempt, execution_id)` 仍统一使用。`execution_id` 是执行 provenance；`executor` 为 `inline`。

**链式**。task 按执行顺序一个串一个成链，每个从上一个已完成 task 的分支创建（`--base`）。每个 task 一个执行 commit，中间只清理 worktree；全部完成后由 `integrate-chain` 一次性把链尾合并回主干，主干只进一次 merge commit。

```text
主干 ── t001 ── t002 ── t003 ──► 全部完成后 merge 链尾
```

**手动并发**。无自动调度器。用户用 `task.py view --serve` 看依赖/冲突/运行中状态后，在多个会话各自 `/task-run tNNN,...` 一段。`start` 加调度门：依赖未完成（完成口径=`done`/`dropped`，不要求已合并主干）硬拒，冲突方正在运行只警告。多会话同时写主仓不互斥——合并撞车由 git 报错、人来收场，index 是派生缓存、撞了重建。

```text
        主干 ──┬── 会话 A：t001 ── t003 ──► 链尾 merge
               └── 会话 B：t002       ──► 链尾 merge
```

## 职责分工

同一会话内按阶段区分写域，两阶段写域互不重叠：

| 阶段 | 唯一写域 | 职责 | skill |
|------|---------|------|-------|
| 实施 | 当前 task worktree | 接收必填 `attempt` / `execution_id`，实施、测试、黑盒、review、finish、一个执行 commit；只写精确 identity 的 `handoff.json` 并交出 `{tid}: {branch} @ {sha}` | `task-work` |
| 调度合并 | 主仓 | `start`；reserve attempt；写 exact terminal/report；以 exact identity cleanup；单 task `integrate` 或链式 `integrate-chain`；派生 index、分支清理、合并后验证 | `task-run` / `task-integrate` |

实施阶段不合并任何分支、不重建 index、不 push、不删分支、不清理 worktree、不询问是否合并主干，也不写 attempt 控制面；唯一交接写入是本 task 分支中的 `handoff.json`。`task-run` 在同一会话依次走完调度合并与实施两个阶段——调度阶段调控制面命令，实施阶段进 worktree 调 `task-work`。多会话手动并发时，每个会话在自己的 worktree 内完整跑链，会话间不互斥——合并撞车由 git 报错、人来收场，index 是派生缓存、撞了重建。

`task-run` 每个 task 依次 `start → attempt reserve --executor inline → task-work → attempt terminal → attempt report → cleanup-worktree`，后继以当前分支作 `--base`，全链最终一次 `integrate-chain` merge；merge/index/integrated 后保留 transaction 与链分支，合并后验证通过再以同一命令 `--continue` 完成删除。`start` 加调度门：`depends_on` 未完成（完成口径=`done`/`dropped`，不要求已合并主干）硬拒，最新前置未合并主干时 base 自动落到其分支 tip；`conflicts_with` 对方正在运行（登记 worktree 存在 且 `status=active`）只警告后放行。

## 合并授权

合并主干需用户**会话级授权**，且不在启动时询问。`task-run` 先执行整条链并完成 exact cleanup，完成后询问一次是否需要合入，用户同意后才首次调用 `integrate-chain`；未获授权不 merge，保留已清理的链分支。合并环节只有 merge 冲突需裁决、合并后验证失败、task `blocked` 或范围扩大时停下来问用户；执行环节停止条件见各 skill。多会话并发时，不同链各自走自己的 `integrate-chain`，互不授权；先后合并冲突由 git 报错、人来收场。

`.claude/hooks/merge_guard.py` 拦截 Bash 工具里的 `git merge`（含 `--abort`，要求一次性 token）；`task.py integrate` / `integrate-chain` 内部 merge 经 subprocess 不经 Bash 工具，由会话级授权覆盖，hook 不拦。两层职责分离：脚本通道 = 已授权入口，hook = 防 agent 在脚本外手动 merge。
