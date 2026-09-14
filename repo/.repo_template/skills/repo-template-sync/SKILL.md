---
name: repo-template-sync
description: 同步启动器。先把模板源最新版 repo_sync.py 复制进消费仓，再跑 prep 刷新 core，最后由 core 完成同步。仅用户显式请求时运行。
disable-model-invocation: true
---

# repo-template-sync

确保本次同步使用模板源中的最新版脚本与 core 流程。消费仓现有的 `repo_sync.py` 可能是旧版（缺 `prep`、旧布局或旧 state 路径），**不能直接拿来跑**——任何子命令之前，必须先用模板源的脚本覆盖它。

1. **确定模板源**：取用户输入；否则读消费仓 state（新路径 `.repo_template/sync_state.json`，缺失再看旧路径 `.agents/skills/repo-template-sync/sync_state.json`）的 `template_source`。核实是产物根，或含 `repo/` 的工厂根（两者都含 `.repo_template/scripts/task.py`）。无法确定时停止，不把拼错的本地路径当远程源尝试。

2. **先复制同步脚本（自举，必须在跑任何 `repo_sync.py` 子命令之前）**：用模板源的最新脚本覆盖消费仓新路径。工厂根的脚本在 `<src>/repo/.repo_template/scripts/`，产物根在 `<src>/.repo_template/scripts/`：

    ```bash
    consumer=/path/to/consumer
    src=/path/to/repo_template            # 产物根或工厂根
    mkdir -p "$consumer/.repo_template/scripts"
    cp "$src/.repo_template/scripts/repo_sync.py" \
       "$consumer/.repo_template/scripts/repo_sync.py"
    ```

    跳过这步会让后续 `init` / `prep` 命中旧脚本而失败——旧脚本没有 `prep`，也没有本条自举指引。

3. **首次接入**：无 state 时运行 `python3 .repo_template/scripts/repo_sync.py init --source <实际路径或URL>`；已有 state（含从旧路径读取）不重跑。`init` 会把旧路径 state 迁移到新路径并保留 `user_prompts`。

4. **刷新 core**：运行 `python3 .repo_template/scripts/repo_sync.py prep`，单向刷新消费仓中的同步脚本、`repo-template-sync-core` 和对应 skill 入口。

5. 重新读取刷新后的 `repo-template-sync-core/SKILL.md`。

6. 按 core 完成 status、plan、裁定、apply、测试和提交审批。

启动器不自行裁定共享文件，也不在模板仓自身作为推送源运行。第 2 步自举与 prep 的写入随 core 最终审批一起处理。
