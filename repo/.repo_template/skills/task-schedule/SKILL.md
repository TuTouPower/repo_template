---
name: task-schedule
description: 为 backlog task 写入真实的硬依赖 depends_on 与并发冲突 conflicts_with，执行计划由 task.py plan 只读计算。用户要求排依赖、标冲突或排出并发执行计划时调用。
disable-model-invocation: true
metadata:
  opencode/autoinvoke: "false"
---

# task-schedule

为 backlog task 写入真实的硬依赖和并发冲突；执行计划由 `task.py plan` 只读计算。

## 组合契约

仅用户显式授权入口或 registry.json 已登记父流程可执行。本流程作为子流程时，由父流程直接读取本 canonical `SKILL.md` 并执行正文，不调用宿主原生 Skill 工具；原样传递参数、exact identity、owner_id 和写域。父流程授权不扩大，缺少所需授权时停止。调用关系以 `.repo_template/skills/registry.json` 为权威。

## 流程

1. 确定候选 task；无参数取有效 backlog，指定 tid 只处理这些 backlog。
2. 读取 spec、相关代码和当前 effective 状态。需要时查看 active worktree 的实际 diff，以免与正在进行的工作冲突。
3. 写图：
    - `depends_on` 只表达必须先获得的行为、接口或产物；
    - `conflicts_with` 只表达不宜并发的重叠改动面；
    - 不确定或 spec 太粗时报告待澄清，不猜边。
4. 只用 `task.py edit` 修改调度字段，不手改 front matter 或 index。
5. 运行 `task.py view` 和 `task.py plan`。依赖环、缺失引用等错误必须修正；冲突只影响并发建议，不影响明确的串行队列。
6. 汇报写图结果和本波计划，询问是否提交调度改动；同意后提交 task 文档和派生 index。

## 边界

- 不改 spec、实现、测试或 blueprint。
- 不创建 worktree、不执行 task、不 merge。
- 脚本负责图校验和排序算法，skill 不复制算法内部细节。
