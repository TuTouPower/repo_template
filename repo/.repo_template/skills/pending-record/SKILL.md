---
name: pending-record
description: 持续澄清并登记 pending 条目（需求/技术债/bug）。bug 派子代理按 bug-to-pending 研究记录，非 bug 普通记录；主会话不停聊。
disable-model-invocation: true
---

# pending-record

持续接收用户口述并登记 pending。输入清楚时一句复述后直接处理；只有歧义、park 或覆盖已有条目时才确认。主会话继续接下一条。

## 路由

- **bug 事项**：派后台子代理按 `bug-to-pending` 流程（`analysis-only` 模式）研究并记录（复现、根因、同类扫描、测试缺口，用 `pending.py new --kind bug` 登记/更新）；主会话不等待耗时分析，继续接下一条。
- **非 bug 事项（普通待办/需求/技术债）**：主会话查重后直接普通记录 pending，调用 `pending.py new` 登记来源、内容与处理状态；用户明确暂搁时 park。
- **已有更新**：用户同意后只更新指定 `pNNN`，保留编号；需要时用脚本 rename/park，不新建重复条目。

## 会话管理

主会话维护：

- in-flight：后台 bug 子代理的 `worker_id / topic / target pNNN（若有）`；
- 本次已变更 pending 路径；
- 失败项。

派发或记录前查已落盘 pending 和 in-flight 主题，避免语义重复。bug worker 完成后简短报告 `pNNN`、动作或失败原因。宿主不支持后台子代理时，主会话可顺序执行同一流程。

## Worker 边界

bug worker 只写 `.scratch/` 和 pending 条目；创建必须经 `pending.py new --kind bug`，更新保留原编号。禁止 commit、task 立项、start 和生产修复。不进入 bug-to-pending 的立项/提交阶段。

## 收尾

用户结束或要求提交时，先等待所有 in-flight 终态，或由用户明确排除仍在运行的项。随后列出本次 create/update/park 文件并询问是否提交；reuse 未改和失败项不提交。

## 边界

本 skill 只登记，不建 task、不改生产代码。已验证技术事实进 findings；park 必须来自用户明确“不办/暂搁”。
