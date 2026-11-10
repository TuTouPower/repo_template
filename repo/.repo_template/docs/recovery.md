# 多会话接管与状态恢复

执行中断后先运行 `task.py recovery {tid}` 判断现场。日常用法见 `usage.md`，模板同步见 `sync.md`。

## 多会话与接管

多会话仅支持同一机器、同一主 clone 的独立 task。每个执行会话生成独立 owner_id（例如 `python3 -c 'import uuid; print(uuid.uuid4().hex)'`），`run-begin` 在 reserve 后自动 claim；恢复不得复用另一个会话的 owner_id。claim 在 ledger 锁内比较并写入；无自动超时。接管须确认原会话已停止，展示 identity/worktree/owner，再用 `--replace-owner <旧owner> --confirm-stopped`。禁止在原会话存活时接管；preflight 携 owner_id，冲突即停止。

## recovery 与原语子命令

`task.py recovery {tid}` 只读输出 phase、原 identity、worktree/task_dir 和下一步；Task 目录始终固定在 `docs/tasks/{tid}_{slug}/`，恢复与诊断只认规范路径，不扫描、不依赖 `docs/archive/tasks/`。主路径 `run-begin` / `run-close` / `finish` 遇到已有现场或中途失败时不做隐式恢复，只报告失败步骤并指向 recovery。

`attempt reserve/claim/terminal/report`、`cleanup-worktree` 是恢复用低层接口，只在 recovery 判定后使用：

- `--attempt/--execution-id` 可省略，主仓从 ledger 当前 attempt 解析；两者须同时提供或同时省略，显式提供只用于 recovery 指定 exact identity，且必须与解析结果一致。
- `run-begin` 中途失败：`started_without_attempt` 只 `attempt reserve`；`executing` 且 owner_id 为空表示尚未 claim，按原 identity `attempt claim`，两者都不重复 start。
- `blocked` 之后恢复：phase 为 `retry_ready`，`attempt reserve` 新 identity 后再 `attempt claim` 继续，不用 `run-begin`。
- `run-close` 中途失败：已写事件不回滚，重跑同一 `run-close` 即可（重入安全，不重复写 terminal/report）。

## finish 回滚语义

`finish` 先做全部前置检查，任一项失败零写入退出。写入阶段依次为持久化提交事务 journal 与快照（worktree 私有 Git 目录）、原位写 done、生成 `handoff.json`、暂存、`git commit -F`。只有 journal、原 index 与 task 目录快照全部落盘后才会写业务文件。

提交结果统一按 Git 实际状态判定，不以进程退出码代替事实：

- **HEAD 已前进**：判定实现 commit 已产生，不回滚、不重复提交；核对 `review` 与 `handoff` 后按 recovery 继续收尾。
- **HEAD 未前进且现场与事务期望一致**：精确回滚 finish 自身写入（front matter、`handoff.json`、index），调用方原有暂存与未暂存内容保持；修复原因后可直接重跑 `finish`。
- **结果未知**（存在 `index.lock` / `HEAD.lock` 等 Git 锁、HEAD 不可读、暂存区或 task 目录已被外部修改）：保留 journal 与现场，输出「提交结果未知」，禁止自动回滚与盲目重试。

遗留 journal 由 `task.py recovery {tid}` 以同一判定逻辑报告 `finish_txn_committed` / `finish_txn_rollback_safe` / `finish_txn_unknown`。重跑 `finish` 会先幂等处理遗留事务：`committed` 拒绝重复提交，`rollback_safe` 先回滚再继续，`unknown` 阻断并要求人工核查。收尾完成后运行 `task.py finish-txn-clear {tid}` 清理已完成事务：仅当 journal 对应实现 commit（tip 为事务起点后继）且 tip 引入变更隐私核对通过时才清理。进程被杀等中断同样由该 journal 与 recovery 兜底。

无 journal 的历史现场仍按既有阶段检测：`finish_interrupted`（已 done、无 `handoff.json`、无实现 commit）停止并报告用户，不重复 finish；`finished_uncommitted`（已有 `handoff.json`、尚无实现 commit）核验后暂存并提交实现 commit；`committed_unreported` 及之后补缺失的 terminal/report 并 cleanup，可由 `run-close` 完成（重入安全；owner_id 须与 claim 一致，跨会话先按上节接管规程 claim）。已提交但 review 门禁失败时，recovery 判 `review_repair`，按「review 门禁失败后的修复」补审后重跑同一 `run-close`，不要按 `committed_unreported` 只补 terminal/report。

## 轮次上限

`review_limit` / `verify_limit` 在 task front matter 持久保存，旧 task 默认 5；只有用户批准后由 `limits --review/--verify ... --reason ...` 增加，新 attempt 不清零历史轮次。这是 agent 必须遵守的流程约束：`--reason` 由调用方填写，代码只核对记录与上限变化一致，不核验批准来源。

## review 门禁失败后的修复

如已提交且 review 门禁失败（stale / missing / format_error / FAIL），保留现场并报告用户；未经用户同意不得修改实现 commit。用户同意后，在原 worktree 对当前 tip 的交付内容重新 review，只改 review 过程文件（`progress.md` 正文、`review_*.md`、渲染的 review prompt），用 `git commit --amend --no-edit` 并入同一实现 commit；amend 不得改 `handoff.json`、交付文件或其它路径。若提交内容已偏离 review 内容（`review_scope` 不一致），amend 修不好：recovery 判 `review_mismatch`，无后继分支且 report 未写入 done 时用 `task.py finish-unwind {tid} --reason …` 撤销实现 commit（分支退回 `diff_anchor`、`progress.md` 还原 active），然后重新 review 再 finish。撤销后旧 `handoff.json` 仍暂存于 worktree，`finish` 会重新生成，无需手动处理。两种修复都要求：没有其它分支包含当前 tip、tip 尚未合入主干、且该 attempt 的 report 尚未写入 done；有后继分支、tip 已合入主干或 report 已 done 时停止并报告，不 amend、不撤销。禁止 rewind、reserve。review 指纹的定义见 `usage.md`「skill 调用」。

## 图合并中断

图合并中断后用 `task.py graph-transaction status` 查询；保存新增改动后 `graph-transaction rollback --confirm-restore` 恢复图基线，不手删硬依赖。

## 状态恢复与升级

升级到单块门禁策略前，须人工收敛 `docs/blueprint/testing.md` 中各门禁章节的 bash/sh 块，步骤见[同步规程](sync.md)「testing.md 单块门禁迁移」。该文件由消费仓维护，同步不自动覆盖。若 start、preflight、finish 或 apply 报多块错误，先按迁移步骤修正文档；同步失败还须核对持久事务和实际 diff，必要时按同步恢复流程 rollback。`--skip-tests` 不绕过策略校验。

各 task worktree 私有 Git 目录的 `repo-task-execution.json` 留存原 reserve identity，ledger 丢失时用于 fail-closed 诊断，不代替 ledger、不入库。

运行态只服务本机主 clone，不提供分布式协作。迁移前停止所有会话，备份整个 Git common dir（含 `repo_template/` 状态目录）、所有 task 分支与 worktree 未提交内容；用隔离副本核对 ledger、Git refs、执行回执（`handoff.json`）和 review 可读后再迁移。不能仅复制 goal 快照或把新 clone 当作原控制面。

ledger 丢失但 done 分支和已入库的执行回执完整时，运行 `task.py ledger import {tid} --confirm-stopped`。脚本验证 exact identity、恰好一个实现 commit、base、`task.md` AC 全部勾选、最终 review 和干净 worktree，原子重建完成事件；已有该 tid 事件则拒绝覆盖。所有恢复与诊断只查找固定规范路径 `docs/tasks/{tid}_{slug}/`；项目交接仅读取 `docs/handoff.md`（历史统一由 Git 历史提供，`docs/archive/handoff.md` 不参与运行期读取）；审计日志写入 `$GIT_COMMON_DIR/repo_template/tasks_audit.log`，不依赖或回退至旧路径。active、缺分支、缺执行回执或坏 review 不可猜补，必须恢复备份；`recovery` 给出停止诊断。goal 快照可用 `goal --reset` 从有效任务重新冻结。

## finish 文件事务恢复

journal v3 记录每一步原子业务写入的前后指纹；写 index 前先在私有 index 计算目标 tree。HEAD 未前进且文件/index 匹配已记录中间状态时，重跑 finish 可精确回滚自身写入，再重新检查。未记录的外部修改一律保留现场，不自动覆盖。

prepared/writing/staging 阶段 HEAD 前进不能视为本事务提交。committing/committed 阶段还须核对唯一父提交与 handoff 执行身份；清理快照之前必须验证实际 tree、review scope 与隐私。hook 改写导致 tree 不匹配时保留 journal；recovery 按 finish_tree_mismatch 诊断，满足原 finish-unwind 写权、running、无 done report、无后继约束后才可撤销重做。成功 unwind 后清理已认领事务，避免新 finish 被旧 journal 锁死。

旧 journal 不自动升级；存在旧事务时先按原版本恢复或人工核验，禁止直接删除备份后升级。故障注入与真实迁移演练须在目标仓按恢复预案执行；模板发布验证不替代目标仓演练。

存在未结束文件事务（`preparing / applying / applied / restore_failed`）或事务记录损坏时，先按同步 `transaction status` 后 `accept/rollback`，或按图事务 `graph-transaction status` / `rollback` 处理；事务数据不在 `.scratch/`，`repo-cleanup` 不读取也不清理事务记录。带 scratch 的 cleanup 在控制面锁被占用时立即失败、零删除。
