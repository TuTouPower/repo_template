{一句话介绍：这个项目是什么、给谁用。} 这是代码仓库的仓库模板，用户创建代码库时复制这个作为起点。

本文件是 agent 行为入口：目录权责、状态机、门禁与 skill 路由。只加载当前任务所需文档。

## 命名

- `{tid}`：task 编号，形如 `t001`、`t042`（小写 `t` + 数字）。
- `{sid}`：spike 编号，形如 `s001`、`s003`（小写 `s` + 数字）。
- `{slug}`：小写 `snake_case`。
- task 目录：`docs/tasks/{tid}_{slug}/`；分支：`{tid}_{slug}`；worktree：`../{repo}_{tid}`。
- finding：`{tid}_code_fNNN` / `{tid}_test_fNNN`（本 task 内跨轮累计递增）。

## 目录与读写规则

写权归属列声明路径的写入责任与时机；具体步骤见对应 skill 或文件内注释。

| 路径 | 用途 | 写权归属 |
|------|------|----------|
| `docs/specs_index.md` | 当前生效 spec 清单（在表即生效） | task 收尾时更新；废弃删除行 |
| `docs/specs/<slug>.md` | 需求级 spec（按已完成 task 累积） | task 收尾时累积更新；废弃移入 `docs/archive/specs/` |
| `docs/tasks/{tid}_{slug}/` | task 工作区兼**状态权威**（backlog 起即存在） | `spec.md` / `task.md` 正文由实现侧写；`task.md` front matter 只经 `scripts/task.py`；reviewer 写 `review_code.md` / `review_test.md`；`finish`/`drop` 由脚本移入 archive |
| `docs/tasks/task_template/` | task 文件模板（非工作项） | 只改模板本身 |
| `docs/archive/tasks/{tid}_{slug}/` | 已归档 task 工作区 | 仅由 `scripts/task.py finish` / `drop` 从 `docs/tasks/` 移入；内部文件只准新增 |
| `docs/tasks_index.json` / `docs/archive/tasks_index.json` | 活跃/归档 task 派生索引（已 gitignore） | 由 `scripts/task.py` 自动重建；不入库、不手改 |
| `docs/archive/tasks_audit.log` | rewind/purge 审计（append-only） | 仅 `scripts/task.py rewind` / `purge` 独占 append，禁止 agent 手动修改 |
| `docs/handoff.md` | 项目级交接（仅最新一节） | 记录须含 branch 与交出时 head_commit；过时段落迁 `docs/archive/handoff.md` |
| `docs/pending.md` | 待办总账：未修 bug（`bNNN`）+ 遗留待办（`fNNN`） | `tasks-run` 收尾闭环并迁 archive；`pending-to-task` 捞条目建 task |
| `docs/findings.md` | 已验证的技术发现（跨 task 复用，`dNNN`） | 只追加与就地修订，不迁 archive；spike 收尾或日常验证出的事实写入 |
| `docs/archive/{handoff,pending}.md` | 对应文件的已闭环/过时历史 | 只追加；由对应 skill 在用户调用时迁入 |
| `docs/blueprint/` | 当前长期真相：架构、领域、约定、决策、测试 | finalization 时更新；写代码或文档前读 `conventions.md`，改跨模块行为前读 `architecture.md`，历史取舍读 `decisions.md`，`{doctor_cmd}` / `{test_cmd}` / `{blackbox_verify}` 在 `testing.md` |
| `docs/reviews/prompts/` | review prompt 模板 | 改审查标准时更新 |
| `docs/spikes/report_template.md` | spike 报告模板 | 只改模板本身 |
| `docs/spikes/{sid}_{slug}/` | 当前 spike（`report.md` 必需；有实验代码建 `code/`） | 流程见 `tasks-run` Step 1.6；结论入 `docs/findings.md` |
| `.agents/skills/` | 项目 skill 正文 | 改 skill 走文档纪律；不放业务代码 |
| `.claude/skills/` | 指向 `.agents/skills/` 的软链 | 只维护软链 |
| `docs/guides/` | 给人看的使用指南 | 给人读，不写 agent 行为规则 |
| `docs/archive/` | 完结或终止的历史 | 镜像原路径；内部文件只准新增 |
| `docs_repo/` | **仅本模板仓**的设计笔记/复盘（非业务） | 不参与 task 状态机；**复制新项目时不得带入** |
| `schemas/` | 跨服务接口契约 | 改契约走 task 流程 |
| `config/` | 配置（默认 + 环境覆盖 + `.env.example`） | 仅 `.env.example` 入库；真值写本地 `.env` |
| `src/` `tests/` `scripts/` `assets/` | 源码、测试、脚本、静态源 | 仅在 task 执行期按 spec 修改；debug 复现不得写入 |
| `artifacts/` `data/` `.scratch/` | 产物、运行数据、一次性草稿 | 运行与草稿；debug 复现/实验代码**只许** `.scratch/`（已 gitignore） |
| `../{repo}_{tid}/`（仓库外） | task 工作副本（git worktree） | 由 `scripts/task.py start` 建、`finish`/`drop`/`rewind` 移除；本地 `.env` 软链回主仓；不手工创建或删除 |

## 开发工作流

### 开发原则

- specs driven：先拆 task 并填写 `spec.md`（契约区行为 AC 须非空）；版本号、底层库选型、目录结构不写进行为 AC，需要长期约束的写 `docs/blueprint/decisions.md`。
- TDD：可测部分先红后绿；测试须触达生产逻辑。实现变更让旧测试语义失效时，新增覆盖新语义的测试；旧测试原样保留或整体删除并写明理由，**禁止就地把旧测试的预期改成当前实现的输出**。
- 用户未明确允许或者不在 skill 流程时，绝不准手动直接更改未被 gitignore 的代码文件。
- 需求拆分为一个或多个 task，强制一个 task 对应一个 commit，每个 commit 必须独立可验证，并且有工程意义。
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
| 串行跑完待做 task | `tasks-run` | **串行**执行；每 task 一个交付单元 |
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
```

## 文档规范

- 结构或语义变化时，先确定最终表述，修改最小完整语义块，禁止逐句打补丁。
- 同一事实、规则或结论只保留一个权威定义；其他位置使用稳定标题或标识引用，避免复制正文和可能失效的编号引用。
- 文档正文直接陈述事实，禁止元引用：不嵌入决策/spike/ticket 编号（`(D24-N3)` `(S15)`）、来源或实现位置标注（括注如 `(根据 D24 决定)` `(D25 wrapper)` `(impl at ts/X.ts)`，叙述如"本节根据 X 决定 Y"）。
- 存在多种合理理解时，先澄清再做跨文档修改。
- 优先使用正向描述；仅安全、不可逆操作、明确禁区三类场景使用否定句。
- 完成后检查：旧表述、重复内容、矛盾结论、失效引用、遗漏同步、元引用残留。
