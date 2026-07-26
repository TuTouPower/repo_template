# repo_template

Agent 友好的通用仓库模板。面向需要结构化 task、review、handoff 和 spike 记录的项目。

## 设计原则

1. **行为入口唯一**：规则、状态机、门禁、目录权责在 [`AGENTS.md`](AGENTS.md)；操作步骤在 `.agents/skills/`（`.claude/skills/*` 软链）。
2. **skill 仅用户触发**：`description: none` + `disable-model-invocation`；禁止 agent 凭语义自行开跑。路由表见 `AGENTS.md`「Task 工作流入口」。
3. **当前与历史分离**：active 在 `docs/tasks` 等；完结/过时/已修迁 `docs/archive/`。
4. **commit 策略**：创建期可一批 backlog；执行期一 task 一 commit。细节见 `AGENTS.md`。
5. **specs driven + TDD**：创建填 spec/plan（行为 AC 非空）；执行期先红后绿；收尾累积 `docs/specs/`。

路径与读写规则以 `AGENTS.md`「目录与读写规则」为准，本 README 不重复目录树。

## 初始化新项目

1. 复制模板到新项目时**不要带上 `docs_repo/`**（仅本模板仓维护笔记，不属于业务脚手架）。推荐：
   ```bash
   rsync -a --exclude docs_repo/ --exclude .git/ /path/to/repo_template/ /path/to/new_project/
   ```
   或 `git clone` / `cp -a` 后再**立刻删掉** `docs_repo/`。**禁止** `cp -r`（会把软链展开成副本，导致 `.agents/skills/` 与 `.claude/skills/` 分叉）。
2. 初始化版本控制。
3. 全局替换 `{project_name}`，填写项目一句话介绍和 `AGENTS.md` 硬约束（含 `{doctor_cmd}` / `{test_cmd}` / `{blackbox_cmd}`）。
4. 保持全部软链：`CLAUDE.md -> AGENTS.md`，以及 `.claude/skills/* -> ../../.agents/skills/*`。
5. 按技术栈补充依赖文件、工具配置和 `.gitignore`。
6. 填写 `docs/blueprint/architecture.md`、`domain.md`、`conventions.md` 初稿；`decisions.md` 初始可为空。
7. 确认 `docs/tasks_index.json`、`docs/specs_index.md` 无伪 active 数据；`docs/tasks/task_template/` 仅作复制源。

README 应改成项目自身介绍，不继续保留模板说明。

## 文档入口

- Agent 工作规则与目录权责：[`AGENTS.md`](AGENTS.md)
- 内容与格式约定：[`docs/blueprint/conventions.md`](docs/blueprint/conventions.md)
- 操作 skill：`.agents/skills/`（preflight / parallel / bug / create / debt / merge / run / hygiene / clean）
