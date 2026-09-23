# 消费仓如何使用 repo_template

## 工具链路径与写权

## AGENTS.md 同步分区协议

消费仓根 `AGENTS.md` 按固定标题分为三类内容：

|区段|同步行为|消费仓可定制范围|
|---|---|---|
|项目介绍（`## 目录与读写规则` 之前）|绝不更新，始终保留消费仓内容|允许补充项目介绍和项目专属规则|
|`## 目录与读写规则`|脚本只报告差异，不覆盖；由 Agent 对照模板智能语义合并|允许增删项目目录、写权和项目专属约定|
|`## 开发原则`|每次同步从模板强制更新|不允许消费仓改写|

`repo_sync.py plan` 会分别展示三部分状态。`apply` 只自动替换 `## 开发原则`；项目介绍和 `## 目录与读写规则` 均不由脚本覆盖。Agent 必须在同步时读取模板与消费仓差异，完成目录与读写规则的语义合并，并确认项目介绍未被改动。缺少这两个标题时，脚本对旧版模板继续使用旧的整文件裁定逻辑；新模板必须包含这两个标题。

消费仓允许修改的 AGENTS.md 范围只有项目介绍和 `## 目录与读写规则`；`## 开发原则` 及其后的模板规则不应由消费仓自行改写。

写权归属列声明路径的写入责任与时机；具体步骤见对应 skill 或文件内注释。

|路径|用途|写权归属|
|---|---|---|
|`.repo_template/docs/task_template/`|task 文件模板（非工作项）|只改模板本身|
|`docs/tasks_index.json` / `docs/archive/tasks_index.json`|活跃/归档 task 派生索引|工作区可由 `add`/`edit`/`drop`/`rewind`/`purge` 重建；入库 commit：维护期随操作提交；集成时由 `integrate` / `integrate-chain` 重建并放入同一个 merge commit；`list` 只读，`list --rebuild` 手动重建；不进 task worktree 的执行 commit|
|`docs/archive/tasks_audit.log`|rewind/purge 审计（append-only）|仅 `.repo_template/scripts/task.py rewind` / `purge` 独占 append，禁止 agent 手动修改|
|`docs/runtime/dispatch_ledger.jsonl`|attempt 控制面（append-only；已 gitignore，仅主仓）|exact identity 为 `(tid, attempt, execution_id)`；生命周期只经 `task.py attempt reserve/terminal/report` 写入，`integrate` / `integrate-chain` 写 `integrated`；`ledger tail` 只读；禁止手工编辑|
|`docs/runtime/goal_queue.json`|goal 模式冻结队列快照（已 gitignore，仅主仓）|仅 `task.py goal` 写入（首次冻结，或显式 tid / `--reset` 覆盖；无参已有快照只读）；`task.py goal-check` 只读；禁止手工编辑|
|`.repo_template/docs/review_prompts/`|review prompt 模板|改审查标准时更新|
|`.repo_template/docs/spike_report_template.md`|spike 报告模板|只改模板本身|
|`.repo_template/skills/`|skill 正文|改 skill 走文档纪律；不放业务代码|
|`.claude/skills/` / `.agents/skills/`|指向 `.repo_template/skills/` 的软链|只维护软链|
|`.opencode/commands/`|各 skill 的 opencode `/` 触发器（由 SKILL.md description 生成，调 `skill` 工具执行）|只读（改 SKILL.md 后重跑 `link-skills`）；手写命令保留|
|`.repo_template/scripts/`|模板自带 task 工具链：`task.py` 是 CLI/兼容 façade，业务实现位于 `repo_task/`，另含 pending.py/findings.py/spikes.py 等|仅模板演进时修改；复制或维护必须保留 `task.py` 与完整 `repo_task/`，并随模板复制进新项目|
|`../{repo}_{tid}/`（仓库外）|task 工作副本（git worktree）|`start` 仅从主仓默认分支调用（不要求干净，主仓未提交改动保留不动）：链式拓扑以 `--base` 指向上一已完成 task 分支；active task 的实施、测试、review、finish/drop 只在自身 worktree 执行；每个 task 一个执行 commit，实施阶段写 exact identity 的 `handoff.json`，调度阶段以同一 identity 清理 worktree 并合并；本地 `.env` 软链回主仓|

## 命令执行约定

本文与 skill 中的 `task.py`、`pending.py`、`repo_sync.py` 等均指消费仓根下 `.repo_template/scripts/` 中的同名脚本，用 `python3 .repo_template/scripts/<脚本名>` 调用；参数先查该子命令 `--help`。维护/控制面命令在主仓执行，task 实施命令在已登记 worktree 执行。`{tid}`、`{sid}`、`{slug}` 等取实际条目，不原样执行占位示例。脚本与命令示例按 POSIX 环境书写（Linux/macOS/WSL/Git Bash）；Windows 原生 shell 下 `python3`/`command -v` 等不可直接用，经 WSL/Git Bash 执行或换成等价调用（`python`、`Get-Command`）。工具链脚本本身支持 Windows（代码含 `os.name == "nt"` 分支），仅文档示例假定 POSIX。

运行前核对目标目录和 `git status --short`，保护已有改动。非零退出先检查报错与现场，不盲目重复写操作；`--write`、`apply`、merge 和删除仍受相应 skill 的授权条件约束。涉及迁移的脚本可能执行 `git mv` / 暂存，执行后同时检查工作区与暂存区。

## 命名与格式

- `AGENTS.md`、`CLAUDE.md`、`README.md` 是工具入口例外。
- task 编号：占位 `{tid}`，值小写 `t001`、`t042`…。目录 / 分支 / finding / worktree：`docs/tasks/{tid}_{slug}/`、`{tid}_{slug}`、`{tid}_code_fNNN`、`../{repo}_{tid}`。`{repo}` 取消费仓目录名。
- spike 编号：占位 `{sid}`，值小写 `s001`、`s003`…。目录：`docs/spikes/{sid}_{slug}/`。
- 总账编号：待办与发现均为一条目一文件，文件名 `pNNN_{slug}.md` / `dNNN_{slug}.md`，编号来自文件名。条目只经 `.repo_template/scripts/pending.py new` 与 `findings.py new` 创建——脚本在 git 公共目录的排他锁内完成「扫描全部本地分支与 worktree 取号 → 建文件」，并发执行不会撞号。`pNNN` 跨 `docs/pending/todo/`、`docs/pending/parked/`、`docs/archive/pending/` 共享全局序列，`dNNN` 在 `docs/findings/` 内递增；历史编号均不复用，不维护索引文件。spike 是目录型条目（`docs/spikes/sNNN_{slug}/`），由 `.repo_template/scripts/spikes.py new` 同法锁内分配，`sNNN` 与 `docs/archive/spikes/` 共享序列。
- AC 编号：spec 验收标准每条行为 AC 用 `AC-NNN`（三位十进制，task 内从 1 顺序编号）。编号一旦分配永久归属，删除后不复用（允许断号，不强制连续），新增用下一个编号。`handoff.json` 的 `ac_evidence` 键引用同一编号，须精确覆盖 spec 验收标准全部 AC——缺或多都阻断合入。编号规范属 spec 模板门禁，见 `.repo_template/docs/task_template/spec.md`。
- 占位示例（模板、示例行）不得占用真实 `tid` / `sid` / `pNNN`，也不得当作 active 工作项执行。
- Markdown 嵌套内容缩进 4 空格，禁止 tab。
- 非归档 Markdown 统一用 md_kx 格式化（`.repo_template/scripts/md_format.py`），表用 `compact`（`|a|b|`）。md_kx 来源 [TuTouPower/md_kx](https://github.com/TuTouPower/md_kx)（PyPI 发行名 `md-kx`，命令 `md_kx`），通常已在开发机全局安装（`uv tool install md-kx`）；消费仓不逐仓安装，缺二进制时 `md_format.py` / pre-commit 会在报错里给出来源与安装入口。格式由 `.md_kx.toml` 统一，禁止 prettier / 按列 pad。commit 由 pre-commit hook 强制（`.repo_template/hooks/pre-commit`，格式化本次 staged 的 `.md` 并重新暂存；工作区与 index 不一致则拒绝），需先 `python3 .repo_template/scripts/repo_sync.py install-hooks` 启用 `core.hooksPath`（已有其它 hooksPath 须 `--force`）；临时手动格式化用 `python3 .repo_template/scripts/md_format.py --changed`，commit 前 `--check` 为绿。
- 消费仓 `prettier --check .` 豁免模板侧路径：分发静态文件（两 `package.json`、`view_static/` 看板 UI——模板自有 `2` 空格/单引号风格、`test_chain_plan_cases.js`）、同步状态（`.repo_template/sync_state.json`，每轮 `apply` 重写）、派生索引（`docs/tasks_index.json`、`docs/archive/tasks_index.json`，可重建）、任务产物（`docs/**/handoff.json`，逐任务生成），以及本地生成的 `.opencode/package.json` / `package-lock.json`（`prettier` 不认嵌套 `.gitignore`）。`repo_sync.py apply` 机械追加到消费仓 `.prettierignore`（消费独有规则保留，去重），`status` / `plan` 展示缺失项；模板文件不随消费仓 `tabWidth` / 引号配置重排，消费侧不手改、不逐个加 `ignore`。同步改写消费仓自有 JSON（`.claude/settings.json`、MCP）时沿用原缩进，不弄红门禁。`.github/workflows/repo-template-ci.yml` 已下线（`b3e5c8f` 起不再分发），残留时 `status` / `plan` / `apply` 警告，确认无消费定制后手动删除。
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
|`template-issue-report`|发现模板仓带入的问题（bug 或设计/约定需求）时，本地保留 `.scratch/repo_template_issues/` 完整报告，脱敏后提交模板仓 GitHub issue；不可用时离线交接；只观察现象、不定位根因、不给方案|
|`repo-template-sync`|消费项目从模板仓同步工具链；审批通过后才 commit|

多会话并发：用户自决开多个会话各跑 `task-run`；`task.py plan` 取本波并发链，`task.py view --serve` 看看板。无自动调度器。

内部调用：

|skill|职责|
|---|---|
|`task-work`|在 task worktree 实施并写 `handoff.json`（由 `task-run` 调用）|
|`task-integrate`|单 task 或链式合并回主干（由 `task-run` 调用）|

### 跨 skill 调用契约

|调用|输入与授权|输出与恢复|写域 / 提交责任|
|---|---|---|---|
|task-create → preflight --creation|已批准创建的 backlog；可含已分类阻塞事项|结构/AC/占位符错误 FAIL；BLOCKING 为 WARN，仍禁止 start|task 目录 + 派生 index；用户同意后创建 commit|
|task-run → task-work|tid、原 exact identity、登记 worktree、持久 review_limit/verify_limit；仅执行授权|执行 commit 或记录阻塞并关闭当前 attempt；中断先 recovery 读阶段|当前 worktree；一个执行 commit，不写主仓 attempt|
|task-work → task-bug analysis-only|现象与父 task 写域，不传递立项/提交授权|pNNN 或分析阻断，仅执行分析登记模式，不进入立项和提交阶段|.scratch 与本次 pending；提交归父 task|
|task-work → review checker|实际 task_dir（finish 后为 archive）与最新报告|PASS / FAIL / INCOMPLETE，按 next_action 处理，exit 0 不等于 PASS|只读；checker 不修改报告或提高预算|
|task-run → task-integrate|逐成员 exact cleanup；整链完成后用户另行批准合并|Git pending merge → 验证 → commit；失败 merge --abort|仅主仓；派生 index 进入同一个 merge commit|

`task.py recovery {tid}` 只读输出 phase、原 identity、worktree/task_dir 和下一步；不会自动 reserve、commit 或清理。`review_limit` / `verify_limit` 在 task front matter 持久保存，旧 task 默认 5；只有用户批准后由 `limits --review/--verify ... --reason ...` 增加，新 attempt 不清零历史轮次。

创建有效性用 `preflight {tid} --creation`；执行就绪仍用 `preflight {tid} --allow-backlog`，执行期严格验证用 `--require-verified`。前者不能替代后两者。

review 指纹绑定实际交付内容（包含当前 task 的 spec、新文件、mode 与软链变化），不随暂存、提交或 finish 的目录迁移改变。cleanup/integrate 对 done 成员从最终提交读取真实报告及处置表，重算同一指纹；handoff 的 review 摘要不能代替 PASS 证据。升级前的旧指纹不自动迁移为 PASS，须重新审阅；如已提交且 review 门禁失败（stale / missing / format_error / FAIL），保留分支/证据并请用户决定恢复方式，未经用户同意不得 amend；用户同意后唯一合法修复是把补审证据 amend 进同一个执行 commit（first parent 必须仍为 diff_anchor，只改 review 过程文件，禁止第二个 commit、rewind、reserve），amend 后 worktree 干净再按原 identity cleanup。

workflow schema 不做运行时兼容。`repo-template-sync` 的 `apply` 会强制更新主仓 `docs/tasks/` 中的存量 spec/task 模板块（包括把旧 `status: blocked` 改为 `active`、补齐轮次上限字段）；存在已登记 task worktree 时拒绝 apply，必须先完成或 rewind，避免主仓与执行分支各用一套 schema。更新后工具链直接拒绝旧字段和旧状态，不保留双轨解析。

Git 原生 integrate 的内容校验依赖 `git merge-tree --write-tree --name-only`，最低要求 Git 2.38；工具会在执行 `git merge --no-commit` 前检查，版本不足时不会留下 pending merge。

## workflow 示例

`/task-create` → `/task-schedule` → `task.py plan`（本波链）/ `view --serve` → 一个或多个会话 `/task-run`（多会话手动并发各跑一段；状态变后重跑 `plan` 得下一批）。goal 模式自治跑队列：先 `task.py goal` 冻结队列并粘贴其输出的 `/goal` 行，终态以 `task.py goal-check` marker 判定。

goal 快照是主仓级单份文件；多会话不要互相覆盖队列。无参已有快照只展示；`--reset` 直接重建并覆盖，显式 tid 改变队列时需确认（`--yes` 表示已确认）。覆盖前保存需要保留的队列，成员与顺序无法确认时停止。`goal-check` 的退出码为 COMPLETE=0、STOPPED=3、INCOMPLETE=2，快照错误为 1；合法停止不等于全部完成。

### `.repo_template/scripts/` 使用示例

```bash
python3 .repo_template/scripts/task.py --help        # 所有子命令、参数与用法
python3 .repo_template/scripts/pending.py --help     # 待办总账
python3 .repo_template/scripts/findings.py --help    # 技术发现
python3 .repo_template/scripts/spikes.py --help      # 技术 spike
python3 .repo_template/scripts/repo_state.py --help  # 完整工作树 vs baseline 取数（清洁度/deliverable 核对）
```
