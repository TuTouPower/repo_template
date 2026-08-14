这是模板工厂仓。产物是 `repo/`。本仓笔记在 `docs_repo/`。

默认工作：改 `repo/.repo_template/`、跑 `repo/.repo_template/tests`、改同步脚本。

- 未点名禁止走 `/task-create`、`/task-run`、`/task-schedule` 等消费仓流程。
- 要验消费体验：把工作区切到 `repo/`，或把 `repo/` 复制成独立消费仓。
- 消费仓完整状态机与写权表只在 `repo/AGENTS.md`。不要把那份链到工厂根。
- 工厂根不放业务骨架。`docs_repo/` 不进产物。

命令：

```bash
pytest repo/.repo_template/tests -q
python3 repo/.repo_template/scripts/task.py --help
```
