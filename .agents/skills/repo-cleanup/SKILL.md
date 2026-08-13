---
name: repo-cleanup
description: none
disable-model-invocation: true
---

# repo-cleanup

删仓库内**明确无用**的文件系统垃圾（缓存、OS/编辑器垃圾、点名的运行产物）。扫描、类别匹配、保护名单过滤、dry-run 预览与删除全部由脚本 `scripts/repo_template/repo_cleanup.py` 执行；本 skill 只留裁定：scratch 活跃引用收集（`--keep`）、审批 commit 门禁、需用户决定项。文档归档用 `repo-hygiene`，不用本 skill。

## 脚本能力映射

`repo_cleanup.py` 在 `scripts/repo_template/` 下（随模板演进同步）。子命令见 `--help`。

|能力|命令|说明|
|---|---|---|
|扫描预览（只读）|`repo_cleanup.py scan [类别...] [--keep REL...]`|列出命中项与保护/keep 跳过；零删除|
|删除|`repo_cleanup.py apply [类别...] [--keep REL...]`|只删列表内路径；scratch/artifacts/data 清内容保留目录|

类别：默认 `pycache / pytest / logs / os / editor`；`node / scratch / artifacts / data` 须点名。保护与类别匹配规则以脚本为准（`.git/`、`AGENTS.md` 等硬保护；`docs/` 下仅 os/editor 类放行）。

## 流程

1. **解析模式与类别**：无 `apply` → 走 scan（只读）；有 `apply` → 删除。类别：未点名 = 默认五类；`node / scratch / artifacts / data` 须点名。

2. **扫描预览**：

    ```bash
    python3 scripts/repo_template/repo_cleanup.py scan [类别...]
    ```

    零写盘。输出命中项表、合计与 keep 跳过。**不确定是否垃圾 → 不删**，列入「需用户决定」。

3. **scratch 引用裁定**（仅点名 `apply scratch` 时必做）：

    - 按 `scripts/repo_template/task.py effective-status` 确定 backlog/active/blocked task 的有效来源与读取位置（`source=worktree` / `branch` → 在 `read_at` 处读 `spec.md` 上下文区与 `task.md` 实施笔记；`source=main` → 读主干）。主干中被 worktree 或未合并分支覆盖的旧状态不重复计。
    - 收集提及的 `.scratch/` 相对路径 → 用 `--keep <REL>` 排除，不删（含该路径的祖先目录，避免 `rmtree` 父目录把引用文件一并清掉）。
    - 无法解析引用 → **不删** `.scratch/`，列入「需用户决定」。

4. **删除**：

    ```bash
    python3 scripts/repo_template/repo_cleanup.py apply [类别...] --keep <REL>...
    ```

    只删脚本列出路径。重复应用前先重跑 scan（干净工作树命中可能已变）。

5. **汇报与 commit 门禁**：

    ```markdown
    ## repo-cleanup 结果

    模式：apply / scan
    已删除：
    - …
    跳过（保护/被引用/不存在）：
    - …
    需用户决定：
    - …
    ```

    默认不 commit。纯 gitignore 产物清理无跟踪 diff → 不 commit；仅当产生可跟踪 diff（如误提交的 `__pycache__`）且用户同意 → 单独维护期 commit；**不**擅自 commit。

## 边界

- 不替代 `repo-hygiene`；不改业务逻辑；不手改 task front matter / audit log。
- 派生 index JSON 不列入清理类别（误删只需重跑 `task.py list --rebuild`）。
- 不把「好久没动的源码/文档」当垃圾。
- 脚本是模板工具链的一部分，随 `repo-template-sync` 硬同步。

## 完成

输出预览或删除结果：已删 / 跳过 / 需用户决定；未获批不 commit。
