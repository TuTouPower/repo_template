---
name: task-create
description: 把用户需求拆成合格 backlog task。用户批准立项或要求拆 task 时调用。
---

# task-create

把**用户需求**拆成合格 **backlog** task。规则见 `AGENTS.md`；`task-bug` / `task-from-pending` 建 task 的落盘步骤也以本 skill 为准。

## 不用本 skill 的场景

|场景|用|
|------|------|
|修 bug、复现、根因、补测分析|`task-bug`|
|连续口述登记 pending（含 bug 分析后只记总账）|`pending-record`|
|把待办转成 task|`task-from-pending`|
|执行实施|`task-run`（多会话手动并发各跑一段）|
|只查缺什么资源|`task-preflight`|

## 步骤

1. **查重**。按 `AGENTS.md`「task 状态读取优先级」读取现有 task，避免重复 slug/等价范围；main 中被链覆盖的旧状态不重复计。

2. **需求澄清**（分歧与歧义先问清，不自行裁定）。需求有不清楚、有歧义、或多个理解皆合理处，逐条问清再拆分：
   - 每条附推荐答案；可从代码库 / 现有 task 核实的问题先核实再问，不问自己能查到的
   - 支持批量抛出问题（一次可问多条），但逐条答复后再汇总确认；需求理解未与用户共享确认前，不进入拆分

3. **拆分**。拆成结果可独立验证、对应一个实现 commit 有工程意义的 task；过大继续拆；记下建议顺序与依赖。

4. **每个 task 落盘**：
   1. `scripts/repo_template/task.py add --title "..." --slug "..." [--review-level full|single]`
      脚本自动分配 tid、建 `docs/tasks/{tid}_{slug}/`、复制模板、写 front matter。
   2. `review_level` 按风险判：
|level|适用|
|------|------|
|`full`|安全、鉴权、资金、并发、数据迁移、协议兼容（默认）|
|`single`|其余全部（含纯文档、配置、格式化）|
      判不准取 `full`。定 `single` 时在提交询问里说明理由，由用户确认。
   3. 只读仓库，填写 `spec.md`：
      - **只替换 `{...}` 占位符**；`<!-- 规范 -->` 标记内的就近规范（如「只写可观察行为」「mock 边界、fixture 来源」）不得删除或改写。
      - **契约区**：范围、非范围、行为 AC（非空）、可测试性声明。版本号/库/目录不进 AC；需部署或人工验证的 AC 加 `[deploy]`。
      - **上下文区**：有意不测、测试策略、未知契约清单、风险与回退、依赖与约束、finalization 更新的 blueprint。未知契约须分类（见第 6 步）。
      - 契约区执行期不改；上下文区执行期可补。
   4. `task.md`：只填正文能填的部分。front matter 由脚本维护，**不手改**；`diff_anchor` 由 `task.py start` 实写（创建 worktree 时写入 base SHA）。
      **不预测实施步骤**——创建期未读代码，写出来的步骤执行时必然失准；步骤由 `task-work` 边做边记进「实施笔记」。

5. **逐 task 自检**：
   - AC 可验收，`spec.md` / `task.md` 无残留 `{...}` 占位符。
   - 每个 task 填写完成后运行 `python3 scripts/repo_template/task.py preflight {tid} --allow-backlog`；全部 `preflight=PASS` 才能进入统一询问提交。
   - 拆分三问（对本次全部 task 过一遍，发现并修正后再提交）：
     1. **AC 可证伪**：每条 AC 是 yes/no 可判；出现「可用/合理/正常/完好」类不可测词 → 就地改写为可观察谓词。
     2. **task 原子性**：标题含「和/与/并」或一个 task 有两个不共享验收面的独立交付 → 拆。
     3. **最弱依赖**：哪个 task 失败会级联最多下游？扇出过大 → 评估降扇出或重排顺序。
     三问结论（clean 或 findings + 已做修正）随第 7 步统一提交询问一并列出。

6. **未知契约分类**（权威；步骤 4.3 引用此处）：
   - `UNVERIFIED-SPIKE`：task 需要先做实验确认的事项（新 major、非标准 provider、协议兼容、平台差异、性能或工具行为）。写进「未知契约清单」并标 `UNVERIFIED-SPIKE`；不在创建期写生产代码，留给 `task-work` Step 1 实验。
   - `UNVERIFIED-BLOCKING`：只有用户或外部环境能核实的事项。task 可保持 backlog，但核实前 `start` 必须失败。
   - 核实后删除标记，改写为结论与验证方式；裸 `UNVERIFIED` 视为格式错误。

7. **全部 task 落盘自检后统一询问提交**。本次所有 task 完成第 4-6 步落盘与自检后，一次性列出全部新建 task 目录与本次重建的两个 index，询问用户是否提交；同意后用**一个创建 commit** 提交本次全部 task 目录与派生 index，不含生产实现。单 task 场景行为一致（落盘自检后提交一次）。链式调用时，调用方改动的总账不纳入本 commit，由调用方在 task 创建完成后单独回写、确认与提交。用户不提交则保持工作区。

## 边界

- 不 `start` / `finish` / 实施修复。
- 创建期只读生产树；只允许写当前 task 目录与 `task.py` 自动重建的两个 index。

## 完成

backlog 已就绪，未 start。下一步 `task-run`（或先 `task-preflight`）。
