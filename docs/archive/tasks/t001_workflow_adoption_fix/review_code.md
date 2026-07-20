# Task review t001（reviewer_focus: 代码）

- task：`t001_workflow_adoption_fix`
- spec：`spec.md`
- diff_anchor：`ba66be5ffeb0decc94e5cb92e9accd7bb6382a2a`
- target：`git diff ba66be5ffeb0decc94e5cb92e9accd7bb6382a2a`
- round：1
- reviewed_at：2026-07-21 18:40 UTC+8

## Findings

（无）

## 结论

- 前轮 finding 复核（Round 2 才写）：N/A
- 本轮新发现：0 条
- 总体判断：纯文档 task；相对 `diff_anchor` 的工作区改动覆盖 AGENTS / conventions / decisions / README / tasks_index / task 模板；10 条 AC 均有对应落地，未发现规格缺失、矛盾状态机或模板冲突。

### AC 对照摘要（证据，非 finding）

| AC | 结论 | 主要证据 |
|----|------|----------|
| review target 为 WT+index；无 `...HEAD` 作证据源 | 满足 | `AGENTS.md:105-108` 肯定 `git diff <diff_anchor>` 并禁止三点 diff；`conventions.md:56`、两 prompt target/Process 一致；全文规范性路径无将 `...HEAD` 标为证据源（仅禁令/历史/决策背景出现） |
| R1 零 finding 可收尾；R2 FAIL → blocked | 满足 | 状态机 `AGENTS.md:59-63`；step 5/6/7 与 `conventions.md:64-68` PASS 判定式一致 |
| 拆分填 spec/plan；step 7 不写 specs；需求完结 | 满足 | 拆分 `AGENTS.md:48-52`；step 7 `AGENTS.md:97`；`### 需求完结` `AGENTS.md:111-117` |
| backlog 建目录与 tasks_index 一致；未填 dropped 可不归档 | 满足 | `AGENTS.md:48-52,119-122` 与 `tasks_index.md:5-6` 对齐 |
| step 1 分支校验；log 模板含 diff_anchor | 满足 | `AGENTS.md:70`；`docs/templates/task/log.md:3`；`conventions.md:41` |
| 删旧 `review_prompt.md`；新 prompt 规则齐全 | 满足 | 工作区 `docs/templates/task/` 无该文件；code/test prompt 均含零发现/finding 边界/`read-only 边界`/`git rev-parse`；test prompt 含 `.fill()` 调查制 |
| 严格模式撤回路径；exception 不改写 verdict | 满足 | `AGENTS.md:85,94-96`；`conventions.md:94,112`；`tasks_index.md:8`；`task_report.md:15-26`；`decisions.md:003-004` |
| README 与 AGENTS 语义一致 | 满足 | `README.md:12-13,38-47`（schemas/config、specs 全需求 done 后固化、review 证据源） |
| finding 标题 ` - `；严重度以 conventions 为唯一完整定义 | 满足 | `conventions.md:60,71+`；prompt/review 示例均为 ` - `；prompt 严重度节引用 conventions 并仅给摘要 |
| adoption 可选；笔误/事实类判定 | 满足 | `conventions.md:44`；`AGENTS.md:87-89` |

### 范围外提示（不进 finding 表）

- `docs/reviews/review_20260720_2346/*` 历史报告仍描述旧态（含旧 `review_prompt.md`、旧 target）；属审阅归档材料，spec 非范围未要求改写。
- AC/adoption 字面曾并列 `git diff --cached`；落地统一为 `git diff <diff_anchor>` 并声明覆盖已暂存与未暂存（`AGENTS.md:107`）。git 语义下对工作区相对 anchor 的审查通常足够；`decisions.md:001` 亦只固化单命令。不构成可复现失败场景，故不记 finding。
- `AGENTS.md:99,103` 仍写 `git log --grep TNNN`，与 `{TID}` 占位习惯略不齐（adoption 项 27 属 LOW）；不影响可执行主路径。

verdict: PASS
