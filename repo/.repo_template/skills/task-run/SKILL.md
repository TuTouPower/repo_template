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

仅用户显式授权入口或 registry.json 已登记父流程可执行。本流程作为子流程时，由父流程直接读取本 canonical `SKILL.md` 并执行正文，不调用宿主原生 Skill 工具；原样传递参数、exact identity、owner_id 和写域。父流程授权不扩大，缺少所需授权时停止。调用关系以 `.repo_template/skills/registry.json` 为权威。

## 授权与队列

执行范围严格限定在已获授权的队列成员内，队列外任务绝不执行。merge 不在启动授权内，整链完成后必须单独向用户请求明确授权。

- 显式指定 tid 或 `/goal` 冻结队列：执行范围严格锁定为给定的成员序列；队列内成员按指定顺序执行到各自执行 commit、attempt report 和 exact cleanup，不逐 task 询问 commit。
- 无参数调用：必须先列出 `task.py plan --serial --json` 拟定执行的成员清单（tid、title、顺序）并向用户确认，取得明确同意后方可开始执行，禁止静默全量执行。
- done/dropped 不作为新执行成员。
- 队列启动后成员和顺序固定；依赖必须位于依赖者之前。

## 执行

对每个 tid：

1. 先观察 effective status、`task.py recovery`、worktree 和 ledger。已有现场就恢复，不重复 start 或 reserve。
2. 新 task：`start`，随后 `attempt reserve --executor inline`，保存 exact `(attempt, execution_id)`；为当前会话生成唯一 `owner_id`，用 `attempt claim` 原子领取执行权。
3. 在登记 worktree 直接读取 `task-work/SKILL.md` 正文执行，原样传递 identity 和 owner_id；不通过原生 Skill 工具调用。
4. 完成后在主仓写同一 identity 的 terminal completed、report done，并 exact cleanup。
5. 后继 task 以刚完成的前一分支为 `--base`。

阻塞时 task 保持 active：实施笔记记录原因和恢复入口，当前 attempt 写 terminal stopped + report blocked，保留 worktree并停止队列。用户补齐条件后直接 reserve 新 attempt继续；新 attempt不重置 review/verify 历史。用户批准追加轮次时先用 `task.py limits` 增加绝对上限。

恢复细节以 `task.py recovery {tid}` 和脚本门禁为准，不在 skill 复制每个中断阶段。current attempt 尚未 terminal 时不得 reserve 新 attempt。running 恢复先核 owner_id；同一会话原 owner 可续行，跨会话必须确认原会话已停止，再 `attempt claim --replace-owner <旧owner> --confirm-stopped` 原子接管；原会话存活则停止，禁止复用他人 owner_id。done 后 review stale 不 rewind/reserve，按 recovery 证据修复 amend 同一执行 commit。

## 合并

所有成员完成并 cleanup 后询问一次是否 merge。用户同意后直接读取 `task-integrate` 正文 合链尾；不同意则保留分支。不 push。

## 停止

遇到以下情况保留现场并报告已完成、当前问题和剩余队列：

- preflight、测试、黑盒或 review 门禁无法闭环；
- 需要密钥、账号、外部环境或产品决策；
- merge 冲突无法可靠裁决；
- 工作区所有权或无关脏改动冲突；
- 用户指定停止。
