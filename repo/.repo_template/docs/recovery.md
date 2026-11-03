# 多会话接管与状态恢复

多会话 claim/接管、`task.py recovery`、review 门禁失败后的修复、状态恢复与升级、图合并中断恢复的规程。日常用法见 `usage.md`，同步、升级与降级见 `sync.md`。

## 多会话与接管

多会话仅支持同一机器、同一主 clone 的独立 task。每个执行会话生成独立 owner_id（例如 `python3 -c 'import uuid; print(uuid.uuid4().hex)'`），reserve 后必须 `attempt claim`；恢复不得复用另一个会话的 owner_id。claim 在 ledger 锁内比较并写入；无自动超时。接管须确认原会话已停止，展示 identity/worktree/owner，再用 `--replace-owner <旧owner> --confirm-stopped`。禁止在原会话存活时接管；preflight 携 owner_id，冲突即停止。

## recovery 与轮次上限

`task.py recovery {tid}` 只读输出 phase、原 identity、worktree/task_dir 和下一步；不会自动 reserve、commit 或清理。`review_limit` / `verify_limit` 在 task front matter 持久保存，旧 task 默认 5；只有用户批准后由 `limits --review/--verify ... --reason ...` 增加，新 attempt 不清零历史轮次。

## review 门禁失败后的修复

如已提交且 review 门禁失败（stale / missing / format_error / FAIL），保留分支/证据并请用户决定恢复方式，未经用户同意不得 amend；用户同意后唯一合法修复是把补审证据 amend 进同一个执行 commit（first parent 必须仍为 diff_anchor，只改 review 过程文件，禁止第二个 commit、rewind、reserve），amend 后 worktree 干净再按原 identity cleanup。review 指纹的定义见 `usage.md`「skill 调用」。

## 图合并中断

图合并中断后用 `task.py graph-transaction status` 查询；保存新增改动后 `graph-transaction rollback --confirm-restore` 恢复图基线，不手删硬依赖。

## 状态恢复与升级

各 task worktree 的 `docs/runtime/execution_context.json` 留存原 reserve identity，ledger 丢失时用于 fail-closed 诊断，不代替 ledger、不入库。

运行态只服务本机主 clone，不提供分布式协作。迁移前停止所有会话，备份整个 Git common dir、主仓 `docs/runtime/`、`.repo_template/sync_state.json`、所有 task 分支与 worktree 未提交内容；用隔离副本核对 ledger、Git refs、执行回执（`handoff.json`）和 review 可读后再迁移。不能仅复制 goal 快照或把新 clone 当作原控制面。

ledger 丢失但 done 分支和已入库的执行回执完整时，运行 `task.py ledger import {tid} --confirm-stopped`。脚本验证 exact identity、一个执行 commit、base、`task.md` AC 全部勾选、最终 review 和干净 worktree，原子重建完成事件；已有该 tid 事件则拒绝覆盖。active、缺分支、缺执行回执或坏 review 不可猜补，必须恢复备份；`recovery` 给出停止诊断。goal 快照可用 `goal --reset` 从有效任务重新冻结。
