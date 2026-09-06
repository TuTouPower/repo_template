---
name: task-bug
description: none
---

# task-bug

复现 bug、定位根因、扫描同类位点、分析测试缺口并登记 pending；用户批准后再建修复 task。不在本 skill 修生产代码。

## 模式

- 默认：分析登记后询问是否立项，批准则调用 `task-create`，最后询问 pending commit。
- `analysis-only`：只分析和登记，返回 `pNNN` 后结束；不立项、不 commit。供 `task-work`、`pending-record` 等内部调用。

## 流程

1. 确认可观察的期望、实际、触发线索和影响；信息不足时只问会改变结论的缺口。
2. 在 `.scratch/` 最小复现。复现失败则报告尝试和卡点，不硬写根因。
3. 根因必须落到可验证机制，并区分产品缺陷、环境、配置或测试假绿。
4. 按同一 helper/API 误用、校验缺失、默认值、复制块或对称路径扫描同类位点。确认成立的合并进同一修复范围；吃不准的单列，不算已确认。
5. 说明现有测试为何未发现，以及应在哪个层级、场景和断言补测，覆盖主点和已确认同类位点。
6. 用 `pending.py new --kind bug` 新建或就地更新等价条目，写现象、影响、根因、同类清单、测试缺口和 `.scratch/` 线索。
7. analysis-only 到此结束。默认模式向用户汇报并询问是否立项；批准后用 `task-create` 建覆盖完整修复面的 backlog task。
8. 列出 pending 改动，询问是否单独提交；task-create 的创建 commit 不混入 pending 文件。

## 边界

只写 `.scratch/`、pending 和经批准的新 task；不 start、不修生产代码。环境或配置问题无需仓库修复时不强建 task。
