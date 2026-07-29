{一句话介绍：这个项目是什么、给谁用。} 这是代码仓库的仓库模板，用户创建代码库时复制这个作为起点。

本文件是 agent 行为入口：目录权责、状态机与 skill 路由。只加载当前任务所需文档。

命名与格式约定见 `docs/blueprint/conventions.md`「命名与格式」。

## 目录与读写规则

写权归属列声明路径的写入责任与时机；具体步骤见对应 skill 或文件内注释。

| 路径 | 用途 | 写权归属 |
|------|------|----------|
| `docs/specs_index.md` | 当前生效 spec 清单（在表即生效） | task 收尾时更新；废弃删除行 |
| `docs/specs/<slug>.md` | 需求级 spec（按已完成 task 累积） | task 收尾时累积更新；废弃移入 `docs/archive/specs/` |
| `docs/tasks/{tid}_{slug}/` | task 工作区兼**状态权威**（backlog 起即存在） | `spec.md` / `task.md` 正文由实现侧写；`task.md` front matter 只经 `scripts/task.py`；reviewer 写 `review_code.md` / `review_test.md`（`single` 级写 `review_general.md`）；`finish`/`drop` 由脚本移入 archive |
| `docs/tasks/task_template/` | task 文件模板（非工作项） | 只改模板本身 |
| `docs/archive/tasks/{tid}_{slug}/` | 已归档 task 工作区 | 仅由 `scripts/task.py finish` / `drop` 从 `docs/tasks/` 移入；内部文件只准新增 |
| `docs/tasks_index.json` / `docs/archive/tasks_index.json` | 活跃/归档 task 派生索引 | 仅主仓协调点由 `scripts/task.py` 重建；`list` 只读，`list --rebuild` 手动重建；入库但不进 task worktree 的执行 commit |
| `docs/archive/tasks_audit.log` | rewind/purge 审计（append-only） | 仅 `scripts/task.py rewind` / `purge` 独占 append，禁止 agent 手动修改 |
| `docs/handoff.md` | 项目级交接（仅最新一节） | 记录须含 branch 与交出时 head_commit；过时段落迁 `docs/archive/handoff.md` |
| `docs/pending.md` | 待办总账：未修 bug + 遗留待办（统一 `pNNN`） | `task-bug` 登记未修 bug；`tasks-run` 收尾闭环并迁 archive；`pending-to-task` 捞遗留待办和 bug 建 task；`repo-hygiene` 补迁漏项 |
| `docs/findings.md` | 已验证的技术发现（跨 task 复用，`dNNN`） | 只追加与就地修订，不迁 archive；spike 收尾或日常验证出的事实写入 |
| `docs/archive/{handoff,pending}.md` | 对应文件的已闭环/过时历史 | 只追加；由对应 skill 在用户调用时迁入 |
| `docs/blueprint/` | 当前长期真相：架构、领域、约定、决策、测试 | finalization 时更新；写代码或文档前读 `conventions.md`，改跨模块行为前读 `architecture.md`，历史取舍读 `decisions.md`，`{doctor_cmd}` / `{test_cmd}` / `{blackbox_verify}` 在 `testing.md` |
| `docs/reviews/prompts/` | review prompt 模板 | 改审查标准时更新 |
| `docs/reviews/review_*/` | 多路 review 会话产物（my-review 等外部评审生成） | 报告 `review_*.md` 入库；`_meta/` 过程文件已 gitignore；确认过时由 `repo-hygiene` 迁 `docs/archive/reviews/` |
| `docs/spikes/report_template.md` | spike 报告模板 | 只改模板本身 |
| `docs/spikes/{sid}_{slug}/` | 当前 spike（`report.md` 必需；有实验代码建 `code/`） | 流程见 `tasks-run`（Step 1 spike 项）；结论入 `docs/findings.md`；完结由 `repo-hygiene` 迁 `docs/archive/spikes/` |
| `.agents/skills/` | 项目 skill 正文 | 改 skill 走文档纪律；不放业务代码 |
| `.claude/skills/` | 指向 `.agents/skills/` 的软链 | 只维护软链 |
| `docs/guides/` | 给人看的使用指南 | 给人读，不写 agent 行为规则 |
| `docs/archive/` | 完结或终止的历史 | 镜像原路径；内部文件只准新增 |
| `docs_repo/` | **仅本模板仓**的设计笔记/复盘（非业务） | 不参与 task 状态机；**复制新项目时不得带入** |
| `schemas/` | 跨服务接口契约 | 改契约走 task 流程 |
| `config/` | 配置（默认 + 环境覆盖 + `.env.example`） | 仅 `.env.example` 入库；真值写本地 `.env` |
| `src/` `tests/` `scripts/` `assets/` | 源码、测试、脚本、静态源 | 仅在 task 执行期按 spec 修改；debug 复现不得写入 |
| `artifacts/` `data/` `.scratch/` | 产物、运行数据、一次性草稿 | 运行与草稿；debug 复现和临时实验只写 `.scratch/`（已 gitignore）；需保留的 spike 验证材料写 `docs/spikes/{sid}_{slug}/code/` |
| `../{repo}_{tid}/`（仓库外） | task 工作副本（git worktree） | `start` 仅在干净主仓默认分支创建；active/blocked task 的实施、测试、review、finish/drop 只在自身 worktree 执行；合并后从主仓清理；本地 `.env` 软链回主仓 |

## 开发工作流

### 开发原则

- specs driven：需求拆分为可独立验证的 task，填写 `spec.md`（契约区行为 AC 须非空）；版本号、底层库选型、目录结构不写进行为 AC，需要长期约束的写 `docs/blueprint/decisions.md`。
- TDD：可测部分先红后绿；测试须触达生产逻辑。实现变更让旧测试语义失效时，新增覆盖新语义的测试；旧测试原样保留或整体删除并写明理由，**禁止就地把旧测试的预期改成当前实现的输出**。
- 用户未明确允许或者不在 skill 流程时，绝不准手动直接更改未被 gitignore 的代码文件。
- 主仓只做 task 创建、start、合并、合并后的派生 index 重建和 worktree 清理；除非用户明确允许否则不在主仓直接 `task-run`。
- `start` 无绕过参数；只能从干净主仓默认分支创建 task branch/worktree。active/blocked task 的 finish/drop 必须在登记的自身 worktree 执行。
- task 执行期一个实现 commit；创建期、状态维护、index 维护与 merge commit 分开。task worktree 的执行 commit 不提交派生 index；index 仅在主仓协调点重建并提交。每个 commit 必须独立可验证，有工程意义。
- task 状态：`backlog` / `active` / `blocked` / `done` / `dropped`。

### skill 调用

仅在用户斜杠或其它 skill 链式调用时进入；**禁止**自行进入。

| 用户意图 | skill | 职责 |
|----------|-------|------|
| 待做 task 还缺我什么 | `tasks-preflight` | 只读汇总缺口 |
| 哪些 backlog task 能并发 | `tasks-parallel` | 只读；以进行中 task 与已存在分支为基线出并发分组 |
| 修 bug / 复现 / 根因立项 | `task-bug` | 复现/根因（仅 `.scratch/`）→ 建修复 task + 补测分析 → commit 创建物 |
| 新需求拆 task | `task-create` | 按**需求**拆建 backlog task；一个 task 目录一个 commit |
| 把遗留待办转成 task | `pending-to-task` | 从 `docs/pending.md`「遗留待办」去重建 task 并回写归档 |
| 多个 backlog task 合并成一个 | `tasks-merge` | 仅 backlog；并 spec/task → `edit` 目标 → `drop` 源 |
| 串行跑完待做 task | `tasks-run` | **串行**执行；每个 task 在自身 worktree 实施 |
| 整理 handoff/pending/过时文档 | `repo-hygiene` | 迁 archive；不手改 task 状态 |
| 清理缓存/无用文件 | `repo-clean` | 默认 dry-run |

### `scripts/task.py` 使用示例

```bash
python3 scripts/task.py --help                  # 显示完整子命令与参数
python3 scripts/task.py list                    # 当前工作区所有 task
python3 scripts/task.py list --status backlog   # 按状态过滤
python3 scripts/task.py show t001               # 某 task 的 front matter 详情
python3 scripts/task.py edit t001 --title "新标题" --review-level single
python3 scripts/task.py rewind t001 --to backlog --reason "需补 spec"   # active/blocked → backlog
python3 scripts/task.py purge t001 --reason "误建"                       # backlog → deleted（仅从未开干）
scripts/pending.py next                         # 当前+归档总账的下一个 pNNN 编号
```

## 文档规范

- 结构或语义变化时，先确定最终表述，修改最小完整语义块，禁止逐句打补丁。
- 同一事实、规则或结论只保留一个权威定义；其他位置使用稳定标题或标识引用，避免复制正文和可能失效的编号引用。
- 文档正文直接陈述事实，禁止元引用：不嵌入决策/spike/ticket/task 编号（`(D24-N3)` `(S15)` `(t012)`）；不嵌入来源或实现位置标注（`(根据 D24 决定)` `(D25 wrapper)` `(impl at ts/X.ts)`）；不嵌入「本节根据 X 决定 Y」式叙述。结构化字段（表格列、`fix_ref`、spec 上下文区的 `来源`、commit subject、测试文件名）按各文件格式约定使用，不受此限。
- 存在多种合理理解时，先澄清再做跨文档修改。
- 优先使用正向描述；仅安全、不可逆操作、明确禁区三类场景使用否定句。
- 完成后检查：旧表述、重复内容、矛盾结论、失效引用、遗漏同步、元引用残留。
