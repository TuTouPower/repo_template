---
name: repo-template-sync
description: 同步启动器。先刷新最新版 repo-template-sync-core，再由 core 完成同步。仅用户显式请求时运行。
disable-model-invocation: true
---

# repo-template-sync

确保本次同步使用模板源中的最新版 core 流程。

1. 从用户输入或现有 `.repo_template/sync_state.json` 确定模板源；首次使用先核实源路径存在或 Git URL 正确，再运行 `repo_sync.py init --source <实际路径或URL>`。无法确定时停止，不把拼错的本地路径当远程源尝试。
2. 运行 `repo_sync.py prep`，单向刷新消费仓中的同步脚本、`repo-template-sync-core` 和对应 skill 入口。
3. 重新读取刷新后的 `repo-template-sync-core/SKILL.md`。
4. 按 core 完成 status、plan、裁定、apply、测试和提交审批。

启动器不自行裁定共享文件，也不在模板仓自身作为推送源运行。prep 的写入随 core 最终审批一起处理。
