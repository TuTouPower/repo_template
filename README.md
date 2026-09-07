# repo_template

Agent 友好的通用仓库模板工厂。产物在 [`repo/`](repo/)，本仓设计笔记在 [`docs_repo/`](docs_repo/)。

消费仓不要打开本仓库根当项目用。复制 `repo/` 作为新项目起点。

## 复制新项目

完整复制规程（防覆盖守卫、软链验证、平台说明）唯一来源：[`repo/README.md`](repo/README.md)「初始化」。要点：目标须为不存在或空目录，用 `rsync -a --ignore-existing` 保留软链，不用 `cp -r`；失败即停。

## 开发本仓

- 工厂规则：[`AGENTS.md`](AGENTS.md)
- 消费仓骨架 / 状态机（只导航，不把该文件加载为工厂指令）：[`repo/AGENTS.md`](repo/AGENTS.md)
- 消费仓用法 / 工具链写权 / skill 调用：[`repo/.repo_template/docs/usage.md`](repo/.repo_template/docs/usage.md)
- 工具链：`repo/.repo_template/`
- 测试：`pytest repo/.repo_template/tests -q`

未点名不要对工厂仓走消费侧 `/task-create`。
