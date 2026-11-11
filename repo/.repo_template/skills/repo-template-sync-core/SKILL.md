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

1. **核验**：来源 clean、固定 SHA 与输入相符。读取本机 state、共享 policy 与可移植 manifest；无 state 时 `--source` 提供临时身份，不提前 init。工作区与暂存区必须完全干净（含未跟踪文件，忽略文件除外）；检查所有登记 worktree 与未结束同步/图事务，存在则先收尾。
2. **预览**：status、plan 列出迁移明细。数据阻断项只报告并给修复方案；用户批准后修复，修复再单独批准并提交，然后同步。明确硬同步、共享裁定、保护项和删除候选。确认 `testing.md` 项目验证策略已选定；模板默认未确认不得作为业务绿灯。旧版各门禁章节存在多个 bash/sh 块时，先按 `sync.md`「testing.md 单块门禁迁移」人工收敛，再 apply；同步不会覆盖该消费仓自有文件。
3. **裁定**：工具链/入口按模板；gitignore、prettierignore、MCP 做安全合并；宿主 settings 只合明确无 secret 片段。AGENTS 项目区原样保留，模板区整块替换；缺少两区标记时由 apply 内置迁移。
4. **apply**：再次确认工作区全净。迁移与模板同步只开一个事务。运行 `apply --expected-commit {来源完整SHA}`，带完整 `--decision`。不得跳过模板契约测试。保留 `testing.md` 存在性、项目策略配置和命令格式检查，但不执行消费仓 doctor/test/blackbox。正式写盘从该 commit 的 Git archive 快照读取，源工作区后续变化不影响本次 apply。失败后按下一步核对回滚状态，不据退出码假定现场已恢复。
5. **恢复**：apply 失败即停止且不 commit；异常中断运行 `transaction status`，先备份中断后的新改动，再 `transaction rollback --confirm-restore`。提交前用户放弃时 `transaction rollback`；现场后续变化会阻止静默覆盖。恢复失败保留日志和备份，报告人工恢复条件。提交后禁止文件 rollback，只能 git revert 同步 commit；common 状态不随 revert 恢复，须告知用户并人工核对。
6. **审批**：列出来源 SHA、全部实际路径、保护/跳过、contract 与项目策略校验、state/manifest、未决项，询问是否 commit。仅批准后提交本次同步内容，提交包含 sync_manifest；随后 `transaction accept`。accept 始终重读并校验当前项目策略，不能因已有验证记录而跳过；未验证时补跑模板契约；文件或 common 状态指纹变化则拒绝 accept。提交前拒绝则恢复，不把“未提交”等同于可恢复。每次同步必须单独明确批准，不复用批量 commit 授权。批准后 git add -A，单个 commit 包含全部迁移与同步结果，禁止 --no-verify；随后立即 accept。旧归档非实体删除项必须逐个单列路径、类型与目录内文件清单，不能并入汇总。

## 完成

报告来源、变更、验证、事务、state 和 commit 状态。任何共享冲突、未选定项目验证、失败检查或未结束事务都表示同步尚未完成。详细恢复边界见 `.repo_template/docs/sync.md`，机械事实以脚本和持久 transaction.json 为准。
