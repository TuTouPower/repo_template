# review_task_batch_scheduling — Claude Haiku 审阅

## 当前模型判断依据

上下文仅提供 `default_model` 占位符，无可靠模型 ID。本 agent 以 `haiku` 模型运行。

## 模块 slug

`task_batch_scheduling`

## 审阅范围

全量审阅 `/home/karon/karson_ubuntu/repo_template/docs_repo/plan_task_batch_scheduling.md`（含与其交互的现有 `task.py` 状态机、`tasks-parallel` skill、`CLAUDE.md`/`AGENTS.md` 路由表、`docs/blueprint/decisions.md`、`docs_repo/decision_log.md` L21）。

---

## 高优先级

### H1. `depends_on` 已存在于 FRONT_MATTER_KEYS（L21 已落地），方案未说明与现存的兼容

- **位置**：`depends_on` 字段设计段、改动面 `task.py` 行
- **现象**：`decision_log.md` L21 已裁决 `depends_on` 进 front matter，状态标「已落地」。当前 `FRONT_MATTER_KEYS` 不含该 key（输出为 `tid, slug, title, status, branch, worktree, review_level, diff_anchor, note`），但 L21 可能是通过 `add --depends-on` 参数支持而与本次的列表语义不同。方案全文假定 `depends_on` 是全新字段，未提及 L21 版本的存在形式、是否已有参数支持、本次是否扩展现有实现。
- **影响**：实施时可能发现 `depends_on` 已部分存在，方案的设计假设（空默认、列表覆盖语义）与 L21 形态不一致，导致返工。
- **建议**：先查 L21 落地的 `depends_on` 实际形态（是否已作为 `add/edit` 参数？是否已是 front matter key？），再在本方案中明确本次是「新增该字段」还是「扩展现有字段为列表 + 新增校验逻辑」。若 L21 是单值，需说明单值到列表的迁移语义。
- **置信度**：中（L21 标记已落地但 `FRONT_MATTER_KEYS` 未含，可能未实际合入默认分支或只在 task 分支中）
- **优先级**：高

### H2. `start` 门禁与 D12（取消 main 冻结）的交互未覆盖

- **位置**：`start` 门禁节
- **现象**：方案在 `start` 加双门禁（depends_on 全部 done + conflicts_with 无交集），但未说明门禁执行时依赖/冲突的「done 集合」和「active 集合」的确定方式。decisions.md D12 已取消 main 冻结，意味着 main 上 task 状态可能滞后于链尾分支。当前 `start` 在「干净主仓默认分支」执行，但门禁所需的 done/active 状态应该从哪里读取——main 的 index/archive、未合并的分支链尾 ref、还是 worktree 登记表？方案未指定。
- **影响**：门禁实现可能漏判或误判——在 main 上看到 t003 是 backlog（实际在未合并链尾已是 done），拒绝合法的 start；或者 main 上看不到 active 的 t005（在另一会话的 worktree 中），放行了与 t005 冲突的 task。
- **建议**：门禁节补充状态源优先级声明，对齐 CLAUDE.md「task 状态读取优先级：登记 worktree → 未合并 task 分支链尾 ref → main」。`next-batch` 的 done/active 集合来源已有说明，`start` 门禁应复用同一优先级。
- **置信度**：高
- **优先级**：高

### H3. `schedule_status: pending_clarification` 的生命周期不完整

- **位置**：Agent 结晶节 + 算法节
- **现象**：`schedule_status` 写入与输出已有定义，但缺少以下生命周期场景的处理：
  1. `schedule_status: pending_clarification` 的 task 用户澄清并补充字段后，`edit --depends-on` 会覆盖列表，但 `schedule_status` 需手动清除（方案提到「澄清后 `edit` 清除」），缺少强制校验——如果用户忘了清 `schedule_status`，task 将永远停在待澄清状态。
  2. 一个已写入 `schedule_status: pending_clarification` 的 task 之后被 rewind（active→backlog），是否需要重跑 `/tasks-schedule`？还是 `schedule_status` 不随 rewind 变，用户澄清后不再需要 Agent？
  3. `finish` 时 `schedule_status` 与正常字段的关系未定义——如果 task 带着 `pending_clarification` 被 finish，应拒绝还是允许？
- **影响**：状态泄漏，出现永久卡在「待澄清」的 task 或带着未澄清标记归档的 task。
- **建议**：
  - `next-batch` 输出待澄清 task 时同时提示「澄清后需手动 `edit --schedule-status ""` 清除标记」。
  - `finish` / `drop` 前置校验：若 `schedule_status` 非空（`pending_clarification`），拒绝并提示原因。
  - rewind 时 `schedule_status` 保持不变（调度信息不因状态回退而丢失），由用户决定是否重跑 `/tasks-schedule`。
- **置信度**：高
- **优先级**：高

---

## 中低优先级

### M1. 多链尾合并的分布式并发窗口问题

- **位置**：批的消费方式节末尾
- **现象**：方案描述「批末多链尾合并」为「按 tid 升序逐个合并」，但批内 task 可在多会话各自独立 `start`/`finish`，不同 task 的 `finish`（含 merge 到 main）时间不定。如果一个 task 先 finish 并 merge 了，后面其他链尾合并时 main 已变，可能引入额外的 merge 冲突。方案仅提到「合并冲突暂停报告冲突 task 对」，但未说明多链尾合并时是否应在全部 task finish 后统一做，还是允许穿插。
- **影响**：用户在多会话并行 finish 时可能遇到意外的 merge 顺序和重复冲突。
- **建议**：明确多链尾合并的执行窗口——是否要求用户在全部批内 task finish 后再统一调合并子命令。若是，需新增一个显式命令（如 `task.py merge-batch t003,t005,t007`）；若允许穿插，需说明 git merge 顺序不保证无额外冲突，且冲突检验应覆盖「当前 main HEAD 与待合并 task 分支的 diff」，而非假设 main 保持 merge 前的状态。
- **置信度**：中
- **优先级**：中

### M2. `next-batch` 的 done 集合漂移问题

- **位置**：done 集合定义
- **现象**：done 集合从 `docs/archive/tasks/` 读取 `status: done`。但 `finish` 将 task 目录从 `docs/tasks/` 移入 archive 后，archive 中的 task 状态变为 `done`，main 上 `tasks_index.json` 重建前可能滞后。`next-batch` 用 archive 作为 done 数据源是合理选择，但方案未说明若 `finish` 执行在 worktree 中完成而主仓 archive 尚未更新时的行为（主仓 `next-batch` 读不到刚 finish 的 task）。
- **影响**：短时间窗口内 `next-batch` 的输出与最新实际状态不一致。
- **建议**：补充说明 `next-batch` 只在主仓执行，且用户应在 finish（含 merge+cleanup）完成后才在主仓调 `next-batch`。这不是 bug 而是使用约定，需写进文档。
- **置信度**：中
- **优先级**：中

### M3. 贪心独立集的保证与退化

- **位置**：算法节
- **现象**：算法选择「按 tid 升序贪心独立集」，声明「不保证最大」。对于简单冲突图（如全连接图），贪心独立集与最大独立集的差异可能很大——最坏情况下贪心只取 1 个而最优可取 N/2 个。方案将此视为可接受（「确定性优先于并行度最大化」），但未说明在冲突密度高时用户如何应对（手动分析？接受串行？）。
- **影响**：冲突密集的大型项目可能每批只调度 1 个 task，退化到近似串行。
- **建议**：文档补充一句退化策略——冲突密度过高时建议重跑 `/tasks-schedule` 检查 `conflicts_with` 是否过度保守。
- **置信度**：中
- **优先级**：低

### M4. `--done` 宽容解析边界模糊

- **位置**：`--done` 语义节
- **现象**：t0015 被拒绝（「无法区分 t015 多打个 0 与 4 位 tid」），但 t0013（同样 4 位）按规则也当拒绝，而 t001（3 位）直接接受。规则本质是「3 位及以下宽容，4 位及以上拒绝」，但未明写这个阈值。`T00025` 去多余零后变成 `t025`，也是 3 位，与拒绝 t0015 的「4 位拒绝」一致。
- **影响**：实现时需明确判断逻辑；文档的 4 行表格并非实现级完备。
- **建议**：在实现 task 的 spec 中定义确定性的解析规则（regex 或状态机），计划文档保持当前抽象级别即可，不做改动。
- **置信度**：高（属于实现细节，计划层不需要完整算法）
- **优先级**：低

### M5. 菱形依赖中「从最新 main 开 worktree 即拿到全部前置改动」与链式分支模型冲突

- **位置**：批的消费方式节
- **现象**：方案说汇点 task「从最新 main 开 worktree 即拿到全部前置改动」，但 CLAUDE.md 明确规定 `start` 的首 task 基于主干、后续 task 基于上一 task 分支形成祖先链。菱形依赖的汇点有多个前置 task 分支，按链式规则只能选一个作为 `--base`，不能从 main 直接开（否则脱离链式拓扑）。方案在此引入了一个例外（「从 main 开 worktree」），与 L1/D01 的链式分支模型不一致。
- **影响**：菱形汇点无法按现有 `start` 的链式语义执行——要么违背链式拓扑从 main 开，要么选一个前置做 base 但缺少其他前置的变更。
- **建议**：这是一个根本性的设计冲突，需要明确处理。可选方案：
  1. 菱形汇点的 `start` 不做链式，直接从 main 开（需修改 `start` 的 `--base` 默认语义，或新增参数）。
  2. 菱形汇点放入后续批次（等所有前置 merge 到 main 后），此时 main 已包含全部前置改动，`start` 从 main 开即可——这要求多链尾合并发生在下一批之前，且 `next-batch` 的 done 集合需等合并完成。方案「汇点 task 等前置全部 done（合并 main）后自然进入后续批次」似乎倾向此方案，但「从最新 main 开 worktree」的措辞在链式模型下不准确——因为后续批次的首 task 也是从 main 开（而非从上一个链尾），断裂了链式拓扑。
- **置信度**：高
- **优先级**：高（从 M 提升至 H 级别）

---

## 改进建议

### I1. 改动面表格重复行

- **位置**：范围表格「做」列
- **现象**：`task.py next-batch 子命令` 在同一行出现两次（完全相同的两行）。
- **影响**：纯格式问题，不影响理解。
- **建议**：删除重复行。

### I2. 待办中「建议拆两 task」的粒度

- **位置**：待办 checklist
- **现象**：建议拆为两 task：task.py 改动 + tasks-schedule 改名。合理，但 tasks-schedule 改名涉及软链同步、路由表重写等联动，两 task 间有顺序依赖（task.py 先合入，改名依赖新的 `edit --depends-on` 参数可用）。
- **建议**：待办显式标注两 task 的顺序依赖（① 先于 ②），避免并行执行时的参数不存在问题。

### I3. 验证计划中 `tasks-run` 回归测试的描述不充分

- **位置**：验证节末条
- **现象**：「无调度字段的 task 行为不变」是正确的回归要求，但 `start` 加门禁后，无调度字段的 task `start` 行为本质不变（depends_on 为空即通过门禁），需要显式验证这一点而非仅说「不受影响」。
- **建议**：验证 list 加一条：无 `depends_on` / `conflicts_with` 字段的旧 task，`start` 门禁校验结果为空依赖+空冲突=通过。

---

## 不确定项

### U1. `tasks-schedule` Agent 分析是否需要读已 merged 的 diff

- **现象**：方案说 Agent「分析全部 backlog task 的依赖与冲突」，沿用现有 tasks-parallel 的「读 spec 范围 + 推导改动面 + 判冲突」流程。但新方案冲突写出为 front matter 字段后，Agent 是否仍需做「改动面推导与判冲突」这步？还是只分析依赖关系而出 `conflicts_with` 由用户手动维护？
- **可能答案**：按方案行文推断，Agent 仍需做冲突分析（因为 `conflicts_with` 对称性由 Agent 负责双向写入），但澄清后可降低后续任务的实现复杂度。
- **建议**：在 skill 重写时明确 `tasks-schedule` 的分析步骤是否与当前 tasks-parallel 的步骤 3-4 相同。

### U2. `next-batch` 是纯 Python 还是有 shell 包装

- **现象**：方案写 `alias tn='python3 scripts/task.py next-batch'`，强调「不使用 `/tasks-next` skill」。但 `next-batch` 输出需要自解释（「各未入选者阻塞原因」），当前设计是 Python 直接输出格式化文本还是有独立的输出模板？
- **建议**：实现时确定输出格式由 Python 直接 print，不另建模板文件，保持简单。

### U3. dropped task 在 depends_on 中的引用处理

- **现象**：方案写「引用不存在或 dropped 的 tid」报错，但 `--done` 语义说「dropped 不算 done」。如果 t002 depends_on t001，t001 后来被 drop，这是否应自动解除依赖（因为 drop 不等于 done，t002 的前置条件不再成立）还是始终报错要求用户手动改 depends_on？
- **可能答案**：按方案保守风格，报错要求用户手动处理（`edit --depends-remove`）更合理。但如果 t001 drop 时 t002 已是 active，用户可能不知道需要手动解除。
- **建议**：drop 时加提示——若有其他 task depends_on 被 drop 的 task，输出警告列表。

---

## 总结

方案设计思路清晰：两张图分离依赖与冲突，调度数据随 front matter 走避免第二状态源，脚本纯图算法零 LLM。发现 4 个高优先级问题：L21 已落地的 `depends_on` 形态需确认（H1）；`start` 门禁与 D12 main 冻结取消的状态源优先级未定义（H2）；`schedule_status` 生命周期缺 finish/drop 门禁（H3）；菱形依赖「从 main 开 worktree」与链式分支模型本质冲突（H4，原标 M5 提级）。中低优先级问题涉及多链尾合并的并发窗口、done 集合漂移和贪心退化。改动面表格有一个重复行。

建议实施前先澄清 H4（菱形依赖的 start 语义），这是方案与现有 task 工作流最根本的冲突点。
