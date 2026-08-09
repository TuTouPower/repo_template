---
name: task-schedule
description: none
disable-model-invocation: true
---

# task-schedule

首次分析 backlog task 的依赖与并发冲突，把调度图写入 task front matter，再调用 `view` 输出全景。之后 `task.py view` 随 task 合并进主干持续刷新可跑集，无需再次调用本 skill。

## 输入

| 用户输入 | 分析范围 |
|----------|----------|
| 无参数 | 全部有效 `backlog` task |
| 一个或多个规范 `tNNN` | 重算指定 task；判冲突时仍比较全部已调度 backlog 与进行中 task |

仅接受仓库规范 tid。

## 步骤

1. **建有效状态基线**。

   ```bash
   scripts/repo_template/task.py list --status backlog
   git worktree list --porcelain
   git branch --no-merged <default> --list 't[0-9]*_*'
   ```

   `<default>` 与 `scripts/repo_template/task.py` 的 `default_branch()` 一致。有效状态按 `AGENTS.md`「task 状态读取优先级」判定（archive 仅作历史回溯，不参与有效状态判定）。对登记 worktree 读取其中 task 状态与 diff；对未合并分支用 `scripts/repo_template/task.py list/show --ref {branch}` 读取。主干中已被 worktree/ref 覆盖的 backlog 不进入分析范围。

   ```bash
   git diff --name-status -M -C <default>...{branch}
   git -C {worktree} diff --name-status -M -C
   git -C {worktree} diff --cached --name-status -M -C
   git -C {worktree} ls-files --others --exclude-standard
   ```

   rename/copy 同时计源、目标路径。基线占用集 = 进行中 task 已提交 diff、worktree staged/unstaged/untracked 路径、spec 推导待改路径。无法归属的脏 worktree 按冲突处理。

2. **列候选**。无参数时取全部有效 backlog；指定 tid 时只重算指定 backlog。非 backlog 单列跳过，不修改。候选为空则报告后结束。

3. **推导改动面与依赖**。读取每个候选 `spec.md`：契约区的范围/非范围，上下文区的依赖与约束、blueprint 更新点。推导：

   | 维度 | 内容 |
   |------|------|
   | 代码路径 | 预计新增/修改的 `src/` `tests/` `scripts/` 文件或目录 |
   | 共享契约 | `schemas/`、schema/codegen 输入、`config/`、blueprint 与共享文档条目 |
   | 硬前置 | spec 明示依赖的 task；写入 `depends_on` |

   spec 太粗、存在多种合理解释或依赖无法确认时，不猜测，标记 `pending_clarification`。

4. **判冲突**。以下任一成立即写入 `conflicts_with`，不把冲突改写成依赖：
   - 代码文件相交；同目录都改结构性文件；
   - 同一 schema/codegen 输入、migration 窗口、config key、blueprint 或共享文档条目；
   - 与进行中 task 的实际/推导改动面相交。

   仅新增不同文件不算冲突。冲突存疑时保守标记。冲突边由 `task.py edit` 自动维护双向，不手工双写。

   禁令：与已有 `depends_on` 关系（含传递）的 task 对禁止写 `conflicts_with`——依赖已蕴含串行，冲突边冗余，`edit` 会以「冲突边与依赖路径冗余」拒绝。冲突的阻塞语义：仅当对端正在运行，或对端 backlog 且依赖已满足（dep-ready）且序号更小时，才压住本 task；被依赖阻塞的对端不构成互斥。

5. **落盘**。禁止直接编辑 `task.md` 或 index。对判断完整的每个候选执行一次完整覆盖：

   ```bash
   scripts/repo_template/task.py edit {tid} \
     --depends-on "t001,t003" \
     --conflicts-with "t006,t008" \
     --schedule-status scheduled
   ```

   无依赖/冲突时对应参数传空字符串。无法判断时只执行：

   ```bash
   scripts/repo_template/task.py edit {tid} --schedule-status pending_clarification
   ```

   若新冲突指向非可编辑 backlog，`edit` 会拒绝反向边写入；该候选改标 `pending_clarification`，报告阻断来源，不绕过状态机。

6. **校验并输出全景**。

   ```bash
   scripts/repo_template/task.py view
   ```

   `invalid_graph` 时按错误中 tid 修正调度字段后重跑；禁止绕过。成功时原样报告 `scripts/repo_template/task.py view` 输出，冲突阻塞行附带被阻塞 task 标题；另用一句话报告本次已调度、待澄清、跳过 tid。

7. **生成三种执行形式**（仅报告，不落盘）。基于本次分析范围内全部 `schedule_status=scheduled` 的 backlog；不含 `pending_clarification` / `unscheduled` / 进行中 task。三种形式覆盖同一批 task，各给可直接复制的执行命令。依赖或冲突指向进行中 task（active/blocked）的 backlog 当前不可执行：一律摘出，标注「等待运行中 tXXX」，不混入链序。

   - **形式一 · 全串行一条链**：全部 scheduled backlog 排成单一拓扑序，一行命令直接复制。约束：`depends_on` 前置先行；`conflicts_with` 对也须串行——冲突对内部优先按 spec 语义定先后（谁先建契约、谁复用），无语义序时序号小者先。输出形如：
     ```
     /task-run t023 -> t025 -> t027 -> t028 -> t029
     ```
   - **形式二 · 下一批并发单跑**：取 `view`「▸ 下一批可跑」的 tid，与 `view` 严格一致，不另行计算；每个 tid 一条命令，多会话各开一条并行。无可跑则明确报告「本批无可跑项」。输出形如：
     ```
     /task-run t023
     /task-run t025
     /task-run t027
     ```
   - **形式三 · 分阶段并发计划**：先把全部 scheduled backlog 尝试分入「链内串行、链间零冲突零依赖」的并发链——冲突连通分量强制同链（链间不得残留冲突边）；依赖边收进同链且同向；无冲突无依赖的独立 task 按主题/变更面聚合。能拆出 ≥2 条链时按此输出：链内顺序 = 依赖序 + 冲突对语义序；链命名按主题（如「资金幂等线」），非机械编号；每条链一行命令，链头附链名与一句同链/顺序理由。输出形如：
     ```
     链 1 · 资金幂等线（full×2）
     /task-run t363 -> t364
     （同改 deduct.ts owner 语义，必须串行；t363 先建 owner 契约，t364 复用。）
     ```
     冲突图全连通、拆不出多条零冲突链时，**不得退化为形式一**，改输出分阶段并发计划：
     1. **算启动前沿**：每个 task 的启动前沿 = `depends_on` 传递闭包 ∪「冲突序中排在其前的全部冲突对端」的传递闭包。冲突对顺序同形式一：先 spec 语义序（谁先建契约、谁复用），无语义序时序号小者先。
     2. **立即可跑集**：启动前沿为空的 task 立即并发。能把后续 task 直接串进链尾的串成链（某 task 的启动前沿顶端恰为某链尾且无其它阻塞时，以 `->` 串入该链），否则各自成单节点链。
     3. **逐节点解锁条件**：其余 task 逐条写 `tX（+ tY…）完成 → tZ 可开`，条件精确到具体节点，不设全局 barrier；多个前置须全部完成才解锁的标「全部」；被 ≥2 条链共同后继的节点（汇流点）单独标注「汇流」。
        **执行提示（与 `start` 门禁对齐）**：解锁条件是人工协调计划，不是自动代码继承。
        - 分叉前置（t023、t025 在不同链、互不为祖先）时，汇流点 t028 无法自动落到单一前置分支：`start t028 --base` 必须显式指向最后完成的前置分支，其余前置分支需先 `integrate-chain` 合入主干；否则 start 因多个未合并依赖分支不构成单条祖先链而 FAIL。
        - 冲突序未写入 `depends_on`：被冲突序压后的 task 若 spec 依赖「先建契约」task 的产物，新会话从主干启动不会含其代码——须在 `depends_on` 补依赖或先 integrate 前置，不能只靠冲突排序保证代码继承。
     输出形如：
     ```
     立即可并发 3 链：
     链 1：/task-run t023 -> t024
     链 2：/task-run t025 -> t026
     链 3：/task-run t027
     解锁：
     - t023 + t025 全部完成 → t028 可开（新会话 /task-run t028）
     - t027 + t028 全部完成 → t029 可开（汇流点，单跑 /task-run t029）
     ```

8. **询问提交**。列出本次改动的 `task.md`（含 peer 反向边）与两个派生 index，询问用户是否提交；同意后才 commit（维护期自成一个 commit，subject 含已写图 tid）。index 已入库且由 `edit` 重建，须随维护 commit 一起提交。用户不提交则保持工作区；但 worktree 从主干 HEAD 创建，未 commit 的调度字段不会进入 task 分支——须先 commit 再执行。

## 边界

- 本 skill 只写 task 调度字段和脚本派生 index；不改 spec、代码、测试或 blueprint。
- 不建分支/worktree，不调用执行类 skill，不执行、finish、drop 或合并 task。
- `view` 只输出 task 全景（运行中/待运行分组/已结束），不执行 task。执行由 `task-run` 承担（多会话手动并发各跑一段）。
- 新增、rewind、merge 后出现 `unscheduled` / `pending_clarification` 时，用户再次调用本 skill 重算相关 task。

## 完成

报告：已写图 tid、待澄清/跳过 tid；单列因已有依赖关系（含传递）而按 Step 4 禁令跳过的冲突对；随后原样输出 `scripts/repo_template/task.py view` 结果，再附三种执行形式（全串行一条链、下一批并发单跑、分阶段并发计划）。
