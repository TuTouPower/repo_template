存在。全仓扫描并核查关键执行链后，发现 7 类问题：前 6 类有明确代码证据，第 7 类属于架构设计债。

1. 高：finish 用异常补偿代替完整事务。
   lifecycle.py:892 (repo/.repo_template/scripts/repo_task/lifecycle.py:892) 只在内存保存回滚快照，进程中断后依赖人工恢复。更严重：提交超时进入异常分支时，未检查 HEAD 是否前进便恢复文件与暂存区；普通非零返回分支却做了检查。故障注入确认：rollback_called=True, head_checked=False。
   根治：建立持久提交事务，统一处理成功、失败与结果未知；依据 Git 实际状态决定继续或恢复。
2. 高：schema 迁移允许违规状态成为同步结果。
   apply.py:88 (repo/.repo_template/scripts/template_sync/apply.py:88) 检出 specs_index 契约违规后仅打印提示，继续同步、推进状态。对应测试:2169 (factory_tests/test_repo_sync.py:2169) 甚至明确断言此时返回成功。错误推迟到后续 finish 才暴露。
   根治：迁移后严格验证目标 schema；无法无损迁移则拒绝完成事务，明确要求裁决存量数据。
3. 中：基线失效后返回“成功”，破坏检查语义。
   repo_state.py:121 (repo/.repo_template/scripts/repo_state.py:121) 在 baseline 无效时，changed-files 只返回未跟踪文件；added-lines 同样遗漏 tracked 改动，仍可能退出 0。只读复现：无效 baseline 得到空输出与成功退出。
   根治：基线不可解析直接失败；需要文件存在性检查时提供独立接口，保持每个命令语义确定。
4. 中：为测试环境保留生产降级。
   md_format.py:166 (repo/.repo_template/scripts/md_format.py:166) 明写“CI 无二进制时现有测试不红”：缺少格式化器或格式化失败仅返回 False；pending/findings/spikes 创建侧忽略结果，继续成功。
   根治：测试显式提供依赖或替身；生产创建流程统一失败语义，将格式化纳入落盘事务。
5. 中：运行期仍承担旧版本兼容。
   三处明确残留：
   - state.py:15 (repo/.repo_template/scripts/template_sync/state.py:15)：正常读取自动回退旧布局 state。
   - _id_scan.py:129 (repo/.repo_template/scripts/_id_scan.py:129)：正常编号扫描仍读取旧单文件总账正文。
   - check_review_status.py:99 (repo/.repo_template/scripts/check_review_status.py:99)：保留 --max-review-round 兼容参数，输出上限还可能与实际持久化判定上限不同。

   根治：旧数据经显式、可恢复迁移收敛到当前格式；删除运行期旧读取与废弃参数。一次性迁移本身应保留，运行期双轨应移除。
6. 中：前后端重复维护业务推导。
   plan.py:435 (repo/.repo_template/scripts/repo_task/plan.py:435) 已生成 stop_reason；board.js:726 (repo/.repo_template/scripts/repo_task/view_static/board.js:726) 却继续调用 JS chainStopInfo 作为 fallback，后者重新分析依赖图、汇合点与冲突。两套实现仍有漂移风险。
   根治：后端输出完整展示模型，前端只渲染；缺字段按接口错误处理，删除客户端重复算法。
7. 设计债：仓库身份通过模块全局变量隐式传播。
   repo_task/context.py:8 (repo/.repo_template/scripts/repo_task/context.py:8) 从源码位置推导仓库；template_sync/context.py:78 (repo/.repo_template/scripts/template_sync/context.py:78) 修改整组全局路径。两套上下文并存，已经需要 store.py:159 (repo/.repo_template/scripts/repo_task/store.py:159) 专门提供显式 root 接口防止写错来源仓。
   根治：入口构造不可变仓库上下文并显式传递，统一文件与 Git 操作目标。这是架构判断，本轮未复现写错仓库事故。

建议顺序：事务完整性 → schema 门禁 → 降级路径 → 兼容残留 → 前后端权威收敛 → 上下文统一。

全量测试：1021 passed，1 skipped，293.63 秒。测试通过包含上述部分既有降级语义，不能证明符合架构铁律。未修改代码，工作区保持干净。