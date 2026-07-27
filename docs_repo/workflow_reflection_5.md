# Claude Code 会话实证分析与工作流改进建议（omni_usage, 2026-07）

> 落地状态见 `decision_log.md`。§1/§3/§4/§5 的 P0 建议已实施；§2 的 `/goal` hook 与 §8 Electron ABI 属宿主与项目特有，不进通用模板（L28）。文中 PASS 率与遗留数来自单一项目样本，按「方向正确、数值待复测」对待。

## 元信息

- **分析对象**：omni_usage 项目 Claude Code 会话日志（`~/.claude/projects/D--Kar-Code-omni-usage/`）
- **时间范围**：2026-07-23 ~ 2026-07-26（自然 4 天，跨 `~56h` + `~18h` 两条主线）
- **样本**：16 个有意义会话，含两个 19MB 大会话（`6cc03e0d` / `af3dbbf3`），日志总量 `> 80MB`
- **方法**：6 路并行 subagent 按文件分组抽样（`jq` / `grep`，禁全量 `cat`），主任务整合
- **与现有报告关系**：`workflow_feedback.md`（omni_media 理论）+ `workflow_retrospective.md`（omni_media 实战）已覆盖 spec/plan 模板字段重叠、`tasks_index.json` 多分支 merge 冲突等结构性问题。**本报告聚焦本轮会话新暴露的运行态问题**，不重复上述内容，仅在文末对照表标注交集。

## TL;DR（按严重度）

1. **审阅 finding 信噪比灾难**：首轮 PASS 率仅 29-30%；单 task 累计处置最多 1454 条，撤回+遗留远超已修。根因不是 reviewer 太严（同模型无能力差），是 finding 定义无界 + 上下文不对称 → P0
2. **`/goal` hook 串行多 task 致单会话 context 溢出**：两个 19MB 会话、`Request too large` 中断、`2377` 次 error → P0
3. **TDD 顺序违规 + 测试断言错误行为**：改测试适配实现、auth 改坏后仍过测 → P0
4. **subagent 失控 + 503 无工作流出口**：13→8→2 agent 数量失守；11+ 次 503 后自定"容错上限"停手，不走 blocked → P1
5. **spec 阶段对外部契约脑补**：CPA"官方接口/SK 前缀"全为假设，17 次"不是"纠错 → P1

## 实证数据概览

| 会话 | 大小 | task 跨度 | 审阅轮次分布 | 首轮 PASS | compact |
|---|---|---|---|---|---|
| `6cc03e0d` (7-21~24) | 19MB | t041-t097 ~30 task | R1:103 / R2:76 / R3:6 | 19/66 = 29% | 0（手动 `/clear` 接力）|
| `af3dbbf3` (7-24~25) | 19MB | t099-t105 7 task | R1:37 / R2:44 / R3-R5:16 | 16/54 = 30% | 1 |
| `87f4adb0` (7-26) | 7.2MB | t111-t118 8 task | 处置 490 已修 / 203 撤回 / 761 遗留 | — | 多次，结尾 `Request too large` |
| `587f2c52` (7-26) | 2.6MB | t121 | 单 task 326 条处置（144 / 54 / 128） | — | — |

---

## 结构性问题

### 1. 审阅 finding 信噪比（P0，本轮最严重）

**现象**

- 首轮 PASS 率 29-30%（`6cc03e0d` 19 PASS / 47 FAIL；`af3dbbf3` 16 PASS / 38 FAIL）
- `87f4adb0` 处置表累计 **490 已修 / 203 撤回 / 761 遗留**——撤回+遗留远超已修
- finding 多为"测试覆盖不足"类（如 `not.toThrow()` 不能证明 unsub 发生、`collect_upcoming_resets` 分支覆盖空洞），但这类本身 unbounded——实现永远可以测得更细
- `max_review_round = 2` 偏紧：`t102` / `t105` 两次被用户加轮到 5

**证据**：`87f4adb0` 处置统计；`6cc03e0d` `overall=FAIL 47 / PASS 19`；`af3dbbf3` `overall=FAIL 38 / PASS 16`。

#### 根因分析：不是 reviewer 太严，是 finding 定义无界

直觉归因"reviewer 标准偏严 / 换模型可能改善"是错的。本项目 reviewer 与主 agent 同为 opus（见 memory `feedback_subagent_model`），不存在能力差异；"严"也不是模型属性。真正根因四层：

**a. 角色框架触发保守偏差（主因）**

同一模型当实现者时追求"够用就过"，当 reviewer 时提示词要求"找问题"。在"找问题"角色下，模型默认漏报风险 > 误报风险，把所有"可以更完整"的点全报。换 sonnet 当 reviewer，只要 prompt 仍是"请审查"，行为相同。这不是模型严，是**角色 prompt 把它推向过度报**。finding 大量是"测试可以更严"而非"行为错误"，这类 finding 没有锚点就会无限报。

**b. 信息不对称，不是能力不对称**

主 agent 有完整 spec/plan/AC 决策上下文，知道"这个分支不测是因为 plan 阶段判定不可达"；reviewer subagent 只拿 `render_review_prompts.py` 渲染的 prompt，**可能不含全部决策依据**。reviewer 报"分支没覆盖"，主 agent 用完整上下文判定"有意为之" → 标 `撤回` 或 `遗留`。撤回率高的本质：**reviewer 在缺上下文下做的合理怀疑，被主 agent 用完整上下文否决**。同模型反而让这个摩擦更隐蔽——不会怀疑是信息差，会误以为是模型判断分歧。

**c. finding 没有锚 AC，阈值无界**

reviewer 提示词没要求"只报 AC 阻塞级"。于是"风格建议 / 边界用例 / 可以更健壮"全混进 finding 表。处置时主 agent 既不能说它错（确实可以更测），又不想改（不阻塞 AC）→ 堆成 `遗留`。**761 条遗留就是这么来的**。根因不是 reviewer 太严，是 finding 定义无界。给任何模型一个无界审查任务，都会产出无界 finding。

**d. 同模型不解决盲区，只转移盲区**

主 agent 有盲区（如总用 `not.toThrow()`），同模型 reviewer 有相似盲区，反而针对同类盲区报得更狠（知道这类容易漏）。换不同模型能引入盲区多样性，但**不解决 AC 锚点缺失这个根因**，只是把"报什么"换个分布。

**结论**：换模型不解决问题，根因在 prompt + 上下文 + 阈值设计。同模型这个事实反而排除了"换模型救场"的幻想，逼到 prompt 工程层。

#### 建议（按因果链倒推，均不依赖换模型）

- **reviewer 提示词加硬阈值**：只报"AC 阻塞 / 行为级缺陷"，"建议加强测试"降级为 non-blocking 备注——直接掐断无界 finding 源头
- **review prompt 带全决策上下文**：`render_review_prompts.py` 输出时附 plan.md 的"已判定不测分支"清单，消除信息差导致的撤回
- **Step 2 红阶段前置"AC 断言清单"**：reviewer 只核对清单是否被覆盖，不让它自由发挥"还可以测什么"
- 撤回率 > 30% 时**强制 reviewer 复盘提示词**（脚本检测 `已修 / (已修+撤回+遗留)` 比例）
- 遗留 finding **必须映射到后续 task tid 或显式 AC 缺口**，禁止悬空
- E2E / UI 类 task 默认 `max_review_round = 3-4`（按 task 类型分级）

### 2. `/goal` hook 致单会话 context 溢出（P0）

**现象**

- 两个 19MB 会话，单条 tool_result 100KB+（subagent 返回 122KB / 98KB / 84KB / 70KB），**未触发 auto-compress**（全文件 0 条 `isCompact`），靠手动 `/clear` + 巨型 summary 接力
- `87f4adb0`：`/goal` Stop hook 强制 8 task 串行到同一会话，`2377` 次 error / `324` 次失败 / 结尾 `Request too large (max 32MB)` 中断
- assistant 想"先问用户"被 hook 阻止；block/drop 由 hook 推进而非设计判断

**证据**：`6cc03e0d` 行 2267 / 7267 两次 `ran out of context`，0 条 `isCompact`；`87f4adb0` 结尾 `Request too large`；`846af54b` Stop hook 原文"treat the condition itself as your directive"。

**建议**

- `/goal` hook 改为**每 task 切会话 / 切 worktree**；检测 `task.py finish` 后强制 stop 让用户重启
- 单会话 task 数硬上限 `≤ 2`
- 每个 `/git-finalize` 后强制 `/compact`
- subagent 派发 prompt 改用文件路径（`.scratch/review_prompts/*.md`）而非内联回显——`6cc03e0d` 中 86 个 subagent × `~80K token` 输入回显 ≈ 7MB，占会话体积 37%

### 3. TDD 顺序违规 + 测试断言错误行为（P0）

**现象**

- `t098`：spec 改"匿名登录"后，旧红测 `handleSessionLogin returns VALIDATION_ERROR when instance_id is missing` 被**改写适配新实现**（实现改测试，非测试驱动实现），并出现 duplicate test title
- `t105` E2E drag 测试 FAIL 后助手自述"我已改测试，按 Step 6 改测试回 Step 3"
- `5f8fdc72`：auth 改坏原本正常代码，"还能过测试"——测试断言了错误行为；用户原话"改坏原本正常代码且还能过测试"

**证据**：`8bb084c7` `task.md` 自述"原 missing instance_id → validation error 用例被改为允许匿名"；`5f8fdc72` 用户原话。

**建议**

- CLAUDE.md Step 3 增加：**实现变更导致旧测试语义失效时，必须新增红测覆盖新语义；旧绿测只允许删除，禁止就地改预期**
- Step 6 改测试需 reviewer 复核"是否在断言预期而非现状"
- bug 修复前必须先回答"现有测试为何没 catch"——对应 memory `test_assert_expected_behavior` 已存在，但执行层缺硬约束

### 4. subagent 失控 + 503 无工作流出口（P1）

**现象**

- `8bb084c7`：先启 13 个 background agent 被用户全停；切 `/code-review` 又启 8 个 finder 被自停；最终才收敛到 2 个
- t099 审阅连续 `11+` 次 `503 No available channel for model claude-opus-4-8`，助手自行解释为"容错上限"停手写 handoff，**未走 `block <tid>`**
- CLAUDE.md `blocked` 表只覆盖"黑盒轮次达上限 / 审阅 FAIL 满轮"两种触发，**无基础设施失败路径**

**证据**：`8bb084c7` "13 background agents were stopped by the user"；t099 503 重试日志。

**建议**

- CLAUDE.md Step 5 加硬约束：**审阅 subagent 总数 = 2**，禁止 fallback 扩容；503 时走 blocked，禁止扩 agent 绕过
- 增加第三类 blocked 触发：**基础设施失败**（503 / 网络 / subagent 启动失败 N 次），`task.py block <tid> --reason infra`，要求口头报告 + 等用户放行
- subagent 派发 prompt 用文件路径而非内联回显（同 §2）

### 5. spec 阶段对外部契约脑补（P1）

**现象**

- `587f2c52`：用户原话"CPA 哪来的官方接口？谁跟你说 API 密钥 SK 开头？你真的试过输入接口地址和密钥能拿到数据吗？"——agent 在 spec 阶段就假设了 API 形态与默认 endpoint
- `15af232b`：17 次"不是" / 15 次"应该" / 5 次"重新"，全程零代码产出
- 违反 CLAUDE.md"未明确即未知"

**建议**

- spec 模板增加**「未知契约清单」必填项**（CPA endpoint / API 形态 / Grok 设备码流程等），未填写禁止进入 Step 2
- spec 评审阶段加"假设审计"步骤：所有"我认为"必须标注证据来源或显式标记 `UNVERIFIED`

### 6. bug 调研 → task 转换接口缺失（P1）

**现象**：`08982e73` 用户连提两 bug + 测试缺口，助手只给文字根因表，未建 task / 未写 `bugs.md`，直到用户喊"记录 bugs.md 新建 task"才动。

**建议**：CLAUDE.md 加规则——**bug 只读调研完成后必须主动追加 `bugs.md` 条目 + 提议拆 task**，不得仅留口头分析。

### 7. 任务拆分缺前置确认，建完又删（P1）

**现象**：`09bcf829` 建 t119/t120 后用户立刻"删除这两个 task"；`87f4adb0` t116 跑到一半 drop。

**建议**：`task.py add` 后在对话列出「tid / slug / AC 摘要 / 影响文件」**等用户确认才进入 Step 1**；高复杂度 task（如 t115 live 契约）先 spike 再拆。

### 8. 原生模块 ABI 脚本反复踩坑（P1，Electron 特有）

**现象**：`ba58e2ad` + `8daf1d5e` 两会话都在调 better-sqlite3 ABI 切换。`ensure_electron_abi.mjs:55` 调 `node-gyp rebuild` 缺 `--runtime=electron`；脚本打印 `gyp info ok` + `switch complete` 看似成功，**实则 stdio pipe 吞 stderr、status 被误判为 0**；`poststart: ensure_node_abi.mjs` 启动后又把模块切回 Node ABI 破坏 dev。

**证据**：用户原话"你丫的好好设计两个脚本到底该怎么做，我要你能自动切换。不要总是出现这种问题"。

**建议**

- 脚本必须**自检产物 ABI**（读 `.node` 的 `NODE_MODULE_VERSION` 与 Electron ABI 比对），非依赖脚本退出码
- 移除 `poststart` 反向切换；dev/prod 脚本拆分
- pre-push 跑 `pnpm test` 触发 `ensure_node_abi` 重建 native、推送慢且破坏同机 dev——单独排查

### 9. 工具 / 命令误用反复（P2）

**现象**

- `846af54b`：误用 Skill 工具调 `/code-review`（实为插件 command，无 `SKILL.md`），调试一圈才发现 `settings.local.json` 那条 `Skill(code-review:code-review)` 是历史预授权残留
- Windows Shell Guard 拦截 Bash（`is_error: true`）
- `24f2da63`：调研期 `search_tool.py` / WebSearch 因 token 含 `&` 被截断，2 分钟超时才换路
- 全文件 `[rtk] /!\ No hook installed` 提示反复出现，`python -c` 被 hook 拦需改写脚本文件

**建议**：维护 `.claude/known_pitfalls.md`，记录曾踩坑的命令分类（command vs skill、Windows Shell Guard、stdio pipe 吞 stderr、shell 元字符截断、RTK 未 init）。

### 10. 打包产物验证低效（P2）

**现象**：`87f4adb0` 结尾手工 `grep out/main/index.js` 与源码对比，反复重启验证；`main@70fb7b4` 多次重启。

**建议**：把"打包后 smoke + manifest 注入断言"纳入 `{blackbox_cmd}` 默认项，禁止靠肉眼读编译产物验证行为。

### 11. 分支卫生 + 工作区边界（P2）

**现象**：`08982e73` 末尾才发现 t098 分支已混入 t103 文档；`587f2c52` 用户"第一个是我加的，你别管"——险些把用户改动卷入 task commit。

**建议**：Step 1 开干前**强制校验** `git status` 与 `git branch --show-current`；建 task 目录前若 `git status` 含未提交且不属于本 task 的改动，必须停下报告。

### 12. task 索引 / note 同步滞后（P2）

**现象**：`39131047` 中 t095 / t104 note `blocked: review` 但实际已 done；`af3dbbf3` 末段用户口头豁免 t087 `tasks_index` 硬约束"手动改"。

**建议**

- `task.py finish` 增加校验：若被 finish 的 tid 在任何兄弟 task 的 `task.md` / `specs_index.md` 中被引用，强制提示同步
- `task.py` 补 note 历史标注能力，避免硬约束被现场豁免

### 13. 独立 review 报告未落地 task（P2）

**现象**：`a8c8ae46` 6 路并行 review 产出 2 critical + 29 important，仅口头"建议拆 task"，未实际 `task.py add`；critical（local-api 越权、mimo 阈值方向反）无 tid 跟踪。

**建议**：`/multi-model-review` skill 末步加强制 hook——critical / important 必须立即 `task.py add` 建 backlog 并填 spec。

---

## 本轮新发现 vs 已知问题

| 问题 | 现有报告覆盖？ |
|---|---|
| 审阅信噪比 / 首轮 PASS 29% | 否（新） |
| `/goal` hook context 溢出 | 否（新，omni_media 未用 `/goal`） |
| subagent 失控 + 503 无出口 | 否（新） |
| TDD 顺序违规 | 否（新） |
| 原生模块 ABI 脚本 | 否（新，Electron 特有） |
| spec/plan 模板字段重叠 | 是 → `workflow_feedback.md` §1 |
| `tasks_index.json` 多分支 merge 冲突 | 是 → `workflow_retrospective.md` §1 |

## 亮点（值得保留的做法）

- **数据恢复事故**（`af3dbbf3`）：先只读反推 `instanceId → provider → plugin`，sqlite 反查，备份再覆盖，解密 `secrets.vault` 全量恢复 10 connector——事故响应纪律好
- **黑盒真实 Electron 运行时验证**（宽度 1200px / `maximum_size=[0,0]` / 重启恢复）扎实，不靠单测糊弄
- **t098 收尾报告如实标注**"真实授权登录未验证"，未伪造结果
- **调研类交叉验证** 6 个开源项目，多源一致后才下结论
- **审阅 FAIL 时 reviewer 真能抓 bug**：`not.toThrow` 不能证 unsub、`collect_upcoming_resets` 分支覆盖空洞、local-api `/v1/secrets` 在 `check_auth` 之前——都是高质量 finding

## 优先级汇总

| P | 问题 | 关键动作 |
|---|---|---|
| P0 | 审阅信噪比 | reviewer 加 AC 硬阈值 + review prompt 注入决策上下文 + Step 2 AC 断言清单（均不依赖换模型） |
| P0 | `/goal` context 溢出 | 每 task 切会话 + 单会话 `≤ 2` task + 强制 `/compact` |
| P0 | TDD 顺序违规 | 旧绿测只删不改 + reviewer 复核改测试 |
| P1 | subagent 失控 + 503 | 审阅 `agent = 2` + infra blocked + 派发用文件路径 |
| P1 | spec 脑补契约 | 未知契约清单必填 + 假设审计 |
| P1 | bug → task 接口 | 只读调研后必须追加 `bugs.md` |
| P1 | 任务建完又删 | `add` 后确认才进 Step 1 |
| P1 | ABI 脚本 | 自检产物 ABI + 移除 `poststart` 反切 |
| P2 | 工具误用 | `known_pitfalls.md` |
| P2 | 打包验证 | 纳入 `{blackbox_cmd}` |
| P2 | 分支卫生 | Step 1 强制 `git status` 校验 |
| P2 | 索引同步 | `task.py finish` 引用校验 |
| P2 | review 落地 | `/multi-model-review` 强制建 task |
