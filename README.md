# repo_template

Agent 友好的通用仓库模板工厂。产物在 [`repo/`](repo/)，本仓设计笔记在 [`docs_repo/`](docs_repo/)。

消费仓不要打开本仓库根当项目用。复制 `repo/` 作为新项目起点。

## 复制新项目

```bash
rsync -a --exclude .git/ --exclude .scratch/ --exclude .pytest_cache/ --exclude __pycache__/ \
  /path/to/repo_template/repo/ /path/to/new_project/
```

禁止 `cp -r`（会把软链展开成副本）。不必再排除 `docs_repo/`——它不在产物里。

详见 [`repo/README.md`](repo/README.md)。

## 开发本仓

- 工厂规则：[`AGENTS.md`](AGENTS.md)
- 消费仓骨架 / 状态机：[`repo/AGENTS.md`](repo/AGENTS.md)
- 消费仓用法 / 工具链写权 / skill 调用：[`repo/.repo_template/docs/usage.md`](repo/.repo_template/docs/usage.md)
- 工具链：`repo/.repo_template/`
- 测试：`pytest repo/.repo_template/tests -q`

未点名不要对工厂仓走消费侧 `/task-create`。
