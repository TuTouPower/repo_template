这是模板工厂仓。产物是 `repo/`。本仓笔记在 `docs_repo/`。

默认工作：改 `repo/.repo_template/`、跑工厂全量测试、改同步脚本。

- 未点名禁止走 `/task-create`、`/task-run`、`/task-schedule` 等消费仓流程。
- 要验消费体验：把工作区切到 `repo/`，或把 `repo/` 复制成独立消费仓。
- 消费仓项目骨架与状态机只在 `repo/AGENTS.md`。不要把那份链到工厂根。
- 消费仓用法、工具链写权与 skill 调用只在 `repo/.repo_template/docs/usage.md`。
- 工厂根不放业务骨架。`docs_repo/` 不进产物。
- 契约测试在 `repo/.repo_template/tests/`，随产物分发；其余集成测试在工厂根 `factory_tests/`，不进产物。
- 全量测试 `--basetemp` 用系统临时目录（`"$(mktemp -d)"`），不用仓内 `.scratch/`。

命令：

```bash
pytest repo/.repo_template/tests factory_tests -q -n auto -p no:cacheprovider --basetemp "$(mktemp -d)"
python3 repo/.repo_template/scripts/task.py --help
```

工厂改模板必须跑全量（契约测试 + `factory_tests/` 集成测试），需要 pytest 与 pytest-xdist；`-n auto` 按 CPU 并行，内存紧张时可用 `-n 2` 限制 worker，排查时可用 `-n 0` 串行。消费仓只含契约测试，默认门禁是 `pytest .repo_template/tests -q -m contract`，见 `repo/docs/blueprint/testing.md`。
