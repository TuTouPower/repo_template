# repo_template

Agent 友好的通用仓库模板工厂。产物在 [`repo/`](repo/)，本仓设计笔记在 [`factory_docs/`](factory_docs/)。

消费仓不要打开本仓库根当项目用。复制 `repo/` 作为新项目起点。

## 复制新项目

完整复制规程（防覆盖守卫、软链验证、平台说明）唯一来源：[`repo/README.md`](repo/README.md)「初始化」。要点：用 `repo/.repo_template/scripts/export_template.py` 从指定 Git commit 导出，目标须不存在或为空，失败即停；细节见该节。

## 开发本仓

- 工厂规则：[`AGENTS.md`](AGENTS.md)
- 消费仓骨架 / 状态机（只导航，不把该文件加载为工厂指令）：[`repo/AGENTS.md`](repo/AGENTS.md)
- 消费仓用法 / 工具链写权 / skill 调用：[`repo/.repo_template/docs/usage.md`](repo/.repo_template/docs/usage.md)
- 工具链：`repo/.repo_template/`
- 测试：工厂全量命令与范围见 [`AGENTS.md`](AGENTS.md)；消费仓默认门禁见 [`repo/docs/blueprint/testing.md`](repo/docs/blueprint/testing.md)

未点名不要对工厂仓走消费侧 `/task-create`。
