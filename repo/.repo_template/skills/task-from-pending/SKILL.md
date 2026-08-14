---
name: task-from-pending
description: none
disable-model-invocation: true
---

# task-from-pending

把 `docs/pending/todo/` 里该做未做的待办转成 backlog task；其中未修 bug 条目按第 2 步分诊，由用户选择处理方式。

**task 流程很重**——每个 task 有 spec、实施、黑盒、审阅、收尾、commit 整套门禁。pending 条目只是记录，不等于值得立 task。建 task 前先核实再决定。

## 步骤

1. **读总账**（只读）：

    - `docs/pending/todo/` 中 `- 处理：未开` 的待办条目——**主要来源**（bug 条目走第 2 步分诊；`docs/pending/parked/` 用户已确认暂搁，不自动捞，见边界）。用 `.repo_template/scripts/pending.py list` 列举。
    - 补扫尚未登记进总账的项（登记本应由 `task-work` 收尾完成，此处扫漏）：
        - 先用 `.repo_template/scripts/task.py effective-status` 确定每 tid 的有效来源（`worktree` / `branch` / `main`）与读取位置；再在各 `read_at` 处读 `task.md` 的 `## Review 处置`，找 `status=遗留` 但 `fix_ref` 为空的行
        - `review_code.md` / `review_test.md` / `review_general.md` 中仍开放的 important/critical
        - `docs/handoff.md` 末段待办
        - 用户点名的目录或 diff
    - 扫到未登记项：用 `.repo_template/scripts/pending.py new --slug <主题>` 建条目并填写（bug 加 `--kind bug`；`- 来源` 写 finding_id 或原 tid），再走下面流程。总账是唯一入口，不绕过。

2. **bug 分诊**（扫到未修 bug 条目时必做；无 bug 条目则跳过）。候选：`todo/` 中 `--kind bug` 且 `- 处理：未开` 的条目，含第 1 步补扫新登记的 bug。逐条列出（现象一句话、是否已有根因分析、是否已有同类位点结论），让用户对每条选择：

    - **A. 未分析，派子代理分析**：后台子代理按 `task-bug` 第 1–6 步执行（复现仅 `.scratch/` → 根因 → 同类位点扫描与合并 → 测试缺口分析 → 登记/更新条目）。主会话不等结果，继续走下面流程处理普通条目。
    - **B. 已分析，派子代理核实**：条目已含根因与补测结论时，后台子代理核实：分析与现状是否一致（只读为主，必要时 `.scratch/` 复验）；**并按 `task-bug` 第 4 步补做或核验同类位点扫描**——缺清单则补扫写入；有清单则逐处核是否仍成立；结论合并进 `- 根因` / `- 影响` / `- 测试缺口` 后就地更新条目。主会话同样不等，继续。
    - **C. 不动**：条目保持 `- 处理：未开` 留在 `todo/`，本次不处理。
        子代理边界：只写 `.scratch/` 与 pending 条目文件；禁止 commit、禁止 `task.py add`、禁止 start。选择 A/B 即授权立项：子代理结论回来后由主会话检查结论可用性（根因落到可验证机制、**已确认同类位点清单或显式「已扫无同类」**、补测方向覆盖各点、报告自洽；不重复复现/核实），再按第 4 步查重——已有等价 backlog/active task 则把 tid 写进条目 `- 处理：`；否则按第 6 步建修复 task（spec 上下文区写来源 `pNNN` + 根因 + **已确认同类位点** + 补测方向；范围/AC 须覆盖合并修复面），随第 7 步回写总账。B 核实不成立 → 报用户，按 A 重派或转 C。

3. **核实条目**（必做，不是可选）。pending 里的描述可能过时、片面或记错——登记时的情况跟现在不一样是常态。对每条候选：

    - 条目之间互相冲突、或核实中遇到不明白 / 有歧义的地方 → 停止自行裁定，先向用户问清再继续；不默认按某条理解处理、不自己选边
    - 读当前代码确认「这个问题现在还在吗」；不在 → 标闭环（`- 处理：已验证不存在`），不走 task
    - 读当前 spec / 测试确认「描述跟现状一致吗」；不一致 → 按现状重写或更新条目，再决定建不建 task
    - 评估影响范围：是真的重要（会影响正确性 / 安全 / 数据），还是只是「当时应该做但没关系」？后者留给用户拍板，不自行建 task
    - 能通过小修直接解决的（非 bug 级小调整），报给用户判断要不要直接修——不是每条都值得走 task 流程

4. **合并与去重**：

    - 用 `.repo_template/scripts/task.py effective-status` 判断有效状态；`source != main`（被 worktree / 未合并分支覆盖）的旧 backlog 不当新候选
    - 已有等价 backlog/active task → 不新建，把该 tid 写进条目 `- 处理：`，汇报已有 tid
    - 同主题的多个条目 → **合并成一个 task**；跨条目的同一系统性缺口 → 一个 follow-up task
    - minor 品味项默认不建，除非用户要求或累积为明确债务包

5. **确认范围**。候选多、含争议、或评估后判断条目不重要时，向用户呈核实结论（现在还在不在、影响、建议）并确认。用户已说「全部捞」= 普通条目里仍有效、非重复的都进入第 4 步聚合，按合并结果建 task；不是一条 pending 一个 task。不替代第 2 步 bug 分诊：未修 bug 仍须逐条选 A/B/C；C 留下不动，A/B 结论可用后才与普通条目一起聚合。

6. **每个确认项落盘**（按 `task-create` 流程，链式调用）。必须先用 `.repo_template/scripts/task.py add` 复制完整模板，禁止手写简化版 `spec.md` / `task.md` 骨架；填写后通过 `task-create` 规定的 backlog preflight。spec 上下文区写清来源 `pNNN` / finding_id / 原 tid + 核实结论（什么时候核实、核实结果）。**bug 修复 task** 的 spec 字段要求（共享根因、已确认同类位点清单或「已扫无同类」、覆盖各点的补测方向、范围/AC 覆盖合并修复面）与 `task-bug` 第 8 步一致，按该处填写。

7. **回写总账**。每个已建 task 的条目用 `.repo_template/scripts/pending.py archive {pNNN} --fix-ref {tid} --write` 迁入 `docs/archive/pending/`。条目留在 `todo/` 等于没转。

8. **询问提交总账回写**。`task-create` 已批量提交 task 目录与派生 index；这里列出本次迁移的条目文件，以及第 2 步子代理新增/更新的 bug 条目文件，询问用户是否提交。

## 边界

- 新建时不写生产代码；编码与补测方向写进 spec 上下文区，等 `task-run` 调度执行。
- 不把「已 done 且仅文档考古」无差别全建成 task。
- 条目核实后判断不存在或已过时，不建 task；直接闭环归档即可。
- 未修 bug 按第 2 步分诊处理，不绕过用户选择自动分析；用户直接报 bug 用 `task-bug`，连续口述登记用 `pending-record`。已验证的技术发现属 `docs/findings/`，不是待办，不转 task。
- `docs/pending/parked/` 条目不自动捞——用户显式要求复活时，先跑 `.repo_template/scripts/pending.py revive {pNNN} --write` 移回 `todo/`，再走核实→建 task 流程。

## 完成

汇报：核实后仍有效的条目、新建 tid、合并的条目列表、判断已不存在的条目（已闭环）、跳过（已有）列表、bug 分诊结果（各条目 A/B/C 去向、子代理结论、新建或并入的 tid）、建议执行顺序。
