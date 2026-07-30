# 审阅报告：task 批次调度方案 (plan_task_batch_scheduling.md)

## 本路模型标识
Antigravity gemini-3.6-flash-high

## 模块 slug
task_batch_scheduling

## 审阅范围
`/home/karon/karson_ubuntu/repo_template/docs_repo/plan_task_batch_scheduling.md` （全量审阅）

## 高优先级

### 1. 算法逻辑与未调度/待澄清状态过滤逻辑存在冲突
- 位置：Line 79-82 与 Line 125-129
- 现象：Line 79 处的算法将 ready 集合定义为 `backlog 中 depends_on ⊆ done 的 task`。若某个 task 的 `depends_on` 为空，但标注了 `schedule_status: pending_clarification` 或完全未包含调度字段（未调度 task），按此公式仍会被纳入 `ready` 集合并被贪心算法选入 `本批`。然而 Line 82 与 Line 129 又规定 `pending_clarification` 与未调度 task 应列入未入选原因或单独列表，Line 125 甚至声称未调度 task 需报错退出，各处逻辑互有矛盾。
- 影响：若直接按 Line 79 公式实现，会导致待澄清或完全未调度的 task 因 `depends_on` 为空而被误判为 ready 进而并发执行，破坏了调度控制边界。
- 建议：重新明确 `ready` 推导公式，显式排除带 `schedule_status: pending_clarification` 或缺少调度字段的 task；并统一处理未调度 task 时是直接报错终止还是分类输出警示。
- 置信度：高
- 优先级：高

### 2. 「整批合并/多链尾合并」缺失 CLI 承载命令与执行机制细节
- 位置：Line 107-109 与 Line 143
- 现象：方案提出在批末进行“多链尾合并”（按 tid 升序逐个合并分支至 main），并在改动面中列出 `scripts/task.py` 支持整批多链尾合并。但全篇未指定该功能挂载在 `task.py` 的哪一个子命令（如 `task.py finish-batch` 或扩展 `finish` 命令），也未定义入参形式、Git 冲突时的暂停/中断恢复状态机制。
- 影响：开发者无法依据方案完成 `scripts/task.py` 命令集设计与代码落地，批末合并流程在实现层面不可执行。
- 建议：补充「多链尾合并」的具体 CLI 子命令与参数规范（例如 `python3 scripts/task.py merge-batch`），明确合并步骤、冲突时的暂停断点及人工介入恢复机制。
- 置信度：高
- 优先级：高

## 中低优先级

### 1. `edit` 命令针对 `conflicts_with` 的修改参数未在接口表中完整列出
- 位置：Line 113-118
- 现象：方案提及 `--depends-on / --conflicts-with 语义对齐`，但在参数定义表格中仅罗列了 `--depends-on`、`--depends-append`、`--depends-remove`，漏列了对应的 `--conflicts-with`、`--conflicts-append`、`--conflicts-remove`。
- 影响：CLI 参数解析实现容易遗漏对互斥关系列表增删改的支撑，导致无法对 `conflicts_with` 字段进行增量 edit 操作。
- 建议：在参数说明表格中显式补齐 `--conflicts-with`、`--conflicts-append`、`--conflicts-remove` 三个参数及其语义。
- 置信度：高
- 优先级：中

### 2. 互斥图贪心独立集计算未显式指定无向邻接构图逻辑
- 位置：Line 80-81 与 Line 120-121
- 现象：Line 121 明确要求校验时按无向图处理，但 Line 80-81 描述算法时写为 `可执行 = ready 中不与 active/blocked task 冲突者`。若 Agent 落盘时仅在单向写了冲突（例如仅 `t002` 标了与 `t006` 冲突），按单向边读取算法会导致两者仍有可能被同时选入同批。
- 影响：算法描述若不够严密，代码实现时可能漏建反向边，导致单向互斥标注失效，引发并行冲突。
- 建议：在 Line 80-81 算法描述中补充“算法构图时需将 `conflicts_with` 扩展为无向对称邻接表（A 冲突 B 则视同 B 冲突 A）”。
- 置信度：高
- 优先级：中

### 3. `schedule_status` 的字段合法值与清理动作缺乏标准规范
- 位置：Line 42 与 Line 59
- 现象：方案定义了 `schedule_status: pending_clarification` 状态，并说明在澄清后经由 `edit` 清除。但未定义 `schedule_status` 是否有其他枚举值，也未说明 `task.py edit` 在修改依赖时是否会自动清除该状态，或需显式指定清除参数。
- 影响：可能导致 front matter 残留非预期的状态标记，影响后续 `next-batch` 的计算。
- 建议：明确 `schedule_status` 的完整合法枚举范围（目前仅 `pending_clarification`），并规范 `task.py edit` 更新调度元数据时对该字段的自动或手动清除行为。
- 置信度：中
- 优先级：低

## 改进建议

### 1. 「改动面」表格中漏记 `docs/decision_log.md`
- 位置：Line 140-149 与 Line 166
- 现象：Line 166 的待办事项中明确要求“落地后更新 `decision_log.md`”，但是在 Line 140-149 的改动面汇总表中遗漏了 `docs/decision_log.md`。
- 影响：改动面清单不完整，不符合仓库「修改代码/方案时同步更新相关文档」的约定。
- 建议：在改动面表格中补齐 `docs/decision_log.md` 及其修改内容说明。
- 置信度：高
- 优先级：建议

### 2. 补充 `start` 命令校验拒绝时的标准化错误输出格式
- 位置：Line 98-103
- 现象：方案规定了 `start` 的依赖与互斥双门禁，但未明确提示拒绝执行时的标准 CLI 错误信息样式。
- 影响：终端或 Agent 调用 `start` 被拒时缺乏一致的可解析报错提示。
- 建议：规范 `start` 拒绝时的输出文本结构（明确指出具体是被哪个前置 `depends_on` 阻断，或与哪个 `active/blocked` 任务冲突）。
- 置信度：中
- 优先级：建议

## 不确定项

### 1. 大规模 TID（4 位及以上数字）的未来兼容性
- 位置：Line 89-96
- 现象：Line 94 将 4 位以上数字（如 `t0015`）定性为非法并直接拒绝。这隐含了项目 TID 永远不超过 3 位数（`t999`）的前提。
- 影响：若项目后续任务总数突破 999，该规则将导致 4 位 TID 无法使用。
- 建议：确认当前项目 TID 规范是否严格限制为 3 位；若未来有扩展需求，建议优化解析规则（去除多余前导零后若有效数值在合法编号范围内则予以接受）。
- 置信度：中
- 优先级：建议
