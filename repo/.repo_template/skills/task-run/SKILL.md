---
name: task-run
description: 按用户给定顺序串行执行固定 task 队列：task-work 实施、attempt 收尾、exact cleanup；merge 不在启动授权内，整链完成后再询问。用户要求连续跑一批 task 或粘贴 /task-run 队列时调用。
disable-model-invocation: true
metadata:
  opencode/autoinvoke: "false"
---

# task-run

按用户给定顺序串行执行固定 task 队列。单 task 实施由 `task-work` 完成，合并由 `task-integrate` 完成。

## 组合契约

仅用户显式授权入口或 registry.json 已登记父流程可执行；作为子流程时由父流程直接读取本正文，原样传递参数、identity、owner_id 与授权，不扩大授权。完整规则见 `.repo_template/docs/usage.md`「skill 调用」。

## 授权与队列

执行范围严格限定在已获授权的队列成员内，队列外任务绝不执行。merge 不在启动授权内，队列处理完后必须单独向用户请求明确授权。

- 显式指定 tid 或 `/goal` 冻结队列：执行范围严格锁定为给定的成员序列；队列内成员按指定顺序执行到各自实现 commit 与 run-close 收尾，不逐 task 询问 commit。
- 无参数调用：必须先列出 `task.py plan --serial --json` 拟定执行的成员清单（tid、title、顺序、所属链与 base）并向用户确认，取得明确同意后方可开始执行，禁止静默全量执行。
- done/dropped 不作为新执行成员。
- 队列启动后成员和顺序固定；依赖必须位于依赖者之前。队列外依赖须在 start 前已 done 且已合入，否则 `run-begin` 的 start 门禁拒绝。
- 队列按[执行拓扑](../../docs/architecture.md#%E6%89%A7%E8%A1%8C%E6%8B%93%E6%89%91)分链。执行前运行 `task.py plan --serial --json {队列 tid 按顺序}`（无参数调用直接用默认队列的输出），读取 `members[]` 的 `chain`、`base.tid`、`base.branch` 与 `queue_chains[]` 的 `tail`；队列启动后沿用该结果。

## 执行

主路径只传 `tid` 与本会话 `owner_id`（唯一标识，队列内复用同一个）；attempt、execution_id 由脚本解析并回显，不手动传递。

对每个 tid：

1. 新 task：`task.py run-begin {tid} --owner-id {owner_id}`，一次完成 start、reserve、claim 并输出 JSON identity。该成员 `base.tid` 非空（链内有前一成员）时加 `--base {base.branch}`；链首 `base.tid` 为空，不加 `--base`，以默认分支起步。已有 active、attempt、worktree 或同名分支时脚本拒绝并提示 recovery，不做隐式恢复：此时运行 `task.py recovery {tid}`，按其 phase 与 `.repo_template/docs/recovery.md` 恢复，不重复 run-begin。
2. 在登记 worktree 直接读取 `task-work/SKILL.md` 正文执行，原样传递 tid 与 owner_id；不通过原生 Skill 工具调用。task-work 止于 `finish` 提交的实现 commit。
3. 回到主仓：`task.py run-close {tid} --owner-id {owner_id} --result done`，一次完成 terminal completed、report done 与 exact cleanup；中途失败按提示重跑同一命令（重入安全）。
4. 同链后继的 `--base` 是刚完成的前一成员分支（即当前成员 `plan` 输出的 `base.branch`）；其它链的成员各自从默认分支起步，互不继承。

阻塞时 task 保持 active：实施笔记记录原因和恢复入口，主仓 `task.py run-close {tid} --owner-id {owner_id} --result blocked --reason {原因}`，保留 worktree，只停当前链：同链排在它之后的成员全部跳过（不 run-begin），继续执行其它链的成员。无法继续的失败用 `--result failed [--class infra|task|contract] --reason {原因}`，同样只停所在链。

用户补齐条件后，`task.py recovery {tid}` 判定 phase 为 retry_ready，按 `recovery.md` 用恢复原语 `attempt reserve` 与 `attempt claim` 开新 attempt 继续；新 attempt 不重置 review/verify 历史。用户批准追加轮次时先用 `task.py limits` 增加绝对上限。

`attempt`、`cleanup-worktree` 等原语子命令是 recovery 的低层接口，只在恢复时使用。恢复细节以 `task.py recovery {tid}` 和脚本门禁为准，不在 skill 复制每个中断阶段；current attempt 尚未 terminal 时不得 reserve 新 attempt。running 恢复先核 owner_id，跨会话接管规程见 `recovery.md`，原会话存活则停止，禁止复用他人 owner_id。done 后 review stale 按 recovery 的 review 修复处理。

## 合并

所有可执行成员处理完并 cleanup 后，一次性向用户列出全部待合分支并询问是否 merge：单成员链用 `integrate {tid}`，多成员链用 `integrate-chain {tail_tid}`（`queue_chains[].tail`）；被阻塞的链只列已完成的前缀（链尾取最后一个已完成成员），阻塞成员与被跳过成员保留现场，不列入。用户同意后按完成顺序逐项直接读取 `task-integrate` 正文合并；不同意则保留分支。不 push。

## 停止

遇到以下情况保留现场，只停当前链（`run-close --result blocked`），跳过同链后续成员，继续其它链：

- preflight、测试、黑盒或 review 门禁无法闭环；
- 需要密钥、账号、外部环境或产品决策。

该障碍显然同样阻断其它链（如共享外部环境不可用）时，一并停止并说明。

遇到以下情况停止整个队列：

- merge 冲突无法可靠裁决；
- 工作区所有权或无关脏改动冲突；
- 用户指定停止。

队列结束或整体停止时报告：已完成成员、阻塞点（tid 与原因）、被跳过成员（tid 与所属阻塞点）和剩余队列。
