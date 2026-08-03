# repo_template

Agent 友好的通用仓库模板。面向需要结构化 task、review、handoff 和 spike 记录的项目。

## 初始化新项目

1. 复制模板到新项目时**不要带上 `docs_repo/`**（仅本模板仓维护笔记，不属于业务脚手架）。推荐：
   ```bash
   rsync -a --exclude docs_repo/ --exclude .git/ --exclude .scratch/ --exclude .pytest_cache/ --exclude __pycache__/ --exclude 'docs/reviews/review_*/' /path/to/repo_template/ /path/to/new_project/
   ```
   或 `git clone` / `cp -a` 后再**立刻删掉** `docs_repo/`。**禁止** `cp -r`（会把软链展开成副本，导致 `.agents/skills/` 与 `.claude/skills/` 分叉）。
2. 初始化版本控制。
3. 全局替换 `{project_name}`，填写项目一句话介绍。
4. 保持全部软链：`CLAUDE.md -> AGENTS.md`，以及 `.claude/skills/* -> ../../.agents/skills/*`。
5. 按技术栈补充依赖文件、工具配置和 `.gitignore`。
6. 填写 `docs/blueprint/architecture.md`、`domain.md`、`conventions.md`、`testing.md` 初稿；`decisions.md` 初始可为空。
7. 确认 `docs/tasks/` 下无遗留 task 目录、`docs/specs_index.md` 无伪 active 数据；`docs/tasks/task_template/`（`spec.md` / `task.md`）仅作复制源。
8. 保留 `tests/repo_template/` 下模板自带的测试文件——它们持续验证 task 工具链（`scripts/repo_template/task.py`、`scripts/repo_template/pending.py`、`scripts/repo_template/spikes.py` 等）的正确性，新项目沿用这些脚本即沿用对应测试。

README 应改成项目自身介绍，不继续保留模板说明。

## 文档入口

- Agent 工作规则与目录权责：[`AGENTS.md`](AGENTS.md)
- 内容与格式约定：[`docs/blueprint/conventions.md`](docs/blueprint/conventions.md)
- 测试方法（`{doctor_cmd}` / `{test_cmd}` / `{blackbox_verify}`）：[`docs/blueprint/testing.md`](docs/blueprint/testing.md)
- 操作 skill：`/task-preflight`、`/task-schedule`、`/task-bug`、`/task-create`、`/task-from-pending`、`/task-merge`、`/task-run`、`/task-dispatch`、`/task-work`、`/task-integrate`、`/repo-hygiene`、`/repo-cleanup`
