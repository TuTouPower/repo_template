---
name: repo-template-sync-core
description: 同步流程本体。保留完整 status/plan/apply、回滚、测试、state 和审批语义；由 repo-template-sync 从固定来源直接读取。
disable-model-invocation: true
user-invocable: false
metadata:
  opencode/autoinvoke: "false"
---

# repo-template-sync-core

由显式同步启动器从固定来源直接读取；不通过原生 Skill 工具调用。输入：consumer、product、source（原始来源身份）、checkout、完整来源 SHA。每条命令均运行来源 `repo_sync.py --consumer "$consumer" --source "$source" --checkout "$checkout" <子命令>`。

## 边界

不在模板仓把自己同步给自己。不覆盖 secret、本机路径或业务文件；不 push。status/plan 不改消费资产，准备写入仅限 `.scratch/` 来源缓存。内部流程不新增授权，共享裁定有歧义时询问用户。

## 流程

1. **核验**：来源 clean、固定 SHA 与输入相符。读取本机 state、共享 policy 与可移植 manifest；无 state 时 `--source` 提供临时身份，不提前 init。检查 active worktree；存在则先完成或 rewind，禁止混用 workflow schema。
2. **预览**：status、plan。明确硬同步、共享裁定、保护项和删除候选。确认 `testing.md` 项目验证策略已选定；模板默认未确认不得作为业务绿灯。
3. **裁定**：工具链/入口按模板；gitignore、prettierignore、MCP 做安全合并；宿主 settings 只合明确无 secret 片段。AGENTS 项目介绍保留，开发原则按模板，目录规则由 Agent 语义合并。语义结果先存 `.scratch/`，通过 `apply --agents-file <结果文件>` 纳入事务，不提前写消费资产；未完成裁定不 apply。
4. **apply**：确认会覆盖的未提交内容已纳入事务，业务改动可隔离。运行 `apply --expected-commit {来源完整SHA}`，带完整 `--decision` 和适用的 `--agents-file`。不得跳过测试。正式写盘从该 commit 的 Git archive 快照读取，源工作区后续变化不影响本轮。内置 contract 或项目验证测试失败时，工具链保证全量自动回滚到 apply 前基线状态，state 绝不推进，备份保留在 `.scratch/repo_sync_rollback` 供排查，绝不在消费仓保留半同步或测试失败的文件。以 transaction status 和实际 diff 为准，不猜恢复结果。
5. **恢复**：apply 失败即停止且不 commit；异常中断运行 `transaction status`，先备份中断后的新改动，再 `transaction rollback --confirm-restore`。成功 apply 但用户放弃时 `transaction rollback`；现场后续变化会阻止静默覆盖。恢复失败保留日志和备份，报告人工恢复条件。
6. **审批**：列出来源 SHA、全部实际路径、保护/跳过、contract 与项目测试、state/manifest、未决项，询问是否 commit。仅批准后提交本轮内容，提交包含 sync_manifest；随后 `transaction accept`。提交前拒绝则恢复，不把“未提交”等同于可恢复。批准的批量 commit 授权可复用，不重复询问。

## 完成

报告来源、变更、验证、事务、state 和 commit 状态。任何共享冲突、未选定项目验证、失败检查或未结束事务都表示同步尚未完成。详细恢复边界见 usage，机械事实以脚本和持久 transaction.json 为准。
