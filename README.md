# repo_template

Agent 友好的通用仓库模板。面向需要结构化 task、review、handoff 和 spike 记录的项目。

## 初始化新项目

01. 复制模板到新项目时**不要带上 `docs_repo/`**（仅本模板仓维护笔记，不属于业务脚手架）。推荐：
    ```bash
    rsync -a --exclude docs_repo/ --exclude .git/ --exclude .scratch/ --exclude .pytest_cache/ --exclude __pycache__/ --exclude 'docs/reviews/review_*/' /path/to/repo_template/ /path/to/new_project/
    ```
    或 `git clone` / `cp -a` 后再**立刻删掉** `docs_repo/`。**禁止** `cp -r`（会把软链展开成副本，导致 `.agents/skills/` 与 `.claude/skills/` 分叉）。
02. 初始化版本控制。
03. 全局替换 `AGENTS.md` 首行 `{一句话介绍：这个项目是什么、给谁用。}` 占位符，填写项目一句话介绍。
04. 保持全部软链：`CLAUDE.md -> AGENTS.md`，以及 `.claude/skills/* -> ../../.agents/skills/*`。
05. 按技术栈补充依赖文件、工具配置和 `.gitignore`。
06. 安装 md_kx（Markdown 格式化，见 `scripts/repo_template/md_format.py`）：`uv tool install md_kx`，确认 `md_kx --table-mode` 支持 `compact`/`spaced`/`pad`（旧版不支持则先升级 md_kx 仓 t008/p008）。`.md_kx.toml` 已随模板硬同步。
07. 启用 commit 前 md_kx 格式化 hook：`python3 scripts/repo_template/repo_sync.py install-hooks`（幂等设置 `core.hooksPath` 指向 `scripts/repo_template/hooks/`；已有其它 hooksPath 须 `--force`；hook 脚本缺失或不可执行则失败；未初始化 sync 时直接 `git config core.hooksPath scripts/repo_template/hooks`）。此后 commit 自动格式化本次 staged 且工作区与 index 一致的 `.md` 并重新暂存；不一致则拒绝。
08. 填写 `docs/blueprint/architecture.md`、`domain.md`、`conventions.md`、`testing.md` 初稿；`decisions.md` 初始可为空。
09. 确认 `docs/tasks/` 下无遗留 task 目录、`docs/specs_index.md` 无伪 active 数据；`docs/tasks/task_template/`（`spec.md` / `task.md`）仅作复制源。
10. 保留 `tests/repo_template/` 下模板自带的测试文件——它们持续验证 task 工具链（`scripts/repo_template/task.py`、完整的 `scripts/repo_template/repo_task/` 实现包、`scripts/repo_template/pending.py`、`scripts/repo_template/spikes.py` 等）的正确性。`task.py` 是 façade，不能脱离 `repo_task/` 单独复制；上述 `rsync` 会保留整套工具链。

README 应改成项目自身介绍，不继续保留模板说明。

## 文档入口

- Agent 工作规则与目录权责：[`AGENTS.md`](AGENTS.md)
- 内容与格式约定：[`docs/blueprint/conventions.md`](docs/blueprint/conventions.md)
- 测试方法（`{doctor_cmd}` / `{test_cmd}` / `{blackbox_verify}`）：[`docs/blueprint/testing.md`](docs/blueprint/testing.md)
- 操作 skill：`/task-preflight`、`/task-schedule`、`/task-bug`、`/pending-record`、`/task-create`、`/task-from-pending`、`/task-merge`、`/task-run`、`/task-work`、`/task-integrate`、`/repo-hygiene`、`/repo-cleanup`
