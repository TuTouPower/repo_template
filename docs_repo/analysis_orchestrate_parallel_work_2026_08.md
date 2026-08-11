# orchestrateParallelWork-skill 借鉴分析

日期：2026-08-07（同日按双份外部评审修正）
对象仓库：https://github.com/wyizhou/orchestrateParallelWork-skill （本地副本 `~/github_repo/orchestrateParallelWork-skill`）
目的：评估该 skill 的机制，筛出可借鉴到本模板 task 工作流的部分。

## 对方定位

面向复杂任务的图编排 Agent Skill（Codex / Claude Code）。核心路线：**编译期图契约 + 强校验**。目标先编译成可审批的 Plan / Node / Edge / Task / Artifact 执行图，批准绑定内容 hash，再由 Coordinator 按 Skill 协议驱动执行，本地 Dashboard 实时展示，交付须经每个 Node 的 test/lint 门与不参与实现的事实型 Validator。

与本模板路线差异：本模板是「人工分析落盘 + 脚本机械校验 + 手动并发」，已明确退役自动调度（decision_log L35）。哲学不同，只取编译期契约思想，不取运行层调度。

## 关键分层：对方三类能力强度不同，不能混为一谈

对方机制分三层，强度递减。本文第一版曾统一写成「运行时硬门禁」，高估了其自动化与防绕过程度：

|层|内容|强度|
|------|------|------|
|A. 编译期硬校验|`compileBundle` 系列：批准 hash、parallel_conflict、validator 白名单、覆盖闭合、档位约束|端到端强制，计划不过编译即拒绝|
|B. 状态机库函数|`activateNode` / `assertNodeSubmission` / `assertNodeTransition` / `staleDescendants` / `invalidateArtifactDescendants` / `markReadyNodes` / `approvalMatches` 等|有完整测试，但**无生产调用方、未接入 CLI**；全仓唯一生产导入者 graphctl.mjs 只用 `compileBundle` / `loadBundle` / `validateRuntimeRegistries`|
|C. 规程约定|Node test/lint 提交门、实际调度与状态迁移、stale 传播执行、重试不覆盖历史、能力探测与串行降级|SKILL.md / references/*.md 要求 Agent 自觉遵守，代码无强制点|

CLI 唯一入口 `graphctl.mjs:12-18` 明文声明只读（"The tool is read-only. It never starts workers or changes plan state"），仅暴露 `validate` / `summary` / `hash` / `check-state` 四个子命令。

准确表述：**对方提供强编译校验、运行状态快照校验和一组经过测试的状态机辅助函数；完整执行仍依赖 Coordinator 按 Skill 协议正确调用并写控制面。** Node test/lint 门、stale 传播、能力降级等均属 B/C 层，不是已端到端硬化的自动调度能力。

## 对方核心机制（A 层，编译期硬校验）

代码集中于 `skills/orchestrate-parallel-work/scripts/graph-core.mjs`，schema 在 `assets/schemas/`。

### 执行图与批准 hash

- 五实体：Plan（13 态状态机、capacity、goal_contract）/ Node（work、integration、validation 三型）/ Edge（data 边绑 artifact 契约，control 边不绑）/ Task（与 Node 一一对应，含 owned_scopes、forbidden_scopes、boundary_dimensions、self_validation 门）/ Artifact（Catalog 计划契约 vs Registry 运行登记）。
- 批准 hash：`contractProjection` 剔除 status/summary/时间戳等运行时字段，对契约内容 `sha256(stableStringify())`（graph-core.mjs:52-69）。批准谓词在 `approvalMatches`（:474-481，被 `validateApproval` :405-419 调用）：status 须 approved，plan_id / plan_version / plan_hash 全等，approved_capacity ≥ effective_capacity。Graph、Task、Artifact、范围、外部影响、验证例外任一变化 → hash 变 → 旧批准失效 → 递增 plan_version 重批。

### 写入面冲突静态判定

- `scopesOverlap`（:96-100）：路径规范化后，目录前缀即视为重叠。
- 编译期检查（:365-372）：拓扑上互相不可达的 Node 对，owned_scopes 或 allowed_external_effects 有任意重叠 → `parallel_conflict`，整份计划拒绝编译。即「不可并行的两节点要么有依赖边串行化，要么计划被拒」。

### Validator 机制

- 角色强制（:268-275）：validator 角色 read-only、无 owned_scopes、禁止委派工具。
- 字段级白名单 brief（:422-448）：只允许恰 8 个字段；feature 只许 `{id, expected_behavior}`；额外字段 → `validator_bias` 编译错误。**注意：编译器只做字段名/类型校验，不识别允许字段字符串里的语义**——`expected_behavior` 仅查非空、`authoritative_input_refs` 仅查是数组，实现者总结仍可塞进这些字符串字段。禁止叙事污染主要仍依赖生成 brief 的流程纪律。
- 观测证据强制（:602-631）：每个声明的 check 必须有 observation；每 case 恰 7 字段齐全（case_id / partition / generated_input_or_fixture_ref / expected_fact / observed_fact / status / evidence_ref）；每 declared partition 至少 1 case 且总数 ≥ minimum_cases；进入 accepted 时所有 case 必须 passed、`coverage_gaps` 必须为空数组。
- 覆盖闭合（:375-394）：每个非验证 Task 的每个 feature / module / boundary_dimension 都被至少一个 validator 覆盖，否则编译失败。
- 独立性（:586-594）：validator 的 agent_instance_id 不能等于所验 artifact producer 的 agent_instance_id，注册表级校验。

### 其他 A 层机制

- 档位编译器（:164-199 `validateExecutionProfile`）：**只强制两种组合**，非「三档全在编译器强制」——lightweight 档强制 risk=low、非验证节点 ≤ 4、恰 1 验证节点、inline + combined；high-risk 强制 mode=assurance、validator ≥ 2、conformance 与 boundary 双 focus。standard 档无任何数值约束，assurance 在非 high-risk 下也不强制 validator 数（测试中有 6 节点 standard 档自由通过）。
- 不可变 artifact：重试建新版本、不覆盖已接受历史；`contract@version` 全局唯一；producer 四元组（node_id / attempt / agent_instance_id / 合同 producer node）与 node-run registry 逐字段匹配（血缘校验 :563-573）。
- Dashboard（dashboard-state.mjs / dashboard-server.mjs）：文件指纹签名轮询 + SSE revision 推送 + 终态浏览器 ack 关闭协议 + degraded 保留最后有效快照。属真实代码实现，但与本模板方向无关。

### B/C 层机制（非硬校验，列此防误读）

- 失效传播：`staleDescendants`（:647-659）将已接受输入变化的下游传递闭包全部置 stale，`invalidateArtifactDescendants`（:661-665）经 producer node 包装。函数有测试但未接入口，实际传播靠 Coordinator 调用。
- 平台适配：能力探测 + 安全降级（无委派/隔离能力时降级串行）。仅存在于规程文档（references/runtime-generic.md:5-9,45、runtime-claude-code.md、runtime-codex.md、SKILL.md:109），代码无强制点。与 A 层硬校验并列展示时须标注强度差异。

## 本模板现状对照

- 依赖/冲突：`depends_on` / `conflicts_with` 由 task-schedule 的 Agent 人工分析落盘。脚本机械校验（`repo_task/scheduling.py`）当前覆盖：自引用拒绝、引用不存在 task、引用 dropped task、depends_on 环检测、schedule_status 合法性、dep-ready 冲突阻塞规则、next-batch 互斥择优、stalled 停滞哨兵；另有 `cmd_edit` 侧依赖×冲突冗余门禁（lifecycle.py:267-321）。**调度死锁环校验曾在 7df2c3d 加入、546a59d 已移除**（dep-ready 收紧后等待环可证恒空，死代码删除），本文第一版「脚本校验…调度死锁环」表述已过时。冲突判定本身无机械化辅助。
- 验证链：task-work Step 2-4 红→绿→黑盒由实施 agent 自己执行；结果以自述字符串写入 `handoff.json`（`tests` / `blackbox` / `review` 仅要求非空字符串，`"tests": "随便写的"` 一样通过，monitoring.py:132-144,196）；`verify_integrate_ready`（monitoring.py:153-237）校验格式、attempt/execution_id identity、分支终态与执行 commit provenance，不校验结果真实性。
- review：subagent 只读 diff 静态判断。**叙事隔离已大部分落地**：render_review_prompts.py 只注入 spec 契约区（:218-220）+ 上下文区（:221）+ diff，不注入 implementer 收尾总结；share_prompt.txt:19 明确 task.md 是 claim 不是证据、一切以 diff 与代码/测试为准；share_prompt.txt:15 声明 reviewer 只读边界、不派 sub-sub-agent。契约区 drift 相对 diff_anchor 的变更以 unified diff 警告块附给 reviewer（render_review_prompts.py:132-170,254-258）。prompt 不注入 `{test_cmd}` / `{blackbox_verify}`，reviewer 不重跑测试。
- 合并后验证：integrate-chain 有 `awaiting_verification` 停顿（integration.py:738-749），但由同一 agent 人工执行，`--continue` 仅有 attempt-completed + handoff 门禁（:669-692），无测试结果机器门禁。单 task integrate 直接 merge + 删分支（:340-409），无此 transaction。
- 独立执行型 Validator 不存在：reviewer 不重跑测试、不生成边界样例、不提交结构化观测记录。

## 借鉴清单（原 1/2/3/5 经评审修正，重排优先级）

### 借鉴 1. post-merge 机器重跑（优先，但须先补两个前提）

目标：把 `awaiting_verification` 的口头外部验证变成脚本门禁，由脚本亲自启动命令并记录退出码，独立于实施者 worktree 环境；同时可接住 omni_media 暴露的 worktree 下 build 环境差异问题（analysis_omni_gate_gaps_2026_07.md 开放问题）。

前提一：`{test_cmd}` 当前不是机器可执行契约。docs/blueprint/testing.md:5-7 中 `{test_cmd}` 是自然语言占位、`{blackbox_verify}` 明示是方法论非单个命令。落地前须先定义机器接口（如配置化命令字段或 CLI 显式参数），不能直接解析 Markdown。

前提二（边界认知）：现有 integrate-chain 顺序是 merge 入主干 → index commit → ledger integrated → awaiting_verification（integration.py:603-627,738-749）。**验证失败时坏代码已进入主干**，机器门禁只能阻止 transaction finalize、分支删除与收尾宣布，不能阻止合入。若目标真是「通过后才进入主干」，须改为在临时集成 worktree/ref 先构造合并结果验证、通过后再推进默认分支——成本高一档，立项时须明确选哪种。

另：方案当前只覆盖 integrate-chain；单 task integrate 无 transaction，须一并决策。

### 借鉴 2. AC 稳定 ID + 证据映射（原 c，前置改造）

目标：覆盖闭合——防「实现了三条 AC 只测了两条」。

前提：当前行为 AC 只是 Markdown checkbox（`- [ ] {可独立验证的行为结果。}`，task_template/spec.md:27），无稳定 ID；task.md:64 说「按需引用 AC 编号」但编号语法从未定义，插入/调序即失效。须先定 AC-NNN 编号约定与 evidence 映射结构（如 `ac_evidence: {"AC-001": ["evidence/tests.log"]}`），脚本检查未知 / 重复 / 遗漏 ID，才谈得上机械覆盖检查。

### 借鉴 3. 写入面冲突的路径前缀机械判定（原 1，须改数据模型或降级）

原表述「按各 task 声明代码路径预计算候选冲突对，不改数据模型」自相矛盾：本模板无结构化路径字段（task.md front matter 仅 depends_on / conflicts_with 等，spec.md 范围是自然语言），脚本没有可机械消费的数据。

二选一：

1. 新增 `owned_scopes` 结构化字段（改数据模型），脚本做前缀重叠判定。须处理：task 创建时未读代码导致路径声明不准、glob 与目录前缀语义、测试/schema/配置/文档间接冲突、多 task 共改派生索引但非业务冲突的例外。
2. 不加字段，由 Agent 从 spec 推断路径、输出候选冲突对供裁剪——此时属 Agent 辅助而非脚本机械判定，强度降一档，不能声称「机械兜底」。

### 借鉴 4. handoff 证据结构化（原 a，定位降级为审计增强）

`tests` / `blackbox` 从自由字符串改为 `{cmd, exit_code, summary, evidence_path}`，evidence 文件存 task 分支；`verify_integrate_ready` 校验 exit_code=0 且 evidence 文件在分支 tip 存在非空。

定位必须写清：字段与 evidence 文件仍由实施者生成，实施者同样可写 `{"exit_code": 0}` 并伪造输出文件。**这是审计增强（可审计性、格式一致性、复查便利），不是真实性证明，不能称为「堵自述假绿」**。真实性只能由借鉴 1（脚本亲自重跑）或不同执行主体复验建立。

### 借鉴 5. Validator 输入隔离（原 2，大部分已落地，只补规则）

本模板已有独立静态 reviewer + 输入叙事隔离（见现状对照 review 行）。准确差距：**没有独立执行型 Validator**——reviewer 不重跑测试、不生成额外边界样例、不提交结构化观测记录。

因此本项不作为新机制立项，只做强化：review prompt 渲染规则补「不得注入实施测试结论/收尾判断」，维持现状的契约区 + 上下文区 + diff 输入边界。是否引入执行型 Validator 并入借鉴 1 统一决策。

### 借鉴 6. 上游失效的下游 stale 传播（原 5，暂缓）

当前状态机下该机制基本没有触发场景：rewind 只接受 active/blocked（done/dropped 已归档拒绝，lifecycle.py:540-553），下游 start 要求前置 done/dropped（integration.py:103-111）——可被 rewind 的上游不可能已有合法启动的下游。

真正需要 stale 传播的是 reopen / supersede / 已完成分支重写 / 契约变更使已完成下游失效，而当前模板没有这些生命周期操作。暂缓，等 reopen/supersede 语义设计后再做。

## 不借鉴的部分

- 自动 DAG 调度、容量计算（effective_capacity）：与本模板手动并发方向相反，已退役（decision_log L35）。
- Dashboard 的 SSE / revision / 关闭协议：`plan_manual_concurrency.md` 明列不做常驻/自动刷新。
- 批准 hash 绑定内容：不是「无场景」——本模板已知存在批准后契约 drift（decision_log L20），但基于薄工作流原则选择了 review 阶段 diff 警告（render_review_prompts.py:132-170,254-258）而非 hash 重批。除非出现未经批准的契约变更反复漏过 review，否则暂不引入。
- per-case 7 字段边界采样、partition 最小样例数、双 validator assurance 档：高风险系统规格，模板项目硬套会把每个 task 验证成本拉高一个量级。

## 一句话结论

对方最值得借鉴的是编译期契约完整性、稳定 AC/边界身份和由工具亲自执行的证据门禁；本模板已有静态独立 review 与叙事隔离，当前真正缺口是**机器执行复验**（借鉴 1）和**可核对的 AC—证据映射**（借鉴 2）。不要把对方 Skill 协议层规则误写成已端到端硬化的自动调度能力。
