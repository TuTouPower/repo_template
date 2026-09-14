# decision_log

跨文档裁决总账。**只列被两份以上复盘诊断过的议题**，单文档独有意见不进表，避免噪音再生。

状态：`已落地` = 已进 AGENTS/skill/prompt/脚本；`开放` = 已裁决未实施；`已否决` = 明确不做；`已落地；被 Ln 取代` = 原方案曾落地，现行以 successor 为准。

本表状态以 `2026-09-07T00:00:00+08:00` 文档审阅核对为准。被取代项在状态列标明 successor，不静默改写原议题。现行行为以 `repo/AGENTS.md`、`repo/.repo_template/docs/usage.md`、`repo/.repo_template/docs/architecture.md` 和 `task.py --help` 为准。

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
| L6 | `plan.md` 退化为 spec 副本、实际弃用 | feedback §1+A、retro_t041_t061 §4、analysis §1b | **删除 plan.md**。根因是 plan 写在信息最少的时刻、用在信息最多的时刻；实施步骤改由执行期记入 `task.md` 实施笔记 | 模板删 plan.md；spec 二分承接上下文 | 已落地 |
| L7 | spec 写死技术选型 → 过时 → FAIL 循环 | retro_t001_t007 痛点 1、feedback §1 | AC 只写行为；技术选择进 `docs/blueprint/decisions.md`；spec 漂移类 finding 标 `spec_drift`，处置为改 spec，不计 FAIL | `spec.md` 模板、`share_prompt.txt` | 已落地 |
| L8 | `max_review_round` 语义混乱（出场次数 vs 闭环次数） | retro_t041_t061 §3、retro_t001_t007 痛点 5、analysis §1 | round = **回归轮次**：上轮 FAIL 修完重审才计数；新 finding 当轮处置不强制 N+1 | `check_review_status.py`、`architecture.md`「Review 与验证」 | 已落地 |
| L9 | 横向缺口只标遗留、不开 task | retro_t001_t007 根因节、feedback、analysis §13 | 遗留 finding 须映射 tid；reviewer 引用已有 follow-up 不重复报 | `task-from-pending` skill、`share_prompt.txt`「系统性缺口去重」 | 已落地 |
| L10 | AC 三处维护（spec / task.md / 处置表） | retro_t001_t007 痛点 7 | AC 唯一源 = spec 契约区；task.md 收尾引用不复制 | `task.md` 模板 | 已落地 |
| L11 | TDD 顺序违规：改测试适配实现 | analysis §3 | 旧绿测只许保留或整体删除并说明；禁止就地改预期；reviewer 必须复核改测方向 | AGENTS「开发原则」、`task-work`「项目测试」、`test_prompt.txt` | 已落地 |
| L12 | blocked 无基础设施失败出口 | analysis §4 | 原方案加 `task.py block --reason infra` 与正式 `blocked` 状态。L36 删除正式 blocked 与 `block`/`resume`；阻塞时 task 保持 active，attempt `terminal=stopped` + `report=blocked`；禁止 agent 自定容错上限后绕过 | `architecture.md`「Attempt」、`task-run` | 已落地；被 L36 取代 |
| L13 | spec 脑补未核实的外部契约 | analysis §5 | spec 上下文区加「未知契约清单」，逐条标 `UNVERIFIED`；reviewer 只提示核实不 blocking | `spec.md` 模板、`share_prompt.txt` | 已落地 |
| L14 | 「一 task 一 commit」与实战脱节 | feedback B、retro_t041_t061 §7 | **保留「一 task 一 commit」**：六份 review 主张放开多 commit，但与黑盒/审阅质量门禁矛盾（回头改要么 amend 破坏历史，要么新 commit 污染历史）。task 太大就回 `task-create` 拆，不在执行期切分 commit。commit 须可独立验证、有工程意义 | `AGENTS.md`「开发原则」+ `task-work`「完成」 | 已落地 |
| L15 | 大模型进 plan mode 不执行既定 plan | 11111#2（已并入本表） | 用户触发 `task-run` 即视为队列已批准；禁止 plan mode、禁止开跑前二次征求同意 | `task-run`「授权与队列」 | 已落地 |
| L16 | finding 无分类，噪音率靠人工事后统计 | retro_t001_t007 数据概览、review/glm §2.1 | finding 必填 `category`（bug/spec_drift/duplicate/nitpick/coverage_gap）；撤回率由脚本算。字段未进 `task.md` 处置表与 `share_prompt.txt`；现行噪音控制见 L2/L17 | `share_prompt.txt`「blocking 硬阈值」 | 开放 |
| L17 | 行数与圈复杂度阈值制造 nitpick | 本轮审阅（review/opus + 模板实读） | 两类命中默认不进 finding 表，只在结论段提示；仅当已产出可观测缺陷才按缺陷出 finding | `code_prompt.txt`「降级规则」 | 已落地 |
| L18 | `review.md` 模板与 prompt 两处定义报告格式 | 本轮审阅 | 格式唯一定义在 prompt；`review.md` 已删除（强于指针） | `.repo_template/docs/review_prompts/` | 已落地 |
| L19 | 模板占位符被原样留在 spec/task | retro_t001_t007（plan 复制 spec 的变体） | `preflight` 拒绝残留 `{...}` 占位符 | `task.py preflight` | 已落地 |
| L20 | 契约在执行期被悄悄改动 | 本轮审阅（配合 L3 注入） | ~~`start` 时锁契约区 hash~~（原方案从未实现）；改为 `render_review_prompts.py` 渲染时 diff 契约区相对 diff_anchor 的变更并附警告块给 reviewer，合法变更可见、静默变更藏不住 | `render_review_prompts.py` | 已落地 |
| L21 | 依赖关系散落 spec 文字，无法机器校验；后续批次重复依赖 Agent 分析 | feedback D、retro_t041_t061、review_task_batch_scheduling 多路审阅 | task front matter 增加 `depends_on`、`conflicts_with`；`task-schedule` 负责 Agent 分析落盘，`task.py plan` 负责后续纯脚本调度（自原 `next-batch --done` 演化）。~~`schedule_status`~~ 已由 L36 删除，不持久化 | `scripts/task.py`、`task-schedule`、task 模板、`architecture.md`「调度图与计划」 | 已落地；`schedule_status` 被 L36 删除 |
| L22 | subagent prompt 内联正文撑爆 context | analysis §2 | 派发只传文件路径，正文写 `.scratch/review_prompts/` | `task-work`「独立 review」 | 已落地 |
| L23 | 遗留待办与 bugs 无统一登记 | 11111#1、analysis §13 | 统一待办入口。原单文件 `docs/pending.md`（`bNNN`/`fNNN` 两节）已演化为 `docs/pending/{todo,parked}/pNNN_{slug}.md`；遗留 finding 的落点是 pending 条目，`task.md` 只留 `fix_ref` | `docs/pending/`、`pending.py`、`pending-record`、`task.md` 模板 | 已落地 |
| L24 | skill 之间规则重叠，可能成新漂移源 | review/glm §2.4 | 状态机、门禁、目录权责只在 AGENTS 定义；用法/写权/skill 调用只在 `usage.md`；执行设计只在 `architecture.md`；skill 只写本阶段顺序、写域和停止条件 | 各 SKILL.md | 开放（2026-09-06 L36 已做一轮收敛，需持续盯） |
| L25 | 分级与粒度规则本身会成为博弈点 | review/k3 跨文档总评 | `review_level` 由创建期定并向用户说明理由。~~`none` 须在提交询问中列出~~：`none` 未实现，现行只有 `full`/`single` | `task-create` | 开放（`full`/`single` 分级博弈风险仍在） |
| L26 | 允许 agent 直接手改 index JSON | feedback E | **否决**。并发下会放大损坏；改为派生缓存后该诉求消失 | — | 已否决 |
| L27 | plan 拆三套永久模板（code/doc/style） | feedback A | **否决**。维护成本高于收益；plan 已整体删除 | — | 已否决 |
| L28 | 把 `/goal` hook、Electron ABI 写进通用模板 | analysis §2/§8 | **否决**。宿主与项目特有，不进模板；单会话 task 上限由使用者自行掌握 | — | 已否决 |
| L29 | 换 reviewer 模型解决审阅信噪比 | analysis §1 | **否决**。根因在 prompt + 上下文 + 阈值，不在模型 | — | 已否决 |
| L30 | 「不切分支，全在 main 上做」 | retro_t041_t061 §1 方案 C | **否决**为全局教条。它与 incident_t071 回答的不是同一问题：串行时 main 直做可行，并发或长未提交窗口必须 worktree。统一按 L4 处理 | — | 已否决 |
| L31 | 已验证的技术发现无处沉淀，spike 结论随报告归档失传 | 用户提出 | `docs/findings/dNNN_{slug}.md` 记已验证事实（一条目一文件）；spike 收尾抽结论，报告全文归档。~~单文件 `docs/findings.md`~~ 已演化为目录 | `docs/findings/`、`findings.py`、`task-work`「实施」 | 已落地 |
| L32 | `task-debt` 名字表达的是「捞技术债」，实际职责是「把总账条目转 task」 | 用户提出 | 改名 `task-from-pending`；入口 `docs/pending/todo/`，建完用 `pending.py archive --fix-ref {tid}` 归档 | `.repo_template/skills/task-from-pending/` | 已落地 |
| L33 | skill 被 agent 按语义自动触发，绕过用户批准 | 用户提出 | 禁止模型按语义自动触发。原方案全部 `description: none` + `disable-model-invocation: true`。现行：用户入口 skill 可有斜杠用 description；执行链 skill 保持 `description: none`。`disable-model-invocation` 未在全部 skill 对齐 | 各 `SKILL.md` frontmatter、`usage.md`「skill 调用」 | 开放（原「全部 none」未恢复） |
| L34 | 调度控制面边沿触发：worker 全灭无人察觉、integrate 后忘补位 | omni_media 两起事故（2026-08）、用户提出、多路 diff 审阅 | 边沿触发 → 水位触发：`task.py reconcile` 幂等 diff 唯一动作来源 + 空闲许可；调度账本记 attempt；handoff.json 机器验证；失败分类 + cron 兜底 | `archive/plan_dispatch_control_plane.md` | 已落地；被 L35 取代 |
| L35 | 放弃 dispatch 自动并发，并发只留用户手动 | 用户决定（2026-08-06） | `task-dispatch` 退役；并发只允许用户手动多会话 `task-run`；`task.py view --serve` 只读看板；`start` 加 depends 硬拒与 conflicts 警告。dispatch 配套 API（`bind`/`escalate`/`observe`/`reconcile` 等）删除；executor 仅 `inline`。ledger、handoff.json、`verify_integrate_ready` 保留。~~merge_guard 保留~~ 已由 L36 删除 | `plans/plan_manual_concurrency.md`、`task.py view --serve`、`cmd_start` 调度门、`.repo_template/docs/architecture.md`、`usage.md` | 已落地；merge_guard 被 L36 取代 |
| L36 | 工作流特殊机制与 skill 过度详细 | 用户逐项裁决（2026-09-06）、`plans/plan_workflow_simplification.md` | 严格模板、测试/黑盒/review、attempt、goal、一 task 一 commit、pending 子代理和同步能力保留；删除 merge token hook 与正式 blocked 状态；attempt report=blocked 继续审计；review/verify limit 均保留并用独立 limits 命令只增；合并改为 Git `merge --no-ff --no-commit` 验证后提交；调度删除 schedule_status、反向冲突边维护和复杂 tie-break；15 个 skill 改为结果与门禁导向，移除重复剧本 | `repo/.repo_template/skills/`、`repo_task/integration.py` / `lifecycle.py` / `scheduling.py`、task 模板、Claude settings、同步脚本 | 已落地 |
| L37 | 消费仓首次追平大改版时同步脚本自举缺口 | md_kx 迁 Mac 实证（2026-09-14） | prep 只能刷新已存在且可跑的脚本：消费仓 `repo_sync.py` 若是旧布局版（旧 state 路径、旧硬同步清单、无 `prep`），调用即失败，无法自举。启动器显式加「先复制模板源 `repo_sync.py` 覆盖消费仓」步骤（任何子命令之前）；`init` 增加旧 state 迁移（带过 `user_prompts`/`last_synced_*`） | `repo/.repo_template/skills/repo-template-sync/SKILL.md`、`repo_sync.py` | 已落地 |

## 未闭环

| 议题 | 卡在哪 |
| --- | --- |
| 统一 task 服务（跨仓库/跨会话的状态服务） | 11111#3 提出，等用户决定。L1 已消除并发写冲突，该诉求优先级下降 |
| 分级效果复测 | analysis 的 PASS 率与遗留数来自单一项目（Electron + 弱测试基础设施）样本。L2/L5 落地后应在非 Electron 项目复测，确认数值而非仅方向 |
| L16 finding `category` | 已裁决未进模板与脚本；噪音控制目前靠 L2/L17 |
| L33 skill 自动触发 | 用户入口 description 与 `disable-model-invocation` 未按原方案对齐 |
| L24/L25 | skill 重复权威与 `review_level` 分级博弈，持续盯 |
