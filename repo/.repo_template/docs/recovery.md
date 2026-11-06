# 多会话接管与状态恢复

执行中断后先运行 `task.py recovery {tid}` 判断现场。日常用法见 `usage.md`，模板同步见 `sync.md`。

## 多会话与接管

多会话仅支持同一机器、同一主 clone 的独立 task。每个执行会话生成独立 owner_id（例如 `python3 -c 'import uuid; print(uuid.uuid4().hex)'`），`run-begin` 在 reserve 后自动 claim；恢复不得复用另一个会话的 owner_id。claim 在 ledger 锁内比较并写入；无自动超时。接管须确认原会话已停止，展示 identity/worktree/owner，再用 `--replace-owner <旧owner> --confirm-stopped`。禁止在原会话存活时接管；preflight 携 owner_id，冲突即停止。

## recovery 与原语子命令

`task.py recovery {tid}` 只读输出 phase、原 identity、worktree/task_dir 和下一步；不会自动 reserve、commit 或清理。主路径 `run-begin` / `run-close` / `finish` 遇到已有现场或中途失败时不做隐式恢复，只报告失败步骤并指向 recovery。

`attempt reserve/claim/terminal/report`、`cleanup-worktree` 是恢复用低层接口，只在 recovery 判定后使用：

- `--attempt/--execution-id` 可省略，主仓从 ledger 当前 attempt 解析；两者须同时提供或同时省略，显式提供只用于 recovery 指定 exact identity，且必须与解析结果一致。
- `run-begin` 中途失败：`started_without_attempt` 只 `attempt reserve`；`executing` 且 owner_id 为空表示尚未 claim，按原 identity `attempt claim`，两者都不重复 start。
- `blocked` 之后恢复：phase 为 `retry_ready`，`attempt reserve` 新 identity 后再 `attempt claim` 继续，不用 `run-begin`。
- `run-close` 中途失败：已写事件不回滚，重跑同一 `run-close` 即可（重入安全，不重复写 terminal/report）。

## finish 回滚语义

`finish` 先做全部前置检查，任一项失败零写入退出。写入阶段依次为原位写 done、生成 `handoff.json`、暂存、`git commit -F`。提交失败（含 pre-commit / commit-msg hook 拒绝）时脚本回滚 finish 自身的写入：front matter、`handoff.json` 与暂存区恢复到 finish 前，调用方已暂存的交付文件保持暂存，修复原因后可直接重跑 `finish`。回滚失败时脚本如实报错并提示运行 `task.py recovery`，此时现场不一致，不重复 finish。

进程被杀等中断不在脚本回滚范围，由 recovery 阶段检测兜底：`finish_interrupted`（已 done、无 `handoff.json`、无实现 commit）停止并报告用户，不重复 finish；`finished_uncommitted`（已有 `handoff.json`、尚无实现 commit）核验后暂存并提交实现 commit；`committed_unreported` 及之后补缺失的 terminal/report 并 cleanup，可由 `run-close` 完成（重入安全；owner_id 须与 claim 一致，跨会话先按上节接管规程 claim）。已提交但 review 门禁失败时，recovery 判 `review_repair`，按「review 门禁失败后的修复」补审后重跑同一 `run-close`，不要按 `committed_unreported` 只补 terminal/report。

## 轮次上限

`review_limit` / `verify_limit` 在 task front matter 持久保存，旧 task 默认 5；只有用户批准后由 `limits --review/--verify ... --reason ...` 增加，新 attempt 不清零历史轮次。

## review 门禁失败后的修复

如已提交且 review 门禁失败（stale / missing / format_error / FAIL），保留现场并报告用户；未经用户同意不得修改实现 commit。用户同意后，在原 worktree 对当前 tip 的交付内容重新 review，只改 review 过程文件（`progress.md` 正文、`review_*.md`、渲染的 review prompt），用 `git commit --amend --no-edit` 并入同一实现 commit；amend 不得改 `handoff.json`、交付文件或其它路径。若提交内容已偏离 review 内容（`review_scope` 不一致），amend 修不好：recovery 判 `review_mismatch`，无后继分支且 report 未写入 done 时用 `task.py finish-unwind {tid} --reason …` 撤销实现 commit（分支退回 `diff_anchor`、`progress.md` 还原 active），然后重新 review 再 finish。撤销后旧 `handoff.json` 仍暂存于 worktree，`finish` 会重新生成，无需手动处理。两种修复都要求：没有其它分支包含当前 tip、tip 尚未合入主干、且该 attempt 的 report 尚未写入 done；有后继分支、tip 已合入主干或 report 已 done 时停止并报告，不 amend、不撤销。禁止 rewind、reserve。review 指纹的定义见 `usage.md`「skill 调用」。

## 图合并中断

图合并中断后用 `task.py graph-transaction status` 查询；保存新增改动后 `graph-transaction rollback --confirm-restore` 恢复图基线，不手删硬依赖。

## 状态恢复与升级

升级到单块门禁策略前，须人工收敛 `docs/blueprint/testing.md` 中各门禁章节的 bash/sh 块，步骤见[同步规程](sync.md)「testing.md 单块门禁迁移」。该文件由消费仓维护，同步不自动覆盖。若 start、preflight、finish 或 apply 报多块错误，先按迁移步骤修正文档；同步失败还须核对持久事务和实际 diff，必要时按同步恢复流程 rollback。`--skip-tests` 不绕过策略校验。

各 task worktree 的 `docs/runtime/execution_context.json` 留存原 reserve identity，ledger 丢失时用于 fail-closed 诊断，不代替 ledger、不入库。

运行态只服务本机主 clone，不提供分布式协作。迁移前停止所有会话，备份整个 Git common dir、主仓 `docs/runtime/`、`.repo_template/sync_state.json`、所有 task 分支与 worktree 未提交内容；用隔离副本核对 ledger、Git refs、执行回执（`handoff.json`）和 review 可读后再迁移。不能仅复制 goal 快照或把新 clone 当作原控制面。

ledger 丢失但 done 分支和已入库的执行回执完整时，运行 `task.py ledger import {tid} --confirm-stopped`。脚本验证 exact identity、恰好一个实现 commit、base、`task.md` AC 全部勾选、最终 review 和干净 worktree，原子重建完成事件；已有该 tid 事件则拒绝覆盖。active、缺分支、缺执行回执或坏 review 不可猜补，必须恢复备份；`recovery` 给出停止诊断。goal 快照可用 `goal --reset` 从有效任务重新冻结。
