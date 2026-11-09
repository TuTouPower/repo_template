# decision_log

跨文档裁决总账。**只列被两份以上复盘诊断过的议题**，单文档独有意见不进表，避免噪音再生。

状态：`已落地` = 已进 AGENTS/skill/prompt/脚本；`开放` = 已裁决未实施；`已否决` = 明确不做；`已落地；被 Ln 取代` = 原方案曾落地，现行以 successor 为准。

核对记录：L1–L36 于 `2026-09-07T00:00:00+08:00` 文档审阅核对；L37 起为之后新增，未纳入该次核对。`2026-11-03T22:30:00+08:00` 仅修订 L6、L7、L10、L13、L15 的落点/状态，其余行未重新核对。`2026-11-04T05:36:34+08:00` 核对 L13、L14、L23、L36，追加宏观方案 D-01–D-14 对应的 L40–L52（D-10/L49 于 `d43deba` 移除，D-11 撤销），并更新改名落点。`2026-11-04T07:46:52+08:00` 追加 L53（撤销证据 commit、恢复一 task 一 commit）。被取代项在状态列标明 successor，不静默改写原议题。现行行为以 `repo/AGENTS.md`、`repo/.repo_template/docs/usage.md`、`repo/.repo_template/docs/architecture.md`、`repo/.repo_template/docs/recovery.md`、`repo/.repo_template/docs/sync.md` 和 `task.py --help` 为准。

文件名映射：commit `35d298b` 改名后，旧 `spec.md` = 现 `task.md`（需求规格，含契约区/验收标准）；旧 `task.md`（过程总账、实施笔记、处置表）先改为 `note.md`，本次 D-12 再改为 `progress.md`。裁决列与议题列保留改名前的历史称呼，落点列写现行路径。

出处列沿用复盘旧称：`feedback` = `workflow_reflection_1.md`；`retro_t041_t061` = `workflow_reflection_4.md`；`retro_t001_t007` = `workflow_reflection_3.md`；`incident_t071` = `workflow_reflection_2.md`；`analysis` = `workflow_reflection_5.md`。

改流程前读本表，不必重读全部复盘。新发现在此加行，不再新写复盘文档。

## 总账

| # | 议题 | 出处 | 裁决 | 落点 | 状态 |
| --- | --- | --- | --- | --- | --- |
| L1 | `tasks_index.json` 跨分支 merge 冲突 | feedback §4/E、retro_t041_t061 §1、analysis §12 | 状态权威下沉到各 task 的 `task.md` front matter；index 改派生缓存。~~gitignore~~ 未成为现行策略：index 由 `add`/`edit`/`rewind`/`purge`/`integrate` 重建并入库 | `scripts/task.py`、`usage.md`「工具链路径与写权」 | 已落地 |
| L2 | 审阅 finding 无界、信噪比低 | analysis §1、feedback §3/C、retro_t041_t061 §2、retro_t001_t007 痛点 3 | blocking 必须锚 AC **或**可观测行为缺陷；「建议加测」「覆盖可更广」降 minor | `share_prompt.txt`「blocking 硬阈值」 | 已落地 |
| L3 | reviewer 缺决策上下文致撤回率高 | analysis §1b、feedback 再评估节 | spec 契约区与上下文区正文直接注入 prompt，reviewer 不再自行读 spec | `render_review_prompts.py`、`share_prompt.txt` | 已落地 |
| L4 | branch ≠ worktree，未提交改动丢失 | incident_t071、retro_t041_t061 §1、analysis §11 | 默认每 task 独立 worktree（`../{repo}_{tid}`，`{repo}` = 消费仓目录名）+ `.env` 软链；禁止长期未提交。~~`--no-worktree`~~ 未实现 | `task.py start` / `preflight`、AGENTS「开发原则」 | 已落地 |
| L5 | 审阅对所有 task 无差别 | feedback §3/C、retro_t041_t061 §2、retro_t001_t007 痛点 3 | `review_level: full\|single` 进 front matter，判不准取 `full`。~~`none`~~ 未实现 | `task.py add --review-level`、`task-work`「独立 review」 | 已落地 |
| L6 | `plan.md` 退化为 spec 副本、实际弃用 | feedback §1+A、retro_t041_t061 §4、analysis §1b | **删除 plan.md**。根因是 plan 写在信息最少的时刻、用在信息最多的时刻；实施步骤改由执行期记入 `task.md` 实施笔记 | `repo/.repo_template/docs/task_template/` 无 plan.md；`task.md` 契约区/上下文区二分承接上下文；实施步骤记 `progress.md`「实施笔记」 | 已落地 |
| L7 | spec 写死技术选型 → 过时 → FAIL 循环 | retro_t001_t007 痛点 1、feedback §1 | AC 只写行为；技术选择进 `docs/blueprint/decisions.md`；spec 漂移类 finding 标 `spec_drift`，处置为改 spec，不计 FAIL | `repo/.repo_template/docs/task_template/task.md` 验收标准规范块、`repo/AGENTS.md`「开发原则」、`share_prompt.txt`「blocking 硬阈值」（spec 过时 → 改 spec，不计 FAIL；`spec_drift` 标签现行文件中已不存在） | 已落地 |
| L8 | `max_review_round` 语义混乱（出场次数 vs 闭环次数） | retro_t041_t061 §3、retro_t001_t007 痛点 5、analysis §1 | round = **回归轮次**：上轮 FAIL 修完重审才计数；新 finding 当轮处置不强制 N+1 | `check_review_status.py`、`architecture.md`「Review 与验证」 | 已落地 |
| L9 | 横向缺口只标遗留、不开 task | retro_t001_t007 根因节、feedback、analysis §13 | 遗留 finding 须映射 tid；reviewer 引用已有 follow-up 不重复报 | `task-from-pending` skill、`share_prompt.txt`「系统性缺口去重」 | 已落地 |
| L10 | AC 三处维护（spec / task.md / 处置表） | retro_t001_t007 痛点 7 | AC 唯一源 = spec 契约区；task.md 收尾引用不复制 | `repo/.repo_template/docs/task_template/task.md` 契约区（AC 唯一源）、`progress.md`「收尾报告」（引用 `task.md` 不复制） | 已落地 |
| L11 | TDD 顺序违规：改测试适配实现 | analysis §3 | 旧绿测只许保留或整体删除并说明；禁止就地改预期；reviewer 必须复核改测方向 | AGENTS「开发原则」、`task-work`「项目测试」、`test_prompt.txt` | 已落地 |
| L12 | blocked 无基础设施失败出口 | analysis §4 | 原方案加 `task.py block --reason infra` 与正式 `blocked` 状态。L36 删除正式 blocked 与 `block`/`resume`；阻塞时 task 保持 active，attempt `terminal=stopped` + `report=blocked`；禁止 agent 自定容错上限后绕过 | `architecture.md`「Attempt」、`task-run` | 已落地；被 L36 取代 |
| L13 | spec 脑补未核实的外部契约 | analysis §5 | spec 上下文区加「未知契约清单」，逐条标 `UNVERIFIED`；reviewer 只提示核实不 blocking | `repo/.repo_template/docs/task_template/task.md`「未知契约清单」、`share_prompt.txt`「blocking 硬阈值」 | 已落地；原 reviewer 非阻断策略已由现行分类门禁取代，见 task 模板与 share_prompt.txt |
| L14 | 「一 task 一 commit」与实战脱节 | feedback B、retro_t041_t061 §7 | **保留「一 task 一 commit」**：六份 review 主张放开多 commit，但与黑盒/审阅质量门禁矛盾（回头改要么 amend 破坏历史，要么新 commit 污染历史）。task 太大就回 `task-create` 拆，不在执行期切分 commit。commit 须可独立验证、有工程意义 | `AGENTS.md`「开发原则」+ `task-work`「完成」 | 已落地；被 L43 取代 |
| L15 | 大模型进 plan mode 不执行既定 plan | 11111#2（已并入本表） | 用户触发 `task-run` 即视为队列已批准；禁止 plan mode、禁止开跑前二次征求同意 | `task-run`「授权与队列」 | 已落地；无参数调用部分已修订：显式 tid 与 `/goal` 冻结队列仍不二次确认，无参数调用 `task-run` 须先列出 `task.py plan --serial --json` 清单并取得用户确认 |
| L16 | finding 无分类，噪音率靠人工事后统计 | retro_t001_t007 数据概览、review/glm §2.1 | finding 必填 `category`（bug/spec_drift/duplicate/nitpick/coverage_gap）；撤回率由脚本算。字段未进 `task.md` 处置表与 `share_prompt.txt`；现行噪音控制见 L2/L17 | `share_prompt.txt`「blocking 硬阈值」 | 开放 |
| L17 | 行数与圈复杂度阈值制造 nitpick | 本轮审阅（review/opus + 模板实读） | 两类命中默认不进 finding 表，只在结论段提示；仅当已产出可观测缺陷才按缺陷出 finding | `code_prompt.txt`「降级规则」 | 已落地 |
| L18 | `review.md` 模板与 prompt 两处定义报告格式 | 本轮审阅 | 格式唯一定义在 prompt；`review.md` 已删除（强于指针） | `.repo_template/docs/review_prompts/` | 已落地 |
| L19 | 模板占位符被原样留在 spec/task | retro_t001_t007（plan 复制 spec 的变体） | `preflight` 拒绝残留 `{...}` 占位符 | `task.py preflight` | 已落地 |
| L20 | 契约在执行期被悄悄改动 | 本轮审阅（配合 L3 注入） | ~~`start` 时锁契约区 hash~~（原方案从未实现）；改为 `render_review_prompts.py` 渲染时 diff 契约区相对 diff_anchor 的变更并附警告块给 reviewer，合法变更可见、静默变更藏不住 | `render_review_prompts.py` | 已落地 |
| L21 | 依赖关系散落 spec 文字，无法机器校验；后续批次重复依赖 Agent 分析 | feedback D、retro_t041_t061、review_task_batch_scheduling 多路审阅 | task front matter 增加 `depends_on`、`conflicts_with`；`task-schedule` 负责 Agent 分析落盘，`task.py plan` 负责后续纯脚本调度（自原 `next-batch --done` 演化）。~~`schedule_status`~~ 已由 L36 删除，不持久化 | `scripts/task.py`、`task-schedule`、task 模板、`architecture.md`「调度图与计划」 | 已落地；`schedule_status` 被 L36 删除 |
| L22 | subagent prompt 内联正文撑爆 context | analysis §2 | 派发只传文件路径，正文写 `.scratch/review_prompts/` | `task-work`「独立 review」 | 已落地 |
| L23 | 遗留待办与 bugs 无统一登记 | 11111#1、analysis §13 | 统一待办入口。原单文件 `docs/pending.md`（`bNNN`/`fNNN` 两节）已演化为 `docs/pending/{todo,parked}/pNNN_{slug}.md`；遗留 finding 的落点是 pending 条目，`task.md` 只留 `fix_ref` | `docs/pending/`、`pending.py`、`pending-record`、`progress.md`「Review 处置」 | 已落地 |
| L24 | skill 之间规则重叠，可能成新漂移源 | review/glm §2.4 | 状态机、门禁、目录权责只在 AGENTS 定义；用法/写权/skill 调用只在 `usage.md`；执行设计只在 `architecture.md`；skill 只写本阶段顺序、写域和停止条件 | 各 SKILL.md | 开放（2026-09-06 L36 已做一轮收敛，需持续盯） |
| L25 | 分级与粒度规则本身会成为博弈点 | review/k3 跨文档总评 | `review_level` 由创建期定并向用户说明理由。~~`none` 须在提交询问中列出~~：`none` 未实现，现行只有 `full`/`single` | `task-create` | 开放（`full`/`single` 分级博弈风险仍在） |
| L26 | 允许 agent 直接手改 index JSON | feedback E | **否决**。并发下会放大损坏；改为派生缓存后该诉求消失 | — | 已否决 |
| L27 | plan 拆三套永久模板（code/doc/style） | feedback A | **否决**。维护成本高于收益；plan 已整体删除 | — | 已否决 |
| L28 | 把 `/goal` hook、Electron ABI 写进通用模板 | analysis §2/§8 | **否决**。宿主与项目特有，不进模板；单会话 task 上限由使用者自行掌握 | — | 已否决 |
| L29 | 换 reviewer 模型解决审阅信噪比 | analysis §1 | **否决**。根因在 prompt + 上下文 + 阈值，不在模型 | — | 已否决 |
| L30 | 「不切分支，全在 main 上做」 | retro_t041_t061 §1 方案 C | **否决**为全局教条。它与 incident_t071 回答的不是同一问题：串行时 main 直做可行，并发或长未提交窗口必须 worktree。统一按 L4 处理 | — | 已否决 |
| L31 | 已验证的技术发现无处沉淀，spike 结论随报告归档失传 | 用户提出 | `docs/findings/dNNN_{slug}.md` 记已验证事实（一条目一文件）；spike 收尾抽结论，报告全文归档。~~单文件 `docs/findings.md`~~ 已演化为目录 | `docs/findings/`、`findings.py`、`task-work`「实施」 | 已落地 |
| L32 | `task-debt` 名字表达的是「捞技术债」，实际职责是「把总账条目转 task」 | 用户提出 | 改名 `task-from-pending`；入口 `docs/pending/todo/`，建完用 `pending.py archive --fix-ref {tid}` 归档 | `.repo_template/skills/task-from-pending/` | 已落地 |
| L33 | skill 被 agent 按语义自动触发，绕过用户批准 | 用户提出 | 禁止模型按语义自动触发：全部消费仓 skill 统一设置 `disable-model-invocation: true`，保留 `description` 供 `/` 菜单与显式调用使用 | 各 `SKILL.md` frontmatter、`test_contract_suite.py` | 已落地 |
| L34 | 调度控制面边沿触发：worker 全灭无人察觉、integrate 后忘补位 | omni_media 两起事故（2026-08）、用户提出、多路 diff 审阅 | 边沿触发 → 水位触发：`task.py reconcile` 幂等 diff 唯一动作来源 + 空闲许可；调度账本记 attempt；handoff.json 机器验证；失败分类 + cron 兜底 | `archive/plan_dispatch_control_plane.md` | 已落地；被 L35 取代 |
| L35 | 放弃 dispatch 自动并发，并发只留用户手动 | 用户决定（2026-08-06） | `task-dispatch` 退役；并发只允许用户手动多会话 `task-run`；`task.py view --serve` 只读看板；`start` 加 depends 硬拒与 conflicts 警告。dispatch 配套 API（`bind`/`escalate`/`observe`/`reconcile` 等）删除；executor 仅 `inline`。ledger、handoff.json、`verify_integrate_ready` 保留。~~merge_guard 保留~~ 已由 L36 删除 | `archive/plan_manual_concurrency.md`、`task.py view --serve`、`cmd_start` 调度门、`.repo_template/docs/architecture.md`、`usage.md` | 已落地；merge_guard 被 L36 取代 |
| L36 | 工作流特殊机制与 skill 过度详细 | 用户逐项裁决（2026-09-06）、`archive/plan_workflow_simplification.md` | 严格模板、测试/黑盒/review、attempt、goal、一 task 一 commit、pending 子代理和同步能力保留；删除 merge token hook 与正式 blocked 状态；attempt report=blocked 继续审计；review/verify limit 均保留并用独立 limits 命令只增；合并改为 Git `merge --no-ff --no-commit` 验证后提交；调度删除 schedule_status、反向冲突边维护和复杂 tie-break；15 个 skill 改为结果与门禁导向，移除重复剧本 | `repo/.repo_template/skills/`、`repo_task/integration.py` / `lifecycle.py` / `scheduling.py`、task 模板、Claude settings、同步脚本 | 已落地；默认状态栏启用方式被 L46 取代 |
| L37 | 消费仓首次追平大改版时同步脚本自举缺口 | md_kx 迁 Mac 实证（2026-09-14） | prep 只能刷新已存在且可跑的脚本：消费仓 `repo_sync.py` 若是旧布局版（旧 state 路径、旧硬同步清单、无 `prep`），调用即失败，无法自举。启动器显式加「先复制模板源 `repo_sync.py` 覆盖消费仓」步骤（任何子命令之前）；`init` 增加旧 state 迁移（带过 `user_prompts`/`last_synced_*`） | `repo/.repo_template/skills/repo-template-sync/SKILL.md`、`repo_sync.py` | 已落地；被 L39 取代 |
| L38 | `task-bug` 名字与职责不匹配（实为分析并记 pending），用户口述待办时非 bug 事项无需子代理耗时研究 | 用户提出 | `task-bug` 改名 `bug-to-pending`；`pending-record` 区分路由：bug 派子代理按 `bug-to-pending` 研究记录，非 bug 普通记录 pending | `.repo_template/skills/bug-to-pending/`、`pending-record`、`usage.md` | 已落地 |
| L39 | 初始化、组合、依赖合并、执行恢复与同步事务之间的契约断点 | `123.md` 宏观审阅、2026-10-24 实现核验与真实 Git 回归 | Git commit 干净导出；registry 单一角色/调用图，父流程读子正文；backlog 图收缩与来源归档共用持久事务；ledger claim 执行权；active review 单调升级；同步器从来源运行、固定 commit 快照与完整资产事务；项目显式验证策略；版本 manifest、共享 policy 与本机 state 分离；严格 done ledger import、附属生命周期门禁 | `export_template.py`、`skill_registry.py`、`repo_task/{graph,file_transaction,validation,finalization}.py`、`attempt claim`、`review-upgrade`、`ledger import`、`repo_sync.py` | 已落地 |
| L40 | 手工编排执行身份与 attempt 收尾 | 宏观方案 D-01，用户批准 | 主路径使用 run-begin / run-close，默认解析 attempt 与 execution_id，保留恢复原语；claim owner 校验不变 | `repo_task/{control,attempts}.py`、`task-run` | 已落地 |
| L41 | finish 与 Git 提交分离产生中间态 | 宏观方案 D-02，用户批准 | finish --message-file 在前置门禁通过后原子写 done、回执与实现 commit，失败恢复自身写入 | `repo_task/lifecycle.py`、`test_finish_atomic.py` | 已落地 |
| L42 | 验证摘要与执行回执重复维护 | 宏观方案 D-03，用户批准 | 验证摘要只写 progress.md，finish 机器生成 handoff.json，不手写回执 | `repo_task/monitoring.py`、`task-work` | 已落地 |
| L43 | 执行分支归档与 amend 例外 | 宏观方案 D-04，用户批准；修订 L14 | 归档由 integrate 的 merge commit 完成；执行历史为一个实现 commit 加零到多个仅含 review 过程文件的证据 commit，禁止改写实现历史 | `repo_task/{integration,monitoring}.py`、`recovery.md` | 已落地；证据 commit 部分被 L53 取代 |
| L44 | 阻塞传播到无关队列成员 | 宏观方案 D-05，用户批准 E1/E2 | 按依赖与冲突关系分链，无关成员从主干起步；阻塞只停止所在链，无可继续成员才判 STOPPED | `repo_task/{plan,goal}.py`、`task-run` | 已落地 |
| L45 | 消费仓携带工厂集成测试 | 宏观方案 D-06，用户批准 | 集成测试移到 factory_tests，不随 repo 分发；消费默认仅运行 contract | `factory_tests/`、`testing.md` | 已落地 |
| L46 | 默认状态栏 hook 缺少渲染端 | 宏观方案 D-07，用户批准；修订 L36 | 默认不启用、不分发 settings.json，状态行改由 run-begin 写入会话工作目录；保留消费仓自有设置 | `template_sync/hooks.py`、`usage.md` | 已落地 |
| L47 | 看板 mock demo 与现行实现重复 | 宏观方案 D-08，用户批准 | 删除 demo 源码与 zip，保留 Git 历史；设计计划移入 archive 并注明现行实现 | `docs_repo/archive/plan_board_v2_chain_panel_demo.md` | 已落地 |
| L48 | 工厂历史 basetemp 占用工作区 | 宏观方案 D-09，用户批准 | 历史测试残留清理；全量 basetemp 使用系统临时目录，缓存与运行产物不提交 | 工厂 `AGENTS.md` | 已落地；当前开发环境仍可使用忽略的 .scratch |
| L50 | note.md 文件名与状态、进展职责不符 | 宏观方案 D-12，用户批准 | 当前过程文件统一 progress.md，标题为 Task 进展；同步事务一次迁移活跃与归档旧布局，两名并存拒绝，失败回滚 | `repo_task/context.py`、`template_sync/workflow_migration.py` | 已落地 |
| L51 | AC 打勾未进入独立 review 机器门禁 | 宏观方案 D-13，用户批准 | reviewer 用 TICK-VALID / TICK-VOID 逐条复验；FAIL 后需改交付内容时实施方撤回全部打勾，修复重验后重新勾选 | review prompts、`repo_task/review.py`、task 模板 | 已落地 |
| L52 | 工厂全量串行验证耗时长 | 宏观方案 D-14，用户批准 | 测试替身隔离到被测模块，删除 xdist 禁令；工厂全量用 -n auto，消费默认门禁不变 | pytest.ini、测试替身、工厂 `AGENTS.md` | 已落地；本机 2 worker 实测 7 分 17 秒，未达原两分钟估算，见方案实施记录 |
| L53 | 撤销证据 commit，恢复一 task 一 commit | review_20261104 D01、用户裁定 | 撤销 L43 的证据 commit 部分（D2），恢复一 task 一 commit，review 失效走 amend 并以 `handoff.review_scope` 约束 | `repo_task/{lifecycle,monitoring,recovery}.py`、`recovery.md`、`architecture.md` | 已落地 |
| L54 | 小 pending 走完整 task 过重；worktree 缺 gitignore 本地文件导致容器验证难跑 | 用户裁定（2026-11-05） | 不做 in-place / main 直跑 task（维持 L4、L30）：worktree 经消费仓 `.worktreeinclude` 软链被忽略的本地文件与目录，仅目录规则的链接追加 `info/exclude` 托管块；小而低风险 pending 经 `pending-fix` 在 main 直修，一条一个 fix commit，超出准入转 task | `repo_task/worktrees.py`、`repo/.worktreeinclude`、`usage.md`、`pending-fix` skill、`AGENTS.md` | 已落地 |
| L55 | 模板同步与消费项目业务测试的运行环境、耗时耦合；策略校验存在跳过和缓存盲区 | 用户裁定（2026-11-06）、SYNC 范围与闭环审阅；部分取代 M-01 | SYNC 只执行模板 contract，项目 doctor/test/blackbox 仍由 task-work 与 reviewer 按项目策略执行；apply 始终校验 testing.md，--skip-tests 仅跳过 contract；accept 始终重读策略，契约测试按原指纹条件复跑；删除失去生产调用方的业务执行器。保留单块 fail-closed，消费仓自有 testing.md 升级前人工迁移 | `template_sync/{apply,transaction,cli}.py`、`repo_task/validation.py`、`sync.md`、`recovery.md` | 已落地 |
| L56 | 代码基座与主干规格来源混淆导致已核实规格阻断（Issue #90） | Issue #90、测试性能方案实施 | 移除 `load_task_backlog_baseline` 的 `diff_anchor` 祖先猜测，主干 backlog 为规格与初始调度唯一权威；启动前对主干目标目录无条件严格脏检查（含 rename 两端与未跟踪）；worktree 启动后从锁定的来源 SHA 完整物化规格，并将 `spec_source_sha` 作为不可变字段贯穿生命周期、验证、审阅契约基线与回执 | `repo_task/{store,integration,monitoring,lifecycle,render_review_prompts}.py` | 已落地 |
| L57 | 环境软链在末尾斜杠规则下未被忽略且进入交付指纹与阻断 finish（Issue #91） | Issue #91、测试性能方案实施 | 显式环境声明 + 精准 Git 原生忽略规则（`info/exclude` 写入不带斜杠规则）；`_review_worktree` 严格区分 cached 与 others，对未忽略环境软链提供明确诊断与指引，不静默算入交付指纹；finish 门禁与指纹使用一致原生忽略边界，真实交付软链（120000）严格保留 | `repo_task/{worktrees,monitoring,lifecycle}.py` | 已落地 |
| L58 | 测试套件运行开销大、重复 Git 子进程与 Arrange 冗余 | 测试性能优化方案 P1–P4 落地 | Arrange 阶段以显式 RepoContext 领域 API 替代非被测 CLI；`repo_task` 引入单次 `ls-tree` 与非交互 `git cat-file --batch` 批量读取与 commit 快照；`factory_tests` 引入受控 Git 环境、Profile 种子与 session 级别的高速独立复制；全量用例数增至 1098，耗时从 255.77s 降至 179.29s（跌破 3 分钟） | `repo_task/{git_ops,store,integration}.py`、`factory_tests/{git_repo_support,conftest}.py` | 已落地 |
| L59 | 实体生命周期目录搬迁导致路径变换与 Git 自动合并重命名冲突（Issue #92） | Issue #92、实体稳定存储方案实施 | 实体终生原位流转不进 Archive：Task（`docs/tasks/`）、Pending（`docs/pending/`）、Spike（`docs/spikes/`）；Archive 仅保留冻结冷文稿（`reviews/` 与 `specs/`）；退役 `archive-handoff` 与 `archive-spike`；跨任务核心资产（progress, task, handoff, review_*）基于完整 `(mode, oid)` 实施所有权严格拦截；依赖实现按 `handoff.json` Blob OID 与非 merge commit 血缘纯拓扑定位；提供 `repo_sync.py migrate entity-storage` 一次性迁移与同步阻断 | `repo_task/{store,lifecycle,integration,monitoring,graph}.py`、`pending.py`、`spikes.py`、`template_sync/entity_storage_migration.py` | 已落地 |
| L60 | 交付隐私门禁粒度与二进制策略分叉 | 2026-11-09 合并审阅、用户授权修复 | 统一 snapshot 新增文本行比较；完整 blob 先解码，所有交付入口共享；全量审计独立 CLI；不按后缀豁免文本 | `repo_task/privacy.py`、hook、lifecycle/integration | 已实现，未运行测试 |
| L61 | finish 半写入不可恢复、外部提交被误认领 | 同上 | journal v2 记录逐步 before/after 与预期 index tree；提交认领核阶段、父提交、执行身份；清理前复验实际 tree；unwind 清理已认领事务 | `repo_task/{commit_txn,lifecycle,recovery,monitoring}.py` | 已实现，未运行测试 |
| L62 | 迁移检测漏项、目标身份未验与运行期旧格式耦合 | 同上；补记 D2/D3 语义 | 显式迁移转换源数据，正常模型验收后写 entity_schema；仅当前协议来源参与取号，合并仍验协议与编号。D2：迁移不认证历史血缘、保留 handoff，使用时核验；D3：逐项事务，前项成功保留，失败仅回滚当前项 | `template_sync/{migrate,legacy_ledgers,entity_storage_migration}.py`、`_id_scan.py` | 已实现，未运行测试 |
| L63 | 相邻 CLI/格式化器仍隐式选择仓库 | 同上；延续 A15 | pending/spikes/findings/Markdown 库调用显式传 RepoContext，入口支持 --root；删除 hook 改写全局身份 | 对应 scripts 与 pre-commit | 已实现，未运行测试 |
| L64 | 状态行写入缺少宿主身份与渲染闭环 | 同上；修订 L46 的无条件写入部分 | 默认不写绑定；显式 --session-id 才启用，session 与 owner 分开；提供 render/resolve，消费方负责宿主配置，不修改 settings | `repo_task/{control,statusline,cli}.py`、`track_worktree.py` | 已实现，宿主未联调 |
| L65 | 依赖错误压平、handoff 校验重复与原始字节哈希 | 同上 | 共享 handoff 契约，保留查询失败/坏数据/歧义原因；核心资产按 Git clean/EOL 语义比较 | `repo_task/{handoff,integration,monitoring}.py` | 已实现，未运行测试 |
| L66 | 消费仓备份、大文件等不入库用户文件无落点；`data/` 是项目运行目录且可被清理 | 用户裁定（2026-11-10） | 新增仓库根 `local/`：gitignore、只属于用户；`repo_cleanup` 任何类别不进入不删除；worktree `.env` 扫描不进入，需要时经 `.worktreeinclude` 显式链接；隐私超限提示改指 `local/`，不再指向 `docs/archive/` | `repo/{.gitignore,AGENTS.md,.worktreeinclude}`、`repo_cleanup.py`、`repo_task/{worktrees,privacy}.py`、`usage.md`、`repo-cleanup` skill | 已实现，未运行测试 |
| L67 | `task-from-pending` 只表达来源，未表达「转为 task」的目标 | 用户提出（2026-11-10） | 更名 `pending-to-task`，同步 canonical skill、registry、命令薄层、宿主软链与使用文档；历史记录保留原名 | `.repo_template/skills/pending-to-task/`、`registry.json`、`.opencode/commands/`、`.agents/skills/`、`.claude/skills/` | 已实现 |
| L68 | `repo-cleanup` 点名清 scratch 会删除未结束同步/图事务；在用草稿只靠 agent 判断 | 用户裁定（2026-11-10） | scratch 进入默认类别；带 scratch 时非阻塞持控制面锁，被占用立即失败；未结束或损坏事务跳过 scratch、其它类别照常清；主人限未关闭 pending 引用与未发布模板 issue 报告，最后修改满 3 天也清，无主照常清；issue 本地报告记录发布地址。方案见 `plans/plan_scratch_cleanup.md` | `repo_cleanup.py`、`repo_task/{file_transaction,locks}.py`、`pending.py`、`repo-cleanup` / `template-issue-report` / `issue-handler` skill | 已批准，未实施；3 天期限是否适用于事务待确认 |


## 未闭环

| 议题 | 卡在哪 |
| --- | --- |
| 统一 task 服务（跨仓库/跨会话的状态服务） | 11111#3 提出，等用户决定。L1 已消除并发写冲突，该诉求优先级下降 |
| 分级效果复测 | analysis 的 PASS 率与遗留数来自单一项目（Electron + 弱测试基础设施）样本。L2/L5 落地后应在非 Electron 项目复测，确认数值而非仅方向 |
| L16 finding `category` | 已裁决未进模板与脚本；噪音控制目前靠 L2/L17 |
| L24/L25 | L39 已落地 registry/权威分工、执行期升 full 与指纹失效；分级效果仍需跨项目复测 |

## 123.md 核验与处理

核验时间：2026-10-24T01:03:18+08:00。报告中的契约缺口与复现故障分开判断，建议列表不全部等同于 bug。原报告保留，不覆盖审阅结论。

| 编号 | 核验 | 落地处理 |
| --- | --- | --- |
| H-01 | 存在；rsync 复制忽略文件 | export_template 只导出指定 commit，拒绝 tracked secret/本机状态，校验软链；真实产品导出后跑 contract |
| H-02 | 父子调用契约冲突存在；未证明三宿主均实际失败 | 父流程读 canonical 正文；registry 定义 public/internal 与调用边，隐藏内部 OpenCode 命令；保留显式授权门禁，补 OpenCode 标准 metadata |
| H-03 | 存在；来源依赖被删除 | merge-backlog 原子收缩图并归档来源；继承前置、下游改指、去重、环/所有权检查；start 同时拒绝滞后 base 图 |
| H-04 | 存在；原 identity 可被多会话接续 | ledger 内原子 claim owner_id，接管比较原 owner 并要求确认已停止，preflight 核对 owner |
| H-05 | 存在；自举/prep 不属于 apply 基线 | 删除覆盖自身与 prep；来源脚本直接运行，正式内容取 Git archive 快照，before-image 持久日志覆盖资产、schema、门禁与 state；备份不消费，失败不吞错 |
| H-06 | 存在；工厂根/URL 示例不可执行 | 先统一 product/checkout，来源 runner 显式 consumer/source；产物、工厂与 file URL 均有真实 Git 测试 |
| M-01 | 存在；默认 contract 可被当作业务充分验证 | 显式 verification_policy/notes；start、preflight、finish 检查策略；同步事务运行 contract 和项目 doctor/test/blackbox，失败完整恢复。同步执行项目命令部分已被 L55 取代，其余策略校验要求保留 |
| M-02 | 存在；active 无合法升级入口 | review-upgrade 仅 single→full；review_level 纳入指纹，旧 PASS 失效，禁止降级 |
| M-03 | 单机边界属取舍；恢复缺口存在 | manifest/policy 可共享，state/runtime 留本机；done Git/handoff 严格 import；active 留执行 identity，缺 ledger 时 fail-closed；补迁机、备份、离线、固定版本与 revert runbook |
| M-04 | 存在；tid 序可能违反依赖，COMPLETE 含未 merge | 默认队列复用依赖拓扑，串行不添加冲突反向边；输出 execution_complete、integrated、awaiting_merge |
| M-05 | 存在；回滚、archive 和调用图实际漂移 | 机械恢复以持久日志/代码为准；archive 窄例外明确；registry 生成入口/调用表，CLI/reviewer 接口分层 |
| M-06 | 存在；转办/修复混淆，spec/spike 收尾缺门禁 | pending resolution 区分 transferred/resolved/obsolete/duplicate；finish 检查 spec 索引或显式豁免，正式 spike 的 report/finding/决定/后续由 finish 和 archive 共用验证 |
| M-07 | 支持边界缺失 | 补充宿主、系统与工具版本要求 |
| M-08 | 采用成本属主观取舍；内部入口遗漏等文档缺口存在 | 12 个公开入口、3 个内部正文流程；README 最短成功路径、初始化完成判据、进阶恢复说明；保持 merge 单独授权 |

Claude 的禁用字段与 OpenCode 的 metadata 语义已对照官方文档核验：[Claude skills](https://code.claude.com/docs/en/skills)、[OpenCode skills](https://opencode.ai/docs/skills/)。

旧 prep 与内存 rollback-stack 测试整体删除，因被测路径已删除；原“整个 task.md 不影响指纹”测试整体替换为“正文排除、review_level 绑定、损坏元数据 fail-closed”，因为升级必须使旧 PASS 失效；保留既有断言，为持久事务、跨调用恢复与失败回滚新增行为测试。原有工作流 fixture 显式声明测试策略与 spec 豁免，不继承模板默认，也不通过修改旧断言适配输出。

本轮工厂全量验证：`pytest repo/.repo_template/tests -q`（临时目录位于工厂 `.scratch/`，设置 Git 搜索上界防止非仓库测试误认父仓），553 passed，无跳过；另通过真实产品导出后的消费 contract、registry 生成一致性、Markdown 格式与 diff 检查。

归档引用约束补充裁决：用户明确允许直接在 main 完成而没有 task ID。所有 pending archive 均强制非空 --reason，--fix-ref 仅为可选引用且不校验 task ID；默认 resolved，转办显式 transferred。备注须说明结果与验证依据，存在引用时可填 main、commit、PR、pending 或 task，不为通过归档伪造 tid。原“默认 transferred 需 tid、duplicate 需引用”约束取消。

归档备注契约补充验证：工厂全量 562 passed，无跳过；覆盖无引用、main/commit/PR 引用、所有 resolution 的空备注拒绝，以及缺少备注时零迁移。

## 后续缺陷核验与 Python 3.9

| 报告项 | 核验与修复 |
| --- | --- |
| finalization d1/s1 与代码块假阳性 | 属实。复用 fenced Markdown 解析，识别至少三位规范 ID 与显式 docs 路径；排除示例代码和非引用内联表达式。 |
| spike 决定字段空格/冒号 | 属实。统一容忍中英文冒号、水平空白及 Markdown 列表符；真实引用缺失仍阻断。 |
| agents-file 逆向覆盖原则 | 现行前序 \_apply_sync_write 已强制更新 current，不能据该单行认定“旧原则覆盖”；回归确认模板原则获保留。为消除顺序依赖，改为直接从固定模板快照缓存原则，再与候选语义结果合成。 |
| 历史 task 目录连带阻断 | 属实。仅强校验当前 tid 的闭环；不遍历其他 tid 目录，历史索引异常仅告警。 |
| 同步验证父子进程锁死锁 | 风险属实；原超时可终止父等待，但不能作为正确互斥协议。锁支持同线程重入；验证子进程对同一 Git common dir 的控制面写入立即失败并回滚；隔离仓库测试正常运行。 |
| graph.split 跨平台路径 | 当前 store 输出通常是 POSIX 路径，并非已复现的 Windows 故障；仍改为 Path.name 与 as_posix，移除手工路径切分。原生 Windows 全流程仍未认证。 |
| execution_context 只判 ledger 非空 | 属实。锁内校验 tid/attempt/execution_id 与最新 ledger 一致；旧 ledger、坏 receipt 及分叉身份均 fail-closed，recovery 输出 runtime_mismatch。 |
| 共享锁文件写后加锁 | POSIX sentinel 内容不参与互斥，不能据此认定互斥已失效；初始化顺序仍需改善。POSIX 在 OS 锁内初始化；Windows 从完整私有 inode 原子发布一字节锁文件；ledger 复用同一 helper。 |
| Python 3.9 | 按用户要求作为最低版本。移除 tarfile.extractall(filter=...) 依赖，受控解包保留路径/软链/类型校验；联合注解延迟求值，替换较新 Path.write_text newline 参数；md_kx 采用独立工具环境。 |

后续缺陷验证：Python 3.9.6 全量 599 passed，Python 3.14.7 全量 599 passed，均无跳过；Python 3.9 专项 100 passed。原生 Windows 尚未实机运行。

## #85：specs_index 严格 schema（取代「历史索引异常仅告警」）

上表「历史 task 目录连带阻断」行的「历史索引异常仅告警」口径已被 #85 取代；原条目不改写、保留历史记录。现行行为：`docs/specs_index.md` 由 `repo_task/specs_index.py` 单点定义严格 schema（全文一张表、固定 3 列、slug `^[a-z][a-z0-9_]*$`、task 清单为 `tNNN`/`baseline` 且不重复、日期 `YYYY-MM-DD`）；finish 校验整张表，违规一次全部列出（带行号）并使收尾失败，不再有「历史 specs_index 条目需维护」WARN 静默降级。`template_sync/specs_index_migration.py` 在同步时精确移除旧 supersedes 说明句、收敛无数据的 4 列 supersedes 布局，status/plan/apply 报告剩余违规清单。消费仓历史数据（说明性表格、额外列、kebab-case slug、含中文描述的 task 清单）属漂移，须人工收敛，不兼容。落点：`repo/.repo_template/scripts/repo_task/specs_index.py`、`repo_task/finalization.py`、`template_sync/specs_index_migration.py`、`.repo_template/docs/usage.md`。

## #90、#91 与测试性能重构（L56、L57、L58）

落地点与验证记录（2026-11-08）：
1. **Issue #90 固定规格快照与主干 backlog 权威**：彻底解耦代码基座 `diff_anchor` 与规格快照权威；`load_task_backlog_baseline(tid)` 仅从主干读取 backlog 记录并锁定 SHA；启动前严格无条件检查主仓目标 task 目录脏改动（含 rename 两端与未跟踪）；worktree 启动后从锁定的 `spec_source_sha` 完整物化文件；`spec_source_sha` 作为不可变字段贯穿 `progress.md`、`handoff.json`、start ledger 事件、`verify_integrate_ready` 与审阅提示词渲染。
2. **Issue #91 显式环境软链与交付物边界**：`_review_worktree()` 严格区分 cached 与 others；利用 Git 原生 `check-ignore --no-index` 精准识别因尾斜杠未被忽略的环境软链并 fail-closed 报错给出修复指引；`link_local_files()` 与 `_ensure_links_ignored()` 强化在 `info/exclude` 写入精准规则；对齐 finish 门禁与未暂存检查；保留真实代码软链（`120000`）交付语义。
3. **测试性能优化（P1–P4）**：
   - P1：Arrange 瘦身，以显式 `RepoContext` 的领域 API 替代非被测 CLI 调用；
   - P2：实现 `git_ops.py` 的非交互式 `batch_cat_file` 协议通信与 `store.py` 的 commit 快照缓存，`_load_tasks_at_commit` 等高频读取由逐文件 spawn Git show 改为单次批量读取；
   - P3：`factory_tests/git_repo_support.py` 提供受控 Git 环境与固定 Profile 种子（工作流 3 任务、2 任务、同步源仓），通过 session fixture 高速复制提供隔离用例私有仓；
   - P4：全量调度对比验证，普通复制与 `--dist=worksteal` 稳定进入 3 分钟内。
4. **全量门禁实测数据**：
   - 基线：1,055 用例，耗时 255.77s；
   - 正确性修复后（#90 + #91）：1,069 用例，耗时 210.91s；
   - 性能优化完成（P1–P4）：1,098 用例（1097 passed, 1 skipped），耗时 179.29s（02:59）。全量测试通过。

## #92 与实体稳定存储重构（L59）

落地点与验证记录（2026-11-08）：
1. **实体规范路径终生固定**：
   - Task 终生在 `docs/tasks/{tid}_{slug}/` 原位表达 `backlog / active / done / dropped`；`finish` 与 `drop` 原位流转，彻底删除 `_stage_archive_moves`、`_archive_move_deviations` 与合并后的任务目录搬迁。
   - Pending 终生在 `docs/pending/{pid}_{slug}.md` 扁平目录原位表达 `todo / parked / closed`；删除 `archive` 与 `rename` 命令；取消自动 `git add`，引入文件内容摘要防御并发修改。
   - Spike 终生在 `docs/spikes/{sid}_{slug}/` 原位表达 `active / completed / superseded`；退役 `archive-spike`；严格校验 findings 关联并拦截循环替代链。
2. **归档边界纯净化**：
   - `docs/archive/` 仅保留 `reviews/` 与 `specs/` 冻结冷文稿；`repo_hygiene.py archive-review` 为唯一合法冷文稿归档命令。
   - 退役 `archive-handoff` 命令；交接快照单文件维护于固定 `docs/handoff.md`，历史由 Git 记录。
   - 本机操作审计日志迁移至 `docs/runtime/tasks_audit.log`，不入库，`repo_cleanup.py` 硬保护。
3. **共享核心资产所有权网**：
   - `monitoring.validate_task_core_asset_changes` 基于未过滤完整 Git 条目 `(mode, oid)`，在 finish 预检、集成就绪、链预检与 staged 两层门禁全链路拦截对非 owner 核心资产（progress, task, handoff, review_*）的新增、删除、篡改、模式变更及非法模式（120000/160000）。
4. **依赖实现来源统一查验**：
   - 彻底脱钩历史文件路径，纯粹基于目标 `handoff.json` Blob OID 与唯一非 merge 实现 commit 血缘拓扑验证。
5. **一次性迁移与同步阻断**：
   - `repo_sync.py migrate entity-storage [--write]` 提供原子文件事务保护与完整前置预检（零兜底、全阻断）；日常 `template_sync apply` 严格阻断旧布局，杜绝旧结构重生。
6. **全量验收实测数据**：
   - 契约测试：367 passed（13.52s）；
   - 工厂全量：1,175 用例（1174 passed, 1 skipped），耗时 203.27s。零回归，全量通过。


