---
name: bug-to-pending
description: 复现 bug、定位根因、扫描同类位点、分析测试缺口并登记 pending；不在本 skill 修生产代码。用户报告 bug、要求排查根因或分析缺陷影响面时调用。
disable-model-invocation: true
metadata:
  opencode/autoinvoke: "false"
---

# bug-to-pending

复现 bug、定位根因、扫描同类位点、分析测试缺口并登记 pending；用户批准后再建修复 task。不在本 skill 修生产代码。

## 组合契约

仅用户显式授权入口或 registry.json 已登记父流程可执行。本流程作为子流程时，由父流程直接读取本 canonical `SKILL.md` 并执行正文，不调用宿主原生 Skill 工具；原样传递参数、exact identity、owner_id 和写域。父流程授权不扩大，缺少所需授权时停止。调用关系以 `.repo_template/skills/registry.json` 为权威。

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
7. analysis-only 到此结束。默认模式向用户汇报并询问是否立项；批准后直接读取 `task-create` 正文建覆盖完整修复面的 backlog task；spec 写来源 pNNN，随后用 `pending.py archive {pNNN} --resolution transferred --fix-ref {tid} --reason "已核实根因与范围并转交该 task" --write` 转办归档，pNNN 与 tid 双向关联。归档表示已转办，不冒充修复完成。
8. 列出 pending 改动，询问是否单独提交；task-create 的创建 commit 不混入 pending 文件。

## 边界

只写 `.scratch/`、pending 和经批准的新 task；不 start、不修生产代码。环境或配置问题无需仓库修复时不强建 task。
