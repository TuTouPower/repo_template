# 消费仓如何使用 repo_template

## 工具链路径与写权

写权归属列声明路径的写入责任与时机；具体步骤见对应 skill 或文件内注释。

|路径|用途|写权归属|
|---|---|---|
|`.repo_template/docs/task_template/`|task 文件模板（非工作项）|只改模板本身|
|`docs/tasks_index.json` / `docs/archive/tasks_index.json`|活跃/归档 task 派生索引|工作区可由 `add`/`edit`/`rewind`/`purge` 重建；入库 commit：维护期随操作提交，合并后由 `integrate` / `integrate-chain` 单独 chore commit；`list` 只读，`list --rebuild` 手动重建；不进 task worktree 的执行 commit|
|`docs/archive/tasks_audit.log`|rewind/purge 审计（append-only）|仅 `.repo_template/scripts/task.py rewind` / `purge` 独占 append，禁止 agent 手动修改|
|`docs/runtime/dispatch_ledger.jsonl`|attempt 控制面（append-only；已 gitignore，仅主仓）|exact identity 为 `(tid, attempt, execution_id)`；生命周期只经 `task.py attempt reserve/terminal/report` 写入，`integrate` / `integrate-chain` 写 `integrated`；`ledger record` 仅允许 `note`，`ledger tail` 只读；禁止手工编辑|
|`docs/runtime/goal_queue.json`|goal 模式冻结队列快照（已 gitignore，仅主仓）|仅 `task.py goal` 写入（首次冻结，或显式 tid / `--reset` 覆盖；无参已有快照只读）；`task.py goal-check` 只读；禁止手工编辑|
|`.repo_template/docs/review_prompts/`|review prompt 模板|改审查标准时更新|
|`.repo_template/docs/spike_report_template.md`|spike 报告模板|只改模板本身|
|`.repo_template/skills/`|skill 正文|改 skill 走文档纪律；不放业务代码|
|`.claude/skills/` / `.agents/skills/`|指向 `.repo_template/skills/` 的软链|只维护软链|
|`.repo_template/scripts/`|模板自带 task 工具链：`task.py` 是 CLI/兼容 façade，业务实现位于 `repo_task/`，另含 pending.py/findings.py/spikes.py 等|仅模板演进时修改；复制或维护必须保留 `task.py` 与完整 `repo_task/`，并随模板复制进新项目|
|`../{repo}_{tid}/`（仓库外）|task 工作副本（git worktree）|`start` 仅从主仓默认分支调用（不要求干净，主仓未提交改动保留不动）：链式拓扑以 `--base` 指向上一已完成 task 分支；active/blocked task 的实施、测试、review、finish/drop 只在自身 worktree 执行；每个 task 一个执行 commit，实施阶段写 exact identity 的 `handoff.json`，调度阶段以同一 identity 清理 worktree 并合并；本地 `.env` 软链回主仓|

## 命名与格式

- `AGENTS.md`、`CLAUDE.md`、`README.md` 是工具入口例外。
- task 编号：占位 `{tid}`，值小写 `t001`、`t042`…。目录 / 分支 / finding / worktree：`docs/tasks/{tid}_{slug}/`、`{tid}_{slug}`、`{tid}_code_fNNN`、`../{repo}_{tid}`。
- spike 编号：占位 `{sid}`，值小写 `s001`、`s003`…。目录：`docs/spikes/{sid}_{slug}/`。
- 总账编号：待办与发现均为一条目一文件，文件名 `pNNN_{slug}.md` / `dNNN_{slug}.md`，编号来自文件名。条目只经 `.repo_template/scripts/pending.py new` 与 `findings.py new` 创建——脚本在 git 公共目录的排他锁内完成「扫描全部本地分支与 worktree 取号 → 建文件」，并发执行不会撞号。`pNNN` 跨 `docs/pending/todo/`、`docs/pending/parked/`、`docs/archive/pending/` 共享全局序列，`dNNN` 在 `docs/findings/` 内递增；历史编号均不复用，不维护索引文件。spike 是目录型条目（`docs/spikes/sNNN_{slug}/`），由 `.repo_template/scripts/spikes.py new` 同法锁内分配，`sNNN` 与 `docs/archive/spikes/` 共享序列。
- AC 编号：spec 验收标准每条行为 AC 用 `AC-NNN`（三位十进制，task 内从 1 顺序编号）。编号一旦分配永久归属，删除后不复用（允许断号，不强制连续），新增用下一个编号。`handoff.json` 的 `ac_evidence` 键引用同一编号，须精确覆盖 spec 验收标准全部 AC——缺或多都阻断合入。编号规范属 spec 模板门禁，见 `.repo_template/docs/task_template/spec.md`。
- 占位示例（模板、示例行）不得占用真实 `tid` / `sid` / `pNNN`，也不得当作 active 工作项执行。
- Markdown 嵌套内容缩进 4 空格，禁止 tab。
- 非归档 Markdown 统一用 md_kx 格式化（`.repo_template/scripts/md_format.py`），表用 `compact`（`|a|b|`）。格式由 `.md_kx.toml` 统一，禁止 prettier / 按列 pad。commit 由 pre-commit hook 强制（`.repo_template/hooks/pre-commit`，格式化本次 staged 的 `.md` 并重新暂存；工作区与 index 不一致则拒绝），需先 `python3 .repo_template/scripts/repo_sync.py install-hooks` 启用 `core.hooksPath`（已有其它 hooksPath 须 `--force`）；临时手动格式化用 `python3 .repo_template/scripts/md_format.py --changed`，commit 前 `--check` 为绿。
- front matter 注释独占整行；行内注释有解析器兜底，但勿依赖。

## skill 调用

用户入口：

|skill|职责|
|---|---|
|`task-create`|按需求拆 backlog task，批量落盘后统一创建 commit|
|`task-schedule`|分析依赖/冲突并落盘；可跑集由 `task.py view` 计算；本波链由 `task.py plan` 重算|
|`task-run`|链式串行跑 task，链尾 `integrate-chain` 合主干|
|`task-preflight`|只读汇总待做 task 缺口|
|`task-bug`|复现/根因/同类位点扫描（仅 `.scratch/`）后建修复 task|
|`pending-record`|持续澄清后派子代理登记 pending；bug 走 task-bug 分析再记|
|`task-from-pending`|从 `docs/pending/todo/` 建 task 并归档条目|
|`task-merge`|合并多个 backlog task（edit 目标 + drop 源）|
|`repo-hygiene`|过时 handoff/pending 等迁 archive|
|`repo-cleanup`|清缓存等无用文件，默认 dry-run|
|`template-issue-report`|发现模板仓带入的问题（bug 或设计/约定需求）时，写交接报告到 `.scratch/repo_template_issues/`，交模板仓 agent 处理；只观察现象、不定位根因、不给方案|
|`repo-template-sync`|消费项目从模板仓同步工具链；审批通过后才 commit|

多会话并发：用户自决开多个会话各跑 `task-run`；`task.py plan` 取本波并发链，`task.py view --serve` 看看板。无自动调度器。

内部调用：

|skill|职责|
|---|---|
|`task-work`|在 task worktree 实施并写 `handoff.json`（由 `task-run` 调用）|
|`task-integrate`|单 task 或链式合并回主干（由 `task-run` 调用）|

## workflow 示例

`/task-create` → `/task-schedule` → `task.py plan`（本波链）/ `view --serve` → 一个或多个会话 `/task-run`（多会话手动并发各跑一段；状态变后重跑 `plan` 得下一批）。goal 模式自治跑队列：先 `task.py goal` 冻结队列并粘贴其输出的 `/goal` 行，终态以 `task.py goal-check` marker 判定。

### `.repo_template/scripts/` 使用示例

```bash
python3 .repo_template/scripts/task.py --help        # 所有子命令、参数与用法
python3 .repo_template/scripts/pending.py --help     # 待办总账
python3 .repo_template/scripts/findings.py --help    # 技术发现
python3 .repo_template/scripts/spikes.py --help      # 技术 spike
python3 .repo_template/scripts/repo_state.py --help  # 完整工作树 vs baseline 取数（清洁度/deliverable 核对）
```
