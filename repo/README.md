# {项目名}

{一句话介绍：这个项目是什么、给谁用。}

从仓库模板复制而来。工具链在 `.repo_template/`，业务文件在仓根其它目录。

## 初始化

以下命令为 POSIX shell（Linux/macOS/WSL/Git Bash）。目标目录应为**不存在或空目录**，已有项目禁止直接覆盖。

1. 从已提交的模板版本一条命令初始化。仅 Git commit 中的 `repo/` 参与分发；命令会导出、`git init`、安装 hooks 并登记来源。需要 Python 3.9+ 与 Git：

    ```bash
    python3 /path/to/repo_template/repo/.repo_template/scripts/export_template.py \
      --source /path/to/repo_template --target /path/to/new_project --ref HEAD --bootstrap
    ```

    目标必须不存在或为空；已有 `core.hooksPath` 时默认不覆盖（确需覆盖时在目标仓显式运行 `repo_sync.py install-hooks --force`），按命令输出的 husky/lefthook 片段人工接入，此时 `install-hooks` 退出码为 2（0 已安装、1 错误、2 未安装需人工接入）；bootstrap 遇退出码 2 会继续完成其余步骤并输出未完成提示。失败即停止，按输出检查现场或删除目标重来。

2. 替换本文件与 `AGENTS.md` 项目区介绍。

3. 保持软链：`.claude/skills/*` 与 `.agents/skills/*` 指向 `.repo_template/skills/*`。

4. 按技术栈补充依赖、工具配置和 `.gitignore` 块外规则；模板规则在 `# repo-template:begin` / `# repo-template:end` 托管块内，同步只替换块内内容，块外规则保留。

5. 确认 md_kx 可用：`md_kx --version`。来源 [TuTouPower/md_kx](https://github.com/TuTouPower/md_kx)（PyPI 发行名 `md-kx`，命令 `md_kx`）；本机缺失才装：`uv tool install md-kx`。`.md_kx.toml` 已随模板提供。

6. 填写 `docs/blueprint/architecture.md`、`domain.md`（未填前不视为权威）。`conventions.md` / `testing.md` 是模板默认，按技术栈改命令与例外；`decisions/` 一条一文件。

7. 确认 `docs/tasks/` 与 `docs/specs/` 无伪 active 数据；task 模板在 `.repo_template/docs/task_template/`。

8. 在 `testing.md` 明确项目测试与黑盒策略，把 `verification_policy` 改为 `project_configured`，在 `verification_notes` 写验证范围与不适用项理由；然后运行 `python3 .repo_template/scripts/repo_sync.py healthcheck`（只读检查软链、hooks 接入、来源登记、md_kx、验证策略与模板占位符）、`pytest .repo_template/tests -q -m contract` 和项目 test/blackbox；healthcheck 退出非零时逐条修复。全部适用检查通过才算初始化完成。

最短工作路径：初始化通过 → `/task-create` → 无依赖单 task 直接 `/task-run tNNN` → 执行结束后批准 merge。依赖图、多会话并发、goal 见 usage，接管与恢复见 recovery，模板同步与升级见 sync；自然语言不自动授权这些入口。复制完成后把本 README 改成项目自身介绍。

## 入口

- Agent 规则：[`AGENTS.md`](AGENTS.md)
- 模板用法（消费仓 agent）：[`.repo_template/docs/usage.md`](.repo_template/docs/usage.md)
- 多会话接管与状态恢复：[`.repo_template/docs/recovery.md`](.repo_template/docs/recovery.md)
- 模板同步、升级与降级：[`.repo_template/docs/sync.md`](.repo_template/docs/sync.md)
- 项目约定：[`docs/blueprint/conventions.md`](docs/blueprint/conventions.md)
- 测试方法：[`docs/blueprint/testing.md`](docs/blueprint/testing.md)

```bash
python3 .repo_template/scripts/task.py --help
python3 .repo_template/scripts/pending.py --help
python3 .repo_template/scripts/findings.py --help
python3 .repo_template/scripts/spikes.py --help
```
