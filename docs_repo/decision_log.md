# decision_log

跨文档裁决总账。**只列被两份以上复盘诊断过的议题**，单文档独有意见不进表，避免噪音再生。

状态：`已落地` = 已进 AGENTS/skill/prompt/脚本；`开放` = 已裁决未实施；`已否决` = 明确不做。

改流程前读本表，不必重读全部复盘。新发现在此加行，不再新写复盘文档。

## 总账

| # | 议题 | 出处 | 裁决 | 落点 | 状态 |
|---|------|------|------|------|------|
| L1 | `tasks_index.json` 跨分支 merge 冲突 | feedback §4/E、retro_t041_t061 §1、analysis §12 | 状态权威下沉到各 task 的 `task.md` front matter；index 改派生缓存并 gitignore | `scripts/task.py`、`.gitignore` | 已落地 |
| L2 | 双审 finding 无界、信噪比低 | analysis §1、feedback §3/C、retro_t041_t061 §2、retro_t001_t007 痛点 3 | blocking 必须锚 AC **或**可观测行为缺陷；「建议加测」「覆盖可更广」降 minor | `share_prompt.txt`「blocking 硬阈值」 | 已落地 |
| L3 | reviewer 缺决策上下文致撤回率高 | analysis §1b、feedback 再评估节 | spec 契约区与上下文区正文直接注入 prompt，reviewer 不再自行读 spec | `render_review_prompts.py`、`share_prompt.txt` | 已落地 |
| L4 | branch ≠ worktree，未提交改动丢失 | incident_t071、retro_t041_t061 §1、analysis §11 | 默认每 task 独立 worktree（`../{repo}_{tid}`）+ `.env` 软链；仅用户明确指令才 `--no-worktree`；禁止长期未提交 | `task.py start` / `preflight`、AGENTS「开发原则」 | 已落地 |
| L5 | 双审对所有 task 无差别 | feedback §3/C、retro_t041_t061 §2、retro_t001_t007 痛点 3 | `review_level: full\|single\|none` 进 front matter，判不准取 `full` | `task.py add --review-level`、`tasks-run` Step 5 | 已落地 |
| L6 | `plan.md` 退化为 spec 副本、实际弃用 | feedback §1+A、retro_t041_t061 §4、analysis §1b | **删除 plan.md**。根因是 plan 写在信息最少的时刻、用在信息最多的时刻；实施步骤改由执行期记入 `task.md` 实施笔记 | 模板删 plan.md；spec 二分承接上下文 | 已落地 |
| L7 | spec 写死技术选型 → 过时 → FAIL 循环 | retro_t001_t007 痛点 1、feedback §1 | AC 只写行为；技术选择进 `decisions.md`；spec 漂移类 finding 标 `spec_drift`，处置为改 spec，不计 FAIL | `spec.md` 模板、`share_prompt.txt` | 已落地 |
| L8 | `max_review_round` 语义混乱（出场次数 vs 闭环次数） | retro_t041_t061 §3、retro_t001_t007 痛点 5、analysis §1 | round = **回归轮次**：上轮 FAIL 修完重审才计数；新 finding 当轮处置不强制 N+1 | `check_review_status.py`、AGENTS「命名与状态」 | 已落地 |
| L9 | 横向缺口只标遗留、不开 task | retro_t001_t007 根因节、feedback、analysis §13 | 遗留 finding 须映射 tid；reviewer 引用已有 follow-up 不重复报 | `pending-to-task` skill、`share_prompt.txt`「系统性缺口去重」 | 已落地 |
| L10 | AC 三处维护（spec / task.md / 处置表） | retro_t001_t007 痛点 7 | AC 唯一源 = spec 契约区；task.md 收尾引用不复制 | `task.md` 模板 | 已落地 |
| L11 | TDD 顺序违规：改测试适配实现 | analysis §3 | 旧绿测只许保留或整体删除并说明；禁止就地改预期；reviewer 必须复核改测方向 | AGENTS「开发原则」、`tasks-run` Step 3、`test_prompt.txt` | 已落地 |
| L12 | blocked 无基础设施失败出口 | analysis §4 | 加 `--reason infra`；禁止 agent 自定容错上限后绕过 | `task.py block`、AGENTS「blocked」 | 已落地 |
| L13 | spec 脑补未核实的外部契约 | analysis §5 | spec 上下文区加「未知契约清单」，逐条标 `UNVERIFIED`；reviewer 只提示核实不 blocking | `spec.md` 模板、`share_prompt.txt` | 已落地 |
| L14 | 「一 task 一 commit」与实战脱节 | feedback B、retro_t041_t061 §7 | task = 一个可 review 的交付单元；commit 数放开 ≥1，每个 subject 含 tid；合并用 `--no-ff` | AGENTS「commit 策略」、`tasks-run` Step 8 | 已落地 |
| L15 | 大模型进 plan mode 不执行既定 plan | 11111#2（已并入本表） | 用户触发 `tasks-run` 即视为队列已批准；禁止 plan mode、禁止开跑前二次征求同意 | AGENTS「硬约束」、`tasks-run` 开头 | 已落地 |
| L16 | finding 无分类，噪音率靠人工事后统计 | retro_t001_t007 数据概览、review/glm §2.1 | finding 必填 `category`（bug/spec_drift/duplicate/nitpick/coverage_gap）；撤回率由脚本算 | `share_prompt.txt`、`task.md` 处置表、`check_review_status.py` | 已落地 |
| L17 | 行数与圈复杂度阈值制造 nitpick | 本轮审阅（review/opus + 模板实读） | 两类命中默认不进 finding 表，只在结论段提示；仅当已产出可观测缺陷才按缺陷出 finding | `code_prompt.txt` | 已落地 |
| L18 | `review.md` 模板与 prompt 两处定义报告格式 | 本轮审阅 | 格式唯一定义在 prompt；`review.md` 只留指针 | `task_template/review.md` | 已落地 |
| L19 | 模板占位符被原样留在 spec/task | retro_t001_t007（plan 复制 spec 的变体） | `preflight` 拒绝残留 `{...}` 占位符 | `task.py preflight` | 已落地 |
| L20 | 契约在执行期被悄悄改动 | 本轮审阅（配合 L3 注入） | `start` 时锁契约区 hash，`preflight` 检测漂移 | `task.py start` / `preflight` | 已落地 |
| L21 | 依赖关系散落 spec 文字，无法机器校验 | feedback D、retro_t041_t061 | `depends_on` 进 front matter；`start` 拒绝依赖未完成的 task | `task.py add/edit/start` | 已落地 |
| L22 | subagent prompt 内联正文撑爆 context | analysis §2 | 派发只传文件路径，正文写 `.scratch/review_prompts/` | `tasks-run` Step 5 | 已落地 |
| L23 | 遗留待办与 bugs 无统一登记 | 11111#1、analysis §13 | `bugs.md` 扩为 `docs/pending.md`，「未修 bug」(`bNNN`) 与「遗留待办」(`fNNN`) 两节共一个入口；遗留 finding 的唯一落点是该节，`task.md` 只留 `fix_ref` 引用 | `docs/pending.md`、`tasks-run` Step 6/7、`task.md` 模板 | 已落地 |
| L24 | 9 个 skill 之间规则重叠，可能成新漂移源 | review/glm §2.4 | 状态机、门禁、目录权责只在 AGENTS 定义；skill 只写操作顺序并引用 AGENTS | 各 SKILL.md | 开放（本轮已清一遍，需持续盯） |
| L25 | 分级与粒度规则本身会成为博弈点 | review/k3 跨文档总评 | `review_level` 由创建期定并向用户说明理由；`none`/`single` 须在提交询问中列出 | `task-create` 步骤 3 | 开放 |
| L26 | 允许 agent 直接手改 index JSON | feedback E | **否决**。并发下会放大损坏；改为派生缓存后该诉求消失 | — | 已否决 |
| L27 | plan 拆三套永久模板（code/doc/style） | feedback A | **否决**。维护成本高于收益；plan 已整体删除 | — | 已否决 |
| L28 | 把 `/goal` hook、Electron ABI 写进通用模板 | analysis §2/§8 | **否决**。宿主与项目特有，不进模板；单会话 task 上限由使用者自行掌握 | — | 已否决 |
| L29 | 换 reviewer 模型解决双审信噪比 | analysis §1 | **否决**。根因在 prompt + 上下文 + 阈值，不在模型 | — | 已否决 |
| L30 | 「不切分支，全在 main 上做」 | retro_t041_t061 §1 方案 C | **否决**为全局教条。它与 incident_t071 回答的不是同一问题：串行时 main 直做可行，并发或长未提交窗口必须 worktree。统一按 L4 处理 | — | 已否决 |
| L31 | 已验证的技术发现无处沉淀，spike 结论随报告归档失传 | 用户提出 | 新建 `docs/findings.md`（`dNNN`）记已验证事实；spike 收尾抽结论，报告全文归档 | `docs/findings.md`、`tasks-run` Step 7、AGENTS「spike」「findings」 | 已落地 |
| L32 | `task-debt` 名字表达的是「捞技术债」，实际职责是「把总账条目转 task」 | 用户提出 | 改名 `pending-to-task`；入口收敛为 `docs/pending.md`「遗留待办」节，建完回写 `处理：{tid}` 并归档 | `.agents/skills/pending-to-task/` | 已落地 |
| L33 | skill 被 agent 按语义自动触发，绕过用户批准 | 用户提出 | 全部 skill frontmatter 固定 `description: none` + `disable-model-invocation: true`；仅允许用户斜杠或已获合法调用的 skill 链式调用。新增 skill 同样照办 | 各 `SKILL.md` frontmatter、AGENTS「skill 触发」 | 已落地 |

## 未闭环

| 议题 | 卡在哪 |
|------|--------|
| 统一 task 服务（跨仓库/跨会话的状态服务） | 11111#3 提出，等用户决定。L1 已消除并发写冲突，该诉求优先级下降 |
| 分级效果复测 | analysis 的 PASS 率与遗留数来自单一项目（Electron + 弱测试基础设施）样本。L2/L5 落地后应在非 Electron 项目复测，确认数值而非仅方向 |
