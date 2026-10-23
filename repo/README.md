# {项目名}

{一句话介绍：这个项目是什么、给谁用。}

从仓库模板复制而来。工具链在 `.repo_template/`，业务文件在仓根其它目录。

## 初始化

以下命令为 POSIX shell（Linux/macOS/WSL/Git Bash）。目标目录应为**不存在或空目录**，已有项目禁止直接覆盖。

01. 从已提交的模板版本导出干净产物。仅 Git commit 中的 `repo/` 参与分发，忽略文件、未提交改动、本机状态不会复制；软链保留并检查目标。需要 Python 3.9+ 与 Git：

    ```bash
    python3 /path/to/repo_template/repo/.repo_template/scripts/export_template.py \
      --source /path/to/repo_template --target /path/to/new_project --ref HEAD
    ```

    目标必须不存在或为空；导出失败即停止。调试未提交模板时先在隔离工厂副本提交，再导出该 commit，禁止把 dirty 工作区作为普通分发源。

02. 在新项目目录初始化版本控制；后续命令均在该目录执行。

03. 替换本文件与 `AGENTS.md` 首行项目介绍。

04. 保持软链：`.claude/skills/*` 与 `.agents/skills/*` 指向 `.repo_template/skills/*`。

05. 按技术栈补充依赖、工具配置和 `.gitignore`。

06. 确认 md_kx 可用：`md_kx --version`。它是本模板约定的 Markdown 格式化器，来源 [TuTouPower/md_kx](https://github.com/TuTouPower/md_kx)（PyPI 发行名 `md-kx`，命令 `md_kx`），通常已在开发机全局安装；本机缺失才装：`uv tool install md-kx`。`.md_kx.toml` 已随模板提供。

07. 启用 commit 前格式化 hook：`python3 .repo_template/scripts/repo_sync.py install-hooks`（`core.hooksPath` 指向 `.repo_template/hooks`；已有其它 hooksPath 须 `--force`）。

08. 填写 `docs/blueprint/architecture.md`、`domain.md`（占位，未填前不视为权威）。`conventions.md` / `testing.md` 是模板默认，按技术栈改命令与例外；`decisions.md` 初始可空。

09. 确认 `docs/tasks/` 无遗留 task 目录、`docs/specs_index.md` 无伪 active 数据。task 模板在 `.repo_template/docs/task_template/`。

10. 登记模板源：复制不带 `.repo_template/sync_state.json`（按消费仓区分 `template_source`，无法预置），无此文件时 `status` / `plan` / `apply` 均报未初始化。在新项目目录执行 `python3 .repo_template/scripts/repo_sync.py init --source <模板来源路径或 URL>`（本机路径接受产物根或含 `repo/` 的工厂根；URL 为 git remote），成功后用 `python3 .repo_template/scripts/repo_sync.py status` 核对 `template_source`。已有 state 不重跑；后续同步走 `/repo-template-sync`。

11. 在 `testing.md` 明确项目测试与黑盒策略，把 `verification_policy` 改为 `project_configured`，在 `verification_notes` 写验证范围与不适用项理由；然后运行 doctor、`pytest .repo_template/tests -q -m contract` 和项目 test/blackbox。检查软链均有效，全部适用检查通过才算初始化完成。

最短工作路径：初始化通过 → `/task-create` → 无依赖单 task 直接 `/task-run tNNN` → 执行结束后批准 merge。依赖图、多会话、goal、恢复与升级见 usage；自然语言不自动授权这些入口。复制完成后把本 README 改成项目自身介绍。

## 入口

- Agent 规则：[`AGENTS.md`](AGENTS.md)
- 模板用法（消费仓 agent）：[`.repo_template/docs/usage.md`](.repo_template/docs/usage.md)
- 项目约定：[`docs/blueprint/conventions.md`](docs/blueprint/conventions.md)
- 测试方法：[`docs/blueprint/testing.md`](docs/blueprint/testing.md)

```bash
python3 .repo_template/scripts/task.py --help
python3 .repo_template/scripts/pending.py --help
python3 .repo_template/scripts/findings.py --help
python3 .repo_template/scripts/spikes.py --help
```
