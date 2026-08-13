---
name: pending-record
description: 持续澄清并登记 pending 条目（需求/技术债/bug）。用户连续口述待办时调用；澄清后派子代理写 pending，主会话不停聊。bug 派子代理走 task-bug 分析后登记。触发：/pending-record、记 pending、登记待办、记录 bug/需求。
disable-model-invocation: true
---

# pending-record

持续接收用户口述的待办，**先问清楚再登记**。澄清后派**后台子代理**落盘 `docs/pending/todo/`；主会话立即继续接下一条，不等子代理结束。

本 skill **只登记**，不建 task、不改生产代码。落盘由子代理完成；**子代理禁止自行 commit**。会话收尾经用户同意后，主会话可一次性提交本会话已完成的 pending 文件（见第 6 步）。立 task 走 `task-from-pending` / `task-create`；用户直接报 bug 且要完整立项也可走 `task-bug`。

## 运行模式

进入本 skill 后保持「记录会话」直到用户说结束 / 提交 / 退出。

主会话维护两张表（内存即可，不必落盘）：

|表|内容|
|---|---|
|**in-flight**|已派未终态的 worker：`worker_id`、类型（entry/bug/update/park）、主题摘要、拟 slug、确认要点|
|**session ledger**|已终态项：`pNNN`、路径、动作（新建/更新/复用未改/park/失败）、worker 结论摘要|

规则：

1. 用户可连续发多条待办；一条未澄清完不跳下一条。用户一条消息塞多项 → 先列队列，按序澄清。
2. 每条：澄清 → 共享确认 → **先写入 in-flight** → 派子代理登记 → 主会话立刻接话。
3. 子代理完成后：移出 in-flight、写入 session ledger；用一两句回报 `pNNN` / 失败原因，不打断当前澄清。
4. **派发前查重范围** = `pending.py list` 已落盘项 ∪ **in-flight 主题** ∪ session ledger。与 in-flight / ledger 语义等价 → 不新派创建 worker；告知用户「同主题处理中或已记」，问是否改并入更新。
5. 会话结束或用户要求提交 → 走第 6 步收尾屏障，**禁止**在仍有 in-flight 时直接进入 commit 确认。

## 步骤（每条待办）

### 1. 分型

先判定类型；说不清就问一句，不默认：

|类型|判定|落盘|
|---|---|---|
|**bug**|已有行为与期望不符 / 回归 / 假绿|子代理走 `task-bug` 第 1–6 步后 `--kind bug` 登记；用户同时明确「现在不办」→ 登记后再 `park`（须理由）|
|**普通**|新功能、遗留、技术债、该做未做|子代理 `pending.py new`（默认 entry）填字段|
|**不办/暂搁**|用户明确现在不办（含 bug 暂搁）|先按上表完成登记（或复用已有 `pNNN`），再 `pending.py park {pNNN} --reason "..." --write`|

已验证技术事实不进 pending，走 `findings.py`（本 skill 不登记 findings，除非用户明确要求另开）。

### 2. 澄清（必须，禁止臆想）

对齐「高压追问」习惯（参考 `grilling` skill）：不共享理解前不派子代理、不写条目。

规则：

- **支持批量抛问**（一次可列多条缺口，编号列出）；用户可逐条或一并答复，收齐后再汇总确认。缺口少或用户只回了一点 → 可继续补问，不强制一轮问完。
- 每问附**推荐答案**（有依据时）；能靠代码库 / 现有 pending / task 回答的先查，不拿能查到的事烦用户。
- **禁止**把模糊口述扩写成具体需求或根因；缺信息就问，不补脑。
- 用户说「你看着写」「随便记」→ 仍须收敛到可登记字段；收敛不了就停，说明缺什么。

**bug 必收敛**（未齐继续问）：

- 期望 vs 实际（可观察现象，非猜的代码原因）
- 如何触发 / 复现线索（步骤、输入、环境；没有就问有没有、能否补）
- 影响范围（谁用、多严重、是否回归）
- 相关 tid / 日志 / 文件（用户有则记，无则不编）
- 若用户已说暂搁：不办理由（写入 park）

**普通条目必收敛**：

- 要做成什么样（可观察结果或交付物），不是实现方案臆测
- 为何现在记、不记会怎样（优先级/上下文一句即可）
- 来源（用户提出 / 某 task 遗留 / 技术债自查等）
- 非范围：明确不做什么（用户提了才写，不自行加）

可从仓库直接核实的歧义先读代码再问剩余缺口。

### 3. 共享确认

澄清够用后，用**极短复述**（3–6 行）请用户确认类型 + 将写入字段要点（含是否 park）。未确认不派子代理。用户改口 → 回第 2 步。

确认后：对照 in-flight / session ledger / `pending.py list` 做语义查重；命中则告知用户（处理中 / 已记 / 已有 `pNNN`），询问是否并入更新——同意派 **4c**，拒绝则跳过；不平行再开创建 worker。

### 4. 派子代理登记（后台，主会话不阻塞）

确认且未与 in-flight 冲突后：登记 in-flight → 立刻派子代理；prompt 只含**已确认事实**、\*\*in-flight 中其它主题摘要（防重复）\*\*与下列指令，不塞未确认猜测。

#### 4a 普通条目（新建）

子代理：

1. 再查 `pending.py list`（防与其它会话竞态）；`todo/` / `parked/` 已有等价条目 → **不新建**，回报 `action=reuse`、已有 `pNNN`、路径、建议是否更新及建议改哪些字段。
2. 无等价则：
    ```bash
    python3 scripts/repo_template/pending.py new --slug <snake_case主题>
    ```
3. 填写模板字段（替换占位，删花括号说明）：
    - H1：`# pNNN {一句话简述}`
    - `- 来源：` / `- 内容：` / `- 处理：未开`
4. 用户确认暂搁：`pending.py park {pNNN} --reason "..." --write`，回报 `action=park`
5. **禁止** commit、`task.py add`、改 `src/` `tests/` 生产树、手写创建 `pNNN_*.md`（必须经 `pending.py new`）。

回报主会话：`action`（create / reuse / park）、`pNNN`、路径、一句话摘要；失败写原因。

#### 4b bug 条目（分析 + 登记/更新）

子代理按 **`task-bug` 第 1–6 步**执行（读 `.agents/skills/task-bug/SKILL.md`）：

1. 收敛现象（以主会话确认内容为准；仍缺可观察事实才停，不猜）
2. 复现**仅** `.scratch/`；失败则汇报尝试与卡点，**不硬登记**
3. 根因落到可验证机制 + 分类
4. 同类位点扫描：同机制是否在其他路径复现；已确认的合并进影响/根因/补测，默认一条 pending
5. 测试缺口分析（为何没盖住 + 覆盖各已确认位点的补测方向）
6. 登记或更新：
    - 查重后无等价：`pending.py new --slug <主题> --kind bug`，填全字段
    - 已有等价 bug 条目：**就地更新**该文件，保留 `pNNN`（`action=update`）
    - 字段：现象 / 影响 / 根因（含同类清单或已扫无）/ 测试缺口 / 线索 / 处理：未开
7. 用户确认暂搁：登记/更新成功后 `pending.py park {pNNN} --reason "..." --write`

子代理边界：

- 只写 `.scratch/` 与 pending 条目；禁止 commit、禁止 `task.py add` / start、禁止生产修复
- **不**做 `task-bug` 第 7–9 步（不请立项、不建修复 task、不替用户 commit）
- 查重时把 prompt 里的 in-flight 摘要当作「已有」：主题等价则不要 `new` 第二条，回报冲突供主会话合并

回报主会话：`action`、`pNNN`、根因一句话、已确认同类位点数/清单（或已扫无）、补测要点、`.scratch/` 线索；复现失败或无法登记时写明原因。

#### 4c 就地更新已有条目（用户同意改写后）

主会话在第 5 步得到用户「改写已有 `pNNN`」授权后派此 worker（计入 in-flight）：

1. 只编辑已确认路径 `docs/pending/{todo|parked}/pNNN_*.md`，**保留 `pNNN` 与文件名编号**；需要改 slug 时用 `pending.py rename`，不手改文件名绕过脚本。
2. 按类型更新字段：普通改来源/内容；bug 按 task-bug 合并字段写（含同类位点）。
3. 用户同时要求暂搁：更新后 `park`。
4. 禁止 new 第二条、禁止 commit、禁止改生产树。

回报：`action=update`、`pNNN`、路径、变更摘要。

### 5. 主会话在子代理回报后

- **create / update / park 成功**：记入 session ledger；告知 `pNNN` + 类型 + 一句话；bug 附根因与同类位点摘要。提醒「未建 task；要修走 `task-from-pending` / 批准后的 `task-bug` 立项」。
- **reuse（发现已有、未改）**：记入 ledger（reuse）；告知已有 `pNNN`，问是否改写。
    - 用户同意改写 → 派 **4c** worker（写入 in-flight），主会话继续。
    - 用户拒绝 → ledger 保持 reuse，不再动文件。
- **失败**：记入 ledger（failed）；原样转述卡点，问用户补信息后重新澄清，或改记普通条目 / 暂搁。

### 6. 会话收尾（含 worker 完成屏障）

用户结束记录或要求提交时：

1. **屏障**：列出全部 in-flight worker。有运行中项时：
    - 默认：**等待全部进入成功/失败终态**后再继续；
    - 或请用户二选一：**(a) 等待全部完成** / **(b) 只提交已完成项，其余继续跟踪**（选 b 则 commit 范围仅 session ledger 已终态且有文件变更的项；in-flight 保留到后续会话或继续等）。
    - **存在运行中 worker 且用户未选 b 时，禁止进入 commit 确认。**
2. 全部相关 worker 终态后（或用户选 b）：列本次 **create/update/park** 的 `docs/pending/todo|parked/pNNN_*.md`（reuse 未改与 failed 不列入 commit）。
3. 询问是否 commit；同意则只提交这些 pending 文件（可含用户明确要求的相关 docs 改动）。`.scratch/` 已 ignore，不入 commit。
4. 未同意 → 文件留工作区，汇报路径与未完成 worker。

## 边界

- **不臆想**：需求与 bug 现象必须来自用户确认或仓库可核实事实；根因只许分析子代理在复现后写，主会话澄清阶段不编根因。
- **主会话不落盘**：登记/分析/更新写操作交给子代理；主会话只澄清、确认、维护 in-flight/ledger、派发、汇报、收尾屏障与询问 commit。
- **不建 task、不 start、不改生产代码**；pending ≠ 值得立 task。
- 子代理**永不**自行 commit；仅主会话在第 6 步经用户同意后提交。
- 不把 findings 与 pending 混用；不自动 archive / 不擅自 park（park 须用户明确不办）。
- `parked/` 不自动捞；复活是 `task-from-pending` / `pending.py revive` 的事。
- 并行 worker 的语义去重依赖主会话 in-flight 表；`pending.py` 锁只保证编号不撞，不保证语义唯一——**派发前必须查 in-flight**。

## 完成

会话内登记清单：`pNNN`、类型（entry/bug/parked）、路径、动作、子代理成败；in-flight 必须为空或已按用户选择声明「仍跟踪」。未 commit 则标明工作区未提交。下一步可选：`task-from-pending` 捞待办，或继续本会话记下一条。
