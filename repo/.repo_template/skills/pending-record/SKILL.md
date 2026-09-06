---
name: pending-record
description: 持续澄清并登记 pending 条目（需求/技术债/bug）。用户连续口述待办时调用；澄清后派子代理写 pending，主会话不停聊。
disable-model-invocation: true
---

# pending-record

持续接收用户口述并登记 pending。输入清楚时一句复述后直接派后台子代理；只有歧义、park 或覆盖已有条目时才确认。主会话继续接下一条，不等待耗时分析。

## 路由

- **4a 普通新建**：查重后调用 `pending.py new`，填写来源、内容和处理状态；用户明确暂搁时 park。
- **4b bug**：调用 `task-bug analysis-only`，完成复现、根因、同类扫描、测试缺口和 bug 条目新建/更新。
- **4c 已有更新**：用户同意后只更新指定 `pNNN`，保留编号；需要时用脚本 rename/park，不新建重复条目。

## 会话管理

主会话只需维护：

- in-flight：`worker_id / topic / target pNNN（若有）`；
- 本次已变更 pending 路径；
- 失败项。

派发前查已落盘 pending 和 in-flight 主题，避免语义重复。worker 完成后简短报告 `pNNN`、动作或失败原因。宿主不支持后台子代理时，主会话可顺序执行同一流程。

## Worker 边界

worker 只写 `.scratch/` 和 pending 条目；创建必须经 `pending.py new`，更新保留原编号。禁止 commit、task 立项、start 和生产修复。4b 不进入 task-bug 的立项/提交阶段。

## 收尾

用户结束或要求提交时，先等待所有 in-flight 终态，或由用户明确排除仍在运行的项。随后列出本次 create/update/park 文件并询问是否提交；reuse 未改和失败项不提交。

## 边界

本 skill 只登记，不建 task、不改生产代码。已验证技术事实进 findings；park 必须来自用户明确“不办/暂搁”。
