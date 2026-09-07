---
name: task-preflight
description: none
disable-model-invocation: true
---

# task-preflight

只读检查待做 task 的机器门禁和用户不可替代缺口。

## 流程

1. 用 `task.py effective-status` 确定每个 tid 的有效状态、来源和读取位置；不得把主干滞后的 backlog 当成未启动 task。
2. 在有效来源运行对应 preflight：backlog 用 `--allow-backlog`，active 在登记 worktree 中运行，分支快照用 `--ref`。
3. 从 `docs/blueprint/testing.md` 的 `doctor_cmd` 章节读取无副作用命令并运行一次；正文为「无」或章节未定义则明确说明。
4. 读取 spec、task 和 `.env.example`，只列 Agent 无法替代的缺口：密钥、账号权限、外部服务、产品决策、外部数据或平台限制。检查环境变量只看是否存在，不读取值。
5. 输出 tid、有效状态、来源、preflight 结果、缺口、是否阻塞及用户动作。无阻塞时提示可运行 `task-run`。

## 边界

- 全程只读，不改 task、代码、测试或环境。
- `UNVERIFIED-SPIKE` 是执行期实验，不自动算用户缺口；`UNVERIFIED-BLOCKING` 和裸 `UNVERIFIED` 阻止 start。
