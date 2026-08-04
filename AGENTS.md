{一句话介绍：这个项目是什么、给谁用。} 这是代码仓库的仓库模板，用户创建代码库时复制这个作为起点。

本文件是 agent 行为入口：目录权责、状态机与 skill 路由。只加载当前任务所需文档。

命名与格式约定见 `docs/blueprint/conventions.md`「命名与格式」。

## 目录与读写规则

写权归属列声明路径的写入责任与时机；具体步骤见对应 skill 或文件内注释。

| 路径 | 用途 | 写权归属 |
|------|------|----------|
| `docs/specs_index.md` | 当前生效 spec 清单（在表即生效） | task 收尾时更新；废弃删除行 |
| `docs/specs/<slug>.md` | 需求级 spec（按已完成 task 累积） | task 收尾时累积更新；废弃移入 `docs/archive/specs/` |
| `docs/tasks/{tid}_{slug}/` | task 工作区兼**状态权威**（backlog 起即存在） | `spec.md` / `task.md` 正文由实现侧写；`task.md` front matter 只经 `scripts/repo_template/task.py`；reviewer 写 `review_code.md` / `review_test.md`（`single` 级写 `review_general.md`）；`finish`/`drop` 由脚本移入 archive |
| `docs/tasks/task_template/` | task 文件模板（非工作项） | 只改模板本身 |
| `docs/archive/tasks/{tid}_{slug}/` | 已归档 task 工作区 | 仅由 `scripts/repo_template/task.py finish` / `drop` 从 `docs/tasks/` 移入；内部文件只准新增 |
| `docs/tasks_index.json` / `docs/archive/tasks_index.json` | 活跃/归档 task 派生索引 | 工作区可由 `add`/`edit`/`rewind`/`purge` 重建；入库 commit：维护期随操作提交，合并后由 `integrate` 单独 chore commit；`list` 只读，`list --rebuild` 手动重建；不进 task worktree 的执行 commit |
| `docs/archive/tasks_audit.log` | rewind/purge 审计（append-only） | 仅 `scripts/repo_template/task.py rewind` / `purge` 独占 append，禁止 agent 手动修改 |
| `docs/runtime/dispatch_ledger.jsonl` | 并行调度账本（attempt 事件，append-only；已 gitignore，仅主仓） | `start`/`integrate` 自动 append；coordinator 经 `task.py ledger record`；禁止手工编辑 |
| `docs/handoff.md` | 项目级交接（仅最新一节） | 记录须含 branch 与交出时 head_commit；过时段落迁 `docs/archive/handoff.md` |
| `docs/pending/{todo,parked}/pNNN_{slug}.md` | 待办与不办总账（一条目一文件，统一 `pNNN`；`parked/`=用户确认暂搁，不迁 archive） | 条目创建与迁移只经 `scripts/repo_template/pending.py`；`task-bug` 登记 bug；`task-work` 收尾闭环迁 archive、遗留建条目；`task-from-pending` 只捞 `todo/` 建 task；`repo-hygiene` 补迁漏项、`parked/` 保留不动 |
| `docs/findings/dNNN_{slug}.md` | 已验证的技术发现（一条目一文件，跨 task 复用，`dNNN`） | 条目创建只经 `scripts/repo_template/findings.py`；只新增与就地修订，不迁 archive；spike 收尾或日常验证出的事实写入 |
| `docs/archive/pending/pNNN_{slug}.md` | 已闭环待办 | 仅由 `scripts/repo_template/pending.py archive` 迁入；只准新增 |
| `docs/archive/handoff.md` | handoff 的过时历史 | 只追加；由对应 skill 在用户调用时迁入 |
| `docs/blueprint/` | 当前长期真相：架构、领域、约定、决策、测试 | finalization 时更新；写代码或文档前读 `conventions.md`，改跨模块行为前读 `architecture.md`，历史取舍读 `decisions.md`，`{doctor_cmd}` / `{test_cmd}` / `{blackbox_verify}` 在 `testing.md` |
| `docs/reviews/prompts/` | review prompt 模板 | 改审查标准时更新 |
| `docs/reviews/review_*/` | 多路 review 会话产物（my-review 等外部评审生成） | 报告 `review_*.md` 入库；`_meta/` 过程文件已 gitignore；确认过时由 `repo-hygiene` 迁 `docs/archive/reviews/` |
| `docs/spikes/report_template.md` | spike 报告模板 | 只改模板本身 |
| `docs/spikes/{sid}_{slug}/` | 当前 spike（`report.md` 必需；有实验代码建 `code/`） | 目录创建只经 `scripts/repo_template/spikes.py new`；流程见 `task-work`（Step 1 spike 项）；结论入 `docs/findings/`；完结由 `repo-hygiene` 迁 `docs/archive/spikes/` |
| `.agents/skills/` | 项目 skill 正文 | 改 skill 走文档纪律；不放业务代码 |
| `.claude/skills/` | 指向 `.agents/skills/` 的软链 | 只维护软链 |
| `docs/guides/` | 给人看的使用指南 | 给人读，不写 agent 行为规则 |
| `docs/archive/` | 完结或终止的历史 | 镜像原路径；内部文件只准新增 |
| `docs_repo/` | **仅本模板仓**的设计笔记/复盘（非业务） | 不参与 task 状态机；**复制新项目时不得带入** |
| `schemas/` | 跨服务接口契约 | 改契约走 task 流程 |
| `config/` | 配置（默认 + 环境覆盖 + `.env.example`） | 仅 `.env.example` 入库；真值写本地 `.env` |
| `src/` `tests/` `assets/` | 源码、测试、静态源 | 仅在 task 执行期按 spec 修改；debug 复现不得写入 |
| `scripts/` | 用户项目脚本 | 仅在 task 执行期按 spec 修改；debug 复现不得写入 |
| `scripts/repo_template/` | 模板自带 task 工具链（task.py/pending.py/findings.py/spikes.py 等） | 仅模板演进时修改；随模板复制进新项目 |
| `artifacts/` `data/` `.scratch/` | 产物、运行数据、一次性草稿 | 运行与草稿；debug 复现和临时实验只写 `.scratch/`（已 gitignore）；需保留的 spike 验证材料写 `docs/spikes/{sid}_{slug}/code/` |
| `../{repo}_{tid}/`（仓库外） | task 工作副本（git worktree） | `start` 仅从主仓默认分支调用（不要求干净，主仓未提交改动保留不动）：并行=主干 HEAD，串行=`--base` 上一已完成 task 分支（须先 cleanup-worktree）；active/blocked task 的实施、测试、review、finish/drop 只在自身 worktree 执行；执行 commit 后由 coordinator 清理 worktree 并合并分支；本地 `.env` 软链回主仓 |

## 开发工作流

### 开发原则

- specs driven：需求拆分为可独立验证的 task，填写 `spec.md`（契约区行为 AC 须非空）；版本号、底层库选型、目录结构不写进行为 AC，需要长期约束的写 `docs/blueprint/decisions.md`。
- TDD：可测部分先红后绿；测试须触达生产逻辑。实现变更让旧测试语义失效时，新增覆盖新语义的测试；旧测试原样保留或整体删除并写明理由，**禁止就地把旧测试的预期改成当前实现的输出**。
- 用户未明确允许或者不在 skill 流程时，绝不准手动直接更改未被 gitignore 的代码文件。
- task 状态读取优先级：登记 worktree → 未合并 task 分支 ref → 主干。进行中 task 的状态在其合并前不进主干；`list/show/preflight --ref` 用于只读分支快照，不能据主干旧 backlog 重复 start 或维护。
- task 执行期一个实现 commit；创建期、状态维护、index 维护与 merge commit 分开。每个 commit 必须独立可验证，有工程意义。
- 发现 commit 混入不属于当前工作的改动时，立即停止工作并向用户汇报；未经用户确认，不继续提交、合并或修正。
- task 状态：`backlog` / `active` / `blocked` / `done` / `dropped`。

### 执行角色与合并时机

串行（`task-run`）与并行（`task-dispatch`）是**两套不同拓扑**，合并时机与分支形态不同：

**串行 = 链式**。task 按执行顺序一个串一个成链，每个从上一个已完成 task 的分支创建（`--base`）。全部完成后一次性把链尾合并回主干，主干只进一次 merge commit。

```text
主干 ── t001 ── t002 ── t003 ──► 全部完成后 merge 链尾
```

**并行 = 扇出**。每个 task 从主干 HEAD 独立扇出，完成即合并回主干——快 task 先合并、先释放并发位、先解锁下游；慢 task 不阻塞任何人。

```text
        主干 ──┬── t001 ──► 完成即 merge
               ├── t002 ──► 完成即 merge
               └── t003 ──► 完成即 merge
```

两角色写域互不重叠：

| 角色 | 唯一写域 | 职责 | skill |
|------|---------|------|-------|
| worker | 自己的 task worktree | 实施、测试、黑盒、review、finish、执行 commit；交出 `{tid}: {branch} @ {sha}` | `task-work` |
| coordinator | 主仓 | task 创建、`start`、`cleanup-worktree`、合并、派生 index 重建、分支清理、合并后验证 | `task-integrate` / `task-dispatch` / `task-run` |

worker 不合并任何分支、不重建 index、不 push、不删分支、不清理自己的 worktree、不询问是否合并主干。主干只有 coordinator 一个写者且串行处理，因此不需要额外的锁。

串行当前会话同时担任两个角色（task 逐个跑、逐个 cleanup-worktree、最后一次性合链尾）；并行由 coordinator 派发多个 worker，自身不执行 task。合并动作都由 `scripts/repo_template/task.py integrate` 承担（串行带 `--chain`）。

合并主干需用户**会话级前置授权**：启动时一次性说明调度范围与合并动作，取得授权后按拓扑自动合并，不再逐 task 询问。合并环节只有四种情况停下来问用户：merge 冲突需裁决、合并后验证失败、task `blocked`、范围扩大。执行环节的停止条件另见各 skill。

`.claude/hooks/merge_guard.py` 拦截 Bash 工具里的 `git merge`（含 `--abort`，要求一次性 token）；`task.py integrate` 内部 merge 经 subprocess 不经 Bash 工具，由会话级授权覆盖，hook 不拦。两层职责分离：脚本通道 = 已授权入口，hook = 防 agent 在脚本外手动 merge。

### skill 调用

用户入口——由用户斜杠触发：

| 用户意图 | skill | 职责 |
|----------|-------|------|
| 新需求拆 task | `task-create` | 按**需求**拆建 backlog task；批量落盘后统一一个创建 commit |
| 分析 backlog task 调度图 | `task-schedule` | Agent 首次分析依赖/冲突并落盘；之后 `task.py view` 机械计算可跑集 |
| 并行跑多个 task | `task-dispatch` | coordinator：reconcile 驱动——派发 worker、收汇报分诊、完成即合并、解锁补位、cron 兜底 |
| 串行跑完待做 task | `task-run` | 链式串行：逐个执行+cleanup；链尾一次 merge |
| 待做 task 还缺我什么 | `task-preflight` | 只读汇总缺口 |
| 修 bug / 复现 / 根因立项 | `task-bug` | 复现/根因（仅 `.scratch/`）→ 建修复 task + 补测分析 → commit 创建物 |
| 把待办转成 task | `task-from-pending` | 从 `docs/pending/todo/` 重建 task 并回写归档 |
| 多个 backlog task 合并成一个 | `task-merge` | 仅 backlog；并 spec/task → `edit` 目标 → `drop` 源 |
| 整理 handoff/pending/过时文档 | `repo-hygiene` | 迁 archive；不手改 task 状态 |
| 清理缓存/无用文件 | `repo-cleanup` | 默认 dry-run |

内部件——由上表 skill 链式调用，用户通常不直接触发：

| skill | 调用方 | 职责 |
|-------|--------|------|
| `task-work` | `task-dispatch` 派发给 worker；`task-run` 自调 | worker：在自身 worktree 实施至执行 commit |
| `task-integrate` | `task-dispatch` 收汇报后；`task-run` 链全部完成后 | coordinator：并行=cleanup-worktree → 合单分支 → 重建 index → 验证 → 删分支；串行 `--chain`=cleanup-worktree → 只合链尾 → 重建 index → 验证 → 删整条链 |

典型路径：`/task-create` → `/task-schedule` → `/task-dispatch`（并行）或 `/task-run`（串行）。
task 彼此冲突面大时并行无收益，看 `task-schedule` 输出的全景图决定。

### `scripts/repo_template/task.py` 使用示例

```bash
python3 scripts/repo_template/task.py --help                  # 显示完整子命令与参数
python3 scripts/repo_template/task.py list                    # 当前工作区所有 task
python3 scripts/repo_template/task.py list --status backlog   # 按状态过滤
python3 scripts/repo_template/task.py show t001               # 当前工作区某 task 详情
python3 scripts/repo_template/task.py show t001 --ref t003_x  # 某本地分支中的累计状态
python3 scripts/repo_template/task.py preflight t002 --allow-backlog --ref t001_x # 只读检查分支中 backlog
python3 scripts/repo_template/task.py start t002               # 并行扇出：从主干 HEAD 建 worktree
python3 scripts/repo_template/task.py start t003 --base t002_x # 串行链式：从上一 task 分支建 worktree
python3 scripts/repo_template/task.py cleanup-worktree t001    # 执行 commit 后清理 worktree，保留分支
python3 scripts/repo_template/task.py integrate t001           # 并行：合单个分支进主干 + 重建 index + 删分支
python3 scripts/repo_template/task.py integrate t001 --chain   # 串行：只合链尾 + 删整条链分支
python3 scripts/repo_template/task.py integrate t001 --continue # 冲突解决并 git add 后继续
python3 scripts/repo_template/task.py edit t001 --title "新标题" --review-level single
python3 scripts/repo_template/task.py edit t005 --depends-on "t001,t003" --conflicts-with "t006" --schedule-status scheduled
python3 scripts/repo_template/task.py view                         # task 全景：运行中/待运行分组/已结束；冲突阻塞行附带被阻塞 task 标题
python3 scripts/repo_template/task.py ps [--all]                   # 在飞 attempt 活表（账本+refs+worktree 观察派生）
python3 scripts/repo_template/task.py reconcile --limit 3 [--model-ladder "opus>haiku"] # 只读行动计划：dispatch/redispatch/integrate/escalate
python3 scripts/repo_template/task.py ledger record --event dispatch --tid t001 --model haiku  # coordinator 落账
python3 scripts/repo_template/task.py ledger tail --tid t001       # 读调度账本
python3 scripts/repo_template/task.py rewind t001 --to backlog --reason "需补 spec"   # active/blocked → backlog
python3 scripts/repo_template/task.py purge t001 --reason "误建"                       # backlog → deleted（仅从未开干）
scripts/repo_template/pending.py new --slug cli_exit_code [--kind bug]                # 锁内取号并建条目文件
scripts/repo_template/pending.py list --state all                                     # 列举 todo/parked/archived
scripts/repo_template/pending.py archive p047 p051 --fix-ref t012                     # dry-run：拟迁闭环条目
scripts/repo_template/pending.py archive p047 p051 --fix-ref t012 --write             # 落盘迁移（拒迁 parked）
scripts/repo_template/pending.py park p047 --reason "等外部依赖" --write               # todo → parked
scripts/repo_template/pending.py revive p047 --write                                  # parked → todo
scripts/repo_template/findings.py new --slug uv_lock_platform_marker                  # 锁内取号并建发现条目
scripts/repo_template/spikes.py new --slug uv_lock_platform_marker                    # 锁内取号并建 spike 目录
```

## 文档规范

- 本节主要约束长期真相文档。task、review、spike、finding、audit、archive 等过程或证据型文档按各自模板保留编号、来源、实现位置和验证记录。
- 结构或语义变化时，先确定目标语义及权威落点，再替换最小自包含语义块。避免逐句追加造成新旧表述并存。
- 同一事实、规则或结论只保留一处权威定义。其他位置可保留不引入新语义的简短摘要，并链接权威文档标题；避免复制完整正文或仅按章节编号引用。
- 长期真相文档直接陈述当前事实，不使用过程编号、来源说明或实现位置作为正文依据，例如 `(D24-N3)`、`(S15)`、`(t012)`、`根据 D24 决定`、`impl at ts/X.ts`。结构化字段及过程、证据型文档不受此限。
- 多种合理理解会影响行为、权威归属或跨文档同步范围时，先澄清再修改。
- 优先先写目标状态、允许行为或执行动作等正向描述。仅安全边界、不可逆操作、写权边界及工作流禁区等使用明确否定句。
- 完成后检查：旧表述残留、重复权威定义、矛盾结论、失效链接、索引或引用遗漏、过程来源表述残留。
