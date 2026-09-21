---
name: repo-template-sync-core
description: 同步流程本体。保留完整 status/plan/apply、回滚、测试、state 和审批语义；由 repo-template-sync 刷新后调用。
disable-model-invocation: true
---

# repo-template-sync-core

将模板源工具链同步到当前消费项目。机械文件分类、保护、回滚和 state 更新由 `repo_sync.py` 实现；本 skill 负责需要 Agent 判断的共享文件裁定和用户审批。

## 硬边界

- 仅用户显式请求时运行；禁止在模板仓本体把自己同步给自己。
- 共享资产裁定前不改这些资产；启动器 `init`/`prep` 已可能写入 state、脚本与入口，URL 源的 status/plan 也会更新 `.scratch/` 源缓存。这些写入属于同步准备的副作用。共享资产有歧义时必须先由用户裁定。
- 不覆盖 secret、本机路径或消费项目业务内容。
- apply 后测试通过且用户批准才 commit；不 push。

## 流程

1. **确认源**：读取 `sync_state.json` 和模板源状态；源缺失、同一性冲突或 dirty 影响版本判断时停止说明。
2. **status**：运行 `repo_sync.py status`，报告模板版本、消费状态和漂移。
3. **plan**：运行 `repo_sync.py plan`，取得硬同步、共享文件、技能入口和删除候选；本步不修改待同步资产；URL 源解析会 clone/pull 本地缓存。
4. **裁定**：
    - `.repo_template/`、模板配置和生成入口按脚本硬同步；

    - `.gitignore` / MCP 只做安全的键或规则合并；

    - `AGENTS.md` 按标题分三类处理：`## 开发原则` 每次从模板强制更新；`## 目录与读写规则` 只由 Agent 做语义合并，脚本绝不覆盖；该标题之前的项目介绍绝不更新，消费仓可以在项目介绍和目录与读写规则中增加自定义内容。消费仓只允许定制这两处，其他模板规则由同步流程管理。

    - 宿主 settings 不自动覆盖，只合并明确需要且不含 secret/本机路径的片段；

    - 手写文件和用户 prompt 保护项保持不动，冲突交用户决定。
5. **apply**：先保存本次会覆盖文件的未提交内容和当前 state，作为失败恢复基线；用户已有改动无法隔离时停止。把完整裁定交给 `repo_sync.py apply`。脚本负责备份、回滚、硬同步、软链/OpenCode入口、AGENTS.md 的开发原则强制更新、workflow schema 强制更新和 state 字段级更新；目录与读写规则的语义合并须由 Agent 智能处理并复核，项目介绍不得改。存在已登记 task worktree 时先完成或 rewind；不保留旧 schema 的运行时兼容。
6. **验证**：`repo_sync.py apply` 内置跑模板契约测试（`pytest .repo_template/tests -q -m contract`），并核脚本报告的结构检查。这不是工厂全量集成测试。写盘异常触发脚本回滚；内置测试返回失败则不推进 state，但保留已写入文件，不能声称已自动回滚。停止且不 commit，核对 diff 与预先保存的基线后修复重验；不要用 `--skip-tests` 绕过。若 apply 已成功推进 state 后的额外检查失败，报告实际 state，不伪称未推进。
7. **审批**：列出实际改动、测试、模板源版本和仍待决定项，询问是否 commit；批准后只提交本轮同步内容。

## 保留的完整语义

status/plan/apply 分离、模板源缓存、文件保护、apply 回滚、测试、state 推进、共享文件裁定和 commit 审批全部保留。详细文件矩阵、AGENTS.md 标题分区协议和恢复信息以 `repo_sync.py` 输出及 `.repo_template/docs/usage.md` 为准。

## 完成

报告同步来源、变更、保护/跳过、测试、state 和 commit 状态。任何未裁定共享冲突都意味着流程未完成。
