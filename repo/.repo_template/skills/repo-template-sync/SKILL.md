---
name: repo-template-sync
description: 同步启动器。直接运行固定来源中的同步器并读取 core，消费资产只在 apply 事务内修改。仅用户显式请求时运行。
disable-model-invocation: true
metadata:
  opencode/autoinvoke: "false"
---

# repo-template-sync

仅用户显式请求时同步。来源脚本与 core 直接读取，不复制到消费仓、不运行 prep。父子组合遵循 usage 与 registry：读取 canonical 正文，不调用宿主原生 Skill 工具。

1. 来源取用户输入，否则读本机 sync_state，缺失读可移植 sync_manifest。来源为本地产物根、工厂根或 Git URL；拼错路径不得当 URL。无法确定就停止。

2. 将来源解析为固定 clean checkout。URL 在消费仓 `.scratch/repo_template_sync_src` 首次 clone；已有缓存先核 remote 和 git status，干净且有网络时 fetch，按用户 ref 或远程默认分支解析 SHA 后 detach 到该 commit。离线只用已存在且可核验的 commit，不盲目重试 clone/pull。来源有 secret/凭据时沿用用户现有认证，不写入文档。

3. 统一解析产物根：若 `<checkout>/.repo_template/scripts/task.py` 存在，product=checkout；否则若 `<checkout>/repo/.repo_template/scripts/task.py` 存在，product=checkout/repo；都无则停止。保存真实 consumer、checkout、product 与完整来源 SHA。本机来源必须 clean；不在来源工作区替用户切分支或丢弃改动，指定旧 ref 时另建隔离 checkout。

4. 后续始终运行来源脚本，目标显式传入，source 保留原始 URL/来源身份，checkout 指向本轮固定 checkout。例如：

    ```bash
    python3 "$product/.repo_template/scripts/repo_sync.py" \
      --consumer "$consumer" --source "$source" --checkout "$checkout" status
    ```

    `consumer`、`product`、`checkout` 均为已核实实际路径，`source` 保留用户给定 URL 或来源路径。此时不 init、不改消费仓脚本或入口。脚本将来源身份与 resolved_checkout 分开保存；可共享且无凭据 URL 自动进入 manifest，本机路径不入库。

5. 直接读取 `$product/.repo_template/skills/repo-template-sync-core/SKILL.md`，传入同一来源 SHA、脚本路径与 consumer，按 core 执行 status/plan、裁定、apply、验证和提交审批。apply 前再次核对来源 SHA 未变化。

准备中止仅留下 `.scratch/` 缓存，无消费资产混合版本。禁止在模板源自身执行同步。来源信任与执行授权沿用用户本轮请求；共享文件裁定不明时停止。
