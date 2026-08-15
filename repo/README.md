# {项目名}

{一句话介绍：这个项目是什么、给谁用。}

从仓库模板复制而来。工具链在 `.repo_template/`，业务文件在仓根其它目录。

## 初始化

1. 从模板工厂复制产物（不要用 `cp -r`，会展开软链）：

    ```bash
    rsync -a --exclude .git/ --exclude .scratch/ --exclude .pytest_cache/ --exclude __pycache__/ \
      /path/to/repo_template/repo/ /path/to/new_project/
    ```

2. 初始化版本控制。

3. 替换本文件与 `AGENTS.md` 首行项目介绍。

4. 保持软链：`CLAUDE.md -> AGENTS.md`；`.claude/skills/*` 与 `.agents/skills/*` 指向 `.repo_template/skills/*`；`.claude/hooks/merge_guard.py` 指向 `.repo_template/hooks/merge_guard.py`。

5. 按技术栈补充依赖、工具配置和 `.gitignore`。

6. 安装 md_kx：`uv tool install md_kx`。`.md_kx.toml` 已随模板提供。

7. 启用 commit 前格式化 hook：`python3 .repo_template/scripts/repo_sync.py install-hooks`（`core.hooksPath` 指向 `.repo_template/hooks`；已有其它 hooksPath 须 `--force`）。

8. 填写 `docs/blueprint/architecture.md`、`domain.md`、`conventions.md`、`testing.md`；`decisions.md` 初始可空。

9. 确认 `docs/tasks/` 无遗留 task 目录、`docs/specs_index.md` 无伪 active 数据。task 模板在 `.repo_template/docs/task_template/`。

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
