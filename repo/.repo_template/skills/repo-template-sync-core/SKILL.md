---
name: repo-template-sync-core
description: 同步流程本体。保留完整 status/plan/apply、回滚、测试、state 和审批语义；由 repo-template-sync 刷新后调用。
disable-model-invocation: true
---

# repo-template-sync-core

将模板源工具链同步到当前消费项目。机械文件分类、保护、回滚和 state 更新由 `repo_sync.py` 实现；本 skill 负责需要 Agent 判断的共享文件裁定和用户审批。

## 硬边界

- 仅用户显式请求时运行；禁止在模板仓本体把自己同步给自己。
- apply 前只读；共享资产有歧义时必须先由用户裁定。
- 不覆盖 secret、本机路径或消费项目业务内容。
- apply 后测试通过且用户批准才 commit；不 push。

## 流程

1. **确认源**：读取 `sync_state.json` 和模板源状态；源缺失、同一性冲突或 dirty 影响版本判断时停止说明。
2. **status**：运行 `repo_sync.py status`，报告模板版本、消费状态和漂移。
3. **plan**：运行 `repo_sync.py plan`，取得硬同步、共享文件、技能入口和删除候选；本步零写盘。
4. **裁定**：
    - `.repo_template/`、模板配置和生成入口按脚本硬同步；
    - `.gitignore` / MCP 只做安全的键或规则合并；
    - `AGENTS.md` 按语义合并，保留消费项目骨架和业务约定；
    - 宿主 settings 不自动覆盖，只合并明确需要且不含 secret/本机路径的片段；
    - 手写文件和用户 prompt 保护项保持不动，冲突交用户决定。
5. **apply**：把完整裁定交给 `repo_sync.py apply`。脚本负责备份、回滚、硬同步、软链/OpenCode入口、共享文件写入、workflow schema 强制更新和 state 字段级更新。存在已登记 task worktree 时先完成或 rewind；不保留旧 schema 的运行时兼容。
6. **验证**：运行同步后的 `.repo_template/tests` 和脚本报告的结构检查。失败不推进 state、不 commit，并报告回滚或残留现场。
7. **审批**：列出实际改动、测试、模板源版本和仍待决定项，询问是否 commit；批准后只提交本轮同步内容。

## 保留的完整语义

status/plan/apply 分离、模板源缓存、文件保护、apply 回滚、测试、state 推进、共享文件裁定和 commit 审批全部保留。详细文件矩阵和恢复信息以 `repo_sync.py` 输出及 `.repo_template/docs/usage.md` 为准。

## 完成

报告同步来源、变更、保护/跳过、测试、state 和 commit 状态。任何未裁定共享冲突都意味着流程未完成。
