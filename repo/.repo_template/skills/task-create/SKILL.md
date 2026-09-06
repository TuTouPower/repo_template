---
name: task-create
description: 把用户需求拆成合格 backlog task。用户批准立项或要求拆 task 时调用。
---

# task-create

把已确认需求拆成独立可验收的 backlog task。严格模板见 `.repo_template/docs/task_template/`，目录与写权见 `AGENTS.md` 和 `.repo_template/docs/usage.md`。

## 流程

1. 按 effective status 查重；已有等价 task 时复用，不重复创建。
2. 只澄清代码库无法回答且会改变范围、AC 或产品行为的问题。没有可靠推荐时不要伪造推荐。
3. 拆分 task：每个 task 有单一交付结果、可证伪 AC 和最小依赖，并适合一个工程意义明确的执行 commit。
4. 用 `task.py add` 创建目录，严格填写 `spec.md` 和 `task.md`：只替换占位符，不删除模板规范块，不手改 front matter。
5. 按风险设置 `review_level`：安全、鉴权、资金、并发、迁移、协议兼容用 `full`；其它通常用 `single`。
6. 对每个 task 运行 `task.py preflight {tid} --creation`。结构、AC、占位符或裸 `UNVERIFIED` 为 FAIL；已分类的 `UNVERIFIED-BLOCKING` 可作为 backlog WARN，但仍不能 start。
7. 列出新增 task、依赖建议和创建有效性结果，询问是否提交；同意后本批 task 和派生 index 用一个创建 commit 提交。

## 边界

- 不实现、不 start、不 finish。
- 只写本批 task 目录和脚本派生 index。
- bug 分析走 `task-bug`；pending 转 task 走 `task-from-pending`。
