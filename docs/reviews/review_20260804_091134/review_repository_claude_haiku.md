# Review：repository（task 并发/串行工作流）

## 当前模型判断依据

主会话模型：default_haiku[1m]。依据：会话环境明确标识；用户要求 my-review，授权 haiku 视角独立审阅。

## 模块 slug

repository

## 审阅范围

全量 tracked 文件（103 个），重点 task 并发（task-dispatch）与串行（task-run）两条工作流及其交互脚本、测试、模板、索引、规范。已读：task.py（2501 行）、12 个 skill、AGENTS.md（CLAUDE.md 为软链同文）、docs/blueprint/*、merge_guard.py、settings.json、_id_scan.py / pending.py / findings.py / spikes.py / check_review_status.py / render_review_prompts.py、task 模板、tasks_index.json、handoff、README、tests/repo_template/ 下 8 个测试文件。未运行破坏性命令；未修改源文件。

## 高优先级

### H1 `integrate --chain` 无链式拓扑校验，混用拓扑时可能误并/误删分支

- 位置：`scripts/repo_template/task.py` `_resolve_chain`（2042-2072 行）
- 现象：`--chain` 把所有「是链尾祖先且未合入 main」的 task 分支一律收集为链，未校验这些分支之间构成链式父子关系（每个应基于上一 task 分支）。`_local_task_branches()` 返回全部 `t[0-9]*_*` 本地分支，与链尾有祖先关系的都会被收集、合并、删除。
- 影响：串行与并行混用时（如链尾分支上被 fork 出并行分支并 finish，或并行扇出的已完成分支恰好是链尾祖先），这些分支会被 `--chain` 一并合并删除，超出「只合链尾」的声明语义。测试仅覆盖纯链式，未覆盖混用。
- 建议：收集链分支时校验「分支须为链尾的一阶祖先链」；非链式祖先分支应报错并提示人工处理，或在文档中明示「--chain 仅用于纯串行链，混用拓扑需先人工清理」。至少给 `_resolve_chain` 加混用场景的测试。
- 置信度：中。优先级：高。

### H2 `task-run` 对 depends_on 前置缺机械校验

- 位置：`.agents/skills/task-run/SKILL.md`「输入与固定队列」「链式拓扑」
- 现象：skill 仅以文档约束「depends_on 边必须排在被依赖者之后」「依赖被前置 task 满足的 backlog 可入队」，无脚本/门禁校验入队 task 的 `depends_on` 前置是否已在 main 完成或排在队列前。串行链式下 git 层面自动继承上一分支成果，但依赖语义（尤其是跨链 task）不校验。
- 影响：队列含 `t003 depends_on t001` 而 t001 未完成且不在队列时，串行照跑，t003 标记 done 但依赖未满足；`view` 的依赖图与串行执行结论可能分叉。
- 建议：task-run 启动前对每个入队 task 的 `depends_on` 前置做校验：前置须已在 main 为 done/dropped，或位于队列内且排在本 task 前；不满足则停止并呈报。
- 置信度：中。优先级：高。

### H3 merge_guard hook 对 task.py 路径与 `git -C` 形式 merge 实际不生效

- 位置：`.claude/hooks/merge_guard.py` `GIT_MERGE_RE`（35 行）；`task.py` `_git`（162-166 行）
- 现象：`GIT_MERGE_RE = (?:^|[\n;&|]\s*)git\s+merge(?![-])` 要求 `git` 后紧跟 `merge`。task.py 内部统一 `["git", "-C", root, "merge", ...]` 经 subprocess 调用，不经 Bash 工具 hook；agent 手写 `git -C repo merge` 也不匹配该正则。token 机制只拦「行首/分号后紧跟 `git merge`」形式。
- 影响：`task.py integrate` 的 merge 与带 `-C` 的手写 merge 全部绕过 token 授权。当前实际依赖 skill 层「会话级前置授权」兜底，hook 的存在会造成「已授权」的假象；绕过面与 hook 声明（「所有 merge 操作必须显式授权」）不符。
- 建议：扩展正则匹配 `git\s+(?:-[A-Za-z]\S*\s+)*merge`（含 `-C path` 形式），或删除 hook 改由 skill 层统一声明，并在 AGENTS.md 说明「task.py 发起的 merge 由 skill 会话授权覆盖，hook 仅拦手写 git merge」。当前两套机制职责不清。
- 置信度：高（机制事实）。优先级：高（授权语义模糊）。

## 中低优先级

### M1 `cmd_edit` 冲突反向边对「未合 main 的 done peer」报错而非跳过

- 位置：`task.py` `cmd_edit` 1666-1678 行
- 现象：反向边同步只跳过 main 中 `status=done` 的 peer（`if peer_task["status"] == "done": continue`）。未合 main 的 done peer 在 main 中仍显示 backlog，进入反向边逻辑后因 `task_effective_state` 探测到未合并分支覆盖而 `sys.exit("无法维护冲突反向边")`。
- 影响：peer 已 finish + cleanup 但未 integrate 的窗口期，其他 backlog task 无法声明与其冲突边；行为与「已合 main 的 done」不一致。测试只覆盖已合 main 场景（`test_edit_skips_reverse_edge_for_done_target_in_main`）。
- 建议：反向边同步对「未合 main 的 done peer」同样跳过（owner 单边声明即可，调度由 view 的 main_done_set 释放），或文档明示该限制。
- 置信度：中。优先级：中。

### M2 SLUG_RE 校验规则不一致

- 位置：`_id_scan.py` 169 行 `^[a-z0-9]+(_[a-z0-9]+)*$` vs `task.py` 85 行 `^[a-z][a-z0-9_]*$`
- 现象：pending/findings/spikes 的 slug 允许数字开头，task slug 不允许。同仓库内同名概念规则不一。
- 影响：低；仅约束风格一致性。
- 建议：统一为一个正则（建议 `^[a-z][a-z0-9_]*$`）。
- 置信度：高。优先级：低。

### M3 测试缺口：`--chain` 的边界组合未覆盖

- 位置：`tests/repo_template/test_task_start_flow.py`（链式测试 685-772 行）
- 现象：现有测试覆盖纯链式、链中 undone、链中登记 worktree，但未覆盖：并行分支混入链尾祖先、`integrate --continue --chain`、`integrate --keep-branch --chain`、同 tid 多分支作为链祖先。
- 影响：H1 所述风险无回归防护。
- 建议：补充上述组合测试。
- 置信度：高。优先级：低。

### M4 测试文件头部 patch 注释与模板仓漂移

- 位置：`tests/repo_template/test_task_start_flow.py` 1-7 行
- 现象：注释称「omni_media 本地补丁」，用 `re.sub(r"\{[^{}\n]*\}", "占位", text)` 兜底消解模板仓没有的占位符，模板仓同步后应移除。
- 影响：补丁掩盖模板占位符误用，可能让「新增占位符」不被门禁发现；且本模块名为 repo_template，patch 是外部仓库遗留，属模板仓内不应存在的内容。
- 建议：移除兜底补丁，改为在模板仓 spec 模板补齐对应字段，恢复严格占位符校验。
- 置信度：中。优先级：低。

## 改进建议

1. `_resolve_chain` 增加链式拓扑校验或文档明示混用禁区（H1）。
2. `task-run` 队列启动前校验 `depends_on` 前置（H2）。
3. 明确 merge 授权双层机制职责：扩展 hook 正则或删 hook 统一 skill 层授权（H3）。
4. `cmd_edit` 对未合 main 的 done peer 跳过反向边，与已合 main 行为对齐（M1）。
5. 统一 SLUG_RE（M2）。
6. 补 `--chain` 边界组合测试（M3）。

## 不确定项

- `integrate --chain` 对「同 tid 多分支且均为链尾祖先」的收集/删除行为：逻辑上安全（逐个 `git branch -d`，未完全合入者被 2157-2160 行校验阻止删除），但无测试覆盖，行为未证实。
- `cmd_edit` 对未合 main 的 done peer 报错，是设计限制还是遗漏（M1），需作者确认。
- `cmd_view` 的 `selected` 贪心选择（ready 中按 tid 升序互不冲突择优）在冲突边密集时可能次优（先选小 tid 挤掉能配更多对的大 tid），对补位吞吐的影响未评估。
- `task-run` 恢复策略中「已 done 分支作为下一个 --base」依赖未合并分支仍存在；若链尾已 `integrate --chain` 删分支，恢复路径依赖主干状态判断，未实测。

## 总体评价

状态机（backlog/active/blocked/done/dropped）、worker/coordinator 写域隔离、worktree 生命周期（start→finish→cleanup→integrate）、失败补偿（rollback_start、_close_task 单次写盘+回滚）设计严谨，测试覆盖充分（1600 行 start_flow 测试覆盖扇出、链式、冲突、rewind、view 图校验）。主要风险集中在「串行链式」拓扑的机械校验缺口（H1/H2）与 merge 授权双层机制的职责模糊（H3）。
