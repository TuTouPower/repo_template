# {项目名}

{一句话介绍：这个项目是什么、给谁用。}

从仓库模板复制而来。工具链在 `.repo_template/`，业务文件在仓根其它目录。

## 初始化

以下命令为 POSIX shell（Linux/macOS/WSL/Git Bash）。目标目录应为**不存在或空目录**，已有项目禁止直接覆盖。

01. 安装 `rsync` 后从模板工厂复制产物。`--ignore-existing` 保证已有文件不被覆盖；统一用 `rsync -a` 保留软链，不用 `cp -r` 代替。复制前先确认目标为空：

    ```bash
    target=/path/to/new_project
    [ -e "$target" ] && [ -n "$(ls -A "$target")" ] && echo "目标非空，停止" && exit 1
    rsync -a --ignore-existing \
      --exclude .git/ --exclude .scratch/ --exclude .pytest_cache/ --exclude __pycache__/ \
      /path/to/repo_template/repo/ "$target/"
    ```

    任何步骤非零退出即停止，不重试覆盖。Windows PowerShell 无本步骤的直接等价物，用 WSL/Git Bash 执行；NTFS 上软链复制需开发者模式或管理员权限。

    成功后在新项目目录执行 `test -L CLAUDE.md && test "$(readlink CLAUDE.md)" = AGENTS.md`，并核对第 4 步的 skill 软链；验证失败先修复复制结果。

02. 在新项目目录初始化版本控制；后续命令均在该目录执行。

03. 替换本文件与 `AGENTS.md` 首行项目介绍。

04. 保持软链：`CLAUDE.md -> AGENTS.md`；`.claude/skills/*` 与 `.agents/skills/*` 指向 `.repo_template/skills/*`。

05. 按技术栈补充依赖、工具配置和 `.gitignore`。

06. 确认 md_kx 可用：`md_kx --version`。它是本模板约定的 Markdown 格式化器，来源 [TuTouPower/md_kx](https://github.com/TuTouPower/md_kx)（PyPI 发行名 `md-kx`，命令 `md_kx`），通常已在开发机全局安装；本机缺失才装：`uv tool install md-kx`。`.md_kx.toml` 已随模板提供。

07. 启用 commit 前格式化 hook：`python3 .repo_template/scripts/repo_sync.py install-hooks`（`core.hooksPath` 指向 `.repo_template/hooks`；已有其它 hooksPath 须 `--force`）。

08. 填写 `docs/blueprint/architecture.md`、`domain.md`（占位，未填前不视为权威）。`conventions.md` / `testing.md` 是模板默认，按技术栈改命令与例外；`decisions.md` 初始可空。

09. 确认 `docs/tasks/` 无遗留 task 目录、`docs/specs_index.md` 无伪 active 数据。task 模板在 `.repo_template/docs/task_template/`。

10. 登记模板源：复制不带 `.repo_template/sync_state.json`（按消费仓区分 `template_source`，无法预置），无此文件时 `status` / `plan` / `prep` / `apply` 均报未初始化。在新项目目录执行 `python3 .repo_template/scripts/repo_sync.py init --source <模板来源路径或 URL>`（本机路径接受产物根或含 `repo/` 的工厂根；URL 为 git remote），成功后用 `python3 .repo_template/scripts/repo_sync.py status` 核对 `template_source`。已有 state 不重跑；后续同步走 `/repo-template-sync`。

复制完成后把本 README 改成项目自身介绍。

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
