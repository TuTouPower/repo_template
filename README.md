# repo_template

Agent 友好的通用仓库模板。面向需要结构化 task、review、handoff 和 spike 记录的项目。

## 设计原则

1. **行为入口唯一**：`AGENTS.md` 定义 agent 必须遵守的工作流和权责。
2. **按需导航**：`AGENTS.md` 说明什么场景读取什么文档，避免全量加载。
3. **当前与历史分离**：active 工作放 `tasks/`、`reviews/`、`spikes/`，完结或终止后移入 `archive/`。
4. **模板不冒充工作项**：task、review、spike 模板集中在 `docs/templates/`，不占用真实 `tid` / `sid`。
5. **task 即 commit**：一个 task 对应一个 commit，含 spec/plan/红绿/黑盒/双轴 review/`task.md` 过程总账闭环。review 与处置在 **commit 前** 完成；review 证据源为以 `diff_anchor` 为基线的 `git diff`（`diff_anchor` 取自 `task.md` front matter）。
6. **specs driven + TDD**：拆分 task 时即 **填写** spec 和 plan（验收标准非空）；开发循环内先写红再变绿；每个 task **收尾时**累积写入 `docs/specs/`（须已过黑盒）。

## 初始化新项目

1. 复制模板到新项目目录。
2. 初始化版本控制。
3. 全局替换 `{project_name}`，填写项目一句话介绍和 `AGENTS.md` 硬约束。
4. 保持 `CLAUDE.md -> AGENTS.md` 软链接。
5. 按技术栈补充依赖文件、工具配置和 `.gitignore`。
6. 填写 `docs/blueprint/architecture.md`、`domain.md`、`conventions.md` 初稿；`decisions.md` 初始可为空。
7. 确认 `docs/tasks_index.json`、`docs/specs_index.md` 无伪 active 数据。

README 应改成项目自身介绍，不继续保留模板说明。

## 目录概览

```text
{project_name}/
├── AGENTS.md                  # agent 行为入口与按需导航
├── CLAUDE.md -> AGENTS.md     # Claude Code 兼容软链接
├── README.md                  # 项目介绍
├── docs/
│   ├── blueprint/             # 当前架构、领域、约定、决策
│   ├── guides/                # 给人看的使用指南
│   ├── templates/             # task / task review / spike 模板
│   ├── tasks/                 # task 工作区（backlog 起含开发中 spec）
│   ├── tasks_index.json       # task 索引（活跃；通过 scripts/task.py 操作）
│   ├── specs/                 # 需求 spec（每个 task 收尾时累积，须已过黑盒）
│   ├── specs_index.md         # 需求索引（随 task 收尾更新）
│   ├── reviews/               # 当前独立 review（review_<TS> 目录）
│   ├── spikes/                # 当前 spike
│   ├── bugs.md                # 已知未修复 bug 追加式记录
│   ├── handoff.md             # 项目级交接
│   └── archive/               # 完结或终止的历史记录
├── schemas/                   # 跨服务接口契约
├── config/                    # 配置（默认 + 环境覆盖 + .env.example）
├── src/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── scripts/
├── assets/
├── artifacts/                 # 不入库
├── data/                      # 不入库
└── .scratch/                  # 不入库的一次性草稿
```

## 文档入口

- Agent 工作规则：[`AGENTS.md`](AGENTS.md)
- 内容与格式约定：[`docs/blueprint/conventions.md`](docs/blueprint/conventions.md)
