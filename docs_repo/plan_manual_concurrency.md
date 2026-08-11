# 手动并发：退役 dispatch 自动调度 + view 可视化看板

决策来源：`decision_log.md` L35。放弃 `task-dispatch` 自动并发；并发只发生在用户维度——`task-schedule` 落盘后，用户在多个会话各自跑 `task-run`。理由：① dispatch 实战效果差，小问题频发、执行不顺畅；② 编程 Agent 能力快速演进，工作流自造特殊机制（hook、skill、控制面）即使暂时有效，也终将被 Agent 自身能力消化——工作流应保持薄而通用。

本文是 L35 的实施设计。实施时按仓库标准 task 流程拆分执行。

## 目标形态

```text
task-create → task-schedule（依赖/冲突落盘）
            → task.py view --serve（用户看板：谁依赖谁、谁阻塞谁、谁在跑）
            → 用户在会话 A：/task-run t001,t003
            → 用户在会话 B：/task-run t002
```

- 执行入口收敛为 `task-run`（链式、单会话串行，已支持显式 task 列表，`task-run/SKILL.md:35`）。
- `task-dispatch` 及 cron 兜底、静默监控退役；`task-work` / `task-integrate` / ledger / merge_guard 保留（`task-run` 内联路径在用）。
- 多会话同时写主仓不加锁：合并撞车由 git 报错、人来收场；index 是派生缓存，撞了重建。

## 变更项

### 1. `cmd_start` 增加调度门（`scripts/repo_template/repo_task/integration.py`）

`start` 目前只校验 spec/task 文档与分支/worktree 占用，不看 `depends_on` / `conflicts_with`。新增：

|检查|不满足时|口径|
|------|------|------|
|`depends_on` 全部前置|**硬拒**，列出未满足 tid|**完成口径**：前置 `done`（有执行 commit + `handoff.json`），**不要求已合并主干**。判据 = 未合并 task 分支 ref 上的 front matter `status=done`（状态读取优先级：登记 worktree → 未合并分支 ref → 主干）|
|`conflicts_with` 对方正在运行|**警告后放行**，列出冲突 tid|「正在运行」= 登记 worktree 存在 且 task front matter `status=active`（`git worktree list` + task.md，不查 ledger）|

依赖硬拒通过时，若前置未合并，`start` 的 base 自动解析到**最新完成的前置分支 tip**（复用现有 `resolve_start_base` / `--base` 机制），git 历史自然串起；用户显式传 `--base` 时以用户为准。前置已合并主干则 base 落主干 HEAD，行为不变。

### 2. `task.py view --serve`（`scripts/repo_template/repo_task/cli.py` + 新模块）

- 绑 `127.0.0.1`，端口 0（系统分配空闲端口），启动后打印 URL 并从 WSL 打开 Windows 默认浏览器（`cmd.exe /c start <url>`，备选 `wslview`）。
- stdlib `http.server`，无框架、无 WebSocket、无后台轮询。
- 页面内容：项目名（仓库目录名）+ 调度图。图用 Mermaid（CDN 或内嵌 JS）渲染 DAG，节点 = task，边 = `depends_on` 实线 / `conflicts_with` 虚线；节点上色：运行中（active + worktree 登记）/ 可跑（backlog 且依赖满足且无运行中冲突）/ 被依赖阻塞 / 被冲突阻塞 / 已结束（done/dropped）。
- 刷新 = 用户点页面刷新按钮 → 服务端重新扫状态重新渲染。每次请求重新计算，不做缓存——状态在多会话间变化，快照必须新鲜。
- 服务进程随会话退出即退出，不做常驻守护。只读，页面不提供任何写操作。
- 无 `--serve` 时 `view` 保持现有终端输出不变。

### 3. 退役拆除

|对象|处置|
|------|------|
|`.agents/skills/task-dispatch/`|删除目录及 `.claude/skills/` 对应软链|
|dispatch cron 节拍（`task-dispatch` 内 cron 兜底）|随 skill 删除一并拆除|
|`docs_repo/plan_worker_silence_monitoring.md` 等 dispatch 专属 plan|不动（docs_repo 是历史证据，只准新增；在 L35 行已标明被替代）|
|`docs/runtime/dispatch_ledger.jsonl` + attempt 生命周期|**保留**——`task-run` inline attempt 在用；路径名维持兼容名|
|`merge_guard.py` hook|**保留**——防脚本外手 merge，与自动并发无关|
|`task-work` / `task-integrate`|**保留**——task-run 内联路径在用；dispatch 专属命令（`attempt bind/escalate/silent-alert`、`observe`、`reconcile`）与对应函数已删，attempt 状态机精简为 `running→terminal→integrated` 三态，executor 收为仅 `inline`|

### 4. 文档同步

|文件|改动|
|------|------|
|`CLAUDE.md` 工作流表|删 `task-dispatch` 行；`task-run` 职责改为「链式串行；多会话手动并发时各跑用户指定段」；典型路径改为 `task-create → task-schedule → task.py view --serve → 一个或多个会话 task-run`|
|`docs/blueprint/architecture_repo_template.md`|「执行拓扑」删扇出段落，补手动并发模型：每会话一条链、coordinator 唯一写者假设解除、撞车靠 git；「执行角色」保留 worker/coordinator 但注明并发发生在用户维度|
|`docs_repo/decision_log.md` L35|实施完成后状态改「已落地」|

## 不做的事

- 不加主仓写锁、不做跨会话互斥。
- view 不做常驻 server、不做自动刷新、不做写操作。
- 不动 `task-run` 的显式列表语义（已有）；不自动沿依赖扩展队列——用户给什么跑什么，边界清晰是手动并发存在的意义。
- 不删 ledger、attempt、handoff.json、merge_guard。
- `docs_repo/` 历史 plan 不改写，由 L35 行标注替代关系。

## 验收

1. 两个会话各 `task-run` 不同 task 段，互不干扰；同一 task 被第二会话 `start` 时报「分支/worktree 已存在」（现有行为）。
2. `start` 一个 `depends_on` 未完成的 task → 拒绝并列出未满足项；前置 `done` 未合并时再 `start` → 通过，分支基于前置分支 tip。
3. `start` 与运行中 task 冲突的 task → 警告后成功。
4. `task.py view --serve` 起服务、浏览器打开、页面显示项目名与着色调度图；某会话推进 task 后点刷新，图状态更新。
5. `task-dispatch` skill 与 cron 不复存在；`task-run` 全链回归通过（单会话串行行为不变）。
