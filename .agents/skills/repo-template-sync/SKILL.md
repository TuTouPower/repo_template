---
name: repo-template-sync
description: 同步启动器。只负责把同步流程 skill（repo-template-sync-core）与工具链硬同步到消费项目，再交由该 skill 执行全部同步。仅用户显式请求时运行。
disable-model-invocation: true
---

# repo-template-sync（启动器）

只做**一件事**：从模板源硬同步「同步流程 skill 本体」到消费项目，保证随后执行的是**最新版**同步流程。同步的机械化、裁定、审批全部由 `repo-template-sync-core` 负责——本 skill **不执行同步流程**。

禁止在模板仓当推送源跑（与 `repo_sync.py apply` 同一性检查同一约束）。

## 为什么拆两段

`repo-template-sync-core` 随模板演进频繁；按旧版流程同步会漏掉模板侧新增的路径、命令与裁定。启动器每次同步前先从模板源原样覆盖 core skill（及它依赖的工具链），agent 重读新版本后再执行——保证「本次同步按最新流程跑」。

启动器自身极薄、稳定，不随同步流程演进，故不参与自更新；模板侧若调整启动器，由 `repo_sync.py apply` 的 skill 覆盖在 core 流程内更新它。

## 流程

1. **模板源就绪**：
    - 消费侧无 `scripts/repo_template/repo_sync.py` → 先从模板源复制该脚本（首次接入引导；完整工具链由步骤 2 补齐）。
    - 无 `sync_state.json` → `repo_sync.py init --source <path|url>`。推断模板源：用户给定 → 常见本机路径 → 不臆造。含 `scripts/repo_template/task.py` → `kind=path`；否则 `url`。
    - 已有 state → 读 `template_source`。缺源且无法初始化 → **停止并报告**。
2. **刷新同步工具（脚本执行）**：`python3 scripts/repo_template/repo_sync.py prep` 从模板源**单向覆盖**（新增 + 覆盖，不做删除）`.agents/skills/repo-template-sync-core/` 与 `scripts/repo_template/`，并建软链 `.claude/skills/repo-template-sync-core`。模板为唯一真相，覆盖即写盘、无裁定；模板源为 url → `resolve_src` 内部先 clone/更新本地缓存。
3. **重读**消费侧 `.agents/skills/repo-template-sync-core/SKILL.md`，以新版本为流程定义与边界。
4. **委派**：按 `repo-template-sync-core` 的流程执行全部同步（status → plan → 裁定 → apply → 审批门禁 → commit）。本 skill 到此结束。

启动写盘路径记入本轮改动清单，随 core 的审批门禁一并 commit。
