# 决策记录（ADR）

只记录已经确认、影响后续工作的非显然决策。追加新条目，不重写历史；决策被替代时，新条目通过“替代”字段引用旧编号。

每条结构：`## NNN 标题（YYYY-MM-DD）`，下接 `- 背景` / `- 选项` / `- 结论` / `- 替代` 四项；替代填旧编号，无则写「无」。

## 001 review 证据源用 working tree 相对 diff_anchor（2026-07-21）

- 背景：单 task 单 commit 且 review 在 commit 前时，`git diff <diff_anchor>...HEAD` 常为空，导致虚假 clean review。
- 选项：A working tree `git diff <diff_anchor>`；B task 内 WIP commit + squash；C commit 后再 review。
- 结论：A。保留「一 task 一 commit + commit 前 review」，只改证据源。
- 替代：无

## 002 backlog 拆分即建目录并填写 spec/plan（2026-07-21）

- 背景：AGENTS 要求拆分建目录，tasks_index 曾写 backlog 不建目录，冲突。
- 选项：A 拆分即建目录；B active 才建目录；C 建目录不强制填。
- 结论：A，并要求验收标准非空。
- 替代：无

## 007 dropped 任务目录一律归档（2026-07-21）

- 背景：曾写「未实质填写的 backlog 可不归档、删目录即可」；用户要求一律归档。
- 选项：A 空模板可不归档；B 一律进 `docs/archive/tasks/`。
- 结论：B。backlog / active 的 dropped 均归档，无例外。
- 替代：收紧 002 附属的「可不归档」表述。

## 008 task.md 合并 owner 过程文档（2026-07-21）

- 背景：log / adoption / task_report 过碎，agent 易漏写。
- 选项：A 合并为 `task.md`（YAML front matter + 正文）；B 整文件 JSON；C 保持三分。
- 结论：A。保留 spec/plan/review_code/review_test；`render_review_prompts.py` 从 front matter 读 tid/slug/diff_anchor。
- 替代：无

## 003 严格模式误报经原 reviewer 撤回（2026-07-21）

- 背景：无「无需修改」时误报会迫使改正确实现。
- 选项：A owner 举证 + 原 reviewer 撤回；B 保持全必修无出口；C owner 不采纳须用户批。
- 结论：A。默认仍须处置；撤回或用户裁决后可不改代码。
- 替代：无

## 004 exception 不改写 reviewer verdict（2026-07-21）

- 背景：遗留后若把 FAIL 改成 PASS，质量门禁语义崩溃。
- 选项：A verdict 不动 + tasks_index/task_report 记 exception；B 新 PASS_WITH_EXCEPTION；C 改写报告为 PASS。
- 结论：A。技术结论与工作流 done 分离。
- 替代：无

## 005 exception 与遗留报告（2026-07-21）

- 背景：遗留需可审计；满轮门禁失败不应 agent 自签收。
- 选项：A 一律无需批准；B 一律需批准；C 门禁通过后的遗留可随报告 done，满轮 blocked 须用户放行。
- 结论：初版 A；**现以 009 为准**：blocked 后 exception 须用户显式放行；报告与口头不可省。
- 替代：无（009 收紧适用边界）

## 006 specs 在每个 task 收尾时累积写入（2026-07-21）

- 背景：曾改为「全需求 task done 后才写 docs/specs/」；用户要求每个 task 在过黑盒后的**收尾**阶段写 specs，不是黑盒 step 立刻写。
- 选项：A step 4 黑盒立刻写；B 仅需求完结写一次；C step 7 收尾写（须已过黑盒）。
- 结论：C。写点只在 step 7；step 4 只做黑盒、不写 specs。
- 替代：推翻 t001 adoption 项 8「需求完结才写」；纠正误落在 step 4 的写点。

## 009 满轮门禁失败进 blocked 不自动收尾（2026-07-21）

- 背景：黑盒 5 轮、双审 2 轮打满后若仍自动收尾，等于 agent 自签收失败交付。
- 选项：A 仍自动收尾 + exception；B 永久死锁；C `blocked` + 停自动推进，用户加轮 / dropped / 显式 exception 后才可收尾。
- 结论：C。状态增加 `blocked`；黑盒未过不得进双审；收尾前置为黑盒通过且双审 PASS，或用户对 blocked 显式 exception。
- 替代：收紧 005 中「无需批准」边界；与早期 Round2 FAIL→blocked 意图对齐。
- **现以 010 为准**：取消 exception 路径。

## 010 取消 exception 收尾路径（2026-07-21）

- 背景：exception 路径允许 blocked 任务经用户放行后绕过门禁直接收尾，留下 `done_with_exception` 状态。语义复杂、与「门禁」互斥，实际无人使用。
- 选项：A 保留 exception；B 取消 exception，blocked 后只剩 加轮 / dropped 两个出口。
- 结论：B。blocked 必须 加轮 或 dropped，无第三条路。`tasks_index` 不再出现 `done_with_exception`。
- 替代：废止 004、005、009 中的 exception 分支；conventions.md 与 task.md 模板同步删除 exception 字段与说明。
