# 模板仓库执行架构

task 工具链的执行拓扑、attempt 生命周期与合并授权。项目自身模块架构见 `architecture.md`。

## 执行拓扑

串行（`task-run`）与并行（`task-dispatch`）只描述 **branch topology**；两者共用同一 attempt 控制面与 exact identity `(tid, attempt, execution_id)`。`execution_id` 是执行 provenance，`host_worker_id` 仅是 agent 宿主句柄；`executor` 为 `inline` 或 `agent`。

**串行 = 链式**。task 按执行顺序一个串一个成链，每个从上一个已完成 task 的分支创建（`--base`）。每个 task 一个执行 commit，中间只清理 worktree；全部完成后由 `integrate-chain` 一次性把链尾合并回主干，主干只进一次 merge commit。

```text
主干 ── t001 ── t002 ── t003 ──► 全部完成后 merge 链尾
```

**并行 = 扇出**。每个 task 从主干 HEAD 独立扇出，完成即以 exact identity 清理并合并——快 task 先合并、先释放并发位、先解锁下游；慢 task 不阻塞任何人。

```text
        主干 ──┬── t001 ──► 完成即 merge
               ├── t002 ──► 完成即 merge
               └── t003 ──► 完成即 merge
```

## 执行角色

两角色写域互不重叠：

| 角色 | 唯一写域 | 职责 | skill |
|------|---------|------|-------|
| worker | 自己的 task worktree | 接收必填 `attempt` / `execution_id`，实施、测试、黑盒、review、finish、一个执行 commit；只写精确 identity 的 `handoff.json` 并交出 `{tid}: {branch} @ {sha}` | `task-work` |
| coordinator | 主仓 | `start`；reserve/bind attempt；查询宿主终态后写 exact terminal/report；以 exact identity cleanup；单 task `integrate` 或链式 `integrate-chain`；派生 index、分支清理、合并后验证 | `task-integrate` / `task-dispatch` / `task-run` |

worker 不合并任何分支、不重建 index、不 push、不删分支、不清理自己的 worktree、不询问是否合并主干，也不写 attempt 控制面；worker 唯一交接写入是本 task 分支中的 `handoff.json`。agent 宿主状态只由 coordinator 查询，`host_worker_id` 不承担 execution provenance。主干只有 coordinator 一个写者且串行处理，因此不需要额外的锁。

`task-run` 当前会话同时承担 coordinator 与 inline worker：每个 task 依次 `start → attempt reserve --executor inline → task-work → attempt terminal → attempt report → cleanup-worktree`，后继以当前分支作 `--base`，全链最终一次 `integrate-chain` merge；merge/index/integrated 后保留 transaction 与链分支，合并后验证通过再以同一命令 `--continue` 完成删除。`task-dispatch` 由 coordinator reserve agent attempt、派发带 identity 的 worker、启动后 bind `host_worker_id`，自身不执行 task；每个完成 task 用 exact identity 调用单 task `integrate`。

## 合并授权

合并主干需用户**会话级授权**。`task-dispatch` 因 task 完成即合并，启动时一次性取得；`task-run` 可在启动时取得，也可先执行整条链，仅在最终首次 `integrate-chain` 前询问一次。已有授权不重复询问，未获授权不 merge。合并环节只有 merge 冲突需裁决、合并后验证失败、task `blocked` 或范围扩大时停下来问用户；执行环节停止条件见各 skill。

`.claude/hooks/merge_guard.py` 拦截 Bash 工具里的 `git merge`（含 `--abort`，要求一次性 token）；`task.py integrate` / `integrate-chain` 内部 merge 经 subprocess 不经 Bash 工具，由会话级授权覆盖，hook 不拦。两层职责分离：脚本通道 = 已授权入口，hook = 防 agent 在脚本外手动 merge。
