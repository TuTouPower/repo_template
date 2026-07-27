# repo_template

Agent 友好的通用仓库模板。面向需要结构化 task、review、handoff 和 spike 记录的项目。

## 初始化新项目

1. 复制模板到新项目时**不要带上 `docs_repo/`**（仅本模板仓维护笔记，不属于业务脚手架）。推荐：
   ```bash
   rsync -a --exclude docs_repo/ --exclude .git/ /path/to/repo_template/ /path/to/new_project/
   ```
   或 `git clone` / `cp -a` 后再**立刻删掉** `docs_repo/`。**禁止** `cp -r`（会把软链展开成副本，导致 `.agents/skills/` 与 `.claude/skills/` 分叉）。
2. 初始化版本控制。
3. 全局替换 `{project_name}`，填写项目一句话介绍。
4. 保持全部软链：`CLAUDE.md -> AGENTS.md`，以及 `.claude/skills/* -> ../../.agents/skills/*`。
5. 按技术栈补充依赖文件、工具配置和 `.gitignore`。
6. 填写 `docs/blueprint/architecture.md`、`domain.md`、`conventions.md`、`testing.md` 初稿；`decisions.md` 初始可为空。
7. 确认 `docs/tasks/` 下无遗留 task 目录、`docs/specs_index.md` 无伪 active 数据；`docs/tasks/task_template/`（`spec.md` / `task.md`）仅作复制源。
8. 删除 `tests/unit/` 下模板自带的测试文件（`test_task_save.py` / `test_task_archive_dir.py` / `test_render_review_prompts.py` / `test_check_review_status.py`）与 `__pycache__/`——它们验证模板脚本自身，新项目脚本不适用；保留 `tests/unit/.gitkeep`。

README 应改成项目自身介绍，不继续保留模板说明。

## 文档入口

- Agent 工作规则与目录权责：[`AGENTS.md`](AGENTS.md)
- 内容与格式约定：[`docs/blueprint/conventions.md`](docs/blueprint/conventions.md)
- 测试方法（`{doctor_cmd}` / `{test_cmd}` / `{blackbox_verify}`）：[`docs/blueprint/testing.md`](docs/blueprint/testing.md)
- 操作 skill：`.agents/skills/`（preflight / parallel / bug / create / pending-to-task / merge / run / hygiene / clean）
